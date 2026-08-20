"""Tests for the bundled providers.docker_scripts.* host-setup helpers --
run standalone as subprocesses by hooks: pre-<stage-id>:/docker.scripts:,
never imported by denver itself. subprocess.run/sudo are always mocked: no
test here may touch the real docker group, docker daemon config, or
~/.docker/config.json.
"""

from __future__ import annotations

import grp
import json
import subprocess
import types
from pathlib import Path

import pytest

from denver_providers.docker_scripts import configure_proxy_client, configure_proxy_daemon, configure_user


# --------------------------------------------------------------------------- #
# configure_user
# --------------------------------------------------------------------------- #
def test_configure_user_ensure_sudo_runs_both_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.append(cmd) or types.SimpleNamespace(returncode=0))
    configure_user.ensure_sudo("groupadd", "docker")
    assert calls[0] == ["sudo", "-v"]
    assert calls[1] == ["sudo", "groupadd", "docker"]


def test_configure_user_already_member(monkeypatch, capsys):
    fake_group = types.SimpleNamespace(gr_mem=["alice"])
    monkeypatch.setattr(grp, "getgrnam", lambda name: fake_group)
    monkeypatch.setattr(configure_user.getpass, "getuser", lambda: "alice")
    monkeypatch.setattr(configure_user, "ensure_sudo", lambda *a: pytest.fail("must not sudo"))

    configure_user.main()
    assert "already in group 'docker'" in capsys.readouterr().out


def test_configure_user_adds_existing_group_member(monkeypatch):
    fake_group = types.SimpleNamespace(gr_mem=["bob"])
    monkeypatch.setattr(grp, "getgrnam", lambda name: fake_group)
    monkeypatch.setattr(configure_user.getpass, "getuser", lambda: "alice")
    calls = []
    monkeypatch.setattr(configure_user, "ensure_sudo", lambda *cmd: calls.append(cmd))

    configure_user.main()
    assert calls == [("usermod", "-aG", "docker", "alice")]


def test_configure_user_creates_missing_group(monkeypatch):
    monkeypatch.setattr(grp, "getgrnam", lambda name: (_ for _ in ()).throw(KeyError(name)))
    monkeypatch.setattr(configure_user.getpass, "getuser", lambda: "alice")
    calls = []
    monkeypatch.setattr(configure_user, "ensure_sudo", lambda *cmd: calls.append(cmd))

    configure_user.main()
    assert ("groupadd", "docker") in calls
    assert ("usermod", "-aG", "docker", "alice") in calls


# --------------------------------------------------------------------------- #
# configure_proxy_client
# --------------------------------------------------------------------------- #
def test_configure_proxy_client_noop_without_proxy(monkeypatch, capsys):
    monkeypatch.delenv("http_proxy", raising=False)
    configure_proxy_client.main()
    assert "No proxy in use" in capsys.readouterr().out


def test_configure_proxy_client_writes_new_config(monkeypatch, tmp_path):
    monkeypatch.setenv("http_proxy", "http://proxy:8080")
    monkeypatch.setenv("https_proxy", "http://proxy:8080")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    configure_proxy_client.main()

    config_file = tmp_path / ".docker" / "config.json"
    data = json.loads(config_file.read_text())
    assert data["proxies"]["default"]["httpProxy"] == "http://proxy:8080"


def test_configure_proxy_client_already_up_to_date(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("http_proxy", "http://proxy:8080")
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    config_file = tmp_path / ".docker" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(
        json.dumps({"proxies": {"default": {"httpProxy": "http://proxy:8080", "httpsProxy": "", "noProxy": ""}}})
    )
    before = config_file.read_text()

    configure_proxy_client.main()

    assert "already up-to-date" in capsys.readouterr().out
    assert config_file.read_text() == before  # untouched, confirms the fixed missing `return`


# --------------------------------------------------------------------------- #
# configure_proxy_daemon
# --------------------------------------------------------------------------- #
def test_configure_proxy_daemon_ensure_sudo_runs_both_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.append(cmd) or types.SimpleNamespace(returncode=0))
    configure_proxy_daemon.ensure_sudo("groupadd", "docker")
    assert calls[0] == ["sudo", "-v"]
    assert calls[1] == ["sudo", "groupadd", "docker"]


def test_configure_proxy_daemon_noop_without_proxy(monkeypatch, capsys):
    monkeypatch.delenv("http_proxy", raising=False)
    configure_proxy_daemon.main()
    assert "No proxy in use" in capsys.readouterr().out


def test_configure_proxy_daemon_already_configured(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("http_proxy", "http://proxy:8080")
    config_file = tmp_path / "docker.conf"
    config_file.write_text('Environment="http_proxy=http://proxy:8080"\n')
    monkeypatch.setattr(configure_proxy_daemon, "CONFIG_FILE", config_file)
    monkeypatch.setattr(configure_proxy_daemon, "ensure_sudo", lambda *a: pytest.fail("must not sudo"))

    configure_proxy_daemon.main()
    assert "already correctly configured" in capsys.readouterr().out


def test_configure_proxy_daemon_writes_new_config(monkeypatch, tmp_path):
    monkeypatch.setenv("http_proxy", "http://proxy:8080")
    monkeypatch.setenv("no_proxy", "localhost")
    config_file = tmp_path / "docker.conf"
    monkeypatch.setattr(configure_proxy_daemon, "CONFIG_FILE", config_file)

    ensure_sudo_calls = []
    monkeypatch.setattr(configure_proxy_daemon, "ensure_sudo", lambda *cmd: ensure_sudo_calls.append(cmd))
    run_calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: run_calls.append((cmd, k)))

    configure_proxy_daemon.main()

    assert ensure_sudo_calls == [("install", "-m", "644", "-D", "/dev/null", str(config_file))]
    assert run_calls[0][0][0] == "sudo"
    assert "http://proxy:8080" in run_calls[0][1]["input"]


def test_configure_proxy_daemon_warns_before_overwrite(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("http_proxy", "http://proxy:8080")
    config_file = tmp_path / "docker.conf"
    config_file.write_text('Environment="http_proxy=http://old-proxy"\n')
    monkeypatch.setattr(configure_proxy_daemon, "CONFIG_FILE", config_file)
    monkeypatch.setattr(configure_proxy_daemon, "ensure_sudo", lambda *a: None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: None)

    configure_proxy_daemon.main()
    assert "will be overwritten" in capsys.readouterr().out
