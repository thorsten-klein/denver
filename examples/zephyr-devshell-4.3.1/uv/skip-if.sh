#!/usr/bin/env bash
# Exit 0 if the venv already has 'west' and 'conan' installed (skip reinstall).
set -euo pipefail
command -v west >/dev/null
command -v conan >/dev/null
