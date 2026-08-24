#!/bin/bash -e
# NOTE: denver invokes this as `bash install_host_tools.sh`, not
# `./install_host_tools.sh` -- the shebang's own '-e' is never consulted then
# (only applies when exec'd directly), hence the explicit 'set -e' below too.
set -e
# One-time host bootstrap. Wired in as 'scripts: setup', so it never runs as
# part of a normal `denver run` -- only when explicitly asked for:
#   denver run examples/raspberry-pico --scripts setup

PACKAGES=(
  git
  cmake
  build-essential    # picotool is a *host* tool, built with the native compiler
  libusb-1.0-0-dev   # picks up USB support for the 'picotool' stage's own build
                      # (load/save/erase/verify/reboot over USB); without it
                      # picotool still builds, just without those commands
)
# deliberately not installed here: gcc-arm-none-eabi -- the 'arm-none-eabi'
# stage (denver.toml) downloads a pinned ARM GNU toolchain release instead,
# so the apt version never needs to exist at all (and wouldn't win on PATH
# anyway).
sudo apt-get install -y "${PACKAGES[@]}"

echo "host tools installed"
