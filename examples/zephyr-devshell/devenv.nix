# Port of this folder's denver.yml to devenv (https://devenv.sh).
# Written to spec and NOT executed -- see ../PORTING-devenv.md.
#
# denver original: the shared base that every zephyr-devshell-<version> env
# imports. Declared `runnable: false` -- starting it directly is an error; it
# exists only to be inherited.
#
# THIS IS THE ONE THING DEVENV DOES THAT NO OTHER TOOL IN THIS COMPARISON
# DOES. devenv.nix is a Nix module, and Nix modules compose by design:
# ../zephyr-devshell-4.3.1/devenv.nix does
#
#     imports = [ ../zephyr-devshell/devenv.nix ];
#
# which is a genuine like-for-like replacement for denver's
# `import: [../zephyr-devshell]` -- a NAMED sibling, not an implicit parent
# directory (mise), and not a flattening (devbox, flox, pixi). Module merge
# semantics even reproduce denver's rule that lists from every layer of the
# chain accumulate rather than replace, and lib.mkDefault / lib.mkForce give
# finer override control than denver has.
#
# GAP (minor) -- devenv has no `runnable: false`. Nothing stops someone
# running `devenv shell` in this folder; it just yields an incomplete
# environment rather than the clear error denver gives.

{ pkgs, lib, config, ... }:

{
  packages = [
    pkgs.cmake
    pkgs.ninja
    pkgs.ccache
    pkgs.git
  ];

  languages.python = {
    enable = true;
    version = lib.mkDefault "3.12.3";
    uv.enable = true;
    venv.enable = true;
  };

  env.CONAN_HOME = lib.mkDefault "${config.devenv.root}/.devenv/conan2";

  # denver's base contributed a hooks/env.sh, and every layer of the import
  # chain adds its own rather than replacing. enterShell concatenates across
  # imported modules the same way.
  enterShell = ''
    . ${./hooks/env.sh}
  '';

  # denver's base also declared a `scripts: setup:` for the JLink udev rule
  # -- host setup, run once via `denver <env> --run setup`, never on a normal
  # start. devenv scripts are the same shape.
  scripts.install-jlink-udev-rules.exec = ''
    sudo bash ${./setup/install_jlink_udev_rules.sh}
  '';
}
