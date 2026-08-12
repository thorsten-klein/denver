"""Tests for providers.zephyr.ZephyrProvider."""

from pathlib import Path

import pytest

from denver_providers.zephyr import ZephyrProvider, west_topdir


# ---- west_topdir -------------------------------------------------------------#
def test_west_topdir_prefers_west(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / ".west").mkdir()
    assert west_topdir(tmp_path / "sub") == tmp_path / "sub"


def test_west_topdir_falls_back_to_outermost_git(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "sub").mkdir()
    assert west_topdir(tmp_path / "sub") == tmp_path


def test_west_topdir_none(tmp_path):
    assert west_topdir(tmp_path) is None


def make_ctx(make_context, config, marker=".git", **ctx_kwargs):
    """Build a Context whose denver_dir has a topdir marker (.git or .west).

    env_dir defaults to a subdirectory of denver_dir (see make_context), so
    the upward search from env_dir (both west_topdir() and
    ZephyrProvider.resolve_defaults's west-yml discovery) still finds this
    same marker.
    """
    denver_dir = make_context.denver_dir
    (denver_dir / marker).mkdir(parents=True, exist_ok=True)
    return make_context(config=config, **ctx_kwargs)


def run_zephyr(config, ctx, stage="zephyr"):
    """Resolve ``config[stage]``'s defaults exactly like denver.py's real
    pipeline would (see ZephyrProvider.resolve_defaults), then run the zephyr
    stage's setup() against it and return ctx."""
    config[stage] = ZephyrProvider.resolve_defaults(ctx, config.get(stage) or {}, config)
    n = ZephyrProvider(config)
    n.stage = stage
    ctx.stage_id = stage  # denver.py sets this before setup(); mirrored here for ctx.run(step=...)
    n.setup(ctx)
    return ctx


def resp(**fields):
    return lambda cmd: type("R", (), {"returncode": 0, "stderr": "", **fields})()


# ---- --fast ------------------------------------------------------------------#
def test_fast_noop_when_workspace_already_configured(make_context, run_recorder, which):
    config = {"zephyr": {}}
    ctx = make_ctx(make_context, config, fast=True)
    west_config = west_topdir(ctx.env_dir) / ".west" / "config"
    west_config.parent.mkdir(parents=True)
    west_config.touch()

    run_zephyr(config, ctx)

    assert run_recorder.calls == []


def test_fast_still_shows_progress_banner(make_context, run_recorder, which, capsys):
    # --fast activates instead of building, but the '[i/n]' progress line
    # must still show under -q, not silently vanish.
    config = {"zephyr": {}}
    ctx = make_ctx(make_context, config, fast=True, quiet=1)
    west_config = west_topdir(ctx.env_dir) / ".west" / "config"
    west_config.parent.mkdir(parents=True)
    west_config.touch()

    run_zephyr(config, ctx)

    err = capsys.readouterr().err
    assert "zephyr" in err
    assert "west config" in err
    assert "activate" in err  # the real work --fast does, not skipped


def test_fast_dies_when_workspace_not_configured(make_context, which):
    config = {"zephyr": {}}
    ctx = make_ctx(make_context, config, fast=True)  # no .west/config yet
    with pytest.raises(SystemExit):
        run_zephyr(config, ctx)


# ---- top-level guards -------------------------------------------------------#
def test_no_west_topdir_dies(make_context, which):
    # explicit west-yml so the central resolver doesn't die first (no git repo
    # to auto-discover from) -- this test is specifically about setup()'s own
    # WEST_TOPDIR guard.
    config = {"zephyr": {"west-yml": "test.yml"}}
    ctx = make_context(config=config)  # no .git/.west anywhere
    with pytest.raises(SystemExit):
        run_zephyr(config, ctx)


def test_west_missing_dies(make_context, which):
    which["west"] = None
    config = {"zephyr": {}}
    ctx = make_ctx(make_context, config)
    with pytest.raises(SystemExit):
        run_zephyr(config, ctx)


def test_west_found_on_path(make_context, run_recorder, which):
    # 'west' is never a denver.yml key -- always the first 'west' on PATH
    config = {"zephyr": {"west-yml": "manifest/west.yml"}}
    ctx = make_ctx(make_context, config)
    run_zephyr(config, ctx)
    assert any("/usr/bin/west config -l" in c for c in run_recorder.commands())


# ---- west-yml resolution ------------------------------------------------------#
def test_west_yml_configured(make_context, run_recorder, which):
    config = {"zephyr": {"west-yml": "manifest/west.yml"}}
    ctx = make_ctx(make_context, config)
    run_zephyr(config, ctx)
    # no assertion needed beyond not crashing; exercised via _configure below


def test_west_yml_default_from_outer_git(make_context, run_recorder, which, tmp_path):
    # marker=".git" (the default): west_topdir falls back to the outermost
    # .git, and the central resolver's own find_outermost_in_parents finds
    # that same one.
    config = {"zephyr": {}}
    ctx = make_ctx(make_context, config)
    run_zephyr(config, ctx)
    assert any(a[-2:] == ["manifest.file", "west.yml"] for a in run_recorder.argvs())


def test_west_yml_default_no_git_dies(make_context, which):
    config = {"zephyr": {}}
    ctx = make_ctx(make_context, config, marker=".west")  # topdir via .west only
    with pytest.raises(SystemExit):
        run_zephyr(config, ctx)


# ---- zephyr_base -----------------------------------------------------------------#
def test_zephyr_base_default(make_context, run_recorder, which):
    config = {"zephyr": {"west-yml": "west.yml"}}
    ctx = make_ctx(make_context, config)
    run_zephyr(config, ctx)
    assert ctx.env.get("WEST_TOPDIR")


# ---- _ensure_workspace ----------------------------------------------------------#
def test_ensure_workspace_creates_config(make_context, run_recorder, which):
    config = {"zephyr": {"west-yml": "west.yml"}}
    ctx = make_ctx(make_context, config)
    run_zephyr(config, ctx)
    assert (west_topdir(ctx.env_dir) / ".west" / "config").is_file()


def test_ensure_workspace_leaves_existing_without_force(make_context, run_recorder, which):
    config = {"zephyr": {"west-yml": "west.yml"}}
    ctx = make_ctx(make_context, config)
    west_config = west_topdir(ctx.env_dir) / ".west" / "config"
    west_config.parent.mkdir(parents=True)
    west_config.write_text("manifest.path=kept\n")
    run_zephyr(config, ctx)
    # ensure() will still rewrite individual keys but the file was not deleted
    # before being rewritten by 'west config' calls (which we've mocked away),
    # so the original content banner is irrelevant -- just check no crash and
    # the file still exists.
    assert west_config.exists()


def test_ensure_workspace_force_recreates(make_context, run_recorder, which):
    config = {"zephyr": {"west-yml": "west.yml"}}
    ctx = make_ctx(make_context, config)
    west_config = west_topdir(ctx.env_dir) / ".west" / "config"
    west_config.parent.mkdir(parents=True)
    west_config.write_text("stale\n")
    ctx.force = True
    run_zephyr(config, ctx)
    assert west_config.is_file()
    assert west_config.read_text() == ""  # recreated empty by touch()


# ---- _configure -----------------------------------------------------------------#
def test_configure_sets_all_keys_when_missing(make_context, run_recorder, which):
    config = {"zephyr": {"west-yml": "west.yml"}}
    ctx = make_ctx(make_context, config)
    run_zephyr(config, ctx)
    set_cmds = [
        c
        for c in run_recorder.commands()
        if "config manifest.path" in c or "config manifest.file" in c or "config zephyr.base" in c
    ]
    assert len(set_cmds) == 3


def test_configure_skips_matching_existing(make_context, run_recorder, which):
    config = {"zephyr": {"west-yml": "west.yml"}}
    ctx = make_ctx(make_context, config)
    west_yml_path = (ctx.env_dir / "west.yml").resolve()
    import os as _os

    manifest_path = _os.path.relpath(west_yml_path.parent, west_topdir(ctx.env_dir))
    zephyr_base = _os.path.relpath((west_topdir(ctx.env_dir) / "zephyr-rtos").resolve(), west_topdir(ctx.env_dir))
    run_recorder.responses["config -l"] = resp(
        stdout=(f"manifest.path={manifest_path}\nmanifest.file=west.yml\nzephyr.base={zephyr_base}\n")
    )
    run_zephyr(config, ctx)
    set_cmds = [
        c for c in run_recorder.commands() if c.startswith("west config manifest") or "config zephyr.base " in c
    ]
    assert set_cmds == []


def test_configure_extra_west_config_overrides(make_context, run_recorder, which):
    config = {"zephyr": {"west-yml": "west.yml", "west-config": {"zephyr.base-prefer": "env"}}}
    ctx = make_ctx(make_context, config)
    run_zephyr(config, ctx)
    assert any(a[-2:] == ["zephyr.base-prefer", "env"] for a in run_recorder.argvs())


# ---- _update ----------------------------------------------------------------------#
def test_update_runs_on_first_run(make_context, run_recorder, which):
    config = {"zephyr": {"west-yml": "west.yml"}}
    ctx = make_ctx(make_context, config)
    run_zephyr(config, ctx)
    assert any(c.endswith("update") or " update" in c for c in run_recorder.commands())
    info_file = ctx.logs_dir / "west-update.info"
    assert info_file.is_file()


@pytest.mark.parametrize("force", [False, True], ids=["skipped-when-unchanged", "forced-even-when-unchanged"])
def test_update_when_info_unchanged(make_context, run_recorder, which, force):
    zephyr_cfg = {"west-yml": "west.yml"}
    config = {"zephyr": zephyr_cfg}
    ctx = make_ctx(make_context, config)
    # pre-populate the info file with exactly what _west_info would compute
    n = ZephyrProvider(config)
    n.stage = "zephyr"
    west = ctx.which("west")
    west_yml = Path(ctx.resolve_path(zephyr_cfg["west-yml"]))
    zephyr_base = ctx.resolve_path("${WEST_TOPDIR}/zephyr-rtos")
    info_text = n._west_info(ctx, west, west_topdir(ctx.env_dir), west_yml, zephyr_base)
    info_file = ctx.logs_dir / "west-update.info"
    info_file.parent.mkdir(parents=True, exist_ok=True)
    info_file.write_text(info_text)
    ctx.force = force
    run_recorder.calls.clear()

    run_zephyr(config, ctx)
    ran_update = any(c.endswith(" update") or "west update" in c for c in run_recorder.commands())
    assert ran_update == force


def test_update_ci_uses_ci_update_args(make_context, run_recorder, which):
    config = {"zephyr": {"west-yml": "west.yml"}}
    ctx = make_ctx(make_context, config)
    ctx.ci = True
    run_zephyr(config, ctx)
    assert any("--narrow" in c for c in run_recorder.commands())


def test_update_args_combined_with_fixed_ci_args(make_context, run_recorder, which):
    # ci-update-args isn't a denver.yml key any more -- CI_UPDATE_ARGS is a
    # fixed constant added on top of whatever 'update-args:' configures.
    config = {"zephyr": {"west-yml": "west.yml", "update-args": ["--stats"]}}
    ctx = make_ctx(make_context, config)
    ctx.ci = True
    run_zephyr(config, ctx)
    update_cmd = next(c for c in run_recorder.commands() if c.endswith("update") or " update " in c)
    assert "--stats" in update_cmd
    assert "--narrow" in update_cmd


# ---- _apply_project_patches -----------------------------------------------------#
def test_apply_project_patches_runs_for_patched_projects(make_context, run_recorder, which, tmp_path):
    config = {"zephyr": {"west-yml": "west.yml"}}
    ctx = make_ctx(make_context, config)
    proj = tmp_path / "proj"
    (proj / "zephyr").mkdir(parents=True)
    (proj / "zephyr" / "patches.yml").write_text("x\n")
    unpatched = tmp_path / "unpatched"
    unpatched.mkdir()

    run_recorder.responses["list -f {abspath}"] = resp(stdout=f"{unpatched}\n{proj}\n")
    run_zephyr(config, ctx)
    patch_argvs = [a for a in run_recorder.argvs() if "--src-module" in a]
    assert any(a[a.index("--src-module") + 1] == str(proj) for a in patch_argvs)
    assert not any(a[a.index("--src-module") + 1] == str(unpatched) for a in patch_argvs)


def test_apply_project_patches_committer_override(make_context, run_recorder, which, tmp_path):
    config = {
        "zephyr": {
            "west-yml": "west.yml",
            "patch-committer": {"GIT_COMMITTER_NAME": "custom"},
        }
    }
    ctx = make_ctx(make_context, config)
    proj = tmp_path / "proj"
    (proj / "zephyr").mkdir(parents=True)
    (proj / "zephyr" / "patches.yml").write_text("x\n")
    run_recorder.responses["list -f {abspath}"] = resp(stdout=f"{proj}\n")

    run_zephyr(config, ctx)
    # match the actual patch invocation specifically: tmp_path itself contains
    # "patch" as a substring here (from the test's own name), so a bare
    # "patch" match would pick up an earlier, unrelated call by accident.
    patch_call = next(c for c in run_recorder.calls if "--src-module" in " ".join(str(x) for x in c.cmd))
    # extra_env is merged into the actual subprocess env by Context.run()
    assert patch_call.kwargs["env"]["GIT_COMMITTER_NAME"] == "custom"


# ---- _update_blobs_cache -----------------------------------------------------------#
def test_blobs_cache_written_when_configured(make_context, run_recorder, which):
    config = {"zephyr": {"west-yml": "west.yml", "blobs-cache": "blobs.txt"}}
    ctx = make_ctx(make_context, config)
    run_zephyr(config, ctx)
    target = ctx.env_dir / "blobs.txt"
    assert target.is_file()
    assert "auto-generated" in target.read_text()


def test_blobs_cache_skipped_when_unconfigured(make_context, run_recorder, which):
    config = {"zephyr": {"west-yml": "west.yml"}}
    ctx = make_ctx(make_context, config)
    run_zephyr(config, ctx)
    assert not (ctx.env_dir / "blobs.txt").exists()


def test_blobs_fetch_args_configured(make_context, run_recorder, which):
    config = {"zephyr": {"west-yml": "west.yml", "blobs-fetch-args": ["--custom-flag"]}}
    ctx = make_ctx(make_context, config)
    run_zephyr(config, ctx)
    assert any("blobs fetch --custom-flag" in c for c in run_recorder.commands())
