from pathlib import Path

from DenverConanFile import DenverConanFile


class ConanRecipe(DenverConanFile):
    name = "fish"
    version = "4.8.1"
    description = "Official prebuilt fish-shell release"
    url = "https://github.com/fish-shell/fish-shell/releases"
    license = "GPL-v2"
    settings = "arch"
    exports_sources = (f"fish-{version}-linux-x86_64.tar.xz",)

    def build(self):
        super().build()
        bin_dir = Path(self.package_folder) / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        self.run(f"cp {self.source_folder}/fish {bin_dir}")
