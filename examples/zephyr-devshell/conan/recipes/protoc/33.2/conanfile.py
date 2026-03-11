from pathlib import Path

from DenverConanFile import DenverConanFile


class ConanRecipe(DenverConanFile):
    name = "protoc"
    version = "33.2"
    description = "protobuf compiler"
    url = "https://github.com/protocolbuffers/protobuf"
    license = "BSD-3-Clause"
    settings = "arch"
    exports_sources = (f"protoc-{version}-linux-x86_64.zip",)

    def build(self):
        super().build()
        package_dir = Path(self.package_folder)
        package_dir.mkdir(parents=True, exist_ok=True)
        self.run(f"cp -r {self.source_folder}/* {package_dir}")
