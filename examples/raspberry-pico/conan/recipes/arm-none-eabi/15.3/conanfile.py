from pathlib import Path

from conan.tools.files import chdir
from DenverConanFile import DenverConanFile


class ConanRecipe(DenverConanFile):
    name = "arm-none-eabi"
    version = "15.3"
    description = "arm-toolchain (x86_64 Linux hosted cross toolchain)"
    url = "https://gitlab.arm.com/tooling/gnu-toolchains-for-arm/-/tree/releases/15.3.rel1"
    license = "GPL-3.0-with-GCC-exception"  # assumed to cover the whole prebuilt archive
    settings = "os", "arch"
    no_copy_source = True
    exports_sources = (f"arm-gnu-toolchain-{version}.rel1-x86_64-arm-none-eabi.tar.xz",)

    def source(self):
        pass  # prebuilt archive: nothing to fetch beyond what export_sources() already staged

    def build(self):
        package_dir = Path(self.package_folder)
        package_dir.mkdir(parents=True, exist_ok=True)
        with chdir(self, package_dir):
            archive = Path(self.source_folder) / self.exports_sources[0]
            self.run(f"tar -xf {archive}")
            self.run("mv */* .")  # strip the archive's own top-level folder
