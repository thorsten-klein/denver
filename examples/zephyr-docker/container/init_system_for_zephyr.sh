#!/bin/bash -e
# Provisions the zephyr-docker image: base Zephyr toolchain deps, editor/
# shell conveniences, Qt (for board GUIs), Node, and -- image-build only --
# the docker CLI and gh CLI. Split into arrays below instead of one long
# apt-get so each group's purpose is named.

# interactive shell -> real user, needs sudo; image build -> already root
SUDO=""
[ -t 0 ] && SUDO=sudo

apt_install() (
    $SUDO apt install -y "$@"
)

$SUDO dpkg --add-architecture i386
$SUDO apt update

# what a plain `west build` / `twister` run needs
ZEPHYR_TOOLCHAIN_DEPS=(
    git cmake ninja-build gperf ccache dfu-util device-tree-compiler wget
    python3-dev python3-venv python3-tk xz-utils file make gcc
    gcc-multilib g++-multilib libsdl2-dev libsdl2-dev:i386 libmagic1
)
apt_install "${ZEPHYR_TOOLCHAIN_DEPS[@]}"

# interactive/debugging conveniences -- not required for a headless build
DEVSHELL_TOOLS=(
    apt-utils git-core strace mc pcmanfm gedit vim nano gdb-multiarch
    libgmp-dev libmpfr-dev texinfo zsh fish tree net-tools iputils-ping
    curl unzip bash-completion picocom qemu-user-static jq shellcheck tig
    catimg clangd-20 terminator usbutils telnet lcov libcurl4-openssl-dev
    tclsh gettext
)
apt_install "${DEVSHELL_TOOLS[@]}"

# python toolchain used outside of the west-managed venv
PYTHON_TOOLS=(python3-dev python3-venv python3-pip python3-setuptools python3-wheel python3-yaml pipx)
apt_install "${PYTHON_TOOLS[@]}"

# Qt: boards with a GUI sample (e.g. LVGL demos) link against this
QT_DEPS=(qt6-base-dev libqt6svg6-dev qt6-multimedia-dev libgl-dev)
apt_install "${QT_DEPS[@]}"

curl -fsSL https://deb.nodesource.com/setup_25.x | $SUDO bash -

$SUDO ln -sf /usr/bin/clangd-20 /usr/bin/clangd
$SUDO apt clean

if [ ! -t 0 ]; then
    # Non-interactive means this is the image build itself (not a live
    # devshell) -- only here do we need the docker/gh CLIs baked in.
    add_apt_repo_from_gpg_key() {
        local name=$1 key_url=$2 repo_line=$3
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL "$key_url" | gpg --dearmor -o "/etc/apt/keyrings/$name.gpg"
        chmod a+r "/etc/apt/keyrings/$name.gpg"
        echo "$repo_line" > "/etc/apt/sources.list.d/$name.list"
    }

    apt_install --no-install-recommends ca-certificates curl gnupg socat

    # shellcheck disable=SC1091
    source /etc/os-release
    add_apt_repo_from_gpg_key docker \
        "https://download.docker.com/linux/$ID/gpg" \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$ID $VERSION_CODENAME stable"
    apt update
    apt_install docker-ce-cli docker-buildx-plugin docker-compose-plugin

    add_apt_repo_from_gpg_key github-cli \
        "https://cli.github.com/packages/githubcli-archive-keyring.gpg" \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/github-cli.gpg] https://cli.github.com/packages stable main"
    apt update
    apt_install gh

    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
fi
