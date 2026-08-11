"""Tests for providers.context."""

import errno
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import providers.context as ctxmod
from providers.context import (
    banner,
    die,
    find_in_parents,
    find_outermost_in_parents,
    info,
    interpolate,
    set_quiet,
    sha256_of_files,
    skip_banner,
    stage_banner,
    warn,
)


# ---- logging helpers ------------------------------------------------------ #
def test_info(caplog):
    caplog.set_level("INFO")
    info("hello")
    assert "hello" in caplog.text


def test_warn(caplog):
    caplog.set_level("INFO")
    warn("careful")
    assert "careful" in caplog.text


def test_warn_hidden_at_quiet_level_1(caplog):
    caplog.set_level("INFO")
    set_quiet(1)
    warn("careful")
    set_quiet(0)
    assert "careful" not in caplog.text


def test_banner_has_no_level_prefix(make_context, capsys):
    banner(make_context(), "mystage", "message")
    err = capsys.readouterr().err
    assert "mystage" in err
    assert "message" in err
    assert "INFO:" not in err


def test_banner_includes_stage_id(make_context, capsys):
    # so two same-provider-type stages (e.g. two 'uv' stages) are
    # distinguishable by which one's banner is currently showing
    banner(make_context(), "uv-zephyr", "install")
    err = capsys.readouterr().err
    assert "uv-zephyr" in err
    assert "install" in err


def test_banner_includes_stage_progress(make_context, capsys):
    ctx = make_context()
    ctx.stage_index, ctx.stage_count = 1, 7
    banner(ctx, "conan", "install")
    err = capsys.readouterr().err
    assert "[1/7] conan - install" in err


def test_skip_banner_shows_stage_and_reason(make_context, capsys):
    ctx = make_context()
    ctx.stage_index, ctx.stage_count = 5, 5
    skip_banner(ctx, "uv-zephyr", "skipped by --skip")
    err = capsys.readouterr().err
    assert "[5/5] stage 'uv-zephyr' skipped by --skip" in err


def test_stage_banner_shows_stage_and_provider(make_context, capsys):
    ctx = make_context()
    ctx.stage_index, ctx.stage_count = 4, 5
    stage_banner(ctx, "uv-zephyr", "uv")
    err = capsys.readouterr().err
    assert "[4/5] stage 'uv-zephyr' (uv)" in err


def test_stage_banner_hidden_at_quiet_level_2(make_context, capsys):
    ctx = make_context()
    set_quiet(2)
    stage_banner(ctx, "uv-zephyr", "uv")
    assert capsys.readouterr().err == ""
    set_quiet(0)


def test_run_reports_an_unstartable_command_instead_of_raising(make_context, caplog):
    # a configured 'exe:' naming a file that isn't there: Popen raises before
    # check= applies, so main()'s CalledProcessError handler never sees it.
    caplog.set_level("INFO")
    ctx = make_context()
    ctx.stage_id = "native-tools"
    with pytest.raises(SystemExit):
        ctx.run(["/nonexistent/conan", "config", "home"])
    assert "stage 'native-tools': cannot run /nonexistent/conan config home" in caplog.text


def test_run_reports_an_unstartable_command_without_a_stage(make_context, caplog):
    # the same call outside any stage (a provider driven directly, e.g. in
    # tests) still reports the command rather than raising.
    caplog.set_level("INFO")
    with pytest.raises(SystemExit):
        make_context().run(["/nonexistent/tool"])
    assert "cannot run /nonexistent/tool" in caplog.text


def test_skip_banner_hidden_at_quiet_level_2(make_context, capsys):
    ctx = make_context()
    set_quiet(2)
    skip_banner(ctx, "uv-zephyr", "skipped by --skip")
    assert capsys.readouterr().err == ""
    set_quiet(0)


def test_die(caplog):
    caplog.set_level("INFO")
    with pytest.raises(SystemExit) as exc:
        die("boom")
    assert exc.value.code == 1
    assert "boom" in caplog.text


# ---- parent search -------------------------------------------------------- #
def test_find_in_parents(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "marker").mkdir()
    (tmp_path / "a" / "marker").mkdir()
    found = list(find_in_parents(tmp_path / "a" / "b", "marker"))
    assert (tmp_path / "a") in found
    assert tmp_path in found
    # nearest-first
    assert found[0] == tmp_path / "a"


def test_find_outermost_and_none(tmp_path):
    assert find_outermost_in_parents(tmp_path, "nope") is None
    (tmp_path / "marker").mkdir()
    (tmp_path / "sub").mkdir()
    assert find_outermost_in_parents(tmp_path / "sub", "marker") == tmp_path


# ---- interpolation -------------------------------------------------------- #
def test_interpolate_variants():
    variables = {"A": "x", "B": None}
    assert interpolate("${A}", variables) == "x"
    assert interpolate("${MISSING}", variables) == ""
    assert interpolate("${MISSING:-def}", variables) == "def"
    assert interpolate("${B:-fallback}", variables) == "fallback"
    assert interpolate(["${A}", 1], variables) == ["x", 1]
    assert interpolate({"k": "${A}"}, variables) == {"k": "x"}
    assert interpolate(42, variables) == 42


# ---- the per-env run lock ------------------------------------------------- #
@pytest.fixture(autouse=True)
def _forget_held_locks():
    """Drop this process's held-lock registry between tests (see _HELD_LOCKS)."""
    yield
    for fd in ctxmod._HELD_LOCKS.values():
        os.close(fd)
    ctxmod._HELD_LOCKS.clear()


def test_lock_is_taken_and_stamped(make_context):
    ctx = make_context()
    ctx.acquire_lock()
    stamp = (ctx.env_workdir / ctxmod.LOCK_FILE_NAME).read_text()
    assert stamp.startswith(f"pid={os.getpid()}")
    assert "boot=" in stamp


def test_lock_is_not_taken_under_dry_run(make_context):
    ctx = make_context(dry_run=True)
    ctx.acquire_lock()
    assert not (ctx.env_workdir / ctxmod.LOCK_FILE_NAME).exists()


def test_lock_held_by_this_process_is_not_retaken(make_context):
    # flock is per open file description, so a second open() of the same path
    # would block against the first even from inside one process
    ctx = make_context()
    ctx.acquire_lock()
    ctx.acquire_lock()  # must not deadlock
    assert len(ctxmod._HELD_LOCKS) == 1


def test_lock_waits_for_another_process(make_context, monkeypatch, caplog):
    caplog.set_level("INFO")
    ctx = make_context()
    calls = []

    def fake_flock(fd, flags):
        calls.append(flags)
        if flags & ctxmod.fcntl.LOCK_NB:
            raise OSError(errno.EAGAIN, "held")

    monkeypatch.setattr(ctxmod.fcntl, "flock", fake_flock)
    ctx.acquire_lock()
    assert "waiting for another denver run" in caplog.text
    assert calls[-1] == ctxmod.fcntl.LOCK_EX  # blocking retry


def test_lock_no_wait_fails_instead_of_waiting(make_context, monkeypatch):
    ctx = make_context()

    def fake_flock(fd, flags):
        raise OSError(errno.EAGAIN, "held")

    monkeypatch.setattr(ctxmod.fcntl, "flock", fake_flock)
    with pytest.raises(SystemExit):
        ctx.acquire_lock(wait=False)


def test_lock_warns_where_locking_is_unsupported(make_context, monkeypatch, caplog):
    # some NFS and overlay mounts do not implement flock at all; saying so
    # beats pretending the run is serialised when nothing enforces it
    caplog.set_level("INFO")
    ctx = make_context()

    def fake_flock(fd, flags):
        raise OSError(errno.ENOLCK, "no locks available")

    monkeypatch.setattr(ctxmod.fcntl, "flock", fake_flock)
    ctx.acquire_lock()
    assert "concurrent runs are not serialised" in caplog.text
    assert ctxmod._HELD_LOCKS == {}


# ---- where an env's state lives ------------------------------------------- #
def test_state_dir_lives_under_the_env_dir(tmp_path):
    env_dir = tmp_path / "myenv"
    env_dir.mkdir()
    got = ctxmod.state_dir_for(env_dir, env_dir / "denver.yml", tmp_path / "fallback", env={})
    assert got == env_dir / ".denver" / "denver"


def test_state_dir_separates_variants_in_one_folder(tmp_path):
    # a folder holding denver.debug.yml and denver.release.yml holds two
    # environments, not one -- they must not share a venv.
    env_dir = tmp_path / "myenv"
    env_dir.mkdir()
    debug = ctxmod.state_dir_for(env_dir, env_dir / "denver.debug.yml", tmp_path / "f", env={})
    release = ctxmod.state_dir_for(env_dir, env_dir / "denver.release.yml", tmp_path / "f", env={})
    assert debug != release


def test_state_dir_separates_two_checkouts_of_one_project(tmp_path):
    # the case the old name-keyed layout always collided on
    dirs = []
    for checkout in ("coA", "coB"):
        env_dir = tmp_path / checkout / "myenv"
        env_dir.mkdir(parents=True)
        dirs.append(ctxmod.state_dir_for(env_dir, env_dir / "denver.yml", tmp_path / "f", env={}))
    assert dirs[0] != dirs[1]


def test_state_dir_honours_an_explicit_root(tmp_path):
    env_dir = tmp_path / "myenv"
    env_dir.mkdir()
    got = ctxmod.state_dir_for(
        env_dir, env_dir / "denver.yml", tmp_path / "f", env={ctxmod.STATE_DIR_VAR: str(tmp_path / "elsewhere")}
    )
    assert got.parent == (tmp_path / "elsewhere")
    assert got.name.startswith("myenv-")


def test_state_dir_falls_back_when_the_env_dir_is_read_only(tmp_path):
    env_dir = tmp_path / "myenv"
    env_dir.mkdir()
    env_dir.chmod(0o500)
    try:
        got = ctxmod.state_dir_for(env_dir, env_dir / "denver.yml", tmp_path / "fallback", env={})
    finally:
        env_dir.chmod(0o700)
    assert got.parent == (tmp_path / "fallback")
    assert got.name.startswith("myenv-")


def test_ensure_state_dir_makes_the_state_ignore_itself(make_context):
    ctx = make_context()
    ctx.ensure_state_dir()
    marker = ctx.env_workdir.parent / ".gitignore"
    assert marker.read_text().endswith("*\n")


def test_ensure_state_dir_keeps_an_edited_gitignore(make_context):
    ctx = make_context()
    ctx.ensure_state_dir()
    marker = ctx.env_workdir.parent / ".gitignore"
    marker.write_text("mine\n")
    ctx.ensure_state_dir()
    assert marker.read_text() == "mine\n"


def test_ensure_state_dir_writes_no_marker_under_a_shared_root(make_context, tmp_path, monkeypatch):
    monkeypatch.setenv(ctxmod.STATE_DIR_VAR, str(tmp_path / "shared"))
    ctx = make_context()
    ctx.ensure_state_dir()
    assert not (ctx.env_workdir.parent / ".gitignore").exists()


def test_ensure_state_dir_reports_a_leftover_legacy_directory(make_context, caplog):
    caplog.set_level("INFO")
    ctx = make_context()
    legacy = ctx.denver_dir / ".envs" / ctx.env_name
    legacy.mkdir(parents=True)
    ctx.ensure_state_dir()
    # never migrated automatically: that directory was keyed on the env dir's
    # bare name, so it may hold a *different* env's state
    assert "no longer used and can be deleted" in caplog.text
    assert legacy.is_dir()


def test_cache_dir_defaults_and_is_overridable(make_context):
    ctx = make_context()
    assert ctx.cache_dir == Path("~/.cache/denver").expanduser()
    assert ctx.env["DENVER_CACHE_DIR"] == str(ctx.cache_dir)
    other = make_context(env={ctxmod.CACHE_DIR_VAR: "/tmp/shared-cache"})
    assert other.env["DENVER_CACHE_DIR"] == "/tmp/shared-cache"


# ---- Context basics ------------------------------------------------------- #
# ---- container detection / relocation bookkeeping ------------------------- #
def test_in_container_via_explicit_variable(monkeypatch):
    # what a wrapper hands across the boundary, so the process inside never
    # has to infer it from a runtime's marker file
    monkeypatch.setattr(ctxmod, "_CONTAINER_MARKERS", ())
    assert ctxmod.in_container({ctxmod.IN_CONTAINER_VAR: "1"}) is True


def test_in_container_via_runtime_variable(monkeypatch):
    # podman/systemd-nspawn/lxc set this themselves
    monkeypatch.setattr(ctxmod, "_CONTAINER_MARKERS", ())
    assert ctxmod.in_container({"container": "podman"}) is True


def test_in_container_via_marker_file(monkeypatch, tmp_path):
    marker = tmp_path / ".containerenv"
    marker.write_text("")
    monkeypatch.setattr(ctxmod, "_CONTAINER_MARKERS", (str(marker),))
    assert ctxmod.in_container({}) is True


def test_in_container_false_without_any_signal(monkeypatch):
    monkeypatch.setattr(ctxmod, "_CONTAINER_MARKERS", ())
    assert ctxmod.in_container({}) is False


def test_in_docker_is_a_read_only_alias(make_context):
    ctx = make_context(in_container=True)
    assert ctx.in_docker is True
    # assigning it used to be how callers faked "inside a container"; that has
    # to fail loudly now rather than silently stop having an effect
    with pytest.raises(AttributeError):
        ctx.in_docker = False


def test_relocated_lists_the_wrapper_stages(make_context):
    ctx = make_context()
    assert ctx.relocated == []
    ctx.set(ctxmod.RELOCATED_VAR, "docker,launcher")
    assert ctx.relocated == ["docker", "launcher"]


def test_relocated_ignores_an_empty_value(make_context):
    ctx = make_context()
    ctx.set(ctxmod.RELOCATED_VAR, "")
    assert ctx.relocated == []


def test_builtins_host(make_context):
    ctx = make_context(in_container=False)
    assert ctx.env["DENVER_ENV_NAME"] == "myenv"
    assert ctx.venv_dir.name.endswith(".venv.host")


def test_builtins_docker_venv(make_context):
    ctx = make_context(in_container=True)
    assert ctx.venv_dir.name == ".venv"


def test_denver_builtin_always_overrides_real_env(make_context):
    # DENVER_-prefixed built-ins always reflect the current run, even if a
    # stale value of the same name was already in the process environment
    ctx = make_context(env={"DENVER_ENV_NAME": "stale"})
    assert ctx.env["DENVER_ENV_NAME"] == "myenv"


# ---- frozen build: bundled libraries must not leak into child processes ----#
def test_frozen_build_drops_its_own_ld_library_path(make_context, monkeypatch):
    # a one-file build runs with LD_LIBRARY_PATH pointing at its extraction
    # dir; a child inheriting it loads denver's bundled libraries instead of
    # the system's, which is how `xz` ended up unable to satisfy its own
    # symbol versions (see _drop_bundled_library_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    ctx = make_context(env={"LD_LIBRARY_PATH": "/tmp/_MEItest"})
    assert "LD_LIBRARY_PATH" not in ctx.env


def test_frozen_build_restores_the_users_own_ld_library_path(make_context, monkeypatch):
    # PyInstaller stashes any pre-existing value here -- the child must see
    # that one, not denver's, and never the stash variable itself
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    ctx = make_context(env={"LD_LIBRARY_PATH": "/tmp/_MEItest", "LD_LIBRARY_PATH_ORIG": "/opt/mine/lib"})
    assert ctx.env["LD_LIBRARY_PATH"] == "/opt/mine/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in ctx.env


def test_unfrozen_denver_leaves_ld_library_path_alone(make_context):
    # nothing was bundled, so the variable is the user's own business
    ctx = make_context(env={"LD_LIBRARY_PATH": "/opt/mine/lib"})
    assert ctx.env["LD_LIBRARY_PATH"] == "/opt/mine/lib"


def test_shell_prompt_prefix_exported(make_context):
    ctx = make_context()
    assert ctx.env["SHELL_PROMPT_PREFIX"] == "(myenv) "


def test_ps1_left_alone(make_context):
    # an interactive bash re-reads its rc files after denver execs it and
    # assigns PS1 outright, so setting PS1 here would be discarded anyway --
    # PROMPT_COMMAND carries the marker for bash instead.
    ctx = make_context(env={"PS1": r"\u@\h:\w\$ "})
    assert ctx.env["PS1"] == r"\u@\h:\w\$ "


def test_prompt_command_set_when_none_inherited(make_context):
    ctx = make_context()
    assert ctx.env["PROMPT_COMMAND"] == ctx.prompt_command
    assert "(myenv) " in ctx.env["PROMPT_COMMAND"]


def test_prompt_command_appended_to_existing(make_context):
    ctx = make_context(env={"PROMPT_COMMAND": "__set_title"})
    assert ctx.env["PROMPT_COMMAND"] == f"__set_title; {ctx.prompt_command}"


def test_prompt_command_trailing_semicolon_normalised(make_context):
    # '<existing>; <snippet>' with a trailing ';' already on the inherited
    # value would be a bash syntax error ('__set_title; ; case ...')
    ctx = make_context(env={"PROMPT_COMMAND": "__set_title ; "})
    assert ctx.env["PROMPT_COMMAND"] == f"__set_title; {ctx.prompt_command}"


def test_prompt_command_not_appended_twice(make_context):
    ctx = make_context(env={"PROMPT_COMMAND": "__set_title"})
    again = make_context(env={"PROMPT_COMMAND": ctx.env["PROMPT_COMMAND"]})
    assert again.env["PROMPT_COMMAND"] == ctx.env["PROMPT_COMMAND"]


def test_zsh_prompt_set(make_context):
    ctx = make_context()
    assert ctx.env["PROMPT"] == "(myenv) %m%#"


def test_zsh_prompt_written_after_ps1(make_context):
    # PROMPT and PS1 are the same parameter in zsh, so of the two, whichever
    # comes later in environ is the one zsh keeps -- PROMPT has to be last,
    # whatever order the two arrived in.
    ctx = make_context(env={"PROMPT": "stale", "PS1": "inherited"})
    keys = list(ctx.env)
    assert keys.index("PROMPT") > keys.index("PS1")
    assert ctx.env["PROMPT"] == "(myenv) %m%#"


@pytest.mark.skipif(not shutil.which("zsh"), reason="zsh not installed")
def test_zsh_prompt_wins_in_real_zsh(make_context):
    # the ordering above is only worth anything if zsh actually resolves it
    # this way -- so assert against zsh itself, not just our own dict order.
    ctx = make_context(env={"PS1": "inherited"})
    env = {"PATH": os.environ["PATH"], **{k: ctx.env[k] for k in ("PS1", "PROMPT")}}
    result = subprocess.run(["zsh", "-c", 'printf "%s" "$PS1"'], env=env, capture_output=True, text=True, check=True)
    assert result.stdout == "(myenv) %m%#"


def test_prompt_command_is_idempotent_per_prompt(make_context):
    # bash runs PROMPT_COMMAND before *every* prompt: without the 'case'
    # guard PS1 would grow '(myenv) (myenv) ...' line after line.
    ctx = make_context()
    script = f"PS1='$ '\n{ctx.prompt_command}\n{ctx.prompt_command}\n{ctx.prompt_command}\nprintf '%s' \"$PS1\""
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    assert result.stdout == "(myenv) $ "


def test_variables_property(make_context):
    ctx = make_context()
    assert ctx.variables is ctx.env


# ---- config section ------------------------------------------------------- #
def test_section_interpolated(make_context):
    ctx = make_context(config={"uv": {"python": "${DENVER_ENV_NAME}"}})
    assert ctx.section("uv") == {"python": "myenv"}
    assert ctx.section("missing") == {}


# ---- path resolution ------------------------------------------------------ #
def test_resolve_path_non_path_value_dies(make_context, caplog):
    # a wrong YAML type (a list where one path is expected) must give
    # denver's own message, not a raw pathlib TypeError
    ctx = make_context()
    with pytest.raises(SystemExit):
        ctx.resolve_path(["conan/base_classes"])
    assert "expected a path in denver.yml" in caplog.text


def test_resolve_path_absolute(make_context, tmp_path):
    ctx = make_context()
    assert ctx.resolve_path(str(tmp_path)) == tmp_path


def test_resolve_path_env_dir(make_context):
    ctx = make_context()
    (ctx.env_dir / "file.txt").write_text("x")
    assert ctx.resolve_path("file.txt") == ctx.env_dir / "file.txt"


def test_resolve_path_import_dir(make_context, tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "shared.txt").write_text("x")
    ctx = make_context(import_dirs=[base])
    assert ctx.resolve_path("shared.txt") == base / "shared.txt"


def test_resolve_path_missing_defaults_to_env_dir(make_context):
    ctx = make_context()
    assert ctx.resolve_path("nope.txt") == ctx.env_dir / "nope.txt"


def test_resolve_path_custom_base(make_context, tmp_path):
    ctx = make_context()
    (tmp_path / "b.txt").write_text("x")
    assert ctx.resolve_path("b.txt", base=tmp_path) == tmp_path / "b.txt"


# ---- env manipulation ----------------------------------------------------- #
def test_set_setdefault(make_context):
    ctx = make_context()
    ctx.set("K", None)
    assert ctx.env["K"] == ""
    ctx.setdefault("K", "v")  # empty -> replaced
    assert ctx.env["K"] == "v"
    ctx.setdefault("K", "other")  # non-empty -> kept
    assert ctx.env["K"] == "v"


def test_prepend_path(make_context):
    ctx = make_context()
    ctx.env.pop("PATH", None)
    ctx.prepend_path("/a")
    assert ctx.env["PATH"] == "/a"
    ctx.prepend_path("/b")
    assert ctx.env["PATH"] == f"/b{os.pathsep}/a"


def test_append_path_var(make_context):
    ctx = make_context()
    ctx.env.pop("X", None)
    ctx.append_path_var("X", "1")
    ctx.append_path_var("X", "2")
    assert ctx.env["X"] == f"1{os.pathsep}2"


def test_apply_env_map(make_context):
    ctx = make_context()
    ctx.apply_env_map({"GREETING": "hi ${DENVER_ENV_NAME}"})
    assert ctx.env["GREETING"] == "hi myenv"
    ctx.apply_env_map(None)  # no-op


# ---- stage toggles -------------------------------------------------------- #
def test_toggles(make_context):
    # force/ci are plain constructor-set flags -- never read back out of a
    # real environment variable, so setting ctx.env["FORCE"]/["CI"] has no
    # effect on them.
    ctx = make_context()
    assert not ctx.force
    assert not ctx.ci
    ctx.env["FORCE"] = "ON"
    ctx.env["CI"] = "ON"
    assert not ctx.force
    assert not ctx.ci

    ctx = make_context(force=True, ci=True)
    assert ctx.force
    assert ctx.ci


# ---- run / which ---------------------------------------------------------- #
def test_run_forwards_args(make_context, run_recorder):
    ctx = make_context()
    ctx.run(["echo", "hi"], cwd="/tmp", check=False, capture=True, extra_env={"E": "1"})
    call = run_recorder.calls[-1]
    assert call.kwargs["cwd"] == "/tmp"
    assert call.kwargs["check"] is False
    assert call.kwargs["capture_output"] is True
    assert call.kwargs["env"]["E"] == "1"


def test_run_echo(make_context, run_recorder, capsys):
    ctx = make_context()
    ctx.run(["true"], echo=True)
    assert "+ true" in capsys.readouterr().err


def test_run_step_prints_banner_before_echo(make_context, run_recorder, capsys):
    ctx = make_context()
    ctx.stage_id = "mystage"
    ctx.run(["true"], step="do the thing")
    err = capsys.readouterr().err
    assert "mystage - do the thing" in err
    assert err.index("do the thing") < err.index("+ true")


def test_run_without_step_prints_no_banner(make_context, run_recorder, capsys):
    ctx = make_context()
    ctx.stage_id = "mystage"
    ctx.run(["true"])
    err = capsys.readouterr().err
    assert "mystage" not in err


def test_run_quiet_suppresses_echo_and_output(make_context, run_recorder, capsys):
    ctx = make_context(quiet=True)
    ctx.run(["true"], echo=True)
    out, err = capsys.readouterr()
    assert out == ""
    assert err == ""
    call = run_recorder.calls[-1]
    assert call.kwargs["stdout"] == subprocess.DEVNULL
    assert call.kwargs["stderr"] == subprocess.DEVNULL


def test_run_quiet_does_not_override_explicit_capture(make_context, run_recorder):
    ctx = make_context(quiet=True)
    ctx.run(["true"], capture=True)
    call = run_recorder.calls[-1]
    assert call.kwargs["capture_output"] is True
    assert "stdout" not in call.kwargs
    assert "stderr" not in call.kwargs


def test_quiet_level_1_hides_info_but_collapses_banner_to_one_line(make_context, capsys, caplog):
    caplog.set_level("INFO")
    ctx = make_context(quiet=1)
    info("hidden-info")
    banner(ctx, "mystage", "still-shown")
    assert "hidden-info" not in caplog.text
    err = capsys.readouterr().err
    assert "-- [1/1] mystage - still-shown" in err
    assert err.count("\n") == 1  # single line, no box
    # a later non-quiet Context resets the shared logger/flag for other tests
    make_context(quiet=0)


def test_quiet_level_2_hides_info_and_banner(make_context, capsys, caplog):
    caplog.set_level("INFO")
    ctx = make_context(quiet=2)
    info("hidden-info")
    banner(ctx, "mystage", "hidden-banner")
    assert "hidden-info" not in caplog.text
    assert capsys.readouterr().err == ""
    # a later non-quiet Context resets the shared logger/flag for other tests
    make_context(quiet=0)


def test_banner_normal_mode_shows_a_boxed_frame(make_context, capsys):
    ctx = make_context()
    banner(ctx, "mystage", "message")
    err = capsys.readouterr().err
    assert err.count("\n") == 3  # top border, text line, bottom border
    assert "| [1/1] mystage - message |" in err


def test_which(make_context, which):
    ctx = make_context()
    which["conan"] = "/opt/conan"
    assert ctx.which("conan") == "/opt/conan"


# ---- source --------------------------------------------------------------- #
def test_source_noop_for_missing(make_context):
    ctx = make_context()
    before = dict(ctx.env)
    ctx.source(ctx.env_dir / "does-not-exist.sh")
    assert ctx.env == before


def test_source_folds_exports(make_context):
    ctx = make_context()
    script = ctx.env_dir / "hook.sh"
    script.write_text("export FROM_SOURCE=yes\n")
    ctx.source(script)
    assert ctx.env["FROM_SOURCE"] == "yes"


def test_source_failure_dies(make_context):
    ctx = make_context()
    script = ctx.env_dir / "bad.sh"
    script.write_text("exit 3\n")
    with pytest.raises(SystemExit):
        ctx.source(script)


# ---- exec ----------------------------------------------------------------- #
def test_exec_calls_execvpe(make_context, exec_recorder):
    ctx = make_context()
    ctx.exec(["fish", "-l"])
    assert exec_recorder["file"] == "fish"
    assert exec_recorder["args"] == ["fish", "-l"]


def test_exec_empty_command_dies(make_context):
    ctx = make_context()
    with pytest.raises(SystemExit):
        ctx.exec([])


def test_exec_nul_byte_in_command_dies(make_context):
    ctx = make_context()
    with pytest.raises(SystemExit):
        ctx.exec(["fish", "-c", "echo\0hi"])


def test_exec_oserror_dies(make_context, monkeypatch):
    ctx = make_context()

    def boom(*a, **k):
        raise OSError("nope")

    monkeypatch.setattr(ctxmod.os, "execvpe", boom)
    with pytest.raises(SystemExit):
        ctx.exec(["missing-binary"])


# ---- checksums ------------------------------------------------------------ #
def test_sha256_of_files_is_independent_of_where_the_tree_lives(tmp_path):
    # the same requirements file in two checkouts of one project must
    # fingerprint identically -- otherwise every switch between them looks
    # like drift and rebuilds the venv from scratch.
    blocks = []
    for checkout in ("coA", "coB"):
        env_dir = tmp_path / checkout / "myenv"
        env_dir.mkdir(parents=True)
        (env_dir / "r.txt").write_text("packaging\n")
        blocks.append(sha256_of_files([env_dir / "r.txt"], base=env_dir))
    assert blocks[0] == blocks[1]


def test_sha256_of_files_still_separates_different_files(tmp_path):
    (tmp_path / "a.txt").write_text("same\n")
    (tmp_path / "b.txt").write_text("same\n")
    one = sha256_of_files([tmp_path / "a.txt"], base=tmp_path)
    other = sha256_of_files([tmp_path / "b.txt"], base=tmp_path)
    assert one != other


def test_fingerprint_label_keeps_an_absolute_path_without_a_base(tmp_path):
    assert ctxmod.fingerprint_label(tmp_path / "r.txt") == str(tmp_path / "r.txt")


def test_fingerprint_label_reaches_outside_the_base(tmp_path):
    # a file in an imported base env sits beside the env dir, not under it;
    # '../' is still stable across checkouts of the same layout.
    env_dir = tmp_path / "leaf"
    env_dir.mkdir()
    assert ctxmod.fingerprint_label(tmp_path / "base" / "r.txt", env_dir) == "../base/r.txt"


def test_sha256_of_files(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    block = sha256_of_files([f, tmp_path / "missing.txt"])
    assert str(f) in block
    assert "0" * 64 in block  # missing file placeholder
