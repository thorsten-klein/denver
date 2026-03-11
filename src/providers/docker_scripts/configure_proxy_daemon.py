#!/usr/bin/env python3
"""Configure the docker daemon's systemd unit to use the host's proxy env vars."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

CONFIG_FILE = Path("/etc/systemd/system/docker.service.d/docker.conf")


def ensure_sudo(*cmd) -> subprocess.CompletedProcess:
    """Run ``cmd`` under sudo, prompting for auth first if needed."""
    print("----------------------------------------------------")
    print("INFO: sudo is required:", *cmd)
    subprocess.run(["sudo", "-v"], check=True)
    return subprocess.run(["sudo", *cmd], check=True)


def main() -> None:
    """Write the docker daemon's systemd proxy drop-in, unless already correctly configured (needs sudo)."""
    http_proxy = os.environ.get("http_proxy", "")
    if not http_proxy:
        print("No proxy in use.")
        return

    if CONFIG_FILE.is_file() and f'Environment="http_proxy={http_proxy}"' in CONFIG_FILE.read_text():
        print("Proxy for docker daemon is already correctly configured.")
        return

    if CONFIG_FILE.exists():
        print(f"Your docker daemon configuration will be overwritten: {CONFIG_FILE}")

    ensure_sudo("install", "-m", "644", "-D", "/dev/null", str(CONFIG_FILE))

    no_proxy = os.environ.get("no_proxy", "")
    content = (
        "[Service]\n"
        f'Environment="HTTP_PROXY={http_proxy}"\n'
        f'Environment="HTTPS_PROXY={http_proxy}"\n'
        f'Environment="NO_PROXY={no_proxy}"\n'
        f'Environment="http_proxy={http_proxy}"\n'
        f'Environment="https_proxy={http_proxy}"\n'
    )
    subprocess.run(["sudo", "tee", str(CONFIG_FILE)], input=content, text=True, check=True)


if __name__ == "__main__":
    main()
