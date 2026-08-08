import yaml
from conan import ConanFile
from conan.internal.util.files import load


class ConanRecipe(ConanFile):
    name = "zephyr-devshell"
    version = "4.3.1"
    url = "TBD"

    def tool_requires_from_catalog(self, name, **kwargs):
        # this unit's catalog, written by denver next to this conanfile (see
        # 'catalog:' in ../denver.yml). It already covers every recipe dir the
        # unit installs from -- this env's own conan/recipes and the shared
        # ../zephyr-devshell/conan/recipes (ccache, clang, cmake, ...) alike --
        # so there is exactly one file to read, with no second catalog to
        # merge in.
        catalog = yaml.safe_load(load(self.recipe_path / "catalog.yml"))
        catalog = {k: v for k, v in catalog.items() if not k.startswith(".")}

        if name not in catalog:
            print(yaml.dump(catalog))
            self.output.error(f"Error: '{name}' is not contained in the catalog.")
            raise ValueError("Error: Please use one of \n - {}".format("\n - ".join(sorted(catalog))))

        self.tool_requires(catalog[name], **kwargs)

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
