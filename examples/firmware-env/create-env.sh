#!/bin/bash -e
# Generate a .env file consumed by docker-compose.yml later on.

SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# A home directory for the container's non-root user. The real host $HOME
# is never mounted in (only this env's own directory is, plus denver's own
# source -- see docker-compose.yml), so the container needs a writable home
# of its own for things like bash history.
#
# Created here, not left to docker: a bind-mount target that does not exist
# yet is created by docker as root, which the non-root container user could
# then not write to.
CONTAINER_HOME=$SELF_DIR/.denver/container-home
mkdir -p "$CONTAINER_HOME"

(
    echo HOST_UID=$(id -u)
    echo HOST_GID=$(id -g)
    echo CONTAINER_HOME=$CONTAINER_HOME
) > $SELF_DIR/.env
