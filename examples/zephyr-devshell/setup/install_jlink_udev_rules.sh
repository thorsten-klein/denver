#!/bin/bash -e
# One-time host setup for the zephyr-devshell envs, run via
# `denver examples/zephyr-devshell-4.3.1 --run setup`.
#
# Installs udev rules into the host system, so the JLink debugger is usable
# without root. udev runs on the host, so this is needed there regardless of
# whether the devshell itself runs in docker (`--skip docker` or not).

SCRIPT_DIR=$(realpath "$(dirname "${BASH_SOURCE[0]}")")
UDEV_JLINK_RULES=99-jlink.rules
# configs/ is a sibling of setup/, not a subdirectory of it. '-m' so a missing
# file still resolves to a clean path for the error message below.
RULES_SRC=$(realpath -m "$SCRIPT_DIR/../configs/$UDEV_JLINK_RULES")
RULES_DST="/etc/udev/rules.d/$UDEV_JLINK_RULES"

cat <<EOF
======================================================================
 zephyr-devshell -- one-time host setup
======================================================================
This installs the udev rule that makes SEGGER J-Link probes usable by
normal users, so \`west flash\` / \`west debug\` / the gdbserver do not
need sudo. Only the host is affected -- no workspace file, no python
env and no container image is touched.

What will be installed:

  1. $UDEV_JLINK_RULES
       from: $RULES_SRC
       to:   $RULES_DST   (mode 644)

     Installed with sudo, so you may be asked for your password.
     An already-installed rule file is left untouched.

======================================================================

EOF

if [ ! -e "$RULES_SRC" ]; then
    echo "error: udev rule file not found: $RULES_SRC" >&2
    exit 1
fi

echo "[1/1] $UDEV_JLINK_RULES -- USB access to J-Link probes without root"
if [ -e "$RULES_DST" ]; then
    echo "      already installed at $RULES_DST -- skipping"
else
    echo "      missing -- running: sudo install -m 644 -D $RULES_SRC $RULES_DST"
    sudo install -m 644 -D "$RULES_SRC" "$RULES_DST"
    echo "      installed. Re-plug the probe, or apply the rule right away with:"
    echo "          sudo udevadm control --reload-rules && sudo udevadm trigger"
fi

echo
echo "Host setup complete."
