#!/bin/bash -e
# NOTE: denver invokes this as `bash download_jlink.sh`, not
# `./download_jlink.sh` -- hence the explicit 'set -e' (the shebang's own
# only applies when exec'd directly).
set -e
# The 'cmd:' of the 'jlink' custom stage. Every other tool in this env is a
# plain '[[<stage>.packages]]' entry on the 'download' provider (a GET, a
# checksum, an unpack -- see doc/providers/download.md). JLink is the one
# exception: SEGGER's download only serves the real archive after a POST
# that accepts its license agreement (a plain GET returns an HTML page, not
# the file), which the 'download' provider has no way to do. So this one
# tool uses the 'custom' escape hatch instead -- the "Worked example:
# bringing a prebuilt binary in by hand" in doc/providers/custom.md, just
# with a POST instead of a GET.
#
# Must be idempotent by itself, same reason as setup_zephyr_sdk.sh: this
# runs on every start, so it has to recognise its own prior success.
#
# JLINK_DIR is already in the environment here, not computed locally -- see
# denver.toml's own top-level [env]; the 'jlink' stage's own env-prepend:
# reuses it too, to put $JLINK_DIR/bin on PATH.

VERSION="8.82"
ARCHIVE_NAME="JLink_Linux_V882_x86_64.tgz"
MD5SUM="1691b1c79764bf1caade424cc39c2e0c"
ARCHIVE="$JLINK_DIR/$ARCHIVE_NAME"
BIN_DIR="$JLINK_DIR/bin"
STAMP="$BIN_DIR/.denver-jlink-$VERSION"

md5_of() { md5sum "$1" | cut -d' ' -f1; }

if [ -f "$STAMP" ]; then
    echo "jlink $VERSION already installed at $BIN_DIR -- skipping"
    exit 0
fi

mkdir -p "$JLINK_DIR"

if [ ! -f "$ARCHIVE" ] || [ "$(md5_of "$ARCHIVE")" != "$MD5SUM" ]; then
    echo "downloading $ARCHIVE_NAME (accepting SEGGER's license agreement)..."
    # transfer goes to a .part file, renamed into place only once complete --
    # same reason the 'download' provider does this: an interrupted run must
    # never leave a truncated archive that the next run would accept.
    curl --fail --location --silent --show-error \
        -d "accept_license_agreement=accepted&submit=Download+software" -X POST \
        --output "$ARCHIVE.part" "https://www.segger.com/downloads/jlink/$ARCHIVE_NAME"
    mv "$ARCHIVE.part" "$ARCHIVE"
fi

actual_md5="$(md5_of "$ARCHIVE")"
if [ "$actual_md5" != "$MD5SUM" ]; then
    echo "checksum mismatch for $ARCHIVE_NAME: expected $MD5SUM, got $actual_md5" >&2
    rm -f "$ARCHIVE"
    exit 1
fi

rm -rf "$BIN_DIR"
mkdir -p "$BIN_DIR"
tar -xf "$ARCHIVE" -C "$BIN_DIR" --strip-components=1
touch "$STAMP"
echo "jlink $VERSION installed at $BIN_DIR"
