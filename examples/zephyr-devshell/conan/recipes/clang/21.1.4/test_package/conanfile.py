import io

from conan.tools.cmake import CMake
from DenverConanFileTest import DenverConanFileTest


class TestPackageConan(DenverConanFileTest):
    test_type = "explicit"

    def build_requirements(self):
        self.tool_requires(self.tested_reference_str)

    def build(self):
        cmake = CMake(self)
        cmake.configure()
        cmake.build()

    def test(self):
        clang_version_str = str(self.get_ref(self.tested_reference_str).version)
        clang_version_str_full = f"clang version {clang_version_str}"

        print(f"Checking for clang version '{clang_version_str}'")

        for exe in ["clang", "clang++"]:
            cmd = f"{exe} --version"

            output = io.StringIO()
            self.run(cmd, output)
            out = output.getvalue()
            if clang_version_str_full not in out:
                print(f"CMD: {cmd}")
                print(out)
                raise ValueError(f"Error: different version than '{clang_version_str}'")

            output = io.StringIO()
            cmd = f"{exe} -dumpversion"
            self.run(cmd, output)
            out = output.getvalue()
            if clang_version_str not in out:
                print(f"CMD: {cmd}")
                print(out)
                raise ValueError(f"Error: different version than '{clang_version_str}'")
