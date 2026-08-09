"""ARM toolchain 15.3: the prebuilt x86_64-hosted arm-none-eabi cross toolchain.

Same shape as the cmake recipe next door -- a pinned url in conandata.yml,
unpacked into the package as-is. Bumping the version is a one-line edit here
plus the matching conandata.yml entry.
"""

from conan import ConanFile
from conan.tools.files import get


class ConanRecipe(ConanFile):
    name = "arm-none-eabi"
    version = "15.3"
    description = "arm-none-eabi toolchain (x86_64 Linux hosted cross toolchain)"
    url = "https://gitlab.arm.com/tooling/gnu-toolchains-for-arm"
    license = "GPL-3.0-with-GCC-exception"
    settings = "os", "arch"

    def build(self):
        (source,) = self.conan_data["sources"].values()
        get(self, **source, destination=self.package_folder, strip_root=True)

    def package(self):
        pass  # build() already put the unpacked toolchain where it belongs
