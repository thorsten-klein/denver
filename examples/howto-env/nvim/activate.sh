#!/bin/bash
# The 'source:' of the 'nvim-by-hand' stage: the one line that makes the
# release install.sh unpacked actually usable.
#
# Sourced (not run), so this PATH entry folds into the environment denver is
# building and reaches every later stage and the final command. The same
# export in 'cmd:' would die with that stage's own subprocess -- which is
# also why it is not simply the last line of install.sh.

SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SELF_DIR/nvim.env"

export PATH="$NVIM_PREFIX/bin:$PATH"
