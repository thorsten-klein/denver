import yaml
from conan import ConanFile
from conan.internal.util.files import load


class ConanRecipe(ConanFile):
    name = "zephyr-devshell"
    version = "4.3.1"
    url = "TBD"

    def tool_requires_from_catalog(self, name, **kwargs):
        # this unit's catalog, written by denver next to this conanfile (see
        # 'catalog:' in ../denver.toml). It already covers every recipe dir the
        # unit installs from -- its own conan/recipes and the reused
        # ../zephyr-devshell/conan/recipes/cmake alike -- so there is exactly
        # one file to read, with no second catalog to merge in.
        catalog = yaml.safe_load(load(self.recipe_path / "catalog.yml"))
        catalog = {k: v for k, v in catalog.items() if not k.startswith(".")}

        if name not in catalog:
            print(yaml.dump(catalog))
            self.output.error(f"Error: '{name}' is not contained in the catalog.")
            raise ValueError("Error: Please use one of \n - {}".format("\n - ".join(sorted(catalog))))

        self.tool_requires(catalog[name], **kwargs)

    def build_requirements(self):
        self.tool_requires_from_catalog("pico-sdk/2.3.0")  # ships picotool too
        self.tool_requires_from_catalog("arm-none-eabi/15.3")
        self.tool_requires_from_catalog("cmake/3.31.9")
