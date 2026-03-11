from pathlib import Path

from DenverConanFile import DenverConanFile


class ConanRecipe(DenverConanFile):
    name = "cmake"
    version = "3.31.9"
    description = "Official prebuilt cmake binary release"
    url = "https://github.com/Kitware/CMake"
    license = "BSD-3-Clause"
    settings = "arch"
    exports_sources = (f"cmake-{version}-linux-x86_64.tar.gz",)

    def build(self):
        super().build()
        package_dir = Path(self.package_folder)
        package_dir.mkdir(parents=True, exist_ok=True)
        self.run(f"cp -r {self.source_folder}/cmake/* {package_dir}")
