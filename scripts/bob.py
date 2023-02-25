#!/usr/bin/env python3

import argparse
import os
import sys
import subprocess
import re
from difflib import SequenceMatcher
from common.console import Term, Directory

try:
    from versioning import version_string
except ModuleNotFoundError:
    def version_string():
        return "Unknown version"


def docker_exists():
    try:
        subprocess.check_output(["docker", "ps"], stderr=subprocess.DEVNULL)
        return True
    except FileNotFoundError:
        Term.fail("Docker is not installed")
        return False
    except subprocess.CalledProcessError:
        Term.fail("Docker is not running")
        return False


def image_exists(image):
    if not docker_exists():
        return False

    try:
        s = subprocess.check_output(["docker", "images", "-q", image], stderr=subprocess.DEVNULL)

        if not s:
            Term.warn("Exact docker image name doesn't exist: %s" % (image))

        return bool(s)
    except subprocess.CalledProcessError:
        Term.fail("Docker is not running")
        return False


def list_images():
    """
    List all available images.
    """
    if not docker_exists():
        return None

    try:
        s = subprocess.check_output(["docker", "images"], stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        Term.fail("Docker is not running")
        return None

    images = []
    lines = s.decode().strip().split("\n")

    if len(lines) < 2:
        return images

    try:
        for line in lines[1:]:
            tmp = line.strip().split()
            images.append(tmp[0])
    except IndexError:
        Term.fail("Error parsing docker images output")        

    return images


def list_containers():
    """
    List all active container names.
    """
    if not docker_exists():
        return None

    try:
        s = subprocess.check_output(["docker", "container", "ls"], stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        Term.fail("Docker is not running")
        return None

    containers = []
    lines = s.decode().strip().split("\n")

    if len(lines) < 2:
        return containers

    try:
        for line in lines[1:]:
            tmp = line.strip().split()
            containers.append(tmp[-1])
    except IndexError:
        Term.fail("Error parsing docker container list output")        

    return containers


def guess_image_by_cwd():
    try:
        cwd = os.path.split(os.getcwd())[1]
        return close_match_image_name(cwd)
    except IndexError:
        return None
    
    
def close_match_image_name(name, min_threshold=0.5):
    best_guess = None
    images = list_images()
    
    if images is None:
        return best_guess
        
    highest_ratio = 0

    for image in images:
        # Try to match part of the string
        substr_weight = 1
        
        if name.lower() in image.lower():
            substr_weight = 1.5 # bigger weight because name is substr of the image name
        
        # Try to find the closest name        
        ratio = SequenceMatcher(None, name, image).ratio() * substr_weight
        
        if ratio > highest_ratio and ratio > min_threshold:
            highest_ratio = ratio
            best_guess = image    

    return best_guess


def image_name_check(image):
    if image is not None:
        # Try to check whether exact image name exists
        if not image_exists(image):
            # Exact image name not found, try to match the image names heuristically
            image = close_match_image_name(image)            
    else:
        # Image name is not supplied by user OR exact image name doesn't exist
        # Try to guest image by current working directory name first
        image = guess_image_by_cwd()
                
    # Still can't guess. give up.
    if image is None:
        Term.fail("Couldn't guess image name based on current directory name. Provide -i/--image.")    
        return None
    else:
        Term.ok("Guessed image: " + image) 
        return image
    

def auto_container_name(cmd):
    """
    Auto generate container name based on passed cmd to be executed.
    """
    name = ""
    
    # cmd is a list
    # Find container name based on the application to be executed.
    # If it's empty, then "shell" is the application name.
    if len(cmd) == 0 or not cmd[0].strip():
        name = "shell"
    else:
        name = cmd[0]

    name = re.sub('[^0-9a-zA-Z]+', '_', name)
    names_in_use = list_containers()
    
    # Prevent name conflict by appending index behind it
    if name in names_in_use:
        index = 0
        tmp_name = name
        
        while tmp_name in names_in_use:
            index = index + 1    
            tmp_name = name + "_" + str(index)
            
        name = tmp_name
    
    return name
    
    
def wants_to_run_compiler(cmd):
    # Determine heuristically whether we are running make, gcc, g++, clang ...
    # cmd is a list.
    tools = ["make", "gcc", "g++", "cc", "c++", "clang", "ccache", "doxygen", "nuitka3"]
    
    try:
        if cmd[0] in tools:
            return True
                
        return False
    except IndexError:
        Term.fail("Error parsing docker images output")        


def create_tmp_dirs():
    user_dir = os.path.expanduser("~")
    dirs = {
        "general": user_dir + "/tmp/bob",
        "ccache": user_dir + "/tmp/bob/ccache",
        "dotcache": user_dir + "/tmp/bob/.cache"
    }
    
    for key in dirs:
        if not os.path.exists(dirs[key]):
            os.makedirs(dirs[key])
        
    return dirs


def docker_run(image, cmd, envs=[], is_detached=False, publish=[]):
    # Start parameter checks
    if not docker_exists():
        sys.exit(1)

    image = image_name_check(image)
    
    if image is None:
        sys.exit(2)

    if not Directory.is_cwd_in_user_dir():
        sys.exit(3)

    # Parameters are fine at this point
    tmp_dirs = create_tmp_dirs()    
    
    user_dir = os.path.expanduser("~")
    user_mount = "%s:%s" % (user_dir, user_dir)
    tmp_mount = "%s:%s" % (tmp_dirs["general"], "/tmp")
    dotcache_mount = "%s:%s" % (tmp_dirs["dotcache"], "/.cache")    
    uid_gid = "%s:%s" % (os.getuid(), os.getgid())

    # Basic docker run cmd and args
    prog_args = [
        "docker", "run",
        "--rm",
        "-v", user_mount,
        "-v", tmp_mount,
        "-v", dotcache_mount,
        "-w", os.getcwd(),
        "--name", auto_container_name(cmd),
        "--user", uid_gid
    ]
    
    # Use interactive mode if we are on a tty
    if sys.stdout.isatty():
        prog_args.extend(["-i", "-t"])

    # Append env variables
    try:
        for env in envs[0]:
            prog_args.extend(["-e", env])
    except TypeError:
        pass

    # Append detached mode
    if is_detached is True:
        prog_args.extend(["-d"])
        
    # Append published ports
    try:
        for item in publish[0]:
            prog_args.extend(["-p", item])
    except TypeError:
        pass
        
    # Bind mount ccache directory if we want to compile something
    if wants_to_run_compiler(cmd):
        prog_args.extend(["-e", "CCACHE_DIR=" + tmp_dirs["ccache"]])

    # Append image name
    prog_args.extend([image])

    # Append cmd to be run
    prog_args.extend(cmd)

    # Execute process
    Term.ok("Executing " + " ".join(prog_args))
    print()
    returncode = subprocess.call(prog_args)
    print()
    return returncode


if __name__ == "__main__":
    desc = "Utility for running docker containers, especially for build environment."
    copy = "Copyright 2019 Galuh Utama <galuh.utama@gwutama.de>"
    parser = argparse.ArgumentParser(description=desc, epilog=copy)
    parser.add_argument("-v", "--version", action="store_true", help="Show version.")                      
    parser.add_argument("-i", "--image", action="store",
                        help="Docker image name. Default: guess from current working directory name.",
                        default=None)
    parser.add_argument("-e", "--env", action="append", help="Environment variable to set.", nargs="*")
    parser.add_argument("-d", "--detached", action="store_true", help="Start container in detached mode.")
    parser.add_argument("-p", "--publish", action="append", 
                        help="Publish a container’s port(s) to the host, format [host's port]:[container's port].", 
                        nargs="*")
    parser.add_argument("cmd", action="store", help="Command to be executed.", nargs="*")
    args = parser.parse_args()

    try:
        if args.version:
            print(version_string())
            sys.exit(0)
        else:
            returncode = docker_run(args.image, args.cmd, args.env, args.detached, args.publish)
            sys.exit(returncode)
    except KeyboardInterrupt:
        print("Keyboard interrupt requested")
        sys.exit(0)
