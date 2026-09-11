"""Tests for providers.nix.NixProvider.

Activation of the *generic* per-stage keys (env:/env-prepend:/env-append:)
is not this provider's own concern -- that's exercised in
test_denver_orchestration.py. What is tested here is the devShell
environment itself: how it is keyed, cached, sourced, and what --fast /
--force / --dry-run do to it.
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

import pytest

from denver_errors import DenverError
from denver_providers.nix import NixProvider, install_instructions, parsed_version

STAGE = "devshell"
# the directory holding a real bash: ctx.source() runs one for real (the
# conftest recorder passes 'bash -c' through), and it is resolved against
# *ctx.env's* PATH -- so a test that replaces PATH wholesale has to leave a
# directory that really has bash on it. /usr/bin on Linux, /bin on macOS:
# looked up rather than guessed, at import time, before the 'which' fixture
# patches shutil.which out.
BASH_DIR = str(Path(shutil.which("bash") or "/bin/bash").parent)
DEVSHELL_ENV = "export NIX_MARKER=1\nexport PATH=/nix/bin\n"


class FakeProc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def config_for(stage_cfg, stage=STAGE):
    return {stage: {"provider": "nix", **stage_cfg}}


def prepare(config, ctx, stage=STAGE):
    """Resolve this stage's defaults the way denver.py does, and return its provider."""
    provider = NixProvider(config)
    provider.stage = stage
    config[stage] = NixProvider.resolve_defaults(ctx, config.get(stage) or {}, config)
    return provider


def run_nix(config, ctx, stage=STAGE):
    provider = prepare(config, ctx, stage)
    provider.setup(ctx)
    return provider


def cache_file_for(config, ctx, stage=STAGE):
    provider = prepare(config, ctx, stage)
    return provider._cache_file(ctx, config[stage])


def resolved(ctx, entry):
    return NixProvider.resolve_defaults(ctx, entry, {})


def write_devshell_env(path, text=DEVSHELL_ENV):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def print_dev_env(text=DEVSHELL_ENV):
    """A `bash -c 'nix ... > <tmp>'` stand-in that writes ``text`` where nix's stdout would have gone."""

    def _respond(cmd):
        target = shlex.split(cmd[2])[-1]
        Path(target).write_text(text)
        return FakeProc()

    return _respond


@pytest.fixture
def nix_recorder(run_recorder, which):
    """A run_recorder with `nix print-dev-env` and `nix --version` already answered, and nix on PATH."""
    run_recorder.responses["print-dev-env"] = print_dev_env()
    run_recorder.responses["--version"] = FakeProc(stdout="nix (Nix) 2.35.2\n")
    return run_recorder


def nix_argvs(recorder):
    return [a for a in recorder.argvs() if "print-dev-env" in " ".join(a)]


def nix_script(recorder):
    """The `nix print-dev-env ...` command line, as tokens, out of the `bash -c '... > <tmp>'` wrapper."""
    return shlex.split(nix_argvs(recorder)[0][2])


def installable(recorder):
    """The installable that `nix print-dev-env` was given (the token right before the redirect)."""
    script = nix_script(recorder)
    return script[script.index(">") - 1]


# ---- config defaults ---------------------------------------------------------#
def test_defaults_fill_every_key(make_context):
    ctx = make_context()
    cfg = resolved(ctx, {})
    assert set(cfg) == set(NixProvider.KEYS)
    assert cfg["exe"] == "nix"
    assert cfg["flake"] == str(ctx.env_dir)
    assert cfg["root"] is None
    assert cfg["shell"] is None
    assert cfg["args"] == []
    assert cfg["impure"] is False
    assert cfg["experimental-features"] == "nix-command flakes"
    assert cfg["expected-version"] is None
    assert cfg["cache"] is True
    assert cfg["keep-path"] is True
    assert cfg["shell-hook"] is True


def test_flake_path_is_resolved_against_env_dir(make_context):
    ctx = make_context()
    cfg = resolved(ctx, {"flake": "nix-env", "root": "."})
    assert cfg["flake"] == str(ctx.env_dir / "nix-env")
    assert cfg["root"] == str(ctx.env_dir)


def test_flake_reference_is_left_verbatim(make_context):
    ctx = make_context()
    cfg = resolved(ctx, {"flake": "github:owner/repo"})
    assert cfg["flake"] == "github:owner/repo"


def test_flake_is_interpolated(make_context):
    ctx = make_context(env={"WHICH": "nix-env"})
    cfg = resolved(ctx, {"flake": "${WHICH}"})
    assert cfg["flake"] == str(ctx.env_dir / "nix-env")


def test_empty_experimental_features_is_kept(make_context):
    ctx = make_context()
    assert resolved(ctx, {"experimental-features": ""})["experimental-features"] == ""


def test_args_are_stringified(make_context):
    ctx = make_context()
    assert resolved(ctx, {"args": ["--option", "sandbox"]})["args"] == ["--option", "sandbox"]


# ---- config validation -------------------------------------------------------#
@pytest.mark.parametrize("key", ["exe", "flake", "root", "shell", "experimental-features", "expected-version"])
def test_string_key_must_be_a_string(make_context, key):
    ctx = make_context()
    with pytest.raises(DenverError):
        resolved(ctx, {key: ["not", "a", "string"]})


@pytest.mark.parametrize("key", ["impure", "cache", "keep-path", "shell-hook"])
def test_bool_key_must_be_a_bool(make_context, key):
    ctx = make_context()
    with pytest.raises(DenverError):
        resolved(ctx, {key: "yes"})


@pytest.mark.parametrize("args", ["--impure", [1, 2]])
def test_args_must_be_a_list_of_strings(make_context, args):
    ctx = make_context()
    with pytest.raises(DenverError):
        resolved(ctx, {"args": args})


# ---- the flake reference nix is given ----------------------------------------#
def test_plain_path_becomes_a_path_reference(make_context, nix_recorder):
    config = config_for({"flake": "nix-env"})
    ctx = make_context(config=config)
    run_nix(config, ctx)
    assert installable(nix_recorder) == f"path:{ctx.env_dir / 'nix-env'}"


def test_root_becomes_a_dir_query(make_context, nix_recorder):
    config = config_for({"flake": "nix-env", "root": ".", "shell": "default"})
    ctx = make_context(config=config)
    run_nix(config, ctx)
    assert installable(nix_recorder) == f"path:{ctx.env_dir}?dir=nix-env#default"


def test_root_equal_to_flake_has_no_dir_query(make_context, nix_recorder):
    config = config_for({"flake": ".", "root": "."})
    ctx = make_context(config=config)
    run_nix(config, ctx)
    assert installable(nix_recorder) == f"path:{ctx.env_dir}"


def test_flake_outside_root_dies(make_context, nix_recorder, tmp_path):
    config = config_for({"flake": str(tmp_path / "elsewhere"), "root": "."})
    ctx = make_context(config=config)
    with pytest.raises(DenverError):
        run_nix(config, ctx)


def test_flake_reference_is_passed_through_untouched(make_context, nix_recorder):
    config = config_for({"flake": "github:owner/repo", "shell": "ci"})
    ctx = make_context(config=config)
    run_nix(config, ctx)
    assert installable(nix_recorder) == "github:owner/repo#ci"


# ---- the nix command ---------------------------------------------------------#
def test_print_dev_env_command(make_context, nix_recorder):
    config = config_for({"args": ["--option", "sandbox", "false"], "impure": True})
    ctx = make_context(config=config)
    run_nix(config, ctx)
    assert nix_argvs(nix_recorder)[0][:2] == ["bash", "-c"]
    script = nix_script(nix_recorder)
    assert script[:4] == ["/usr/bin/nix", "--extra-experimental-features", "nix-command flakes", "print-dev-env"]
    assert script[4:8] == ["--impure", "--option", "sandbox", "false"]


def test_empty_experimental_features_drops_the_flag(make_context, nix_recorder):
    config = config_for({"experimental-features": ""})
    ctx = make_context(config=config)
    run_nix(config, ctx)
    assert "--extra-experimental-features" not in nix_argvs(nix_recorder)[0][2]


def test_quiet_and_verbose_reach_nix(make_context, nix_recorder):
    config = config_for({})
    ctx = make_context(config=config, quiet=1, verbose=True)
    run_nix(config, ctx)
    script = nix_argvs(nix_recorder)[0][2]
    assert "--quiet" in script
    assert "--print-build-logs" in script


def test_stdout_is_redirected_into_the_cache(make_context, nix_recorder):
    config = config_for({})
    ctx = make_context(config=config)
    cache_file = cache_file_for(config, ctx)
    run_nix(config, ctx)
    assert cache_file.is_file()
    assert cache_file.read_text() == DEVSHELL_ENV
    assert not cache_file.with_suffix(".sh.tmp").exists()


# ---- nix itself --------------------------------------------------------------#
def test_missing_nix_dies(make_context, nix_recorder, which):
    which["nix"] = None
    config = config_for({})
    ctx = make_context(config=config)
    with pytest.raises(DenverError):
        run_nix(config, ctx)


def test_custom_exe_is_used(make_context, nix_recorder):
    config = config_for({"exe": "nix-2.35"})
    ctx = make_context(config=config)
    run_nix(config, ctx)
    assert "/usr/bin/nix-2.35" in nix_argvs(nix_recorder)[0][2]


def test_no_expected_version_asks_nix_for_none(make_context, nix_recorder):
    config = config_for({})
    ctx = make_context(config=config)
    run_nix(config, ctx)
    assert not any(a[-1] == "--version" for a in nix_recorder.argvs())


def test_matching_version_does_not_warn(make_context, nix_recorder, caplog):
    config = config_for({"expected-version": "2.35.2"})
    ctx = make_context(config=config)
    run_nix(config, ctx)
    assert "expected" not in caplog.text


@pytest.mark.parametrize("reported", ["nix (Nix) 2.30.0\n", ""])
def test_other_version_warns(make_context, nix_recorder, caplog, reported):
    nix_recorder.responses["--version"] = FakeProc(stdout=reported)
    config = config_for({"expected-version": "2.35.2"})
    ctx = make_context(config=config)
    run_nix(config, ctx)
    assert "expected 2.35.2" in caplog.text


def test_install_instructions_without_a_pinned_version():
    assert "nix-" not in install_instructions(None)
    assert "nix-2.35.2" in install_instructions("2.35.2")


# ---- the cache ---------------------------------------------------------------#
def test_existing_cache_is_reused(make_context, nix_recorder):
    config = config_for({})
    ctx = make_context(config=config)
    write_devshell_env(cache_file_for(config, ctx))
    run_nix(config, ctx)
    assert nix_argvs(nix_recorder) == []


def test_force_refreshes_the_cache(make_context, nix_recorder):
    config = config_for({})
    ctx = make_context(config=config, force=True)
    write_devshell_env(cache_file_for(config, ctx), "export NIX_MARKER=stale\n")
    run_nix(config, ctx)
    assert nix_argvs(nix_recorder) != []
    assert ctx.env["NIX_MARKER"] == "1"


def test_cache_false_refreshes_every_run(make_context, nix_recorder):
    config = config_for({"cache": False})
    ctx = make_context(config=config)
    write_devshell_env(cache_file_for(config, ctx), "export NIX_MARKER=stale\n")
    run_nix(config, ctx)
    assert nix_argvs(nix_recorder) != []


def test_cache_key_covers_the_installable(make_context):
    ctx = make_context()
    config_a = config_for({"shell": "default"})
    config_b = config_for({"shell": "ci"})
    assert cache_file_for(config_a, ctx) != cache_file_for(config_b, ctx)


def test_cache_key_covers_tracked_file_content(make_context, run_recorder):
    config = config_for({})
    ctx = make_context(config=config)
    flake = ctx.env_dir / "flake.nix"
    run_recorder.responses["ls-files"] = FakeProc(stdout="flake.nix\0")
    flake.write_text("{ }\n")
    before = cache_file_for(config, ctx)
    flake.write_text("{ inputs = { }; }\n")
    assert cache_file_for(config, ctx) != before


def test_cache_key_of_a_remote_flake_needs_no_files(make_context, run_recorder):
    config = config_for({"flake": "github:owner/repo"})
    ctx = make_context(config=config)
    assert cache_file_for(config, ctx).is_absolute()
    assert not any("ls-files" in c for c in run_recorder.commands())


# ---- activation --------------------------------------------------------------#
def test_devshell_env_is_sourced_into_the_environment(make_context, nix_recorder):
    config = config_for({})
    ctx = make_context(config=config)
    run_nix(config, ctx)
    assert ctx.env["NIX_MARKER"] == "1"


def test_keep_path_re_appends_dropped_entries(make_context, nix_recorder):
    config = config_for({})
    ctx = make_context(config=config)
    ctx.env["PATH"] = f"/venv/bin{os.pathsep}{BASH_DIR}"
    run_nix(config, ctx)
    assert ctx.env["PATH"] == f"/nix/bin{os.pathsep}/venv/bin{os.pathsep}{BASH_DIR}"


def test_keep_path_false_leaves_the_dev_shell_path_alone(make_context, nix_recorder):
    config = config_for({"keep-path": False})
    ctx = make_context(config=config)
    # BASH_DIR stays on PATH only so the bash ctx.source() itself runs is findable
    ctx.env["PATH"] = f"/venv/bin{os.pathsep}{BASH_DIR}"
    run_nix(config, ctx)
    assert ctx.env["PATH"] == "/nix/bin"


# what a current nix really emits: the hook as a variable, then the eval of it
# that `nix develop` itself would run (see _SHELL_HOOK_LINE_RE).
HOOK_DEVSHELL_ENV = 'shellHook="export HOOKED=yes"\neval "${shellHook:-}"\n'


def test_shell_hook_runs_by_default(make_context, nix_recorder):
    nix_recorder.responses["print-dev-env"] = print_dev_env(HOOK_DEVSHELL_ENV)
    config = config_for({})
    ctx = make_context(config=config)
    run_nix(config, ctx)
    assert ctx.env["HOOKED"] == "yes"


def test_shell_hook_false_strips_nix_own_eval(make_context, nix_recorder):
    nix_recorder.responses["print-dev-env"] = print_dev_env(HOOK_DEVSHELL_ENV)
    config = config_for({"shell-hook": False})
    ctx = make_context(config=config)
    cache_file = cache_file_for(config, ctx)
    run_nix(config, ctx)
    assert "HOOKED" not in ctx.env
    assert "eval" not in cache_file.read_text()


def test_shell_hook_setting_is_part_of_the_cache_key(make_context):
    ctx = make_context()
    assert cache_file_for(config_for({}), ctx) != cache_file_for(config_for({"shell-hook": False}), ctx)


# ---- --fast ------------------------------------------------------------------#
def test_fast_dies_without_a_cached_devshell_env(make_context, nix_recorder):
    config = config_for({})
    ctx = make_context(config=config, fast=True)
    with pytest.raises(DenverError):
        run_nix(config, ctx)


def test_fast_sources_the_cache_without_calling_nix(make_context, nix_recorder):
    config = config_for({})
    ctx = make_context(config=config, fast=True)
    write_devshell_env(cache_file_for(config, ctx))
    run_nix(config, ctx)
    assert nix_argvs(nix_recorder) == []
    assert ctx.env["NIX_MARKER"] == "1"


# ---- --dry-run ---------------------------------------------------------------#
def test_dry_run_does_not_evaluate_the_flake(make_context, nix_recorder, capsys):
    config = config_for({})
    ctx = make_context(config=config, dry_run=True)
    cache_file = cache_file_for(config, ctx)
    run_nix(config, ctx)
    assert nix_argvs(nix_recorder) == []
    assert not cache_file.exists()
    err = capsys.readouterr().err
    assert "print-dev-env" in err
    assert "not built in a preview" in err


def test_dry_run_sources_an_existing_cache(make_context, nix_recorder):
    config = config_for({})
    ctx = make_context(config=config, dry_run=True)
    write_devshell_env(cache_file_for(config, ctx))
    run_nix(config, ctx)
    assert ctx.env["NIX_MARKER"] == "1"


# ---- path safety -------------------------------------------------------------#
def test_stage_id_with_a_path_separator_dies(make_context, nix_recorder):
    # the stage id names the cache file, so one containing '/' or '..' would
    # otherwise write outside the env's own state directory
    config = config_for({}, stage="../escape")
    ctx = make_context(config=config)
    with pytest.raises(DenverError):
        run_nix(config, ctx, stage="../escape")


# ---- version parsing ---------------------------------------------------------#
@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("nix (Nix) 2.35.1\n", "2.35.1"),
        ("nix (Nix) 2.18\n", "2.18"),
        ("nix (Nix) 2.35.1-rc1\n", "2.35.1"),
        ("", None),
        ("nix: command not found\n", None),
        ("nix (Nix) 7\n", None),
    ],
)
def test_parsed_version(output, expected):
    assert parsed_version(output) == expected
