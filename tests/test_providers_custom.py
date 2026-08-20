"""Tests for providers.custom.CustomProvider."""

import subprocess

import pytest

from denver_providers.custom import CustomProvider


def run_custom(config, ctx, stage="custom"):
    n = CustomProvider(config)
    n.stage = stage
    n.setup(ctx)
    return ctx, n


# ---- guard clauses --------------------------------------------------------#
def test_cmd_missing_dies(make_context):
    config = {"custom": {}}
    ctx = make_context(config=config)
    with pytest.raises(SystemExit):
        run_custom(config, ctx)


def test_cmd_not_a_string_dies(make_context):
    config = {"custom": {"cmd": ["echo", "hi"]}}
    ctx = make_context(config=config)
    with pytest.raises(SystemExit):
        run_custom(config, ctx)


def test_cmd_blank_string_dies(make_context):
    config = {"custom": {"cmd": "   "}}
    ctx = make_context(config=config)
    with pytest.raises(SystemExit):
        run_custom(config, ctx)


def test_neither_cmd_nor_source_dies(make_context):
    config = {"custom": {}}
    ctx = make_context(config=config)
    with pytest.raises(SystemExit):
        run_custom(config, ctx)


def test_source_not_a_string_dies(make_context):
    config = {"custom": {"source": ["a.sh"]}}
    ctx = make_context(config=config)
    with pytest.raises(SystemExit):
        run_custom(config, ctx)


def test_source_blank_string_dies(make_context):
    config = {"custom": {"source": "   "}}
    ctx = make_context(config=config)
    with pytest.raises(SystemExit):
        run_custom(config, ctx)


def test_source_missing_file_dies(make_context):
    config = {"custom": {"source": "nope.sh"}}
    ctx = make_context(config=config)
    with pytest.raises(SystemExit):
        run_custom(config, ctx)


# ---- banner text -- key names, not a generic 'run' -- --verbose only -------#
# banner() (the boxed per-sub-step marker) only prints under --verbose (see
# context.py's set_quiet/banner) -- these all opt in via make_context(verbose=True).
def test_cmd_banners_as_cmd_not_run(make_context, capsys):
    config = {"custom": {"cmd": "true"}}
    ctx = make_context(config=config, verbose=True)
    run_custom(config, ctx)
    err = capsys.readouterr().err
    assert "- cmd" in err
    assert "- run" not in err


def test_source_banners_as_source(make_context, capsys):
    config = {"custom": {"source": "vars.sh"}}
    ctx = make_context(config=config, verbose=True)
    (ctx.env_dir / "vars.sh").write_text("export MYVAR=1\n")
    run_custom(config, ctx)
    err = capsys.readouterr().err
    assert "- source" in err
    assert "- run" not in err


def test_cmd_and_source_each_get_their_own_banner_in_order(make_context, capsys):
    config = {"custom": {"cmd": "true", "source": "vars.sh"}}
    ctx = make_context(config=config, verbose=True)
    (ctx.env_dir / "vars.sh").write_text("export MYVAR=1\n")
    run_custom(config, ctx)
    err = capsys.readouterr().err
    assert err.index("- cmd") < err.index("- source")


# ---- --fast ------------------------------------------------------------------#
def test_fast_skips_running_cmd(make_context, tmp_path):
    marker = tmp_path / "marker"
    config = {"custom": {"cmd": f"touch {marker}"}}
    ctx = make_context(config=config, fast=True)
    run_custom(config, ctx)
    assert not marker.exists()


def test_fast_banners_cmd_as_skipped(make_context, capsys):
    config = {"custom": {"cmd": "true"}}
    ctx = make_context(config=config, fast=True, verbose=True)
    run_custom(config, ctx)
    err = capsys.readouterr().err
    assert "- cmd (skipped by --fast)" in err


def test_fast_still_validates_cmd(make_context):
    config = {"custom": {}}
    ctx = make_context(config=config, fast=True)
    with pytest.raises(SystemExit):
        run_custom(config, ctx)


def test_fast_still_runs_source(make_context):
    config = {"custom": {"source": "vars.sh"}}
    ctx = make_context(config=config, fast=True)
    (ctx.env_dir / "vars.sh").write_text("export MYVAR=1\n")
    run_custom(config, ctx)
    assert ctx.env["MYVAR"] == "1"


# ---- happy path ------------------------------------------------------------#
def test_cmd_runs_via_bash(make_context, tmp_path):
    marker = tmp_path / "marker"
    config = {"custom": {"cmd": f"touch {marker}"}}
    ctx = make_context(config=config)
    run_custom(config, ctx)
    assert marker.exists()


def test_cmd_supports_shell_syntax(make_context, tmp_path):
    marker = tmp_path / "marker"
    config = {"custom": {"cmd": f"echo one && echo two > {marker}"}}
    ctx = make_context(config=config)
    run_custom(config, ctx)
    assert marker.read_text() == "two\n"


def test_cmd_failure_propagates(make_context):
    config = {"custom": {"cmd": "exit 3"}}
    ctx = make_context(config=config)
    with pytest.raises(subprocess.CalledProcessError):
        run_custom(config, ctx)


def test_source_folds_exports_into_ctx_env(make_context):
    config = {"custom": {"cmd": "true", "source": "vars.sh"}}
    ctx = make_context(config=config)
    (ctx.env_dir / "vars.sh").write_text("export MYVAR=1\n")
    run_custom(config, ctx)
    assert ctx.env["MYVAR"] == "1"


# ---- info lines -- cmd/source/launcher each log what they're about to do --#
# Not gated by --verbose (unlike banner()/the '+cmd' echo): a stage combining
# several of these keys should still be able to tell, from plain default
# output, which one is actually running -- not just that a "cmd" step
# started, but what its own 'cmd:'/'source:'/'launcher:' value actually was.
def test_cmd_logs_its_own_value(make_context, caplog):
    caplog.set_level("INFO")
    config = {"custom": {"cmd": "true"}}
    ctx = make_context(config=config)
    run_custom(config, ctx)
    assert "custom[custom]: run cmd: true" in caplog.text


def test_source_logs_the_resolved_path(make_context, caplog):
    caplog.set_level("INFO")
    config = {"custom": {"source": "vars.sh"}}
    ctx = make_context(config=config)
    (ctx.env_dir / "vars.sh").write_text("export MYVAR=1\n")
    run_custom(config, ctx)
    assert f"custom[custom]: source {ctx.env_dir / 'vars.sh'}" in caplog.text


def test_launcher_logs_its_shell_split_tokens(make_context, caplog):
    caplog.set_level("INFO")
    config = {"custom": {"launcher": ["myscript.sh --name 'hello world'"]}}
    ctx = make_context(config=config)
    n = CustomProvider(config)
    n.stage = "custom"
    n.wrap(ctx, ["echo", "hi"])
    assert "custom[custom]: run launcher: myscript.sh --name 'hello world'" in caplog.text


def test_wrap_with_no_launcher_entries_logs_nothing(make_context, caplog):
    caplog.set_level("INFO")
    config = {"custom": {"cmd": "true"}}
    ctx = make_context(config=config)
    n = CustomProvider(config)
    n.stage = "custom"
    n.wrap(ctx, ["echo", "hi"])
    assert "launcher" not in caplog.text


def test_fast_skip_message_replaces_the_cmd_log_line(make_context, caplog):
    # --fast's own "skips '<cmd>'" message already says what 'cmd:' was --
    # the plain cmd-log line is for the real run only, not a second time here.
    caplog.set_level("INFO")
    config = {"custom": {"cmd": "true"}}
    ctx = make_context(config=config, fast=True)
    run_custom(config, ctx)
    assert "--fast skips 'true'" in caplog.text
    assert "custom[custom]: run cmd: true" not in caplog.text


def test_multiple_custom_stages_use_their_own_section(make_context, tmp_path):
    marker_a = tmp_path / "a"
    marker_b = tmp_path / "b"
    config = {
        "custom-a": {"cmd": f"touch {marker_a}"},
        "custom-b": {"cmd": f"touch {marker_b}"},
    }
    ctx = make_context(config=config)
    run_custom(config, ctx, stage="custom-a")
    run_custom(config, ctx, stage="custom-b")
    assert marker_a.exists()
    assert marker_b.exists()


# ---- launcher: -----------------------------------------------------------------#
def test_launcher_alone_is_valid(make_context):
    config = {"custom": {"launcher": ["myscript.sh --"]}}
    ctx = make_context(config=config)
    run_custom(config, ctx)  # must not raise -- 'launcher:' alone satisfies cmd/source/launcher


def test_launcher_not_a_list_dies(make_context):
    config = {"custom": {"launcher": "myscript.sh --"}}
    ctx = make_context(config=config)
    with pytest.raises(SystemExit):
        run_custom(config, ctx)


def test_launcher_non_string_entry_dies(make_context):
    config = {"custom": {"launcher": [["myscript.sh"]]}}
    ctx = make_context(config=config)
    with pytest.raises(SystemExit):
        run_custom(config, ctx)


def test_launcher_blank_entry_dies(make_context):
    config = {"custom": {"launcher": ["   "]}}
    ctx = make_context(config=config)
    with pytest.raises(SystemExit):
        run_custom(config, ctx)


def test_kind_is_wrapper_when_launcher_configured(make_context):
    config = {"custom": {"launcher": ["myscript.sh --"]}}
    n = CustomProvider(config)
    n.stage = "custom"
    assert n.kind == "wrapper"


def test_kind_is_wrapper_for_a_stage_id_other_than_the_provider_name(make_context):
    # regression test: make_stage() (denver_providers/__init__.py) always
    # constructs via cls(config) *then* sets provider.stage = stage_id --
    # self.stage is still 'custom' (Provider.__init__'s default) throughout
    # __init__ itself. A 'kind' computed once there, from self.config.get(
    # self.stage), would read the wrong (nonexistent) section for any stage
    # id other than literally 'custom' and always land on 'setup', even
    # with 'launcher:' configured -- exactly what make_stage()'s own
    # construct-then-assign order does for every real stage id.
    config = {"picotool": {"launcher": ["myscript.sh --"]}}
    n = CustomProvider(config)  # self.stage == 'custom' here, not yet 'picotool'
    n.stage = "picotool"  # what make_stage() does right after construction
    assert n.kind == "wrapper"


@pytest.mark.parametrize("launcher_value", [None, []], ids=["unset", "empty"])
def test_kind_is_setup_when_launcher_not_configured(make_context, launcher_value):
    config = {"custom": {"cmd": "true"}}
    if launcher_value is not None:
        config["custom"]["launcher"] = launcher_value
    n = CustomProvider(config)
    n.stage = "custom"
    assert n.kind == "setup"


def test_wrap_prepends_split_tokens_in_order(make_context):
    # the exact example from the feature request: each entry is split on
    # whitespace, entries concatenate in order, the real command lands last
    config = {"custom": {"launcher": ["myscript.sh --", "otherscript.sh --"]}}
    ctx = make_context(config=config)
    n = CustomProvider(config)
    n.stage = "custom"
    assert n.wrap(ctx, ["<cmd>"]) == ["myscript.sh", "--", "otherscript.sh", "--", "<cmd>"]


def test_wrap_splits_shell_style_keeping_quoted_args_together(make_context):
    config = {"custom": {"launcher": ["myscript.sh --name 'hello world'"]}}
    ctx = make_context(config=config)
    n = CustomProvider(config)
    n.stage = "custom"
    assert n.wrap(ctx, ["<cmd>"]) == ["myscript.sh", "--name", "hello world", "<cmd>"]


def test_wrap_with_no_launcher_entries_returns_cmd_unchanged(make_context):
    config = {"custom": {"cmd": "true"}}
    ctx = make_context(config=config)
    n = CustomProvider(config)
    n.stage = "custom"
    assert n.wrap(ctx, ["echo", "hi"]) == ["echo", "hi"]


def test_wrap_unaffected_by_fast(make_context):
    # relocating the command isn't a build step -- --fast never skips it
    config = {"custom": {"launcher": ["wrap.sh"]}}
    ctx = make_context(config=config, fast=True)
    n = CustomProvider(config)
    n.stage = "custom"
    assert n.wrap(ctx, ["echo", "hi"]) == ["wrap.sh", "echo", "hi"]


def test_launcher_and_cmd_both_run_independently(make_context, tmp_path):
    marker = tmp_path / "marker"
    config = {"custom": {"cmd": f"touch {marker}", "launcher": ["wrap.sh"]}}
    ctx = make_context(config=config)
    ctx, n = run_custom(config, ctx)
    assert marker.exists()
    assert n.wrap(ctx, ["echo", "hi"]) == ["wrap.sh", "echo", "hi"]
