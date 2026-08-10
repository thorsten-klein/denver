"""Tests for providers.conan_scripts.build_catalog -- the catalog.yml generator
recipes.py invokes as a subprocess (see recipes.generate_catalog).

get_rrev.inspect()/get_rrev.compute_rrev() (real recipe-revision computation
against conan's cache) are monkeypatched throughout: this module's own
logic -- catalog.yml bookkeeping, dependency resolution, rrev propagation --
is what's under test here, not conan's revision algorithm itself (that
belongs to test_conan_scripts_get_rrev.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from providers.conan_scripts import build_catalog


# --------------------------------------------------------------------------- #
# ConfigFile
# --------------------------------------------------------------------------- #
def test_config_file_read_missing_data_becomes_empty_dict(tmp_path, monkeypatch):
    path = tmp_path / "empty.yml"
    path.write_text("")
    cfg = build_catalog.ConfigFile(str(path), read=True)
    assert cfg.data == {}


def test_config_file_read_populates_data(tmp_path):
    path = tmp_path / "data.yml"
    path.write_text(yaml.safe_dump({"a": 1}))
    cfg = build_catalog.ConfigFile(str(path), read=True)
    assert cfg.data == {"a": 1}


def test_config_file_get_data_empty():
    cfg = build_catalog.ConfigFile("/nonexistent.yml")
    assert cfg.get_data() == ""


def test_config_file_save_and_reload(tmp_path):
    path = tmp_path / "out.yml"
    cfg = build_catalog.ConfigFile(str(path))
    cfg.data = {"x": "y"}
    cfg.save_file()
    reloaded = build_catalog.ConfigFile(str(path), read=True)
    assert reloaded.data == {"x": "y"}


# --------------------------------------------------------------------------- #
# Recipe
# --------------------------------------------------------------------------- #
def _make_recipe(tmp_path, monkeypatch, name="foo", version="1.0", conandata=None):
    recipe_dir = tmp_path / "recipes" / name / version
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "conanfile.py").write_text("x")
    (recipe_dir / "conandata.yml").write_text(yaml.safe_dump(conandata or {}))

    monkeypatch.setattr(build_catalog.get_rrev, "compute_rrev", lambda d: (name, version, "deadbeef"))
    monkeypatch.setattr(build_catalog.get_rrev, "inspect", lambda path, attrs: {"name": name, "version": version})
    monkeypatch.setattr(build_catalog, "workdir", tmp_path)

    return build_catalog.Recipe(str(recipe_dir))


def test_recipe_reads_name_version(tmp_path, monkeypatch):
    recipe = _make_recipe(tmp_path, monkeypatch)
    assert recipe.name == "foo"
    assert recipe.version == "1.0"
    assert recipe.rrev is None


@pytest.mark.parametrize(
    "mutate",
    [
        None,
        lambda r: setattr(r, "version", "2.0"),
        lambda r: setattr(r, "name", "bar"),
    ],
    ids=["matching-dirs-ok", "version-mismatch", "name-mismatch"],
)
def test_recipe_check(tmp_path, monkeypatch, mutate):
    recipe = _make_recipe(tmp_path, monkeypatch)
    if mutate is None:
        recipe.check()  # must not raise
    else:
        mutate(recipe)  # simulate conanfile disagreeing with the folder name
        with pytest.raises(build_catalog.GenerateError, match="does not match"):
            recipe.check()


def test_recipe_get_full_reference_requires_rrev(tmp_path, monkeypatch):
    recipe = _make_recipe(tmp_path, monkeypatch)
    with pytest.raises(build_catalog.GenerateError, match="rrev not set"):
        recipe.get_full_reference()


def test_recipe_get_full_reference_with_and_without_rrev(tmp_path, monkeypatch):
    recipe = _make_recipe(tmp_path, monkeypatch)
    recipe.rrev = "deadbeef"
    assert recipe.get_full_reference() == "foo/1.0@denver/snapshot#deadbeef"
    assert recipe.get_full_reference(rrev=False) == "foo/1.0@denver/snapshot"


def test_recipe_add_dependency_dedupes(tmp_path, monkeypatch):
    recipe = _make_recipe(tmp_path, monkeypatch, name="foo")
    dep = _make_recipe(tmp_path, monkeypatch, name="bar")
    recipe.add_dependency("requires", dep)
    recipe.add_dependency("requires", dep)
    assert recipe.requires == [dep]


def test_recipe_calculate_rrev(tmp_path, monkeypatch):
    recipe = _make_recipe(tmp_path, monkeypatch)
    monkeypatch.setattr(build_catalog.get_rrev, "compute_rrev", lambda d: ("foo", "1.0", "newrev"))
    assert recipe.calculate_rrev() == "newrev"


def test_recipe_get_json(tmp_path, monkeypatch):
    recipe = _make_recipe(tmp_path, monkeypatch)
    recipe.rrev = "deadbeef"
    j = recipe.get_json()
    assert j["name"] == "foo"
    assert j["version"] == "1.0"
    assert j["rrev"] == "deadbeef"
    assert j["full_reference"].endswith("#deadbeef")


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #
def test_catalog_find_recipe(tmp_path, monkeypatch):
    catalog = build_catalog.Catalog()
    catalog.recipes = [_make_recipe(tmp_path, monkeypatch, name="foo", version="1.0")]
    assert catalog.find_recipe("foo", "1.0") is not None
    assert catalog.find_recipe("foo", "2.0") is None
    assert catalog.find_recipe("bar", "1.0") is None


def test_catalog_resolve_dependencies_recursively_no_conandata(tmp_path, monkeypatch):
    catalog = build_catalog.Catalog()
    recipe = _make_recipe(tmp_path, monkeypatch)
    recipe.conandata_yml = None
    catalog.recipes = [recipe]
    catalog.resolve_dependencies_recursively(recipe)  # must not raise


def test_catalog_resolve_dependencies_missing_dep_raises(tmp_path, monkeypatch):
    catalog = build_catalog.Catalog()
    recipe = _make_recipe(tmp_path, monkeypatch, name="foo", conandata={"requires": ["bar/2.0@denver/snapshot"]})
    catalog.recipes = [recipe]
    with pytest.raises(build_catalog.GenerateError, match="Could not find"):
        catalog.resolve_dependencies_recursively(recipe)


def test_catalog_resolve_dependencies_stale_version_suggests_update(tmp_path, monkeypatch):
    # 'bar' exists only at 1.0, but foo's conandata.yml still references
    # 2.0 -- the recipe exists (just at a different version), so the error
    # should point at the version to update to, not claim it's missing.
    catalog = build_catalog.Catalog()
    dep = _make_recipe(tmp_path, monkeypatch, name="bar", version="1.0")
    recipe = _make_recipe(tmp_path, monkeypatch, name="foo", conandata={"requires": ["bar/2.0@denver/snapshot"]})
    catalog.recipes = [recipe, dep]
    with pytest.raises(build_catalog.GenerateError, match=r"should update reference.*to version '1\.0'"):
        catalog.resolve_dependencies_recursively(recipe)


def test_catalog_resolve_dependencies_multiple_versions_of_same_name_reports_not_found(tmp_path, monkeypatch):
    # an ambiguous stale reference (several versions of 'bar' coexist) falls
    # back to the generic "could not find" message rather than guessing.
    catalog = build_catalog.Catalog()
    dep_a = _make_recipe(tmp_path, monkeypatch, name="bar", version="1.0")
    dep_b = _make_recipe(tmp_path, monkeypatch, name="bar", version="1.5")
    recipe = _make_recipe(tmp_path, monkeypatch, name="foo", conandata={"requires": ["bar/2.0@denver/snapshot"]})
    catalog.recipes = [recipe, dep_a, dep_b]
    with pytest.raises(build_catalog.GenerateError, match="Could not find"):
        catalog.resolve_dependencies_recursively(recipe)


def test_catalog_resolve_dependencies_links_recipes(tmp_path, monkeypatch):
    catalog = build_catalog.Catalog()
    dep = _make_recipe(tmp_path, monkeypatch, name="bar", version="1.0")
    recipe = _make_recipe(
        tmp_path, monkeypatch, name="foo", conandata={"requires": ["bar/1.0@denver/snapshot"], "tool_requires": []}
    )
    catalog.recipes = [recipe, dep]
    catalog.resolve_dependencies_recursively(recipe)
    assert recipe.requires == [dep]
    assert dep.users["requires"] == [recipe]


def test_catalog_update_rrevs_recursively_propagates(tmp_path, monkeypatch):
    catalog = build_catalog.Catalog()
    dep = _make_recipe(tmp_path, monkeypatch, name="bar", version="1.0")
    recipe = _make_recipe(tmp_path, monkeypatch, name="foo", conandata={"requires": ["bar/1.0@denver/snapshot#oldrev"]})
    catalog.recipes = [recipe, dep]
    catalog.resolve_dependencies_recursively(recipe)

    monkeypatch.setattr(build_catalog.get_rrev, "compute_rrev", lambda d: ("bar", "1.0", "newrev"))
    catalog.update_rrevs_recursively(dep)

    assert dep.rrev == "newrev"
    assert recipe.conandata_yml.data["requires"] == ["bar/1.0@denver/snapshot#newrev"]


def test_catalog_update_rrevs_sorts_and_resaves(tmp_path, monkeypatch):
    catalog = build_catalog.Catalog()
    dep = _make_recipe(tmp_path, monkeypatch, name="bar", version="1.0")
    recipe = _make_recipe(
        tmp_path,
        monkeypatch,
        name="foo",
        conandata={"requires": ["zzz/9.0@denver/snapshot#x", "bar/1.0@denver/snapshot#oldrev"]},
    )
    catalog.recipes = [recipe, dep]
    # only wire the bar dependency (zzz is left as an unrelated, already-sorted-after entry)
    recipe.conandata_yml.data["requires"] = ["bar/1.0@denver/snapshot#oldrev", "zzz/9.0@denver/snapshot#x"]
    dep.users["requires"] = [recipe]

    monkeypatch.setattr(
        build_catalog.get_rrev,
        "compute_rrev",
        lambda d: ("bar", "1.0", "newrev") if "bar" in str(d) else (None, None, None),
    )
    catalog.update_rrevs_recursively(dep)

    saved = build_catalog.ConfigFile(recipe.conandata_yml.path, read=True)
    assert saved.data["requires"] == sorted(["bar/1.0@denver/snapshot#newrev", "zzz/9.0@denver/snapshot#x"])


def test_catalog_update_rrevs_recursively_exact_match_not_prefix(tmp_path, monkeypatch):
    # bar/1.0 and bar/1.0.1 coexist as separate 'requires' entries of foo --
    # updating bar/1.0 must match only its own exact entry, not also
    # 'bar/1.0.1@...' via a startswith() prefix (B1).
    catalog = build_catalog.Catalog()
    dep = _make_recipe(tmp_path, monkeypatch, name="bar", version="1.0")
    recipe = _make_recipe(
        tmp_path,
        monkeypatch,
        name="foo",
        conandata={
            "requires": ["bar/1.0@denver/snapshot#oldrev", "bar/1.0.1@denver/snapshot#otherrev"],
        },
    )
    recipe.conandata_yml.data["requires"] = ["bar/1.0@denver/snapshot#oldrev", "bar/1.0.1@denver/snapshot#otherrev"]
    dep.users["requires"] = [recipe]
    catalog.recipes = [recipe, dep]

    monkeypatch.setattr(build_catalog.get_rrev, "compute_rrev", lambda d: ("bar", "1.0", "newrev"))
    catalog.update_rrevs_recursively(dep)  # must not raise ("too many values to unpack")

    assert "bar/1.0@denver/snapshot#newrev" in recipe.conandata_yml.data["requires"]
    assert "bar/1.0.1@denver/snapshot#otherrev" in recipe.conandata_yml.data["requires"]


def test_catalog_update_rrevs_recursively_cycle_terminates(tmp_path, monkeypatch):
    # an (accidental) dependency cycle must terminate instead of recursing
    # forever (B8).
    catalog = build_catalog.Catalog()
    a = _make_recipe(
        tmp_path, monkeypatch, name="a", version="1.0", conandata={"requires": ["b/1.0@denver/snapshot#old"]}
    )
    b = _make_recipe(
        tmp_path, monkeypatch, name="b", version="1.0", conandata={"requires": ["a/1.0@denver/snapshot#old"]}
    )
    a.conandata_yml.data["requires"] = ["b/1.0@denver/snapshot#old"]
    b.conandata_yml.data["requires"] = ["a/1.0@denver/snapshot#old"]
    a.users["requires"] = [b]
    b.users["requires"] = [a]
    catalog.recipes = [a, b]

    monkeypatch.setattr(
        build_catalog.get_rrev,
        "compute_rrev",
        lambda d: ("a", "1.0", "newrev-a") if Path(d).parent.name == "a" else ("b", "1.0", "newrev-b"),
    )

    catalog.update_rrevs_recursively(a)  # must terminate, not recurse forever

    assert "b/1.0@denver/snapshot#newrev-b" in a.conandata_yml.data["requires"]
    assert "a/1.0@denver/snapshot#newrev-a" in b.conandata_yml.data["requires"]


def test_catalog_get_json_and_write_catalog(tmp_path, monkeypatch, capsys):
    catalog = build_catalog.Catalog()
    recipe = _make_recipe(tmp_path, monkeypatch, name="foo", version="1.0")
    recipe.rrev = "deadbeef"
    catalog.recipes = [recipe]

    j = catalog.get_json()
    assert "foo/1.0" in j

    out_file = tmp_path / "catalog.yml"
    catalog.write_catalog(str(out_file))
    written = yaml.safe_load(out_file.read_text())
    assert written["foo/1.0"] == "foo/1.0@denver/snapshot#deadbeef"
    assert "Successfully created" in capsys.readouterr().out


def test_catalog_add_recipe_dirs_runs_check(tmp_path, monkeypatch):
    recipe_dir = tmp_path / "recipes" / "foo" / "1.0"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "conanfile.py").write_text("x")
    (recipe_dir / "conandata.yml").write_text("{}")
    monkeypatch.setattr(build_catalog.get_rrev, "compute_rrev", lambda d: ("foo", "1.0", "rev"))
    monkeypatch.setattr(build_catalog.get_rrev, "inspect", lambda path, attrs: {"name": "foo", "version": "1.0"})
    monkeypatch.setattr(build_catalog, "workdir", tmp_path)

    catalog = build_catalog.Catalog()
    catalog.add_recipe_dirs([str(recipe_dir)])
    assert len(catalog.recipes) == 1


# --------------------------------------------------------------------------- #
# main()
# --------------------------------------------------------------------------- #
def test_main_end_to_end(tmp_path, monkeypatch):
    recipes_dir = tmp_path / "recipes"
    recipe_dir = recipes_dir / "foo" / "1.0"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "conanfile.py").write_text("x")
    (recipe_dir / "conandata.yml").write_text("{}")

    monkeypatch.setattr(build_catalog.get_rrev, "compute_rrev", lambda d: ("foo", "1.0", "rev"))
    monkeypatch.setattr(build_catalog.get_rrev, "inspect", lambda path, attrs: {"name": "foo", "version": "1.0"})
    monkeypatch.setattr(build_catalog, "workdir", tmp_path)

    out = tmp_path / "out.yml"
    monkeypatch.setattr(sys, "argv", ["build_catalog.py", f"--recipes-dir={recipes_dir}", f"-o={out}"])
    build_catalog.main()

    written = yaml.safe_load(out.read_text())
    assert "foo/1.0" in written


def test_main_user_channel_flags_set_reference(tmp_path, monkeypatch):
    # --user/--channel (denver.yml's conan.user:/conan.channel:, wired
    # through conan.py -> recipes.py -> here) replace the default
    # "denver"/"snapshot" user/channel in every generated reference.
    recipes_dir = tmp_path / "recipes"
    recipe_dir = recipes_dir / "foo" / "1.0"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "conanfile.py").write_text("x")
    (recipe_dir / "conandata.yml").write_text("{}")

    monkeypatch.setattr(build_catalog.get_rrev, "compute_rrev", lambda d: ("foo", "1.0", "rev"))
    monkeypatch.setattr(build_catalog.get_rrev, "inspect", lambda path, attrs: {"name": "foo", "version": "1.0"})
    monkeypatch.setattr(build_catalog, "workdir", tmp_path)

    out = tmp_path / "out.yml"
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_catalog.py", f"--recipes-dir={recipes_dir}", f"-o={out}", "--user=acme", "--channel=stable"],
    )
    build_catalog.main()

    written = yaml.safe_load(out.read_text())
    assert written["foo/1.0"] == "foo/1.0@acme/stable#rev"
    # --user/--channel are threaded through Catalog/Recipe explicitly (no
    # module-level global left to reset here -- see build_catalog.py's
    # DEFAULT_CONAN_USER/DEFAULT_CONAN_CHANNEL).


def test_main_without_output_prints_and_writes_nothing(tmp_path, monkeypatch, capsys):
    # no -o: the catalog is printed, never written -- this used to drop a
    # catalog.yml into workdir that nobody asked for.
    recipes_dir = tmp_path / "recipes"
    recipe_dir = recipes_dir / "foo" / "1.0"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "conanfile.py").write_text("x")
    (recipe_dir / "conandata.yml").write_text("{}")

    monkeypatch.setattr(build_catalog.get_rrev, "compute_rrev", lambda d: ("foo", "1.0", "rev"))
    monkeypatch.setattr(build_catalog.get_rrev, "inspect", lambda path, attrs: {"name": "foo", "version": "1.0"})
    monkeypatch.setattr(build_catalog, "workdir", tmp_path)
    monkeypatch.setattr(sys, "argv", ["build_catalog.py"])

    build_catalog.main()

    assert "foo/1.0: foo/1.0@denver/snapshot#rev" in capsys.readouterr().out
    assert not (tmp_path / "catalog.yml").exists()
    assert not (recipes_dir / "catalog.yml").exists()


def test_build_returns_resolved_catalog(tmp_path, monkeypatch):
    recipes_dir = tmp_path / "recipes"
    recipe_dir = recipes_dir / "foo" / "1.0"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "conanfile.py").write_text("x")
    (recipe_dir / "conandata.yml").write_text("{}")

    monkeypatch.setattr(build_catalog.get_rrev, "compute_rrev", lambda d: ("foo", "1.0", "rev"))
    monkeypatch.setattr(build_catalog.get_rrev, "inspect", lambda path, attrs: {"name": "foo", "version": "1.0"})

    catalog = build_catalog.build([recipes_dir], user="acme", channel="stable")

    assert catalog.get_references() == {"foo/1.0": "foo/1.0@acme/stable#rev"}
    assert list(recipes_dir.glob("**/catalog.yml")) == []


def _make_recipe_tree(root, name, monkeypatch=None):
    """Create <root>/<name>/1.0/{conanfile.py,conandata.yml}."""
    recipe_dir = root / name / "1.0"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "conanfile.py").write_text("x")
    (recipe_dir / "conandata.yml").write_text("{}")
    return recipe_dir


def test_build_covers_every_dir_it_is_given(tmp_path, monkeypatch):
    # a unit's recipe-dirs resolve as ONE catalog (see build()'s docstring),
    # so what a catalog contains follows from which dirs are passed together.
    base = tmp_path / "base" / "recipes"
    layer = tmp_path / "layer" / "recipes"
    _make_recipe_tree(base, "foo")
    _make_recipe_tree(layer, "bar")

    monkeypatch.setattr(build_catalog.get_rrev, "compute_rrev", lambda d: (Path(d).parents[0].name, "1.0", "rev"))
    monkeypatch.setattr(
        build_catalog.get_rrev,
        "inspect",
        lambda path, attrs: {"name": Path(path).parents[1].name, "version": "1.0"},
    )

    assert build_catalog.build([base]).get_references() == {"foo/1.0": "foo/1.0@denver/snapshot#rev"}
    assert build_catalog.build([base, layer]).get_references() == {
        "bar/1.0": "bar/1.0@denver/snapshot#rev",
        "foo/1.0": "foo/1.0@denver/snapshot#rev",
    }
