#!/usr/bin/env python3
"""Write the host's proxy env vars into the docker CLI config (~/.docker/config.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> None:
    """Write http_proxy/https_proxy/no_proxy into ~/.docker/config.json, unless already up-to-date."""
    http_proxy = os.environ.get("http_proxy", "")
    if not http_proxy:
        print("No proxy in use.")
        return

    config_file = Path.home() / ".docker" / "config.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)

    data = json.loads(config_file.read_text()) if config_file.exists() else {}
    before = json.dumps(data, sort_keys=True)

    proxies = data.setdefault("proxies", {})
    default = proxies.setdefault("default", {})
    default.setdefault("httpProxy", http_proxy)
    default.setdefault("httpsProxy", os.environ.get("https_proxy", ""))
    default.setdefault("noProxy", os.environ.get("no_proxy", ""))

    if json.dumps(data, sort_keys=True) == before:
        print(f"docker client config already up-to-date: {config_file}")
        return

    print(f"Writing changes to {config_file}")
    config_file.write_text(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    main()
