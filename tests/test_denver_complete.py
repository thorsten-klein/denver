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
        "stages: [fakesetup]\n"
        "fakesetup:\n"
        "  provider: fakesetup\n"
        "  scripts:\n"
        "    setup: [a.sh]\n"
        "    login: [b.sh]\n"
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
