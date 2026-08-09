"""The test `denver examples/howto-env` runs by default.

It asserts what each stage of the pipeline promised: the OS from the docker
stage, the interpreter and packages from the pip stage, the pinned tools from
the conan stage, and the exported variable from the custom stage. A green run
means the whole environment really was built, not just configured.
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def test_docker_stage_gave_us_ubuntu_24_04():
    assert "Ubuntu 24.04" in platform.freedesktop_os_release()["PRETTY_NAME"]


def test_docker_stage_installed_the_apt_packages():
    for tool in ("jq", "netstat", "curl"):
        assert shutil.which(tool), f"{tool} is not on PATH"


def test_pip_stage_gave_us_python_3_12_and_pytest():
    assert sys.version_info[:2] == (3, 12)
    import pytest

    assert pytest.__version__ == "9.1.1"


def test_conan_stage_gave_us_the_pinned_tool_versions():
    cmake = subprocess.run(["cmake", "--version"], capture_output=True, text=True, check=True)
    assert "3.31.9" in cmake.stdout

    gcc = subprocess.run(["arm-none-eabi-gcc", "--version"], capture_output=True, text=True, check=True)
    assert "15.3" in gcc.stdout


def test_custom_stage_exported_the_team_convention():
    assert os.environ["PYTEST_ADDOPTS"] == "-v -s"


def test_conan_cache_is_the_one_mounted_from_the_host():
    """Not ~/.conan2 inside the --rm container, which would be thrown away every run."""
    env_dir = Path(__file__).resolve().parent.parent
    assert os.environ["CONAN_HOME"] == str(env_dir / ".conan2")
    assert (env_dir / ".conan2").is_dir()
