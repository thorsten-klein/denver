"""Shared ConanFile base for the recipes' own test_package/conanfile.py files."""

from pathlib import Path

from conan import ConanFile
from conan.api.model import RecipeReference
from conan.tools.cmake import CMakeToolchain


class DenverConanFileTest(ConanFile):
    settings = "os", "arch", "compiler", "build_type"

    def layout(self):
        # keep cmake's generated/build files under a dedicated subfolder
        # instead of dumping them straight into the build folder
        test_build = Path(self.folders.build) / "test_build"
        self.folders.build = str(test_build)
        self.folders.generators = str(test_build)

    def generate(self):
        toolchain = CMakeToolchain(self)
        toolchain.generate()
        return toolchain

    def get_ref(self, ref_str):
        """Parse a conan reference string, e.g. to pull the version back out of tested_reference_str."""
        return RecipeReference.loads(ref_str)
