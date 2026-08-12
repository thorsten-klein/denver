#!/usr/bin/env python3
"""Builds a recipe catalog from a tree of conan recipes.

Walks a recipes directory for conandata.yml files, resolves each recipe's
requires/tool_requires against the others, computes each one's RREV (see
get_rrev.py) and propagates a changed RREV to every recipe that depends on
it, producing a name -> full_reference mapping.

That mapping is a return value, not a file: recipes.py's generate_catalog()
imports build() and hands ``Catalog.get_references()`` straight to its export
step. A catalog.yml is only ever written when something explicitly asks for
one -- ``--output`` here, ``--export-catalog`` in recipes.py, denver.yml's
``conan.export-catalog:`` -- so running a build never leaves a generated
file behind in the recipe tree.
"""

import argparse
from collections import OrderedDict
from pathlib import Path

import yaml
from conan.tools.files import load, save

try:
    # invoked as a real subprocess (its own sys.path[0] is this directory) --
    # see the [[tool.mypy.overrides]] for this same module in pyproject.toml.
    import get_rrev  # pyright: ignore[reportMissingImports]
except ImportError:
    # imported as providers.conan_scripts.build_catalog, e.g. by denver's own tests
    from . import get_rrev

workdir = Path(__file__).parents[1]

# denver.yml's conan.user:/conan.channel: (never a real environment variable
# -- see ConanProvider's own docstring) default to these. Threaded explicitly
# through Catalog/Recipe (see main()) rather than a module-level global so
# construction stays reentrant and doesn't leak between denver's own tests.
DEFAULT_CONAN_USER = "denver"
DEFAULT_CONAN_CHANNEL = "snapshot"


def _reference_name_version(reference):
    """Parse the '(name, version)' out of a conan reference 'name/version@user/channel[#rrev]'."""
    name_version = reference.split("@")[0]
    name, version = name_version.split("/")
    return name, version


class GenerateError(Exception):
    """Raised for catalog-generation failures in this script."""


class ConfigFile:
    """Thin wrapper around a YAML file (conandata.yml / catalog.yml)."""

    def __init__(self, path, read=False):
        """Store ``path``; optionally load it immediately via read()."""
        self.path = path
        self.data = OrderedDict()
        if read:
            self.read()

    def read(self):
        """Load ``self.path`` as YAML into ``self.data`` (empty dict for an empty/missing file)."""
        self.data = yaml.safe_load(load(object, self.path))
        if not self.data:
            self.data = {}  # yaml does not support OrderedDict

    def get_data(self):
        """Serialize ``self.data`` to YAML text ("" if empty)."""
        if not self.data:
            return ""
        return yaml.safe_dump(self.data, default_flow_style=False)  # sorted by default

    def save_file(self):
        """Write ``self.data`` back to ``self.path`` as YAML."""
        save(object, self.path, self.get_data())


class Recipe:
    """One recipe directory: its conandata.yml, name/version, RREV, and resolved dependencies."""

    def __init__(self, recipe_dir, *, user=DEFAULT_CONAN_USER, channel=DEFAULT_CONAN_CHANNEL):
        """Load ``recipe_dir``'s current conandata.yml and its name/version from the conanfile.

        Side-effect-free (no network I/O): this only reads what's already on
        disk. Call regenerate_conandata_yml() explicitly (Catalog.add_recipe_dirs
        does this) to recompute the RREV and rewrite conandata.yml -- which
        may need to fetch a not-yet-local exports_source.
        """
        self.recipe_dir = recipe_dir
        self.user = user
        self.channel = channel

        conandata_yml_path = Path(recipe_dir) / "conandata.yml"
        self.conandata_yml = ConfigFile(conandata_yml_path, read=True)

        inspect = get_rrev.inspect(Path(recipe_dir) / "conanfile.py", ["name", "version"])
        self.name = inspect["name"]
        self.version = inspect["version"]

        # init fields for later filling
        self.rrev = None
        self.users = {}
        self.requires = []
        self.tool_requires = []

    def regenerate_conandata_yml(self):
        """Recompute this recipe's RREV (may fetch a not-yet-local exports_source) and reload conandata.yml.

        Not called by __init__ -- constructing a Recipe must stay
        side-effect-free; the caller (Catalog.add_recipe_dirs) calls this
        explicitly as a separate step.
        """
        get_rrev.compute_rrev(self.recipe_dir)  # this will re-generate conandata.yml
        self.conandata_yml.read()

    def add_dependency(self, kind, recipe):
        """Add ``recipe`` to this recipe's ``kind`` ("requires"/"tool_requires") list, deduped by name+version."""
        assert kind in ["requires", "tool_requires"]

        def find(in_list, recipe):
            return any(r.name == recipe.name and r.version == recipe.version for r in in_list)

        kind_members = getattr(self, kind)
        if not find(kind_members, recipe):
            kind_members.append(recipe)

    def get_full_reference(self, rrev=True):
        """Return this recipe's full conan reference, e.g. 'name/version@user/channel#rrev'."""
        if not self.rrev:
            raise GenerateError(f"Error: rrev not set for '{self.recipe_dir}'")
        reference_str = f"{self.name}/{self.version}@{self.user}/{self.channel}"
        if rrev:
            reference_str += f"#{self.rrev}"
        return reference_str

    def check(self):
        """Die if the recipe_dir's name/version/name-of-parent don't match what the conanfile itself declares."""
        version_from_dir = Path(self.recipe_dir).name
        if version_from_dir != self.version:
            raise GenerateError(
                f"Folder name '{version_from_dir}' of '{self.recipe_dir}' does not match the version inside conanfile '{self.version}'"
            )

        name_from_dir = Path(self.recipe_dir).parent.name
        if name_from_dir != self.name:
            raise GenerateError(
                f"Folder name '{name_from_dir}' of '{self.recipe_dir}' does not match the name inside conanfile '{self.name}'"
            )

    def calculate_rrev(self):
        """Recompute and return this recipe's current RREV (does not mutate ``self.rrev``)."""
        _, _, rrev = get_rrev.compute_rrev(self.recipe_dir)
        return rrev

    def get_json(self):
        """This recipe's catalog entry: name, version, rrev, full_reference."""
        return OrderedDict({
            "name": self.name,
            "version": self.version,
            "rrev": self.rrev,
            "full_reference": self.get_full_reference(),
        })


class Catalog:
    """The full set of recipes discovered under a recipes directory, with their dependencies resolved."""

    def __init__(self, *, user=DEFAULT_CONAN_USER, channel=DEFAULT_CONAN_CHANNEL):
        """Start with no recipes loaded; populate via add_recipe_dirs(). ``user``/``channel`` go into every Recipe."""
        self.recipes = []
        self.user = user
        self.channel = channel

    def add_recipe_dirs(self, recipe_dirs):
        """Load, sync (regenerate_conandata_yml()) and validate (check()) a Recipe from each directory."""
        for recipe_dir in recipe_dirs:
            recipe = Recipe(recipe_dir, user=self.user, channel=self.channel)
            recipe.regenerate_conandata_yml()
            recipe.check()
            self.recipes += [recipe]

    def find_recipe(self, name, version):
        """Return the loaded Recipe matching ``name``/``version`` exactly, or None."""
        retval = None
        m_recipes = [recipe for recipe in self.recipes if name == recipe.name]
        for recipe in m_recipes:
            if version == recipe.version:
                retval = recipe
                break
        return retval

    def _dependency_not_found_error(self, recipe, kind, ref_name, ref_version):
        """Build the GenerateError for a requires/tool_requires reference resolve_dependencies_recursively can't find.

        find_recipe() already matches on the referenced version; a
        same-named recipe at a *different* version means the reference is
        just stale, not missing -- point at the version it should be
        updated to instead of a generic "not found".
        """
        same_name = [r for r in self.recipes if r.name == ref_name]
        if len(same_name) == 1:
            return GenerateError(
                f"Error: You should update reference for '{ref_name}' in "
                f"'{recipe.conandata_yml.path}' ({kind}) to version "
                f"'{same_name[0].version}' (currently '{ref_version}')."
            )
        return GenerateError(
            f"Error: Could not find any dependency '{ref_name}/{ref_version}' (used in {recipe.conandata_yml.path}"
        )

    @staticmethod
    def _references_of(recipe, kind):
        """The raw ``kind`` ("requires"/"tool_requires") reference strings in ``recipe``'s conandata.yml."""
        if not recipe.conandata_yml:
            return []
        return recipe.conandata_yml.data.get(kind) or []

    def _link_dependency(self, recipe, kind, reference):
        """Link one ``kind`` reference of ``recipe`` to the loaded Recipe it names, in both directions."""
        ref_name, ref_version = _reference_name_version(reference)
        dep_recipe = self.find_recipe(ref_name, ref_version)
        if not dep_recipe:
            raise self._dependency_not_found_error(recipe, kind, ref_name, ref_version)

        dep_recipe.users.setdefault(kind, []).append(recipe)
        self.resolve_dependencies_recursively(dep_recipe)
        recipe.add_dependency(kind, dep_recipe)

    def resolve_dependencies_recursively(self, recipe):
        """Link ``recipe``'s requires/tool_requires (from its conandata.yml) to their loaded Recipe objects."""
        for kind in ["requires", "tool_requires"]:
            for reference in self._references_of(recipe, kind):
                self._link_dependency(recipe, kind, reference)

    @staticmethod
    def _pinned_entry(user_dependencies, recipe):
        """The one entry in ``user_dependencies`` naming ``recipe``, whatever RREV it currently pins.

        There must be exactly one, since resolve_dependencies_recursively
        already matched this user to this recipe via that same reference.
        Matched on the parsed (name, version) tuple, not a startswith()
        prefix, so e.g. 'bar/1.0' can never accidentally match a coexisting
        'bar/1.0.1@...' entry.
        """
        (entry,) = [x for x in user_dependencies if _reference_name_version(x) == (recipe.name, recipe.version)]
        return entry

    def _repin_user(self, user, kind, recipe, recipe_reference):
        """Point ``user``'s ``kind`` entry for ``recipe`` at ``recipe_reference``, sorted. True if it changed."""
        do_save = False
        user_dependencies = user.conandata_yml.data[kind]
        user_dependency = self._pinned_entry(user_dependencies, recipe)
        if user_dependency != recipe_reference:
            do_save = True
            print(f"Info: updated {recipe_reference} in {user.conandata_yml.path}")
            user_dependencies.remove(user_dependency)  # remove old
            user_dependencies.append(recipe_reference)  # add new
        if user_dependencies != sorted(user_dependencies):
            print(f"Info: {user.conandata_yml.path} needs to be re-generated")
            do_save = True
        user.conandata_yml.data[kind] = sorted(user_dependencies)  # ensure it is sorted
        return do_save

    def _propagate_rrev(self, recipe, kind, users, _visited):
        """Re-pin every recipe in ``users`` to ``recipe``'s current reference, recursing into the ones that moved."""
        recipe_reference = recipe.get_full_reference(rrev=True)
        for user in users:
            if self._repin_user(user, kind, recipe, recipe_reference):
                user.conandata_yml.save_file()
                self.update_rrevs_recursively(user, _visited)  # update its users to apply changes

    def update_rrevs_recursively(self, recipe, _visited=None):
        """Recompute ``recipe``'s RREV and propagate it into every dependent recipe's conandata.yml, recursively.

        ``_visited`` is an internal ``{(name, version)}`` set, fresh for
        every top-level call: it stops a diamond-shaped dependency graph from
        being walked repeatedly (each shared dependency re-triggering every
        path above it again) and stops a dependency cycle from recursing
        forever -- a recipe already visited in this call tree is skipped.
        """
        if _visited is None:
            _visited = set()
        key = (recipe.name, recipe.version)
        if key in _visited:
            return
        _visited.add(key)

        # ensure that rrev is set / up-to-date
        recipe.rrev = recipe.calculate_rrev()

        for kind, users in recipe.users.items():
            self._propagate_rrev(recipe, kind, users, _visited)

    def update_all_rrevs(self):
        """update_rrevs_recursively() for every loaded recipe."""
        for recipe in self.recipes:
            self.update_rrevs_recursively(recipe)

    def resolve_all_dependencies(self):
        """resolve_dependencies_recursively() for every loaded recipe."""
        for recipe in self.recipes:
            self.resolve_dependencies_recursively(recipe)

    def get_json(self):
        """Every recipe's catalog entry, keyed by 'name/version', sorted."""
        json_dict = OrderedDict()
        for recipe in self.recipes:
            json_dict[f"{recipe.name}/{recipe.version}"] = recipe.get_json()
        return OrderedDict(sorted(json_dict.items()))

    def get_references(self):
        """The catalog itself: every recipe's 'name/version' -> full reference, sorted.

        This -- not a file on disk -- is what a catalog *is* to every caller
        (see the module docstring); write_catalog() below just serializes it
        for the callers that asked for a file.
        """
        return {key: val["full_reference"] for key, val in self.get_json().items()}

    def write_catalog(self, output_file_path):
        """Write every recipe's full_reference to ``output_file_path``."""
        catalog = ConfigFile(output_file_path)
        catalog.data = self.get_references()
        print(catalog.get_data())
        catalog.save_file()
        print("----------------------------------------------")
        print(f"Successfully created '{output_file_path}'!")
        print("----------------------------------------------")


def build(recipes_dirs, *, user=DEFAULT_CONAN_USER, channel=DEFAULT_CONAN_CHANNEL):
    """Return a fully resolved Catalog of every recipe found under ``recipes_dirs``.

    The whole pipeline (discover -> load -> resolve dependencies -> update
    RREVs) with nothing written out: recipes.py calls this directly and uses
    the returned Catalog's get_references(), main() below calls it and then
    decides what to do with the result.

    All the dirs are resolved as *one* catalog (that's what a denver
    ``conanfiles:`` unit is), so a recipe in one dir may require a recipe in
    another -- which also means the result depends on which dirs are passed
    together.
    """
    recipes = [p.parent for d in recipes_dirs for p in Path(d).glob("**/conandata.yml")]
    catalog = Catalog(user=user, channel=channel)
    catalog.add_recipe_dirs(sorted(recipes))
    catalog.resolve_all_dependencies()
    catalog.update_all_rrevs()
    return catalog


def main():
    """CLI entry point: build a Catalog from --recipes-dir, then write --output (or print it)."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Write the catalog to this file (YAML). Without it the catalog is printed, not written.",
    )
    parser.add_argument(
        "--recipes-dir",
        type=str,
        action="append",
        default=[],
        help="Path to conan recipes (repeatable -- all dirs form one catalog)",
    )
    parser.add_argument(
        "--user",
        type=str,
        default=DEFAULT_CONAN_USER,
        help="conan user for each generated reference (denver.yml's conan.user:)",
    )
    parser.add_argument(
        "--channel",
        type=str,
        default=DEFAULT_CONAN_CHANNEL,
        help="conan channel for each generated reference (denver.yml's conan.channel:)",
    )
    args = parser.parse_args()

    recipes_dirs = [Path(d).resolve() for d in args.recipes_dir] or [(workdir / "recipes").resolve()]
    catalog = build(recipes_dirs, user=args.user, channel=args.channel)

    # no --output: print the catalog instead of writing a catalog.yml nobody
    # asked for (this used to default to workdir/catalog.yml).
    if not args.output:
        print(yaml.safe_dump(catalog.get_references(), default_flow_style=False))
        return
    catalog.write_catalog(args.output)


if __name__ == "__main__":
    main()
