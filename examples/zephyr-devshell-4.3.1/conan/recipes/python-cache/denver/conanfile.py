from DenverConanFile import DenverConanFile


class ConanRecipe(DenverConanFile):
    name = "python-cache"
    version = "denver"
    license = ""
    url = ""
    description = ""
    settings = (
        "os",
        "arch",
    )
    exports_sources = (
        'download_wheels.sh',
        'requirements.txt',
        'requirements.final.txt',
        'requirements-from-git.txt',
        'overrides.txt',
    )

    def export_sources(self):
        return

    def source(self):
        return

    def build(self):
        self.run(
            f"./download_wheels.sh {self.package_folder} -r requirements.txt -r requirements.final.txt -r requirements-from-git.txt --overrides overrides.txt"
        )

    def package(self):
        return

    def package_info(self):
        self.buildenv_info.append("PIP_FIND_LINKS", self.package_folder, separator=' ')
        self.buildenv_info.append("UV_FIND_LINKS", self.package_folder, separator=',')
