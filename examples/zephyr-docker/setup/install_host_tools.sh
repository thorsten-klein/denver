#!/bin/bash -e
# One-time host bootstrap for the zephyr-docker env, run via
# `denver run examples/zephyr-docker --scripts setup` -- deliberately not part of the
# normal stage pipeline, because everything here touches the host system
# (apt, systemd) and needs sudo.

TOTAL=6

cat <<'EOF'
======================================================================
 zephyr-docker -- one-time host setup
======================================================================
This installs the host-side tools the docker provider needs to build
and run the dev container. Nothing is installed *into* the container
here, and nothing in your workspace is touched.

What will be installed (each step is skipped if already satisfied):

  1. containerd       container runtime                (apt: containerd)
  2. docker           container engine + CLI           (apt: docker.io)
  3. docker buildx    BuildKit builder used by
                      `docker compose build`           (apt: docker-buildx)
  4. make             drives this example's build
                      targets                          (apt: make)
  5. jq               create-env.sh parses
                      `docker compose config` JSON     (apt: jq)
  6. binfmt-support   QEMU/binfmt registration, so
                      foreign-arch images can run      (apt: binfmt-support)

Every install goes through `sudo apt-get install -y`, so you may be
asked for your password. Nothing is removed, upgraded or reconfigured.
======================================================================

EOF

step=0

# apt_install_if_missing <apt-package> <why it is needed> <check command...>
# Installs the package only when the check command fails, and says either way.
apt_install_if_missing() {
    local pkg=$1 reason=$2
    shift 2
    step=$((step + 1))
    printf '[%d/%d] %s -- %s\n' "$step" "$TOTAL" "$pkg" "$reason"
    if "$@" > /dev/null 2>&1; then
        echo "       already present -- skipping"
        return
    fi
    echo "       missing -- running: sudo apt-get install -y $pkg"
    sudo apt-get install -y "$pkg"
}

# Ensure that minimal tools are installed
apt_install_if_missing containerd     "container runtime"                    command -v containerd
apt_install_if_missing docker.io      "container engine and CLI"             command -v docker
apt_install_if_missing docker-buildx  "BuildKit builder for compose build"   docker buildx --help
apt_install_if_missing make           "build targets of this example"        command -v make
apt_install_if_missing jq             "JSON parsing in create-env.sh"        command -v jq

# Ensure QEMU is installed in the host system
apt_install_if_missing binfmt-support "QEMU binfmt registration" \
    test -e /lib/systemd/system/binfmt-support.service

cat <<'EOF'

Host setup complete. You can now start the env with:

    denver run examples/zephyr-docker

If `docker` was installed just now, you may need to log out and back in
(or run `newgrp docker`) before you can use it without sudo.
EOF
