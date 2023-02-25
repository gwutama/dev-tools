#!/bin/bash

dscacheutil -flushcache
killall -HUP mDNSResponder
