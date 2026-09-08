#!/bin/bash -e
# Shared by postStartCommand and postAttachCommand (see devcontainer.json) --
# kept in one file rather than the same one-liner pasted into both, which
# would only drift apart the moment either needed a tweak.
#
# Brings up conan/uv/zephyr/uv-zephyr -- fast on repeat calls, thanks to each
# stage's own skip-on-success check -- and writes the resulting environment
# to /tmp/denver.env, then makes sure ~/.bashrc sources it.
#
# Why this dance at all: a VS Code terminal is never a child process of
# whatever ran this script, so the environment variables denver just built
# for its own process can't reach it any other way -- env only flows
# parent->child, never to a sibling process started later. Writing them out
# as 'export KEY=VALUE' lines and sourcing that file from every new shell's
# rc is the standard workaround (same trick direnv/nvm use). Run again on
# every start/attach rather than once, since neither /tmp nor a bare append
# to ~/.bashrc is guaranteed to survive a container recreation.

SELF_DIR=$(dirname $(realpath "${BASH_SOURCE[0]}"))
ENV_DIR=$(realpath "$SELF_DIR/..")

# '--skip docker': this already runs inside the container the 'docker' stage
# would relocate into -- its own setup() refuses to run a second time from
# in there.
"$SELF_DIR/denver.sh" run "$ENV_DIR" --skip docker --export-env /tmp/denver.env -- true

SOURCE_LINE='[ -f /tmp/denver.env ] && . /tmp/denver.env'
grep -qxF "$SOURCE_LINE" ~/.bashrc 2>/dev/null || echo "$SOURCE_LINE" >> ~/.bashrc
