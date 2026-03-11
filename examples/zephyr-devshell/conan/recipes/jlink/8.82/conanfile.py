from pathlib import Path

from DenverConanFile import DenverConanFile


class ConanRecipe(DenverConanFile):
    name = "jlink"
    version = "8.82"
    description = "SEGGER J-Link software and documentation pack"
    url = "https://www.segger.com/downloads/jlink/"
    license = "SEGGER's Friendly License"
    settings = "os", "arch"
    no_copy_source = True
    exports_sources = (f"JLink_Linux_V{version.replace('.', '')}_x86_64.tgz",)

    def build(self):
        super().build()
        package_dir = Path(self.package_folder)
        package_dir.mkdir(parents=True, exist_ok=True)
        self.run(f"cp -r {self.source_folder}/jlink {package_dir}/bin")

    def package_info(self):
        super().package_info()
        # libjlinkarm.so lives next to JLinkExe in the same dir; register it
        # as a libdir so VirtualBuildEnv puts it on LD_LIBRARY_PATH -- needed
        # by pyOCD/pylink, which dlopen() it at runtime.
        self.cpp_info.libdirs = ["bin"]
