#!/bin/bash -e
# Renders the docker-compose .env file consumed by docker-compose.yml.
# Called by denver's docker provider as an 'env-scripts' entry before
# 'docker compose build/run' -- see src/denver_providers/docker.py's module
# docstring for how DENVER_DOCKER_IMAGE and env-scripts fit together.

ENV_FILE_TARGET=${1:-}

SELF_DIR=$(cd "$(dirname "$BASH_SOURCE")" && pwd)
DENVER_DIR=$(cd "$SELF_DIR/../.." && pwd)
[ -z "$ENV_FILE_TARGET" ] && ENV_FILE_TARGET="$SELF_DIR/.env"

DENVER_GLOBAL_CONFIG_DIR=${DENVER_GLOBAL_CONFIG_DIR:-$HOME/.denver}
DENVER_GLOBAL_ENV_DIR=${DENVER_GLOBAL_ENV_DIR:-$DENVER_GLOBAL_CONFIG_DIR/zephyr-docker}
HOST_HOME=${HOST_HOME:-$HOME}
CCACHE_DIR=${CCACHE_DIR:-$DENVER_GLOBAL_ENV_DIR/.ccache}
CCACHE_READONLY_DIRS=$CCACHE_DIR.fallback

find_workspace_root() {
    # Walk up from DENVER_DIR and report the outermost ancestor that has a
    # .git -- that's the west/git workspace root the container should see.
    local dir=$DENVER_DIR found=""
    while [ "$dir" != "/" ]; do
        [ -d "$dir/.git" ] && found=$dir
        dir=$(dirname "$dir")
    done
    echo "$found"
}
WEST_TOPDIR=$(find_workspace_root)

resolve_container_image() {
    # docker-compose.yml is the single source of truth for the tag; fall
    # back to a fixed name if compose config can't be parsed for any reason.
    local tag
    tag=$(docker compose -f "$SELF_DIR/docker-compose.yml" config --format=json 2>/dev/null | jq -r '.services.dev.image' || true)
    [ -z "$tag" ] || [ "$tag" = "null" ] && tag="zephyr-docker:latest"
    echo "$tag"
}
CONTAINER_IMAGE=$(resolve_container_image)

# Persistent state that must survive container recreation, grouped by area.
PERSISTENT_DIRS=(
    "$DENVER_GLOBAL_ENV_DIR/.vscode-server"
    "$DENVER_GLOBAL_ENV_DIR/.git-data"
    "$DENVER_GLOBAL_ENV_DIR/.cache"
    "$DENVER_GLOBAL_ENV_DIR/.config"
    "$DENVER_GLOBAL_ENV_DIR/.config/git"
    # fish's runtime subdirs are pre-created so it doesn't race to make them
    # itself on first launch (that race prints spurious permission errors).
    "$DENVER_GLOBAL_ENV_DIR/.config/fish"
    "$DENVER_GLOBAL_ENV_DIR/.config/fish/completions"
    "$DENVER_GLOBAL_ENV_DIR/.config/fish/conf.d"
    "$DENVER_GLOBAL_ENV_DIR/.config/fish/functions"
    "$DENVER_GLOBAL_ENV_DIR/.java"
    "$DENVER_GLOBAL_ENV_DIR/.local/share"
    "$DENVER_GLOBAL_ENV_DIR/ctcache"
    "$CCACHE_READONLY_DIRS"
    "$DENVER_GLOBAL_ENV_DIR/.copilot"
    "$DENVER_GLOBAL_ENV_DIR/.claude"
    "$DENVER_GLOBAL_ENV_DIR/.gemini"
    /var/tmp/west
    /var/tmp/zephyr
)
for d in "${PERSISTENT_DIRS[@]}"; do
    mkdir -p "$d"
done

PERSISTENT_FILES=(
    "$DENVER_GLOBAL_ENV_DIR/.bash_history"
    "$DENVER_GLOBAL_ENV_DIR/.git-data/.git-credentials"
    "$HOST_HOME/.gitconfig"
)
for f in "${PERSISTENT_FILES[@]}"; do
    [ -f "$f" ] || touch "$f"
done

# A non-interactive stdin is how we tell an ad-hoc CI runner apart from an
# interactive devshell (CI runners of this kind don't allocate a tty).
[ -t 0 ] || CI_BUILD=true

sync_git_credentials() {
    local host_file="$HOST_HOME/.git-credentials"
    local container_file="$DENVER_GLOBAL_ENV_DIR/.git-data/.git-credentials"
    [ -f "$host_file" ] || touch "$host_file"
    if [ ! -s "$container_file" ]; then
        cp "$host_file" "$container_file"
    fi
    echo "Info: git credentials are stored here: $container_file"
}
sync_git_credentials

render_env_file() {
    # forward any conan auth vars set on the host into the container
    while IFS='=' read -r name value; do
        case "$name" in
            CONAN_LOGIN_USERNAME*|CONAN_PASSWORD*) echo "$name=$value" ;;
        esac
    done < <(env)

    if [ "${CI_BUILD:-}" = "true" ]; then
        echo "CI=true"
    fi

    echo "CONTAINER_IMAGE=$CONTAINER_IMAGE"
    echo "HOST_UID=$(id -u)"
    echo "HOST_GID=$(id -g)"
    echo "WORKSPACE_DIR=$WEST_TOPDIR"
    echo "DISPLAY=$DISPLAY"
    echo "CCACHE_DIR=$CCACHE_DIR"
    echo "CCACHE_READONLY_DIRS=$CCACHE_READONLY_DIRS"
    echo "HOST_HOME=$HOST_HOME"
    echo "DOCKER_HOME=/home/ubuntu"
    echo "DENVER_DIR=$DENVER_DIR"
    echo "DENVER_GLOBAL_ENV_DIR=$DENVER_GLOBAL_ENV_DIR"
    echo "JETBRAINS_LICENSE_SERVER=$JETBRAINS_LICENSE_SERVER"
    echo "CONAN_HOME=$DENVER_GLOBAL_ENV_DIR/.conan"
    echo "PYTHONPATH=$DENVER_DIR/envs/zephyr-devshell/conan/base_classes"
    echo "PIP_CONFIG_FILE=$HOST_HOME/.config/pip/pip.conf"
    echo "PIP_CACHE_DIR=$DENVER_GLOBAL_ENV_DIR/.pip.cache"
    echo "UV_CACHE_DIR=$DENVER_GLOBAL_ENV_DIR/.uv.cache"
    echo "USER_CACHE_DIR=/var/tmp/zephyr"

    if [ -n "${DENVER_HOOK_DOCKER_ENV:-}" ]; then
        # shellcheck disable=SC1090
        source "$DENVER_HOOK_DOCKER_ENV"
    fi
}
render_env_file > "$ENV_FILE_TARGET"
