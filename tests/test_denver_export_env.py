"""Tests for the --export-env CLI flag: dump the built env to a file for something else to source.

See tests/test_context.py for write_export_env() itself; these drive
denver.main() end to end (see --env's own tests, test_denver_env_flag.py,
for the analogous pattern) and check the file it leaves behind.
"""

import pytest

import denver
import denver_providers as providers
from denver_providers.base import Provider


@pytest.fixture
def echo_env(tmp_path):
    """Factory: write an env whose single stage does nothing, run it, return the export file's path."""

    class Fake(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            pass

    providers.PROVIDERS["fakesetup"] = Fake
    try:

        def _run(argv=()):
            env_dir = tmp_path / "e"
            env_dir.mkdir(exist_ok=True)
            (env_dir / "denver.toml").write_text(
                'stages = [\n  "fakesetup",\n]\n\n[fakesetup]\nprovider = "fakesetup"\n'
            )
            out = tmp_path / "denver.env"
            denver.main(["run", str(env_dir), "--export-env", str(out), *argv, "--", "echo", "hi"])
            return out

        yield _run
    finally:
        del providers.PROVIDERS["fakesetup"]


def test_writes_a_value_from_dashe(echo_env, exec_recorder, monkeypatch):
    # -e applies straight to this process's real os.environ (see
    # _run_resolved_cli), so monkeypatch.delenv(..., raising=False) alone
    # can't undo it afterward if MY_VAR wasn't already present -- pin a
    # value first so monkeypatch actually has something to restore.
    monkeypatch.setenv("MY_VAR", "pre-existing")
    monkeypatch.delenv("MY_VAR")
    out = echo_env(["-e", "MY_VAR=hello"])
    assert "export MY_VAR=hello\n" in out.read_text()


def test_not_written_when_flag_omitted(tmp_path, exec_recorder):
    class Fake(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            pass

    providers.PROVIDERS["fakesetup"] = Fake
    try:
        env_dir = tmp_path / "e"
        env_dir.mkdir()
        (env_dir / "denver.toml").write_text('stages = [\n  "fakesetup",\n]\n\n[fakesetup]\nprovider = "fakesetup"\n')
        out = tmp_path / "denver.env"
        denver.main(["run", str(env_dir), "--", "echo", "hi"])
        assert not out.exists()
    finally:
        del providers.PROVIDERS["fakesetup"]


def test_dry_run_writes_nothing(echo_env, exec_recorder):
    out = echo_env(["--dry-run"])
    assert not out.exists()


# ---- the pieces on their own ------------------------------------------------ #
def test_run_options_export_env_defaults_to_none():
    assert denver.RunOptions().export_env is None


def test_run_options_export_env_is_settable():
    assert denver.RunOptions(export_env="/tmp/denver.env").export_env == "/tmp/denver.env"


# ---- carried across a wrapper reinvocation (docker et al.) ------------------ #
def test_reinvoke_command_re_passes_the_flag(tmp_path):
    cmd = denver.reinvoke_command(
        tmp_path / "denver.toml",
        ["echo", "hi"],
        ["docker"],
        options=denver.RunOptions(export_env="/tmp/denver.env"),
    )
    assert cmd[cmd.index("--export-env") + 1] == "/tmp/denver.env"
    # ...still ahead of the forwarded command's own '--' marker
    assert cmd.index("--export-env") < cmd.index("--")


def test_reinvoke_command_omits_the_flag_when_unset(tmp_path):
    cmd = denver.reinvoke_command(
        tmp_path / "denver.toml",
        ["echo", "hi"],
        ["docker"],
        options=denver.RunOptions(),
    )
    assert "--export-env" not in cmd


def test_flags_are_carried_into_the_wrapper(tmp_path, monkeypatch, exec_recorder):
    class FakeWrap(Provider):
        name = "fakewrap"
        kind = "wrapper"

        def setup(self, ctx):
            pass

        def wrap(self, ctx, cmd):
            return ["WRAPPED", *cmd]

    class FakeSetup(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            pass

    monkeypatch.setitem(providers.PROVIDERS, "fakewrap", FakeWrap)
    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", FakeSetup)

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text(
        'stages = [\n  "fakewrap",\n  "fakesetup",\n]\n\n[fakewrap]\nprovider = "fakewrap"\n\n'
        '[fakesetup]\nprovider = "fakesetup"\n'
    )
    denver.main(["run", str(env_dir), "--export-env", "/tmp/denver.env", "--", "echo", "hi"])

    relocated = exec_recorder["args"]
    assert relocated[0] == "WRAPPED"
    assert relocated[relocated.index("--export-env") + 1] == "/tmp/denver.env"
