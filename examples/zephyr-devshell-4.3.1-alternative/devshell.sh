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

  # Modules only declare their own pip requirements (in zephyr/module.yml,
  # package-managers.pip.requirement-files) *after* they are cloned, so this
  # cannot be baked into flake.nix at eval time -- this is what denver does
  # in the uv-zephyr stage. pythonEnv is a read-only nix store path, nowhere
  # to pip-install into, so this installs into a plain --target directory
  # instead, then PYTHONPATH puts that directory on every later commands
  # sys.path -- one pip-managed layer on top of the nix-managed base, rather
  # than uv-zephyrs one shared venv for everything.
  #
  # -m only accepts names west packages itself recognizes as zephyr modules
  # (a project with a zephyr/module.yml) -- passing every west project name
  # blindly dies on the first one that is not a module (e.g. net-tools).
  # zephyr itself is skipped too: its own requirements are already in
  # pythonEnv (flake.nix), and re-running pip for it here would just
  # re-fetch the same packages into a second location for nothing.
  PIP_EXTRA_DIR="$PWD/.pip-extra"
  if [ ! -d "$PIP_EXTRA_DIR" ]; then
    pip_module_args=()
    while read -r name path; do
      [ -f "$path/zephyr/module.yml" ] && pip_module_args+=(-m "$name")
    done < <(west list -f "{name} {path}")
    west packages "${pip_module_args[@]}" pip --install --ignore-venv-check -- --target="$PIP_EXTRA_DIR"
  fi
  export PYTHONPATH="$PIP_EXTRA_DIR${PYTHONPATH:+:$PYTHONPATH}"

  if [ "$#" -eq 0 ]; then
    # flake.nix'\''s shellHook already exports a prefixed PS1, but plain
    # "exec bash" would lose it: an interactive non-login bash sources
    # ~/.bashrc, which on most distros overwrites PS1 outright. --rcfile
    # replays that same bashrc first, then reapplies the prefix after it,
    # same trick nix develop itself uses for its own prompt.
    exec bash --rcfile <(cat "$HOME/.bashrc" 2>/dev/null; printf "PS1=\"(zephyr-devshell-4.3.1-nix) \$PS1\"\n")
  fi
  exec "$@"
' bash "$@"
