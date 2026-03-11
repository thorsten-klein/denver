from pathlib import Path

from conan.tools.files import chdir
from DenverConanFile import DenverConanFile


class ConanRecipe(DenverConanFile):
    name = "zephyr-sdk"
    version = "0.17.4"
    description = "Zephyr SDK: host toolchain plus per-arch cross toolchains"
    url = "https://github.com/zephyrproject-rtos/sdk-ng/releases"
    license = "Apache 2.0"
    settings = "os", "arch"
    no_copy_source = True
    exports_sources = (
        f"zephyr-sdk-{version}_linux-x86_64_minimal.tar.xz",
        "toolchain_linux-x86_64_arm-zephyr-eabi.tar.xz",
        "toolchain_linux-x86_64_x86_64-zephyr-elf.tar.xz",
        "toolchain_linux-x86_64_riscv64-zephyr-elf.tar.xz",
    )

    def source(self):
        pass  # prebuilt archives: nothing to fetch beyond what export_sources() already staged

    def _sdk_dir(self):
        return Path(self.package_folder) / f"zephyr-sdk-{self.version}"

    def build(self):
        sdk_dir = self._sdk_dir()
        Path(self.package_folder).mkdir(parents=True, exist_ok=True)

        with chdir(self, self.package_folder):
            self.run(f"tar -xf {self.source_folder}/{self.exports_sources[0]}")

        # cmake package install writes an absolute path derived from $HOME
        # into zephyr_sdk_export.cmake; point HOME at a scratch dir first so
        # that path is never the invoking user's real home, then patch the
        # '~' cmake left behind back into a real $ENV{HOME} lookup
        self.run(f"HOME={sdk_dir}/cmake.tmp {sdk_dir}/setup.sh -c")
        self.run(f"sed -i 's@~@$ENV{{HOME}}@g' {sdk_dir}/cmake/zephyr_sdk_export.cmake")

        self.run(f"{sdk_dir}/setup.sh -h")  # host toolchain
        self._mark_install_location(sdk_dir)

        with chdir(self, sdk_dir):
            for archive in self.exports_sources[1:]:
                self.run(f"tar -xf {self.source_folder}/{archive}")

    def package(self):
        pass  # build() already populated package_folder directly

    def package_info(self):
        sdk_dir = self._sdk_dir()
        self.buildenv_info.define("ZEPHYR_SDK_INSTALL_DIR", str(sdk_dir))
        self._relocate_if_moved(sdk_dir)

    # -- relocation: the SDK's own scripts embed an absolute install path --

    def _location_marker(self, sdk_dir):
        return sdk_dir / "relocation"

    def _mark_install_location(self, sdk_dir):
        self._location_marker(sdk_dir).write_text(str(sdk_dir))

    def _relocate_if_moved(self, sdk_dir):
        marker = self._location_marker(sdk_dir)
        if marker.read_text() == str(sdk_dir):
            return  # still at the location it was installed at
        self.output.info(f"zephyr-sdk moved since install, re-running host toolchain setup at {sdk_dir}")
        self.run(f"{sdk_dir}/setup.sh -h")
        self._mark_install_location(sdk_dir)
