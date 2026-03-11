#!/bin/bash -e

# install udev rules into the host system, so the JLink debugger is usable
# without root (needed on the host regardless of --skip docker)
SCRIPT_DIR=$(realpath "$(dirname "$BASH_SOURCE")")
UDEV_JLINK_RULES=99-jlink.rules
[ -e "/etc/udev/rules.d/$UDEV_JLINK_RULES" ] || sudo install -m 644 -D "$SCRIPT_DIR/configs/$UDEV_JLINK_RULES" /etc/udev/rules.d/$UDEV_JLINK_RULES
