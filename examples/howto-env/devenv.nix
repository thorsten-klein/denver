# Port of this folder's denver.yml to devenv (https://devenv.sh).
# Written to spec and NOT executed -- see ../PORTING-devenv.md.
#
# denver original: five stages --
#   docker-with-tools    Ubuntu 24.04 container, everything below runs inside
#   uv-packages          python 3.12.3 venv (conan, pytest)
#   nvim-by-hand         one prebuilt release, downloaded/checksummed by hand
#   tools-from-internet  cmake 3.31.9 + arm-none-eabi 15.3 via conan
#   best-practices       the team's PYTEST_ADDOPTS convention
#
# Four of the five port, and the fifth is the same structural mismatch every
# tool in this comparison hits.

{ pkgs, lib, config, ... }:

{
  # GAP (structural) -- no port of 'docker-with-tools'.
  #
  # devenv does have `devenv container build shell`, which packages this
  # environment as an OCI image. But that runs the opposite direction from
  # denver's docker stage: denver takes an image you name (ubuntu:24.04, with
  # its apt packages) and relocates every later stage INTO it, with
  # `--skip docker` running the identical stack on the host. devenv takes the
  # environment it resolved from nixpkgs and packages THAT as an image.
  #
  # So devenv answers "ship this env as a container"; it does not answer
  # "this project only builds on Ubuntu 24.04 with these apt packages". If
  # your constraint is a specific glibc or a distro package with no nixpkgs
  # equivalent, this is where the port stops.

  packages = [
    # 'tools-from-internet', first half.
    pkgs.cmake

    # 'nvim-by-hand', in full. denver's version of that stage is ~60 lines
    # across nvim/install.sh (download, checksum, atomic unpack, idempotence),
    # nvim/activate.sh (PATH) and nvim/nvim.env (the pins) -- hand-written on
    # purpose, so doc/how-to.md can show what that job costs immediately
    # before showing conan doing it properly. Here it is one line.
    pkgs.neovim

    # 'tools-from-internet', second half.
    # UNVERIFIED VERSION -- same caveat as ../raspberry-pico/devenv.nix:
    # exactly 15.3 depends on the pinned nixpkgs input.
    pkgs.gcc-arm-embedded
  ];

  # 'uv-packages'. denver pinned 3.12.3 because that is what ubuntu:24.04
  # ships and the venv had to match the container's interpreter -- inside a
  # container denver cannot install another one. devenv brings its own
  # interpreter, so the pin is a choice here rather than a constraint imposed
  # by the layer below.
  languages.python = {
    enable = true;
    version = "3.12.3";
    uv.enable = true;
    venv = {
      enable = true;
      requirements = ./requirements.txt;
    };
  };

  # 'best-practices' -- ports exactly, and more declaratively than the
  # original (denver had to source a script so the export would survive into
  # the final command; devenv just sets it).
  env.PYTEST_ADDOPTS = "-v -s";

  scripts.test.exec = "pytest";
}
