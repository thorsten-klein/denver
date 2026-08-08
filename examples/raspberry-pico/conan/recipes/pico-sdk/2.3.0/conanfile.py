from pathlib import Path

from DenverConanFile import DenverConanFile


class ConanRecipe(DenverConanFile):
    name = "pico-sdk"
    version = "2.3.0"
    description = "The Raspberry Pi Pico SDK, plus the picotool built against it"
    url = "https://github.com/raspberrypi/pico-sdk"
    license = "BSD-3-Clause"
    settings = "os", "arch"
    no_copy_source = True
    # the SDK's release tarball ships its submodules as empty directories, so
    # cmake reports e.g. "TinyUSB submodule has not been initialized" and
    # silently drops USB/wireless/bluetooth/mbedtls support. There's no git
    # repo here to 'git submodule update --init' from, so each one is fetched
    # as its own source archive, pinned to the commit pico-sdk 2.3.0 itself
    # points at (github: /repos/raspberrypi/pico-sdk/contents/lib?ref=2.3.0),
    # and unpacked into lib/<name> by build() below.
    submodules = {
        "btstack": "075a0780f0fad7ff67d58ac19f46e8953656a752",
        "cyw43-driver": "055d64274b014dd7b1c2fc94d26e8a18face7124",
        "lwip": "77dcd25a72509eb83f72b033d219b1d40cd8eb95",
        "mbedtls": "0bebf8b8c7f07abe3571ded48a11aa907a1ffb20",
        "tinyusb": "86ad6e56c1700e85f1c5678607a762cfe3aa2f47",
    }
    exports_sources = (
        f"pico-sdk-{version}.tar.gz",
        f"picotool-{version}.tar.gz",
    ) + tuple(f"{name}-{sha}.tar.gz" for name, sha in submodules.items())
    # picotool ships in this package rather than one of its own: it's the
    # only consumer of the SDK here, and a separate recipe would have to
    # tool_require pico-sdk just to see PICO_SDK_PATH. So the cmake project
    # built below is picotool's, not the SDK's (the SDK's own CMakeLists is
    # meant to be included by a firmware project, never configured here).
    kind = "cmake"
    build_script_folder = f"picotool-{version}"
    # libusb-1.0 is picked up from the host if its headers are installed
    # ('libusb-1.0-0-dev' on debian/ubuntu); without them picotool still
    # builds, minus the commands that talk to a Pico over USB (load, save,
    # erase, verify, reboot) -- cmake logs which of the two it did.
    #
    # cmake comes from the host (apt) rather than from the cmake recipe: a
    # package being built only sees its *own* tool requires, so the env's
    # conan cmake doesn't reach this build, and tool_requiring it here would
    # stack this recipe on top of another one. Installing a *missing* one
    # needs tools.system.package_manager:mode=install -- the env's denver.yml
    # passes it to `conan install` (without it conan only checks, and says so).
    system_tools_requires = ("cmake",)

    def _sdk_source_dir(self):
        # two archives are unpacked side by side, so the base class leaves
        # each one's own top-level folder name alone (no rename to self.name)
        return Path(self.source_folder) / f"pico-sdk-{self.version}"

    def _sdk_dir(self):
        return Path(self.package_folder) / f"pico-sdk-{self.version}"

    def generate(self):
        toolchain = self.get_generator()
        # picotool refuses to configure without it. It points at the copy
        # build() stages in the package (not at self.source_folder's pristine
        # one): that's the tree with the submodules filled in, so picotool
        # picks up mbedtls and builds its signing/hashing support. The path
        # only has to exist by the time cmake configures, which is inside
        # build() -- i.e. after that copy.
        toolchain.variables["PICO_SDK_PATH"] = str(self._sdk_dir())
        toolchain.generate()

    def _install_submodules(self, sdk_dir):
        """Replace the release tarball's empty lib/<name> placeholders with the real trees."""
        for name, sha in self.submodules.items():
            target = sdk_dir / "lib" / name
            self.run(f"rm -rf {target}")
            self.run(f"cp -r {self.source_folder}/{name}-{sha} {target}")

    def build(self):
        sdk_dir = self._sdk_dir()
        sdk_dir.parent.mkdir(parents=True, exist_ok=True)
        self.run(f"cp -r {self._sdk_source_dir()} {sdk_dir}")
        self._install_submodules(sdk_dir)
        super().build()  # picotool -> <package>/bin/picotool

    def package_info(self):
        super().package_info()
        self.buildenv_info.define("PICO_SDK_PATH", str(self._sdk_dir()))
