#!/bin/bash -e
# Thin apt wrapper: update, install the packages given as argv, then purge
# apt's own caches so the layer doesn't carry them around.

log_step() { echo "+ apt-get $*" >&2; }

log_step update
apt-get update

log_step "install -y $*"
apt-get install -y "$@"

log_step clean
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
