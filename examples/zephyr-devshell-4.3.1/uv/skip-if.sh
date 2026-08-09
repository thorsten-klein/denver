#!/usr/bin/env bash
# Exit 0 if *this env's venv* already has 'west' and 'conan' installed, so
# the (slow) reinstall can be skipped.
#
# Deliberately not `command -v west`: that searches the whole PATH, which
# includes the host's own ~/.local/bin, /usr/bin and friends. A west or
# conan the developer happens to have installed host-wide -- in whatever
# version -- would then satisfy this check on the very first run, skip the
# install entirely, and leave the venv empty. Every later stage would go on
# silently using the host's version instead of the one this env pins, and
# the failure only surfaces much later as some unrelated tool error. Only
# what is inside the venv counts.
#
# The uv provider activates the venv before running this script (see
# UvProvider.setup), so VIRTUAL_ENV is set here. If it somehow isn't, this
# exits non-zero and the install simply runs -- never skip on a check that
# could not be made.
set -euo pipefail
: "${VIRTUAL_ENV:?not run from an activated venv -- refusing to skip the install}"
test -x "$VIRTUAL_ENV/bin/west"
test -x "$VIRTUAL_ENV/bin/conan"
