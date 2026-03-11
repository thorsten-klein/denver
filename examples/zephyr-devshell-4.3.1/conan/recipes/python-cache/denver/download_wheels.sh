#!/bin/bash -e
# Resolve the requirement set given as argv with uv, then fetch a wheel for
# every resolved package into OUTPUT_DIR so the conan recipe can cache them.

OUTPUT_DIR=$1
shift
SELF_DIR=$(cd "$(dirname "$BASH_SOURCE")" && pwd)

cd "$SELF_DIR"
mkdir -p "$OUTPUT_DIR"

rm -rf .venv
uv venv -p 3.12.3 .venv
# shellcheck disable=SC1091
source .venv/bin/activate

# force the resolver out to the real index instead of any local wheel cache
export PIP_NO_INDEX=0
python3 -m ensurepip --upgrade

echo "resolving requirements via uv (overrides applied by caller)..."
set -x
uv pip install "$@"
uv pip freeze > freeze.txt
# 'pip wheel' fetches prebuilt wheels directly, unlike 'pip download' which
# also resolves -- freeze.txt is already the resolved set, so skip that.
python3 -m pip wheel -r freeze.txt --no-deps -w "$OUTPUT_DIR"
