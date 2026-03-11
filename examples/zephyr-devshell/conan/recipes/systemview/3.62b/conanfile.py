from pathlib import Path

from DenverConanFile import DenverConanFile


class ConanRecipe(DenverConanFile):
    name = "systemview"
    version = "3.62b"
    description = "SEGGER SystemView tracing tool"
    url = "https://www.segger.com/downloads/systemview/"
    license = "SEGGER's Friendly License"
    settings = "os", "arch"
    no_copy_source = True
    exports_sources = (f"SystemView_Linux_V{version.replace('.', '')}_x86_64.tgz",)

    def build(self):
        super().build()
        package_dir = Path(self.package_folder)
        package_dir.mkdir(parents=True, exist_ok=True)
        self.run(f"cp -r {self.source_folder}/systemview {package_dir}/bin")
