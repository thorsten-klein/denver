"""cmake 3.31.9: the official prebuilt Linux x86_64 release, repackaged.

Nothing is compiled here: conan's own `get()` downloads the archive pinned in
conandata.yml, verifies its md5 and unpacks it straight into the package.
conan then puts <package>/bin on PATH for a tool_requires by itself, which is
what makes `cmake` available to every later stage and the final command.
"""

from conan import ConanFile
from conan.tools.files import get


class ConanRecipe(ConanFile):
    name = "cmake"
    version = "3.31.9"
    description = "Official prebuilt cmake binary release"
    url = "https://github.com/Kitware/CMake"
    license = "BSD-3-Clause"
    settings = "os", "arch"

    def build(self):
        # the one archive pinned in conandata.yml, unpacked straight into the
        # package folder: there is nothing to build, and it saves copying a
        # few hundred MB out of a source folder afterwards
        (source,) = self.conan_data["sources"].values()
        get(self, **source, destination=self.package_folder, strip_root=True)

    def package(self):
        pass  # build() already put the unpacked release where it belongs
