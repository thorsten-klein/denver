"""Tests for 'denver complete' and the hidden 'denver __complete' subcommand.

'complete [bash|fish|zsh]' prints that shell's wiring script (see
_completion_script in src/denver.py), wired up to every command word
_completion_bind_names comes up with, auto-detecting the shell from the
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
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

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
    assert capsys.readouterr().out == denver._completion_script(shell, denver._completion_bind_names())


# ---- _completion_bind_names -- which command words get wired up ------------- #
def test_completion_bind_names_always_starts_with_denver():
    assert denver._completion_bind_names()[0] == "denver"


def test_completion_bind_names_includes_the_absolute_path_to_denver_py():
    assert str(Path(denver.__file__).resolve()) in denver._completion_bind_names()


def test_completion_bind_names_includes_however_it_was_actually_invoked(monkeypatch):
    monkeypatch.setattr(denver.sys, "argv", ["./src/denver.py", "complete"])
    assert "./src/denver.py" in denver._completion_bind_names()


def test_completion_bind_names_dedupes_argv0_against_denver(monkeypatch):
    monkeypatch.setattr(denver.sys, "argv", ["denver", "complete"])
    assert denver._completion_bind_names().count("denver") == 1


def test_completion_bind_names_dedupes_argv0_against_the_absolute_path(monkeypatch):
    monkeypatch.setattr(denver.sys, "argv", [denver._denver_launcher()[-1], "complete"])
    names = denver._completion_bind_names()
    assert names.count(denver._denver_launcher()[-1]) == 1


# ---- _completion_script -- wiring every bound name, self-invoking ----------- #
def test_completion_script_bash_registers_every_name_and_reinvokes_via_comp_words_0():
    out = denver._completion_script("bash", ["denver", "./src/denver.py"])
    assert "complete -F _denver_complete -o default -o bashdefault denver ./src/denver.py" in out
    assert "local cmd=${COMP_WORDS[0]}" in out
    assert '"${resolved[@]}" __complete' in out
    assert "denver __complete" not in out  # never hardcoded -- see COMP_WORDS[0] above


def test_completion_script_bash_resolves_the_typed_word_through_bash_aliases():
    out = denver._completion_script("bash", ["denver"])
    assert "BASH_ALIASES[$cmd]" in out  # e.g. `alias denver=/path/to/denver.py`


def test_completion_script_zsh_registers_every_name_and_reinvokes_via_words_1():
    out = denver._completion_script("zsh", ["denver", "./src/denver.py"])
    assert "compdef _denver_complete denver ./src/denver.py" in out
    assert "local cmd=${words[1]}" in out
    assert '"${resolved[@]}" __complete' in out
    assert "denver __complete" not in out


def test_completion_script_zsh_resolves_the_typed_word_through_zsh_aliases():
    out = denver._completion_script("zsh", ["denver"])
    assert "aliases[$cmd]" in out  # e.g. `alias denver=/path/to/denver.py`


# ---- unquoted `eval $(denver complete)` -- see _completion_script's docstring ---- #
# Command substitution word-splits on IFS when it's not inside double quotes,
# collapsing every newline in the printed script down to a plain space before eval
# ever runs it -- so the script must still be one valid (';'-separated) line even
# after that happens. `eval "$(...)"` (quoted) never hits this; these drive the
# unquoted form specifically, through a real shell, to catch a regression no amount
# of string-matching on _completion_script's output would.
def _run_unquoted_eval(shell, extra_setup=""):
    if not shutil.which(shell):
        pytest.skip(f"{shell} not installed")
    cmd = f"{shlex.quote(sys.executable)} {shlex.quote(denver.__file__)} complete {shell}"
    script = f"{extra_setup}eval $({cmd})\n"
    return subprocess.run([shell, "-c", script + "type _denver_complete"], capture_output=True, text=True)


def test_bash_completion_function_defines_via_unquoted_eval():
    result = _run_unquoted_eval("bash")
    assert "is a function" in result.stdout, result.stderr


def test_zsh_completion_function_defines_via_unquoted_eval():
    result = _run_unquoted_eval("zsh", extra_setup="autoload -Uz compinit; compinit -u\n")
    assert result.returncode == 0, result.stderr


def test_zsh_completion_passes_each_word_as_its_own_positional_arg():
    # Regression test: "${words[2,CURRENT]}" (no '(@)' flag) quotes an array
    # slice the same way zsh quotes a scalar -- it collapses into one
    # IFS-joined string, so __complete would see a single mangled argument
    # like "run --show" instead of two ("run", "--show") and match nothing.
    # Driven through a real zsh (not string-matching on the script text),
    # the same way the unquoted-eval tests above are, because that's the
    # only thing that actually exercises zsh's own quoting rules.
    if not shutil.which("zsh"):
        pytest.skip("zsh not installed")
    cmd = f"{shlex.quote(sys.executable)} {shlex.quote(denver.__file__)}"
    script = (
        f"alias denver={shlex.quote(cmd)}\n"
        f"eval $({cmd} complete zsh)\n"
        "autoload -Uz compinit; compinit -u\n"
        "words=(denver run --show); CURRENT=3\n"
        'compadd() { print -r -- "$@"; }\n'
        "_denver_complete\n"
    )
    result = subprocess.run(["zsh", "-c", script], capture_output=True, text=True)
    assert "--show-config" in result.stdout, result.stderr
    assert "--show-config-min" in result.stdout, result.stderr


def test_completion_script_fish_registers_one_complete_c_line_per_bare_name():
    out = denver._completion_script("fish", ["denver", "./src/denver.py"])
    assert "complete -c denver -f -a '(__denver_complete)'" in out
    assert "$tokens[1] __complete" in out
    assert "denver __complete" not in out


def test_completion_script_fish_registers_path_names_with_dash_p_not_dash_c():
    # '-c' only ever matches a bare command name -- a path needs '-p', or fish
    # never matches it against anything typed at the prompt (checkout invocations,
    # e.g. './src/denver.py', are always a path).
    out = denver._completion_script("fish", ["denver", "./src/denver.py"])
    assert "complete -p ./src/denver.py -f -a '(__denver_complete)'" in out
    assert "complete -c ./src/denver.py" not in out


def test_completion_script_fish_suppresses_filenames_except_for_path_flags():
    # '-f' keeps denver's own candidates from being drowned out by every file in the
    # cwd; a second, '-n'-gated entry forces filenames back on ('-F') only right
    # after a flag that takes an arbitrary path (-c/--config/-cf/--config-file),
    # since __complete has no sensible dynamic completion for those (see
    # _pending_flag_value_candidates).
    out = denver._completion_script("fish", ["denver"])
    assert "function __denver_expects_path" in out
    assert "contains -- $prev -c --config -cf --config-file" in out
    assert "complete -c denver -n __denver_expects_path -F -a '(__denver_complete)'" in out


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


# ---- 'denver __complete run <env> --scripts <partial>' -- action names ------ #
def test_dunder_complete_completes_script_names_from_the_envs_own_scripts(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text(
        'stages = [\n  "fakesetup",\n]\n\n[fakesetup]\nprovider = "fakesetup"\n\n[fakesetup.scripts]\nsetup = [\n  "a.sh",\n]\nlogin = [\n  "b.sh",\n]\n'
    )

    assert denver.main(["__complete", "run", str(env_dir), "--scripts", ""]) == 0
    out = capsys.readouterr().out
    assert "setup" in out
    assert "login" in out


# ---- past the forwarded-command '--' boundary ------------------------------- #
def test_dunder_complete_offers_nothing_past_the_forwarded_command_boundary(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text('stages = []\n')

    # the word actually being completed must be *after* the '--' for denver
    # to recognise it's past its own flags -- 'denver __complete run <env> --'
    # (with no further, currently-being-typed word) is not the same request
    # and does not exercise this boundary; the trailing '""' is load-bearing.
    assert denver.main(["__complete", "run", str(env_dir), "--", ""]) == 0
    assert capsys.readouterr().out == ""


# ---- robustness: must never raise, even for a nonexistent env -------------- #
def test_dunder_complete_is_a_no_op_for_a_nonexistent_env_rather_than_raising(capsys):
    assert denver.main(["__complete", "run", "/definitely/does/not/exist", "--scripts", ""]) == 0
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
    (env_dir / "denver.toml").write_text(
        'stages = [\n  "fakesetup",\n  "docker",\n]\n\n[fakesetup]\nprovider = "fakesetup"\n'
    )

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
    (env_dir / "denver.toml").write_text('stages = []\n')

    assert denver.main(["__complete", "run", str(env_dir), "-e", "DENVER_TEST_COMPLETION"]) == 0
    assert "DENVER_TEST_COMPLETION_VAR" in capsys.readouterr().out.splitlines()


# ---- 'denver __complete run <env> -c <partial>' -- no dynamic completion --- #
def test_dunder_complete_offers_nothing_for_a_flag_with_no_dynamic_completion(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text('stages = []\n')

    assert denver.main(["__complete", "run", str(env_dir), "-c", ""]) == 0
    assert capsys.readouterr().out == ""


# ---- 'denver __complete run <env> --<TAB>' -- denver's own + declared flags  #
def test_dunder_complete_completes_flags_including_the_envs_own_declared_ones(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text(
        'stages = []\nargs = [\n  { flags = "--board", default = "x" },\n  { flags = [\n  "--release",\n  "-r",\n], action = "store_true" },\n  "justastring",\n  { help = "no \'flags:\' key at all" },\n]\n'  # malformed (no 'flags:') -- ignored too
    )

    assert denver.main(["__complete", "run", str(env_dir), "--b"]) == 0
    assert capsys.readouterr().out.splitlines() == ["--board"]

    assert denver.main(["__complete", "run", str(env_dir), "-"]) == 0
    out = set(capsys.readouterr().out.splitlines())
    assert {"--board", "--release", "-r", "--scripts", "--show-config"} <= out


def test_dunder_complete_flags_without_any_declared_args_are_just_denvers_own(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text('stages = []\n')  # no 'args:' key at all

    assert denver.main(["__complete", "run", str(env_dir), "--sh"]) == 0
    assert capsys.readouterr().out.splitlines() == ["--show-config", "--show-config-min"]


# ---- 'denver __complete run <env> ' -- flags offered before typing '-' ----- #
def test_dunder_complete_offers_flags_right_after_env_even_before_a_dash(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text('stages = []\n')

    assert denver.main(["__complete", "run", str(env_dir), ""]) == 0
    out = set(capsys.readouterr().out.splitlines())
    assert {"--scripts", "--show-config", "-c", "--config"} <= out


# ---- 'denver __complete run <env> <extra positional>' -- nothing to offer -- #
def test_dunder_complete_offers_nothing_for_a_second_plain_positional(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text('stages = []\n')

    assert denver.main(["__complete", "run", str(env_dir), "somethingelse"]) == 0
    assert capsys.readouterr().out == ""


# ---- 'denver __complete run <path-to-a-denver.toml-file> ...' -- direct file  #
def test_dunder_complete_accepts_env_given_as_a_direct_config_file_path(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text('stages = [\n  "fakesetup",\n]\n\n[fakesetup]\nprovider = "fakesetup"\n')

    assert denver.main(["__complete", "run", str(env_dir / "denver.toml"), "--until", ""]) == 0
    assert capsys.readouterr().out.splitlines() == ["fakesetup"]


# ---- 'denver __complete run <partial-dir>/<partial-name>' -- nested paths -- #
def test_dunder_complete_completes_paths_inside_an_already_typed_directory(tmp_path, monkeypatch, capsys):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "denver.toml").write_text('stages = []\n')
    (sub / "readme.txt").write_text("not a denver config or a directory\n")
    monkeypatch.chdir(tmp_path)

    assert denver.main(["__complete", "run", "sub/"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["sub/denver.toml"]  # readme.txt is neither a dir nor a denver config -- excluded


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


def test_parent_process_name_walks_past_a_frozen_builds_own_bootloader(monkeypatch):
    # a frozen build's immediate parent is its own PyInstaller bootloader (see
    # _own_process_names) -- pid 111; its parent, pid 42, is the real calling shell.
    monkeypatch.setattr(os, "getppid", lambda: 111)
    monkeypatch.setattr(denver, "_own_process_names", lambda: {"denver"})
    monkeypatch.setattr(denver, "_process_name", lambda pid: {111: "denver", 42: "zsh"}[pid])
    monkeypatch.setattr(denver, "_parent_pid", lambda pid: {111: 42}[pid])
    assert denver._parent_process_name() == "zsh"


def test_parent_process_name_gives_up_once_the_hop_bound_is_exceeded(monkeypatch):
    # every ancestor looks like the bootloader itself -- never finds a real shell,
    # but must still terminate rather than walking /proc forever.
    monkeypatch.setattr(os, "getppid", lambda: 1)
    monkeypatch.setattr(denver, "_own_process_names", lambda: {"denver"})
    monkeypatch.setattr(denver, "_process_name", lambda pid: "denver")
    monkeypatch.setattr(denver, "_parent_pid", lambda pid: pid + 1)
    assert denver._parent_process_name() is None


def test_parent_process_name_none_when_the_walk_runs_out_of_ancestors(monkeypatch):
    monkeypatch.setattr(os, "getppid", lambda: 111)
    monkeypatch.setattr(denver, "_own_process_names", lambda: {"denver"})
    monkeypatch.setattr(denver, "_process_name", lambda pid: "denver")
    monkeypatch.setattr(denver, "_parent_pid", lambda pid: None)
    assert denver._parent_process_name() is None


# ---- _own_process_names ------------------------------------------------------ #
def test_own_process_names_is_just_argv0_when_not_frozen(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["/path/to/denver.py", "complete"])
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert denver._own_process_names() == {"denver.py"}


def test_own_process_names_includes_the_frozen_executable_too(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["/path/to/denver", "complete"])
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/tmp/onefile-extract/denver", raising=False)
    assert denver._own_process_names() == {"denver"}


# ---- _process_name / _parent_pid --------------------------------------------- #
def test_process_name_reads_proc_comm(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda self: "zsh\n")
    assert denver._process_name(os.getpid()) == "zsh"


def test_process_name_falls_back_to_ps_when_proc_is_unreadable(monkeypatch):
    def raise_oserror(self):
        raise OSError

    monkeypatch.setattr(Path, "read_text", raise_oserror)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(args=[], returncode=0, stdout="zsh\n")
    )
    assert denver._process_name(2**30) == "zsh"


def test_parent_pid_reads_proc_stat_after_the_comms_closing_paren(monkeypatch):
    # a comm containing ')' itself is exactly why only the tail after the last ')' is parsed.
    monkeypatch.setattr(Path, "read_text", lambda self: "111 (my )proc) S 42 111 111 0 -1\n")
    assert denver._parent_pid(111) == 42


def test_parent_pid_none_when_the_stat_entry_has_no_ppid_field(monkeypatch):
    # closing ')' present but nothing usable follows it -- a malformed/truncated entry.
    monkeypatch.setattr(Path, "read_text", lambda self: "111 (comm)\n")
    assert denver._parent_pid(111) is None


def test_parent_pid_falls_back_to_ps_when_proc_is_unreadable(monkeypatch):
    def raise_oserror(self):
        raise OSError

    monkeypatch.setattr(Path, "read_text", raise_oserror)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(args=[], returncode=0, stdout="42\n")
    )
    assert denver._parent_pid(2**30) == 42


def test_parent_pid_none_when_ps_is_not_available(monkeypatch):
    def raise_oserror(*a, **kw):
        raise OSError

    monkeypatch.setattr(Path, "read_text", raise_oserror)
    monkeypatch.setattr(subprocess, "run", raise_oserror)
    assert denver._parent_pid(2**30) is None


def test_parent_pid_none_when_ps_exits_nonzero(monkeypatch):
    def raise_oserror(self):
        raise OSError

    monkeypatch.setattr(Path, "read_text", raise_oserror)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(args=[], returncode=1, stdout="")
    )
    assert denver._parent_pid(2**30) is None


def test_parent_pid_none_when_ps_output_is_not_numeric(monkeypatch):
    def raise_oserror(self):
        raise OSError

    monkeypatch.setattr(Path, "read_text", raise_oserror)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(args=[], returncode=0, stdout="not-a-pid\n")
    )
    assert denver._parent_pid(2**30) is None
