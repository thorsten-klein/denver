#!/bin/bash -e
# NOTE: denver invokes this as `bash install_host_tools.sh`, not
# `./install_host_tools.sh` -- the shebang's own '-e' is never consulted then
# (only applies when exec'd directly), hence the explicit 'set -e' below too.
set -e
# One-time host bootstrap. Wired in as 'scripts: setup', so it never runs as
# part of a normal `denver run` -- only when explicitly asked for:
#   denver run examples/zephyr-devshell-4.3.1-simple --scripts setup
#
# A trimmed version of ../../zephyr-docker/container/init_system_for_zephyr.sh's
# own ZEPHYR_TOOLCHAIN_DEPS: only what a headless `west build`/`twister` run
# needs, none of that script's interactive/GUI conveniences (no Qt, no
# editors, no docker/gh CLI -- there is no container here to build). Grab the
# full list from that script if you want the desktop-devshell experience too.
PACKAGES=(
  git
  gperf
  device-tree-compiler
  python3-dev
  python3-venv
  xz-utils
  file
  make
  gcc
  libmagic1
  curl
  # ccache/cmake/ninja/clang are deliberately not here: 'native-tools' below
  # downloads pinned versions of those instead of relying on apt's.
  # ccache IS installed from apt though (see denver.toml's [host-tools]) --
  # unlike the rest, it isn't a single prebuilt binary release, so pinning it
  # via 'download' would mean compiling it from source, which is exactly the
  # kind of job conan exists for (see ../zephyr-devshell-4.3.1). Getting an
  # unpinned version from apt is the trade-off this "simple" env makes
  # instead -- see README.md.
  ccache
)
sudo apt-get update
sudo apt-get install -y "${PACKAGES[@]}"

echo "host tools installed"
