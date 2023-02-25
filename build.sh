#!/bin/bash

set -e

BUILD_DIR=$PWD/dist

# Create build directory
rm -rf $BUILD_DIR
mkdir -p $BUILD_DIR

# scripts
echo "Building scripts"
cd scripts && ./build.sh && cd ..
mv scripts/dist $BUILD_DIR/scripts

# bauhaus
echo "Building bauhaus"
cp -r $BUILD_DIR/scripts bauhaus/
cd bauhaus && ./build.sh && cd ..
