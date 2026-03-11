from pathlib import Path

from DenverConanFile import DenverConanFile


class ConanRecipe(DenverConanFile):
    name = "doxygen"
    version = "1.15.0"
    description = "Official prebuilt doxygen binary release"
    url = "https://github.com/doxygen/doxygen/releases"
    license = "GPL-2.0-or-later"
    settings = "os", "arch"
    exports_sources = (f"doxygen-{version}.linux.bin.tar.gz",)

    def build(self):
        super().build()
        package_dir = Path(self.package_folder)
        package_dir.mkdir(parents=True, exist_ok=True)
        self.run(f"cp -r {self.source_folder}/doxygen/* {package_dir}")
