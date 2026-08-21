"""The test `denver run examples/firmware-env` runs by default.

It asserts what each stage of the pipeline promised: the OS from the docker
stage, the interpreter and packages from the uv stage, the hand-installed
release from the first custom stage, the pinned tools from the conan stage,
and the exported variable from the second custom stage. A green run means the
whole environment really was built, not just configured.
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
    for tool in ("gcc", "make", "curl"):
        assert shutil.which(tool), f"{tool} is not on PATH"


def test_uv_stage_gave_us_python_3_12_and_pytest():
    assert sys.version_info[:2] == (3, 12)
    import pytest

    assert pytest.__version__ == "9.1.1"


def test_custom_stage_put_the_hand_installed_nvim_on_path():
    """The prebuilt release nvim/install.sh unpacked, reachable via nvim/activate.sh's PATH entry."""
    nvim = subprocess.run(["nvim", "--version"], capture_output=True, text=True, check=True)
    assert "NVIM v0.12.4" in nvim.stdout

    # not some nvim the host happens to have: the one under this env's own
    # state dir, where nvim/nvim.env pins it
    workdir = Path(os.environ["DENVER_ENV_WORKDIR"])
    assert shutil.which("nvim") == str(workdir / "nvim" / "0.12.4" / "bin" / "nvim")


def test_conan_stage_gave_us_the_pinned_tool_version():
    cmake = subprocess.run(["cmake", "--version"], capture_output=True, text=True, check=True)
    assert "3.31.9" in cmake.stdout


def test_custom_stage_exported_the_team_convention():
    assert os.environ["PYTEST_ADDOPTS"] == "-v -s"


def test_we_can_compile_and_run_the_hello_world_cmake_project():
    """gcc from the docker stage's apt list, cmake from the conan stage."""
    env_dir = Path(__file__).resolve().parent.parent
    build_dir = env_dir / "hello-world" / "build"
    subprocess.run(
        ["cmake", "-S", str(env_dir / "hello-world"), "-B", str(build_dir)],
        check=True,
    )
    subprocess.run(["cmake", "--build", str(build_dir)], check=True)

    hello_world = subprocess.run(
        [str(build_dir / "hello-world")], capture_output=True, text=True, check=True
    )
    assert hello_world.stdout == "Hello, world!\n"


def test_conan_cache_is_the_one_mounted_from_the_host():
    """Not ~/.conan2 inside the --rm container, which would be thrown away every run."""
    env_dir = Path(__file__).resolve().parent.parent
    assert os.environ["CONAN_HOME"] == str(env_dir / ".conan2")
    assert (env_dir / ".conan2").is_dir()
