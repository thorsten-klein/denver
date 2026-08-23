"""Tests for denver.py orchestration: hooks, stacking, run_stages."""

import json
import re
import sys
from pathlib import Path

import pytest

import denver
import denver_providers as providers
from denver_providers.base import Provider


# ---- collect_import_dirs --------------------------------------------------#
def test_collect_import_dirs(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "denver.toml").write_text('stages = [\n  "uv",\n]\n')
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    cfg_path = env_dir / "denver.toml"
    cfg_path.write_text('import = [\n  "../base",\n]\n')
    assert denver.collect_import_dirs(cfg_path) == [base]


def test_collect_import_dirs_none(tmp_path):
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    cfg_path = env_dir / "denver.toml"
    cfg_path.write_text('stages = [\n  "uv",\n]\n')
    assert denver.collect_import_dirs(cfg_path) == []


def test_collect_import_dirs_multi_level_nearest_first(tmp_path):
    """A 3-level import chain (env -> mid -> base) yields all three ancestor
    dirs, nearest (most-derived) first -- so a conventional default file is
    found in 'mid' before falling through to 'base'."""
    base = tmp_path / "base"
    base.mkdir()
    (base / "denver.toml").write_text('stages = [\n  "uv",\n]\n')

    mid = tmp_path / "mid"
    mid.mkdir()
    (mid / "denver.toml").write_text('import = [\n  "../base",\n]\n')

    env_dir = tmp_path / "env"
    env_dir.mkdir()
    cfg_path = env_dir / "denver.toml"
    cfg_path.write_text('import = [\n  "../mid",\n]\n')

    assert denver.collect_import_dirs(cfg_path) == [mid, base]


def test_collect_import_dirs_circular_dies(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    (a / "denver.toml").write_text('import = [\n  "../b",\n]\n')
    b = tmp_path / "b"
    b.mkdir()
    (b / "denver.toml").write_text('import = [\n  "../a",\n]\n')

    with pytest.raises(SystemExit):
        denver.collect_import_dirs(a / "denver.toml")


# ---- run_hook / collect_hook_entries ----------------------------------------#
def _write_denver_toml(env_dir, content):
    (env_dir / "denver.toml").write_text(content)
    return env_dir / "denver.toml"


def test_run_hook_missing_key_noop(make_context):
    ctx = make_context()
    cfg_path = _write_denver_toml(ctx.env_dir, 'stages = ["uv"]\n')
    denver.run_hook(ctx, cfg_path, "pre-uv")  # no error


def test_run_hook_single_script(make_context):
    ctx = make_context()
    (ctx.env_dir / "hook.sh").write_text("export HOOKED=1\n")
    cfg_path = _write_denver_toml(ctx.env_dir, '[hooks]\npre-uv = "hook.sh"\n')
    denver.run_hook(ctx, cfg_path, "pre-uv")
    assert ctx.env["HOOKED"] == "1"


def test_run_hook_list_of_scripts(make_context):
    ctx = make_context()
    (ctx.env_dir / "a.sh").write_text("export A=1\n")
    (ctx.env_dir / "b.sh").write_text("export B=1\n")
    cfg_path = _write_denver_toml(ctx.env_dir, '[hooks]\npre-cmd = ["a.sh", "b.sh"]\n')
    denver.run_hook(ctx, cfg_path, "pre-cmd")
    assert ctx.env["A"] == "1"
    assert ctx.env["B"] == "1"


def test_run_hook_missing_script_dies(make_context):
    ctx = make_context()
    cfg_path = _write_denver_toml(ctx.env_dir, '[hooks]\npre-uv = "nope.sh"\n')
    with pytest.raises(SystemExit):
        denver.run_hook(ctx, cfg_path, "pre-uv")


def test_run_hook_unconfigured_script_is_never_discovered(make_context):
    # hooks/env.sh is the conventional name, and it is sitting right next to
    # the denver.toml -- but nothing lists it, so it is not sourced.
    ctx = make_context()
    (ctx.env_dir / "hooks").mkdir()
    (ctx.env_dir / "hooks" / "env.sh").write_text("export CONVENTIONAL=1\n")
    cfg_path = _write_denver_toml(ctx.env_dir, 'stages = ["uv"]\n')  # no hooks: at all
    denver.run_hook(ctx, cfg_path, "env")
    assert "CONVENTIONAL" not in ctx.env


def test_run_hook_nothing_configured_is_a_no_op(make_context):
    ctx = make_context()
    cfg_path = _write_denver_toml(ctx.env_dir, 'stages = ["uv"]\n')
    denver.run_hook(ctx, cfg_path, "env")  # no error, nothing to source
    assert "CONVENTIONAL" not in ctx.env


def test_run_hook_list_is_sourced_in_order(make_context):
    # 'hooks: env:' is a list: every entry is sourced, in the declared order,
    # so a later script wins over an earlier one.
    ctx = make_context()
    (ctx.env_dir / "hooks").mkdir()
    (ctx.env_dir / "hooks" / "env.sh").write_text("export BASE=1\nexport SHARED=from-base\n")
    (ctx.env_dir / "hooks" / "env.user.sh").write_text("export USER_ONLY=1\nexport SHARED=from-user\n")
    cfg_path = _write_denver_toml(
        ctx.env_dir,
        '[hooks]\nenv = ["hooks/env.sh", "hooks/env.user.sh"]\n',
    )
    denver.run_hook(ctx, cfg_path, "env")
    assert ctx.env["BASE"] == "1"
    assert ctx.env["USER_ONLY"] == "1"
    assert ctx.env["SHARED"] == "from-user"  # last entry sourced last, so it wins


def test_run_hook_only_configured_script_runs(make_context):
    ctx = make_context()
    (ctx.env_dir / "hooks").mkdir()
    (ctx.env_dir / "hooks" / "env.sh").write_text("export CONVENTIONAL=1\n")
    (ctx.env_dir / "explicit.sh").write_text("export EXPLICIT=1\n")
    cfg_path = _write_denver_toml(ctx.env_dir, '[hooks]\nenv = "explicit.sh"\n')
    denver.run_hook(ctx, cfg_path, "env")
    assert ctx.env["EXPLICIT"] == "1"
    assert "CONVENTIONAL" not in ctx.env


def test_run_hook_stacked_base_runs_before_derived(tmp_path, make_context):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "denver.toml").write_text('[hooks]\nenv = "base.sh"\n')
    (base_dir / "base.sh").write_text("export ORDER=${ORDER}base\nexport FROM_BASE=1\n")

    env_dir = tmp_path / "derived"
    env_dir.mkdir()
    (env_dir / "derived.sh").write_text("export ORDER=${ORDER}derived\nexport FROM_DERIVED=1\n")
    cfg_path = env_dir / "denver.toml"
    cfg_path.write_text('import = [\n  "../base",\n]\n\n[hooks]\nenv = "derived.sh"\n')

    ctx = make_context(env_dir=env_dir)
    denver.run_hook(ctx, cfg_path, "env")
    assert ctx.env["FROM_BASE"] == "1"
    assert ctx.env["FROM_DERIVED"] == "1"
    assert ctx.env["ORDER"] == "basederived"  # base-first


def test_run_hook_stacked_lists_from_both_layers(tmp_path, make_context):
    # each layer declares its own 'hooks: env:' list; both are sourced,
    # base-first, and each entry resolves against its own layer's dir.
    base_dir = tmp_path / "base"
    (base_dir / "hooks").mkdir(parents=True)
    (base_dir / "denver.toml").write_text('stages = [\n  "uv",\n]\n\n[hooks]\nenv = [\n  "hooks/env.sh",\n]\n')
    (base_dir / "hooks" / "env.sh").write_text("export FROM_BASE=1\n")

    env_dir = tmp_path / "derived"
    (env_dir / "hooks").mkdir(parents=True)
    (env_dir / "hooks" / "env.sh").write_text("export FROM_DERIVED=1\n")
    cfg_path = env_dir / "denver.toml"
    cfg_path.write_text('import = [\n  "../base",\n]\n\n[hooks]\nenv = [\n  "hooks/env.sh",\n]\n')

    ctx = make_context(env_dir=env_dir)
    denver.run_hook(ctx, cfg_path, "env")
    assert ctx.env["FROM_BASE"] == "1"
    assert ctx.env["FROM_DERIVED"] == "1"


def test_collect_hook_entries_circular_import_dies(tmp_path):
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    (a_dir / "denver.toml").write_text('import = [\n  "../b",\n]\n')
    (b_dir / "denver.toml").write_text('import = [\n  "../a",\n]\n')
    with pytest.raises(SystemExit):
        denver.collect_hook_entries(a_dir / "denver.toml", "env")


# ---- expand_section_imports ------------------------------------------------#
def test_expand_section_imports_stacks_and_overrides(tmp_path):
    src_env = tmp_path / "src"
    src_env.mkdir()
    (src_env / "denver.toml").write_text('[docker]\nexe = "docker"\nservice = "dev"\n')

    env_dir = tmp_path / "env"
    env_dir.mkdir()
    # 'service' conflicts with the stacked-in value, so overriding it deliberately
    # needs '!' -- see test_denver_config.py's deep_merge conflict tests
    config = {"docker": {"import": ["../src"], "service": "!override"}}
    expanded, extra_dirs = denver.expand_section_imports(config, env_dir)
    assert expanded["docker"] == {"exe": "docker", "service": "override"}
    assert extra_dirs == [src_env]


def test_expand_section_imports_direct_file_ref(tmp_path):
    src_env = tmp_path / "src"
    src_env.mkdir()
    (src_env / "denver.toml").write_text('[docker]\nexe = "docker"\n')

    env_dir = tmp_path / "env"
    env_dir.mkdir()
    config = {"docker": {"import": ["../src/denver.toml"]}}
    expanded, extra_dirs = denver.expand_section_imports(config, env_dir)
    assert expanded["docker"] == {"exe": "docker"}
    assert extra_dirs == [src_env]


def test_expand_section_imports_explicit_section_ref(tmp_path):
    src_env = tmp_path / "src"
    src_env.mkdir()
    (src_env / "denver.toml").write_text('[conan]\nbase-classes = [\n  "conan/base_classes",\n]\n')

    env_dir = tmp_path / "env"
    env_dir.mkdir()
    config = {"conan": {"import": ["../src/denver.toml:conan"]}}
    expanded, extra_dirs = denver.expand_section_imports(config, env_dir)
    assert expanded["conan"] == {"base-classes": ["conan/base_classes"]}
    assert extra_dirs == [src_env]


def test_expand_section_imports_no_imports_passthrough(tmp_path):
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    config = {"uv": {"python": "3.9"}, "stages": ["uv"]}
    expanded, extra_dirs = denver.expand_section_imports(config, env_dir)
    assert expanded == config
    assert extra_dirs == []


# ---- default_command --------------------------------------------------------#
def test_default_command_non_tty_dies(monkeypatch):
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit):
        denver.default_command({})


@pytest.mark.parametrize(
    "config, env, check",
    [
        pytest.param({"command": "fish"}, {}, lambda cmd: cmd == ["fish"], id="command-string"),
        pytest.param({"command": ["zsh", "-l"]}, {}, lambda cmd: cmd == ["zsh", "-l"], id="command-list-form"),
        pytest.param({}, {"SHELL": "/usr/bin/zsh"}, lambda cmd: cmd == ["/usr/bin/zsh"], id="uses-shell-env-var"),
        pytest.param({}, {"SHELL": None}, lambda cmd: cmd == ["bash"], id="falls-back-to-bash-without-shell-env-var"),
        pytest.param(
            {"docker": {"compose": {"default-cmd": "bash"}}},
            {},
            lambda cmd: cmd == ["bash"],
            id="uses-docker-default-cmd",
        ),
        pytest.param(
            {"command": "zsh", "docker": {"compose": {"default-cmd": "bash"}}},
            {},
            lambda cmd: cmd == ["zsh"],
            id="top-level-wins-over-docker-default-cmd",
        ),
    ],
)
def test_default_command_variants(monkeypatch, config, env, check):
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: True)
    for key, val in env.items():
        if val is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, val)
    cmd = denver.default_command(config)
    assert check(cmd)


# ---- resolve_command ---------------------------------------------------------#
def test_resolve_command_returns_forwarded_verbatim():
    # main() already splits argv on the first literal '--' before any denver
    # flag is parsed, so 'forwarded' reaching here never carries that marker.
    assert denver.resolve_command({}, ["echo", "hi"]) == ["echo", "hi"]


def test_resolve_command_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: True)
    assert denver.resolve_command({"command": "zsh"}, []) == ["zsh"]


def test_resolve_command_in_container_default_is_false_leaves_cmd_untouched(monkeypatch):
    # in_container defaults to False -- a plain host run, unaffected by the
    # container-only completion wiring covered below.
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: True)
    assert denver.resolve_command({"command": "zsh"}, []) == ["zsh"]


def test_resolve_command_in_container_never_wraps_an_explicit_forwarded_command(monkeypatch):
    # an explicit '-- command' is the user's own, verbatim -- never rewritten,
    # in_container or not.
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: True)
    assert denver.resolve_command({}, ["bash", "-c", "true"], in_container=True) == ["bash", "-c", "true"]


# ---- default_command / _completion_wrapped_shell -- in-container completion -#
@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_default_command_in_container_wires_completion_for_a_known_shell(monkeypatch, shell):
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: True)
    cmd = denver.default_command({"command": shell}, in_container=True)
    assert cmd[0] == shell
    assert cmd[1] == "-c"
    assert f"complete {shell}" in cmd[2] or "complete fish" in cmd[2]
    assert cmd[2].endswith(f"exec {shell} -i")


def test_default_command_in_container_preserves_extra_args_on_the_final_exec(monkeypatch):
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: True)
    cmd = denver.default_command({"command": ["zsh", "-l"]}, in_container=True)
    assert cmd == ["zsh", "-c", cmd[2]]
    assert cmd[2].endswith("exec zsh -i -l")


def test_default_command_in_container_leaves_an_unrecognised_command_untouched(monkeypatch):
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: True)
    assert denver.default_command({"command": "python3"}, in_container=True) == ["python3"]
    assert denver.default_command({"command": "echo hi"}, in_container=True) == ["echo hi"]


def test_default_command_not_in_container_never_wraps(monkeypatch):
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: True)
    assert denver.default_command({"command": "fish"}, in_container=False) == ["fish"]
    assert denver.default_command({"command": "fish"}) == ["fish"]  # in_container defaults to False


def test_completion_wrapped_shell_uses_the_shell_basename_of_a_path(monkeypatch):
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: True)
    cmd = denver.default_command({"command": "/usr/bin/zsh"}, in_container=True)
    # the actual binary invoked is still the full path given -- only the
    # completion-script lookup ('denver complete <shell>') uses its basename.
    assert cmd[0] == "/usr/bin/zsh"
    assert "complete zsh" in cmd[2]
    assert cmd[2].endswith("exec /usr/bin/zsh -i")


# ---- reinvoke_command -------------------------------------------------------#
def test_reinvoke_command(tmp_path):
    config_path = tmp_path / "e" / "denver.toml"
    cmd = denver.reinvoke_command(config_path, ["echo", "hi"], ["docker"])
    assert cmd[0] == "python3"
    # this file's own path, independent of any per-run state directory --
    # correct whether denver runs from a checkout or an editable install
    # (see docstring)
    assert cmd[1] == str(Path(denver.__file__).resolve())
    assert cmd[2] == "run"
    assert cmd[3] == str(config_path)
    # the active wrapper stage(s) are skipped, so the inner denver doesn't
    # try to relocate into them again
    assert cmd[cmd.index("--skip") + 1] == "docker"
    assert "-q" not in cmd
    # 'forwarded' is re-introduced behind a fresh '--', so the re-invoked
    # denver's own argv splitting separates it from denver's own flags again
    assert cmd[-3:] == ["--", "echo", "hi"]


def test_reinvoke_command_frozen_reinvokes_the_executable_itself(tmp_path, monkeypatch):
    # a frozen single-file build has no denver.py to hand to an interpreter --
    # __file__ then points into PyInstaller's extraction dir, at a path that is
    # never actually written, and the container's python3 would not have
    # denver's dependencies anyway. The executable re-runs itself instead.
    exe = tmp_path / "bin" / "denver"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    config_path = tmp_path / "e" / "denver.toml"

    cmd = denver.reinvoke_command(config_path, ["echo", "hi"], ["docker"])

    assert cmd[0] == str(exe.resolve())
    assert "python3" not in cmd
    assert str(Path(denver.__file__).resolve()) not in cmd
    # everything after the launcher is unchanged
    assert cmd[1] == "run"
    assert cmd[2] == str(config_path)
    assert cmd[cmd.index("--skip") + 1] == "docker"
    assert cmd[-3:] == ["--", "echo", "hi"]


def test_reinvoke_command_no_forwarded_command_omits_separator(tmp_path):
    # with nothing to forward, no '--' is added either -- there's nothing for
    # it to separate, and the re-invoked denver would otherwise see a
    # trailing '--' as (the start of) an empty command instead of none at all
    config_path = tmp_path / "e" / "denver.toml"
    cmd = denver.reinvoke_command(config_path, [], ["docker"])
    assert "--" not in cmd


def test_reinvoke_command_skips_every_wrapper_stage(tmp_path):
    config_path = tmp_path / "e" / "denver.toml"
    cmd = denver.reinvoke_command(config_path, ["echo", "hi"], ["docker", "docker2"])
    skip_positions = [i for i, tok in enumerate(cmd) if tok == "--skip"]
    assert [cmd[i + 1] for i in skip_positions] == ["docker", "docker2"]


def test_reinvoke_command_forwards_quiet(tmp_path):
    config_path = tmp_path / "e" / "denver.toml"
    cmd = denver.reinvoke_command(config_path, ["echo", "hi"], ["docker"], options=denver.RunOptions(quiet=True))
    # -q must reach the re-invoked (in-container) denver -- it was already
    # stripped out of the forwarded args by the outer main()'s own parsing.
    assert "-q" in cmd
    assert cmd[-2:] == ["echo", "hi"]


def test_reinvoke_command_forwards_original_until_and_skip(tmp_path):
    # the user's own --until/--skip (distinct from wrapper_stage_ids, the
    # wrapper's own forced --skip) must reach the inner denver too, or a
    # stage the user asked to skip runs anyway once relocated into the
    # wrapper -- the inner denver has no other way to know about them.
    config_path = tmp_path / "e" / "denver.toml"
    cmd = denver.reinvoke_command(
        config_path,
        ["echo", "hi"],
        ["docker"],
        options=denver.RunOptions(until_stage="conan", skip_stages=["uv-zephyr"]),
    )
    assert cmd[cmd.index("--until") + 1] == "conan"
    skip_positions = [i for i, tok in enumerate(cmd) if tok == "--skip"]
    assert [cmd[i + 1] for i in skip_positions] == ["uv-zephyr", "docker"]


def test_reinvoke_command_forwards_fast(tmp_path):
    config_path = tmp_path / "e" / "denver.toml"
    cmd = denver.reinvoke_command(config_path, ["echo", "hi"], ["docker"], options=denver.RunOptions(fast=True))
    # --fast must reach the re-invoked (in-container) denver too, for the
    # same reason as -q above -- it's what actually skips uv/conan/zephyr's
    # build steps, which run inside the container, not on the host.
    assert "--fast" in cmd
    assert cmd[-2:] == ["echo", "hi"]


def test_reinvoke_command_preserves_custom_config_filename(tmp_path):
    # a folder may hold several denver.xxx.yml variants; the wrapper
    # re-invocation must point the inner denver at the exact same file, not
    # just the directory (which would silently fall back to denver.toml).
    config_path = tmp_path / "e" / "denver.debug.yml"
    cmd = denver.reinvoke_command(config_path, ["echo", "hi"], ["docker"])
    assert cmd[2] == "run"
    assert cmd[3] == str(config_path)


# ---- run_stages -------------------------------------------------------------#
class RecordingSetup(Provider):
    name = "fakesetup"
    kind = "setup"

    def setup(self, ctx):
        ctx.set(f"RAN_{self.stage.upper()}", "1")


class RecordingWrapper(Provider):
    name = "fakewrap"
    kind = "wrapper"
    KEYS = ("marker",)  # arbitrary key used by the section-stacking tests below

    def setup(self, ctx):
        ctx.set("WRAP_SETUP", "1")

    def wrap(self, ctx, cmd):
        return ["WRAPPED", *cmd]


class RecordingDockerWrapper(Provider):
    """A minimal wrapper provider actually *named* 'docker' -- only that .name matters here: it's what
    _wrapper_target_cmd's in_container check keys off of (see test_run_stages_pure_docker_wrapper_wires_completion_below)."""

    name = "docker"
    kind = "wrapper"

    def setup(self, ctx):
        pass

    def wrap(self, ctx, cmd):
        return cmd


@pytest.fixture
def fake_providers(monkeypatch):
    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", RecordingSetup)
    monkeypatch.setitem(providers.PROVIDERS, "fakewrap", RecordingWrapper)
    monkeypatch.setitem(providers.PROVIDERS, "fakedocker", RecordingDockerWrapper)


def _env(tmp_path, config):
    """Create an env dir with a denver.toml written from ``config``.

    collect_import_dirs() re-reads the file from disk (to find 'import:'), so
    the on-disk file must reflect the config passed to run_stages().
    """
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    cfg_path = env_dir / "denver.toml"
    cfg_path.write_text(denver.dump_toml(config))
    return env_dir, cfg_path


def test_run_stages_no_stages_dies(tmp_path):
    env_dir, cfg_path = _env(tmp_path, {})
    with pytest.raises(SystemExit):
        denver.run_stages(env_dir, {}, cfg_path, [])


def test_run_stages_setup_only(tmp_path, fake_providers, exec_recorder, monkeypatch):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {"stages": ["fakesetup"], "fakesetup": {"provider": "fakesetup"}, "command": "myshell"}
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: True)
    denver.run_stages(env_dir, config, cfg_path, [])
    assert exec_recorder["args"] == ["myshell"]


def test_run_stages_with_hooks(tmp_path, fake_providers, exec_recorder):
    config = {
        "stages": ["fakesetup"],
        "fakesetup": {"provider": "fakesetup"},
        "hooks": {
            "env": "env.sh",
            "pre-fakesetup": "pre.sh",
            "post-fakesetup": "post.sh",
            "pre-cmd": "precmd.sh",
        },
    }
    env_dir, cfg_path = _env(tmp_path, config)
    (env_dir / "env.sh").write_text("export ENV_HOOK=1\n")
    (env_dir / "pre.sh").write_text("export PRE_HOOK=1\n")
    (env_dir / "post.sh").write_text("export POST_HOOK=1\n")
    (env_dir / "precmd.sh").write_text("export PRECMD_HOOK=1\n")
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    assert exec_recorder["env"]["ENV_HOOK"] == "1"
    assert exec_recorder["env"]["PRE_HOOK"] == "1"
    assert exec_recorder["env"]["POST_HOOK"] == "1"
    assert exec_recorder["env"]["PRECMD_HOOK"] == "1"
    assert exec_recorder["env"]["RAN_FAKESETUP"] == "1"


def test_run_stages_env_hook_runs_before_declarative_env(tmp_path, fake_providers, exec_recorder):
    config = {
        "stages": ["fakesetup"],
        "fakesetup": {"provider": "fakesetup"},
        "hooks": {"env": "env.sh"},
        "env": {"SOME_VAR": "from-declarative"},
    }
    env_dir, cfg_path = _env(tmp_path, config)
    (env_dir / "env.sh").write_text("export SOME_VAR=from-hook\n")
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    # declarative 'env:' overrides whatever the hook set, since it runs after
    assert exec_recorder["env"]["SOME_VAR"] == "from-declarative"


def test_run_stages_wrapper_active_relocates_reinvocation(tmp_path, fake_providers, exec_recorder):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap"},
        "fakesetup": {"provider": "fakesetup"},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    # wrapper's setup ran (host side), then reinvoke command was wrapped
    assert exec_recorder["env"]["WRAP_SETUP"] == "1"
    assert exec_recorder["args"][0] == "WRAPPED"
    assert "python3" in exec_recorder["args"]
    # the reinvocation skips the wrapper stage itself, so the inner denver
    # doesn't try to relocate into it again
    args = exec_recorder["args"]
    assert args[args.index("--skip") + 1] == "fakewrap"
    # setup provider did NOT run on the host (it runs inside the wrapper)
    assert "RAN_FAKESETUP" not in exec_recorder["env"]


def test_run_stages_wrapper_reinvocation_forwards_skip_stages(tmp_path, fake_providers, exec_recorder):
    # a setup stage the user --skip'd on the host must stay skipped once
    # relocated into the wrapper too -- the inner denver recomputes its own
    # 'stages:' from scratch and has no memory of the original --skip unless
    # it's re-passed explicitly (see reinvoke_command's own docstring).
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakewrap", "fakesetup", "fakesetup2"],
        "fakewrap": {"provider": "fakewrap"},
        "fakesetup": {"provider": "fakesetup"},
        "fakesetup2": {"provider": "fakesetup"},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(skip_stages=["fakesetup2"]))
    args = exec_recorder["args"]
    skip_positions = [i for i, tok in enumerate(args) if tok == "--skip"]
    assert [args[i + 1] for i in skip_positions] == ["fakesetup2", "fakewrap"]


def test_run_stages_numbers_by_full_declared_stage_list(tmp_path, exec_recorder, capsys):
    # banner()'s '[i/n]' counts every declared stage (3 here), not just the
    # ones actually running -- both a real stage and one --skip'd out keep
    # their own declared position instead of the total silently shrinking.
    class Fake(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            from denver_providers.context import banner

            banner(ctx, self.stage, "run")

    import denver_providers as providers_module

    providers_module.PROVIDERS["fakesetup"] = Fake
    try:
        env_dir, cfg_path = _env(tmp_path, {})
        config = {
            "stages": ["fakesetup", "fakesetup2", "fakesetup3"],
            "fakesetup": {"provider": "fakesetup"},
            "fakesetup2": {"provider": "fakesetup"},
            "fakesetup3": {"provider": "fakesetup"},
        }
        denver.run_stages(
            env_dir,
            config,
            cfg_path,
            ["echo", "hi"],
            options=denver.RunOptions(skip_stages=["fakesetup2"], verbose=True),
        )
    finally:
        del providers_module.PROVIDERS["fakesetup"]
    err = capsys.readouterr().err
    assert "[1/3] fakesetup - run" in err
    assert "[2/3] stage 'fakesetup2' skipped by --skip" in err
    assert "[3/3] fakesetup3 - run" in err


def test_run_stages_prints_a_stage_summary(tmp_path, fake_providers, exec_recorder, capsys):
    # the per-stage "finished in Ns" lines are scattered through a run's
    # output, often thousands of lines of build noise apart -- restated as one
    # block right before the command launches. --verbose only, see
    # _print_stage_summary.
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["one", "two"],
        "one": {"provider": "fakesetup"},
        "two": {"provider": "fakesetup"},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(verbose=True))
    err = capsys.readouterr().err
    assert re.search(r"one\s+\d+\.\d+s", err)
    assert re.search(r"two\s+\d+\.\d+s", err)


def test_run_stages_summary_keeps_a_row_for_a_skipped_stage(tmp_path, fake_providers, exec_recorder, capsys):
    # a stage that did not run keeps its row, carrying the reason instead of
    # a duration, so the summary matches the '[i/n]' trail above it
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["one", "two"],
        "one": {"provider": "fakesetup"},
        "two": {"provider": "fakesetup"},
    }
    denver.run_stages(
        env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(skip_stages=["two"], verbose=True)
    )
    assert re.search(r"two\s+skipped by --skip", capsys.readouterr().err)


def test_stage_summary_prints_nothing_without_stages(make_context, capsys):
    # a Context driven directly (a provider under test, say) has no pipeline
    # behind it; the summary must not assume one
    denver._print_stage_summary(make_context())
    assert capsys.readouterr().err == ""


def test_run_stages_summary_silent_at_quiet_level_2(tmp_path, fake_providers, exec_recorder, capsys):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {"stages": ["one"], "one": {"provider": "fakesetup"}}
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(quiet=2))
    assert "one" not in capsys.readouterr().err


def test_run_stages_shows_skipped_stages_in_pipeline_order(tmp_path, exec_recorder, capsys):
    # the trail must read as the pipeline it describes: a skipped stage
    # reports in its own position, not batched after every stage that ran.
    class Fake(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            from denver_providers.context import banner

            banner(ctx, self.stage, "run")

    import denver_providers as providers_module

    providers_module.PROVIDERS["fakesetup"] = Fake
    try:
        env_dir, cfg_path = _env(tmp_path, {})
        config = {
            "stages": ["one", "two", "three", "four"],
            "one": {"provider": "fakesetup"},
            "two": {"provider": "fakesetup"},
            "three": {"provider": "fakesetup"},
            "four": {"provider": "fakesetup"},
        }
        denver.run_stages(
            env_dir,
            config,
            cfg_path,
            ["echo", "hi"],
            options=denver.RunOptions(skip_stages=["two", "three"], verbose=True),
        )
    finally:
        del providers_module.PROVIDERS["fakesetup"]
    err = capsys.readouterr().err
    positions = [
        err.index("[1/4] one - run"),
        err.index("[2/4] stage 'two' skipped by --skip"),
        err.index("[3/4] stage 'three' skipped by --skip"),
        err.index("[4/4] four - run"),
    ]
    assert positions == sorted(positions)


def test_run_stages_shows_earlier_skips_before_a_stage_that_dies(tmp_path, exec_recorder, capsys):
    # the moment the skip lines explain the most is when a later stage fails:
    # reporting them only after every stage ran lost them entirely.
    class Dies(Provider):
        name = "fakedies"
        kind = "setup"

        def setup(self, ctx):
            from denver_providers.context import die

            die("boom")

    import denver_providers as providers_module

    providers_module.PROVIDERS["fakedies"] = Dies
    try:
        env_dir, cfg_path = _env(tmp_path, {})
        config = {
            "stages": ["skipped-one", "boomer"],
            "skipped-one": {"provider": "fakedies"},
            "boomer": {"provider": "fakedies"},
        }
        options = denver.RunOptions(skip_stages=["skipped-one"])
        with pytest.raises(SystemExit):
            denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=options)
    finally:
        del providers_module.PROVIDERS["fakedies"]
    err = capsys.readouterr().err
    assert err.index("[1/2] stage 'skipped-one' skipped by --skip") < err.index("[2/2] stage 'boomer' (fakedies)")


def test_run_stages_announces_each_stage_before_its_provider_runs(tmp_path, exec_recorder, capsys):
    # providers that check for their tool before their first banner() call
    # would otherwise fail with nothing on screen naming the stage -- and the
    # stage id is exactly what --skip takes.
    class Silent(Provider):
        name = "fakesilent"
        kind = "setup"

    import denver_providers as providers_module

    providers_module.PROVIDERS["fakesilent"] = Silent
    try:
        env_dir, cfg_path = _env(tmp_path, {})
        config = {"stages": ["quiet-stage"], "quiet-stage": {"provider": "fakesilent"}}
        denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    finally:
        del providers_module.PROVIDERS["fakesilent"]
    assert "[1/1] stage 'quiet-stage' (fakesilent)" in capsys.readouterr().err


def test_run_stages_shows_skipped_by_until_reason(tmp_path, fake_providers, exec_recorder, capsys):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakesetup", "fakesetup2"],
        "fakesetup": {"provider": "fakesetup"},
        "fakesetup2": {"provider": "fakesetup"},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(until_stage="fakesetup"))
    err = capsys.readouterr().err
    assert "[2/2] stage 'fakesetup2' skipped by --until" in err


def test_run_stages_disabled_stage_does_not_run(tmp_path, fake_providers, exec_recorder):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakesetup", "fakesetup2"],
        "fakesetup": {"provider": "fakesetup"},
        "fakesetup2": {"provider": "fakesetup", "disabled": True},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    assert exec_recorder["env"]["RAN_FAKESETUP"] == "1"
    assert "RAN_FAKESETUP2" not in exec_recorder["env"]


def test_run_stages_shows_skipped_disabled_reason(tmp_path, fake_providers, exec_recorder, capsys):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakesetup", "fakesetup2"],
        "fakesetup": {"provider": "fakesetup"},
        "fakesetup2": {"provider": "fakesetup", "disabled": True},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    err = capsys.readouterr().err
    assert "[2/2] stage 'fakesetup2' skipped (disabled: true)" in err


def test_run_stages_disabled_stage_enabled_via_config_override(tmp_path, fake_providers, exec_recorder):
    # what '-c fakesetup2.disabled=false' produces by the time run_stages
    # sees it (apply_config_overrides already ran in main())
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakesetup", "fakesetup2"],
        "fakesetup": {"provider": "fakesetup"},
        "fakesetup2": {"provider": "fakesetup", "disabled": False},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    assert exec_recorder["env"]["RAN_FAKESETUP2"] == "1"


def test_run_stages_disabled_wrapper_stage_stays_inactive(tmp_path, fake_providers, exec_recorder):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap", "disabled": True},
        "fakesetup": {"provider": "fakesetup"},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    # the wrapper never activated: fakesetup ran directly on the host, no relocation
    assert "WRAP_SETUP" not in exec_recorder["env"]
    assert exec_recorder["env"]["RAN_FAKESETUP"] == "1"
    assert exec_recorder["args"] == ["echo", "hi"]


def test_run_stages_disabled_and_skipped_together_shows_skip_reason(tmp_path, fake_providers, exec_recorder, capsys):
    # a stage --until/--skip already excludes never reaches the 'disabled'
    # branch of the reason check -- --skip's reason wins.
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakesetup", "fakesetup2"],
        "fakesetup": {"provider": "fakesetup"},
        "fakesetup2": {"provider": "fakesetup", "disabled": True},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(skip_stages=["fakesetup2"]))
    err = capsys.readouterr().err
    assert "[2/2] stage 'fakesetup2' skipped by --skip" in err


def _executable_script(path, body):
    """Write a real, executable shell script at ``path`` -- 'skip-on-*:' scripts are exec'd, not sourced."""
    path.write_text(body)
    path.chmod(0o755)


def test_run_stages_skip_on_success_skips_stage_when_script_exits_0(tmp_path, fake_providers, exec_recorder):
    env_dir, cfg_path = _env(tmp_path, {})
    _executable_script(env_dir / "check.sh", "#!/bin/sh\nexit 0\n")
    config = {
        "stages": ["fakesetup"],
        "fakesetup": {"provider": "fakesetup", "skip-on-success": ["check.sh"]},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    assert "RAN_FAKESETUP" not in exec_recorder["env"]


def test_run_stages_skip_on_success_runs_stage_when_script_exits_nonzero(tmp_path, fake_providers, exec_recorder):
    env_dir, cfg_path = _env(tmp_path, {})
    _executable_script(env_dir / "check.sh", "#!/bin/sh\nexit 1\n")
    config = {
        "stages": ["fakesetup"],
        "fakesetup": {"provider": "fakesetup", "skip-on-success": ["check.sh"]},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    assert exec_recorder["env"]["RAN_FAKESETUP"] == "1"


def test_run_stages_skip_on_failure_skips_stage_when_script_exits_1(tmp_path, fake_providers, exec_recorder):
    env_dir, cfg_path = _env(tmp_path, {})
    _executable_script(env_dir / "check.sh", "#!/bin/sh\nexit 1\n")
    config = {
        "stages": ["fakesetup"],
        "fakesetup": {"provider": "fakesetup", "skip-on-failure": ["check.sh"]},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    assert "RAN_FAKESETUP" not in exec_recorder["env"]


def test_run_stages_skip_on_failure_runs_stage_when_script_exits_0(tmp_path, fake_providers, exec_recorder):
    # skip-on-failure's condition is exit 1, not exit 0 -- a passing (exit 0)
    # script here means "not yet failed", the opposite of skip-on-success.
    env_dir, cfg_path = _env(tmp_path, {})
    _executable_script(env_dir / "check.sh", "#!/bin/sh\nexit 0\n")
    config = {
        "stages": ["fakesetup"],
        "fakesetup": {"provider": "fakesetup", "skip-on-failure": ["check.sh"]},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    assert exec_recorder["env"]["RAN_FAKESETUP"] == "1"


def test_run_stages_skip_on_success_and_skip_on_failure_are_independent(tmp_path, fake_providers, exec_recorder):
    # either group being fully satisfied is enough to skip, regardless of
    # what the other group's scripts say.
    env_dir, cfg_path = _env(tmp_path, {})
    _executable_script(env_dir / "zero.sh", "#!/bin/sh\nexit 0\n")
    _executable_script(env_dir / "one.sh", "#!/bin/sh\nexit 0\n")
    config = {
        "stages": ["fakesetup"],
        "fakesetup": {
            "provider": "fakesetup",
            "skip-on-success": ["zero.sh"],
            "skip-on-failure": ["one.sh"],
        },
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    assert "RAN_FAKESETUP" not in exec_recorder["env"]


def test_run_stages_force_bypasses_skip_on_success(tmp_path, fake_providers, exec_recorder):
    env_dir, cfg_path = _env(tmp_path, {})
    _executable_script(env_dir / "check.sh", "#!/bin/sh\nexit 0\n")
    config = {
        "stages": ["fakesetup"],
        "fakesetup": {"provider": "fakesetup", "skip-on-success": ["check.sh"]},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(force=True))
    assert exec_recorder["env"]["RAN_FAKESETUP"] == "1"


def test_run_stages_skip_on_success_missing_script_dies(tmp_path, fake_providers, exec_recorder):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakesetup"],
        "fakesetup": {"provider": "fakesetup", "skip-on-success": ["nope.sh"]},
    }
    with pytest.raises(SystemExit):
        denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])


def test_run_stages_skip_on_success_not_a_list_of_strings_dies(tmp_path, fake_providers, exec_recorder):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakesetup"],
        "fakesetup": {"provider": "fakesetup", "skip-on-success": "not-a-list"},
    }
    with pytest.raises(SystemExit):
        denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])


def test_run_stages_shows_skip_on_success_reason_banner(tmp_path, fake_providers, exec_recorder, caplog):
    caplog.set_level("INFO")
    env_dir, cfg_path = _env(tmp_path, {})
    _executable_script(env_dir / "check.sh", "#!/bin/sh\nexit 0\n")
    config = {
        "stages": ["fakesetup"],
        "fakesetup": {"provider": "fakesetup", "skip-on-success": ["check.sh"]},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    assert "skip-on-success scripts all exited 0" in caplog.text


def test_run_stages_wrapper_shows_skipped_wrapper_banner_on_host(tmp_path, fake_providers, exec_recorder, capsys):
    # a wrapper stage the user --skip'd (e.g. `--skip docker`, run directly
    # on the host) still gets counted in the total and its own skip banner,
    # same as a skipped setup stage.
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap"},
        "fakesetup": {"provider": "fakesetup"},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(skip_stages=["fakewrap"]))
    err = capsys.readouterr().err
    assert "[1/2] stage 'fakewrap' skipped by --skip" in err
    # the wrapper being skipped means fakesetup ran directly on the host
    assert exec_recorder["env"]["RAN_FAKESETUP"] == "1"


def test_run_stages_wrapper_reinvocation_forwards_quiet(tmp_path, fake_providers, exec_recorder):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap"},
        "fakesetup": {"provider": "fakesetup"},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(quiet=True))
    # -q must be re-passed to the in-container denver, or the reinvoked
    # process (which does the actual uv/conan/zephyr build) stays noisy.
    assert "-q" in exec_recorder["args"]


def test_run_stages_wrapper_reinvocation_forwards_fast(tmp_path, fake_providers, exec_recorder):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap"},
        "fakesetup": {"provider": "fakesetup"},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(fast=True))
    assert "--fast" in exec_recorder["args"]


def test_run_stages_pure_wrapper_relocates_command_directly(tmp_path, fake_providers, exec_recorder):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {"stages": ["fakewrap"], "fakewrap": {"provider": "fakewrap"}}
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    assert exec_recorder["args"] == ["WRAPPED", "echo", "hi"]


def test_run_stages_pure_wrapper_named_docker_wires_completion_into_the_default_shell(
    tmp_path, fake_providers, exec_recorder, monkeypatch
):
    # a wrapper whose .name is 'docker' (as opposed to test_run_stages_pure_
    # wrapper_relocates_command_directly's 'fakewrap') is what
    # _wrapper_target_cmd's in_container check keys off of -- so, with no
    # forwarded command, the resolved default shell comes back wired for
    # 'denver complete' (see _completion_wrapped_shell).
    env_dir, cfg_path = _env(tmp_path, {})
    config = {"stages": ["fakedocker"], "fakedocker": {"provider": "fakedocker"}, "command": "fish"}
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: True)
    denver.run_stages(env_dir, config, cfg_path, [])
    assert exec_recorder["args"][0] == "fish"
    assert exec_recorder["args"][1] == "-c"
    assert "complete fish" in exec_recorder["args"][2]
    assert exec_recorder["args"][2].endswith("exec fish -i")


def test_run_stages_pure_wrapper_not_named_docker_never_wires_completion(
    tmp_path, fake_providers, exec_recorder, monkeypatch
):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {"stages": ["fakewrap"], "fakewrap": {"provider": "fakewrap"}, "command": "fish"}
    monkeypatch.setattr(denver.sys.stdin, "isatty", lambda: True)
    denver.run_stages(env_dir, config, cfg_path, [])
    assert exec_recorder["args"] == ["WRAPPED", "fish"]


def test_run_stages_skip_wrapper_stage_runs_on_host(tmp_path, fake_providers, exec_recorder):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap"},
        "fakesetup": {"provider": "fakesetup"},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(skip_stages=["fakewrap"]))
    assert "WRAP_SETUP" not in exec_recorder["env"]
    assert exec_recorder["env"]["RAN_FAKESETUP"] == "1"
    assert exec_recorder["args"] == ["echo", "hi"]


# ---- --until / --skip stage filtering -------------------------------------#
def _multi_stage_config():
    return {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap"},
        "fakesetup": {"provider": "fakesetup"},
    }


def test_run_stages_until_keeps_named_stage_and_everything_before_it(tmp_path, fake_providers, exec_recorder):
    # --until names the *last* stage to run: it and every stage before it
    # still run. Here that keeps the preceding wrapper stage (which a plain
    # "run this stage alone" filter would have dropped), so the wrapper is
    # prepared and execution relocates into it, carrying the same --until on.
    env_dir, cfg_path = _env(tmp_path, _multi_stage_config())
    denver.run_stages(
        env_dir, _multi_stage_config(), cfg_path, ["echo", "hi"], options=denver.RunOptions(until_stage="fakesetup")
    )
    assert exec_recorder["env"]["WRAP_SETUP"] == "1"
    args = exec_recorder["args"]
    assert args[args.index("--until") + 1] == "fakesetup"


def test_run_stages_until_drops_stages_after_named_one(tmp_path, fake_providers, exec_recorder):
    config = {
        "stages": ["fakesetup", "fakesetup2"],
        "fakesetup": {"provider": "fakesetup"},
        "fakesetup2": {"provider": "fakesetup"},
    }
    env_dir, cfg_path = _env(tmp_path, config)
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(until_stage="fakesetup"))
    assert exec_recorder["env"]["RAN_FAKESETUP"] == "1"
    # the command still runs, in the partial environment those stages built
    assert exec_recorder["args"] == ["echo", "hi"]


def test_run_stages_skip_excludes_named_stage(tmp_path, fake_providers, exec_recorder):
    env_dir, cfg_path = _env(tmp_path, _multi_stage_config())
    denver.run_stages(
        env_dir, _multi_stage_config(), cfg_path, ["echo", "hi"], options=denver.RunOptions(skip_stages=["fakesetup"])
    )
    assert "RAN_FAKESETUP" not in exec_recorder["env"]


def test_run_stages_until_and_skip_filters_out_everything_dies(tmp_path, fake_providers):
    env_dir, cfg_path = _env(tmp_path, _multi_stage_config())
    config = _multi_stage_config()
    options = denver.RunOptions(until_stage="fakewrap", skip_stages=["fakewrap"])
    with pytest.raises(SystemExit):
        denver.run_stages(env_dir, config, cfg_path, [], options=options)


# ---- per-stage performance recording -----------------------------------------#
def test_run_stages_records_performance_trace(tmp_path, fake_providers, exec_recorder, monkeypatch, capsys):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {"stages": ["fakesetup"], "fakesetup": {"provider": "fakesetup"}}
    # the trace file is written regardless of --verbose (see below); the
    # printed "finished in" line is --verbose only (performance output).
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"], options=denver.RunOptions(verbose=True))

    err = capsys.readouterr().err
    assert "fakesetup" in err
    assert "finished in" in err

    perf_path = tmp_path / "env" / ".denver" / "denver" / "performance.jsonl"
    events = [json.loads(line) for line in perf_path.read_text().splitlines()]
    stage_events = [e for e in events if e.get("ph") == "X"]
    assert len(stage_events) == 1
    assert stage_events[0]["name"] == "fakesetup"
    assert stage_events[0]["args"]["provider"] == "fakesetup"
    assert stage_events[0]["dur"] >= 0
    assert any(e.get("ph") == "M" for e in events)


def test_run_stages_appends_performance_across_runs(tmp_path, fake_providers, exec_recorder, monkeypatch):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {"stages": ["fakesetup"], "fakesetup": {"provider": "fakesetup"}}
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])

    perf_path = tmp_path / "env" / ".denver" / "denver" / "performance.jsonl"
    events = [json.loads(line) for line in perf_path.read_text().splitlines()]
    stage_events = [e for e in events if e.get("ph") == "X"]
    assert len(stage_events) == 2
    # each run_stages() call gets its own ctx and announces itself once --
    # two separate calls (simulating two separate denver invocations sharing
    # this test's pid) means two metadata events; a real docker-wrapped run's
    # host/container processes each contribute their own the same way.
    assert len([e for e in events if e.get("ph") == "M"]) == 2


def test_run_stages_tolerates_pre_existing_garbage_in_performance_file(
    tmp_path, fake_providers, exec_recorder, monkeypatch
):
    env_dir, cfg_path = _env(tmp_path, {})
    perf_path = tmp_path / "env" / ".denver" / "denver" / "performance.jsonl"
    perf_path.parent.mkdir(parents=True)
    perf_path.write_text("not json\n")

    config = {"stages": ["fakesetup"], "fakesetup": {"provider": "fakesetup"}}
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])

    lines = perf_path.read_text().splitlines()
    assert lines[0] == "not json"  # pre-existing content is never touched, only appended to
    stage_events = [json.loads(line) for line in lines[1:] if json.loads(line).get("ph") == "X"]
    assert len(stage_events) == 1


def test_run_stages_in_container_skips_wrapper(tmp_path, fake_providers, exec_recorder, monkeypatch):
    import denver_providers.context as ctxmod

    # simulate running inside a container: in_container becomes True post-init
    orig_init = ctxmod.Context.__init__

    def patched_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.in_container = True

    monkeypatch.setattr(ctxmod.Context, "__init__", patched_init)

    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap"},
        "fakesetup": {"provider": "fakesetup"},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    assert "WRAP_SETUP" not in exec_recorder["env"]
    assert exec_recorder["env"]["RAN_FAKESETUP"] == "1"


def test_run_stages_no_logo_for_pure_wrapper(tmp_path, fake_providers, exec_recorder, capsys):
    """A pure-wrapper stage list (no non-wrapper setups) relocates straight
    into docker via ctx.exec() -- current run_stages() has no print_logo()
    call on that path, so no banner is shown here."""
    env_dir, cfg_path = _env(tmp_path, {})
    config = {"stages": ["fakewrap"], "fakewrap": {"provider": "fakewrap"}}

    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    err = capsys.readouterr().err
    assert denver.LOGO_PATH.read_text().splitlines()[0] not in err


def test_run_stages_logo_shown_even_when_already_in_container(
    tmp_path, fake_providers, exec_recorder, monkeypatch, capsys
):
    """The non-wrapper branch's print_logo() isn't gated on ctx.in_container,
    so it shows here too -- this is also the path taken when a docker-
    wrapped env reinvokes itself inside the container."""
    import denver_providers.context as ctxmod

    orig_init = ctxmod.Context.__init__

    def patched_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.in_container = True

    monkeypatch.setattr(ctxmod.Context, "__init__", patched_init)

    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap"},
        "fakesetup": {"provider": "fakesetup"},
    }
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    err = capsys.readouterr().err
    assert denver.LOGO_PATH.read_text().splitlines()[0] in err


# A synthetic PATH-lookup-default provider: every built-in provider's 'exe:'
# now defaults to a bare tool name (see docker/uv/conan's resolve_defaults),
# so none of them exercise a PATH lookup at resolve time any more. This
# stand-in keeps the *orchestration* mechanism -- run_stages() re-resolving
# each stage's raw section right before its setup(), not just trusting the
# upfront resolve_full_config() snapshot -- covered independently of any one
# provider's own default-resolution choices.
class PathLookupSetup(Provider):
    name = "pathlookup"
    kind = "setup"
    KEYS = ("exe",)

    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):  # noqa: ARG003  # shared (ctx, cfg, config) signature
        resolved = dict(cfg)
        resolved["exe"] = cfg.get("exe") or ctx.which("tool", dry_fallback=True)
        return resolved

    def setup(self, ctx):
        from denver_providers.context import die

        cfg = self.config_section(ctx)
        exe = cfg.get("exe")
        if not exe:
            die(f"pathlookup[{self.stage}]: needs 'exe' on PATH")
        ctx.run([exe, "action"])
        # simulates a uv-style stage that creates+activates its venv as the
        # last step of its own setup() -- so *this* stage's own invocation
        # above still used the pre-activation resolution, and only a later
        # stage's refresh sees the update.
        ctx.env["PATH"] = "/synthetic-venv-marker/bin:" + ctx.env["PATH"]


@pytest.fixture
def fake_pathlookup_provider(monkeypatch):
    monkeypatch.setitem(providers.PROVIDERS, "pathlookup", PathLookupSetup)


def test_run_stages_reresolves_stage_defaults_before_setup(
    tmp_path, run_recorder, exec_recorder, monkeypatch, fake_pathlookup_provider
):
    """A PATH-lookup default resolved once, upfront, before any stage's
    setup() runs, can be stale by the time that stage's setup() actually
    executes (e.g. an earlier stage installs/activates the tool in
    between). run_stages() must re-resolve each stage's defaults right
    before running it, not just rely on the upfront resolve_full_config()
    snapshot -- otherwise a tool an earlier stage just installed would still
    look 'missing' to a later stage."""
    import denver_providers.context as ctxmod

    which_calls = {"n": 0}

    def fake_which(name, path=None):
        if name != "tool":
            return f"/usr/bin/{name}"
        which_calls["n"] += 1
        # not found the first time (resolve_full_config's upfront pass);
        # found from the second call on (run_setup's pre-setup() refresh).
        return None if which_calls["n"] == 1 else "/usr/bin/tool"

    monkeypatch.setattr(ctxmod.shutil, "which", fake_which)

    config = {"stages": ["pathlookup"], "pathlookup": {"provider": "pathlookup"}}
    env_dir, cfg_path = _env(tmp_path, config)
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    # setup() didn't die on "tool not found" -- it saw the re-resolved value
    assert which_calls["n"] >= 2
    assert exec_recorder["args"] == ["echo", "hi"]


def _venv_creating_response(run_recorder):
    """A run_recorder response that materialises the venv dir ``uv venv`` was asked for."""

    def _respond(cmd):
        Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
        return run_recorder.default

    return _respond


def _python_version_response(version="3.12.3"):
    """A run_recorder response answering `python3 --version`."""
    return lambda cmd: type("R", (), {"stdout": f"Python {version}\n", "returncode": 0})()


def _tool_from_venv_or_host(name, path=None):
    """shutil.which stub: '/venv/bin/tool' once the synthetic venv marker is on the lookup PATH,
    '/usr/bin/tool' before that."""
    if name != "tool":
        return f"/usr/bin/{name}"
    return "/venv/bin/tool" if "synthetic-venv-marker" in (path or "") else "/usr/bin/tool"


def test_run_stages_reresolves_over_a_default_it_already_found(
    tmp_path, run_recorder, exec_recorder, monkeypatch, fake_pathlookup_provider
):
    """The upfront pass finding *something* must not freeze that answer.

    The failure this guards against: the host has its own copy of a tool
    (conan, west, uv), so the upfront resolve picks that up; an earlier
    stage then installs the pinned one into the venv, and the later stage
    goes on running the host's anyway -- silently unpinned. Re-resolving
    the *resolved* section can't fix that (a resolver only fills unset
    keys, so its own output reads as an explicit choice), which is why the
    refresh starts from the raw section instead.
    """
    import denver_providers.context as ctxmod

    monkeypatch.setattr(ctxmod.shutil, "which", _tool_from_venv_or_host)

    # two pathlookup stages: the first activates its venv as the last step of
    # its own setup() (putting it on PATH), so the second's own refresh must
    # resolve to the venv's tool, not the host's.
    config = {
        "stages": ["pathlookup-first", "pathlookup-second"],
        "pathlookup-first": {"provider": "pathlookup"},
        "pathlookup-second": {"provider": "pathlookup"},
    }
    env_dir, cfg_path = _env(tmp_path, config)
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])

    commands = run_recorder.commands()
    assert any(c.startswith("/usr/bin/tool action") for c in commands)  # stage 1: no venv yet
    assert any(c.startswith("/venv/bin/tool action") for c in commands)  # stage 2: refreshed
    assert exec_recorder["args"] == ["echo", "hi"]


def test_run_stages_refresh_keeps_an_explicit_value(tmp_path, run_recorder, exec_recorder, monkeypatch):
    # the refresh re-runs the resolver, so what the author actually wrote
    # has to survive it -- a PATH lookup must never overrule an explicit key
    import denver_providers.context as ctxmod

    monkeypatch.setattr(ctxmod.shutil, "which", lambda name, path=None: f"/usr/bin/{name}")

    def create_venv_dir(cmd):
        from pathlib import Path

        Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
        return run_recorder.default

    run_recorder.responses["venv -p"] = create_venv_dir
    run_recorder.responses["python3 --version"] = lambda cmd: type(
        "R", (), {"stdout": "Python 3.12.3\n", "returncode": 0}
    )()

    config = {"stages": ["uv"], "uv": {"provider": "uv", "exe": "/opt/pinned/uv"}}
    env_dir, cfg_path = _env(tmp_path, config)
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])

    commands = run_recorder.commands()
    assert any(c.startswith("/opt/pinned/uv") for c in commands)
    assert not any(c.startswith("/usr/bin/uv") for c in commands)


def test_run_stages_stacking_used_by_stage(tmp_path, fake_providers, exec_recorder):
    src_env = tmp_path / "src"
    src_env.mkdir()
    (src_env / "denver.toml").write_text('[fakewrap]\nmarker = "from-src"\n')
    env_dir, cfg_path = _env(tmp_path, {})
    config = {"stages": ["fakewrap"], "fakewrap": {"import": ["../src"], "provider": "fakewrap"}}
    denver.run_stages(env_dir, config, cfg_path, ["echo", "hi"])
    assert exec_recorder["args"] == ["WRAPPED", "echo", "hi"]


# ---- run_named_scripts (--scripts <name>, e.g. 'setup'/'login'/'clean') ------#
# parametrized over the name: the mechanism (run_named_scripts) doesn't care
# what 'name' is, so one suite covers every convention (setup, login, clean,
# or a project-specific one) instead of a hand-duplicated copy per name.
@pytest.mark.parametrize("name", ["setup", "login"])
def test_run_named_scripts_runs_each_setup_stage_in_order(tmp_path, fake_providers, run_recorder, name):
    # no wrapper stage declared at all -- always the flat, direct-run case
    env_dir, cfg_path = _env(tmp_path, {})
    (env_dir / "a.sh").write_text("#!/bin/bash\n")
    (env_dir / "b.sh").write_text("#!/bin/bash\n")
    config = {
        "stages": ["fakesetup-a", "fakesetup-b"],
        "fakesetup-a": {"provider": "fakesetup", "scripts": {name: ["a.sh"]}},
        "fakesetup-b": {"provider": "fakesetup", "scripts": {name: ["b.sh"]}},
    }
    denver.run_named_scripts(env_dir, config, cfg_path, [name])
    commands = run_recorder.commands()
    assert str((env_dir / "a.sh").resolve()) in commands[-2]
    assert str((env_dir / "b.sh").resolve()) in commands[-1]
    # no provider setup()/wrap() ran, no exec() happened
    assert not any("WRAPPED" in c for c in commands)


@pytest.mark.parametrize("name", ["setup", "login"])
def test_run_named_scripts_skips_stage_without_entry(tmp_path, fake_providers, run_recorder, caplog, name):
    caplog.set_level("INFO")
    env_dir, cfg_path = _env(tmp_path, {})
    config = {"stages": ["fakesetup"], "fakesetup": {"provider": "fakesetup"}}
    denver.run_named_scripts(env_dir, config, cfg_path, [name])
    assert run_recorder.commands() == []
    # nothing ran and nothing was printed -- this is what the user actually
    # asked for (--scripts <name> found no entries at all), so it has to say
    # so rather than exiting 0 in total silence
    assert f"no '{name}' scripts to run for env '{env_dir.name}'" in caplog.text


@pytest.mark.parametrize("name", ["setup", "login"])
def test_run_named_scripts_warns_once_per_empty_name(tmp_path, fake_providers, run_recorder, caplog, name):
    # two names in one invocation, only one of them declared anywhere --
    # only the empty one gets warned about, and only once
    other = "other-name"
    env_dir, cfg_path = _env(tmp_path, {})
    (env_dir / "a.sh").write_text("#!/bin/bash\n")
    config = {
        "stages": ["fakesetup"],
        "fakesetup": {"provider": "fakesetup", "scripts": {name: ["a.sh"]}},
    }
    caplog.set_level("INFO")
    denver.run_named_scripts(env_dir, config, cfg_path, [name, other])
    assert f"no '{other}' scripts to run for env '{env_dir.name}'" in caplog.text
    assert f"no '{name}' scripts to run" not in caplog.text
    assert caplog.text.count("scripts to run for env") == 1


@pytest.mark.parametrize("name", ["setup", "login"])
def test_run_named_scripts_wrapper_relocation_warns_when_neither_side_has_entries(
    tmp_path, fake_providers, run_recorder, exec_recorder, caplog, name
):
    # neither the wrapper stage nor the setup stage declares 'name' -- there
    # is nothing to relocate and nothing ran on the host either, so this has
    # to be flagged the same way the no-wrapper case is
    caplog.set_level("INFO")
    env_dir, cfg_path = _env(tmp_path, {})
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap"},
        "fakesetup": {"provider": "fakesetup"},
    }
    denver.run_named_scripts(env_dir, config, cfg_path, [name])
    assert run_recorder.commands() == []
    assert exec_recorder == {}
    assert f"no '{name}' scripts to run for env '{env_dir.name}'" in caplog.text


@pytest.mark.parametrize("name", ["setup", "login"])
def test_run_named_scripts_missing_file_dies(tmp_path, fake_providers, run_recorder, name):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {"stages": ["fakesetup"], "fakesetup": {"provider": "fakesetup", "scripts": {name: ["nope.sh"]}}}
    with pytest.raises(SystemExit):
        denver.run_named_scripts(env_dir, config, cfg_path, [name])


@pytest.mark.parametrize("name", ["setup", "login"])
def test_run_named_scripts_non_list_dies(tmp_path, fake_providers, run_recorder, name):
    env_dir, cfg_path = _env(tmp_path, {})
    config = {"stages": ["fakesetup"], "fakesetup": {"provider": "fakesetup", "scripts": {name: "a.sh"}}}
    with pytest.raises(SystemExit):
        denver.run_named_scripts(env_dir, config, cfg_path, [name])


@pytest.mark.parametrize("name", ["setup", "login"])
def test_run_named_scripts_only_filters_to_named_stage(tmp_path, fake_providers, run_recorder, name):
    env_dir, cfg_path = _env(tmp_path, {})
    (env_dir / "a.sh").write_text("#!/bin/bash\n")
    (env_dir / "b.sh").write_text("#!/bin/bash\n")
    config = {
        "stages": ["fakesetup-a", "fakesetup-b"],
        "fakesetup-a": {"provider": "fakesetup", "scripts": {name: ["a.sh"]}},
        "fakesetup-b": {"provider": "fakesetup", "scripts": {name: ["b.sh"]}},
    }
    denver.run_named_scripts(env_dir, config, cfg_path, [name], until_stage="fakesetup-a")
    commands = run_recorder.commands()
    assert len(commands) == 1
    assert str((env_dir / "a.sh").resolve()) in commands[0]


# ---- run_named_scripts: wrapper context ------------------------------------#
@pytest.mark.parametrize("name", ["setup", "login"])
def test_run_named_scripts_wrapper_runs_on_host_no_relocation_needed(tmp_path, fake_providers, run_recorder, name):
    # only the wrapper stage has a script; nothing needs relocating, so it
    # runs directly on the host -- no wrapper setup(), no exec()
    env_dir, cfg_path = _env(tmp_path, {})
    (env_dir / "a.sh").write_text("#!/bin/bash\n")
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap", "scripts": {name: ["a.sh"]}},
        "fakesetup": {"provider": "fakesetup"},
    }
    denver.run_named_scripts(env_dir, config, cfg_path, [name])
    commands = run_recorder.commands()
    assert str((env_dir / "a.sh").resolve()) in commands[-1]
    assert not any("WRAPPED" in c for c in commands)


@pytest.mark.parametrize("name", ["setup", "login"])
def test_run_named_scripts_relocates_setup_entries_into_active_wrapper(
    tmp_path, fake_providers, run_recorder, exec_recorder, name
):
    # a setup stage's own entry needs the wrapper's context (e.g. conan only
    # exists once inside a docker-wrapped env) -- the wrapper's own entry
    # runs on the host first, then the wrapper is prepared (setup()) and
    # denver re-invoked --skip <that wrapper stage> --scripts <name> inside it
    # for the setup stage's own entry
    env_dir, cfg_path = _env(tmp_path, {})
    (env_dir / "wrap-script.sh").write_text("#!/bin/bash\n")
    (env_dir / "setup-script.sh").write_text("#!/bin/bash\n")
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap", "scripts": {name: ["wrap-script.sh"]}},
        "fakesetup": {"provider": "fakesetup", "scripts": {name: ["setup-script.sh"]}},
    }
    denver.run_named_scripts(env_dir, config, cfg_path, [name])
    assert str((env_dir / "wrap-script.sh").resolve()) in run_recorder.commands()[0]
    assert exec_recorder["env"]["WRAP_SETUP"] == "1"
    assert exec_recorder["args"][0] == "WRAPPED"
    args = exec_recorder["args"]
    assert args[args.index("--skip") + 1] == "fakewrap"
    assert args[args.index("--scripts") + 1] == name


def test_run_named_scripts_relocation_forwards_verbose(tmp_path, fake_providers, run_recorder, exec_recorder):
    # --verbose (like --quiet) has to survive the reinvocation into the
    # wrapper, or the inner denver would silently fall back to its default.
    env_dir, cfg_path = _env(tmp_path, {})
    (env_dir / "setup-script.sh").write_text("#!/bin/bash\n")
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap"},
        "fakesetup": {"provider": "fakesetup", "scripts": {"setup": ["setup-script.sh"]}},
    }
    denver.run_named_scripts(env_dir, config, cfg_path, ["setup"], verbose=True)
    assert "-v" in exec_recorder["args"]


@pytest.mark.parametrize("name", ["setup", "login"])
def test_run_named_scripts_skip_wrapper_stage_runs_entirely_on_host(
    tmp_path, fake_providers, run_recorder, exec_recorder, name
):
    env_dir, cfg_path = _env(tmp_path, {})
    (env_dir / "setup-script.sh").write_text("#!/bin/bash\n")
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap", "scripts": {name: ["wrap-script.sh"]}},
        "fakesetup": {"provider": "fakesetup", "scripts": {name: ["setup-script.sh"]}},
    }
    denver.run_named_scripts(env_dir, config, cfg_path, [name], skip_stages=["fakewrap"])
    commands = run_recorder.commands()
    assert str((env_dir / "setup-script.sh").resolve()) in commands[-1]
    assert not any("wrap-script.sh" in c for c in commands)
    assert exec_recorder == {}


@pytest.mark.parametrize("name", ["setup", "login"])
def test_run_named_scripts_relocation_forwards_quiet(tmp_path, fake_providers, run_recorder, exec_recorder, name):
    env_dir, cfg_path = _env(tmp_path, {})
    (env_dir / "setup-script.sh").write_text("#!/bin/bash\n")
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap"},
        "fakesetup": {"provider": "fakesetup", "scripts": {name: ["setup-script.sh"]}},
    }
    denver.run_named_scripts(env_dir, config, cfg_path, [name], quiet=True)
    assert "-q" in exec_recorder["args"]


@pytest.mark.parametrize("name", ["setup", "login"])
def test_run_named_scripts_relocation_forwards_until_and_skip(
    tmp_path, fake_providers, run_recorder, exec_recorder, name
):
    env_dir, cfg_path = _env(tmp_path, {})
    (env_dir / "setup-script.sh").write_text("#!/bin/bash\n")
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap"},
        "fakesetup": {"provider": "fakesetup", "scripts": {name: ["setup-script.sh"]}},
    }
    denver.run_named_scripts(env_dir, config, cfg_path, [name], until_stage="fakesetup", skip_stages=["x"])
    args = exec_recorder["args"]
    assert args[args.index("--until") + 1] == "fakesetup"
    # the caller's own --skip ("x") is forwarded first, then the active
    # wrapper stage ("fakewrap") is appended so the re-invocation doesn't
    # try to relocate into it again
    skip_positions = [i for i, tok in enumerate(args) if tok == "--skip"]
    assert [args[i + 1] for i in skip_positions] == ["x", "fakewrap"]


# ---- run_named_scripts: repeatable --scripts (multiple names, one call) ----#
def test_run_named_scripts_runs_multiple_names_in_order(tmp_path, fake_providers, run_recorder):
    env_dir, cfg_path = _env(tmp_path, {})
    (env_dir / "setup.sh").write_text("#!/bin/bash\n")
    (env_dir / "login.sh").write_text("#!/bin/bash\n")
    config = {
        "stages": ["fakesetup"],
        "fakesetup": {"provider": "fakesetup", "scripts": {"setup": ["setup.sh"], "login": ["login.sh"]}},
    }
    denver.run_named_scripts(env_dir, config, cfg_path, ["setup", "login"])
    commands = run_recorder.commands()
    assert str((env_dir / "setup.sh").resolve()) in commands[0]
    assert str((env_dir / "login.sh").resolve()) in commands[1]


def test_run_named_scripts_relocation_carries_every_name_that_needs_it(
    tmp_path, fake_providers, run_recorder, exec_recorder
):
    # both names have a setup-stage entry -- both land in the single
    # reinvocation, in the order given.
    env_dir, cfg_path = _env(tmp_path, {})
    (env_dir / "setup-script.sh").write_text("#!/bin/bash\n")
    (env_dir / "login-script.sh").write_text("#!/bin/bash\n")
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap"},
        "fakesetup": {
            "provider": "fakesetup",
            "scripts": {"setup": ["setup-script.sh"], "login": ["login-script.sh"]},
        },
    }
    denver.run_named_scripts(env_dir, config, cfg_path, ["setup", "login"])
    args = exec_recorder["args"]
    scripts_positions = [i for i, tok in enumerate(args) if tok == "--scripts"]
    assert [args[i + 1] for i in scripts_positions] == ["setup", "login"]


def test_run_named_scripts_relocation_only_carries_names_that_actually_need_it(
    tmp_path, fake_providers, run_recorder, exec_recorder
):
    # 'setup' only has a wrapper-stage entry (runs on the host, no
    # relocation needed for it); 'login' has a setup-stage entry too -- only
    # 'login' is worth carrying into the reinvocation.
    env_dir, cfg_path = _env(tmp_path, {})
    (env_dir / "wrap-script.sh").write_text("#!/bin/bash\n")
    (env_dir / "login-script.sh").write_text("#!/bin/bash\n")
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap", "scripts": {"setup": ["wrap-script.sh"]}},
        "fakesetup": {"provider": "fakesetup", "scripts": {"login": ["login-script.sh"]}},
    }
    denver.run_named_scripts(env_dir, config, cfg_path, ["setup", "login"])
    assert str((env_dir / "wrap-script.sh").resolve()) in run_recorder.commands()[0]
    args = exec_recorder["args"]
    scripts_positions = [i for i, tok in enumerate(args) if tok == "--scripts"]
    assert [args[i + 1] for i in scripts_positions] == ["login"]


def test_run_named_scripts_relocation_skipped_when_no_name_needs_it(tmp_path, fake_providers, run_recorder):
    # neither name has any setup-stage entry -- both run entirely on the
    # host, no reinvocation at all.
    env_dir, cfg_path = _env(tmp_path, {})
    (env_dir / "wrap-a.sh").write_text("#!/bin/bash\n")
    (env_dir / "wrap-b.sh").write_text("#!/bin/bash\n")
    config = {
        "stages": ["fakewrap", "fakesetup"],
        "fakewrap": {"provider": "fakewrap", "scripts": {"setup": ["wrap-a.sh"], "login": ["wrap-b.sh"]}},
        "fakesetup": {"provider": "fakesetup"},
    }
    denver.run_named_scripts(env_dir, config, cfg_path, ["setup", "login"])
    commands = run_recorder.commands()
    assert not any("WRAPPED" in c for c in commands)
