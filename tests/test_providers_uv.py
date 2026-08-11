"""Tests for providers.uv.UvProvider."""

from pathlib import Path

import pytest

from providers.uv import UvProvider


@pytest.fixture(autouse=True)
def _venv_creates_dir(run_recorder):
    """'uv venv -p <version> <dir>' has the real side effect of creating <dir>;
    subsequent steps (checksums, skip-if) depend on it existing.
    Also default 'python3 --version' (used for the in-docker version check) to
    match the uv.python default so tests only need to override it explicitly
    when they want a mismatch."""

    def create_venv_dir(cmd):
        Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
        return run_recorder.default

    run_recorder.responses["venv -p"] = create_venv_dir
    run_recorder.responses["python3 --version"] = lambda cmd: type(
        "R", (), {"stdout": "Python 3.12.3\n", "returncode": 0}
    )()


def run_uv(config, ctx, stage="uv"):
    """Resolve ``config[stage]``'s defaults exactly like denver.py's real
    pipeline would (see UvProvider.resolve_defaults), then run the uv
    stage's setup() against it and return ctx.

    Rebinds ``ctx.config`` to ``config`` first: Context.section() (used by
    Provider.config_section()) always reads from ``ctx.config`` as bound at
    Context construction, so a second run_uv() call against the same ctx
    but a *different* config dict (simulating a later denver invocation
    after denver.yml changed, against the same on-disk venv/logs) would
    otherwise silently keep reading the first call's config.
    """
    ctx.config = config
    config[stage] = UvProvider.resolve_defaults(ctx, config.get(stage) or {}, config)
    n = UvProvider(config)
    n.stage = stage
    ctx.stage_id = stage  # denver.py sets this before setup(); mirrored here for ctx.run(step=...)
    n.setup(ctx)
    return ctx


# ---- setup: --fast ----------------------------------------------------------#
def test_fast_activates_existing_venv_without_building(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["requirements.txt"]}}
    ctx = make_context(config=config, fast=True)
    (ctx.env_dir / "requirements.txt").write_text("packaging\n")
    ctx.venv_dir.mkdir(parents=True)
    run_uv(config, ctx)

    assert ctx.env["VIRTUAL_ENV"] == str(ctx.venv_dir)
    assert str(ctx.venv_dir / "bin") in ctx.env["PATH"]
    assert run_recorder.calls == []


def test_fast_still_shows_progress_banner(make_context, run_recorder, which, capsys):
    # --fast activates instead of installing, but the '[i/n]' progress line
    # must still show under -q, not silently vanish.
    config = {"uv": {"requirements": ["requirements.txt"]}}
    ctx = make_context(config=config, fast=True, quiet=1)
    (ctx.env_dir / "requirements.txt").write_text("packaging\n")
    ctx.venv_dir.mkdir(parents=True)
    run_uv(config, ctx)

    err = capsys.readouterr().err
    assert "uv" in err
    assert "install" in err
    assert "activate" in err  # the real work --fast does, not skipped


def test_fast_dies_when_venv_missing(make_context, which):
    config = {"uv": {"requirements": ["requirements.txt"]}}
    ctx = make_context(config=config, fast=True)
    with pytest.raises(SystemExit):
        run_uv(config, ctx)


# ---- setup: full happy path -------------------------------------------------#
def test_setup_installs_requirements(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["requirements.txt"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "requirements.txt").write_text("packaging\n")
    run_uv(config, ctx)

    assert ctx.env["VIRTUAL_ENV"] == str(ctx.venv_dir)
    assert str(ctx.venv_dir / "bin") in ctx.env["PATH"]
    checksum_file = ctx.venv_dir / "uv-checksums.txt"
    assert checksum_file.is_file()
    commands = run_recorder.commands()
    assert any("uv venv" in c for c in commands)
    assert any("uv pip install" in c for c in commands)


def test_setup_no_requirements_only_creates_venv(make_context, run_recorder, which):
    config = {"uv": {}}
    ctx = make_context(config=config)
    run_uv(config, ctx)
    assert not (ctx.venv_dir / "uv-checksums.txt").exists()
    assert not any("uv pip install" in c for c in run_recorder.commands())


def test_setup_uv_missing_dies(make_context, which):
    which["uv"] = None
    config = {"uv": {}}
    ctx = make_context(config=config)
    with pytest.raises(SystemExit):
        run_uv(config, ctx)


def test_setup_uv_configured_explicitly(make_context, run_recorder, which):
    which["uv"] = None  # not on PATH, but explicitly configured below
    config = {"uv": {"uv": "/opt/uv"}}
    ctx = make_context(config=config)
    run_uv(config, ctx)
    assert any("/opt/uv venv" in c for c in run_recorder.commands())


def test_setup_custom_venv_name_gives_distinct_dir(make_context, run_recorder, which):
    config = {"uv-2": {"venv": "second"}}
    ctx = make_context(config=config)
    run_uv(config, ctx, stage="uv-2")
    assert ctx.env["VIRTUAL_ENV"] == str(ctx.venv_dir_for("second"))


# ---- _ensure_python ----------------------------------------------------------#
def test_ensure_python_in_docker_matching_version(make_context, run_recorder, which):
    run_recorder.responses["python3 --version"] = lambda cmd: type(
        "R", (), {"stdout": "Python 3.12.3\n", "returncode": 0}
    )()
    config = {"uv": {"python": "3.12.3"}}
    ctx = make_context(config=config, in_docker=True)
    run_uv(config, ctx)
    assert any("uv python find 3.12.3" in c for c in run_recorder.commands())


def test_ensure_python_in_docker_mismatch_dies(make_context, run_recorder, which):
    run_recorder.responses["python3 --version"] = lambda cmd: type(
        "R", (), {"stdout": "Python 3.9.0\n", "returncode": 0}
    )()
    config = {"uv": {"python": "3.12.3"}}
    ctx = make_context(config=config, in_docker=True)
    with pytest.raises(SystemExit):
        run_uv(config, ctx)


def test_ensure_python_host_installs(make_context, run_recorder, which):
    config = {"uv": {"python": "3.12.3"}}
    ctx = make_context(config=config, in_docker=False)
    run_uv(config, ctx)
    assert any("uv python install 3.12.3" in c for c in run_recorder.commands())


# ---- _ensure_venv checksum/recreate logic ------------------------------------#
def test_ensure_venv_first_run_recreates(make_context, run_recorder, which):
    config = {"uv": {}}
    ctx = make_context(config=config)
    run_uv(config, ctx)
    assert any("uv venv" in c for c in run_recorder.commands())


def test_ensure_venv_checksum_unchanged_skips_venv_creation(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")

    # first run creates the venv dir + checksums.txt
    ctx.venv_dir.mkdir(parents=True)
    from providers.context import sha256_of_files

    (ctx.venv_dir / "uv-checksums.txt").write_text(sha256_of_files([ctx.env_dir / "r.txt"], base=ctx.env_dir))
    run_recorder.calls.clear()

    run_uv(config, ctx)
    assert not any("uv venv " in c for c in run_recorder.commands())


def test_ensure_venv_checksum_changed_recreates(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")

    ctx.venv_dir.mkdir(parents=True)
    (ctx.venv_dir / "uv-checksums.txt").write_text("stale-checksum")
    (ctx.venv_dir / "marker").write_text("x")

    run_uv(config, ctx)
    commands = run_recorder.commands()
    assert any("uv venv" in c for c in commands)
    assert not (ctx.venv_dir / "marker").exists()  # old venv was removed


def test_ensure_venv_force_recreates_existing(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    ctx.venv_dir.mkdir(parents=True)
    from providers.context import sha256_of_files

    (ctx.venv_dir / "uv-checksums.txt").write_text(sha256_of_files([ctx.env_dir / "r.txt"], base=ctx.env_dir))
    ctx.force = True

    run_uv(config, ctx)
    assert any("uv venv" in c for c in run_recorder.commands())


# ---- shared venv (unset/identical 'venv:' across stages) ---------------------#
def test_shared_venv_only_first_stage_decides_recreate(make_context, run_recorder, which):
    config = {
        "uv": {"requirements": ["r.txt"]},
        "uv-2": {"requirements": ["r2.txt"]},
    }
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    (ctx.env_dir / "r2.txt").write_text("packaging\n")

    run_uv(config, ctx, stage="uv")
    run_recorder.calls.clear()
    run_uv(config, ctx, stage="uv-2")

    # the second stage sharing the same (default) venv this run must never
    # decide to recreate it, even though it has no checksum file of its own
    # yet -- that would wipe what the first stage just installed
    assert not any("uv venv" in c for c in run_recorder.commands())
    # it still installs its own requirements into that shared venv
    install_cmd = next(c for c in run_recorder.commands() if "uv pip install" in c)
    assert str((ctx.env_dir / "r2.txt").resolve()) in install_cmd


def test_distinct_venv_names_each_decide_their_own_recreate(make_context, run_recorder, which):
    config = {
        "uv": {"requirements": ["r.txt"]},
        "uv-2": {"requirements": ["r2.txt"], "venv": "second"},
    }
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    (ctx.env_dir / "r2.txt").write_text("packaging\n")

    run_uv(config, ctx, stage="uv")
    run_recorder.calls.clear()
    run_uv(config, ctx, stage="uv-2")

    # a stage with its own distinct venv still creates it normally
    assert any("uv venv" in c for c in run_recorder.commands())


# ---- _install branches -------------------------------------------------------#
def test_install_skips_when_all_skip_if_scripts_pass(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "skip-if": ["check.sh"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    (ctx.env_dir / "check.sh").write_text("#!/bin/sh\nexit 0\n")
    # checksums must already match, otherwise _ensure_venv recreates the venv
    # before _install ever runs the skip-if scripts
    from providers.context import sha256_of_files

    ctx.venv_dir.mkdir(parents=True)
    (ctx.venv_dir / "uv-checksums.txt").write_text(sha256_of_files([ctx.env_dir / "r.txt"], base=ctx.env_dir))
    run_recorder.responses["check.sh"] = lambda cmd: type("R", (), {"returncode": 0})()

    run_uv(config, ctx)
    assert not any("uv pip install" in c for c in run_recorder.commands())


def test_install_runs_when_a_skip_if_script_fails(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "skip-if": ["check.sh"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    (ctx.env_dir / "check.sh").write_text("#!/bin/sh\nexit 1\n")
    run_recorder.responses["check.sh"] = lambda cmd: type("R", (), {"returncode": 1})()

    run_uv(config, ctx)
    assert any("uv pip install" in c for c in run_recorder.commands())


def test_install_dies_when_skip_if_script_missing(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "skip-if": ["nope.sh"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    with pytest.raises(SystemExit):
        run_uv(config, ctx)


def test_install_skips_via_configured_skip_if_script(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "skip-if": ["uv/skip-if.sh"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    (ctx.env_dir / "uv").mkdir(parents=True)
    (ctx.env_dir / "uv" / "skip-if.sh").write_text("#!/bin/sh\nexit 0\n")
    run_recorder.responses["skip-if.sh"] = lambda cmd: type("R", (), {"returncode": 0})()

    run_uv(config, ctx)
    assert not any("uv pip install" in c for c in run_recorder.commands())


def test_install_ignores_unconfigured_skip_if_script(make_context, run_recorder, which):
    # uv/skip-if.sh is there and would skip the install, but nothing names
    # it, so it is never looked at: no path is guessed from the layout.
    config = {"uv": {"requirements": ["r.txt"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    (ctx.env_dir / "uv").mkdir(parents=True)
    (ctx.env_dir / "uv" / "skip-if.sh").write_text("#!/bin/sh\nexit 0\n")
    run_recorder.responses["skip-if.sh"] = lambda cmd: type("R", (), {"returncode": 0})()

    run_uv(config, ctx)
    assert any("uv pip install" in c for c in run_recorder.commands())


def test_install_force_ignores_skip_if(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "skip-if": ["check.sh"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    (ctx.env_dir / "check.sh").write_text("#!/bin/sh\nexit 0\n")
    run_recorder.responses["check.sh"] = lambda cmd: type("R", (), {"returncode": 0})()
    ctx.force = True

    run_uv(config, ctx)
    assert any("uv pip install" in c for c in run_recorder.commands())


@pytest.mark.parametrize(
    "no_index, in_docker, expect_present",
    [
        (None, True, False),  # default is off, in docker as much as on the host
        (None, False, False),
        ("auto", True, True),  # explicit 'auto': in-docker turns --no-index on
        ("auto", False, False),  # explicit 'auto': on-host leaves it off
        (True, False, True),  # explicit True wins even on host
        (False, True, False),  # explicit False wins even in-docker
    ],
    ids=[
        "default-in-docker",
        "default-on-host",
        "auto-in-docker",
        "auto-on-host",
        "explicit-true",
        "explicit-false-in-docker",
    ],
)
def test_install_no_index(make_context, run_recorder, which, no_index, in_docker, expect_present):
    uv_cfg = {"requirements": ["r.txt"]}
    if no_index is not None:
        uv_cfg["no-index"] = no_index
    config = {"uv": uv_cfg}
    ctx = make_context(config=config, in_docker=in_docker)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    run_uv(config, ctx)
    install_cmd = next(c for c in run_recorder.commands() if "uv pip install" in c)
    assert ("--no-index" in install_cmd) == expect_present


def test_install_with_overrides_and_find_links(make_context, run_recorder, which):
    config = {
        "uv": {
            "requirements": ["r.txt"],
            "overrides": ["overrides.txt"],
            "find-links": ["."],
        }
    }
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    (ctx.env_dir / "overrides.txt").write_text("x\n")
    run_uv(config, ctx)
    argv = next(a for a in run_recorder.argvs() if "uv pip install" in " ".join(a))
    assert argv[argv.index("--override") + 1] == str((ctx.env_dir / "overrides.txt").resolve())
    assert argv[argv.index("--find-links") + 1] == str((ctx.env_dir / ".").resolve())


# ---- install-args: (including '$(...)' command entries) ----------------------#
def test_install_args_literal_entry(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "install-args": ["--pre"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")

    run_uv(config, ctx)
    argv = next(a for a in run_recorder.argvs() if "uv pip install" in " ".join(a))
    assert "--pre" in argv


def test_install_args_command_entry_used_as_literal_install_args(make_context, run_recorder, which):
    config = {"uv": {"install-args": ["$(echo foo==1.0)"]}}
    ctx = make_context(config=config)
    run_recorder.responses["echo foo==1.0"] = lambda cmd: type("R", (), {"stdout": "foo==1.0\n", "returncode": 0})()

    run_uv(config, ctx)
    argv = next(a for a in run_recorder.argvs() if "uv pip install" in " ".join(a))
    assert "foo==1.0" in argv
    assert "-r" not in argv


def test_install_args_command_entry_and_requirements_file_combine(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "install-args": ["$(echo foo==1.0)"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    run_recorder.responses["echo foo==1.0"] = lambda cmd: type("R", (), {"stdout": "foo==1.0\n", "returncode": 0})()

    run_uv(config, ctx)
    argv = next(a for a in run_recorder.argvs() if "uv pip install" in " ".join(a))
    assert argv[argv.index("-r") + 1] == str((ctx.env_dir / "r.txt").resolve())
    assert "foo==1.0" in argv


def test_install_args_command_entry_checksum_unchanged_skips_venv_creation(make_context, run_recorder, which):
    from providers.uv import UvProvider

    config = {"uv": {"install-args": ["$(echo foo==1.0)"]}}
    ctx = make_context(config=config)
    run_recorder.responses["echo foo==1.0"] = lambda cmd: type("R", (), {"stdout": "foo==1.0\n", "returncode": 0})()

    checksum = UvProvider(config)._requirements_checksum(ctx, [], ["foo==1.0\n"])
    ctx.venv_dir.mkdir(parents=True)
    (ctx.venv_dir / "uv-checksums.txt").write_text(checksum)
    run_recorder.calls.clear()

    run_uv(config, ctx)
    assert not any("uv venv " in c for c in run_recorder.commands())


def test_install_args_command_entry_output_change_recreates_venv(make_context, run_recorder, which):
    config = {"uv": {"install-args": ["$(echo foo==2.0)"]}}
    ctx = make_context(config=config)
    run_recorder.responses["echo foo==2.0"] = lambda cmd: type("R", (), {"stdout": "foo==2.0\n", "returncode": 0})()

    ctx.venv_dir.mkdir(parents=True)
    (ctx.venv_dir / "uv-checksums.txt").write_text("stale-checksum")
    (ctx.venv_dir / "marker").write_text("x")

    run_uv(config, ctx)
    assert any("uv venv" in c for c in run_recorder.commands())
    assert not (ctx.venv_dir / "marker").exists()  # old venv was removed


# ---- append-mode ---------------------------------------------------------------#
def test_append_mode_default_uses_only_current_run_args(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    run_uv(config, ctx)

    config2 = {"uv": {"requirements": ["r2.txt"]}}
    (ctx.env_dir / "r2.txt").write_text("packaging\n")
    run_recorder.calls.clear()
    run_uv(config2, ctx)

    install_cmd = next(c for c in run_recorder.commands() if "uv pip install" in c)
    assert str((ctx.env_dir / "r.txt").resolve()) not in install_cmd
    assert str((ctx.env_dir / "r2.txt").resolve()) in install_cmd


def test_append_mode_true_keeps_prior_run_args(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "append-mode": True}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    run_uv(config, ctx)

    # a later run with a *different* set of requirements (as if a dynamic
    # '$(...)' source dropped this one) must still install the old one too
    config2 = {"uv": {"requirements": ["r2.txt"], "append-mode": True}}
    (ctx.env_dir / "r2.txt").write_text("packaging\n")
    run_recorder.calls.clear()
    run_uv(config2, ctx)

    install_cmd = next(c for c in run_recorder.commands() if "uv pip install" in c)
    assert str((ctx.env_dir / "r.txt").resolve()) in install_cmd
    assert str((ctx.env_dir / "r2.txt").resolve()) in install_cmd


def test_append_mode_persists_across_venv_recreation(make_context, run_recorder, which):
    # the accumulated arg history lives outside the venv dir, so it survives
    # a checksum-triggered recreate instead of being wiped along with it
    config = {"uv": {"requirements": ["r.txt"], "append-mode": True}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    run_uv(config, ctx)

    config2 = {"uv": {"requirements": ["r2.txt"], "append-mode": True}}
    (ctx.env_dir / "r2.txt").write_text("changed\n")  # forces a checksum change -> recreate
    run_recorder.calls.clear()
    run_uv(config2, ctx)

    install_cmd = next(c for c in run_recorder.commands() if "uv pip install" in c)
    assert str((ctx.env_dir / "r.txt").resolve()) in install_cmd
    assert str((ctx.env_dir / "r2.txt").resolve()) in install_cmd


def test_append_mode_deduplicates_unchanged_requirement(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "append-mode": True}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    run_uv(config, ctx)

    run_recorder.calls.clear()
    run_uv(config, ctx)

    install_cmd = next(c for c in run_recorder.commands() if "uv pip install" in c)
    assert install_cmd.count(str((ctx.env_dir / "r.txt").resolve())) == 1


# ---- lock: create / sync ---------------------------------------------------------#
def make_project(ctx, rel="py", *, lockfile=True):
    """A minimal uv project dir (pyproject.toml, optionally its uv.lock) under the env dir."""
    project = ctx.env_dir / rel
    project.mkdir(parents=True, exist_ok=True)
    (project / "pyproject.toml").write_text("[project]\nname = 'x'\nversion = '0'\n")
    if lockfile:
        (project / "uv.lock").write_text("version = 1\n")
    return project


def start_next_run(ctx, run_recorder):
    """Make ``ctx`` look like a *later* denver invocation against the same on-disk state.

    _ensure_venv only lets the first stage per run decide whether to recreate
    a given venv (see the shared-venv tests); a second run_uv() call against
    the same ctx would otherwise be treated as a second *stage* of one run and
    never reach the checksum comparison at all.
    """
    ctx._uv_venvs_ensured_this_run.clear()
    run_recorder.calls.clear()


def test_lock_create_runs_uv_lock_for_the_project(make_context, run_recorder, which):
    config = {"uv": {"lock": {"create": "py/uv.lock"}}}
    ctx = make_context(config=config)
    project = make_project(ctx, lockfile=False)
    run_uv(config, ctx)

    argv = next(a for a in run_recorder.argvs() if "uv lock" in " ".join(a))
    assert argv[argv.index("--project") + 1] == str(project)


def test_lock_sync_installs_lockfile_into_the_activated_venv(make_context, run_recorder, which):
    config = {"uv": {"lock": {"sync": "py/uv.lock"}}}
    ctx = make_context(config=config)
    project = make_project(ctx)
    run_uv(config, ctx)

    argv = next(a for a in run_recorder.argvs() if "uv sync" in " ".join(a))
    assert argv[argv.index("--project") + 1] == str(project)
    # into *this* stage's venv, exactly as locked, without pruning whatever
    # else (another stage, this stage's own 'requirements:') lives in it
    assert {"--active", "--frozen", "--inexact"} <= set(argv)
    assert ctx.env["VIRTUAL_ENV"] == str(ctx.venv_dir)


def test_lock_create_runs_before_sync(make_context, run_recorder, which):
    config = {"uv": {"lock": {"create": "py/uv.lock", "sync": "py/uv.lock"}}}
    ctx = make_context(config=config)
    make_project(ctx)
    run_uv(config, ctx)

    commands = run_recorder.commands()
    assert next(i for i, c in enumerate(commands) if "uv lock" in c) < next(
        i for i, c in enumerate(commands) if "uv sync" in c
    )


def test_lock_without_pyproject_dies(make_context, run_recorder, which):
    config = {"uv": {"lock": {"sync": "py/uv.lock"}}}
    ctx = make_context(config=config)
    (ctx.env_dir / "py").mkdir()
    (ctx.env_dir / "py" / "uv.lock").write_text("version = 1\n")
    with pytest.raises(SystemExit):
        run_uv(config, ctx)


def test_lock_sync_missing_lockfile_dies(make_context, run_recorder, which):
    config = {"uv": {"lock": {"sync": "py/uv.lock"}}}
    ctx = make_context(config=config)
    make_project(ctx, lockfile=False)
    with pytest.raises(SystemExit):
        run_uv(config, ctx)


def test_lock_path_must_name_a_uv_lock_file(make_context, run_recorder, which):
    # uv only ever reads/writes '<project>/uv.lock' -- a path naming anything
    # else is a config error, caught centrally in resolve_defaults.
    config = {"uv": {"lock": {"sync": "py/frozen.lock"}}}
    ctx = make_context(config=config)
    make_project(ctx)
    with pytest.raises(SystemExit):
        run_uv(config, ctx)


def test_lock_unknown_subkey_dies(make_context, run_recorder, which):
    config = {"uv": {"lock": {"syncc": "py/uv.lock"}}}
    ctx = make_context(config=config)
    make_project(ctx)
    with pytest.raises(SystemExit):
        run_uv(config, ctx)


def test_lock_only_stage_never_pip_installs(make_context, run_recorder, which):
    config = {"uv": {"lock": {"sync": "py/uv.lock"}}}
    ctx = make_context(config=config)
    make_project(ctx)
    run_uv(config, ctx)
    assert not any("uv pip install" in c for c in run_recorder.commands())


def test_lock_alongside_requirements_both_run(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "lock": {"sync": "py/uv.lock"}}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    make_project(ctx)
    run_uv(config, ctx)

    commands = run_recorder.commands()
    assert any("uv sync" in c for c in commands)
    # the lockfile is synced first, so the requirements install on top of it
    assert next(i for i, c in enumerate(commands) if "uv sync" in c) < next(
        i for i, c in enumerate(commands) if "uv pip install" in c
    )


def test_lock_sync_lockfile_change_recreates_venv(make_context, run_recorder, which):
    # 'lock: sync:'s lockfile is an install input, so drift in it recreates
    # the venv just like a changed requirements file does
    config = {"uv": {"lock": {"sync": "py/uv.lock"}}}
    ctx = make_context(config=config)
    make_project(ctx)
    run_uv(config, ctx)

    (ctx.env_dir / "py" / "uv.lock").write_text("version = 1\n# changed\n")
    (ctx.venv_dir / "marker").write_text("x")
    start_next_run(ctx, run_recorder)
    run_uv(config, ctx)

    assert any("uv venv" in c for c in run_recorder.commands())
    assert not (ctx.venv_dir / "marker").exists()


def test_lock_sync_unchanged_lockfile_keeps_venv(make_context, run_recorder, which):
    config = {"uv": {"lock": {"sync": "py/uv.lock"}}}
    ctx = make_context(config=config)
    make_project(ctx)
    run_uv(config, ctx)

    start_next_run(ctx, run_recorder)
    run_uv(config, ctx)

    assert not any("uv venv " in c for c in run_recorder.commands())
    assert any("uv sync" in c for c in run_recorder.commands())  # still re-synced


def test_lock_create_output_does_not_invalidate_its_own_checksum(make_context, run_recorder, which):
    # the created lockfile is an *output* (like 'freeze-to:'): writing it must
    # not make the next run think its inputs drifted and recreate the venv
    config = {"uv": {"lock": {"create": "py/uv.lock"}}}
    ctx = make_context(config=config)
    make_project(ctx, lockfile=False)
    run_uv(config, ctx)
    (ctx.env_dir / "py" / "uv.lock").write_text("version = 1\n")  # what `uv lock` would have written

    start_next_run(ctx, run_recorder)
    run_uv(config, ctx)
    assert not any("uv venv " in c for c in run_recorder.commands())


def test_lock_gets_the_same_wheel_sources_as_pip_install(make_context, run_recorder, which):
    config = {
        "uv": {
            "lock": {"create": "py/uv.lock", "sync": "py/uv.lock"},
            "find-links": ["wheels"],
            "no-index": True,
        }
    }
    ctx = make_context(config=config)
    make_project(ctx)
    (ctx.env_dir / "wheels").mkdir()
    run_uv(config, ctx)

    for command in ("uv lock", "uv sync"):
        argv = next(a for a in run_recorder.argvs() if command in " ".join(a))
        assert argv[argv.index("--find-links") + 1] == str((ctx.env_dir / "wheels").resolve())
        assert "--no-index" in argv


def test_lock_skipped_by_skip_if(make_context, run_recorder, which):
    config = {"uv": {"lock": {"sync": "py/uv.lock"}, "skip-if": ["check.sh"]}}
    ctx = make_context(config=config)
    make_project(ctx)
    (ctx.env_dir / "check.sh").write_text("#!/bin/sh\nexit 0\n")
    run_recorder.responses["check.sh"] = lambda cmd: type("R", (), {"returncode": 0})()
    # checksums must already match, otherwise the venv is recreated first
    ctx.venv_dir.mkdir(parents=True)
    from providers.context import sha256_of_files

    (ctx.venv_dir / "uv-checksums.txt").write_text(sha256_of_files([ctx.env_dir / "py" / "uv.lock"], base=ctx.env_dir))

    run_uv(config, ctx)
    assert not any("uv sync" in c for c in run_recorder.commands())


def test_lock_not_synced_under_fast(make_context, run_recorder, which):
    config = {"uv": {"lock": {"sync": "py/uv.lock"}}}
    ctx = make_context(config=config, fast=True)
    make_project(ctx)
    ctx.venv_dir.mkdir(parents=True)
    run_uv(config, ctx)
    assert run_recorder.calls == []


# ---- freeze-to ------------------------------------------------------------------#
def test_freeze_writes_when_configured(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "freeze-to": "frozen.txt"}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    run_uv(config, ctx)
    target = ctx.env_dir / "frozen.txt"
    assert target.is_file()
    assert "auto-generated" in target.read_text()


def test_freeze_skipped_when_unconfigured(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    run_uv(config, ctx)
    assert not (ctx.env_dir / "frozen.txt").exists()


def test_freeze_skipped_when_no_requirements_at_all(make_context, run_recorder, which):
    config = {"uv": {"freeze-to": "frozen.txt"}}
    ctx = make_context(config=config)
    run_uv(config, ctx)
    assert not (ctx.env_dir / "frozen.txt").exists()


# ---- _apply_patches -----------------------------------------------------------#
def test_apply_patches_none_configured(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    run_uv(config, ctx)
    assert not any("venv-patcher" in c for c in run_recorder.commands())


def test_apply_patches_file_missing_dies(make_context, run_recorder, which):
    # a configured patches file that isn't on disk is a config error, caught
    # centrally in resolve_defaults before anything runs.
    config = {"uv": {"requirements": ["r.txt"], "venv-patcher": {"patches": "nope.yml"}}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    with pytest.raises(SystemExit):
        run_uv(config, ctx)


def test_apply_patches_no_patcher_available(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "venv-patcher": {"patches": "patches.yml"}}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    (ctx.env_dir / "patches.yml").write_text("x\n")
    which["venv-patcher"] = None
    run_uv(config, ctx)
    assert not any("apply -f" in c for c in run_recorder.commands())


def test_apply_patches_runs_patcher(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "venv-patcher": {"patches": "patches.yml"}}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    (ctx.env_dir / "patches.yml").write_text("x\n")
    run_uv(config, ctx)
    assert any("apply -f" in c for c in run_recorder.commands())


def test_apply_patches_not_guessed_from_directory_layout(make_context, run_recorder, which):
    # uv/venv-patcher/patches.yml exists but no 'venv-patcher:' section
    # names it, so no patching happens.
    config = {"uv": {"requirements": ["r.txt"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    (ctx.env_dir / "uv" / "venv-patcher").mkdir(parents=True)
    (ctx.env_dir / "uv" / "venv-patcher" / "patches.yml").write_text("x\n")
    run_uv(config, ctx)
    assert not any("apply -f" in c for c in run_recorder.commands())


def test_apply_patches_explicit_conventional_path(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "venv-patcher": {"patches": "uv/venv-patcher/patches.yml"}}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    (ctx.env_dir / "uv" / "venv-patcher").mkdir(parents=True)
    (ctx.env_dir / "uv" / "venv-patcher" / "patches.yml").write_text("x\n")
    run_uv(config, ctx)
    assert any("apply -f" in c for c in run_recorder.commands())


def test_apply_patches_explicit_patcher_path(make_context, run_recorder, which):
    config = {
        "uv": {"requirements": ["r.txt"], "venv-patcher": {"patches": "patches.yml", "exe": "/opt/vp"}},
    }
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    (ctx.env_dir / "patches.yml").write_text("x\n")
    run_uv(config, ctx)
    assert any("/opt/vp apply -f" in c for c in run_recorder.commands())


def test_venv_patcher_without_patches_dies(make_context, run_recorder, which):
    # declaring 'venv-patcher:' but not saying what to apply is a config
    # error -- there is no conventional patches file to fall back to.
    config = {"uv": {"requirements": ["r.txt"], "venv-patcher": {"exe": "/opt/vp"}}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    with pytest.raises(SystemExit):
        run_uv(config, ctx)
