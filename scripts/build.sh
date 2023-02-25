#!/bin/bash

set -e

BUILD_DIR=$PWD/dist
BUILD_CMD_PREFIX=
BUILD_BOB_PREFIX="bob -i gwutama/bauhaus -- "

# Use bob to buildl linux 64bit executable
if [ -n "$BUILD_LINUX_X64" ]; then
    BUILD_CMD_PREFIX=$BUILD_BOB_PREFIX
fi

# Generate python versioning script file (versioning.py)
gitinfo --gen-python

# Create build directory
rm -rf $BUILD_DIR
mkdir -p $BUILD_DIR

# bob
echo "Building bob"
$BUILD_CMD_PREFIX nuitka3 -o bob --remove-output --follow-imports -j 2 bob.py
$BUILD_CMD_PREFIX strip bob
mv bob $BUILD_DIR

# gitinfo
echo "Building gitinfo"
$BUILD_CMD_PREFIX nuitka3 -o gitinfo --remove-output --follow-imports -j 2 gitinfo.py
$BUILD_CMD_PREFIX strip gitinfo
mv gitinfo $BUILD_DIR

# textfinder
echo "Building textfinder"
$BUILD_CMD_PREFIX nuitka3 -o textfinder --remove-output --follow-imports -j 2 textfinder.py
$BUILD_CMD_PREFIX strip textfinder
mv textfinder $BUILD_DIR

rm versioning.py
