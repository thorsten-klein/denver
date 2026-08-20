#!/bin/bash -e
# Generate a .env file consumed by docker-compose.yml later on.

SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# conan's cache. Kept next to this env rather than in ~/.conan2, because the
# container is started with --rm: a cache inside it would be thrown away and
# both toolchains re-downloaded on every single run. docker-compose.yml
# mounts this same path into the container.
#
# Created here, not left to docker: a bind-mount target that does not exist
# yet is created by docker as root, which the non-root container user could
# then not write to.
CONAN_HOME=$SELF_DIR/.conan2
mkdir -p "$CONAN_HOME"

HOST_HOME=$HOME

DENVER_EXE="python3 $(cd "$SELF_DIR/../../src" && pwd)/denver.py"
(
    echo HOST_UID=$(id -u)
    echo HOST_GID=$(id -g)
    echo HOST_HOME=$HOST_HOME
    echo HOME=$HOST_HOME
    echo CONAN_HOME=$CONAN_HOME
    echo DENVER_EXE="$DENVER_EXE"
) > $SELF_DIR/.env
