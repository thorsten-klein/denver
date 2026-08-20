"""Pytest fixtures and helpers for the denver test suite.

The suite never invokes real tools (uv, conan, west, docker) or replaces the
process: subprocess.run, shutil.which and os.execvpe are stubbed so the pure
orchestration/config logic can be exercised deterministically.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

import denver
import denver_providers as providers
import denver_providers.context as ctxmod
from denver_providers import Context
from denver_providers.context import set_quiet


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    """Point denver's state root at this test's own tmp_path.

    denver.DENVER_DIR is the checkout root when running from a checkout,
    which is what the test suite does -- so anything reaching run_stages()
    wrote its venvs/caches into the real repository, and every test shared
    one directory per env *name*. Harmless while nothing was serialised;
    with a per-env run lock (Context.acquire_lock) it means xdist workers
    block on each other's locks instead of running.

    tmp_path/"denver" specifically, so it matches what make_context() below
    passes as denver_dir and the paths tests already assert on.
    """
    monkeypatch.setattr(denver, "DENVER_DIR", tmp_path / "denver")


@pytest.fixture(autouse=True)
def _no_container_markers(monkeypatch):
    """Stop the *host's* container marker files from reaching in_container().

    in_container() answers a question about the real machine, partly by
    looking for /.dockerenv, /run/.containerenv and /run/systemd/container on
    the real filesystem. That makes every test which builds a Context -- and
    so every assertion that depends on ctx.in_container, from 'no-index: auto'
    resolving to true/false all the way to whether the logo is printed --
    quietly conditional on where the suite happens to run. WSL2 with systemd
    is the case that actually bites: it has /run/systemd/container, so the
    whole suite behaves as if it were inside a container.

    Emptying the marker list leaves the *explicit* signals intact
    (DENVER_IN_CONTAINER, and the 'container' variable podman/nspawn/lxc set),
    which is how tests and wrappers ask for container behaviour on purpose. A
    test needing a marker file supplies its own list on top of this, as
    test_in_container_via_marker_file does.
    """
    monkeypatch.setattr(ctxmod, "_CONTAINER_MARKERS", ())


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


@pytest.fixture(autouse=True)
def _reset_provider_registry():
    """Undo any 'extensions.providers.dirs:' registration a test made, process-wide.

    load_extension_providers() mutates shared state directly: the
    providers.PROVIDERS registry, the set of already-imported provider
    files, sys.modules and sys.path. Without a reset, one test's extension
    provider (all of it keyed by a tmp_path that the next test won't share)
    would leak into every test running after it in the same worker process.
    """
    before_providers = dict(providers.PROVIDERS)
    before_files = set(providers._loaded_extension_files)
    before_modules = set(sys.modules)
    before_path = list(sys.path)
    yield
    providers.PROVIDERS.clear()
    providers.PROVIDERS.update(before_providers)
    providers._loaded_extension_files.clear()
    providers._loaded_extension_files.update(before_files)
    for name in set(sys.modules) - before_modules:
        if name.startswith("denver_extension_provider_"):
            del sys.modules[name]
    sys.path[:] = before_path


# --------------------------------------------------------------------------- #
# Fake subprocess plumbing
# --------------------------------------------------------------------------- #
class FakeProc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _cmd_parts(cmd):
    """A command as a list of argv entries, whether it arrived as a list or a string."""
    return list(cmd) if isinstance(cmd, (list, tuple)) else [cmd]


def _cmd_joined(cmd):
    """A command as one string, for substring matching and assertions."""
    return " ".join(str(p) for p in _cmd_parts(cmd))


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

    def _matching_response(self, joined):
        """The first configured response whose key is a substring of ``joined``."""
        for key, resp in self.responses.items():
            if key in joined:
                return resp
        return None

    def __call__(self, cmd, *args, **kwargs):
        self.calls.append(types.SimpleNamespace(cmd=cmd, args=args, kwargs=kwargs))
        resp = self._matching_response(_cmd_joined(cmd))
        if callable(resp):
            return resp(cmd)
        if resp is not None:
            return resp
        if _cmd_parts(cmd)[:2] == ["bash", "-c"]:
            return self._real_run(cmd, *args, **kwargs)
        return self.default

    def commands(self):
        """List of joined command strings, for assertions."""
        return [_cmd_joined(c.cmd) for c in self.calls]

    def argvs(self):
        """List of argv lists (str-coerced), for precise flag/value assertions."""
        return [[str(p) for p in _cmd_parts(c.cmd)] for c in self.calls]


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

    import denver_providers.context as ctxmod

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

    import denver_providers.context as ctxmod

    monkeypatch.setattr(ctxmod.os, "execvpe", fake_execvpe)
    # exec() resolves the program against the env's PATH before handing it
    # over, and these tests exec synthetic names ('WRAPPED', 'fish', ...)
    # that exist on no machine -- so the lookup answers "found, unchanged"
    # here. A test that wants the not-found path drives shutil.which itself
    # (see test_exec_unresolvable_command_dies).
    monkeypatch.setattr(ctxmod.shutil, "which", lambda name, path=None: name)
    return captured


# --------------------------------------------------------------------------- #
# Env / Context helpers
# --------------------------------------------------------------------------- #
def write_env(env_dir: Path, config: dict | None = None, files: dict | None = None):
    """Create an env directory with a denver.toml and optional extra files."""
    env_dir.mkdir(parents=True, exist_ok=True)
    if config is not None:
        (env_dir / "denver.toml").write_text(denver.dump_toml(config))
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
        verbose=False,
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
            verbose=verbose,
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
