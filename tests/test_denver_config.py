"""Tests for denver.py config loading & merging."""

import pytest

import denver


def test_load_config_file_empty_toml(tmp_path):
    p = tmp_path / "empty.toml"
    p.write_text("")
    assert denver.load_config_file(p) == {}


def test_load_config_file_toml_mapping(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("a = 1\n")
    assert denver.load_config_file(p) == {"a": 1}


def test_load_config_file_nested_value(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("a = [1, 2]\n")
    assert denver.load_config_file(p) == {"a": [1, 2]}


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
    (target_dir / "denver.toml").write_text('a = 1\n')
    resolved = denver.resolve_import("../base", base_dir)
    assert resolved == target_dir / "denver.toml"


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
    p = tmp_path / "denver.toml"
    p.write_text('stages = [\n  "uv",\n]\n')
    assert denver.load_config(p) == {"stages": ["uv"]}


def test_load_config_with_import_merges_base_first(tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "denver.toml").write_text('stages = [\n  "uv",\n]\n\n[uv]\npython = "3.9"\n')

    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text('import = [\n  "../base",\n]\n\n[uv]\nrequirements = [\n  "r.txt",\n]\n')

    cfg = denver.load_config(env_dir / "denver.toml")
    assert cfg["stages"] == ["uv"]
    assert cfg["uv"] == {"python": "3.9", "requirements": ["r.txt"]}
    assert "import" not in cfg


def test_load_config_runnable_false_does_not_leak_through_import(tmp_path):
    # 'runnable: false' marks one specific file (a shared base meant only to
    # be imported) -- a derived env importing it must not inherit that flag
    # into its own resolved config (is_runnable_env() reads it straight from
    # each file's own raw YAML, never through this merge, for the same
    # reason -- see load_config()'s own comment).
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "denver.toml").write_text('runnable = false\nstages = [\n  "uv",\n]\n')

    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text('import = [\n  "../base",\n]\n')

    cfg = denver.load_config(env_dir / "denver.toml")
    assert "runnable" not in cfg


def test_load_config_runnable_own_value_still_applies(tmp_path):
    # unlike 'import', 'runnable' isn't dropped from the file that actually
    # sets it -- only from what an *importer* inherits from it.
    p = tmp_path / "denver.toml"
    p.write_text('runnable = false\nstages = [\n  "uv",\n]\n')
    cfg = denver.load_config(p)
    assert cfg["runnable"] is False


def test_load_config_import_override_wins(tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "denver.toml").write_text('command = "fish"\n')
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    # a different string for a key the base already set requires '!' -- see
    # test_load_config_conflicting_string_dies / test_load_config_bang_override_wins
    (env_dir / "denver.toml").write_text('import = [\n  "../base",\n]\ncommand = "!bash"\n')
    cfg = denver.load_config(env_dir / "denver.toml")
    assert cfg["command"] == "bash"


def test_load_config_conflicting_string_dies(tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "denver.toml").write_text('command = "fish"\n')
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text('import = [\n  "../base",\n]\ncommand = "bash"\n')
    with pytest.raises(SystemExit):
        denver.load_config(env_dir / "denver.toml")


def test_load_config_same_string_no_conflict(tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "denver.toml").write_text('[uv]\npython = "3.12.3"\n')
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / "denver.toml").write_text('import = [\n  "../base",\n]\n\n[uv]\npython = "3.12.3"\n')
    cfg = denver.load_config(env_dir / "denver.toml")
    assert cfg["uv"]["python"] == "3.12.3"


def test_load_config_circular_import_dies(tmp_path):
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    (a_dir / "denver.toml").write_text('import = [\n  "../b",\n]\n')
    (b_dir / "denver.toml").write_text('import = [\n  "../a",\n]\n')
    with pytest.raises(SystemExit):
        denver.load_config(a_dir / "denver.toml")


# ---- typo hints ("did you mean") --------------------------------------------#
def test_with_hint_close_match():
    assert denver._with_hint("stagse", ["stages", "version"]) == "'stagse' (did you mean 'stages'?)"


def test_with_hint_no_close_match():
    assert denver._with_hint("xyz", ["stages", "version"]) == "'xyz'"


def test_list_with_hints_joins_multiple():
    result = denver._list_with_hints(["stagse", "xyz"], ["stages", "version"])
    assert result == "'stagse' (did you mean 'stages'?), 'xyz'"


def test_validate_top_level_keys_known_keys_ok():
    config = {"version": 1.0, "stages": ["uv"], "uv": {}, "command": "fish"}
    denver.validate_top_level_keys(config)  # no error


def test_validate_top_level_keys_unknown_section_dies():
    config = {"stages": ["uv"], "uv": {}, "typo-section": {}}
    with pytest.raises(SystemExit):
        denver.validate_top_level_keys(config)


def test_validate_top_level_keys_no_stages_only_known_keys_ok():
    config = {"runnable": False, "hooks": {}}
    denver.validate_top_level_keys(config)  # no error


def test_validate_top_level_keys_extensions_ok():
    config = {"extensions": {"providers": {"dirs": ["my_providers"]}}}
    denver.validate_top_level_keys(config)  # no error


def test_validate_top_level_keys_typo_hints_at_the_close_key(caplog):
    # 'stages' itself misspelled
    config = {"stagse": ["uv"], "uv": {}}
    with pytest.raises(SystemExit):
        denver.validate_top_level_keys(config)
    assert "did you mean 'stages'?" in caplog.text


def test_validate_top_level_keys_typo_hints_at_the_close_stage_id(caplog):
    # 'uv' declared correctly in 'stages:', but its own section is misspelled
    # as 'vu' -- a stray top-level key with nowhere else to belong.
    config = {"stages": ["uv"], "vu": {}}
    with pytest.raises(SystemExit):
        denver.validate_top_level_keys(config)
    assert "did you mean 'uv'?" in caplog.text


# ---- 'denver-version:' requirement -------------------------------------------#
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.0.3", ((1, 0, 3), 0)),
        ("v1.0.3", ((1, 0, 3), 0)),
        (" 1.0 ", ((1, 0), 0)),
        (1.0, ((1, 0), 0)),  # YAML parses an unquoted 1.0 as a float
        ("1.1.0rc1", ((1, 1, 0), -1)),
        ("1.1.0.dev3+g1234567", ((1, 1, 0), -1)),  # setuptools-scm, untagged commit
        ("1.0.3-2-gabc1234", ((1, 0, 3), 1)),  # git describe, 2 commits past the tag
        ("not-a-version", None),
        ("", None),
    ],
)
def test_parse_version(text, expected):
    assert denver.parse_version(text) == expected


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("1.0.3", "1.0.3", 0),
        ("1.0", "1.0.0", 0),  # zero-padded, not compared by length
        ("1.0.4", "1.0.3", 1),
        ("1.2", "1.10", -1),  # numeric, not lexicographic
        ("1.1.0.dev3+g1234567", "1.1.0", -1),  # a pre-release precedes its release
        ("1.0.3-2-gabc1234", "1.0.3", 1),  # ... a commit past the tag follows it
        ("1.0.3-2-gabc1234", "1.0.4", -1),
    ],
)
def test_compare_versions(left, right, expected):
    assert denver.compare_versions(denver.parse_version(left), denver.parse_version(right)) == expected


def test_parse_version_spec_bare_version_means_at_least():
    assert denver.parse_version_spec("1.0.3") == [(">=", ((1, 0, 3), 0), ">=1.0.3")]


def test_parse_version_spec_multiple_specifiers():
    assert denver.parse_version_spec(">=1.0.3, <2") == [
        (">=", ((1, 0, 3), 0), ">=1.0.3"),
        ("<", ((2,), 0), "<2"),
    ]


@pytest.mark.parametrize("spec", ["", "~=1.0.3", ">=abc", ">=1.0.3, ", ">= 1.0.3 extra"])
def test_parse_version_spec_invalid_dies(spec):
    with pytest.raises(SystemExit):
        denver.parse_version_spec(spec)


def test_validate_denver_version_unset_never_looks_at_the_version(monkeypatch):
    monkeypatch.setattr(denver, "package_version", lambda: pytest.fail("must not be called"))
    denver.validate_denver_version({"stages": ["uv"]})  # no error


@pytest.mark.parametrize("spec", [">=1.0.3", "1.0.3", "1.0", ">=1.0.3, <2", "==1.0.3", "!=1.0.2"])
def test_validate_denver_version_satisfied(monkeypatch, spec):
    monkeypatch.setattr(denver, "package_version", lambda: "1.0.3")
    denver.validate_denver_version({"denver-version": spec})  # no error


@pytest.mark.parametrize("spec", [">=1.0.4", "1.0.4", ">=1.0.3, <1.0.3", "==1.0.2", "!=1.0.3"])
def test_validate_denver_version_unsatisfied_dies(monkeypatch, spec):
    monkeypatch.setattr(denver, "package_version", lambda: "1.0.3")
    with pytest.raises(SystemExit):
        denver.validate_denver_version({"denver-version": spec})


def test_validate_denver_version_message_names_both_versions(monkeypatch, caplog):
    monkeypatch.setattr(denver, "package_version", lambda: "1.0.3")
    with pytest.raises(SystemExit):
        denver.validate_denver_version({"denver-version": ">=1.0.4"})
    assert ">=1.0.4" in caplog.text
    assert "1.0.3" in caplog.text


def test_validate_denver_version_untagged_checkout_counts_as_newer(monkeypatch):
    # a checkout 2 commits past the 1.0.3 tag has everything 1.0.3 has
    monkeypatch.setattr(denver, "package_version", lambda: "1.0.3-2-gabc1234")
    denver.validate_denver_version({"denver-version": ">=1.0.3"})  # no error


@pytest.mark.parametrize("running", [None, "unknown (not installed)"])
def test_validate_denver_version_undeterminable_warns_but_runs(monkeypatch, caplog, running):
    monkeypatch.setattr(denver, "package_version", lambda: running)
    denver.validate_denver_version({"denver-version": ">=1.0.4"})  # no error
    assert "cannot verify" in caplog.text


# ---- --config / -c overrides ------------------------------------------------#
def test_parse_config_override_spec_plain_set():
    assert denver.parse_config_override_spec("uv.python=3.12.3") == (["uv", "python"], "=", "3.12.3")


def test_parse_config_override_spec_append():
    assert denver.parse_config_override_spec("uv.requirements+=numpy") == (
        ["uv", "requirements"],
        "+=",
        "numpy",
    )


def test_parse_config_override_spec_no_operator_dies():
    with pytest.raises(SystemExit):
        denver.parse_config_override_spec("uv.python")


def test_parse_config_override_spec_empty_path_dies():
    with pytest.raises(SystemExit):
        denver.parse_config_override_spec("=3.12.3")


def test_apply_config_override_sets_top_level_scalar():
    config = denver.apply_config_override({}, "command=bash")
    assert config == {"command": "bash"}


def test_apply_config_override_creates_missing_parent_dicts():
    config = denver.apply_config_override({}, "uv.python=3.12.3")
    assert config == {"uv": {"python": "3.12.3"}}


def test_apply_config_override_overwrites_existing_value():
    config = denver.apply_config_override({"uv": {"python": "3.9", "amend": True}}, "uv.python=3.12.3")
    assert config == {"uv": {"python": "3.12.3", "amend": True}}


def test_apply_config_override_parses_json_types():
    config = denver.apply_config_override({}, "uv.exe=true")
    assert config["uv"]["exe"] is True
    config = denver.apply_config_override({}, 'uv.requirements=["a", "b"]')
    assert config["uv"]["requirements"] == ["a", "b"]


def test_apply_config_override_bare_word_stays_a_string():
    """A value that isn't valid JSON on its own (e.g. a bare version string) falls back to a plain str."""
    config = denver.apply_config_override({}, "uv.python=3.12.3")
    assert config["uv"]["python"] == "3.12.3"


def test_apply_config_override_does_not_mutate_input():
    base = {"uv": {"python": "3.9"}}
    denver.apply_config_override(base, "uv.python=3.12.3")
    assert base == {"uv": {"python": "3.9"}}


def test_apply_config_override_plus_equals_appends_to_list():
    config = denver.apply_config_override({"uv": {"requirements": ["a"]}}, "uv.requirements+=b")
    assert config["uv"]["requirements"] == ["a", "b"]


def test_apply_config_override_plus_equals_on_unset_behaves_like_set():
    config = denver.apply_config_override({}, 'uv.requirements+=["a"]')
    assert config["uv"]["requirements"] == ["a"]


def test_apply_config_override_plus_equals_concatenates_strings():
    config = denver.apply_config_override({"command": "fish "}, "command+=-C hello")
    assert config["command"] == "fish -C hello"


def test_apply_config_override_plus_equals_adds_numbers():
    config = denver.apply_config_override({"retries": 1}, "retries+=2")
    assert config["retries"] == 3


def test_apply_config_override_plus_equals_incompatible_types_dies():
    with pytest.raises(SystemExit):
        denver.apply_config_override({"uv": {"python": "3.9"}}, "uv.python+=1")


def test_apply_config_override_plus_equals_onto_bool_dies():
    with pytest.raises(SystemExit):
        denver.apply_config_override({"flag": True}, "flag+=1")


def test_apply_config_overrides_applies_in_order_last_wins():
    config = denver.apply_config_overrides({}, ["uv.python=3.9", "uv.python=3.12.3"])
    assert config["uv"]["python"] == "3.12.3"


# ---- --until / --skip stage-name validation --------------------------------#
def test_validate_stage_filters_known_stages_ok():
    config = {"stages": ["uv", "conan"]}
    denver.validate_stage_filters(config, "uv", ["conan"])  # no error


def test_validate_stage_filters_no_filters_ok():
    config = {"stages": ["uv"]}
    denver.validate_stage_filters(config, None, [])  # no error


def test_validate_stage_filters_unknown_until_dies():
    config = {"stages": ["uv"]}
    with pytest.raises(SystemExit):
        denver.validate_stage_filters(config, "typo", [])


def test_validate_stage_filters_unknown_skip_dies():
    config = {"stages": ["uv"]}
    with pytest.raises(SystemExit):
        denver.validate_stage_filters(config, None, ["typo"])


def test_validate_stage_filters_unknown_lists_available_as_ordered_bullets(caplog):
    # deliberately not alphabetical -- the message must preserve 'stages:'
    # declaration order, not sort it
    config = {"stages": ["zephyr", "conan", "uv"]}
    with pytest.raises(SystemExit):
        denver.validate_stage_filters(config, None, ["typo"])
    assert "  - zephyr\n  - conan\n  - uv" in caplog.text


def test_validate_stage_filters_unknown_typo_hints_at_the_close_stage_id(caplog):
    config = {"stages": ["uv", "conan"]}
    with pytest.raises(SystemExit):
        denver.validate_stage_filters(config, "conna", [])  # 'conan' misspelled
    assert "did you mean 'conan'?" in caplog.text


# ---- 'hooks:' key validation -------------------------------------------------#
def test_validate_hooks_keys_known_names_ok():
    config = {"stages": ["uv"], "hooks": {"env": "e.sh", "pre-uv": "a.sh", "post-uv": "b.sh", "pre-cmd": "c.sh"}}
    denver.validate_hooks_keys(config)  # no error


def test_validate_hooks_keys_unset_ok():
    denver.validate_hooks_keys({"stages": ["uv"]})  # no error


def test_validate_hooks_keys_not_a_mapping_dies():
    config = {"stages": ["uv"], "hooks": ["pre-uv"]}
    with pytest.raises(SystemExit):
        denver.validate_hooks_keys(config)


def test_validate_hooks_keys_unknown_name_dies():
    config = {"stages": ["uv"], "hooks": {"typo": "a.sh"}}
    with pytest.raises(SystemExit):
        denver.validate_hooks_keys(config)


def test_validate_hooks_keys_typo_hints_at_the_close_name(caplog):
    # 'pre-uv' misspelled -- this is exactly what run_hook() silently
    # skipped before validate_hooks_keys existed.
    config = {"stages": ["uv"], "hooks": {"per-uv": "a.sh"}}
    with pytest.raises(SystemExit):
        denver.validate_hooks_keys(config)
    assert "did you mean 'pre-uv'?" in caplog.text


def test_validate_hooks_keys_checks_against_every_declared_stage():
    # 'pre-conan' is a real hook name once 'conan' is declared, even though
    # 'conan' itself isn't the stage the typo happens to be near.
    config = {"stages": ["uv", "conan"], "hooks": {"pre-conan": "a.sh"}}
    denver.validate_hooks_keys(config)  # no error
