"""Tests for denver.py config loading & merging."""

import pytest

import denver


def test_load_yaml_empty_file(tmp_path):
    p = tmp_path / "empty.yml"
    p.write_text("")
    assert denver.load_yaml(p) == {}


def test_load_yaml_mapping(tmp_path):
    p = tmp_path / "c.yml"
    p.write_text("a: 1\n")
    assert denver.load_yaml(p) == {"a": 1}


def test_load_yaml_non_mapping_dies(tmp_path):
    p = tmp_path / "list.yml"
    p.write_text("- 1\n- 2\n")
    with pytest.raises(SystemExit):
        denver.load_yaml(p)


def test_deep_merge_dicts():
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    override = {"a": {"y": 3, "z": 4}, "c": 5}
    result = denver.deep_merge(base, override)
    assert result == {"a": {"x": 1, "y": 3, "z": 4}, "b": 1, "c": 5}
    # base untouched
    assert base == {"a": {"x": 1, "y": 2}, "b": 1}


def test_deep_merge_non_dict_non_list_override_replaces():
    assert denver.deep_merge(1, 2) == 2
    assert denver.deep_merge(True, False) is False


def test_deep_merge_lists_append():
    assert denver.deep_merge([1, 2], [3]) == [1, 2, 3]
    assert denver.deep_merge({"a": [1]}, {"a": [2]}) == {"a": [1, 2]}
    # base untouched
    base = [1, 2]
    denver.deep_merge(base, [3])
    assert base == [1, 2]


def test_deep_merge_new_list_key_no_lower_layer_is_used_as_is():
    assert denver.deep_merge({}, {"a": [1, 2]}) == {"a": [1, 2]}


def test_deep_merge_list_bang_entry_drops_lower_layer():
    assert denver.deep_merge(["a", "b"], ["!c"]) == ["c"]


def test_deep_merge_list_bang_entry_keeps_other_new_entries():
    # every entry in the overriding list is still appended, '!'-marked one
    # included -- only the lower layer's own entries are dropped.
    assert denver.deep_merge(["a", "b"], ["x", "!c", "y"]) == ["x", "c", "y"]


def test_deep_merge_list_bang_entry_kept_literal_with_no_lower_layer_at_all():
    # a genuinely new list key (base is denver._UNSET) has nothing to
    # deliberately override -- '!' stays a literal character.
    assert denver.deep_merge({}, {"a": ["!c"]}) == {"a": ["!c"]}


def test_deep_merge_new_string_key_no_conflict():
    # base has no prior value for this key at all: no conflict, whatever
    # the override sets (not marked with '!') simply becomes the value.
    assert denver.deep_merge({}, "y") == "y"
    assert denver.deep_merge({"a": {"x": "1"}}, {"a": {"z": "2"}}) == {"a": {"x": "1", "z": "2"}}


def test_deep_merge_same_string_value_no_conflict():
    assert denver.deep_merge("x", "x") == "x"


def test_deep_merge_conflicting_strings_dies():
    with pytest.raises(SystemExit):
        denver.deep_merge("x", "y")


def test_deep_merge_bang_prefix_overrides_and_strips_marker():
    assert denver.deep_merge("x", "!y") == "y"


def test_deep_merge_bang_prefix_on_new_key_still_strips_marker():
    # no prior value either: '!' is still stripped, not stored verbatim.
    assert denver.deep_merge({}, "!y") == "y"


def test_deep_merge_bang_prefix_kept_literal_with_no_lower_layer_at_all():
    # unlike the case above, a genuinely new dict key (base is denver._UNSET,
    # not merely absent from an already-real dict) has nothing to
    # deliberately override -- so a leading '!' is an ordinary character,
    # not an escape marker, and must not be silently stripped.
    result = denver.deep_merge({}, {"command": "!important"})
    assert result == {"command": "!important"}


def test_resolve_import_directory(tmp_path):
    base_dir = tmp_path / "env"
    base_dir.mkdir()
    target_dir = tmp_path / "base"
    target_dir.mkdir()
    (target_dir / "denver.yml").write_text("a: 1\n")
    resolved = denver.resolve_import("../base", base_dir)
    assert resolved == target_dir / "denver.yml"


def test_resolve_import_direct_file(tmp_path):
    base_dir = tmp_path / "env"
    base_dir.mkdir()
    yml = tmp_path / "custom.yml"
    yml.write_text("a: 1\n")
    resolved = denver.resolve_import("../custom.yml", base_dir)
    assert resolved == yml


def test_resolve_import_missing_dies(tmp_path):
    base_dir = tmp_path / "env"
    base_dir.mkdir()
    with pytest.raises(SystemExit):
        denver.resolve_import("../nope", base_dir)


def test_load_config_no_import(tmp_path):
    p = tmp_path / "denver.yml"
    p.write_text("stages: [pip]\n")
    assert denver.load_config(p) == {"stages": ["pip"]}


def test_load_config_with_import_merges_base_first(tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "denver.yml").write_text("stages: [pip]\npip:\n  python: '3.9'\n")

    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("import:\n- ../base\npip:\n  requirements: [r.txt]\n")

    cfg = denver.load_config(env_dir / "denver.yml")
    assert cfg["stages"] == ["pip"]
    assert cfg["pip"] == {"python": "3.9", "requirements": ["r.txt"]}
    assert "import" not in cfg


def test_load_config_runnable_false_does_not_leak_through_import(tmp_path):
    # 'runnable: false' marks one specific file (a shared base meant only to
    # be imported) -- a derived env importing it must not inherit that flag
    # into its own resolved config (is_runnable_env() reads it straight from
    # each file's own raw YAML, never through this merge, for the same
    # reason -- see load_config()'s own comment).
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "denver.yml").write_text("runnable: false\nstages: [pip]\n")

    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("import:\n- ../base\n")

    cfg = denver.load_config(env_dir / "denver.yml")
    assert "runnable" not in cfg


def test_load_config_runnable_own_value_still_applies(tmp_path):
    # unlike 'import', 'runnable' isn't dropped from the file that actually
    # sets it -- only from what an *importer* inherits from it.
    p = tmp_path / "denver.yml"
    p.write_text("runnable: false\nstages: [pip]\n")
    cfg = denver.load_config(p)
    assert cfg["runnable"] is False


def test_load_config_import_override_wins(tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "denver.yml").write_text("command: fish\n")
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    # a different string for a key the base already set requires '!' -- see
    # test_load_config_conflicting_string_dies / test_load_config_bang_override_wins
    (env_dir / "denver.yml").write_text("import: [../base]\ncommand: '!bash'\n")
    cfg = denver.load_config(env_dir / "denver.yml")
    assert cfg["command"] == "bash"


def test_load_config_conflicting_string_dies(tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "denver.yml").write_text("command: fish\n")
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("import: [../base]\ncommand: bash\n")
    with pytest.raises(SystemExit):
        denver.load_config(env_dir / "denver.yml")


def test_load_config_same_string_no_conflict(tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "denver.yml").write_text("pip:\n  python: '3.12.3'\n")
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("import: [../base]\npip:\n  python: '3.12.3'\n")
    cfg = denver.load_config(env_dir / "denver.yml")
    assert cfg["pip"]["python"] == "3.12.3"


def test_load_config_circular_import_dies(tmp_path):
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    (a_dir / "denver.yml").write_text("import: [../b]\n")
    (b_dir / "denver.yml").write_text("import: [../a]\n")
    with pytest.raises(SystemExit):
        denver.load_config(a_dir / "denver.yml")


def test_validate_top_level_keys_known_keys_ok():
    config = {"version": 1.0, "stages": ["pip"], "pip": {}, "command": "fish"}
    denver.validate_top_level_keys(config)  # no error


def test_validate_top_level_keys_unknown_section_dies():
    config = {"stages": ["pip"], "pip": {}, "typo-section": {}}
    with pytest.raises(SystemExit):
        denver.validate_top_level_keys(config)


def test_validate_top_level_keys_no_stages_only_known_keys_ok():
    config = {"runnable": False, "hooks": {}}
    denver.validate_top_level_keys(config)  # no error


# ---- --config / -c overrides ------------------------------------------------#
def test_parse_config_override_spec_plain_set():
    assert denver.parse_config_override_spec("pip.python=3.12.3") == (["pip", "python"], "=", "3.12.3")


def test_parse_config_override_spec_append():
    assert denver.parse_config_override_spec("pip.requirements+=numpy") == (
        ["pip", "requirements"],
        "+=",
        "numpy",
    )


def test_parse_config_override_spec_no_operator_dies():
    with pytest.raises(SystemExit):
        denver.parse_config_override_spec("pip.python")


def test_parse_config_override_spec_empty_path_dies():
    with pytest.raises(SystemExit):
        denver.parse_config_override_spec("=3.12.3")


def test_apply_config_override_sets_top_level_scalar():
    config = denver.apply_config_override({}, "command=bash")
    assert config == {"command": "bash"}


def test_apply_config_override_creates_missing_parent_dicts():
    config = denver.apply_config_override({}, "pip.python=3.12.3")
    assert config == {"pip": {"python": "3.12.3"}}


def test_apply_config_override_overwrites_existing_value():
    config = denver.apply_config_override({"pip": {"python": "3.9", "uv": True}}, "pip.python=3.12.3")
    assert config == {"pip": {"python": "3.12.3", "uv": True}}


def test_apply_config_override_parses_yaml_types():
    config = denver.apply_config_override({}, "pip.uv=true")
    assert config["pip"]["uv"] is True
    config = denver.apply_config_override({}, "pip.requirements=[a, b]")
    assert config["pip"]["requirements"] == ["a", "b"]


def test_apply_config_override_does_not_mutate_input():
    base = {"pip": {"python": "3.9"}}
    denver.apply_config_override(base, "pip.python=3.12.3")
    assert base == {"pip": {"python": "3.9"}}


def test_apply_config_override_plus_equals_appends_to_list():
    config = denver.apply_config_override({"pip": {"requirements": ["a"]}}, "pip.requirements+=b")
    assert config["pip"]["requirements"] == ["a", "b"]


def test_apply_config_override_plus_equals_on_unset_behaves_like_set():
    config = denver.apply_config_override({}, "pip.requirements+=[a]")
    assert config["pip"]["requirements"] == ["a"]


def test_apply_config_override_plus_equals_concatenates_strings():
    config = denver.apply_config_override({"command": "fish "}, "command+=-C hello")
    assert config["command"] == "fish -C hello"


def test_apply_config_override_plus_equals_adds_numbers():
    config = denver.apply_config_override({"retries": 1}, "retries+=2")
    assert config["retries"] == 3


def test_apply_config_override_plus_equals_incompatible_types_dies():
    with pytest.raises(SystemExit):
        denver.apply_config_override({"pip": {"python": "3.9"}}, "pip.python+=1")


def test_apply_config_override_plus_equals_onto_bool_dies():
    with pytest.raises(SystemExit):
        denver.apply_config_override({"flag": True}, "flag+=1")


def test_apply_config_overrides_applies_in_order_last_wins():
    config = denver.apply_config_overrides({}, ["pip.python=3.9", "pip.python=3.12.3"])
    assert config["pip"]["python"] == "3.12.3"


# ---- --until / --skip stage-name validation --------------------------------#
def test_validate_stage_filters_known_stages_ok():
    config = {"stages": ["pip", "conan"]}
    denver.validate_stage_filters(config, "pip", ["conan"])  # no error


def test_validate_stage_filters_no_filters_ok():
    config = {"stages": ["pip"]}
    denver.validate_stage_filters(config, None, [])  # no error


def test_validate_stage_filters_unknown_until_dies():
    config = {"stages": ["pip"]}
    with pytest.raises(SystemExit):
        denver.validate_stage_filters(config, "typo", [])


def test_validate_stage_filters_unknown_skip_dies():
    config = {"stages": ["pip"]}
    with pytest.raises(SystemExit):
        denver.validate_stage_filters(config, None, ["typo"])
