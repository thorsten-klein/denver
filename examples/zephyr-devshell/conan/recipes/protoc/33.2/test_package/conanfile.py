import io

from DenverConanFileTest import DenverConanFileTest


class TestPackageConan(DenverConanFileTest):
    test_type = "explicit"

    def build_requirements(self):
        self.tool_requires(self.tested_reference_str)

    def test(self):
        version_str = str(self.get_ref(self.tested_reference_str).version)
        cmd = "protoc --version"

        output = io.StringIO()
        self.run(cmd, output)
        out = output.getvalue()
        if out.rstrip() != f"libprotoc {version_str}":
            print(f"CMD: {cmd}")
            print(out)
            print(f"Expected: libprotoc {version_str}")
            raise ValueError(f"Error: different version than '{version_str}'")
