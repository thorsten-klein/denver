#!/bin/bash -e
# The 'cmd:' of the 'nvim-by-hand' stage: bring one prebuilt binary release
# into this environment, by hand -- download, verify, unpack.
#
# Run (not sourced) in an isolated subprocess, which is right for a build
# step: it prints its progress, and nothing it exports needs to survive.
# Putting the result on PATH is activate.sh's job, precisely because that
# *does* have to survive.
#
# Everything below is what the conan stage further down gets for free.

SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SELF_DIR/nvim.env"

# Idempotence is ours to implement here: this script runs on every start, so
# it has to recognise the release it already installed and do nothing.
if [ -x "$NVIM_PREFIX/bin/nvim" ]; then
    echo "nvim $NVIM_VERSION already installed: $NVIM_PREFIX"
    exit 0
fi

# Unpacked into a staging dir next to the final one and moved into place only
# once it is complete -- a failed download must not leave a half-unpacked
# tree that the check above would then accept forever.
cd "$DENVER_ENV_DIR"
mkdir -p "$(dirname "$NVIM_PREFIX")"
STAGING=$(mktemp -d "$NVIM_PREFIX.XXXXXX")
trap 'rm -rf "$STAGING"' EXIT

echo "downloading $NVIM_URL"
curl -fLsS -o "$STAGING/nvim.tar.gz" "$NVIM_URL"

# Compare the checksum to ensure the correct file was downloaded
echo "$NVIM_SHA256  $STAGING/nvim.tar.gz" | sha256sum -c -

tar -xzf "$STAGING/nvim.tar.gz" -C "$STAGING" --strip-components=1
rm -f "$STAGING/nvim.tar.gz"
mv "$STAGING" "$NVIM_PREFIX"
trap - EXIT

echo "nvim $NVIM_VERSION installed: $NVIM_PREFIX"
