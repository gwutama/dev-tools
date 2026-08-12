#!/usr/bin/env python3
"""
Portable raw disk image flasher for Linux and macOS.
Uses only Python standard library. External deps: sudo, umount/diskutil.
WARNING: The selected target disk is completely overwritten.
"""

import argparse
import atexit
import bz2
import curses
import gzip
import hashlib
import lzma
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
from datetime import datetime
import zipfile
from pathlib import Path

PROGRAM_NAME = 'Image Flasher'
BACKTITLE = PROGRAM_NAME
OS = platform.system()   # 'Linux' or 'Darwin'
_temp_dir = None         # created in main(), cleaned on exit
_scr = None              # curses main screen


# ── Cleanup & signals ─────────────────────────────────────────────────────────

def _cleanup():
    """Remove the temp directory and restore the terminal on exit."""
    if _temp_dir and Path(_temp_dir).is_dir():
        shutil.rmtree(_temp_dir, ignore_errors=True)
    if _temp_dir:
        tui_stop()

atexit.register(_cleanup)

for _sig in (signal.SIGTERM, signal.SIGHUP):
    signal.signal(_sig, lambda s, f: sys.exit(0))


# ── Dependency check ──────────────────────────────────────────────────────────

def check_dependencies():
    """Exit if any required external tool is missing."""
    tools = []
    if OS == 'Linux':
        tools.append('umount')
    elif OS == 'Darwin':
        tools.append('diskutil')
    else:
        sys.exit(f'Error: unsupported OS: {OS}')
    if os.getuid() != 0:
        tools.append('sudo')

    missing = [t for t in tools if not shutil.which(t)]
    if missing:
        sys.exit(f'Error: missing required tools: {", ".join(missing)}')


# ── Helpers ───────────────────────────────────────────────────────────────────

def format_size(nbytes):
    """Convert a byte count to a human-readable string (e.g. '1.4 GiB')."""
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if nbytes < 1024 or unit == 'TiB':
            return f'{int(nbytes)} {unit}' if unit == 'B' else f'{nbytes:.1f} {unit}'
        nbytes /= 1024


def run_root(*cmd, **kw):
    """Run a command as root, prepending 'sudo -n' when not already root."""
    if os.getuid() != 0:
        cmd = ('sudo', '-n') + cmd
    return subprocess.run(cmd, **kw)


def raw_device(disk):
    """Return the raw (unbuffered) device path for direct I/O.
    On macOS /dev/diskN → /dev/rdiskN; Linux is unchanged.
    """
    return disk.replace('/dev/disk', '/dev/rdisk', 1) if OS == 'Darwin' else disk


COMPRESSION_OPENERS = {
    '.gz': gzip.open,
    '.bz2': bz2.open,
    '.xz': lzma.open,
}
UNSUPPORTED_COMPRESSION_SUFFIXES = {
    '.7z', '.rar', '.tar', '.tbz', '.tgz', '.txz', '.z', '.zst',
}


def unpack_image(image):
    """Return (image_path, error). Extract supported compressed images to temp."""
    source = Path(image)
    suffix = source.suffix.lower()
    if len(source.suffixes) > 1 and source.suffixes[-2].lower() == '.tar':
        return None, 'TAR archives are not supported; select compressed raw image instead.'
    if suffix not in COMPRESSION_OPENERS and suffix != '.zip':
        if suffix in UNSUPPORTED_COMPRESSION_SUFFIXES:
            return None, f'Compression format {suffix} is not supported.'
        return image, ''

    output = Path(_temp_dir) / source.stem
    try:
        if suffix == '.zip':
            with zipfile.ZipFile(source) as archive:
                members = [member for member in archive.infolist() if not member.is_dir()]
                if len(members) != 1:
                    return None, 'ZIP image must contain exactly one file.'
                output = Path(_temp_dir) / Path(members[0].filename).name
                with archive.open(members[0]) as src, output.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
        else:
            with COMPRESSION_OPENERS[suffix](source, 'rb') as src, output.open('wb') as dst:
                shutil.copyfileobj(src, dst)
    except (OSError, EOFError, lzma.LZMAError, zipfile.BadZipFile) as exc:
        output.unlink(missing_ok=True)
        return None, f'Could not unpack {source.name}: {exc}'

    return str(output), ''


def remove_unpacked_image(state):
    """Delete temporary unpacked image, leaving original selected image intact."""
    unpacked = state.pop('unpacked_image', '')
    if unpacked:
        Path(unpacked).unlink(missing_ok=True)


def write_verification_log(image, device, image_size, bytes_read,
                           source_hash, device_hash, details):
    """Write persistent diagnostics for a failed verification."""
    log = Path(tempfile.gettempdir()) / \
        f'image-flasher-verify-{datetime.now():%Y%m%d-%H%M%S}.log'
    log.write_text(
        f'Verification failed: {datetime.now().isoformat()}\n'
        f'Source image: {image}\n'
        f'Device: {device}\n'
        f'Image size: {image_size} bytes\n'
        f'Bytes read: {bytes_read}\n'
        f'Source SHA-256: {source_hash or "unavailable"}\n'
        f'Device SHA-256: {device_hash}\n'
        f'Details: {details}\n',
    )
    return str(log)


# ── Curses TUI ────────────────────────────────────────────────────────────────
# Replaces the external `dialog` utility entirely.
# Return codes: OK=0  CANCEL=1  EXTRA=3  (unchanged from dialog convention)

OK, CANCEL, EXTRA = 0, 1, 3


def tui_start():
    """Initialise curses. Call once after all pre-flight checks pass."""
    global _scr
    _scr = curses.initscr()
    curses.noecho(); curses.cbreak(); curses.curs_set(0)
    _scr.keypad(True)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)   # dialog bg
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)   # focused button
        curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_WHITE)  # selected item


def tui_stop():
    """Restore the terminal to its original state."""
    global _scr
    if _scr is None:
        return
    try:
        curses.nocbreak(); curses.echo(); curses.curs_set(1)
        _scr.keypad(False); curses.endwin()
    except Exception:
        pass
    _scr = None


def _dims():
    return _scr.getmaxyx() if _scr else (24, 80)


def _new_win(h, w):
    """Create a new window centered on the screen."""
    sh, sw = _dims()
    win = curses.newwin(h, w, max(1, (sh - h) // 2), max(0, (sw - w) // 2))
    win.keypad(True)   # required so getch() returns KEY_UP/DOWN instead of raw ESC sequences
    if curses.has_colors():
        win.bkgd(' ', curses.color_pair(1))
    return win


def _frame(win, title):
    """Draw border, bold title, and backtitle on the main screen."""
    h, w = win.getmaxyx()
    attr = curses.color_pair(1) if curses.has_colors() else 0
    win.border()
    if title:
        t = f' {title} '
        try:
            win.addstr(0, max(1, (w - len(t)) // 2), t[:w - 2], attr | curses.A_BOLD)
        except curses.error:
            pass
    sh, sw = _dims()
    try:
        _scr.addstr(0, 1, f' {BACKTITLE} '[:sw - 1], curses.A_REVERSE)
        _scr.noutrefresh()
    except curses.error:
        pass


def _wrap(text, w):
    """Word-wrap text preserving explicit newlines."""
    lines = []
    for para in text.split('\n'):
        lines.extend(textwrap.wrap(para, w) if para.strip() else [''])
    return lines


def _put_text(win, lines, start_y, w):
    attr = curses.color_pair(1) if curses.has_colors() else 0
    for i, line in enumerate(lines):
        try:
            win.addstr(start_y + i, 2, line[:w - 4], attr)
        except curses.error:
            pass


def _put_buttons(win, labels, focus):
    """Render a centred button row; focus is the index of the active button."""
    h, w = win.getmaxyx()
    bstrs  = [f'[ {l} ]' for l in labels]
    total  = sum(len(b) for b in bstrs) + 2 * (len(bstrs) - 1)
    x = max(1, (w - total) // 2)
    for i, bs in enumerate(bstrs):
        attr = (curses.color_pair(2) if i == focus else curses.color_pair(1)) \
               if curses.has_colors() else (curses.A_REVERSE if i == focus else 0)
        try:
            win.addstr(h - 2, x, bs, attr)
        except curses.error:
            pass
        x += len(bs) + 2


def _reset():
    """Clear and refresh the main screen between dialogs."""
    if _scr:
        _scr.clear(); _scr.refresh()


def show_error(msg):
    dlg_msgbox('Error', msg)


def dlg_msgbox(title, text):
    """Display a message box and wait for OK / Enter / ESC."""
    sh, sw = _dims()
    w = min(74, sw - 2)
    lines = _wrap(text, w - 4)
    h = min(len(lines) + 4, sh - 2)
    win = _new_win(h, w)
    _frame(win, title)
    _put_text(win, lines[:h - 4], 1, w)
    _put_buttons(win, ['OK'], 0)
    win.refresh()
    while win.getch() not in (10, 13, curses.KEY_ENTER, 27, ord(' ')):
        pass
    _reset()


def dlg_yesno(title, text, yes='Yes', no='No', extra=None):
    """Confirmation dialog.  Left/Right or Tab cycle buttons; Enter activates.
    Returns OK (0), CANCEL (1), or EXTRA (3).
    """
    sh, sw = _dims()
    w = min(74, sw - 2)
    lines = _wrap(text, w - 4)
    h = min(len(lines) + 4, sh - 2)
    win = _new_win(h, w)
    _frame(win, title)
    _put_text(win, lines[:h - 4], 1, w)
    btns  = [yes, no] + ([extra] if extra else [])
    focus = 0
    while True:
        _put_buttons(win, btns, focus)
        win.refresh()
        k = win.getch()
        if k == curses.KEY_LEFT:
            focus = (focus - 1) % len(btns)
        elif k in (curses.KEY_RIGHT, 9):        # Right / Tab
            focus = (focus + 1) % len(btns)
        elif k in (10, 13, curses.KEY_ENTER):
            _reset()
            return (OK, CANCEL, EXTRA)[min(focus, 2)]
        elif k == 27:                            # ESC
            _reset(); return CANCEL


def dlg_radiolist(title, text, items, default=0, extra_label=None):
    """Scrollable radio-button list.
    Up/Down navigate; Enter/Space confirm; Tab moves focus to buttons.
    Returns (OK/CANCEL/EXTRA, selected_index).
    """
    sh, sw = _dims()
    w = min(90, sw - 2)
    text_lines = _wrap(text, w - 4)
    tl     = len(text_lines)
    list_h = min(len(items), max(3, sh - tl - 7))
    h      = tl + list_h + 5
    win    = _new_win(h, w)
    _frame(win, title)
    _put_text(win, text_lines, 1, w)
    btns      = ['OK', 'Cancel'] + ([extra_label] if extra_label else [])
    cur       = default   # cursor / highlight (moves with arrows)
    sel       = default   # selection / asterisk (moves with Space)
    scroll    = max(0, min(cur, len(items) - list_h))
    list_y    = tl + 1
    btn_focus = -1    # -1 = list focused, >=0 = button index

    while True:
        attr_n = curses.color_pair(1) if curses.has_colors() else 0
        attr_s = curses.color_pair(3) if curses.has_colors() else curses.A_REVERSE
        for row in range(list_h):
            idx = scroll + row
            y   = list_y + row
            if idx >= len(items):
                try: win.addstr(y, 2, ' ' * (w - 4), attr_n)
                except curses.error: pass
                continue
            radio = '(*) ' if idx == sel else '( ) '   # asterisk = selection
            line  = (radio + items[idx]).ljust(w - 4)[:w - 4]
            highlighted = (idx == cur and btn_focus < 0)
            try:
                win.addstr(y, 2, line, attr_s if highlighted else attr_n)
            except curses.error:
                pass
        _put_buttons(win, btns, btn_focus)
        win.refresh()
        k = win.getch()

        if btn_focus < 0:
            if k == curses.KEY_UP and cur > 0:
                cur -= 1
                if cur < scroll: scroll = cur
            elif k == curses.KEY_DOWN and cur < len(items) - 1:
                cur += 1
                if cur >= scroll + list_h: scroll = cur - list_h + 1
            elif k == ord(' '):                          # Space: move asterisk here
                sel = cur
            elif k in (9, 10, 13, curses.KEY_ENTER):    # Tab / Enter: go to buttons
                btn_focus = 0
            elif k == 27:
                _reset(); return CANCEL, sel
        else:
            if k == curses.KEY_LEFT:
                btn_focus = (btn_focus - 1) % len(btns)
            elif k in (curses.KEY_RIGHT, 9):
                btn_focus = (btn_focus + 1) % len(btns)
            elif k == curses.KEY_UP:
                btn_focus = -1
            elif k in (10, 13, curses.KEY_ENTER):
                _reset()
                if btn_focus == 0: return OK, sel
                if btn_focus == 1: return CANCEL, sel
                return EXTRA, sel
            elif k == 27:
                _reset(); return CANCEL, sel


def dlg_passwordbox(title, text):
    """Masked password entry.  Type to input; Tab/Enter moves to buttons.
    Returns (OK/CANCEL, password_string).
    """
    sh, sw = _dims()
    w = min(74, sw - 2)
    lines   = _wrap(text, w - 4)
    h       = len(lines) + 6
    win     = _new_win(h, w)
    _frame(win, title)
    _put_text(win, lines, 1, w)
    field_w  = w - 5
    input_y  = len(lines) + 2
    password = []
    in_field = True
    btn_focus = 0
    attr = curses.color_pair(1) if curses.has_colors() else 0
    curses.curs_set(1)

    while True:
        display = ('*' * len(password)).ljust(field_w)[:field_w]
        try:
            win.addstr(input_y, 2, f'[{display}]', attr)
            if in_field:
                win.move(input_y, 3 + min(len(password), field_w - 1))
        except curses.error:
            pass
        _put_buttons(win, ['OK', 'Cancel'], -1 if in_field else btn_focus)
        win.refresh()
        k = win.getch()

        if in_field:
            if k in (10, 13, curses.KEY_ENTER, 9):
                in_field = False; btn_focus = 0; curses.curs_set(0)
            elif k == 27:
                curses.curs_set(0); _reset(); return CANCEL, ''
            elif k in (curses.KEY_BACKSPACE, 127, 8) and password:
                password.pop()
            elif 32 <= k <= 126:
                password.append(chr(k))
        else:
            if k == curses.KEY_LEFT:
                btn_focus = (btn_focus - 1) % 2
            elif k in (curses.KEY_RIGHT, 9):
                btn_focus = (btn_focus + 1) % 2
            elif k == curses.KEY_UP:
                in_field = True; curses.curs_set(1)
            elif k in (10, 13, curses.KEY_ENTER):
                _reset()
                return (OK, ''.join(password)) if btn_focus == 0 else (CANCEL, '')
            elif k == 27:
                _reset(); return CANCEL, ''


class Gauge:
    """In-process progress bar updated from the same thread doing I/O."""
    def __init__(self, title, text):
        sh, sw = _dims()
        self._w    = min(78, sw - 2)
        self._tl   = _wrap(text, self._w - 4)
        self._h    = len(self._tl) + 4
        self._title = title
        self._pct  = 0
        self._win  = _new_win(self._h, self._w)
        self._attr = curses.color_pair(1) if curses.has_colors() else 0
        self._draw()

    def _draw(self):
        win, w, h = self._win, self._w, self._h
        win.erase()
        _frame(win, self._title)
        _put_text(win, self._tl, 1, w)
        bar_w  = w - 8
        filled = int(self._pct * bar_w / 100)
        y = h - 2
        try:
            win.addstr(y, 2,          ' ' * filled,            self._attr | curses.A_REVERSE)
            win.addstr(y, 2 + filled, ' ' * (bar_w - filled),  self._attr)
            win.addstr(y, w - 6,      f'{self._pct:3d}%',       self._attr | curses.A_BOLD)
        except curses.error:
            pass
        win.refresh()

    def update(self, percent):
        self._pct = min(100, max(0, int(percent)))
        self._draw()

    def close(self):
        try:
            self._win.erase(); self._win.refresh()
        except Exception:
            pass
        del self._win
        _reset()


# ── Disk enumeration ──────────────────────────────────────────────────────────

def list_disks():
    """Return (devices, labels) lists for physical disks."""
    devices, labels = [], []

    if OS == 'Linux':
        # Read physical disk info directly from sysfs (no lsblk needed).
        for entry in sorted(Path('/sys/block').iterdir()):
            try:
                uevent = (entry / 'uevent').read_text()
            except OSError:
                continue
            if 'DEVTYPE=disk' not in uevent:
                continue
            dev = f'/dev/{entry.name}'
            try:
                sectors = int((entry / 'size').read_text().strip())
            except (OSError, ValueError):
                continue
            if sectors == 0:
                continue
            model = ''
            for mpath in ('device/model', 'device/name'):
                try:
                    model = (entry / mpath).read_text().strip()
                    break
                except OSError:
                    pass
            devices.append(dev)
            labels.append(f'{model or "Unknown"} — {format_size(sectors * 512)} — {dev}')

    elif OS == 'Darwin':
        raw = subprocess.run(
            ['diskutil', 'list'], capture_output=True, text=True,
        ).stdout
        for line in raw.splitlines():
            parts = line.split()
            if not parts or 'physical' not in line:
                continue
            disk = parts[0].rstrip(':')
            if not disk.startswith('/dev/disk'):
                continue
            info = subprocess.run(
                ['diskutil', 'info', disk], capture_output=True, text=True,
            ).stdout
            model = size = ''
            for iline in info.splitlines():
                if not model and ('Media Name' in iline or 'Device / Media Name' in iline):
                    model = iline.split(':', 1)[-1].strip()
                if not size and 'Disk Size' in iline:
                    size = iline.split(':', 1)[-1].strip().split('(')[0].strip()
            devices.append(disk)
            labels.append(f'{model or "Unknown"} — {size or "?"} — {disk}')

    return devices, labels


# ── Image enumeration ─────────────────────────────────────────────────────────

def list_images(images_dir):
    """Return (paths, labels) for regular files found in images_dir."""
    paths, labels = [], []
    try:
        entries = sorted(Path(images_dir).iterdir())
    except OSError:
        return paths, labels
    for p in entries:
        if p.is_file():
            paths.append(str(p))
            labels.append(f'{p.name} — {format_size(p.stat().st_size)}')
    return paths, labels


# ── Sudo ──────────────────────────────────────────────────────────────────────

def obtain_sudo():
    """Ensure a valid sudo session; prompt for password if needed.
    Returns True on success, False if the user cancels.
    """
    if os.getuid() == 0:
        return True
    if subprocess.run(['sudo', '-n', '-v'], capture_output=True).returncode == 0:
        return True   # existing timestamp is valid

    while True:
        code, password = dlg_passwordbox(
            'Administrator permission required',
            'Enter your password to unmount and overwrite the selected disk.',
        )
        if code != OK:
            return False

        result = subprocess.run(
            ['sudo', '-S', '-p', '', '-v'],
            input=password + '\n', capture_output=True, text=True,
        )
        if result.returncode == 0:
            return True

        err = result.stderr.strip() or \
            'Authentication failed or this account is not permitted to use sudo.'
        code = dlg_yesno(
            'sudo failed', f'{err}\n\nRetry authentication?',
            yes='Retry', no='Exit',
        )
        if code != OK:
            return False


# ── Disk unmount ──────────────────────────────────────────────────────────────

def unmount_target(disk):
    """Unmount all partitions on disk. Returns (ok, error_msg)."""
    if OS == 'Linux':
        # Partitions appear as subdirectories named <disk><N> in sysfs.
        disk_name = Path(disk).name
        sys_disk = Path('/sys/block') / disk_name
        for entry in sorted(sys_disk.iterdir()):
            if entry.name.startswith(disk_name):
                run_root('umount', f'/dev/{entry.name}', capture_output=True)
        run_root('umount', disk, capture_output=True)
        return True, ''

    if OS == 'Darwin':
        r = run_root('diskutil', 'unmountDisk', disk, capture_output=True, text=True)
        if r.returncode != 0:
            err = (r.stdout + r.stderr).strip()
            return False, err or 'Could not unmount the selected disk.'
        return True, ''

    return True, ''


# ── Wizard steps ──────────────────────────────────────────────────────────────

def select_disk(state):
    """Step 1: pick a physical disk. Returns True on success, False to quit."""
    rescan = True
    while True:
        if rescan:
            state['disk_devices'], state['disk_labels'] = list_disks()
            rescan = False

        devices, labels = state['disk_devices'], state['disk_labels']

        if not devices:
            code = dlg_yesno(
                'No disks detected',
                'No physical disks were detected.\n\n'
                'Plug in a disk and press Refresh to scan again.',
                yes='Refresh', no='Exit',
            )
            if code == OK:
                rescan = True
                continue
            return False

        idx = min(state['disk_index'], len(devices) - 1)
        code, i = dlg_radiolist(
            'Step 1 of 4 — Select target disk',
            'Select the disk to overwrite. All data on it will be destroyed.',
            labels, default=idx, extra_label='Refresh',
        )
        if code == OK:
            state['disk_index'] = i
            state['selected_disk'] = devices[i]
            state['selected_disk_label'] = labels[i]
            return True
        elif code == EXTRA:   # Refresh
            rescan = True
        else:
            return False


def select_image(state, images_dir):
    """Step 2: pick an image file. Returns 0 on success, 2 for Back."""
    rescan = True
    while True:
        if rescan:
            state['image_paths'], state['image_labels'] = list_images(images_dir)
            rescan = False

        paths, labels = state['image_paths'], state['image_labels']

        if not paths:
            code = dlg_yesno(
                'No images found',
                f'No image files found in:\n\n{images_dir}\n\n'
                'Add images to the directory and press Refresh.',
                yes='Refresh', no='Back',
            )
            if code == OK:
                rescan = True
                continue
            return 2

        idx = min(state['image_index'], len(paths) - 1)
        code, i = dlg_radiolist(
            'Step 2 of 4 — Select image',
            f'Select a raw disk image from:\n{images_dir}',
            labels, default=idx, extra_label='Refresh',
        )
        if code == OK:
            state['image_index'] = i
            state['selected_image'] = paths[i]
            state['selected_image_label'] = labels[i]
            return 0
        elif code == EXTRA:   # Refresh
            rescan = True
        else:
            return 2


def flash_image(state):
    """Step 3: confirm and write the image to disk.
    Returns 0 (success), 1 (error), or 2 (back).
    """
    disk = state['selected_disk']
    out_dev = raw_device(disk)

    code = dlg_yesno(
        'Confirm destructive operation',
        f'Image:\n{state["selected_image_label"]}\n\n'
        f'Target:\n{state["selected_disk_label"]}\n\n'
        'WARNING: All data on the target disk will be permanently overwritten.',
        yes='Flash', no='Back',
    )
    if code != OK:
        return 2

    image, err = unpack_image(state['selected_image'])
    if err:
        show_error(err)
        return 2
    if image != state['selected_image']:
        state['unpacked_image'] = image

    if not obtain_sudo():
        remove_unpacked_image(state)
        return 2

    ok, err = unmount_target(disk)
    if not ok:
        show_error(err)
        remove_unpacked_image(state)
        state['flash_result'] = 1
        return 1

    image_size = Path(image).stat().st_size
    gauge = Gauge(
        'Step 3 of 4 — Flashing',
        f'Writing {Path(image).name} to {disk}\n\n'
        'Do not remove the disk or power off the computer.',
    )

    # Python reads the image in chunks and writes directly (root) or via
    # a privileged `sudo python3` subprocess (non-root). No dd needed.
    CHUNK = 4 * 1024 * 1024
    write_ok = True
    err_details = ''

    if os.getuid() == 0:
        # Root: open the device directly.
        try:
            with open(image, 'rb') as src, open(out_dev, 'wb') as dst:
                written = 0
                for chunk in iter(lambda: src.read(CHUNK), b''):
                    dst.write(chunk)
                    written += len(chunk)
                    gauge.update(min(written * 100 // image_size, 99))
        except OSError as e:
            write_ok = False
            err_details = str(e)
    else:
        # Non-root: open device via sudo python3 receiving data on stdin.
        writer_script = (
            'import sys\n'
            'f=open(sys.argv[1],"wb")\n'
            'while True:\n'
            ' c=sys.stdin.buffer.read(4194304)\n'
            ' if not c:break\n'
            ' f.write(c)\n'
            'f.close()\n'
        )
        writer_proc = subprocess.Popen(
            ['sudo', '-n', 'python3', '-c', writer_script, out_dev],
            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        try:
            with open(image, 'rb') as src:
                written = 0
                for chunk in iter(lambda: src.read(CHUNK), b''):
                    writer_proc.stdin.write(chunk)
                    written += len(chunk)
                    gauge.update(min(written * 100 // image_size, 99))
        except (OSError, BrokenPipeError):
            write_ok = False
        try:
            writer_proc.stdin.close()
        except Exception:
            pass
        writer_proc.wait()
        if writer_proc.returncode != 0:
            write_ok = False
            err_details = 'sudo python3 failed; ensure python3 is in sudo\'s PATH.'

    if write_ok:
        try:
            os.sync()   # flush kernel write buffers to device
        except OSError:
            pass

    gauge.close()

    if not write_ok:
        remove_unpacked_image(state)
        state['flash_details'] = err_details or \
            'The image could not be written to the selected disk.'
        state['flash_result'] = 1
        return 1

    state['flash_result'] = 0
    return 0


def verify_flash(state):
    """Step 4a: compare SHA-256 of the source image vs. the first image_size
    bytes read back from the device. Sets state['verify_result'] to 0 or 1.
    Uses Python's hashlib — no external sha256sum needed.
    """
    image = state.get('unpacked_image', state['selected_image'])
    disk = state['selected_disk']
    out_dev = raw_device(disk)
    image_size = Path(image).stat().st_size

    gauge = Gauge(
        'Step 4 of 4 — Verifying',
        f'Verifying {Path(image).name} against {disk}\n\n'
        'Do not remove the disk or power off the computer.',
    )

    # Hash the source image in a background thread while reading the device.
    img_hash = [None]
    hash_error = [None]

    def _hash_source():
        try:
            h = hashlib.sha256()
            with open(image, 'rb') as f:
                for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
                    h.update(chunk)
            img_hash[0] = h.hexdigest()
        except OSError as exc:
            hash_error[0] = str(exc)

    src_thread = threading.Thread(target=_hash_source, daemon=True)
    src_thread.start()

    # Hash the first image_size bytes read back from the device.
    CHUNK = 4 * 1024 * 1024
    h = hashlib.sha256()
    remaining = image_size
    read_ok = True

    if os.getuid() == 0:
        # Root: open device directly.
        try:
            with open(out_dev, 'rb') as dev:
                while remaining > 0:
                    chunk = dev.read(min(CHUNK, remaining))
                    if not chunk:
                        read_ok = False
                        break
                    h.update(chunk)
                    remaining -= len(chunk)
                    gauge.update(min((image_size - remaining) * 100 // image_size, 99))
        except OSError as exc:
            read_ok = False
            state['verify_details'] = str(exc)
    else:
        # Non-root: stream device via sudo python3.
        reader_script = (
            'import sys\n'
            'f=open(sys.argv[1],"rb")\n'
            'while True:\n'
            ' c=f.read(4194304)\n'
            ' if not c:break\n'
            ' sys.stdout.buffer.write(c)\n'
            ' sys.stdout.buffer.flush()\n'
        )
        reader_proc = subprocess.Popen(
            ['sudo', '-n', 'python3', '-c', reader_script, out_dev],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        try:
            while remaining > 0:
                chunk = reader_proc.stdout.read(min(CHUNK, remaining))
                if not chunk:
                    read_ok = False
                    break
                h.update(chunk)
                remaining -= len(chunk)
                gauge.update(min((image_size - remaining) * 100 // image_size, 99))
        except (OSError, BrokenPipeError) as exc:
            read_ok = False
            state['verify_details'] = str(exc)
        reader_proc.stdout.close()   # closing pipe sends SIGPIPE to reader
        reader_proc.wait()
    try:
        src_thread.join()
        if hash_error[0]:
            state['verify_details'] = f'Could not hash source image: {hash_error[0]}'
        elif not read_ok:
            state['verify_details'] = state.get(
                'verify_details', 'Could not read the selected disk.',
            )
        elif h.hexdigest() != img_hash[0]:
            state['verify_details'] = 'Data read from the disk differs from the source image.'
        else:
            state['verify_details'] = ''
        match = read_ok and not hash_error[0] and h.hexdigest() == img_hash[0]
        state['verify_result'] = 0 if match else 1
        if not match:
            state['verify_log'] = write_verification_log(
                image, out_dev, image_size, image_size - remaining,
                img_hash[0], h.hexdigest(), state['verify_details'],
            )
    finally:
        gauge.close()
        remove_unpacked_image(state)


def show_result(state):
    """Show the flash/verify outcome. Returns True to restart, False to exit."""
    fr, vr = state['flash_result'], state['verify_result']
    img  = state['selected_image_label']
    disk = state['selected_disk_label']

    if fr:
        title = 'Step 4 of 4 — Failed'
        body  = f'Flashing failed.\n\n{state["flash_details"]}'
    elif vr:
        title = 'Step 4 of 4 — Verification failed'
        body  = (
            'The image was written but verification failed.\n'
            'The data on disk does not match the source image.\n\n'
            f'{state.get("verify_details", "")}\n\n'
            f'Diagnostic log:\n{state.get("verify_log", "unavailable")}\n\n'
            f'Image:\n{img}\n\nTarget:\n{disk}'
        )
    else:
        title = 'Step 4 of 4 — Success'
        body  = (
            'Flashing completed and verified successfully.\n\n'
            f'Image:\n{img}\n\nTarget:\n{disk}\n\n'
            'The operating system may now detect new partitions on the target disk.'
        )

    return dlg_yesno(title, body, yes='Restart', no='Exit') == OK


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Portable raw disk image flasher (curses TUI, stdlib only).',
    )
    parser.add_argument(
        '-d', '--images-dir',
        metavar='DIRECTORY',
        help='Directory containing raw disk images '
             '(default: images/ beside this script)',
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    images_dir = args.images_dir or str(script_dir / 'images')

    if not Path(images_dir).is_dir():
        sys.exit(f'Error: images directory does not exist: {images_dir}')

    check_dependencies()

    global _temp_dir
    _temp_dir = tempfile.mkdtemp(prefix='image-flasher.')
    tui_start()

    state = {
        # disk selection
        'disk_devices': [], 'disk_labels': [], 'disk_index': 0,
        'selected_disk': '', 'selected_disk_label': '',
        # image selection
        'image_paths': [], 'image_labels': [], 'image_index': 0,
        'selected_image': '', 'selected_image_label': '',
        # results
        'flash_result': 0, 'flash_details': '',
        'verify_result': 0, 'verify_details': '', 'verify_log': '',
    }

    step = 1
    while True:
        if step == 1:
            if not select_disk(state):
                break
            step = 2
        elif step == 2:
            step = 3 if select_image(state, images_dir) == 0 else 1
        elif step == 3:
            r = flash_image(state)
            step = 2 if r == 2 else 4
        elif step == 4:
            if state['flash_result'] == 0:
                verify_flash(state)
            if not show_result(state):
                break
            step = 1


if __name__ == '__main__':
    main()
