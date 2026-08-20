"""Tests for denver.py's hand-written --show-config renderer (dump_toml)."""

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


def test_dump_toml_deeply_nested_table_uses_dotted_path():
    out = denver.dump_toml({"example": {"nested": {"key": "value"}}})
    assert out == '[example]\n\n[example.nested]\nkey = "value"\n'


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
