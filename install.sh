#!/bin/bash

# Build first
sh build.sh

# textfinder
cp scripts/textfinder.py /usr/local/bin/textfinder
chmod 777 /usr/local/bin/textfinder

# gitinfo
cp scripts/gitinfo.sh /usr/local/bin/gitinfo
chmod 777 /usr/local/bin/gitinfo

# bauhaus
docker pull di.the-shire.utama/gwutama/bauhaus

# bob
cp dist/scripts/bob /usr/local/bin/bob
chmod 777 /usr/local/bin/bob

# configs
cp configs/astylerc.gu ~/.astyle
