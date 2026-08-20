"""Tests for denver.toml-declared CLI arguments ('denver-custom-args:').

Each 'denver-custom-args:' entry becomes one argparse.add_argument call, and whatever the
user then passes is exported as DENVER_ARG_<DEST> -- so these tests drive
denver.main() end to end and read the values back out of the environment the
final command is exec'd with (the one mechanism hooks, stages, ${...}
interpolation and the command itself all share).
"""

import sys
from pathlib import Path

import pytest

import denver
import denver_providers as providers
from denver_providers.base import Provider


@pytest.fixture
def echo_env(tmp_path, monkeypatch, exec_recorder):
    """Factory: write an env whose single stage does nothing, run it, return the exec'd environment."""

    class Fake(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            pass

    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", Fake)

    def _run(args, argv=(), stage=None):
        env_dir = tmp_path / "e"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "denver.toml").write_text(
            denver.dump_toml({
                "stages": ["fakesetup"],
                "fakesetup": {"provider": "fakesetup", **(stage or {})},
                "denver-custom-args": args,
            })
        )
        denver.main(["run", str(env_dir), *argv, "--", "echo", "hi"])
        return exec_recorder["env"]

    return _run


# ---- the values reaching the environment ----------------------------------- #
def test_declared_flag_value_is_exported(echo_env):
    env = echo_env([{"flags": ["--target", "-t"], "help": "what to build"}], ["--target", "release"])
    assert env["DENVER_ARG_TARGET"] == "release"


def test_short_flag_works_too(echo_env):
    assert echo_env([{"flags": ["--target", "-t"]}], ["-t", "debug"])["DENVER_ARG_TARGET"] == "debug"


def test_single_flag_string_instead_of_a_list(echo_env):
    assert echo_env([{"flags": "--target"}], ["--target", "release"])["DENVER_ARG_TARGET"] == "release"


def test_default_is_used_when_the_flag_is_not_given(echo_env):
    assert echo_env([{"flags": ["--target"], "default": "debug"}])["DENVER_ARG_TARGET"] == "debug"


def test_flag_without_a_default_exports_nothing(echo_env):
    # deliberately *unset* rather than empty, so a '${DENVER_ARG_X:-fallback}'
    # in the denver.toml still takes its fallback
    assert "DENVER_ARG_TARGET" not in echo_env([{"flags": ["--target"]}])


def test_store_true_is_exported_as_1_or_0(echo_env):
    entry = [{"flags": ["--debug"], "action": "store_true"}]
    assert echo_env(entry, ["--debug"])["DENVER_ARG_DEBUG"] == "1"
    assert echo_env(entry)["DENVER_ARG_DEBUG"] == "0"


def test_multi_value_flags_are_space_joined(echo_env):
    entry = [{"flags": ["--board"], "action": "append", "default": []}]
    assert echo_env(entry, ["--board", "a", "--board", "b"])["DENVER_ARG_BOARD"] == "a b"


def test_non_string_default_is_stringified(echo_env):
    assert echo_env([{"flags": ["--jobs"], "default": 8}])["DENVER_ARG_JOBS"] == "8"


def test_dashes_in_a_flag_name_become_underscores(echo_env):
    env = echo_env([{"flags": ["--build-type"]}], ["--build-type", "release"])
    assert env["DENVER_ARG_BUILD_TYPE"] == "release"


def test_explicit_dest_names_the_variable(echo_env):
    assert echo_env([{"flags": ["-t"], "dest": "target"}], ["-t", "x"])["DENVER_ARG_TARGET"] == "x"


def test_value_is_visible_to_interpolation_in_the_same_denver_yml(tmp_path, run_recorder, exec_recorder):
    # the point of exporting into ctx.env before anything resolves: the env's
    # own config can read back what the user passed
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text(
        'stages = [\n  "c",\n]\n\n[[denver-custom-args]]\nflags = [\n  "--target",\n]\ndefault = "debug"\n\n[c]\nprovider = "custom"\ncmd = "echo building ${DENVER_ARG_TARGET}"\n'
    )
    denver.main(["run", str(env_dir), "--target", "release", "--", "echo", "hi"])
    assert any("echo building release" in cmd for cmd in run_recorder.commands())


def test_choices_are_enforced_by_argparse(echo_env):
    with pytest.raises(SystemExit) as excinfo:
        echo_env([{"flags": ["--target"], "choices": ["debug", "release"]}], ["--target", "nope"])
    assert excinfo.value.code == 2


def test_a_flag_the_env_does_not_declare_is_still_an_error(echo_env):
    with pytest.raises(SystemExit) as excinfo:
        echo_env([{"flags": ["--target"]}], ["--typo"])
    assert excinfo.value.code == 2


# ---- show / --scripts -------------------------------------------------------- #
def test_show_config_sees_the_passed_value(tmp_path, capsys, which):
    # a path resolved centrally, before any stage runs (see
    # resolve_provider_defaults) -- so --show-config and the real run agree
    # on what the flag was set to
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text(
        'stages = [\n  "u",\n]\n\n[[denver-custom-args]]\nflags = [\n  "--who",\n]\ndefault = "world"\n\n[u]\nprovider = "uv"\nskip-on-success = [\n  "${DENVER_ARG_WHO}.sh",\n]\n'
    )
    assert denver.main(["run", str(env_dir), "--show-config", "--who", "denver"]) == 0
    assert "denver.sh" in capsys.readouterr().out


def test_show_config_lists_the_args_section_itself(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text(
        'stages = [\n  "c",\n]\n\n[[denver-custom-args]]\nflags = [\n  "--who",\n]\ndefault = "world"\n\n[c]\nprovider = "custom"\ncmd = true\n'
    )
    denver.main(["run", str(env_dir), "--show-config"])
    assert "--who" in capsys.readouterr().out


def test_run_scripts_see_the_value(tmp_path, run_recorder):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "s.sh").write_text("")
    (env_dir / "denver.toml").write_text(
        'stages = [\n  "c",\n]\n\n[[denver-custom-args]]\nflags = [\n  "--who",\n]\n\n[c]\nprovider = "custom"\ncmd = true\n\n[c.scripts]\nsetup = [\n  "s.sh",\n]\n'
    )
    assert denver.main(["run", str(env_dir), "--who", "denver", "--scripts", "setup"]) == 0
    assert run_recorder.calls[-1].kwargs["env"]["DENVER_ARG_WHO"] == "denver"


# ---- --help ---------------------------------------------------------------- #
def test_help_lists_the_envs_own_flags(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text(
        'stages = [\n  "c",\n]\n\n[[denver-custom-args]]\nflags = [\n  "--target",\n]\nhelp = "what to build"\n\n[c]\nprovider = "custom"\ncmd = true\n'
    )
    # 'denver run <env> --help' returns normally (0), same as a bare
    # top-level '--help': 'run' uses a plain store_true -h/--help
    # (see _add_help_flag), not argparse's own exiting action, precisely so
    # this second, config-aware parse (the one that registers --target) gets
    # a chance to run before help is printed.
    assert denver.main(["run", str(env_dir), "--help"]) == 0
    out = capsys.readouterr().out
    assert "usage:" in out
    assert "--target" in out
    assert "what to build" in out


def test_help_for_a_path_that_is_not_an_env_still_works(tmp_path, capsys):
    assert denver.main(["run", str(tmp_path / "nope"), "--help"]) == 0
    assert "usage:" in capsys.readouterr().out


def test_help_for_a_directory_without_a_denver_yml_still_works(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    assert denver.main(["run", str(tmp_path / "empty"), "--help"]) == 0
    assert "usage:" in capsys.readouterr().out


def test_a_mistyped_flag_without_an_env_is_still_an_error(tmp_path):
    # with no env to declare flags of its own, denver's own are the whole
    # vocabulary -- so an unknown one cannot be somebody else's.
    with pytest.raises(SystemExit) as excinfo:
        denver.main(["run", str(tmp_path / "nope"), "--typo"])
    assert excinfo.value.code == 2


def test_a_non_existent_env_is_still_reported_as_such(tmp_path, caplog):
    # not "unknown flag" and not "no environment given": a path that isn't
    # there is resolve_env_dir's own message, as it is for every other run
    with pytest.raises(SystemExit):
        denver.main(["run", str(tmp_path / "nope"), "--show-config"])
    assert "not found" in caplog.text


def test_version_still_answers_without_an_env(capsys):
    assert denver.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("denver ")


# ---- layering -------------------------------------------------------------- #
def test_args_stack_across_the_import_chain(tmp_path, capsys):
    base = tmp_path / "base"
    base.mkdir()
    (base / "denver.toml").write_text('runnable = false\n\n[[denver-custom-args]]\nflags = [\n  "--from-base",\n]\n')
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text(
        f'import = ["{base}"]\nstages = ["c"]\n\n[[denver-custom-args]]\nflags = ["--from-env"]\n\n'
        f'[c]\nprovider = "custom"\ncmd = true\n'
    )
    assert denver.main(["run", str(env_dir), "--help"]) == 0
    out = capsys.readouterr().out
    assert "--from-base" in out
    assert "--from-env" in out


# ---- validation ------------------------------------------------------------ #
@pytest.mark.parametrize(
    ("args_toml", "expected"),
    [
        ('denver-custom-args = "nope"\n', "must be a list"),
        ('denver-custom-args = ["just-a-string"]\n', "must be a mapping"),
        ('[[denver-custom-args]]\nhelp = "no flags here"\n', "needs 'flags:'"),
        ("[[denver-custom-args]]\nflags = []\n", "needs 'flags:'"),
        ('[[denver-custom-args]]\nflags = ["target"]\n', "must be a string starting with '-'"),
        ("[[denver-custom-args]]\nflags = [7]\n", "must be a string starting with '-'"),
        ('[[denver-custom-args]]\nflags = ["--x"]\ntype = "int"\n', "sets 'type:'"),
        ('[[denver-custom-args]]\nflags = ["--x"]\nhelpp = "typo"\n', "cannot add --x"),
        ('[[denver-custom-args]]\nflags = ["--x"]\naction = "no-such-action"\n', "cannot add --x"),
        ('[[denver-custom-args]]\nflags = ["--force"]\ndest = "mine"\n', "cannot add --force"),
        ('[[denver-custom-args]]\nflags = ["--force"]\n', "denver's own arguments already use"),
        ('[[denver-custom-args]]\nflags = ["--ci-mode"]\ndest = "ci"\n', "denver's own arguments already use"),
        (
            '[[denver-custom-args]]\nflags = ["--a"]\n\n[[denver-custom-args]]\nflags = ["--b"]\ndest = "a"\n',
            "denver's own arguments already use",
        ),
    ],
)
def test_invalid_args_entry_dies(tmp_path, caplog, args_toml, expected):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text(f'stages = ["c"]\n{args_toml}\n[c]\nprovider = "custom"\ncmd = true\n')
    with pytest.raises(SystemExit):
        denver.main(["run", str(env_dir), "--show-config"])
    assert expected in caplog.text


def test_an_env_declaring_no_args_is_unaffected(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text('stages = [\n  "c",\n]\n\n[c]\nprovider = "custom"\ncmd = true\n')
    assert denver.main(["run", str(env_dir), "--show-config"]) == 0
    assert "denver-custom-args" not in capsys.readouterr().out


# ---- surviving a wrapper relocation ---------------------------------------- #
def test_reinvoke_command_re_passes_the_flags(tmp_path):
    # the inner denver re-reads the same denver.toml, so it declares the same
    # flags -- but nobody gave them to it, and each would fall back to its
    # 'default:'
    cmd = denver.reinvoke_command(
        tmp_path / "denver.toml",
        ["echo", "hi"],
        ["docker"],
        options=denver.RunOptions(cli_args=denver.CliArgs(argv=["--target", "release"])),
    )
    assert cmd[cmd.index("--target") + 1] == "release"
    # ...still ahead of the forwarded command's own '--' marker
    assert cmd.index("--target") < cmd.index("--")


def test_relocated_run_cmd_re_passes_the_flags(tmp_path):
    cmd = denver._relocated_run_cmd(
        tmp_path / "denver.toml",
        ["setup"],
        quiet=0,
        until_stage=None,
        skip_stages=(),
        cli_argv=["--target", "release"],
    )
    assert cmd[-2:] == ["--target", "release"]


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
        'stages = [\n  "fakewrap",\n  "fakesetup",\n]\n\n[[denver-custom-args]]\nflags = [\n  "--target",\n]\ndefault = "debug"\n\n[fakewrap]\nprovider = "fakewrap"\n\n[fakesetup]\nprovider = "fakesetup"\n'
    )
    denver.main(["run", str(env_dir), "--target", "release", "--", "echo", "hi"])

    relocated = exec_recorder["args"]
    assert relocated[0] == "WRAPPED"
    assert relocated[relocated.index("--target") + 1] == "release"


# ---- the pieces on their own ------------------------------------------------ #
def test_cli_args_defaults_to_empty():
    empty = denver._cli_args(None)
    assert (empty.env, empty.argv) == ({}, [])


def test_config_arg_dest_falls_back_to_a_short_flag():
    # no long flag to derive a name from, and no explicit 'dest:' either --
    # argparse's own rule, mirrored (see config_arg_dest)
    assert denver.config_arg_dest(["-t"], {"flags": ["-t"]}) == "t"


def test_resolve_full_config_without_cli_args(tmp_path, which):
    # every caller predating 'denver-custom-args:' (a provider driven directly, a test)
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text('stages = []\n')
    config, ctx = denver.resolve_full_config(env_dir, {"stages": []}, env_dir / "denver.toml")
    assert not [key for key in ctx.env if key.startswith(denver.ARG_ENV_PREFIX)]
    assert config["stages"] == []


def test_frozen_reinvocation_carries_the_flags_too(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "denver"))
    cmd = denver.reinvoke_command(
        tmp_path / "denver.toml",
        [],
        ["docker"],
        options=denver.RunOptions(cli_args=denver.CliArgs(argv=["--target", "release"])),
    )
    assert cmd[0] == str(Path(tmp_path / "bin" / "denver").resolve())
    assert cmd[-2:] == ["--target", "release"]
