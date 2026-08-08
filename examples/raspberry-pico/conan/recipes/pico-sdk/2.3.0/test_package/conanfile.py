import io

from DenverConanFileTest import DenverConanFileTest


class TestPackageConan(DenverConanFileTest):
    test_type = "explicit"

    def build_requirements(self):
        self.tool_requires(self.tested_reference_str)

    def test(self):
        version_str = str(self.get_ref(self.tested_reference_str).version)

        # the SDK itself: PICO_SDK_PATH points at this version's tree
        output = io.StringIO()
        self.run("echo $PICO_SDK_PATH", output)
        print(output.getvalue())
        assert output.getvalue().strip().endswith(f"pico-sdk-{version_str}")

        # ... and the picotool built against it, on PATH
        output = io.StringIO()
        self.run("picotool version", output)
        print(output.getvalue())
        assert version_str in output.getvalue()
