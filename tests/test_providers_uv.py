"""Tests for providers.uv.UvProvider."""

from pathlib import Path

import pytest

from denver_providers.uv import UvProvider


@pytest.fixture(autouse=True)
def _venv_creates_dir(run_recorder):
    """'uv venv [-p <version>] <dir>' has the real side effect of creating <dir>;
    subsequent steps (checksums) depend on it existing. Keyed on
    'uv venv' rather than 'venv -p', since '-p' is only passed when the stage
    actually configures a 'python:' (there is no default -- see
    UvProvider.resolve_defaults).
    Also default 'python3 --version' (used for the in-docker version check) to
    match the version those tests configure, so they only need to override it
    explicitly when they want a mismatch."""

    def create_venv_dir(cmd):
        Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
        return run_recorder.default

    run_recorder.responses["uv venv"] = create_venv_dir
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
    config = {"uv": {"exe": "/opt/uv"}}
    ctx = make_context(config=config)
    run_uv(config, ctx)
    assert any("/opt/uv venv" in c for c in run_recorder.commands())


def test_setup_custom_venv_name_gives_distinct_dir(make_context, run_recorder, which):
    config = {"uv-2": {"venv": "second"}}
    ctx = make_context(config=config)
    run_uv(config, ctx, stage="uv-2")
    assert ctx.env["VIRTUAL_ENV"] == str(ctx.venv_dir_for("second"))


# ---- _ensure_python ----------------------------------------------------------#
def test_ensure_python_in_container_matching_version(make_context, run_recorder, which):
    run_recorder.responses["python3 --version"] = lambda cmd: type(
        "R", (), {"stdout": "Python 3.12.3\n", "returncode": 0}
    )()
    config = {"uv": {"python": "3.12.3"}}
    ctx = make_context(config=config, in_container=True)
    run_uv(config, ctx)
    assert any("uv python find 3.12.3" in c for c in run_recorder.commands())


def test_ensure_python_in_container_mismatch_dies(make_context, run_recorder, which):
    run_recorder.responses["python3 --version"] = lambda cmd: type(
        "R", (), {"stdout": "Python 3.9.0\n", "returncode": 0}
    )()
    config = {"uv": {"python": "3.12.3"}}
    ctx = make_context(config=config, in_container=True)
    with pytest.raises(SystemExit):
        run_uv(config, ctx)


def test_ensure_python_host_installs(make_context, run_recorder, which):
    config = {"uv": {"python": "3.12.3"}}
    ctx = make_context(config=config, in_container=False)
    run_uv(config, ctx)
    assert any("uv python install 3.12.3" in c for c in run_recorder.commands())


def _write_pyvenv_cfg(venv_dir, version, home=None):
    """Give ``venv_dir`` the pyvenv.cfg a real venv creator would have written."""
    venv_dir.mkdir(parents=True, exist_ok=True)
    home = venv_dir if home is None else home
    (venv_dir / "pyvenv.cfg").write_text(f"home = {home}\nimplementation = CPython\nversion_info = {version}\n")


# ---- 'python:' is optional, and an existing venv's interpreter wins ----------#
def test_no_python_configured_passes_no_dash_p(make_context, run_recorder, which):
    # denver picks no interpreter of its own: uv's discovery decides.
    config = {"uv": {}}
    ctx = make_context(config=config)
    run_uv(config, ctx)
    venv_cmd = next(c for c in run_recorder.commands() if " venv " in c)
    assert "-p" not in venv_cmd.split()
    assert not any("python install" in c for c in run_recorder.commands())


def test_no_python_configured_shows_as_null(make_context, which):
    resolved = UvProvider.resolve_defaults(make_context(), {}, {})
    assert resolved["python"] is None


def test_existing_venv_with_matching_python_is_reused(make_context, run_recorder, which):
    config = {"uv": {"python": "3.12.3"}}
    ctx = make_context(config=config)
    _write_pyvenv_cfg(ctx.venv_dir, "3.12.3")
    (ctx.venv_dir / "uv-checksums.txt").write_text("")
    run_uv(config, ctx)
    assert not any(" venv " in c for c in run_recorder.commands())


def test_existing_venv_accepts_a_partial_python_version(make_context, run_recorder, which):
    # a prefix, exactly as uv resolves it: '3.12' is satisfied by 3.12.7
    config = {"uv": {"python": "3.12"}}
    ctx = make_context(config=config)
    _write_pyvenv_cfg(ctx.venv_dir, "3.12.7")
    (ctx.venv_dir / "uv-checksums.txt").write_text("")
    run_uv(config, ctx)
    assert not any(" venv " in c for c in run_recorder.commands())


def test_existing_venv_with_a_different_python_dies(make_context, run_recorder, which, caplog):
    caplog.set_level("INFO")
    config = {"uv": {"python": "3.11.14"}}
    ctx = make_context(config=config)
    _write_pyvenv_cfg(ctx.venv_dir, "3.12.3")
    (ctx.venv_dir / "uv-checksums.txt").write_text("")
    with pytest.raises(SystemExit):
        run_uv(config, ctx)
    assert "is Python 3.12.3, but 'python: 3.11.14' is configured" in caplog.text
    assert "--force" in caplog.text
    assert "'venv:'" in caplog.text


def test_a_patch_version_is_not_satisfied_by_another(make_context, run_recorder, which):
    config = {"uv": {"python": "3.12.3"}}
    ctx = make_context(config=config)
    _write_pyvenv_cfg(ctx.venv_dir, "3.12.4")
    (ctx.venv_dir / "uv-checksums.txt").write_text("")
    with pytest.raises(SystemExit):
        run_uv(config, ctx)


def test_force_recreates_instead_of_reporting_a_mismatch(make_context, run_recorder, which):
    # --force is the resolution the mismatch error asks for, so it is exempt
    config = {"uv": {"python": "3.11.14"}}
    ctx = make_context(config=config, force=True)
    _write_pyvenv_cfg(ctx.venv_dir, "3.12.3")
    run_uv(config, ctx)
    assert any("uv venv -p 3.11.14" in c for c in run_recorder.commands())


def test_a_non_release_python_is_not_compared(make_context, run_recorder, which):
    # uv also accepts 'cpython@3.12', a path, ... -- denver does not
    # re-implement uv's resolution to second-guess those.
    config = {"uv": {"python": "cpython@3.12"}}
    ctx = make_context(config=config)
    _write_pyvenv_cfg(ctx.venv_dir, "3.9.0")
    (ctx.venv_dir / "uv-checksums.txt").write_text("")
    run_uv(config, ctx)
    assert not any(" venv " in c for c in run_recorder.commands())


def test_venv_without_a_pyvenv_cfg_is_not_judged(make_context, run_recorder, which):
    config = {"uv": {"python": "3.12.3"}}
    ctx = make_context(config=config)
    ctx.venv_dir.mkdir(parents=True)
    (ctx.venv_dir / "uv-checksums.txt").write_text("")
    run_uv(config, ctx)
    assert not any(" venv " in c for c in run_recorder.commands())


def test_venv_whose_base_interpreter_vanished_is_recreated(make_context, run_recorder, which, caplog):
    # broken rather than reusable, and no configured value to contradict --
    # the one place recreating unasked is right.
    caplog.set_level("INFO")
    config = {"uv": {}}
    ctx = make_context(config=config)
    _write_pyvenv_cfg(ctx.venv_dir, "3.12.3", home=ctx.env_dir / "gone")
    (ctx.venv_dir / "uv-checksums.txt").write_text("")
    run_uv(config, ctx)
    assert "base interpreter is gone" in caplog.text
    assert any(" venv " in c for c in run_recorder.commands())


def test_two_stages_sharing_a_venv_must_agree_on_python(make_context, run_recorder, which, caplog):
    # previously silently ignored: the second stage installed its interpreter
    # and the venv kept the first stage's.
    caplog.set_level("INFO")
    config = {
        "uv-a": {"provider": "uv", "python": "3.12.3"},
        "uv-b": {"provider": "uv", "python": "3.11.14"},
    }
    ctx = make_context(config=config)
    run_uv(config, ctx, stage="uv-a")
    with pytest.raises(SystemExit):
        run_uv(config, ctx, stage="uv-b")
    assert "'python: 3.11.14' is configured" in caplog.text


def test_two_stages_sharing_a_venv_may_agree_on_python(make_context, run_recorder, which):
    config = {
        "uv-a": {"provider": "uv", "python": "3.12.3"},
        "uv-b": {"provider": "uv", "python": "3.12"},
    }
    ctx = make_context(config=config)
    run_uv(config, ctx, stage="uv-a")
    run_uv(config, ctx, stage="uv-b")
    assert sum(1 for c in run_recorder.commands() if " venv " in c) == 1


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
    from denver_providers.context import sha256_of_files

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
    from denver_providers.context import sha256_of_files

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
# skip-on-success/skip-on-failure are handled generically one layer up, before
# setup() is ever called (see denver.py's _stage_skip_reason) -- coverage for
# that mechanism lives in tests/test_denver_orchestration.py, not here.
@pytest.mark.parametrize(
    "no_index, in_container, expect_present",
    [
        (None, True, False),  # default is off, in docker as much as on the host
        (None, False, False),
        ("auto", True, True),  # explicit 'auto': in-docker turns --no-index on
        ("auto", False, False),  # explicit 'auto': on-host leaves it off
        (True, False, True),  # explicit True wins even on host
        (False, True, False),  # explicit False wins even in-docker
    ],
    ids=[
        "default-in-container",
        "default-on-host",
        "auto-in-container",
        "auto-on-host",
        "explicit-true",
        "explicit-false-in-container",
    ],
)
def test_install_no_index(make_context, run_recorder, which, no_index, in_container, expect_present):
    uv_cfg = {"requirements": ["r.txt"]}
    if no_index is not None:
        uv_cfg["no-index"] = no_index
    config = {"uv": uv_cfg}
    ctx = make_context(config=config, in_container=in_container)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    run_uv(config, ctx)
    install_cmd = next(c for c in run_recorder.commands() if "uv pip install" in c)
    assert ("--no-index" in install_cmd) == expect_present


def test_install_with_overrides(make_context, run_recorder, which):
    config = {
        "uv": {
            "requirements": ["r.txt"],
            "overrides": ["overrides.txt"],
        }
    }
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    (ctx.env_dir / "overrides.txt").write_text("x\n")
    run_uv(config, ctx)
    argv = next(a for a in run_recorder.argvs() if "uv pip install" in " ".join(a))
    assert argv[argv.index("--override") + 1] == str((ctx.env_dir / "overrides.txt").resolve())


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
    from denver_providers.uv import UvProvider

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


# ---- reinstall ---------------------------------------------------------------#
def test_reinstall_default_uses_only_current_run_args(make_context, run_recorder, which):
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


def test_reinstall_true_keeps_prior_run_args(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "reinstall": True}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    run_uv(config, ctx)

    # a later run with a *different* set of requirements (as if a dynamic
    # '$(...)' source dropped this one) must still install the old one too
    config2 = {"uv": {"requirements": ["r2.txt"], "reinstall": True}}
    (ctx.env_dir / "r2.txt").write_text("packaging\n")
    run_recorder.calls.clear()
    run_uv(config2, ctx)

    install_cmd = next(c for c in run_recorder.commands() if "uv pip install" in c)
    assert str((ctx.env_dir / "r.txt").resolve()) in install_cmd
    assert str((ctx.env_dir / "r2.txt").resolve()) in install_cmd


def test_reinstall_persists_across_venv_recreation(make_context, run_recorder, which):
    # the accumulated arg history lives outside the venv dir, so it survives
    # a checksum-triggered recreate instead of being wiped along with it
    config = {"uv": {"requirements": ["r.txt"], "reinstall": True}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    run_uv(config, ctx)

    config2 = {"uv": {"requirements": ["r2.txt"], "reinstall": True}}
    (ctx.env_dir / "r2.txt").write_text("changed\n")  # forces a checksum change -> recreate
    run_recorder.calls.clear()
    run_uv(config2, ctx)

    install_cmd = next(c for c in run_recorder.commands() if "uv pip install" in c)
    assert str((ctx.env_dir / "r.txt").resolve()) in install_cmd
    assert str((ctx.env_dir / "r2.txt").resolve()) in install_cmd


def test_reinstall_deduplicates_unchanged_requirement(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "reinstall": True}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    run_uv(config, ctx)

    run_recorder.calls.clear()
    run_uv(config, ctx)

    install_cmd = next(c for c in run_recorder.commands() if "uv pip install" in c)
    assert install_cmd.count(str((ctx.env_dir / "r.txt").resolve())) == 1


# ---- lockfile ---------------------------------------------------------------#
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


def test_lockfile_syncs_into_the_activated_venv(make_context, run_recorder, which):
    config = {"uv": {"lockfile": "py/uv.lock"}}
    ctx = make_context(config=config)
    project = make_project(ctx)
    run_uv(config, ctx)

    argv = next(a for a in run_recorder.argvs() if "uv sync" in " ".join(a))
    assert argv[argv.index("--project") + 1] == str(project)
    # into *this* stage's venv, exactly as locked, without pruning whatever
    # else (another stage, this stage's own 'requirements:') lives in it
    assert {"--active", "--frozen", "--inexact"} <= set(argv)
    assert ctx.env["VIRTUAL_ENV"] == str(ctx.venv_dir)


def test_lockfile_without_pyproject_dies(make_context, run_recorder, which):
    config = {"uv": {"lockfile": "py/uv.lock"}}
    ctx = make_context(config=config)
    (ctx.env_dir / "py").mkdir()
    (ctx.env_dir / "py" / "uv.lock").write_text("version = 1\n")
    with pytest.raises(SystemExit):
        run_uv(config, ctx)


def test_lockfile_missing_dies(make_context, run_recorder, which):
    config = {"uv": {"lockfile": "py/uv.lock"}}
    ctx = make_context(config=config)
    make_project(ctx, lockfile=False)
    with pytest.raises(SystemExit):
        run_uv(config, ctx)


def test_lockfile_path_must_name_a_uv_lock_file(make_context, run_recorder, which):
    # uv only ever reads/writes '<project>/uv.lock' -- a path naming anything
    # else is a config error, caught centrally in resolve_defaults.
    config = {"uv": {"lockfile": "py/frozen.lock"}}
    ctx = make_context(config=config)
    make_project(ctx)
    with pytest.raises(SystemExit):
        run_uv(config, ctx)


def test_lockfile_only_stage_never_pip_installs(make_context, run_recorder, which):
    config = {"uv": {"lockfile": "py/uv.lock"}}
    ctx = make_context(config=config)
    make_project(ctx)
    run_uv(config, ctx)
    assert not any("uv pip install" in c for c in run_recorder.commands())


def test_lockfile_alongside_requirements_both_run(make_context, run_recorder, which):
    config = {"uv": {"requirements": ["r.txt"], "lockfile": "py/uv.lock"}}
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


def test_lockfile_change_recreates_venv(make_context, run_recorder, which):
    # 'lockfile:' is an install input, so drift in it recreates the venv
    # just like a changed requirements file does
    config = {"uv": {"lockfile": "py/uv.lock"}}
    ctx = make_context(config=config)
    make_project(ctx)
    run_uv(config, ctx)

    (ctx.env_dir / "py" / "uv.lock").write_text("version = 1\n# changed\n")
    (ctx.venv_dir / "marker").write_text("x")
    start_next_run(ctx, run_recorder)
    run_uv(config, ctx)

    assert any("uv venv" in c for c in run_recorder.commands())
    assert not (ctx.venv_dir / "marker").exists()


def test_lockfile_unchanged_keeps_venv(make_context, run_recorder, which):
    config = {"uv": {"lockfile": "py/uv.lock"}}
    ctx = make_context(config=config)
    make_project(ctx)
    run_uv(config, ctx)

    start_next_run(ctx, run_recorder)
    run_uv(config, ctx)

    assert not any("uv venv " in c for c in run_recorder.commands())
    assert any("uv sync" in c for c in run_recorder.commands())  # still re-synced


def test_lockfile_and_install_share_no_index(make_context, run_recorder, which):
    # both uv commands that resolve packages must see the same offline policy
    config = {"uv": {"requirements": ["r.txt"], "lockfile": "py/uv.lock", "no-index": True}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    make_project(ctx)
    run_uv(config, ctx)

    for command in ("uv sync", "uv pip install"):
        argv = next(a for a in run_recorder.argvs() if command in " ".join(a))
        assert "--no-index" in argv


def test_lockfile_not_synced_under_fast(make_context, run_recorder, which):
    config = {"uv": {"lockfile": "py/uv.lock"}}
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


def test_apply_patches_runs_configured_command(make_context, run_recorder, which):
    # 'patches.yml' isn't on disk anywhere -- passed through completely
    # literally, exactly as configured (not e.g. rewritten to an
    # env-dir-relative path that doesn't exist either).
    config = {"uv": {"requirements": ["r.txt"], "patches-apply": ["venv-patcher", "apply", "-f", "patches.yml"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    run_uv(config, ctx)
    assert any("venv-patcher apply -f patches.yml" in c for c in run_recorder.commands())


def test_apply_patches_resolves_relative_path_under_env_dir(make_context, run_recorder, which):
    # a token that does name a real file is rewritten to its absolute path
    # -- 'apply'/'-f'/the bare 'venv-patcher' exe name are left alone since
    # nothing on disk matches them.
    config = {"uv": {"requirements": ["r.txt"], "patches-apply": ["venv-patcher", "apply", "-f", "patches.yml"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    (ctx.env_dir / "patches.yml").write_text("x\n")
    run_uv(config, ctx)
    expected = f"venv-patcher apply -f {ctx.env_dir / 'patches.yml'}"
    assert any(expected in c for c in run_recorder.commands())


def test_apply_patches_resolves_relative_path_from_import(make_context, run_recorder, which, tmp_path):
    # the same fallback resolve_path() itself uses: a 'patches-apply:' entry
    # declared in an imported base config is relative to *that* base's own
    # dir, not the leaf env's -- env_dir stays fixed to the leaf for the
    # whole run, so this only resolves via ctx.import_dirs.
    base_dir = tmp_path / "base-env"
    base_dir.mkdir()
    (base_dir / "patches.yml").write_text("x\n")
    config = {"uv": {"requirements": ["r.txt"], "patches-apply": ["venv-patcher", "apply", "-f", "patches.yml"]}}
    ctx = make_context(config=config, import_dirs=[base_dir])
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    run_uv(config, ctx)
    expected = f"venv-patcher apply -f {base_dir / 'patches.yml'}"
    assert any(expected in c for c in run_recorder.commands())
