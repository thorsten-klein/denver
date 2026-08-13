#!/bin/bash -e
# One-time host bootstrap: everything the docker stage itself needs before it
# can run at all. Wired in as 'scripts: setup:', so it never runs as part of a normal start,
# but ONLY when the user explicitly calls `denver examples/howto-env --run setup`

command -v docker >/dev/null || sudo apt-get install -y docker.io
command -v jq >/dev/null || sudo apt-get install -y jq

if ! docker compose version >/dev/null 2>&1; then
    sudo apt-get install -y docker-compose-v2
fi

echo "host tools OK"
