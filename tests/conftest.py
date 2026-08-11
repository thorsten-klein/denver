"""Pytest fixtures and helpers for the denver test suite.

The suite never invokes real tools (uv, conan, west, docker) or replaces the
process: subprocess.run, shutil.which and os.execvpe are stubbed so the pure
orchestration/config logic can be exercised deterministically.
"""

from __future__ import annotations

import subprocess
import types
from pathlib import Path

import pytest
import yaml

from providers import Context
from providers.context import set_quiet


@pytest.fixture(autouse=True)
def _reset_quiet_level():
    """Restore the shared 'denver' logger to its default level after every test.

    Context.__init__ calls set_quiet(), which sets logging.getLogger("denver")'s
    level directly -- a process-wide mutation with no corresponding reset. Under
    pytest-xdist's dynamic load-balancing, a quiet=1/2 test can run right before
    an unrelated one in the same worker process, silencing that later test's own
    warning/info logging (and its caplog assertions) for no reason visible in
    either test. Autouse so every test gets a clean slate regardless of which
    ones construct a quiet Context.
    """
    yield
    set_quiet(0)


# --------------------------------------------------------------------------- #
# Fake subprocess plumbing
# --------------------------------------------------------------------------- #
class FakeProc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


class RunRecorder:
    """Records subprocess.run calls and returns configurable responses.

    ``responses`` maps a substring of the (joined) command to a FakeProc or a
    callable(cmd)->FakeProc. First match wins; otherwise ``default`` is used --
    except a ``bash -c ...`` invocation (Context.source()'s mechanism), which
    passes through to the real subprocess.run unless explicitly overridden, so
    sourcing behaviour stays real even while other tools are mocked.
    """

    def __init__(self, real_run):
        self._real_run = real_run
        self.calls = []
        self.responses = {}
        self.default = FakeProc()

    def __call__(self, cmd, *args, **kwargs):
        self.calls.append(types.SimpleNamespace(cmd=cmd, args=args, kwargs=kwargs))
        parts = cmd if isinstance(cmd, (list, tuple)) else [cmd]
        joined = " ".join(str(c) for c in parts)
        for key, resp in self.responses.items():
            if key in joined:
                return resp(cmd) if callable(resp) else resp
        if len(parts) >= 2 and parts[0] == "bash" and parts[1] == "-c":
            return self._real_run(cmd, *args, **kwargs)
        return self.default

    def commands(self):
        """List of joined command strings, for assertions."""
        out = []
        for c in self.calls:
            parts = c.cmd if isinstance(c.cmd, (list, tuple)) else [c.cmd]
            out.append(" ".join(str(p) for p in parts))
        return out

    def argvs(self):
        """List of argv lists (str-coerced), for precise flag/value assertions."""
        return [[str(p) for p in (c.cmd if isinstance(c.cmd, (list, tuple)) else [c.cmd])] for c in self.calls]


@pytest.fixture
def run_recorder(monkeypatch):
    real_run = subprocess.run
    rec = RunRecorder(real_run)
    monkeypatch.setattr(subprocess, "run", rec)
    return rec


@pytest.fixture
def which(monkeypatch):
    """Control shutil.which results: a dict {name: path|None}. Default: found."""
    table = {}

    def fake_which(name, path=None):
        if name in table:
            return table[name]
        return f"/usr/bin/{name}"

    import providers.context as ctxmod

    monkeypatch.setattr(ctxmod.shutil, "which", fake_which)
    return table


@pytest.fixture
def exec_recorder(monkeypatch):
    """Capture ctx.exec() instead of replacing the process."""
    captured = {}

    def fake_execvpe(file, args, env):
        captured["file"] = file
        captured["args"] = args
        captured["env"] = env

    import providers.context as ctxmod

    monkeypatch.setattr(ctxmod.os, "execvpe", fake_execvpe)
    return captured


# --------------------------------------------------------------------------- #
# Env / Context helpers
# --------------------------------------------------------------------------- #
def write_env(env_dir: Path, config: dict | None = None, files: dict | None = None):
    """Create an env directory with a denver.yml and optional extra files."""
    env_dir.mkdir(parents=True, exist_ok=True)
    if config is not None:
        (env_dir / "denver.yml").write_text(yaml.safe_dump(config, sort_keys=False))
    for rel, content in (files or {}).items():
        p = env_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return env_dir


@pytest.fixture
def make_env(tmp_path):
    """Factory: write an env under a fake denver root (tmp_path/denver)."""
    denver_dir = tmp_path / "denver"
    (denver_dir / "envs").mkdir(parents=True, exist_ok=True)

    def _make(name="myenv", config=None, files=None):
        return write_env(denver_dir / "envs" / name, config, files)

    _make.denver_dir = denver_dir
    return _make


@pytest.fixture
def make_context(tmp_path, monkeypatch):
    """Factory to build a Context with controllable in_container / env."""

    def _make(
        env_dir=None,
        config=None,
        import_dirs=None,
        in_container=False,
        env=None,
        quiet=0,
        fast=False,
        force=False,
        ci=False,
        dry_run=False,
    ):
        denver_dir = tmp_path / "denver"
        denver_dir.mkdir(parents=True, exist_ok=True)
        if env_dir is None:
            env_dir = denver_dir / "envs" / "myenv"
            env_dir.mkdir(parents=True, exist_ok=True)
        for key, val in (env or {}).items():
            monkeypatch.setenv(key, val)
        ctx = Context(
            denver_dir,
            env_dir,
            config or {},
            import_dirs=import_dirs,
            quiet=quiet,
            fast=fast,
            force=force,
            ci=ci,
            dry_run=dry_run,
        )
        ctx.in_container = in_container
        ctx.venv_dir = ctx.venv_dir_for(None)
        return ctx

    # exposed so callers can seed denver_dir (e.g. with a .git/.west marker)
    # before the first _make() call
    _make.denver_dir = tmp_path / "denver"
    return _make
