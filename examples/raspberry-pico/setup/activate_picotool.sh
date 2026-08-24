#!/bin/bash
# The 'source:' of the 'build-picotool' stage: the one line that makes what
# build_picotool.sh built actually usable.
#
# Sourced (not run), so this PATH entry folds into the environment denver is
# building and reaches every later stage and the final command. The same
# export in 'cmd:' would die with that stage's own subprocess -- which is
# also why it is not simply the last line of build_picotool.sh.

SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SELF_DIR/picotool.env"

export PATH="$PICOTOOL_INSTALL_DIR/bin:$PATH"
