# Building and running (general)
# ------------------------------
# Build with: docker build --rm -t gwutama/bauhaus .
# Push with: docker push gwutama/bauhaus
# Run with: docker run -i -t gwutama/bauhaus [cmd]
#   More complex run: docker run --rm -v "$PWD":[source dir] -w [build dir] gwutama/bauhaus [cmd]
#
# CMake
# -----
# CMake build is kind of complicated:
#   docker run -i --rm -v "/path/to/project/dir/":/path/to/project/dir/ \
#   -w /path/to/project/dir/build_dir/ -t gwutama/bauhaus \
#   cmake -DCMAKE_BUILD_TYPE=Debug ..
#
# From build directory (cleverly simplified)
#   docker run -i --rm -v "$PWD/../":"$PWD/../" -w "$PWD" -t gwutama/ \
#   cmake -DCMAKE_BUILD_TYPE=Debug ..
#
# Make
# ----
# From build directory (cleverly simplified)
#   docker run -i --rm -v "$PWD/../":"$PWD/../" -w "$PWD" -t gwutama/bauhaus make
#
# Buildbot Worker
# ---
# docker run -i --rm -v "$PWD/../":"$PWD/../" -w "$PWD" -t gwutama/bauhaus buildbot-worker create-worker bauhaus host user pass
# docker run -i --rm -v "$PWD/../":"$PWD/../" -w "$PWD" -t gwutama/bauhaus dumb-init twistd --pidfile= -ny bauhaus/buildbot.tac
#
# Miscellaneous
# -------------
# Cleanup unused and <none> images: docker system prune

FROM ubuntu:18.04

LABEL Description="Build environment for C++/Qt x86_64." \
      Maintainer="Galuh Utama <galuh.utama@gwutama.de>" \
      Version="0.1"

# External files
ADD qtinstall /opt/qtinstall
ADD buildbot-worker-init.sh /usr/local/bin/buildbot-worker-init
ADD versioning.json /opt
ADD scripts/bob /usr/local/bin
ADD scripts/gitinfo /usr/local/bin
ADD scripts/textfinder /usr/local/bin

RUN chmod +x /usr/local/bin/*

# Environment variables
ENV PATH="/opt/Qt/5.12.6/gcc_64/bin:/opt/qtinstall/:${PATH}"
ENV LD_LIBRARY_PATH="/opt/Qt/5.12.6/gcc_64/lib:${LD_LIBRARY_PATH}"
ENV QT_CI_PACKAGES=qt.qt5.5126.gcc_64

# Update and install build tools
RUN apt-get update;\
    apt-get install -y --no-install-recommends \
        dumb-init dbus xz-utils curl wget file iproute2 net-tools inetutils-ping \
        python3 python3-pip python3-setuptools python3-dev \
    	libx11-xcb-dev mesa-common-dev libglu1-mesa-dev libgl1-mesa-dev \
        build-essential automake autoconf make cmake gdb ccache \
        gcovr valgrind doxygen graphviz astyle subversion git \
        makeself

# Install Qt 5.12.6
RUN cd /opt;\
    install-qt 5.12.6;\
    rm qt-*.run
        
# Install buildbot
RUN pip3 install wheel 'buildbot[bundle]'

# Install python libs
RUN pip3 install nuitka gitpython
