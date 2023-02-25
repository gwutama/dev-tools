#!/bin/bash

/usr/bin/dumb-init twistd --pidfile= -ny $1/buildbot.tac

