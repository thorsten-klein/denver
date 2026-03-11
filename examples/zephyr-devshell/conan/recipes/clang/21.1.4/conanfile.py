from pathlib import Path

from conan.tools.files import chdir
from DenverConanFile import DenverConanFile


class ConanRecipe(DenverConanFile):
    name = "clang"
    version = "21.1.4"
    description = "clang compiler"
    url = "https://github.com/llvm/llvm-project/"
    license = "LLVM-exception"  # assumed to cover the whole prebuilt archive
    settings = "os", "arch"
    no_copy_source = True
    exports_sources = (f"LLVM-{version}-Linux-X64.tar.xz",)

    def source(self):
        pass  # prebuilt archive: nothing to fetch beyond what export_sources() already staged

    def build(self):
        package_dir = Path(self.package_folder)
        package_dir.mkdir(parents=True, exist_ok=True)
        with chdir(self, package_dir):
            archive = Path(self.source_folder) / self.exports_sources[0]
            self.run(f"tar -xf {archive}")
            self.run("mv */* .")
            self.run("cd bin && ln -s ld.lld ld")
