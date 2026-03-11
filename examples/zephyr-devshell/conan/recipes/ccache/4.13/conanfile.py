from DenverConanFile import DenverConanFile


class ConanRecipe(DenverConanFile):
    name = "ccache"
    version = "4.13"
    description = "Compiler cache that speeds up recompilation"
    url = "https://github.com/ccache/ccache"
    license = "Unknown"  # GPL-3.0 with sublicenses
    settings = "arch"
    no_copy_source = True
    exports_sources = (
        f"{name}-{version}.tar.gz",
        "0001-feat-Add-read-only-fallback-cache-directories.patch",
    )

    def generate(self):
        toolchain = self.get_generator()
        toolchain.variables["ENABLE_TESTING"] = False
        toolchain.generate()
