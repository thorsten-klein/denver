"""What this environment wants on PATH, and in exactly which versions.

The '@denver/snapshot' half of each reference is the user/channel denver's
recipes-exporter stamps onto every recipe it exports (the conan provider's
'user:'/'channel:' keys, defaulting to denver/snapshot).
"""

from conan import ConanFile


class FirmwareEnv(ConanFile):
    name = "firmware-env"
    version = "1.0"

    def build_requirements(self):
        self.tool_requires("cmake/3.31.9@denver/snapshot")
