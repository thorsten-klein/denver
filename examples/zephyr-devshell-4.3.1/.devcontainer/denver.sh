#!/bin/bash -e
# Thin wrapper around this checkout's own src/denver.py

SELF_DIR=$(dirname $(realpath "${BASH_SOURCE[0]}"))
"$SELF_DIR/../../../src/denver.py" "$@"
