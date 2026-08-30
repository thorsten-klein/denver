#!/bin/bash
# Sourced by denver as hooks.env: runs once, before any stage, so these
# exports apply to the whole devshell (see src/denver.py's own module
# docstring for 'hooks:'). WEST_TOPDIR/DENVER_ENV_WORKDIR are denver
# built-ins, already present in the environment by the time this runs.
#
# Same exports as ../zephyr-devshell/hooks/env.sh (the base env this used to
# 'import:'), minus nothing -- 'ccache' is still on PATH here too, just from
# apt (see setup/install_host_tools.sh) instead of a conan recipe built from
# source. See README.md for what else changed.
SCRIPT_DIR=$(realpath "$(dirname $BASH_SOURCE)")

export CMAKE_COLOR_DIAGNOSTICS=ON
# let plain cmake (e.g. from an IDE) find_package(Zephyr)
export CMAKE_PREFIX_PATH="$CMAKE_PREFIX_PATH:$WEST_TOPDIR/zephyr-rtos"

# ccache
export CCACHE_BASEDIR="$WEST_TOPDIR"
export CCACHE_NOHASHDIR=1

# ctcache (clang-tidy-cache)
export CTCACHE_LOCAL=1
export CTCACHE_DIR="$DENVER_ENV_WORKDIR/ctcache"
export CTCACHE_KEEP_COMMENTS=1
export CTCACHE_STRIP="$WEST_TOPDIR"
export CTCACHE_STRIP_SRC=ON
export CTCACHE_EXCLUDE_USER_CONFIG=ON

# codechecker
export CODECHECKER_TRIM_PATH_PREFIX="$WEST_TOPDIR"

# west (WEST_CONFIG_SYSTEM is west's own env var, not a denver.toml key)
export WEST_CONFIG_SYSTEM="$SCRIPT_DIR/../configs/west_base_config"
