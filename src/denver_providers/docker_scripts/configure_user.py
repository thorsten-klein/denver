#!/usr/bin/env python3
"""Ensure the 'docker' group exists and the current user is a member."""

from __future__ import annotations

import getpass
import grp
import subprocess


def ensure_sudo(*cmd) -> subprocess.CompletedProcess:
    """Run ``cmd`` under sudo, prompting for auth first if needed."""
    print("----------------------------------------------------")
    print("INFO: sudo is required:", *cmd)
    subprocess.run(["sudo", "-v"], check=True)
    return subprocess.run(["sudo", *cmd], check=True)


def main() -> None:
    """Ensure the 'docker' group exists and the current user is a member of it (needs sudo)."""
    try:
        docker_group = grp.getgrnam("docker")
    except KeyError:
        print("Group 'docker' does not exist. Create it.")
        ensure_sudo("groupadd", "docker")
        docker_group = None

    user = getpass.getuser()
    if docker_group is not None and user in docker_group.gr_mem:
        print(f"User '{user}' is already in group 'docker'.")
        return

    print(f"Add user '{user}' to group 'docker'")
    ensure_sudo("usermod", "-aG", "docker", user)


if __name__ == "__main__":
    main()
