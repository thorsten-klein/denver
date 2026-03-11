import io

from DenverConanFileTest import DenverConanFileTest


class TestPackageConan(DenverConanFileTest):
    test_type = "explicit"

    def build_requirements(self):
        self.tool_requires(self.tested_reference_str)

    def test(self):
        output = io.StringIO()
        self.run("echo ${ZEPHYR_SDK_INSTALL_DIR}", output)
        out = output.getvalue().rstrip()
        print(out)
        assert out
