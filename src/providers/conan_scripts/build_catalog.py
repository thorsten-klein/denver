#!/usr/bin/env python3
"""Generates catalog.yml from a tree of conan recipes.

Walks a recipes directory for conandata.yml files, resolves each recipe's
requires/tool_requires against the others, computes each one's RREV (see
get_rrev.py) and propagates a changed RREV to every recipe that depends on
it, then writes the resulting name -> full_reference mapping to catalog.yml.
Invoked by recipes.py's generate_catalog() as a subprocess.
"""

import argparse
from collections import OrderedDict
from pathlib import Path

import yaml
from conan.tools.files import load, save

try:
    # invoked as a real subprocess (its own sys.path[0] is this directory)
    import get_rrev
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
        get_rrev.get_RREV(self.recipe_dir)  # this will re-generate conandata.yml
        self.conandata_yml.read()

    def add_dependency(self, kind, recipe):
        """Add ``recipe`` to this recipe's ``kind`` ("requires"/"tool_requires") list, deduped by name+version."""
        assert kind in ["requires", "tool_requires"]

        def find(in_list, recipe):
            return any(r.name == recipe.name and r.version == recipe.version for r in in_list)

        kind_members = getattr(self, kind)
        if not find(kind_members, recipe):
            kind_members.append(recipe)

    def get_full_reference(self, RREV=True):
        """Return this recipe's full conan reference, e.g. 'name/version@user/channel#rrev'."""
        if not self.rrev:
            raise GenerateError(f"Error: rrev not set for '{self.recipe_dir}'")
        reference_str = f"{self.name}/{self.version}@{self.user}/{self.channel}"
        if RREV:
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
        _, _, rrev = get_rrev.get_RREV(self.recipe_dir)
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

    def resolve_dependencies_recursively(self, recipe):
        """Link ``recipe``'s requires/tool_requires (from its conandata.yml) to their loaded Recipe objects."""
        if not recipe.conandata_yml:
            return
        for kind in ["requires", "tool_requires"]:
            if kind not in recipe.conandata_yml.data:
                continue

            for reference in recipe.conandata_yml.data[kind]:
                ref_name, ref_version = _reference_name_version(reference)
                dep_recipe = self.find_recipe(ref_name, ref_version)
                if not dep_recipe:
                    # find_recipe() above already matches on the referenced
                    # version; a same-named recipe at a *different* version
                    # means the reference is just stale, not missing --
                    # point at the version it should be updated to instead
                    # of a generic "not found".
                    same_name = [r for r in self.recipes if r.name == ref_name]
                    if len(same_name) == 1:
                        raise GenerateError(
                            f"Error: You should update reference for '{ref_name}' in "
                            f"'{recipe.conandata_yml.path}' ({kind}) to version "
                            f"'{same_name[0].version}' (currently '{ref_version}')."
                        )
                    raise GenerateError(
                        f"Error: Could not find any dependency '{ref_name}/{ref_version}' (used in {recipe.conandata_yml.path}"
                    )
                if kind not in dep_recipe.users:
                    dep_recipe.users[kind] = []

                dep_recipe.users[kind] += [recipe]
                self.resolve_dependencies_recursively(dep_recipe)

                recipe.add_dependency(kind, dep_recipe)

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
            recipe_reference = recipe.get_full_reference(RREV=True)

            for user in users:
                do_save = False
                user_dependencies = user.conandata_yml.data[kind]
                # find *the* entry for this recipe by its exact (name, version)
                # (ignoring whatever RREV it currently pins) -- there must be
                # exactly one, since resolve_dependencies_recursively already
                # matched this user to this recipe via that same reference.
                # Matched on the parsed tuple, not a startswith() prefix, so
                # e.g. 'bar/1.0' can never accidentally match a coexisting
                # 'bar/1.0.1@...' entry.
                (user_dependency,) = [
                    x for x in user_dependencies if _reference_name_version(x) == (recipe.name, recipe.version)
                ]
                if user_dependency != recipe_reference:
                    do_save = True
                    print(f"Info: updated {recipe_reference} in {user.conandata_yml.path}")
                    user_dependencies.remove(user_dependency)  # remove old
                    user_dependencies.append(recipe_reference)  # add new
                if user_dependencies != sorted(user_dependencies):
                    print(f"Info: {user.conandata_yml.path} needs to be re-generated")
                    do_save = True
                user.conandata_yml.data[kind] = sorted(user_dependencies)  # ensure it is sorted
                if do_save:
                    user.conandata_yml.save_file()
                    self.update_rrevs_recursively(user, _visited)  # update its users to apply changes

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

    def write_catalog(self, output_file_path):
        """Write every recipe's full_reference to ``output_file_path``."""
        catalog = ConfigFile(output_file_path)
        catalog.data = {key: val["full_reference"] for key, val in self.get_json().items()}
        print(catalog.get_data())
        catalog.save_file()
        print("----------------------------------------------")
        print(f"Successfully created '{output_file_path}'!")
        print("----------------------------------------------")


def main():
    """CLI entry point: build a Catalog from --recipes-dir, resolve/update it, and write --output."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Output catalog file (YAML format)",
    )
    parser.add_argument(
        "--recipes-dir",
        type=str,
        help="Path to conan recipes",
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

    recipes_dir = Path(args.recipes_dir or workdir / "recipes").resolve()
    recipes = [p.parent for p in recipes_dir.glob("**/conandata.yml")]

    catalog = Catalog(user=args.user, channel=args.channel)
    catalog.add_recipe_dirs(sorted(recipes))
    catalog.resolve_all_dependencies()
    catalog.update_all_rrevs()

    catalog_yml = args.output or workdir / "catalog.yml"
    catalog.write_catalog(catalog_yml)


if __name__ == "__main__":
    main()
