import io

from DenverConanFileTest import DenverConanFileTest


class TestPackageConan(DenverConanFileTest):
    test_type = "explicit"

    def build_requirements(self):
        self.tool_requires(self.tested_reference_str)

    def test(self):
        version_str = str(self.get_ref(self.tested_reference_str).version)
        output = io.StringIO()
        self.run("ccache --version", output)
        print(output.getvalue())
        assert version_str in output.getvalue()
