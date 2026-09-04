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


# One marker string per shell's own wiring script, unique enough to tell them apart -- same
# strings the explicit-shell tests above already assert on.
_SHELL_SCRIPT_MARKER = {"bash": "complete -F", "zsh": "compdef", "fish": "complete -c denver"}


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_complete_with_no_shell_auto_detects_through_a_real_uv_run_wrapper(shell):
    # End-to-end regression test for README's own recommended dev-checkout alias,
    # `alias denver="uv run $PWD/src/denver.py"`: 'uv run' is an unrelated process
    # between denver.py and the real invoking shell (see _NON_SHELL_LAUNCHER_NAMES),
    # so this drives a real 'uv run' through a real shell rather than mocking
    # _parent_process_name -- the only way to catch a regression in the actual
    # ancestor walk, not just in what _detect_shell does with its result.
    if not shutil.which(shell):
        pytest.skip(f"{shell} not installed")
    if not shutil.which("uv"):
        pytest.skip("uv not installed")
    # bash/zsh both exec() their own process image away for a script's trailing simple
    # command instead of forking a child for it -- harmless normally (nothing left to do
    # after), but here it would silently erase the very "zsh"/"bash" ancestor this test
    # means to walk past, so 'uv run ...' wouldn't be its last command's real parent at
    # all. The trailing ';true' forces a real fork instead. fish never does this, but
    # it's harmless to include there too.
    cmd = f"uv run {shlex.quote(sys.executable)} {shlex.quote(denver.__file__)} complete; true"
    result = subprocess.run([shell, "-c", cmd], capture_output=True, text=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert _SHELL_SCRIPT_MARKER[shell] in result.stdout, (result.stdout, result.stderr)
    for other_shell, marker in _SHELL_SCRIPT_MARKER.items():
        if other_shell != shell:
            assert marker not in result.stdout, (shell, result.stdout)


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
    # Via the 'alias' builtin itself, not by indexing $BASH_ALIASES directly -- that's an
    # associative array, a bash-4.0+ feature, and macOS's own system bash is still 3.2 (see
    # _completion_script_bash's own docstring for what indexing it there actually does).
    out = denver._completion_script("bash", ["denver"])
    assert 'alias -- "$cmd"' in out  # e.g. `alias denver=/path/to/denver.py`
    assert "BASH_ALIASES" not in out


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


def test_bash_completion_resolves_a_multi_word_alias_into_separate_words():
    # Regression test: 'alias -- "$cmd"' prints the alias's *whole* value as one quoted string
    # ("alias denver='python3 /path/to/denver.py'") -- eval'ing that string straight into the
    # resolved=(...) array literal would make it ONE element (the literal 22-odd-character
    # string, spaces and all), not two ("python3", "/path/to/denver.py") the way real alias
    # expansion's own re-parse would split it. Driven through a real bash (not string-matching
    # on the script text), the same way the unquoted-eval tests above are, because that's the
    # only thing that actually exercises bash's own word-splitting.
    if not shutil.which("bash"):
        pytest.skip("bash not installed")
    cmd = f"{shlex.quote(sys.executable)} {shlex.quote(denver.__file__)}"
    script = (
        f"alias denver={shlex.quote(cmd)}\n"
        f"eval $({cmd} complete bash)\n"
        "COMP_WORDS=(denver run --show)\n"
        "COMP_CWORD=2\n"
        "COMP_LINE='denver run --show'\n"
        "COMP_POINT=${#COMP_LINE}\n"
        "_denver_complete\n"
        'printf "%s\\n" "${COMPREPLY[@]}"\n'
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert "--show-config" in result.stdout.split(), (result.stdout, result.stderr)
    assert "--show-config-full" in result.stdout.split(), (result.stdout, result.stderr)


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
    assert "--show-config-full" in result.stdout, result.stderr


def test_fish_completion_function_defines_via_unquoted_eval():
    # Same hazard as bash/zsh above, but fish-specific in its failure mode: fish (unlike bash)
    # never treats a bare space as a statement separator, so without explicit ';'s the whole
    # script collapsing onto one line left a leading '#' comment free to swallow the entire
    # line, comment included -- 'eval (denver complete)' used to silently define nothing at
    # all in fish, with no error, rather than raising the way bash/zsh's own hazard would have.
    #
    # Uses fish's own '(cmd)' substitution, not bash/zsh-style '$(cmd)' -- the latter is only
    # valid from fish 3.4 on, and it's the unquoted word-splitting that's under test here, which
    # '(cmd)' triggers identically on every fish version.
    if not shutil.which("fish"):
        pytest.skip("fish not installed")
    cmd = f"{shlex.quote(sys.executable)} {shlex.quote(denver.__file__)} complete fish"
    script = f"eval ({cmd});\nfunctions -q __denver_complete; and echo DEFINED; or echo NOT_DEFINED\n"
    result = subprocess.run(["fish", "-c", script], capture_output=True, text=True)
    assert "DEFINED" in result.stdout.split(), result.stdout + result.stderr
    assert "NOT_DEFINED" not in result.stdout.split(), result.stdout + result.stderr


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


# ---- zsh descriptions -- compadd -d, column-aligned 'value -- description' -------------------- #
def test_completion_script_zsh_asks_for_descriptions_via_dunder_complete_describe():
    out = denver._completion_script("zsh", ["denver"])
    assert "__complete --describe" in out
    assert "compadd -d descriptions --" in out


def test_completion_script_zsh_defines_a_literal_tab_not_a_raw_byte():
    # See _completion_script_zsh's own docstring: a raw tab byte embedded in the script text
    # would itself be an IFS character and get word-split away by an unquoted `eval $(...)`,
    # same as a real newline would -- it has to be the literal 4 characters $ ' \ t ' instead,
    # for zsh to turn into a real tab only once *it* evaluates the script.
    out = denver._completion_script("zsh", ["denver"])
    assert "tab=$'\\t'" in out
    assert "\t" not in out


def test_zsh_completion_descriptions_pair_each_flag_with_its_own_help_text():
    # Real zsh, real dispatch machinery (not a hand-built `names` list) -- the same harness
    # test_zsh_real_dispatch_completes_run_flags_for_every_typed_spelling below uses, but reading
    # back the 'descriptions' array (not just 'values') the completion widget would show.
    if not shutil.which("zsh"):
        pytest.skip("zsh not installed")
    cmd = f"{shlex.quote(sys.executable)} {shlex.quote(denver.__file__)}"
    script = (
        f"alias denver={shlex.quote(cmd)}\n"
        f"eval $({cmd} complete zsh)\n"
        "autoload -Uz compinit; compinit -u\n"
        "words=(denver run --show); CURRENT=3\n"
        'compadd() { local i; for (( i = 1; i <= $#values; i++ )); do print -r -- "$values[$i]|$descriptions[$i]"; done }\n'
        "_denver_complete\n"
    )
    result = subprocess.run(["zsh", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    pairs = dict(line.split("|", 1) for line in result.stdout.splitlines())
    assert pairs["--show-config"].startswith("--show-config ")
    assert "print the fully resolved" in pairs["--show-config"]
    assert pairs["--show-config-full"].startswith("--show-config-full ")
    assert "like --show-config" in pairs["--show-config-full"]


def test_zsh_completion_descriptions_are_column_aligned_on_the_separator():
    # compadd -d's own display strings replace what's shown per candidate rather than annotating
    # it (see _completion_script_zsh's docstring, and compadd(1)) -- so denver pads every value
    # out to the widest one before appending ' -- description', the same way zsh's own '_describe'
    # utility does, so every '--' actually lines up instead of ragged-left.
    if not shutil.which("zsh"):
        pytest.skip("zsh not installed")
    cmd = f"{shlex.quote(sys.executable)} {shlex.quote(denver.__file__)}"
    script = (
        f"alias denver={shlex.quote(cmd)}\n"
        f"eval $({cmd} complete zsh)\n"
        "autoload -Uz compinit; compinit -u\n"
        "words=(denver run --show); CURRENT=3\n"
        'compadd() { local i; for (( i = 1; i <= $#values; i++ )); do print -r -- "$descriptions[$i]"; done }\n'
        "_denver_complete\n"
    )
    result = subprocess.run(["zsh", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    separator_columns = {line.index(" -- ") for line in result.stdout.splitlines() if " -- " in line}
    assert len(separator_columns) == 1, result.stdout  # every '--' at the same column


# ---- real end-to-end dispatch: absolute path / relative path / bare `denver` / an alias ------- #
# Each of these is a genuinely different way _completion_bind_names ends up wiring things, and (as
# the zsh basename-first dispatch quirk showed -- see _completion_script_zsh's own docstring)
# string-matching _completion_script's own output can't catch a shell's *own* completer lookup
# quietly deciding not to use any of it. These run every case through the real shell: generate the
# wiring the exact same way a real rc file would (a real subprocess launch, not a hand-built
# `names` list), source it for real, then complete for real and check the candidates that come back.

_DENVER_ABS = str(Path(denver.__file__).resolve())
_REPO_ROOT = Path(denver.__file__).resolve().parent.parent
_DISPATCH_CASES = ["absolute path", "relative path", "denver", "alias"]


def _dispatch_cases(tmp_path):
    """The 4 ways of typing the denver command real dispatch must resolve, for every shell.

    'gen': how 'denver complete <shell>' itself gets launched to generate the wiring -- a real
    subprocess, launched the exact same way 'word' is typed, so _completion_bind_names sees
    whatever sys.argv[0] a real shell hands it for that spelling. That's what actually
    distinguishes the absolute/relative cases from each other; 'denver' and 'alias' both just
    need *a* wiring script, since the bare 'denver' name is always in it regardless of how
    'denver complete' itself was launched (see _completion_bind_names).

    'word': what's actually typed at the prompt for the dispatch half of each test.
    'cwd': where *dispatch* needs to run from (only 'relative path' cares -- './src/denver.py'
    only resolves to anything from the repo root).
    'path': an extra PATH entry dispatch needs to resolve 'word' (only 'denver' does -- a
    stand-in for a real 'pip install -e .' console-script, since one may not exist here; the
    others are either a real path or resolved via BASH_ALIASES/zsh's $aliases/a fish function,
    none of which touch PATH at all).
    'alias': whether this case needs 'denver' aliased to _DENVER_ABS before completing (only
    'alias' does -- proving the alias-resolution branch present in every one of the three
    generated scripts, with nothing real named 'denver' on PATH at all).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "denver"
    wrapper.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(_DENVER_ABS)} \"$@\"\n")
    wrapper.chmod(0o755)
    return {
        "absolute path": {
            "gen": [sys.executable, _DENVER_ABS],
            "word": _DENVER_ABS,
            "cwd": None,
            "path": None,
            "alias": False,
        },
        "relative path": {
            "gen": ["./src/denver.py"],
            "word": "./src/denver.py",
            "cwd": _REPO_ROOT,
            "path": None,
            "alias": False,
        },
        "denver": {"gen": ["denver"], "word": "denver", "cwd": None, "path": str(bin_dir), "alias": False},
        "alias": {"gen": [sys.executable, _DENVER_ABS], "word": "denver", "cwd": None, "path": None, "alias": True},
    }


def _dispatch_env(case):
    env = dict(os.environ)
    if case["path"]:
        env["PATH"] = f"{case['path']}:{env.get('PATH', '')}"
    return env


def _dispatch_cwd(case):
    return str(case["cwd"]) if case["cwd"] else None


def _generate_wiring(shell, case):
    """The wiring script 'denver complete <shell>' prints, launched the way this case names."""
    result = subprocess.run(
        [*case["gen"], "complete", shell],
        capture_output=True,
        text=True,
        cwd=_dispatch_cwd(case),
        env=_dispatch_env(case),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("case_name", _DISPATCH_CASES)
def test_bash_real_dispatch_completes_run_flags_for_every_typed_spelling(tmp_path, case_name):
    if not shutil.which("bash"):
        pytest.skip("bash not installed")
    case = _dispatch_cases(tmp_path)[case_name]
    word = case["word"]
    script = _generate_wiring("bash", case)
    alias_line = f"alias denver={shlex.quote(_DENVER_ABS)}\n" if case["alias"] else ""
    comp_line = f"{word} run --sh"
    test_script = (
        script
        + alias_line
        # bash's own dispatch is nothing more than "is COMP_WORDS[0] one of the names 'complete
        # -F' registered" -- confirmed here via 'complete -p', then exercised for real by setting
        # COMP_WORDS/COMP_CWORD/COMP_LINE/COMP_POINT and calling the very function it resolved to.
        + f"complete -p {shlex.quote(word)} > /dev/null || {{ echo DENVER_TEST_NOT_REGISTERED; exit 1; }}\n"
        + f"COMP_WORDS=({shlex.quote(word)} run --sh)\n"
        "COMP_CWORD=2\n"
        f"COMP_LINE={shlex.quote(comp_line)}\n"
        "COMP_POINT=${#COMP_LINE}\n"
        "_denver_complete\n"
        'printf "%s\\n" "${COMPREPLY[@]}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", test_script], capture_output=True, text=True, cwd=_dispatch_cwd(case), env=_dispatch_env(case)
    )
    assert result.returncode == 0, result.stderr
    assert "DENVER_TEST_NOT_REGISTERED" not in result.stdout
    candidates = result.stdout.split()
    assert "--show-config" in candidates, (result.stdout, result.stderr)
    assert "--show-config-full" in candidates, (result.stdout, result.stderr)


@pytest.mark.parametrize("case_name", _DISPATCH_CASES)
def test_zsh_real_dispatch_completes_run_flags_for_every_typed_spelling(tmp_path, case_name):
    if not shutil.which("zsh"):
        pytest.skip("zsh not installed")
    case = _dispatch_cases(tmp_path)[case_name]
    word = case["word"]
    script = _generate_wiring("zsh", case)
    alias_line = f"alias denver={shlex.quote(_DENVER_ABS)}\n" if case["alias"] else ""
    test_script = (
        # extendedglob is the harder of the two real-world cases -- see _completion_script_zsh's
        # own docstring: it's what turns a leading-dot relative word's fallback lookup key into a
        # malformed, never-matching string, which is exactly the bug this whole section guards.
        "setopt extendedglob\n"
        "autoload -Uz compinit; compinit -u\n"
        + script
        + alias_line
        # zsh's real dispatcher (_normal -> _set_command -> _dispatch, see
        # Completion/Base/Core/_set_command upstream) needs an actual completion widget context
        # to run end to end -- this drives its two load-bearing pieces directly instead: the
        # real, autoloaded _set_command computes the exact lookup keys zsh's own dispatch would,
        # and _comps is the exact table 'compdef' populated, checked in the same order _dispatch
        # itself tries them.
        + "autoload -Uz _set_command\n"
        + f"words=({shlex.quote(word)} run --sh); CURRENT=3\n"
        "_set_command\n"
        "comp=''\n"
        'for key in "$_comp_command" "$_comp_command1" "$_comp_command2"; do\n'
        "  [[ -n ${_comps[$key]} ]] && { comp=${_comps[$key]}; break }\n"
        "done\n"
        "[[ -z $comp ]] && { print DENVER_TEST_NO_DISPATCH; exit 1 }\n"
        'compadd() { local i; for (( i = 1; i <= $#values; i++ )); do print -r -- "$values[$i]"; done }\n'
        '"$comp"\n'
    )
    result = subprocess.run(
        ["zsh", "-c", test_script], capture_output=True, text=True, cwd=_dispatch_cwd(case), env=_dispatch_env(case)
    )
    assert result.returncode == 0, result.stderr
    assert "DENVER_TEST_NO_DISPATCH" not in result.stdout, "zsh's own dispatch never resolved to _denver_complete"
    candidates = result.stdout.split()
    assert "--show-config" in candidates, (result.stdout, result.stderr)
    assert "--show-config-full" in candidates, (result.stdout, result.stderr)


@pytest.mark.parametrize("case_name", _DISPATCH_CASES)
def test_fish_real_dispatch_completes_run_flags_for_every_typed_spelling(tmp_path, case_name):
    if not shutil.which("fish"):
        pytest.skip("fish not installed")
    case = _dispatch_cases(tmp_path)[case_name]
    word = case["word"]
    script = _generate_wiring("fish", case)
    alias_line = f"alias denver {shlex.quote(_DENVER_ABS)}\n" if case["alias"] else ""
    # 'complete --do-complete' is fish's own real dispatch end to end (commandline parsing,
    # '-c'/'-p' matching, the lot) -- not a hand-called function, unlike bash/zsh above, since
    # fish has no non-interactive way to introspect *which* completer it would pick without
    # actually asking it to complete something for real.
    test_script = script + alias_line + f"complete --do-complete={shlex.quote(f'{word} run --sh')}\n"
    result = subprocess.run(
        ["fish", "-c", test_script], capture_output=True, text=True, cwd=_dispatch_cwd(case), env=_dispatch_env(case)
    )
    assert result.returncode == 0, result.stderr
    candidates = [line.split("\t", 1)[0] for line in result.stdout.splitlines() if line]
    assert "--show-config" in candidates, (result.stdout, result.stderr)
    assert "--show-config-full" in candidates, (result.stdout, result.stderr)


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


def test_detect_shell_falls_back_to_the_shell_env_var_past_an_unrelated_wrapper(monkeypatch):
    # e.g. `alias denver="uv run $PWD/src/denver.py"` (README's own recommended dev
    # setup): the immediate parent is "uv" itself, not the shell that ran the
    # alias -- an unrelated process in between, same as the docstring's tmux/su
    # example. Bug: since "uv" is truthy, the old code never even looked at
    # $SHELL and fell straight through to "bash", so fish users sourcing
    # `denver complete` got a bash completion script fish can't parse.
    monkeypatch.setattr(denver, "_parent_process_name", lambda: "uv")
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    assert denver._detect_shell() == "fish"


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


def test_dunder_complete_offers_a_denver_yml_file_when_pyyaml_is_installed(tmp_path, monkeypatch, capsys):
    (tmp_path / "denver.yml").write_text("stages: []\n")
    monkeypatch.chdir(tmp_path)

    assert denver.main(["__complete", "run", ""]) == 0
    assert "denver.yml" in capsys.readouterr().out


def test_dunder_complete_hides_a_denver_yml_file_without_pyyaml_installed(tmp_path, monkeypatch, capsys):
    (tmp_path / "denver.yml").write_text("stages: []\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(denver, "yaml", None)

    assert denver.main(["__complete", "run", ""]) == 0
    assert "denver.yml" not in capsys.readouterr().out


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
        'stages = []\ndenver-custom-args = [\n  { flags = "--board", default = "x" },\n  { flags = [\n  "--release",\n  "-r",\n], action = "store_true" },\n  "justastring",\n  { help = "no \'flags:\' key at all" },\n]\n'  # malformed (no 'flags:') -- ignored too
    )

    assert denver.main(["__complete", "run", str(env_dir), "--b"]) == 0
    assert capsys.readouterr().out.splitlines() == ["--board"]

    assert denver.main(["__complete", "run", str(env_dir), "-"]) == 0
    out = set(capsys.readouterr().out.splitlines())
    assert {"--board", "--release", "-r", "--scripts", "--show-config"} <= out


def test_dunder_complete_flags_without_any_declared_args_are_just_denvers_own(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text('stages = []\n')  # no 'denver-custom-args:' key at all

    assert denver.main(["__complete", "run", str(env_dir), "--sh"]) == 0
    assert capsys.readouterr().out.splitlines() == ["--show-config", "--show-config-full"]


# ---- 'denver __complete --describe' -- the 'candidate\tdescription' lines fish/zsh show -------- #
# In-process (denver.main + capsys), not through a real shell -- see the real-dispatch and zsh
# description tests above/below for that end; these instead pin down _completion_description_lookup's
# own branching directly, the same way the plain (undescribed) __complete tests above do for
# _complete_candidates_unsafe's.
def test_dunder_complete_describe_top_level_pairs_run_with_its_own_blurb(capsys):
    assert denver.main(["__complete", "--describe"]) == 0
    out = capsys.readouterr().out
    assert "run\tbuild/enter an env" in out
    assert "--help\tshow this help and exit" in out


def test_dunder_complete_describe_top_level_with_a_partial_word_still_shows_blurbs(capsys):
    # A single (non-empty) word is still "nothing typed yet" for _completion_description_lookup
    # -- words[:-1] is [] either way -- so this hits the same _top_level_help() branch as the
    # 'literally no words at all' case above, just reached from one word instead of zero.
    assert denver.main(["__complete", "--describe", "r"]) == 0
    out = capsys.readouterr().out
    assert "run\tbuild/enter an env" in out


def test_dunder_complete_describe_shell_names_pair_each_with_its_own_blurb(capsys):
    assert denver.main(["__complete", "--describe", "complete", ""]) == 0
    out = capsys.readouterr().out
    assert "fish\tthe friendly interactive shell" in out
    assert "zsh\tthe Z shell" in out


def test_dunder_complete_describe_offers_nothing_once_complete_already_has_a_shell(capsys):
    assert denver.main(["__complete", "--describe", "complete", "bash", ""]) == 0
    assert capsys.readouterr().out == ""


def test_dunder_complete_describe_unrecognised_subcommand_has_no_vocabulary_either(capsys):
    # Neither 'complete' nor 'run' -- _complete_candidates_unsafe already offers nothing for
    # this (see test_dunder_complete_offers_nothing_for_an_unrecognised_subcommand below), so
    # there's nothing to describe either, but _completion_description_lookup still runs and
    # must fall all the way through to its own final `return {}`.
    assert denver.main(["__complete", "--describe", "frobnicate", "x"]) == 0
    assert capsys.readouterr().out == ""


def test_dunder_complete_describe_run_flags_pair_each_with_its_own_help_text(capsys):
    assert denver.main(["__complete", "--describe", "run", "--show-conf"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("--show-config\t")
    assert "print the fully resolved" in out[0]
    assert out[1].startswith("--show-config-full\t")


def test_dunder_complete_describe_offers_nothing_past_the_forwarded_command_boundary(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text("stages = []\n")
    assert denver.main(["__complete", "--describe", "run", str(env_dir), "--", "extra"]) == 0
    assert capsys.readouterr().out == ""


def test_dunder_complete_describe_stays_undescribed_mid_flag_value(tmp_path, capsys):
    # A pending flag's own value (here --until's) has no description vocabulary at all -- see
    # _run_description_lookup -- so whatever stage ids come back are never tab-decorated.
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text('stages = ["build"]\n')
    assert denver.main(["__complete", "--describe", "run", str(env_dir), "--until", ""]) == 0
    assert capsys.readouterr().out == "build\n"


def test_dunder_complete_describe_declared_flags_pair_with_their_own_help(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text(
        'stages = []\ndenver-custom-args = [\n'
        '  { flags = "--target", help = "which board to build for" },\n'
        '  { flags = "--release" },\n'  # no 'help:' -- stays undescribed
        '  "justastring",\n'  # malformed (not a mapping at all) -- ignored, not raised on
        "]\n"
    )
    assert denver.main(["__complete", "--describe", "run", str(env_dir), "--tar"]) == 0
    assert capsys.readouterr().out == "--target\twhich board to build for\n"

    assert denver.main(["__complete", "--describe", "run", str(env_dir), "--rel"]) == 0
    assert capsys.readouterr().out == "--release\n"


def test_dunder_complete_describe_env_paths_have_no_description_vocabulary(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "myenv").mkdir()
    assert denver.main(["__complete", "--describe", "run", "my"]) == 0
    assert capsys.readouterr().out == "myenv/\n"


def test_dunder_complete_describe_swallows_a_lookup_exception_and_still_returns_bare_candidates(monkeypatch, capsys):
    monkeypatch.setattr(
        denver,
        "_completion_description_lookup",
        lambda words: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert denver.main(["__complete", "--describe", "run", "--dry"]) == 0
    out = capsys.readouterr().out
    assert out == "--dry-run\n"  # bare -- no tab, since the lookup itself blew up


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


def test_parent_process_name_walks_past_a_uv_run_wrapper(monkeypatch):
    # `alias denver="uv run $PWD/src/denver.py"` (README's own recommended dev
    # setup): pid 111 is the "uv run" child that actually launches python, so
    # its immediate parent, pid 111, isn't the real shell -- pid 42 is.
    monkeypatch.setattr(os, "getppid", lambda: 111)
    monkeypatch.setattr(denver, "_own_process_names", lambda: {"denver.py"})
    monkeypatch.setattr(denver, "_process_name", lambda pid: {111: "uv", 42: "fish"}[pid])
    monkeypatch.setattr(denver, "_parent_pid", lambda pid: {111: 42}[pid])
    assert denver._parent_process_name() == "fish"


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
