#!/usr/bin/env bash
# Portable dialog-based raw disk image flasher for Linux and macOS.
# WARNING: The selected target disk is completely overwritten.

set -u
set -o pipefail

PROGRAM_NAME="Image Flasher"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
IMAGES_DIR="$SCRIPT_DIR/images"
OS="$(uname -s 2>/dev/null || true)"
TEMP_DIR=""
SUDO_PASSWORD=""
FLASH_RESULT=0
FLASH_DETAILS=""
VERIFY_RESULT=0
SELECTED_DISK_INDEX=0
SELECTED_IMAGE_INDEX=0

# Print usage information and exit.
usage() {
    cat <<USAGE
Usage: $(basename "$0") [-d DIRECTORY | --images-dir DIRECTORY]

Options:
  -d, --images-dir DIRECTORY  Directory containing raw disk images.
                              Default: images/ beside this script.
  -h, --help                  Show this help.
USAGE
}

while (($# > 0)); do
    case "$1" in
        -d|--images-dir)
            if (($# < 2)); then
                printf 'Error: %s requires a directory argument.\n' "$1" >&2
                exit 2
            fi
            IMAGES_DIR=$2
            shift 2
            ;;
        --images-dir=*)
            IMAGES_DIR=${1#*=}
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        *)
            printf 'Error: unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

# Verify that all required external tools are present on the system.
# Exits with an error if any are missing. The required set varies by OS
# and whether the script is running as root.
check_dependencies() {
    local missing=()
    local commands=(dialog pv dd head find wc awk sed sync uname basename dirname mktemp mkfifo rm id)

    case "$OS" in
        Linux)
            commands+=(lsblk umount)
            ;;
        Darwin)
            commands+=(diskutil)
            ;;
        *)
            printf 'Error: unsupported operating system: %s\n' "$OS" >&2
            exit 1
            ;;
    esac

    if [[ $(id -u) -ne 0 ]]; then
        commands+=(sudo)
    fi

    local command_name
    for command_name in "${commands[@]}"; do
        if ! command -v "$command_name" >/dev/null 2>&1; then
            missing+=("$command_name")
        fi
    done

    # Require either sha256sum (Linux/GNU coreutils) or shasum (macOS/Perl).
    if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
        missing+=(sha256sum)
    fi

    if ((${#missing[@]} > 0)); then
        printf 'Error: missing required tools:' >&2
        printf ' %s' "${missing[@]}" >&2
        printf '\n' >&2
        exit 1
    fi
}

# Trap handler called on EXIT, INT, TERM, and HUP signals.
# Closes any open file descriptor 3, removes the temporary directory,
# wipes the sudo password from memory, and clears the terminal.
cleanup() {
    local exit_code=$?
    exec 3>&- 2>/dev/null || true
    [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]] && rm -rf -- "$TEMP_DIR"
    SUDO_PASSWORD=""
    clear 2>/dev/null || true
    exit "$exit_code"
}

check_dependencies
trap cleanup EXIT INT TERM HUP

if [[ ! -d "$IMAGES_DIR" ]]; then
    printf 'Error: images directory does not exist: %s\n' "$IMAGES_DIR" >&2
    exit 1
fi

TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/image-flasher.XXXXXX") || {
    printf 'Error: could not create temporary directory.\n' >&2
    exit 1
}

BACKTITLE="$PROGRAM_NAME"

# Display a modal error dialog with the given message.
show_error() {
    dialog --clear --backtitle "$BACKTITLE" --title "Error" \
        --msgbox "$1" 9 70
}

# Display a modal informational dialog with the given message.
show_info() {
    dialog --clear --backtitle "$BACKTITLE" --title "Information" \
        --msgbox "$1" 9 70
}

# Compute the SHA-256 digest of stdin and print only the lowercase hex hash.
# Supports sha256sum (Linux/GNU) and shasum (macOS).
sha256_stream() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum | awk '{print $1}'
    else
        shasum -a 256 | awk '{print $1}'
    fi
}

# Convert a byte count to a human-readable size string (e.g. 1.4 GiB).
format_file_size() {
    local bytes=$1
    awk -v bytes="$bytes" 'BEGIN {
        split("B KiB MiB GiB TiB", units, " ");
        value = bytes + 0;
        unit = 1;
        while (value >= 1024 && unit < 5) {
            value /= 1024;
            unit++;
        }
        if (unit == 1) printf "%d %s", value, units[unit];
        else printf "%.1f %s", value, units[unit];
    }'
}

# Populate the global arrays DISK_DEVICES and DISK_LABELS with physical
# block devices reported by lsblk on Linux.
list_disks_linux() {
    DISK_DEVICES=()
    DISK_LABELS=()

    local line device size type model
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        device=$(printf '%s\n' "$line" | awk '{print $1}')
        size=$(printf '%s\n' "$line" | awk '{print $2}')
        type=$(printf '%s\n' "$line" | awk '{print $NF}')
        model=$(printf '%s\n' "$line" | awk '{$1=""; $2=""; $NF=""; sub(/^ +/, ""); sub(/ +$/, ""); print}')
        [[ "$type" != "disk" ]] && continue
        [[ -z "$model" ]] && model="Unknown model"
        DISK_DEVICES+=("$device")
        DISK_LABELS+=("$model — $size — $device")
    done < <(lsblk -dnpo NAME,SIZE,MODEL,TYPE 2>/dev/null)
}

# Populate the global arrays DISK_DEVICES and DISK_LABELS with physical
# disks reported by diskutil on macOS.
list_disks_macos() {
    DISK_DEVICES=()
    DISK_LABELS=()

    local device info model size
    while IFS= read -r device; do
        [[ -z "$device" ]] && continue
        info=$(diskutil info "$device" 2>/dev/null || true)
        model=$(printf '%s\n' "$info" | awk -F: '/Media Name/ {sub(/^[[:space:]]+/, "", $2); print $2; exit}')
        [[ -z "$model" ]] && model=$(printf '%s\n' "$info" | awk -F: '/Device \/ Media Name/ {sub(/^[[:space:]]+/, "", $2); print $2; exit}')
        size=$(printf '%s\n' "$info" | awk -F: '/Disk Size/ {sub(/^[[:space:]]+/, "", $2); sub(/ \(.*/, "", $2); print $2; exit}')
        [[ -z "$model" ]] && model="Unknown model"
        [[ -z "$size" ]] && size="Unknown size"
        DISK_DEVICES+=("$device")
        DISK_LABELS+=("$model — $size — $device")
    done < <(diskutil list 2>/dev/null | awk '/^\/dev\/disk[0-9]+ .*physical/ {sub(/:$/, "", $1); print $1}')
}

# Step 1: Present a radiolist of physical disks and let the user choose one.
# Sets SELECTED_DISK and SELECTED_DISK_LABEL on success.
# Returns 1 if no disks are found or the user cancels (exits the program).
select_disk() {
    case "$OS" in
        Linux) list_disks_linux ;;
        Darwin) list_disks_macos ;;
    esac

    if ((${#DISK_DEVICES[@]} == 0)); then
        show_error "No physical disks were detected."
        return 1
    fi

    local menu_items=()
    local i state
    # Clamp the remembered index in case the disk list changed between visits.
    ((SELECTED_DISK_INDEX >= ${#DISK_DEVICES[@]})) && SELECTED_DISK_INDEX=0
    for ((i = 0; i < ${#DISK_DEVICES[@]}; i++)); do
        state="off"
        ((i == SELECTED_DISK_INDEX)) && state="on"
        menu_items+=("$i" "${DISK_LABELS[$i]}" "$state")
    done

    local selected
    selected=$(dialog --clear --backtitle "$BACKTITLE" \
        --title "Step 1 of 4 — Select target disk" \
        --radiolist "Select the disk to overwrite. All data on it will be destroyed." \
        20 90 12 "${menu_items[@]}" 3>&1 1>&2 2>&3) || return 1

    SELECTED_DISK_INDEX=$selected
    SELECTED_DISK=${DISK_DEVICES[$selected]}
    SELECTED_DISK_LABEL=${DISK_LABELS[$selected]}
}

# Step 2: Scan IMAGES_DIR for regular files and let the user pick one.
# Sets SELECTED_IMAGE and SELECTED_IMAGE_LABEL on success.
# Returns 1 on error, 2 if the user pressed Back (return to step 1).
select_image() {
    IMAGE_PATHS=()
    IMAGE_LABELS=()

    local path size bytes
    while IFS= read -r -d '' path; do
        bytes=$(wc -c < "$path" | awk '{print $1}')
        size=$(format_file_size "$bytes")
        IMAGE_PATHS+=("$path")
        IMAGE_LABELS+=("$(basename "$path") — $size")
    done < <(find "$IMAGES_DIR" -maxdepth 1 -type f -print0 2>/dev/null | sort -z)

    if ((${#IMAGE_PATHS[@]} == 0)); then
        show_error "No image files were found in:\n\n$IMAGES_DIR"
        return 1
    fi

    local menu_items=()
    local i state
    # Clamp the remembered index in case the image list changed between visits.
    ((SELECTED_IMAGE_INDEX >= ${#IMAGE_PATHS[@]})) && SELECTED_IMAGE_INDEX=0
    for ((i = 0; i < ${#IMAGE_PATHS[@]}; i++)); do
        state="off"
        ((i == SELECTED_IMAGE_INDEX)) && state="on"
        menu_items+=("$i" "${IMAGE_LABELS[$i]}" "$state")
    done

    local selected
    selected=$(dialog --clear --backtitle "$BACKTITLE" \
        --title "Step 2 of 4 — Select image" \
        --cancel-label "Back" \
        --radiolist "Select a raw disk image from:\n$IMAGES_DIR" \
        20 90 12 "${menu_items[@]}" 3>&1 1>&2 2>&3) || return 2

    SELECTED_IMAGE_INDEX=$selected
    SELECTED_IMAGE=${IMAGE_PATHS[$selected]}
    SELECTED_IMAGE_LABEL=${IMAGE_LABELS[$selected]}
}

# Ensure the script has root privileges needed to write to a raw device.
# If already root, returns immediately. Otherwise attempts to refresh the
# sudo timestamp, prompting for a password if necessary.
# Returns 1 if the user cancels or chooses not to retry a failed password.
obtain_sudo() {
    [[ $(id -u) -eq 0 ]] && return 0

    # Reuse a valid existing sudo timestamp without asking for a password.
    if sudo -n -v >/dev/null 2>&1; then
        return 0
    fi

    while true; do
        SUDO_PASSWORD=$(dialog --clear --backtitle "$BACKTITLE" \
            --title "Administrator permission required" \
            --cancel-label "Back" \
            --insecure --passwordbox \
            "Enter your password to unmount and overwrite the selected disk." \
            10 70 3>&1 1>&2 2>&3) || return 1

        if printf '%s\n' "$SUDO_PASSWORD" | sudo -S -p '' -v >/dev/null 2>"$TEMP_DIR/sudo-error"; then
            SUDO_PASSWORD=""
            return 0
        fi

        SUDO_PASSWORD=""
        local error_text
        error_text=$(sed -n '1,4p' "$TEMP_DIR/sudo-error")
        [[ -z "$error_text" ]] && error_text="Authentication failed or this account is not permitted to use sudo."

        if ! dialog --clear --backtitle "$BACKTITLE" --title "sudo failed" \
            --yes-label "Retry" --no-label "Exit" \
            --yesno "$error_text\n\nRetry authentication?" 12 76; then
            return 1
        fi
    done
}

# Run a command as root: directly if already root, via sudo -n otherwise.
run_as_root() {
    if [[ $(id -u) -eq 0 ]]; then
        "$@"
    else
        sudo -n "$@"
    fi
}

# Unmount all partitions of SELECTED_DISK so dd can write to the raw device.
# On Linux, iterates every child partition reported by lsblk.
# On macOS, uses diskutil unmountDisk which handles all partitions at once.
unmount_target() {
    case "$OS" in
        Linux)
            local partition
            while IFS= read -r partition; do
                [[ -z "$partition" ]] && continue
                run_as_root umount "$partition" >/dev/null 2>&1 || true
            done < <(lsblk -lnpo NAME "$SELECTED_DISK" 2>/dev/null | awk 'NR > 1')
            run_as_root umount "$SELECTED_DISK" >/dev/null 2>&1 || true
            ;;
        Darwin)
            run_as_root diskutil unmountDisk "$SELECTED_DISK" >"$TEMP_DIR/unmount-output" 2>&1
            ;;
    esac
}

# Return the path to the raw (unbuffered) output device.
# On macOS, /dev/diskN must be opened as /dev/rdiskN for direct access;
# on Linux the block device path is used as-is.
raw_output_device() {
    if [[ "$OS" == "Darwin" ]]; then
        printf '%s\n' "$SELECTED_DISK" | sed 's#^/dev/disk#/dev/rdisk#'
    else
        printf '%s\n' "$SELECTED_DISK"
    fi
}

# Step 3: Confirm the operation with the user, obtain sudo if needed,
# unmount the target disk, then write the image with dd while showing a
# progress bar via pv. Finishes with a sync to flush kernel write buffers.
# Sets FLASH_RESULT (0 = success, 1 = failed) and FLASH_DETAILS on failure.
# Returns 2 if the user pressed Back at the confirmation or sudo prompt.
flash_image() {
    local image_size output_device progress_fifo dd_error pv_error
    image_size=$(wc -c < "$SELECTED_IMAGE" | awk '{print $1}')
    output_device=$(raw_output_device)
    progress_fifo="$TEMP_DIR/progress.fifo"
    dd_error="$TEMP_DIR/dd-error"
    pv_error="$TEMP_DIR/pv-error"

    # Build the display form of the write command so the user can verify it.
    local sudo_prefix=""
    [[ $(id -u) -ne 0 ]] && sudo_prefix="sudo "
    local flash_cmd="pv \"$SELECTED_IMAGE\" | ${sudo_prefix}dd of=\"$output_device\" bs=4194304"

    if ! dialog --clear --backtitle "$BACKTITLE" \
        --title "Confirm destructive operation" \
        --yes-label "Flash" --no-label "Back" \
        --yesno "Image:\n$SELECTED_IMAGE_LABEL\n\nTarget:\n$SELECTED_DISK_LABEL\n\nCommand:\n$flash_cmd\n\nWARNING: All data on the target disk will be permanently overwritten." \
        20 86; then
        return 2
    fi

    obtain_sudo || return 2

    if ! unmount_target; then
        local unmount_error
        unmount_error=$(sed -n '1,8p' "$TEMP_DIR/unmount-output" 2>/dev/null || true)
        [[ -z "$unmount_error" ]] && unmount_error="Could not unmount the selected disk."
        show_error "$unmount_error"
        return 1
    fi

    mkfifo "$progress_fifo"

    dialog --clear --backtitle "$BACKTITLE" \
        --title "Step 3 of 4 — Flashing" \
        --gauge "Writing $(basename "$SELECTED_IMAGE") to $SELECTED_DISK\n\nDo not remove the disk or power off the computer." \
        12 78 0 < "$progress_fifo" &
    local dialog_pid=$!

    # Keep the gauge FIFO open until dd and sync have completed.
    exec 3>"$progress_fifo"

    set +e
    pv -n -s "$image_size" "$SELECTED_IMAGE" 2> >(
        while IFS= read -r percent; do
            percent=${percent%.*}
            [[ "$percent" =~ ^[0-9]+$ ]] || continue
            ((percent > 99)) && percent=99
            printf '%s\n' "$percent" >&3
        done
    ) | run_as_root dd of="$output_device" bs=4194304 2>"$dd_error"
    local flash_status=$?
    set -e

    if ((flash_status == 0)); then
        run_as_root sync >/dev/null 2>&1
        flash_status=$?
    fi

    if ((flash_status == 0)); then
        printf '100\n' >&3
    fi

    exec 3>&-
    wait "$dialog_pid" 2>/dev/null || true
    rm -f -- "$progress_fifo"

    if ((flash_status != 0)); then
        FLASH_DETAILS=$(sed -n '1,10p' "$dd_error" 2>/dev/null || true)
        [[ -z "$FLASH_DETAILS" ]] && FLASH_DETAILS="The image could not be written to the selected disk."
        FLASH_RESULT=1
        return 1
    fi

    FLASH_RESULT=0
}

# Reads back exactly as many bytes as the source image from the target device,
# computes SHA-256 on both streams in parallel, and compares the digests.
# Displays a progress gauge while reading from the device.
# Sets VERIFY_RESULT to 0 (hashes match) or 1 (mismatch or read error).
verify_flash() {
    local image_size output_device verify_fifo img_hash_file img_hash dev_hash
    image_size=$(wc -c < "$SELECTED_IMAGE" | awk '{print $1}')
    output_device=$(raw_output_device)
    verify_fifo="$TEMP_DIR/verify.fifo"
    img_hash_file="$TEMP_DIR/img-hash"

    mkfifo "$verify_fifo"

    dialog --clear --backtitle "$BACKTITLE" \
        --title "Step 4 of 4 \u2014 Verifying" \
        --gauge "Verifying $(basename "$SELECTED_IMAGE") against $SELECTED_DISK\n\nDo not remove the disk or power off the computer." \
        12 78 0 < "$verify_fifo" &
    local dialog_pid=$!

    # Keep the gauge FIFO open for the duration of the read-back.
    exec 3>"$verify_fifo"

    # Hash the source image in the background while reading back from the device.
    sha256_stream < "$SELECTED_IMAGE" > "$img_hash_file" &
    local img_hash_pid=$!

    set +e
    dev_hash=$(run_as_root dd if="$output_device" bs=4194304 2>/dev/null \
        | head -c "$image_size" \
        | pv -n -s "$image_size" 2> >(
            while IFS= read -r percent; do
                percent=${percent%.*}
                [[ "$percent" =~ ^[0-9]+$ ]] || continue
                ((percent > 99)) && percent=99
                printf '%s\n' "$percent" >&3
            done
        ) \
        | sha256_stream)
    local verify_status=$?
    set -e

    wait "$img_hash_pid" 2>/dev/null || true
    img_hash=$(cat "$img_hash_file" 2>/dev/null || true)

    printf '100\n' >&3
    exec 3>&-
    wait "$dialog_pid" 2>/dev/null || true
    rm -f -- "$verify_fifo"

    if ((verify_status != 0)) || [[ -z "$img_hash" || -z "$dev_hash" || "$img_hash" != "$dev_hash" ]]; then
        VERIFY_RESULT=1
    else
        VERIFY_RESULT=0
    fi
}

# Step 4: Show the outcome of the flash operation.
# Displays a success or failure message and offers the user two choices:
#   Restart (return 0) — go back to step 1 to flash another image.
#   Exit    (return 1) — quit the program.
show_flash_result() {
    local title body
    if ((FLASH_RESULT != 0)); then
        title="Step 4 of 4 \u2014 Failed"
        body="Flashing failed.\n\n$FLASH_DETAILS"
    elif ((VERIFY_RESULT != 0)); then
        title="Step 4 of 4 \u2014 Verification failed"
        body="The image was written but verification failed.\nThe data on disk does not match the source image.\n\nImage:\n$SELECTED_IMAGE_LABEL\n\nTarget:\n$SELECTED_DISK_LABEL"
    else
        title="Step 4 of 4 \u2014 Success"
        body="Flashing completed and verified successfully.\n\nImage:\n$SELECTED_IMAGE_LABEL\n\nTarget:\n$SELECTED_DISK_LABEL\n\nThe operating system may now detect new partitions on the target disk."
    fi

    dialog --clear --backtitle "$BACKTITLE" \
        --title "$title" \
        --yes-label "Restart" --no-label "Exit" \
        --yesno "$body" 17 86
}

# Drive the four-step wizard. A step variable tracks the current screen;
# each step function's return code determines whether to advance, go back,
# restart from step 1, or exit.
main() {
    local step=1
    while true; do
        case "$step" in
            1)
                select_disk || exit 0
                step=2
                ;;
            2)
                select_image
                case $? in
                    0) step=3 ;;
                    2) step=1 ;;
                    *) exit 0 ;;
                esac
                ;;
            3)
                flash_image
                case $? in
                    2) step=2 ;;
                    *) step=4 ;;
                esac
                ;;
            4)
                ((FLASH_RESULT == 0)) && verify_flash
                show_flash_result
                case $? in
                    0) step=1 ;;
                    *) exit 0 ;;
                esac
                ;;
        esac
    done
}

main
