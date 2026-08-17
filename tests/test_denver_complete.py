"""Tests for 'denver complete' and the hidden 'denver __complete' subcommand.

'complete [bash|fish|zsh]' prints that shell's wiring script (see
_COMPLETION_SCRIPTS in src/denver.py), auto-detecting the shell from the
parent process (_detect_shell) when none is given; '__complete' is what
each of those scripts' completion function actually shells out to on every
keystroke, and is deliberately bare-except-wrapped in _run_cli so a
completion request can never itself raise or die() into the user's
terminal mid-keystroke -- these tests drive both through denver.main() end
to end, the same way a real shell would. The explicit-shell tests below
pass a shell name rather than relying on auto-detection, which depends on
whatever process actually happens to be running the tests.
"""

from __future__ import annotations

import os
import subprocess

import pytest

import denver


# ---- 'denver complete <shell>' -- the wiring scripts ------------------------ #
def test_complete_bash_prints_the_bash_wiring_script(capsys):
    assert denver.main(["complete", "bash"]) == 0
    out = capsys.readouterr().out
    assert "_denver_complete" in out
    assert "complete -F" in out


def test_complete_zsh_prints_the_zsh_wiring_script(capsys):
    assert denver.main(["complete", "zsh"]) == 0
    out = capsys.readouterr().out
    assert "compdef" in out
    assert "compadd" in out


def test_complete_fish_prints_the_fish_wiring_script(capsys):
    assert denver.main(["complete", "fish"]) == 0
    out = capsys.readouterr().out
    assert "__denver_complete" in out
    assert "complete -c denver" in out


def test_complete_rejects_an_unknown_shell_name(capsys):
    with pytest.raises(SystemExit):
        denver.main(["complete", "tcsh"])
    assert "invalid choice" in capsys.readouterr().err


# ---- 'denver complete' with no shell -- auto-detected from the parent ------- #
@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_complete_with_no_shell_auto_detects_from_the_parent_process(monkeypatch, capsys, shell):
    monkeypatch.setattr(denver, "_parent_process_name", lambda: shell)
    assert denver.main(["complete"]) == 0
    assert capsys.readouterr().out == denver._COMPLETION_SCRIPTS[shell]


def test_detect_shell_reads_the_parent_processs_name(monkeypatch):
    monkeypatch.setattr(denver, "_parent_process_name", lambda: "zsh")
    assert denver._detect_shell() == "zsh"


def test_detect_shell_strips_the_login_shell_dash_prefix(monkeypatch):
    monkeypatch.setattr(denver, "_parent_process_name", lambda: "-zsh")
    assert denver._detect_shell() == "zsh"


def test_detect_shell_falls_back_to_the_shell_env_var(monkeypatch):
    monkeypatch.setattr(denver, "_parent_process_name", lambda: None)
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    assert denver._detect_shell() == "fish"


def test_detect_shell_falls_back_to_bash_when_nothing_is_recognised(monkeypatch):
    monkeypatch.setattr(denver, "_parent_process_name", lambda: "csh")
    monkeypatch.delenv("SHELL", raising=False)
    assert denver._detect_shell() == "bash"


# ---- 'denver __complete' -- top-level candidates ---------------------------- #
def test_dunder_complete_with_no_words_lists_top_level_subcommands_and_flags(capsys):
    assert denver.main(["__complete", ""]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "run" in out
    assert "complete" in out


# ---- 'denver __complete run <partial-path>' -- <env> positional ------------- #
def test_dunder_complete_completes_env_paths_from_the_current_directory(tmp_path, monkeypatch, capsys):
    (tmp_path / "myenv").mkdir()
    monkeypatch.chdir(tmp_path)

    assert denver.main(["__complete", "run", ""]) == 0
    out = capsys.readouterr().out
    assert "myenv/" in out


# ---- 'denver __complete run <env> --action <partial>' -- action names ------ #
def test_dunder_complete_completes_action_names_from_the_envs_own_scripts(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text(
        "stages: [fakesetup]\nfakesetup:\n  provider: fakesetup\n  scripts:\n    setup: [a.sh]\n    login: [b.sh]\n"
    )

    assert denver.main(["__complete", "run", str(env_dir), "--action", ""]) == 0
    out = capsys.readouterr().out
    assert "setup" in out
    assert "login" in out


# ---- past the forwarded-command '--' boundary ------------------------------- #
def test_dunder_complete_offers_nothing_past_the_forwarded_command_boundary(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: []\n")

    # the word actually being completed must be *after* the '--' for denver
    # to recognise it's past its own flags -- 'denver __complete run <env> --'
    # (with no further, currently-being-typed word) is not the same request
    # and does not exercise this boundary; the trailing '""' is load-bearing.
    assert denver.main(["__complete", "run", str(env_dir), "--", ""]) == 0
    assert capsys.readouterr().out == ""


# ---- robustness: must never raise, even for a nonexistent env -------------- #
def test_dunder_complete_is_a_no_op_for_a_nonexistent_env_rather_than_raising(capsys):
    assert denver.main(["__complete", "run", "/definitely/does/not/exist", "--action", ""]) == 0
    assert capsys.readouterr().out == ""


def test_complete_candidates_swallows_any_exception(monkeypatch):
    # _complete_candidates' own reason to exist: a completion request must
    # never raise, no matter what goes wrong computing the real answer.
    def boom(words):
        raise RuntimeError("boom")

    monkeypatch.setattr(denver, "_complete_candidates_unsafe", boom)
    assert denver._complete_candidates(["run", ""]) == []


# ---- 'denver __complete' with truly no words (not even a trailing "") ------ #
def test_dunder_complete_with_literally_no_words_lists_top_level_completions_unfiltered(capsys):
    assert denver.main(["__complete"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == denver._TOP_LEVEL_COMPLETIONS


# ---- 'denver __complete complete <TAB>' -- shell names ---------------------- #
def test_dunder_complete_completes_shell_names_after_the_complete_subcommand(capsys):
    assert denver.main(["__complete", "complete", ""]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["bash", "fish", "zsh"]


def test_dunder_complete_offers_nothing_once_complete_already_has_a_shell(capsys):
    assert denver.main(["__complete", "complete", "bash", ""]) == 0
    assert capsys.readouterr().out == ""


# ---- 'denver __complete <bogus>' -- an unrecognised subcommand ------------- #
def test_dunder_complete_offers_nothing_for_an_unrecognised_subcommand(capsys):
    assert denver.main(["__complete", "bogus", ""]) == 0
    assert capsys.readouterr().out == ""


# ---- 'denver __complete run <env> --until/--skip <partial>' -- stage ids --- #
def test_dunder_complete_completes_stage_ids_for_until_and_skip(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [fakesetup, docker]\nfakesetup:\n  provider: fakesetup\n")

    assert denver.main(["__complete", "run", str(env_dir), "--until", ""]) == 0
    assert set(capsys.readouterr().out.splitlines()) == {"fakesetup", "docker"}

    assert denver.main(["__complete", "run", str(env_dir), "--skip", "d"]) == 0
    assert capsys.readouterr().out.splitlines() == ["docker"]


def test_dunder_complete_stage_ids_empty_without_an_env_yet(capsys):
    # --until given before <env> -- env_value is None, so there's nothing to
    # read 'stages:' from yet (see _completion_env_paths' own empty check).
    assert denver.main(["__complete", "run", "--until", ""]) == 0
    assert capsys.readouterr().out == ""


# ---- 'denver __complete run <env> -e/--env <partial>' -- env var names ----- #
def test_dunder_complete_completes_environment_variable_names(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DENVER_TEST_COMPLETION_VAR", "1")
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: []\n")

    assert denver.main(["__complete", "run", str(env_dir), "-e", "DENVER_TEST_COMPLETION"]) == 0
    assert "DENVER_TEST_COMPLETION_VAR" in capsys.readouterr().out.splitlines()


# ---- 'denver __complete run <env> -c <partial>' -- no dynamic completion --- #
def test_dunder_complete_offers_nothing_for_a_flag_with_no_dynamic_completion(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: []\n")

    assert denver.main(["__complete", "run", str(env_dir), "-c", ""]) == 0
    assert capsys.readouterr().out == ""


# ---- 'denver __complete run <env> --<TAB>' -- denver's own + declared flags  #
def test_dunder_complete_completes_flags_including_the_envs_own_declared_ones(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text(
        "stages: []\n"
        "args:\n"
        "- flags: --board\n"
        "  default: x\n"
        "- flags: [--release, -r]\n"
        "  action: store_true\n"
        "- justastring\n"  # malformed (not a mapping) -- ignored, not an error
        "- help: no 'flags:' key at all\n"  # malformed (no 'flags:') -- ignored too
    )

    assert denver.main(["__complete", "run", str(env_dir), "--b"]) == 0
    assert capsys.readouterr().out.splitlines() == ["--board"]

    assert denver.main(["__complete", "run", str(env_dir), "-"]) == 0
    out = set(capsys.readouterr().out.splitlines())
    assert {"--board", "--release", "-r", "--action", "--show-config"} <= out


def test_dunder_complete_flags_without_any_declared_args_are_just_denvers_own(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: []\n")  # no 'args:' key at all

    assert denver.main(["__complete", "run", str(env_dir), "--sh"]) == 0
    assert capsys.readouterr().out.splitlines() == ["--show-config"]


# ---- 'denver __complete run <env> <extra positional>' -- nothing to offer -- #
def test_dunder_complete_offers_nothing_for_a_second_plain_positional(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: []\n")

    assert denver.main(["__complete", "run", str(env_dir), "somethingelse"]) == 0
    assert capsys.readouterr().out == ""


# ---- 'denver __complete run <path-to-a-denver.yml-file> ...' -- direct file  #
def test_dunder_complete_accepts_env_given_as_a_direct_config_file_path(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [fakesetup]\nfakesetup:\n  provider: fakesetup\n")

    assert denver.main(["__complete", "run", str(env_dir / "denver.yml"), "--until", ""]) == 0
    assert capsys.readouterr().out.splitlines() == ["fakesetup"]


# ---- 'denver __complete run <partial-dir>/<partial-name>' -- nested paths -- #
def test_dunder_complete_completes_paths_inside_an_already_typed_directory(tmp_path, monkeypatch, capsys):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "denver.yml").write_text("stages: []\n")
    (sub / "readme.txt").write_text("not a denver config or a directory\n")
    monkeypatch.chdir(tmp_path)

    assert denver.main(["__complete", "run", "sub/"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["sub/denver.yml"]  # readme.txt is neither a dir nor a denver config -- excluded


def test_dunder_complete_path_candidates_empty_for_a_nonexistent_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert denver.main(["__complete", "run", "no-such-dir/x"]) == 0
    assert capsys.readouterr().out == ""


# ---- _completion_wrapped_shell -- the empty-cmd guard ----------------------- #
def test_completion_wrapped_shell_leaves_an_empty_cmd_untouched():
    assert denver._completion_wrapped_shell([]) == []


# ---- _parent_process_name --------------------------------------------------- #
def test_parent_process_name_reads_the_real_proc_entry():
    # our own test process really does have a parent -- no mocking needed to
    # exercise the primary (Linux /proc) path.
    assert denver._parent_process_name()


def test_parent_process_name_falls_back_to_ps_when_proc_is_unreadable(monkeypatch):
    monkeypatch.setattr(os, "getppid", lambda: 2**30)  # no such /proc entry
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(args=[], returncode=0, stdout="zsh\n")
    )
    assert denver._parent_process_name() == "zsh"


def test_parent_process_name_none_when_ps_is_not_available(monkeypatch):
    monkeypatch.setattr(os, "getppid", lambda: 2**30)

    def raise_oserror(*a, **kw):
        raise OSError

    monkeypatch.setattr(subprocess, "run", raise_oserror)
    assert denver._parent_process_name() is None


def test_parent_process_name_none_when_ps_exits_nonzero(monkeypatch):
    monkeypatch.setattr(os, "getppid", lambda: 2**30)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(args=[], returncode=1, stdout="")
    )
    assert denver._parent_process_name() is None


def test_parent_process_name_none_when_getppid_itself_fails(monkeypatch):
    def raise_oserror():
        raise OSError

    monkeypatch.setattr(os, "getppid", raise_oserror)
    assert denver._parent_process_name() is None
