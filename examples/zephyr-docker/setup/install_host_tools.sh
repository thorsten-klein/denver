#!/bin/bash -e

# Ensure that minimal tools are installed
command -v containerd > /dev/null || sudo apt-get install -y containerd
command -v docker > /dev/null || sudo apt-get install -y docker.io
docker buildx --help > /dev/null || sudo apt-get install -y docker-buildx
command -v make > /dev/null || sudo apt-get install -y make
command -v jq > /dev/null || sudo apt-get install -y jq

# Ensure QEMU is installed in the host system
[ -e /lib/systemd/system/binfmt-support.service ] || sudo apt-get install -y binfmt-support
