#!/bin/bash -e
# NOTE: denver invokes this as `bash build_picotool.sh`, not
# `./build_picotool.sh` -- the shebang's own '-e' is never consulted then
# (only applies when exec'd directly), hence the explicit 'set -e' below too.
set -e
# The 'cmd:' of the 'build-picotool' stage: builds picotool (fetched by the
# 'picotool' git stage, see ../denver.toml) against the Pico SDK checkout
# ('pico-sdk' git stage), via cmake. Run (not sourced) in an isolated
# subprocess, which is right for a build step: it prints its progress, and
# nothing it exports needs to survive. Putting the result on PATH is
# activate_picotool.sh's job, precisely because that *does* have to survive.

SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SELF_DIR/picotool.env"

# Idempotence is ours to implement here: this script runs on every start, so
# it has to recognise the build it already produced and do nothing.
if [ -x "$PICOTOOL_INSTALL_DIR/bin/picotool" ]; then
  echo "picotool already built: $PICOTOOL_INSTALL_DIR/bin/picotool"
  exit 0
fi

cmake -S "$PICOTOOL_SRC" -B "$PICOTOOL_BUILD_DIR" \
  -DPICO_SDK_PATH="$PICO_SDK_PATH" \
  -DCMAKE_INSTALL_PREFIX="$PICOTOOL_INSTALL_DIR"
cmake --build "$PICOTOOL_BUILD_DIR" -j"$(nproc)"
cmake --install "$PICOTOOL_BUILD_DIR"

echo "picotool built: $PICOTOOL_INSTALL_DIR/bin/picotool"
