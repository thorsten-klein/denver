import io

from DenverConanFileTest import DenverConanFileTest


class TestPackageConan(DenverConanFileTest):
    test_type = "explicit"

    def build_requirements(self):
        self.tool_requires(self.tested_reference_str)

    def test(self):
        version_str = str(self.get_ref(self.tested_reference_str).version)
        version_str_full = f"SEGGER SystemView V{version_str[:2]}"

        output = io.StringIO()
        self.run("which SystemView", output)
        systemview = output.getvalue().rstrip()
        assert systemview

        cmd = f"strings {systemview}"
        output = io.StringIO()
        self.run(cmd, output)
        out = output.getvalue()
        if version_str_full not in out:
            print(f"CMD: {cmd}")
            print(out)
            print(f"Expected: {version_str_full}")
            raise ValueError(f"Error: different version than '{version_str}'")
