import copy

import yaml
from conan import ConanFile
from conan.internal.util.files import load


class ConanRecipe(ConanFile):
    name = "zephyr-devshell"
    version = "4.3.1"
    url = "TBD"

    def tool_requires_from_catalog(self, name, **kwargs):
        catalog = yaml.safe_load(load(self.recipe_path / "recipes" / "catalog.yml"))

        # pick up the common tool recipes (ccache, clang, cmake, ...) shared across all
        # zephyr-devshell-* envs, generated into examples/zephyr-devshell/conan/recipes/catalog.yml
        catalog_common_path = self.recipe_path.parents[1] / "zephyr-devshell" / "conan" / "recipes" / "catalog.yml"
        catalog_common = yaml.safe_load(load(catalog_common_path)) if catalog_common_path.exists() else {}

        all_catalogs = copy.deepcopy(catalog_common)
        all_catalogs.update(catalog)
        all_catalogs = {k: v for k, v in all_catalogs.items() if not k.startswith(".")}

        if name not in all_catalogs:
            print(yaml.dump(all_catalogs))
            self.output.error(f"Error: '{name}' is not contained in the catalog.")
            raise ValueError("Error: Please use one of \n - {}".format("\n - ".join(sorted(all_catalogs))))

        self.tool_requires(all_catalogs[name], **kwargs)

    def build_requirements(self):
        self.tool_requires_from_catalog("ccache/4.13")
        self.tool_requires_from_catalog("clang/21.1.4")
        self.tool_requires_from_catalog("cmake/3.31.9")
        self.tool_requires_from_catalog("doxygen/1.15.0")
        self.tool_requires_from_catalog("jlink/8.82")
        self.tool_requires_from_catalog("ninja/1.13.2")
        self.tool_requires_from_catalog("protoc/33.2")
        self.tool_requires_from_catalog("python-cache/denver")
        self.tool_requires_from_catalog("systemview/3.62b")
        self.tool_requires_from_catalog("west-blobs-cache/denver")
        self.tool_requires_from_catalog("zephyr-sdk/0.17.4")
