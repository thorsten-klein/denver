from DenverConanFile import DenverConanFile


class ConanRecipe(DenverConanFile):
    name = "west-blobs-cache"
    version = "denver"
    license = ""
    url = ""
    description = ""
    settings = (
        "os",
        "arch",
    )
    exports_sources = (
        'download.sh',
        'blobs.txt',
    )

    def export_sources(self):
        return

    def source(self):
        return

    def build(self):
        self.run(f"./download.sh blobs.txt {self.package_folder}")

    def package(self):
        return

    def package_info(self):
        self.buildenv_info.append("WEST_BLOBS_CACHE_DIRS", self.package_folder, separator=';')
