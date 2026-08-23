"""Tests for providers.git.GitProvider.

Activation (env:/env-prepend:/env-append:) is not this provider's own
concern any more -- it's the generic per-stage mechanism every provider
gets, exercised in test_denver_orchestration.py instead.
"""

from __future__ import annotations

import pytest

from denver_providers.git import GitProvider

URL = "https://example.invalid/repo.git"
SHA_OLD = "a" * 40
SHA_NEW = "b" * 40


def config_for(stage_cfg, stage="pico-sdk"):
    return {stage: {"provider": "git", **stage_cfg}}


def run_git(config, ctx, stage="pico-sdk"):
    """Resolve this stage's defaults the way denver.py does, then run its setup()."""
    provider = GitProvider(config)
    provider.stage = stage
    config[stage] = GitProvider.resolve_defaults(ctx, config.get(stage) or {}, config)
    provider.setup(ctx)
    return provider


def resolved(ctx, entry):
    return GitProvider.resolve_defaults(ctx, entry, {})


def make_checkout(path, *, git=True):
    path.mkdir(parents=True, exist_ok=True)
    if git:
        (path / ".git").mkdir()
    return path


def fake_proc(stdout="", returncode=0):
    return type("R", (), {"stdout": stdout, "returncode": returncode})()


# ---- config defaults --------------------------------------------------------#
def test_defaults_fill_every_key(make_context):
    ctx = make_context()
    cfg = resolved(ctx, {"url": URL, "path": "checkout", "revision": "1.0"})
    assert set(cfg) == set(GitProvider.KEYS)
    assert cfg["remote"] == "origin"
    assert cfg["submodules"] is False


def test_path_resolved_absolute(make_context):
    ctx = make_context()
    cfg = resolved(ctx, {"url": URL, "path": "checkout", "revision": "1.0"})
    assert cfg["path"] == str(ctx.env_dir / "checkout")


def test_url_and_revision_interpolated(make_context):
    ctx = make_context(env={"PIN": "2.3.0"})
    cfg = resolved(ctx, {"url": URL, "path": "checkout", "revision": "${PIN}"})
    assert cfg["revision"] == "2.3.0"


@pytest.mark.parametrize("missing", ["url", "path", "revision"])
def test_missing_required_key_dies(make_context, missing):
    ctx = make_context()
    entry = {"url": URL, "path": "checkout", "revision": "1.0"}
    del entry[missing]
    with pytest.raises(SystemExit):
        resolved(ctx, entry)


def test_submodules_must_be_bool(make_context):
    ctx = make_context()
    with pytest.raises(SystemExit):
        resolved(ctx, {"url": URL, "path": "checkout", "revision": "1.0", "submodules": "true"})


def test_remote_must_be_a_string(make_context):
    ctx = make_context()
    with pytest.raises(SystemExit):
        resolved(ctx, {"url": URL, "path": "checkout", "revision": "1.0", "remote": 1})


# ---- setup(): fresh clone ----------------------------------------------------#
def test_fresh_path_clones_fetches_and_checks_out(make_context, run_recorder):
    config = config_for({"url": URL, "path": "checkout", "revision": "1.0"})
    ctx = make_context(config=config)
    path = ctx.env_dir / "checkout"
    run_recorder.responses["rev-parse --verify -q"] = lambda cmd: fake_proc(stdout=SHA_NEW + "\n")
    run_git(config, ctx)

    argvs = run_recorder.argvs()
    assert ["git", "clone", "--origin", "origin", "--", URL, str(path)] in argvs
    assert ["git", "-C", str(path), "fetch", "--tags", "--prune", "origin"] in argvs
    assert any(a[:5] == ["git", "-C", str(path), "checkout", "--detach"] for a in argvs)


def test_already_cloned_is_not_re_cloned(make_context, run_recorder):
    config = config_for({"url": URL, "path": "checkout", "revision": "1.0"})
    ctx = make_context(config=config)
    make_checkout(ctx.env_dir / "checkout")
    run_recorder.responses["rev-parse --verify -q"] = lambda cmd: fake_proc(stdout=SHA_NEW + "\n")
    run_git(config, ctx)

    assert not any(a[:2] == ["git", "clone"] for a in run_recorder.argvs())


def test_already_at_revision_skips_checkout(make_context, run_recorder):
    config = config_for({"url": URL, "path": "checkout", "revision": "1.0"})
    ctx = make_context(config=config)
    path = make_checkout(ctx.env_dir / "checkout")
    run_recorder.responses["rev-parse --verify -q"] = lambda cmd: fake_proc(stdout=SHA_OLD + "\n")
    run_recorder.responses["rev-parse HEAD"] = lambda cmd: fake_proc(stdout=SHA_OLD + "\n")
    run_git(config, ctx)
    assert not any(a[:4] == ["git", "-C", str(path), "checkout"] for a in run_recorder.argvs())


def test_different_commit_re_checks_out(make_context, run_recorder):
    config = config_for({"url": URL, "path": "checkout", "revision": "1.0"})
    ctx = make_context(config=config)
    path = make_checkout(ctx.env_dir / "checkout")
    run_recorder.responses["rev-parse --verify -q"] = lambda cmd: fake_proc(stdout=SHA_NEW + "\n")
    run_recorder.responses["rev-parse HEAD"] = lambda cmd: fake_proc(stdout=SHA_OLD + "\n")
    run_git(config, ctx)
    assert ["git", "-C", str(path), "checkout", "--detach", SHA_NEW] in run_recorder.argvs()


def test_revision_found_only_after_targeted_fetch(make_context, run_recorder):
    # 'fetch --tags' alone doesn't see a raw commit sha that isn't the tip of
    # any branch/tag -- resolves only once the second, targeted 'fetch
    # <remote> <revision>' has run (see _resolve_revision).
    config = config_for({"url": URL, "path": "checkout", "revision": "deadbeef"})
    ctx = make_context(config=config)
    path = make_checkout(ctx.env_dir / "checkout")
    calls = {"n": 0}

    def rev_parse_response(cmd):
        calls["n"] += 1
        return fake_proc(returncode=1) if calls["n"] == 1 else fake_proc(stdout=SHA_NEW + "\n")

    run_recorder.responses["rev-parse --verify -q"] = rev_parse_response
    run_git(config, ctx)
    argvs = run_recorder.argvs()
    assert ["git", "-C", str(path), "fetch", "origin", "deadbeef"] in argvs
    assert ["git", "-C", str(path), "checkout", "--detach", SHA_NEW] in argvs


def test_unresolvable_revision_dies(make_context, run_recorder):
    config = config_for({"url": URL, "path": "checkout", "revision": "nope"})
    ctx = make_context(config=config)
    make_checkout(ctx.env_dir / "checkout")
    with pytest.raises(SystemExit):
        run_git(config, ctx)


# ---- --fast ------------------------------------------------------------------#
def test_fast_dies_when_never_checked_out(make_context):
    config = config_for({"url": URL, "path": "checkout", "revision": "1.0"})
    ctx = make_context(config=config, fast=True)
    with pytest.raises(SystemExit):
        run_git(config, ctx)


def test_fast_skips_git_entirely_when_already_checked_out(make_context, run_recorder):
    config = config_for({"url": URL, "path": "checkout", "revision": "1.0"})
    ctx = make_context(config=config, fast=True)
    make_checkout(ctx.env_dir / "checkout")
    run_git(config, ctx)
    assert run_recorder.calls == []


# ---- submodules ---------------------------------------------------------------#
def test_submodules_true_runs_submodule_update(make_context, run_recorder):
    config = config_for({"url": URL, "path": "checkout", "revision": "1.0", "submodules": True})
    ctx = make_context(config=config)
    path = ctx.env_dir / "checkout"
    run_recorder.responses["rev-parse --verify -q"] = lambda cmd: fake_proc(stdout=SHA_NEW + "\n")
    run_git(config, ctx)
    argvs = run_recorder.argvs()
    assert ["git", "-C", str(path), "submodule", "sync"] in argvs
    assert ["git", "-C", str(path), "submodule", "update", "--init"] in argvs


def test_submodules_false_by_default(make_context, run_recorder):
    config = config_for({"url": URL, "path": "checkout", "revision": "1.0"})
    ctx = make_context(config=config)
    run_recorder.responses["rev-parse --verify -q"] = lambda cmd: fake_proc(stdout=SHA_NEW + "\n")
    run_git(config, ctx)
    # checked as a distinct argv token, not a substring of the whole joined
    # command: the tmp env dir pytest hands this test is itself named
    # 'test_submodules_false_by_default0', which contains "submodule" too.
    assert not any("submodule" in a for a in run_recorder.argvs())


# ---- --force -------------------------------------------------------------------#
def test_force_resets_and_cleans_before_checkout(make_context, run_recorder):
    config = config_for({"url": URL, "path": "checkout", "revision": "1.0"})
    ctx = make_context(config=config, force=True)
    path = make_checkout(ctx.env_dir / "checkout")
    run_recorder.responses["rev-parse --verify -q"] = lambda cmd: fake_proc(stdout=SHA_NEW + "\n")
    run_recorder.responses["rev-parse HEAD"] = lambda cmd: fake_proc(stdout=SHA_NEW + "\n")
    run_git(config, ctx)
    argvs = run_recorder.argvs()
    assert ["git", "-C", str(path), "reset", "--hard"] in argvs
    assert ["git", "-C", str(path), "clean", "-fdx"] in argvs


# ---- --dry-run -------------------------------------------------------------------#
def test_dry_run_never_clones_or_checks_out(make_context, run_recorder):
    config = config_for({"url": URL, "path": "checkout", "revision": "1.0"})
    ctx = make_context(config=config, dry_run=True)
    path = ctx.env_dir / "checkout"
    run_git(config, ctx)
    assert not any(a[:2] == ["git", "clone"] for a in run_recorder.argvs())
    assert not path.exists()
