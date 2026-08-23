"""Tests for removing an env's state: 'denver run --clean' and the 'denver clean <env>' subcommand."""

from __future__ import annotations

from pathlib import Path

import pytest

import denver

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


def test_clean_dies_when_the_env_dir_is_read_only_and_not_overridden(make_env, run_recorder, which):
    # no fallback location any more -- an unwritable env dir with no
    # DENVER_ENV_WORKDIR override is a hard stop, same as a real run
    env_dir = make_env(config=scripted_config(), files={"do.sh": "#!/bin/bash\n"})
    env_dir.chmod(0o500)
    try:
        with pytest.raises(SystemExit):
            denver.main(["run", str(env_dir), "--clean"])
    finally:
        env_dir.chmod(0o700)


def test_clean_removes_an_explicit_env_workdir_override(make_env, run_recorder, which, tmp_path, monkeypatch):
    exact = tmp_path / "exact-workdir"
    monkeypatch.setenv("DENVER_ENV_WORKDIR", str(exact))
    env_dir = make_env(config=scripted_config(), files={"do.sh": "#!/bin/bash\n"})
    exact.mkdir(parents=True)
    (exact / "performance.jsonl").write_text("{}\n")

    assert denver.main(["run", str(env_dir), "--clean"]) == 0

    assert not exact.exists()
    # the conventional in-env-dir location was never used, so there is nothing there to remove
    assert not (env_dir / ".denver").exists()


def test_clean_never_removes_an_env_workdir_overrides_parent_even_if_named_denver(
    make_env, run_recorder, which, tmp_path, monkeypatch
):
    # DENVER_ENV_WORKDIR's exact directory is removed, but its parent is only
    # ever cleaned up when it is the conventional '<env>/.denver' -- even
    # when an override happens to sit inside a directory of that same name
    root = tmp_path / ".denver"
    exact = root / "custom-workdir"
    monkeypatch.setenv("DENVER_ENV_WORKDIR", str(exact))
    env_dir = make_env(config=scripted_config(), files={"do.sh": "#!/bin/bash\n"})
    exact.mkdir(parents=True)

    assert denver.main(["run", str(env_dir), "--clean"]) == 0

    assert not exact.exists()
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


# ---- 'denver clean <env>' ---------------------------------------------------#
def test_clean_subcommand_removes_the_workdir_and_the_spent_denver_parent(make_env):
    env_dir = make_env(config={"stages": []})
    workdir = build_state(env_dir)

    assert denver.main(["clean", str(env_dir), "-y"]) == 0

    assert not workdir.exists()
    assert not (env_dir / ".denver").exists()


def test_clean_subcommand_removes_an_explicit_env_workdir_override_too(make_env, tmp_path, monkeypatch):
    # an env built once with DENVER_ENV_WORKDIR set and once without has state
    # in both places -- 'denver clean' must find it even though it isn't the
    # place a run would pick without that override
    exact = tmp_path / "exact-workdir"
    monkeypatch.setenv("DENVER_ENV_WORKDIR", str(exact))
    env_dir = make_env(config={"stages": []})
    workdir = build_state(env_dir)
    exact.mkdir(parents=True)

    assert denver.main(["clean", str(env_dir), "-y"]) == 0

    assert not workdir.exists()
    assert not exact.exists()


def test_clean_subcommand_cleans_the_envs_it_imports(make_env):
    base = make_env(name="base", config={"stages": []})
    base_workdir = build_state(base)
    leaf = make_env(name="leaf", config={"import": ["../base"], "stages": []})
    leaf_workdir = build_state(leaf)

    assert denver.main(["clean", str(leaf), "-y"]) == 0

    assert not leaf_workdir.exists()
    assert not base_workdir.exists()


def test_clean_subcommand_still_cleans_an_env_whose_config_is_broken(make_env, caplog):
    env_dir = make_env(config={"stages": []})
    (env_dir / "denver.toml").write_text('import = ["../base"\n')
    workdir = build_state(env_dir)

    assert denver.main(["clean", str(env_dir), "-y"]) == 0

    assert not workdir.exists()
    assert "cannot read" in caplog.text


def test_clean_subcommand_dry_run_removes_nothing(make_env, caplog):
    env_dir = make_env(config={"stages": []})
    workdir = build_state(env_dir)

    assert denver.main(["clean", str(env_dir), "--dry-run"]) == 0

    assert workdir.is_dir()
    assert f"would remove {workdir}" in caplog.text


def test_clean_subcommand_with_nothing_to_remove_says_so(make_env, caplog):
    env_dir = make_env(config={"stages": []})

    assert denver.main(["clean", str(env_dir)]) == 0

    assert "nothing to remove" in caplog.text


def test_clean_subcommand_warns_about_an_import_pointing_nowhere(make_env, caplog):
    env_dir = make_env(config={"import": ["../gone"], "stages": []})
    workdir = build_state(env_dir)

    assert denver.main(["clean", str(env_dir), "-y"]) == 0

    assert not workdir.exists()
    assert "points nowhere" in caplog.text


# ---- confirmation -----------------------------------------------------------#
def test_clean_subcommand_asks_to_confirm_each_directory_before_removing(make_env, monkeypatch):
    env_dir = make_env(config={"stages": []})
    workdir = build_state(env_dir)
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: True)
    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", fake_input)

    assert denver.main(["clean", str(env_dir)]) == 0

    assert not workdir.exists()
    assert not (env_dir / ".denver").exists()
    # both the state dir and its now-spent '.denver' parent are confirmed on their own
    assert any(str(workdir) in p for p in prompts)
    assert any(str(env_dir / ".denver") in p for p in prompts)


def test_clean_subcommand_declining_a_directory_keeps_it_but_continues_with_the_rest(make_env, caplog, monkeypatch):
    # declining the state dir keeps its now-not-spent '.denver' parent too,
    # without even asking about it -- removing the parent would take the
    # declined state dir with it
    base = make_env(name="base", config={"stages": []})
    base_workdir = build_state(base)
    leaf = make_env(name="leaf", config={"import": ["../base"], "stages": []})
    leaf_workdir = build_state(leaf)
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: True)
    calls = []

    def fake_input(prompt=""):
        calls.append(prompt)
        return "n" if len(calls) == 1 else "y"  # decline leaf's own state dir, confirm everything after

    monkeypatch.setattr("builtins.input", fake_input)

    assert denver.main(["clean", str(leaf)]) == 0

    assert leaf_workdir.is_dir()
    assert (leaf / ".denver").is_dir()  # kept along with the declined state dir, never even asked about
    assert not base_workdir.exists()  # a later, unrelated chain -- still removed
    assert f"kept {leaf_workdir}" in caplog.text


def test_clean_subcommand_declining_everything_says_nothing_was_removed(make_env, caplog, monkeypatch):
    env_dir = make_env(config={"stages": []})
    workdir = build_state(env_dir)
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    assert denver.main(["clean", str(env_dir)]) == 0

    assert workdir.is_dir()
    assert "clean: nothing removed" in caplog.text


def test_clean_subcommand_without_yes_dies_when_not_interactive(make_env, monkeypatch):
    # no tty to ask on, and no --yes to answer for it -- refuses rather than
    # silently doing nothing or silently removing anything
    env_dir = make_env(config={"stages": []})
    workdir = build_state(env_dir)
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: False)

    with pytest.raises(SystemExit):
        denver.main(["clean", str(env_dir)])

    assert workdir.is_dir()


def test_clean_subcommand_yes_skips_confirmation_even_when_not_interactive(make_env, monkeypatch):
    env_dir = make_env(config={"stages": []})
    workdir = build_state(env_dir)
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: False)

    assert denver.main(["clean", str(env_dir), "-y"]) == 0

    assert not workdir.exists()


def test_clean_subcommand_dry_run_never_asks_for_confirmation(make_env, monkeypatch):
    # --dry-run removes nothing either way, so there is nothing to confirm
    env_dir = make_env(config={"stages": []})
    workdir = build_state(env_dir)
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: False)

    assert denver.main(["clean", str(env_dir), "--dry-run"]) == 0

    assert workdir.is_dir()


# ---- --all --------------------------------------------------------------------#
def test_clean_subcommand_all_also_removes_the_shared_cache_dir(make_env, tmp_path, monkeypatch):
    env_dir = make_env(config={"stages": []})
    workdir = build_state(env_dir)
    cache_dir = tmp_path / "shared-cache"
    (cache_dir / "downloads").mkdir(parents=True)
    monkeypatch.setenv("DENVER_CACHE_DIR", str(cache_dir))

    assert denver.main(["clean", str(env_dir), "-y", "--all"]) == 0

    assert not workdir.exists()
    assert not cache_dir.exists()


def test_clean_subcommand_without_all_leaves_the_shared_cache_dir(make_env, tmp_path, monkeypatch):
    env_dir = make_env(config={"stages": []})
    build_state(env_dir)
    cache_dir = tmp_path / "shared-cache"
    (cache_dir / "downloads").mkdir(parents=True)
    monkeypatch.setenv("DENVER_CACHE_DIR", str(cache_dir))

    assert denver.main(["clean", str(env_dir), "-y"]) == 0

    assert cache_dir.is_dir()


def test_clean_subcommand_all_with_no_cache_dir_present_removes_only_the_state(make_env, tmp_path, monkeypatch):
    env_dir = make_env(config={"stages": []})
    workdir = build_state(env_dir)
    monkeypatch.setenv("DENVER_CACHE_DIR", str(tmp_path / "never-created"))

    assert denver.main(["clean", str(env_dir), "-y", "--all"]) == 0

    assert not workdir.exists()


def test_clean_subcommand_is_offered_by_completion(tmp_path):
    assert "clean" in denver._complete_candidates(["cl"])
    assert "--dry-run" in denver._complete_candidates(["clean", str(tmp_path), "--d"])
    assert "--yes" in denver._complete_candidates(["clean", str(tmp_path), "--y"])
    assert "--all" in denver._complete_candidates(["clean", str(tmp_path), "--a"])


def test_clean_subcommand_completes_env_paths_for_its_positional(make_env):
    # the <env> positional is still open, so paths are what's offered
    env_dir = make_env(config={"stages": []})
    assert f"{env_dir}/" in denver._complete_candidates(["clean", f"{env_dir.parent}/"])


def test_clean_subcommand_flags_come_with_their_own_help_text(capsys):
    assert denver.main(["__complete", "--describe", "clean", str(Path.cwd()), "--dry"]) == 0
    assert capsys.readouterr().out.startswith("--dry-run\t")
