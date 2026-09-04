"""Tests for --dry-run: previewing what a run would do, without doing it.

The contract (see Context.run's docstring) is deliberately narrow: commands
that exist for their *effect* and filesystem writes are printed and skipped;
read-only queries (capture=True) and sourced scripts really run, because they
are what the printed commands are derived from. Both halves are asserted
here -- a dry run that silently stopped querying would print commands a real
run would never use, and one that silently kept writing would be worse than
no dry run at all.
"""

import json
import re
import subprocess

import pytest
import yaml

import denver
import denver_providers as providers
from denver_providers.conan import ConanProvider
from denver_providers.context import _dry_tag
from denver_providers.uv import UvProvider
from denver_providers.zephyr import ZephyrProvider

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _without_legend(lines):
    """``lines`` minus dry_run_legend()'s own one-time block, if it's the first thing in them.

    Exactly six dry-run-tagged lines (one intro, one per marker), always
    first whenever a run actually prints the legend -- skipped so its "'+'
    command that would run" etc. don't count as (fake) notes alongside real
    ones sharing that marker.
    """
    if lines and "no command below is executed for its effect" in lines[0]:
        return lines[6:]
    return lines


def dry_lines(capsys, marker=None):
    """Every real (non-legend) '[dry-run ...] ...' line from stderr (ANSI color stripped), optionally only those with ``marker``.

    Each marker's tag is colored (see DRY_MARKER_COLORS) -- stripped here so
    callers can assert on the plain message text without also having to
    spell out which color a given marker uses.
    """
    all_lines = [_ANSI_RE.sub("", ln) for ln in capsys.readouterr().err.splitlines() if "[dry-run" in ln]
    lines = _without_legend(all_lines)
    if marker is None:
        return lines
    prefix = f"[dry-run {marker}] "
    return [ln[len(prefix) :] for ln in lines if ln.startswith(prefix)]


# ---- Context.run -------------------------------------------------------------#
def test_run_prints_effect_command_instead_of_running_it(make_context, run_recorder, capsys):
    ctx = make_context(dry_run=True)
    result = ctx.run(["touch", "/tmp/nope"])

    assert run_recorder.calls == []
    assert dry_lines(capsys, "+") == ["touch /tmp/nope"]
    # stands in as an immediately-successful, output-less call, so a caller
    # branching on it takes the same path a successful real run would
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert result.args == ["touch", "/tmp/nope"]


def test_run_prints_effect_command_even_under_quiet(make_context, capsys):
    # in a dry run these lines *are* the output: --quiet suppressing them
    # would leave the run with nothing to show at all.
    ctx = make_context(dry_run=True, quiet=2)
    ctx.run(["touch", "/tmp/nope"])
    assert dry_lines(capsys, "+") == ["touch /tmp/nope"]


def test_run_really_runs_capture_queries(make_context, run_recorder, capsys):
    # a query exists for its output, which some provider is about to branch
    # on -- skipping it would leave a dry run with nothing to decide with.
    ctx = make_context(dry_run=True)
    run_recorder.responses["config home"] = type("R", (), {"stdout": "/home/conan\n", "returncode": 0})()
    result = ctx.run(["conan", "config", "home"], capture=True, echo=False)

    assert run_recorder.commands() == ["conan config home"]
    assert result.stdout == "/home/conan\n"
    assert dry_lines(capsys, "?") == ["conan config home"]


def test_run_reports_a_query_that_failed(make_context, run_recorder, capsys):
    # a failed query answered nothing, so whatever the caller derives from
    # it below is a guess -- silence would let the preview look
    # authoritative exactly where it is least so.
    ctx = make_context(dry_run=True)
    run_recorder.responses["config home"] = type("R", (), {"stdout": "", "returncode": 2})()
    ctx.run(["conan", "config", "home"], capture=True, echo=False, check=False)

    assert any("exited 2" in ln for ln in dry_lines(capsys, "?"))


def test_run_query_with_missing_executable_is_reported_not_fatal(make_context, monkeypatch, capsys):
    # previewing an env whose tools an earlier (skipped) stage would have
    # installed is half the point, so a missing one degrades to a failed
    # query rather than raising out of the middle of the preview.
    def boom(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(subprocess, "run", boom)
    ctx = make_context(dry_run=True)
    result = ctx.run(["west", "list"], capture=True, echo=False, check=True)

    assert result.returncode == 127
    assert result.stdout == ""
    assert any("not available" in ln for ln in dry_lines(capsys, "?"))


def test_run_dry_note_reaches_query_before_step_banner(make_context, run_recorder, capsys):
    # banner()'s own step marker is --verbose only (see set_quiet/banner);
    # the --dry-run preview lines below it are unconditional -- this checks
    # the two never print out of order when both are on.
    ctx = make_context(dry_run=True, verbose=True)
    ctx.stage_id = "mystage"
    ctx.run(["make", "all"], step="build")
    err = capsys.readouterr().err
    assert err.index("mystage - build") < err.index(f"{_dry_tag('+')} make all")


def test_run_still_executes_normally_without_dry_run(make_context, run_recorder, capsys):
    ctx = make_context()
    ctx.run(["touch", "/tmp/nope"])
    assert run_recorder.commands() == ["touch /tmp/nope"]
    assert dry_lines(capsys) == []


# ---- Context.exec ------------------------------------------------------------#
def test_exec_prints_command_and_returns(make_context, exec_recorder, capsys):
    ctx = make_context(dry_run=True)
    ctx.exec(["echo", "hi"])
    assert exec_recorder == {}  # os.execvpe never reached
    assert dry_lines(capsys, "+") == ["exec: echo hi"]


def test_exec_still_validates_the_command_under_dry_run(make_context):
    ctx = make_context(dry_run=True)
    with pytest.raises(SystemExit):
        ctx.exec([""])


# ---- Context.which -----------------------------------------------------------#
def test_which_dry_fallback_returns_bare_name_once_warned(make_context, which, capsys, caplog):
    which["west"] = None
    ctx = make_context(dry_run=True)

    assert ctx.which("west", dry_fallback=True) == "west"
    assert ctx.which("west", dry_fallback=True) == "west"
    # warned once per tool, not once per lookup: every stage re-resolves its
    # own defaults, so a per-lookup warning would bury the preview
    assert len([r for r in caplog.records if "not on PATH" in r.message]) == 1


def test_which_without_dry_fallback_still_reports_missing(make_context, which):
    which["west"] = None
    ctx = make_context(dry_run=True)
    assert ctx.which("west") is None


def test_which_dry_fallback_prefers_a_real_hit(make_context, which):
    which["west"] = "/usr/bin/west"
    ctx = make_context(dry_run=True)
    assert ctx.which("west", dry_fallback=True) == "/usr/bin/west"


def test_which_dry_fallback_inactive_without_dry_run(make_context, which):
    which["west"] = None
    ctx = make_context()
    assert ctx.which("west", dry_fallback=True) is None


# ---- Context filesystem helpers ----------------------------------------------#
def test_filesystem_helpers_write_for_real_without_dry_run(make_context, tmp_path):
    ctx = make_context()
    target = tmp_path / "sub" / "f.txt"

    ctx.mkdir(target.parent)
    ctx.write_text(target, "one\n")
    ctx.append_text(target, "two\n")
    assert target.read_text() == "one\ntwo\n"

    stamp = tmp_path / "sub" / "stamp"
    ctx.touch(stamp)
    assert stamp.is_file()
    ctx.unlink(stamp)
    assert not stamp.exists()

    ctx.rmtree(target.parent)
    assert not target.parent.exists()


def test_filesystem_helpers_only_report_under_dry_run(make_context, tmp_path, capsys):
    ctx = make_context(dry_run=True)
    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "f.txt").write_text("original\n")

    ctx.mkdir(tmp_path / "new")
    ctx.write_text(keep / "f.txt", "replaced\n")
    ctx.append_text(keep / "f.txt", "appended\n")
    ctx.touch(keep / "stamp")
    ctx.unlink(keep / "f.txt")
    ctx.rmtree(keep)

    assert (keep / "f.txt").read_text() == "original\n"
    assert not (keep / "stamp").exists()
    assert not (tmp_path / "new").exists()
    assert keep.is_dir()

    reported = dry_lines(capsys, "~")
    assert reported == [
        f"mkdir {tmp_path / 'new'}",
        f"write {keep / 'f.txt'}",
        f"append to {keep / 'f.txt'}",
        f"touch {keep / 'stamp'}",
        f"rm {keep / 'f.txt'}",
        f"rm -r {keep}",
    ]


def test_dry_mkdir_stays_quiet_for_a_directory_that_already_exists(make_context, tmp_path, capsys):
    ctx = make_context(dry_run=True)
    ctx.mkdir(tmp_path)
    assert dry_lines(capsys, "~") == []


# ---- Context.source ----------------------------------------------------------#
def test_source_still_folds_exports_under_dry_run(make_context, tmp_path, capsys):
    # sourcing is how denver *computes* the environment: a command rendered
    # without it would show empty ${...} values and a PATH missing every
    # tool an earlier stage put there.
    script = tmp_path / "vars.sh"
    script.write_text("export FROM_SOURCE=yes\n")
    ctx = make_context(dry_run=True)
    ctx.source(script)

    assert ctx.env["FROM_SOURCE"] == "yes"
    assert dry_lines(capsys, ".") == [str(script)]


def test_source_skips_a_missing_script_silently_under_dry_run(make_context, tmp_path, capsys):
    ctx = make_context(dry_run=True)
    ctx.source(tmp_path / "never-written.sh")
    assert dry_lines(capsys) == []


# ---- uv provider -------------------------------------------------------------#
def _run_uv(config, ctx, stage="uv"):
    ctx.config = config
    config[stage] = UvProvider.resolve_defaults(ctx, config.get(stage) or {}, config)
    provider = UvProvider(config)
    provider.stage = stage
    ctx.stage_id = stage
    provider.setup(ctx)


def test_uv_dry_run_keeps_the_existing_venv_and_writes_nothing(make_context, run_recorder, which, capsys):
    config = {"uv": {"requirements": ["r.txt"]}}
    ctx = make_context(config=config, dry_run=True)
    (ctx.env_dir / "r.txt").write_text("packaging\n")
    ctx.venv_dir.mkdir(parents=True)
    (ctx.venv_dir / "marker").write_text("x")  # stale venv a real run would wipe

    _run_uv(config, ctx)

    assert run_recorder.calls == []
    assert (ctx.venv_dir / "marker").exists()  # NOT wiped
    assert not (ctx.venv_dir / "uv-checksums.txt").exists()
    assert not ctx.logs_dir.exists()
    # the `uv venv` a real run would follow the removal with is still shown,
    # even though the (unremoved) venv dir is still sitting there
    shown = dry_lines(capsys, "+")
    assert any("uv venv" in c for c in shown)
    assert any("uv pip install" in c for c in shown)


def test_uv_dry_run_previews_with_uv_missing_from_path(make_context, which, capsys):
    which["uv"] = None
    config = {"uv": {}}
    ctx = make_context(config=config, dry_run=True)
    _run_uv(config, ctx)
    assert any(c.startswith("uv venv") for c in dry_lines(capsys, "+"))


def test_uv_in_container_skips_the_version_check_when_python3_cannot_answer(make_context, run_recorder, which, capsys):
    # only reachable under --dry-run, where a failed query is reported rather
    # than fatal: there is simply no version to compare then.
    config = {"uv": {"python": "3.12.3"}}
    ctx = make_context(config=config, dry_run=True, in_container=True)
    run_recorder.responses["python3 --version"] = type("R", (), {"stdout": "", "returncode": 127})()

    _run_uv(config, ctx)
    assert any("uv python find 3.12.3" in c for c in dry_lines(capsys, "+"))


def test_uv_dry_run_previews_sync_of_a_lockfile_that_has_not_been_written_yet(make_context, which, capsys):
    config = {"uv": {"lockfile": "py/uv.lock"}}
    ctx = make_context(config=config, dry_run=True)
    (ctx.env_dir / "py").mkdir()
    (ctx.env_dir / "py" / "pyproject.toml").write_text("[project]\nname='x'\n")

    _run_uv(config, ctx)
    shown = dry_lines(capsys, "+")
    assert any("uv sync" in c for c in shown)


# ---- conan provider ----------------------------------------------------------#
def _run_conan(config, ctx, stage="conan"):
    ctx.config = config
    config[stage] = ConanProvider.resolve_defaults(ctx, config.get(stage) or {}, config)
    provider = ConanProvider(config)
    provider.stage = stage
    ctx.stage_id = stage
    provider.setup(ctx)


def test_conan_dry_run_previews_profile_detection_when_conan_cannot_answer(
    make_context, run_recorder, which, capsys, caplog
):
    which["conan"] = None
    config = {"conan": {}}
    ctx = make_context(config=config, dry_run=True)
    run_recorder.responses["config home"] = type("R", (), {"stdout": "", "returncode": 127, "stderr": ""})()

    _run_conan(config, ctx)

    assert any("conan profile detect" in c for c in dry_lines(capsys, "+"))
    assert any("config home` failed" in r.message for r in caplog.records)


def test_conan_dry_run_does_not_warn_about_a_venv_it_cannot_compare_against(
    make_context, run_recorder, which, capsys, caplog
):
    # a bare name is --dry-run's stand-in for a conan no stage installed yet:
    # it names no location, so there's nothing to compare against the venv
    # (and resolving it would silently make it cwd-relative).
    which["conan"] = None
    config = {"conan": {}}
    ctx = make_context(config=config, dry_run=True)
    ctx.set("VIRTUAL_ENV", "/some/venv")
    run_recorder.responses["config home"] = type("R", (), {"stdout": "/home/conan\n", "returncode": 0})()

    _run_conan(config, ctx)
    assert not any("from outside the active venv" in r.message for r in caplog.records)


def test_conan_dry_run_leaves_the_install_tree_alone(make_context, run_recorder, which, tmp_path, capsys):
    config = {"conan": {"conanfile": "conanfile.py"}}
    ctx = make_context(config=config, dry_run=True)
    (ctx.env_dir / "conanfile.py").write_text("# recipe\n")
    install_root = ctx.env_workdir / ".conan"
    install_root.mkdir(parents=True)
    (install_root / "marker").write_text("x")  # a real run wipes this tree
    run_recorder.responses["config home"] = type("R", (), {"stdout": str(tmp_path / "home"), "returncode": 0})()

    _run_conan(config, ctx)

    assert (install_root / "marker").exists()
    assert not (install_root / "conanbuildenv.sh").exists()
    assert any("conan install" in c for c in dry_lines(capsys, "+"))


# ---- zephyr provider ---------------------------------------------------------#
def test_zephyr_dry_run_does_not_reset_existing_workspace_under_force(make_context, which, tmp_path, capsys):
    # --force re-runs west update/configure but must not wipe an existing
    # .west/config (see ZephyrProvider._ensure_workspace) -- confirm the
    # --dry-run preview agrees: no rm/touch of it at all.
    config = {"zephyr": {"west-yml": "west.yml", "base": "zephyr-rtos"}}
    ctx = make_context(config=config, dry_run=True, force=True)
    (ctx.env_dir / "west.yml").write_text("manifest: {}\n")
    west_config = ctx.env_dir / ".west" / "config"
    west_config.parent.mkdir(parents=True)
    west_config.write_text("original\n")

    ctx.config = config
    config["zephyr"] = ZephyrProvider.resolve_defaults(ctx, config["zephyr"], config)
    provider = ZephyrProvider(config)
    provider.stage = "zephyr"
    ctx.stage_id = "zephyr"
    provider.setup(ctx)

    assert west_config.read_text() == "original\n"  # neither removed nor truncated
    reported = dry_lines(capsys, "~")
    assert f"rm {west_config}" not in reported
    assert f"touch {west_config}" not in reported


# ---- docker provider ---------------------------------------------------------#
def test_docker_dry_run_previews_without_docker_on_path(make_context, run_recorder, which, capsys):
    from denver_providers.docker import DockerProvider

    which["docker"] = None
    config = {"docker": {"compose": {"image": "img:dev", "file": "docker-compose.yml"}}}
    ctx = make_context(config=config, dry_run=True)
    (ctx.env_dir / "docker-compose.yml").write_text("services: {}\n")
    # the local-cache probe is a query, so it really runs: miss it, so the
    # build this test is about is the branch actually taken
    run_recorder.responses["image inspect"] = type("R", (), {"stdout": "", "returncode": 1})()

    ctx.config = config
    config["docker"] = DockerProvider.resolve_defaults(ctx, config["docker"], config)
    provider = DockerProvider(config)
    provider.stage = "docker"
    ctx.stage_id = "docker"
    provider.setup(ctx)

    assert any("compose" in c and "build" in c for c in dry_lines(capsys, "+"))


# ---- end-to-end through run_stages() -----------------------------------------#
def _env(tmp_path, config):
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    cfg_path = env_dir / "denver.yml"
    cfg_path.write_text(yaml.safe_dump(config))
    return env_dir, cfg_path


def test_run_stages_dry_run_prints_legend_and_never_execs(tmp_path, exec_recorder, monkeypatch, capsys):
    config = {
        "stages": ["hello"],
        "hello": {"provider": "custom", "cmd": "echo hello"},
    }
    env_dir, cfg_path = _env(tmp_path, config)
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(dry_run=True))

    err = capsys.readouterr().err
    assert "no command below is executed for its effect" in err
    # shlex-quoted -- 'echo hello' is one single argv element ('bash -c'
    # <script>), not two bare words, so the printed line has to show that
    assert f"{_dry_tag('+')} bash -c 'echo hello'" in err
    assert f"{_dry_tag('+')} exec: echo hi" in err
    assert "NOT started (--dry-run)" in err
    assert exec_recorder == {}


def test_run_stages_dry_run_records_no_performance_trace(tmp_path, exec_recorder, monkeypatch):
    # the durations here are of printing commands, not of running them --
    # recording them would poison the very timings the file exists for.
    config = {"stages": ["hello"], "hello": {"provider": "custom", "cmd": "echo hello"}}
    env_dir, cfg_path = _env(tmp_path, config)
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(dry_run=True))
    assert not (tmp_path / "env" / ".denver" / "denver" / "performance.jsonl").exists()

    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    events = (tmp_path / "env" / ".denver" / "denver" / "performance.jsonl").read_text().splitlines()
    assert [json.loads(e) for e in events if json.loads(e).get("ph") == "X"]


def test_run_stages_dry_run_says_wrapper_relocated_stages_are_not_previewed(tmp_path, exec_recorder, monkeypatch):
    # relocating into the wrapper is itself one of the commands not being
    # run, so those stages are never reached -- said out loud rather than
    # left as a silently short pipeline.

    class Wrap(providers.base.Provider):
        name = "fakewrap"
        kind = "wrapper"

        def wrap(self, ctx, cmd):
            return ["WRAPPED", *cmd]

    class Setup(providers.base.Provider):
        name = "fakesetup"

    monkeypatch.setitem(providers.PROVIDERS, "fakewrap", Wrap)
    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", Setup)

    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap"},
        "fakesetup": {"provider": "fakesetup"},
    }
    env_dir, cfg_path = _env(tmp_path, config)
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(dry_run=True))

    assert exec_recorder == {}


def test_run_stages_dry_run_wrapper_note_names_the_skip_that_would_show_them(
    tmp_path, exec_recorder, monkeypatch, capsys
):

    class Wrap(providers.base.Provider):
        name = "fakewrap"
        kind = "wrapper"

    class Setup(providers.base.Provider):
        name = "fakesetup"

    monkeypatch.setitem(providers.PROVIDERS, "fakewrap", Wrap)
    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", Setup)
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap"},
        "fakesetup": {"provider": "fakesetup"},
    }
    env_dir, cfg_path = _env(tmp_path, config)
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(dry_run=True))

    note = dry_lines(capsys, "!")
    assert len(note) == 1
    assert "fakesetup" in note[0]
    assert "--skip fakewrap" in note[0]


# ---- --scripts <name> ----------------------------------------------------#
def test_run_named_scripts_dry_run_prints_scripts_instead_of_running_them(
    tmp_path, run_recorder, exec_recorder, monkeypatch, capsys
):
    config = {
        "stages": ["hello"],
        "hello": {"provider": "custom", "cmd": "echo hello", "scripts": {"setup": ["setup.sh"]}},
    }
    env_dir, cfg_path = _env(tmp_path, config)
    (env_dir / "setup.sh").write_text("#!/bin/sh\ntouch $PWD/should-not-exist\n")

    denver.run_named_scripts(env_dir, config, cfg_path, ["setup"], dry_run=True)

    assert run_recorder.calls == []
    assert dry_lines(capsys, "+") == [str(env_dir / "setup.sh")]


def test_run_named_scripts_dry_run_says_wrapper_relocated_scripts_are_not_previewed(
    tmp_path, run_recorder, exec_recorder, monkeypatch, capsys
):

    class Wrap(providers.base.Provider):
        name = "fakewrap"
        kind = "wrapper"

    monkeypatch.setitem(providers.PROVIDERS, "fakewrap", Wrap)
    config = {
        "stages": ["fakewrap", "hello"],
        "fakewrap": {"provider": "fakewrap"},
        "hello": {"provider": "custom", "cmd": "echo hello", "scripts": {"setup": ["setup.sh"]}},
    }
    env_dir, cfg_path = _env(tmp_path, config)
    (env_dir / "setup.sh").write_text("#!/bin/sh\n")

    denver.run_named_scripts(env_dir, config, cfg_path, ["setup"], dry_run=True)

    note = dry_lines(capsys, "!")
    assert len(note) == 1
    assert "'setup' scripts" in note[0]
    assert "--skip fakewrap" in note[0]
    assert exec_recorder == {}


# ---- CLI ---------------------------------------------------------------------#
def test_main_dry_run_flag_reaches_run_stages(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(denver, "run_stages", lambda *a, **kw: seen.update(kw))
    config = {"stages": ["hello"], "hello": {"provider": "custom", "cmd": "echo hello"}}
    env_dir, _ = _env(tmp_path, config)

    denver.main(["run", str(env_dir), "--dry-run", "--", "echo", "hi"])
    assert seen["options"].dry_run is True

    denver.main(["run", str(env_dir), "--", "echo", "hi"])
    assert seen["options"].dry_run is False


def test_main_dry_run_flag_reaches_run_named_scripts(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(denver, "run_named_scripts", lambda *a, **kw: seen.update(kw))
    config = {"stages": ["hello"], "hello": {"provider": "custom", "cmd": "echo hello"}}
    env_dir, _ = _env(tmp_path, config)

    denver.main(["run", str(env_dir), "--scripts", "setup", "--dry-run"])
    assert seen["dry_run"] is True


def test_dry_run_flag_is_documented_in_help(capsys):
    # --dry-run is one of 'run''s own flags, not a top-level one -- see
    # 'denver run --help', not the bare top-level 'denver --help' (which
    # only lists the subcommands themselves, see print_help).
    denver.main(["run", "--help"])
    assert "--dry-run" in capsys.readouterr().out
