"""Tests for denver.py's hand-written --show-config renderer (dump_toml)."""

import io
import tomllib

import pytest

import denver


# ---- dump_toml: scalars, None, empty containers -----------------------------#
def test_dump_toml_empty_dict():
    assert denver.dump_toml({}) == ""


def test_dump_toml_scalars():
    out = denver.dump_toml({"s": "hi", "i": 5, "f": 1.5, "t": True, "f2": False})
    assert out == 's = "hi"\ni = 5\nf = 1.5\nt = true\nf2 = false\n'


def test_dump_toml_none_is_commented_out():
    out = denver.dump_toml({"python": None})
    assert out == "# python = null\n"


def test_dump_toml_empty_nested_containers_stay_inline():
    out = denver.dump_toml({"hooks": {}, "requirements": []})
    assert out == "hooks = {}\nrequirements = []\n"


def test_dump_toml_list_with_mixed_dict_and_scalar_entries_uses_inline_tables():
    # not uniformly dicts, so not table-like (see _toml_is_table_like) -- each dict entry still
    # needs *some* rendering, so it becomes an inline table instead of a '[[...]]' block.
    out = denver.dump_toml({"args": [{"flags": "--board"}, "justastring"]})
    assert out == 'args = [\n  { flags = "--board" },\n  "justastring",\n]\n'


def test_dump_toml_list_with_empty_dict_entry_inline():
    out = denver.dump_toml({"args": [{}, "x"]})
    assert out == 'args = [\n  {},\n  "x",\n]\n'


def test_dump_toml_list_with_nested_list_entry_inline():
    out = denver.dump_toml({"args": [[1, 2], "x"]})
    assert out == 'args = [\n  [\n  1,\n  2,\n],\n  "x",\n]\n'


def test_dump_toml_scalar_list():
    out = denver.dump_toml({"requirements": ["a.txt", "b.txt"]})
    assert out == 'requirements = [\n  "a.txt",\n  "b.txt",\n]\n'


# ---- dump_toml: nested tables ------------------------------------------------#
def test_dump_toml_nested_table():
    out = denver.dump_toml({"uv": {"python": "3.12.3"}})
    assert out == '[uv]\npython = "3.12.3"\n'


def test_dump_toml_sub_table_of_a_section_is_inline():
    # only the top level hands out '[section]' headers -- below one, a table
    # stays with the section it belongs to
    out = denver.dump_toml({"example": {"nested": {"key": "value"}}})
    assert out == '[example]\nnested = { key = "value" }\n'


def test_dump_toml_sub_table_with_an_unset_key_keeps_its_header():
    # '# key = null' is a comment line, and TOML has no comment inside an
    # inline table -- so the key would otherwise vanish from --show-config-full
    out = denver.dump_toml({"example": {"nested": {"key": None}}})
    assert out == "[example]\n\n[example.nested]\n# key = null\n"


def test_dump_toml_sub_table_with_an_unset_key_deep_inside_keeps_its_header():
    out = denver.dump_toml({"example": {"nested": {"key": [{"deep": None}]}}})
    assert out == "[example]\n\n[example.nested]\n\n[[example.nested.key]]\n# deep = null\n"


def test_dump_toml_array_of_tables_inside_a_section_still_gets_blocks():
    # an entry per block, one key per line -- more readable than one flattened
    # line per entry, so this shape keeps its header at any depth
    out = denver.dump_toml({"conan": {"exe": "conan", "recipes": [{"dirs": ["a"]}]}})
    assert out == '[conan]\nexe = "conan"\n\n[[conan.recipes]]\ndirs = [\n  "a",\n]\n'


def test_dump_toml_scalar_then_table_gets_blank_line_separator():
    out = denver.dump_toml({"version": "1.0", "uv": {"python": "3.12.3"}})
    assert out == 'version = "1.0"\n\n[uv]\npython = "3.12.3"\n'


def test_dump_toml_two_top_level_tables_are_separated_by_a_blank_line():
    out = denver.dump_toml({"uv": {"python": "3.12.3"}, "conan": {"provider": "conan"}})
    assert out == '[uv]\npython = "3.12.3"\n\n[conan]\nprovider = "conan"\n'


# ---- dump_toml: array of tables ----------------------------------------------#
def test_dump_toml_array_of_tables_single_entry():
    out = denver.dump_toml({"conan": {"conanfiles": [{"path": "conanfile.py"}]}})
    assert out == '[conan]\n\n[[conan.conanfiles]]\npath = "conanfile.py"\n'


def test_dump_toml_array_of_tables_multiple_entries_are_blank_line_separated():
    out = denver.dump_toml({"conanfiles": [{"path": "a"}, {"path": "b"}]})
    assert out == '[[conanfiles]]\npath = "a"\n\n[[conanfiles]]\npath = "b"\n'


def test_dump_toml_entry_keeps_a_nested_table_inline():
    # a '[packages.env]' header would attach to whichever entry precedes it by
    # position -- an entry is rendered self-contained instead
    out = denver.dump_toml({"packages": [{"name": "ninja", "env": {"PATH": "."}}]})
    assert out == '[[packages]]\nname = "ninja"\nenv = { PATH = "." }\n'


def test_dump_toml_entry_inline_table_stays_on_one_line():
    # TOML allows a multi-line array, but never a multi-line inline table
    out = denver.dump_toml({"packages": [{"env": {"PATH": ["bin", "sbin"], "deep": {"a": 1}}}]})
    assert out == '[[packages]]\nenv = { PATH = ["bin", "sbin"], deep = { a = 1 } }\n'


def test_dump_toml_entry_with_a_nested_table_parses_back_unchanged():
    data = {"packages": [{"name": "ninja", "env": {"PATH": "."}}, {"name": "gcc", "env": {"PATH": "bin"}}]}
    assert tomllib.loads(denver.dump_toml(data)) == data


# ---- dump_toml: keys needing quoting -----------------------------------------#
def test_dump_toml_non_bare_key_is_quoted():
    out = denver.dump_toml({"my key": 1})
    assert out == '"my key" = 1\n'


def test_dump_toml_non_bare_key_in_table_header_is_quoted():
    out = denver.dump_toml({"my section": {"a": 1}})
    assert out == '["my section"]\na = 1\n'


# ---- dump_toml: string rendering ---------------------------------------------#
def test_dump_toml_string_escaping():
    out = denver.dump_toml({"cmd": 'echo "hi"\\there'})
    assert out == 'cmd = "echo \\"hi\\"\\\\there"\n'


def test_dump_toml_multiline_string_uses_literal_block():
    out = denver.dump_toml({"cmd": "set -e\necho hi\n"})
    assert out == "cmd = '''\nset -e\necho hi\n'''\n"


def test_dump_toml_multiline_string_leading_blank_line_is_preserved():
    out = denver.dump_toml({"cmd": "\nsecond line"})
    assert out == "cmd = '''\n\nsecond line'''\n"


def test_dump_toml_multiline_string_containing_triple_quote_falls_back_to_basic_string():
    out = denver.dump_toml({"cmd": "it's a '''quote'''\nhere"})
    assert out == "cmd = \"it's a '''quote'''\\nhere\"\n"


# ---- _toml_scalar: defensive guard -------------------------------------------#
def test_toml_scalar_rejects_unsupported_types():
    with pytest.raises(TypeError):
        denver._toml_scalar(None)
    with pytest.raises(TypeError):
        denver._toml_scalar({"a": 1})


# ---- dump_toml: color=True -- ANSI syntax highlighting -----------------------#
# One span per syntactic role (see dump_toml's own color constants), each
# self-contained (opened and reset within the same call) -- never nested, so
# a plain 'in out' substring check is enough; no need to strip escapes back
# out to check the underlying text, since the plain-text tests above already
# cover that with color=False (the default every one of them uses).
def test_dump_toml_color_highlights_key_string_and_header():
    out = denver.dump_toml({"uv": {"python": "3.12.3"}}, color=True)
    assert f"{denver._TOML_HEADER_COLOR}[uv]{denver._TOML_RESET}" in out
    assert f"{denver._TOML_KEY_COLOR}python{denver._TOML_RESET}" in out
    assert f'{denver._TOML_STRING_COLOR}"3.12.3"{denver._TOML_RESET}' in out
    # never nested -- the key's own reset ends its span well before the value's starts
    assert out.index(denver._TOML_RESET) < out.rindex(denver._TOML_STRING_COLOR)


def test_dump_toml_color_highlights_bool_and_number():
    out = denver.dump_toml({"a": True, "b": 5}, color=True)
    assert f"{denver._TOML_SCALAR_COLOR}true{denver._TOML_RESET}" in out
    assert f"{denver._TOML_SCALAR_COLOR}5{denver._TOML_RESET}" in out


def test_dump_toml_color_highlights_null_comment_as_one_span():
    # the whole '# key = null' line is one comment-colored span -- not the
    # plain-colored key nested inside a separately-colored comment marker
    out = denver.dump_toml({"python": None}, color=True)
    assert out == f"{denver._TOML_COMMENT_COLOR}# python = null{denver._TOML_RESET}\n"


def test_dump_toml_color_highlights_inline_table_key():
    out = denver.dump_toml({"args": [{"flags": "--board"}]}, color=True)
    assert f"{denver._TOML_KEY_COLOR}flags{denver._TOML_RESET}" in out


def test_dump_toml_color_false_by_default_stays_plain():
    # every plain-text test above relies on this -- pinned explicitly too
    assert denver._TOML_HEADER_COLOR not in denver.dump_toml({"uv": {"a": 1}})


def test_dump_toml_color_resets_the_module_flag_even_on_error():
    # dump_toml's own finally: a TypeError mid-render (an unsupported scalar
    # type) must not leave color "stuck on" for the next, uncolored caller
    with pytest.raises(TypeError):
        denver.dump_toml({"a": {"b": 1}, "c": [object()]}, color=True)
    assert denver._TOML_HEADER_COLOR not in denver.dump_toml({"uv": {"a": 1}})


# ---- supports_color -----------------------------------------------------------#
def _tty(isatty):
    """A stand-in stream whose .isatty() returns ``isatty`` -- io.StringIO's own always returns False."""
    stream = io.StringIO()
    stream.isatty = lambda: isatty
    return stream


def test_supports_color_true_for_a_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    assert denver.supports_color(_tty(True)) is True


def test_supports_color_false_when_not_a_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    assert denver.supports_color(_tty(False)) is False


def test_supports_color_no_color_wins_even_on_a_tty(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert denver.supports_color(_tty(True)) is False


def test_supports_color_force_color_wins_when_not_a_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert denver.supports_color(_tty(False)) is True


def test_supports_color_no_color_beats_force_color_if_both_are_set(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert denver.supports_color(_tty(True)) is False


def test_supports_color_false_when_term_is_dumb(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert denver.supports_color(_tty(True)) is False


def test_supports_color_defaults_to_checking_real_stdout(monkeypatch):
    # no stream given -- falls back to sys.stdout itself
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setattr(denver.sys, "stdout", _tty(True))
    assert denver.supports_color() is True
