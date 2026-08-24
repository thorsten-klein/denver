#!/bin/bash -e
# NOTE: denver invokes this as `bash check_host_tools.sh`, not
# `./check_host_tools.sh` -- the shebang's own '-e' is never consulted then
# (only applies when exec'd directly), hence the explicit 'set -e' below too.
set -e
# The 'cmd:' of the 'host-tools' stage: this env can't install its own apt
# packages on every run (that needs sudo -- see install_host_tools.sh, wired
# in as 'scripts: setup'), so all it can do here is check they are actually
# on PATH and fail loud, with the fix, if not.
#
# arm-none-eabi-gcc is deliberately not checked here: the 'arm-none-eabi'
# stage installs a pinned version of its own rather than relying on apt's.

MISSING=()
for tool in git cmake; do
  command -v "$tool" >/dev/null || MISSING+=("$tool")
done

if [ ${#MISSING[@]} -gt 0 ]; then
  echo "Missing host tool(s): ${MISSING[*]}" >&2
  echo "Run 'denver run ${DENVER_ENV_DIR} --scripts setup' once (needs sudo) to install them." >&2
  exit 1
fi

echo "host tools OK"
