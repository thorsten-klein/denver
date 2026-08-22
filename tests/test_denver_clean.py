"""Tests for 'denver run --clean' -- the env's own 'clean' scripts, then its state directory."""

from __future__ import annotations

import pytest

import denver
from denver_providers.context import env_state_key

#: this stage's own setup work -- must never run for a --scripts/--setup/--login/--clean invocation
STAGE_CMD = "echo stage-setup-ran"


def state_dir(env_dir, stem="denver"):
    """Where a run of ``<env>/<stem>.toml`` keeps its state, in the conventional (env-dir) location."""
    return env_dir / ".denver" / stem


def build_state(env_dir, stem="denver", *, gitignore=True):
    """Fake what a real run leaves behind: a state dir with something in it, plus denver's own .gitignore."""
    workdir = state_dir(env_dir, stem)
    (workdir / ".logs").mkdir(parents=True)
    (workdir / "performance.jsonl").write_text("{}\n")
    if gitignore:
        (workdir.parent / ".gitignore").write_text("*\n")
    return workdir


def scripted_config(name="clean"):
    """A one-stage config declaring a 'scripts: <name>:' entry, plus setup work that --clean must not run."""
    return {
        "stages": ["tools"],
        "command": "the-final-command",
        "tools": {"provider": "custom", "cmd": STAGE_CMD, "scripts": {name: ["do.sh"]}},
    }


def _scripted_env(make_env, name="clean", **kwargs):
    """An env whose one stage declares a 'scripts: <name>:' entry, with state already built."""
    env_dir = make_env(config=scripted_config(name), files={"do.sh": "#!/bin/bash\n"}, **kwargs)
    return env_dir, build_state(env_dir)


# ---- what 'run --clean' does ---------------------------------------------------#
def test_run_clean_runs_the_clean_scripts_and_removes_the_state_dir(make_env, run_recorder, which):
    env_dir, workdir = _scripted_env(make_env)

    assert denver.main(["run", str(env_dir), "--clean"]) == 0

    assert str((env_dir / "do.sh").resolve()) in run_recorder.commands()[-1]
    assert not workdir.exists()
    assert not (env_dir / ".denver").exists()


@pytest.mark.parametrize("flag", ["--clean", "--setup", "--login"])
def test_the_scripts_shorthands_never_build_or_enter_the_env(make_env, run_recorder, which, exec_recorder, flag):
    # --clean is exactly its --setup/--login siblings here: the env's own
    # scripts run and that is all -- no stage's setup(), no final command
    env_dir, _ = _scripted_env(make_env, name=flag.lstrip("-"))

    assert denver.main(["run", str(env_dir), flag]) == 0

    assert not [cmd for cmd in run_recorder.commands() if STAGE_CMD in cmd]
    assert exec_recorder == {}


def test_run_scripts_clean_runs_the_scripts_but_keeps_the_state_dir(make_env, run_recorder, which):
    # the long form is the opt-out: only what the env itself declares runs
    env_dir, workdir = _scripted_env(make_env)

    assert denver.main(["run", str(env_dir), "--scripts", "clean"]) == 0

    assert str((env_dir / "do.sh").resolve()) in run_recorder.commands()[-1]
    assert workdir.is_dir()


def test_run_clean_removes_the_state_dir_even_with_no_clean_scripts_declared(make_env, run_recorder, which, caplog):
    env_dir, workdir = _scripted_env(make_env, name="setup")

    assert denver.main(["run", str(env_dir), "--clean"]) == 0

    assert "no 'clean' scripts to run" in caplog.text
    assert not workdir.exists()


def test_run_clean_dry_run_removes_nothing(make_env, run_recorder, which, caplog):
    env_dir, workdir = _scripted_env(make_env)

    assert denver.main(["run", str(env_dir), "--clean", "--dry-run"]) == 0

    assert workdir.is_dir()
    assert (env_dir / ".denver").is_dir()
    # the parent is reported too, even though the state dir it is judged on is still there
    assert f"would remove {workdir}" in caplog.text
    assert f"would remove {env_dir / '.denver'}" in caplog.text


def test_run_clean_listing_the_script_names_removes_nothing(make_env, capsys):
    # a bare '--scripts' anywhere means "list the names" -- a query, which
    # must not delete the state dir on its way out
    env_dir, workdir = _scripted_env(make_env)

    assert denver.main(["run", str(env_dir), "--clean", "--scripts"]) == 0

    assert "available --scripts names" in capsys.readouterr().err
    assert workdir.is_dir()


def test_run_clean_keeps_the_scripts_ordering():
    args = denver.build_arg_parser().parse_args(["run", "e", "--setup", "--clean"])
    assert args.scripts == ["setup", "clean"]
    assert args.clean_workdir is True


def test_run_without_clean_leaves_clean_workdir_off():
    args = denver.build_arg_parser().parse_args(["run", "e", "--scripts", "clean"])
    assert args.clean_workdir is False


def test_clean_is_offered_as_a_run_flag(tmp_path):
    assert "--clean" in denver._complete_candidates(["run", str(tmp_path), "--cl"])


# ---- which directories go -------------------------------------------------------#
def test_clean_removes_the_state_dir_and_the_spent_denver_parent(make_env, run_recorder, which):
    env_dir, workdir = _scripted_env(make_env)

    assert denver.main(["run", str(env_dir), "--clean"]) == 0

    assert not workdir.exists()
    # the env is back to exactly what its author checked in
    assert not (env_dir / ".denver").exists()
    assert (env_dir / "denver.toml").is_file()


def test_clean_keeps_a_denver_dir_still_holding_another_configs_state(make_env, run_recorder, which):
    env_dir, workdir = _scripted_env(make_env)
    other = build_state(env_dir, "denver.debug")

    assert denver.main(["run", str(env_dir), "--clean"]) == 0

    assert not workdir.exists()
    assert other.is_dir()
    assert (env_dir / ".denver" / ".gitignore").is_file()


def test_clean_of_one_config_variant_leaves_the_others_alone(make_env, run_recorder, which):
    env_dir, workdir = _scripted_env(make_env)
    debug = build_state(env_dir, "denver.debug")
    (env_dir / "denver.debug.toml").write_text(
        f'stages = ["tools"]\n\n[tools]\nprovider = "custom"\ncmd = "{STAGE_CMD}"\n'
    )

    assert denver.main(["run", str(env_dir / "denver.debug.toml"), "--clean"]) == 0

    assert not debug.exists()
    assert workdir.is_dir()


def test_clean_removes_a_denver_dir_holding_nothing_but_the_state_dir(make_env, run_recorder, which):
    # an env whose .gitignore was never written (a shared/read-only root once,
    # a hand-deleted file) still ends up with no .denver dir left
    env_dir = make_env(config=scripted_config(), files={"do.sh": "#!/bin/bash\n"})
    build_state(env_dir, gitignore=False)

    assert denver.main(["run", str(env_dir), "--clean"]) == 0

    assert not (env_dir / ".denver").exists()


def test_clean_under_a_shared_state_root_removes_only_that_envs_state(
    make_env, run_recorder, which, tmp_path, monkeypatch
):
    shared = tmp_path / "shared"
    monkeypatch.setenv("DENVER_STATE_DIR", str(shared))
    env_dir = make_env(config=scripted_config(), files={"do.sh": "#!/bin/bash\n"})
    workdir = shared / env_state_key(env_dir, env_dir / "denver.toml")
    workdir.mkdir(parents=True)
    (workdir / "performance.jsonl").write_text("{}\n")

    assert denver.main(["run", str(env_dir), "--clean"]) == 0

    assert not workdir.exists()
    # the shared root is not denver's to remove -- other envs live in it
    assert shared.is_dir()


def test_clean_never_removes_a_shared_root_even_when_it_is_named_denver(
    make_env, run_recorder, which, tmp_path, monkeypatch
):
    # a DENVER_STATE_DIR of '~/.denver' has the very name the in-env state dir
    # has -- but it is shared by every env pointed at it, so only this env's
    # own subdirectory may go
    root = tmp_path / ".denver"
    monkeypatch.setenv("DENVER_STATE_DIR", str(root))
    env_dir = make_env(config=scripted_config(), files={"do.sh": "#!/bin/bash\n"})
    workdir = root / env_state_key(env_dir, env_dir / "denver.toml")
    workdir.mkdir(parents=True)

    assert denver.main(["run", str(env_dir), "--clean"]) == 0

    assert not workdir.exists()
    assert root.is_dir()


# ---- reporting --------------------------------------------------------------#
def test_clean_reports_every_path_it_removed(make_env, run_recorder, which, caplog):
    env_dir, workdir = _scripted_env(make_env)

    denver.main(["run", str(env_dir), "--clean"])

    assert f"removed {workdir}" in caplog.text
    assert f"removed {env_dir / '.denver'}" in caplog.text


def test_clean_with_nothing_to_remove_says_so(make_env, caplog):
    # clean_env directly: a run always creates its own state directory before
    # the 'clean' scripts get to run, so this guard is only ever reached when
    # the directory is gone for some other reason (removed by hand, a
    # --dry-run of a never-built env)
    env_dir = make_env(config=scripted_config(), files={"do.sh": "#!/bin/bash\n"})

    denver.clean_env(env_dir, env_dir / "denver.toml")

    assert "nothing to remove" in caplog.text
