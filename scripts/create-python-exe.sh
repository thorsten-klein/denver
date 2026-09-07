#!/usr/bin/env bash
#
# Build the single-file 'denver' executable: denver, its providers, the
# bundled scripts/assets, PyYAML and a CPython interpreter, all in one file.
# The result needs nothing preinstalled on the target machine -- not even
# python -- which is what makes it the "just download and run it" answer next
# to `pip install denver-tool`.
#
#   scripts/create-python-exe.sh [--output DIR] [--python PYTHON] [--no-archive]
#
# Output (default dist/): the executable 'denver' plus a
# denver-<version>-<arch>-<os>.tar.xz holding it (e.g.
# denver-1.2.3-arm64-macos.tar.xz), which the release workflow attaches to
# the release. <arch>/<os> come from the build machine, since PyInstaller
# only builds for the platform it runs on.
#
# PORTABILITY: a PyInstaller binary bundles the interpreter but still links
# the *build machine's* glibc, and glibc is only backward compatible -- so the
# binary runs on every distro whose glibc is at least as new as the one it was
# built against, and on none older. Building it on a modern Ubuntu would
# therefore quietly exclude every LTS/enterprise distro older than that
# runner. .github/workflows/release-binary.yml builds Linux inside
# almalinux:8 (glibc 2.28) for that reason; run this script there too (or in
# any comparably old glibc) if you want a binary as portable as the released
# one. The floor that build actually gives is printed at the end of this
# script. macOS has no such floor -- just needs the right arch (arm64 needs
# Apple Silicon).
set -euo pipefail

# Pinned rather than floating: the bootloader PyInstaller prepends is shipped
# prebuilt in its wheel, so its version is part of what the produced binary
# *is* -- an unannounced upgrade is an unannounced change to every released
# executable.
PYINSTALLER_VERSION="6.16.0"

# Read <arch>/<os> off uname, for the archive name below.
case "$(uname -s)" in
    Linux) OS_NAME="linux" ;;
    Darwin) OS_NAME="macos" ;;
    *) echo "ERROR: unsupported OS '$(uname -s)'" >&2; exit 1 ;;
esac
case "$(uname -m)" in
    x86_64|amd64) ARCH_NAME="x64" ;;
    arm64|aarch64) ARCH_NAME="arm64" ;;
    *) echo "ERROR: unsupported arch '$(uname -m)'" >&2; exit 1 ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="$REPO_ROOT/dist"
PYTHON="${PYTHON:-python3}"
ARCHIVE=1

while [ $# -gt 0 ]; do
    case "$1" in
        --output) OUTPUT_DIR="$2"; shift 2 ;;
        --python) PYTHON="$2"; shift 2 ;;
        --no-archive) ARCHIVE=0; shift ;;
        -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "ERROR: unknown argument '$1'" >&2; exit 2 ;;
    esac
done

command -v "$PYTHON" >/dev/null || { echo "ERROR: no '$PYTHON' on PATH (pass --python)" >&2; exit 1; }

# denver's version is derived from the checkout's git tags by setuptools-scm
# at install time and then read back out of the installed metadata at runtime
# (see denver.py's package_version) -- there is no git and no checkout left
# inside the frozen binary to ask later. A tagless checkout (a shallow CI
# clone, a source tarball) silently falls back to pyproject.toml's
# fallback_version, so the binary would ship claiming a version that isn't the
# one it was built from. Warn rather than fail: a local `--version`-doesn't-
# matter build is legitimate, an unnoticed one in a release is not.
if ! git -C "$REPO_ROOT" describe --tags --match '*.*.*' >/dev/null 2>&1; then
    echo "WARNING: no git tags found in $REPO_ROOT -- the executable will report" >&2
    echo "WARNING: pyproject.toml's setuptools-scm fallback_version, not the real one." >&2
fi

# pip builds a local source directory *in place*, so installing the repo below
# drops a build/ and an egg-info into the checkout (exactly what `poe clean`
# removes). Cleared before, so a stale one cannot poison this build, and again
# afterwards, so this script leaves the tree as it found it. Failing to remove
# them is worth stopping for rather than hitting setuptools' unhelpful
# "Operation not permitted" mid-build: it means they belong to another user --
# typically root, from a build run inside a container with the checkout
# mounted, which is precisely how the portable binary is built.
clean_in_tree_build_artifacts() {
    rm -rf "$REPO_ROOT/build" "$REPO_ROOT"/src/*.egg-info
}
if ! clean_in_tree_build_artifacts; then
    echo "ERROR: cannot remove $REPO_ROOT/build (or src/*.egg-info) -- left by a build" >&2
    echo "ERROR: that ran as a different user, e.g. as root inside a container." >&2
    echo "ERROR: Remove them with the same user, e.g.:" >&2
    echo "ERROR:   docker run --rm -v \"$REPO_ROOT\":/src alpine rm -rf /src/build /src/src/denver_tool.egg-info" >&2
    exit 1
fi

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"; clean_in_tree_build_artifacts || true' EXIT

echo ">>> creating build venv with $("$PYTHON" -V)"
"$PYTHON" -m venv "$BUILD_DIR/venv"
VENV_PY="$BUILD_DIR/venv/bin/python"

# A real (non-editable) install on purpose: it is what puts denver's
# dist-info metadata, its assets/ and providers/ package data, and PyYAML in
# one place for PyInstaller to collect. An editable install would leave all
# of it behind a path hook that the frozen binary has no way to follow.
echo ">>> installing denver + pyinstaller==$PYINSTALLER_VERSION"
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet "$REPO_ROOT" "pyinstaller==$PYINSTALLER_VERSION"

# What each collection flag is for -- none of these are found automatically:
#
# --copy-metadata denver-tool: the frozen binary is not a checkout and not an
#   installed distribution, so scm_version() finds no git and
#   importlib.metadata finds no package -- `denver --version` and every
#   'denver-version:' pin in a denver.yml would go unanswerable. This ships
#   the dist-info that answers them (see denver.py's package_version).
# --collect-data denver_assets: logo.txt, read from disk at runtime (print_logo).
# --collect-submodules denver_providers: denver imports providers lazily, from
#   inside functions, and picks the class by name out of the PROVIDERS
#   registry -- a static analyser sees neither.
# --add-data conan_scripts/docker_scripts: these are *not* imported, they are
#   handed to conan/docker as script paths and run standalone by another
#   interpreter (see denver_providers/conan_scripts/__init__.py). So they have to
#   exist as real files, not as modules frozen into the archive -- which is
#   also why --collect-submodules above does not already cover them.
echo ">>> freezing"
"$BUILD_DIR/venv/bin/pyinstaller" \
    --onefile \
    --name denver \
    --clean \
    --noconfirm \
    --distpath "$BUILD_DIR/dist" \
    --workpath "$BUILD_DIR/work" \
    --specpath "$BUILD_DIR/work" \
    --copy-metadata denver-tool \
    --collect-data denver_assets \
    --collect-submodules denver_providers \
    --add-data "$REPO_ROOT/src/denver_providers/conan_scripts:denver_providers/conan_scripts" \
    --add-data "$REPO_ROOT/src/denver_providers/docker_scripts:denver_providers/docker_scripts" \
    "$REPO_ROOT/src/denver.py"

# Smoke test before packaging: --version and --license both prove the
# bundled dist-info metadata is readable (see denver.py's package_version and
# license_text -- --copy-metadata above is what makes either possible in a
# frozen binary), --show-config resolves an env through every provider --
# which is what actually catches a provider or a bundled script that did not
# make it into the archive. '-c denver-version=null' drops the example's own
# pin: a build from an untagged tree reports a dev version no pin is
# satisfied by, which says nothing about whether this binary is complete
# (same reasoning as ci.yml's installed-mode smoke test).
echo ">>> smoke-testing $BUILD_DIR/dist/denver"
VERSION_OUTPUT="$("$BUILD_DIR/dist/denver" --version)"
echo "$VERSION_OUTPUT"
"$BUILD_DIR/dist/denver" --license >/dev/null
"$BUILD_DIR/dist/denver" --help >/dev/null
"$BUILD_DIR/dist/denver" run "$REPO_ROOT/examples/simple-env" --show-config -c denver-version=null >/dev/null

# 'denver X.Y.Z' -> X.Y.Z; see denver.py's package_version for what it prints.
VERSION="${VERSION_OUTPUT#denver }"
PLATFORM="${ARCH_NAME}-${OS_NAME}"
ARCHIVE_NAME="denver-${VERSION}-${PLATFORM}.tar.xz"

mkdir -p "$OUTPUT_DIR"
cp "$BUILD_DIR/dist/denver" "$OUTPUT_DIR/denver"

if [ "$ARCHIVE" = 1 ]; then
    # Tarball holds the versioned+platform binary, plus a plain 'denver'
    # symlink to put on PATH. Version+platform in the name avoids clashes
    # when installing several releases or several platforms side by side.
    cp "$BUILD_DIR/dist/denver" "$BUILD_DIR/dist/denver-$VERSION-$PLATFORM"
    ln -sf "denver-$VERSION-$PLATFORM" "$BUILD_DIR/dist/denver"
    XZ_OPT=-9 tar -C "$BUILD_DIR/dist" -caf "$OUTPUT_DIR/$ARCHIVE_NAME" "denver-$VERSION-$PLATFORM" denver
fi

echo
echo ">>> $VERSION_OUTPUT -> $OUTPUT_DIR/denver ($(du -h "$OUTPUT_DIR/denver" | cut -f1))"
if [ "$ARCHIVE" = 1 ]; then
    echo ">>> $OUTPUT_DIR/$ARCHIVE_NAME ($(du -h "$OUTPUT_DIR/$ARCHIVE_NAME" | cut -f1))"
fi
if [ "$OS_NAME" = "linux" ]; then
    echo ">>> built against $(getconf GNU_LIBC_VERSION 2>/dev/null || echo 'glibc (unknown version)'): runs on any $ARCH_NAME Linux with at least that glibc"
else
    echo ">>> built on $(sw_vers -productVersion 2>/dev/null || echo 'macOS'): runs on $ARCH_NAME macOS"
fi
