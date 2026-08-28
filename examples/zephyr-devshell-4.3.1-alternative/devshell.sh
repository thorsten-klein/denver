#!/usr/bin/env bash
# The nix/direnv equivalent of 'denver run examples/zephyr-devshell-4.3.1-alternative -- <command>'.
#
#   ./devshell.sh west build zephyr-rtos/samples/hello_world
#   ./devshell.sh                       # no command -> opens an interactive shell
#
# Enters the flake's devShell (built once via the nix store, instant on every
# later call) and, the first time, turns this directory into a west
# workspace and clones it ('west update') -- what denver's 'zephyr' stage
# does for ../zephyr-devshell-4.3.1. Both checks are directory checks, not
# command checks, on purpose: see that example's README on why
# 'skip-on-success:' scripts must only ever look at this env's own state.
#
# Not 'west init -l .': with '-l', west creates '.west' next to the given
# directory, one level *up* -- fine for a manifest repo meant to be nested
# inside a bigger workspace (which is west's own convention, and how
# ../zephyr-devshell-4.3.1 itself works against the outer checkout's .git),
# wrong here, where this directory is meant to be self-contained. Writing
# '.west/config' directly is what denver's own zephyr provider does too
# (src/denver_providers/zephyr.py's _ensure_workspace/_configure) --
# 'west init' is a convenience wrapper around exactly this.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# --no-warn-dirty: this directory normally sits inside a dirty working tree
# -- expected, not worth a warning every run. (There is no equivalent flag
# for zephyr-nix's own "invalid Python constraints" evaluation warning --
# see flake.nix's 'nixpkgs.url' comment; it only fires on a fresh
# evaluation, e.g. the first run or right after editing flake.nix, not on
# every repeat call, because nix caches evaluation results.)
exec nix --extra-experimental-features "nix-command flakes" develop \
  --no-warn-dirty \
  --command bash -c '
  set -euo pipefail
  if [ ! -f .west/config ]; then
    mkdir -p .west
    : > .west/config
    west config manifest.path .
    west config manifest.file west.yml
  fi
  [ -d zephyr-rtos ] || west update
  if [ "$#" -eq 0 ]; then
    exec bash
  fi
  exec "$@"
' bash "$@"
