from pathlib import Path

from DenverConanFile import DenverConanFile


class ConanRecipe(DenverConanFile):
    name = "ninja"
    version = "1.13.2"
    description = "Official prebuilt ninja binary release"
    url = "https://github.com/ninja-build/ninja/releases"
    license = "Apache-2.0"
    settings = "arch"
    exports_sources = ("ninja-linux.zip",)

    def build(self):
        super().build()
        bin_dir = Path(self.package_folder) / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        self.run(f"cp -r {self.source_folder}/ninja {bin_dir}")
