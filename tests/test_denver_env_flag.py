"""Tests for the -e/--env CLI flag: set an environment variable for this run.

Unlike 'args:' (test_denver_args.py), this is a denver-own flag, not
something an env declares -- these tests drive denver.main() end to end and
read the values back out of the environment the final command is exec'd
with, plus os.environ itself (see build_arg_parser's -e/--env).
"""

import os

import pytest

import denver
import denver_providers as providers
from denver_providers.base import Provider


@pytest.fixture
def echo_env(tmp_path, exec_recorder):
    """Factory: write an env whose single stage does nothing, run it, return the exec'd environment."""

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
            (env_dir / "denver.yml").write_text("stages: [fakesetup]\nfakesetup:\n  provider: fakesetup\n")
            denver.main([str(env_dir), *argv, "--", "echo", "hi"])
            return exec_recorder["env"]

        yield _run
    finally:
        del providers.PROVIDERS["fakesetup"]


# ---- the values reaching the final command's environment ------------------- #
def test_name_equals_value_is_exported(echo_env, monkeypatch):
    monkeypatch.delenv("MY_VAR", raising=False)
    env = echo_env(["-e", "MY_VAR=hello"])
    assert env["MY_VAR"] == "hello"


def test_long_flag_works_too(echo_env, monkeypatch):
    monkeypatch.delenv("MY_VAR", raising=False)
    env = echo_env(["--env", "MY_VAR=hello"])
    assert env["MY_VAR"] == "hello"


def test_repeatable(echo_env, monkeypatch):
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAR", raising=False)
    env = echo_env(["-e", "FOO=1", "-e", "BAR=2"])
    assert env["FOO"] == "1"
    assert env["BAR"] == "2"


def test_a_later_entry_for_the_same_name_wins(echo_env, monkeypatch):
    monkeypatch.delenv("FOO", raising=False)
    env = echo_env(["-e", "FOO=1", "-e", "FOO=2"])
    assert env["FOO"] == "2"


def test_bare_name_forwards_the_current_value_from_denvers_own_environment(echo_env, monkeypatch):
    monkeypatch.setenv("MY_VAR", "already-exported")
    env = echo_env(["-e", "MY_VAR"])
    assert env["MY_VAR"] == "already-exported"


def test_bare_name_unset_forwards_an_empty_string(echo_env, monkeypatch):
    monkeypatch.delenv("MY_VAR", raising=False)
    env = echo_env(["-e", "MY_VAR"])
    assert env["MY_VAR"] == ""


def test_overrides_the_config_env_map(tmp_path, exec_recorder, monkeypatch):
    # -e is the more direct, explicit statement of intent -- it wins over
    # denver.yml's own declarative 'env:' map for the same name.
    class Fake(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            pass

    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", Fake)
    monkeypatch.delenv("MY_VAR", raising=False)
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text(
        "stages: [fakesetup]\nfakesetup:\n  provider: fakesetup\nenv:\n  MY_VAR: from-config\n"
    )
    denver.main([str(env_dir), "-e", "MY_VAR=from-cli", "--", "echo", "hi"])
    assert exec_recorder["env"]["MY_VAR"] == "from-cli"


def test_a_bad_entry_dies(echo_env):
    with pytest.raises(SystemExit):
        echo_env(["-e", "=nope"])


# ---- applied to denver's own process, too ----------------------------------- #
def test_applied_to_os_environ(echo_env, monkeypatch):
    monkeypatch.delenv("MY_VAR", raising=False)
    echo_env(["-e", "MY_VAR=hello"])
    assert os.environ["MY_VAR"] == "hello"


# ---- carried across a wrapper reinvocation (docker et al.) ------------------ #
def test_reinvoke_command_re_passes_the_flags(tmp_path):
    cmd = denver.reinvoke_command(
        tmp_path / "denver.yml",
        ["echo", "hi"],
        ["docker"],
        options=denver.RunOptions(env_vars={"MY_VAR": "hello"}),
    )
    assert cmd[cmd.index("--env") + 1] == "MY_VAR=hello"
    # ...still ahead of the forwarded command's own '--' marker
    assert cmd.index("--env") < cmd.index("--")


def test_relocated_run_cmd_re_passes_the_flags(tmp_path):
    cmd = denver._relocated_run_cmd(
        tmp_path / "denver.yml",
        "setup",
        quiet=0,
        until_stage=None,
        skip_stages=(),
        env_vars={"MY_VAR": "hello"},
    )
    assert cmd[-2:] == ["--env", "MY_VAR=hello"]


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
    monkeypatch.delenv("MY_VAR", raising=False)

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text(
        "stages: [fakewrap, fakesetup]\nfakewrap:\n  provider: fakewrap\nfakesetup:\n  provider: fakesetup\n"
    )
    denver.main([str(env_dir), "-e", "MY_VAR=hello", "--", "echo", "hi"])

    relocated = exec_recorder["args"]
    assert relocated[0] == "WRAPPED"
    assert relocated[relocated.index("--env") + 1] == "MY_VAR=hello"


# ---- the pieces on their own ------------------------------------------------ #
def test_parsed_env_vars_empty():
    assert denver._parsed_env_vars([]) == {}


def test_parsed_env_vars_name_equals_value():
    assert denver._parsed_env_vars(["FOO=bar"]) == {"FOO": "bar"}


def test_parsed_env_vars_bare_name(monkeypatch):
    monkeypatch.setenv("FOO", "from-shell")
    assert denver._parsed_env_vars(["FOO"]) == {"FOO": "from-shell"}


def test_parsed_env_vars_later_entry_wins():
    assert denver._parsed_env_vars(["FOO=1", "FOO=2"]) == {"FOO": "2"}


def test_parsed_env_vars_rejects_an_empty_name():
    with pytest.raises(SystemExit):
        denver._parsed_env_vars(["=nope"])


def test_run_options_env_vars_defaults_to_empty():
    assert denver.RunOptions().env_vars == {}
