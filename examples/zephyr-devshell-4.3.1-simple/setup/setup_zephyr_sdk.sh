#!/bin/bash -e
# NOTE: denver invokes this as `bash setup_zephyr_sdk.sh`, not
# `./setup_zephyr_sdk.sh` -- hence the explicit 'set -e' (the shebang's own
# only applies when exec'd directly).
set -e
# The 'cmd:' of the 'zephyr-sdk' custom stage. The 'zephyr-sdk-download'
# download stage above fetched and checksummed four archives, each unpacked
# into its own directory (one archive per unpack-dir is all that provider
# ever does -- see doc/providers/download.md): the minimal SDK plus three
# cross toolchains. This assembles them into one working SDK install the
# way the old conan recipe's build() did by hand: symlink each toolchain
# into the minimal SDK's own root, then run the SDK's own setup.sh to
# register the cmake package and the host tools.
#
# Must be idempotent by itself: unlike a 'uv'/'conan' stage, denver cannot
# fingerprint an arbitrary 'cmd:' and skip it on its own -- this runs on
# every start (see "cmd: vs source:" in doc/providers/custom.md), so it has
# to recognise its own prior success.
#
# ZEPHYR_SDK_INSTALL_DIR is already in the environment here, not computed
# locally -- it's set once, in denver.toml's own top-level [env], reused
# below and by every later stage/the final command alike.

SDK_ROOT="$ZEPHYR_SDK_INSTALL_DIR"
STAMP="$SDK_ROOT/.denver-sdk-setup"

if [ -f "$STAMP" ]; then
    echo "zephyr-sdk already set up at $SDK_ROOT -- skipping"
    exit 0
fi

for toolchain in arm-zephyr-eabi riscv64-zephyr-elf x86_64-zephyr-elf; do
    src="${DENVER_ENV_WORKDIR}/download/zephyr-toolchain-$toolchain/$toolchain"
    echo "linking $toolchain into the SDK..."
    ln -sfn "$src" "$SDK_ROOT/$toolchain"
done

# The cmake package install writes an absolute path derived from $HOME into
# zephyr_sdk_export.cmake; point HOME at a scratch dir first so that path is
# never the invoking user's real home, then patch the '~' cmake left behind
# back into a real $ENV{HOME} lookup -- same trick the old conan recipe used,
# so the SDK's cmake package still resolves correctly for whoever's $HOME
# actually runs `west build`.
HOME="$SDK_ROOT/cmake.tmp" "$SDK_ROOT/setup.sh" -c
sed -i 's@~@$ENV{HOME}@g' "$SDK_ROOT/cmake/zephyr_sdk_export.cmake"
rm -rf "$SDK_ROOT/cmake.tmp"

"$SDK_ROOT/setup.sh" -h  # host toolchain

touch "$STAMP"
echo "zephyr-sdk set up at $SDK_ROOT"
