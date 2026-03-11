#!/bin/bash

set -e

BLOBS_TXT="$1"
OUTPUT_DIR="$2"
shift 2

SCRIPTDIR=$(realpath $(dirname $BASH_SOURCE))
(
    cd $SCRIPTDIR

    mkdir -p "$OUTPUT_DIR"

    # iterate over BLOBS_TXT and download each blob to OUTPUT_DIR (flat directory structure)
    while IFS= read -r line || [ -n "$line" ]; do
        # skip blank lines and comments
        [ -z "$line" ] && continue
        case "$line" in \#*) continue;; esac

        rel_path="${line%%:*}"
        url="${line#*:}"
        if [ -z "$rel_path" ] || [ -z "$url" ]; then
            echo "Error: malformed line: $line" >&2
            exit 1
        fi

        name="$(basename "$rel_path")"

        # skip if any cached copy "<name>.<sha256>" already exists
        if compgen -G "$OUTPUT_DIR/$name.*" > /dev/null; then
            existing="$(compgen -G "$OUTPUT_DIR/$name.*" | head -n1)"
            echo "Already present: $existing"
            continue
        fi

        tmp="$OUTPUT_DIR/$name.tmp"
        echo "Downloading $name ..."
        curl --fail --location --silent --show-error --retry 3 --retry-delay 2 \
            --output "$tmp" "$url"

        sha256="$(sha256sum "$tmp" | awk '{print $1}')"
        dest="$OUTPUT_DIR/$name.$sha256"
        mv "$tmp" "$dest"
        echo ">> Cached as $dest"

    done < "$BLOBS_TXT"
)
