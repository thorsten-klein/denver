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
# What used to be the 'docker' stage's job: the full env drops into a
# container built from a Zephyr-ready Ubuntu image (see
# ../../zephyr-docker/container/init_system_for_zephyr.sh); this one runs
# straight on the host, so the host needs a minimal version of that image's
# own package list instead. cmake/ninja/ccache/clang are deliberately not
# checked here -- 'native-tools' below downloads pinned versions of those
# rather than relying on apt's.

MISSING=()
for tool in git gperf dtc python3 make gcc file curl; do
  command -v "$tool" >/dev/null || MISSING+=("$tool")
done

if [ ${#MISSING[@]} -gt 0 ]; then
  echo "Missing host tool(s): ${MISSING[*]}" >&2
  echo "Run 'denver run ${DENVER_ENV_DIR} --scripts setup' once (needs sudo) to install them." >&2
  exit 1
fi

echo "host tools OK"
