# Port of this folder's denver.yml to devenv (https://devenv.sh).
# Written to spec and NOT executed -- see ../PORTING-devenv.md.
#
# denver original: a single 'uv' stage -- a virtualenv with two pinned
# packages, nothing else.
#
# This one is a straight like-for-like replacement. devenv has a first-class
# Python venv with uv as the installer, which is precisely what denver's uv
# provider is.

{ pkgs, lib, config, ... }:

{
  languages.python = {
    enable = true;
    # denver's uv stage did not pin an interpreter for this env; devenv
    # resolves one from nixpkgs.
    version = "3.12";

    # denver's `uv: requirements: [requirements.txt]`, one for one --
    # including "create it if missing, reinstall when the file changes",
    # which devenv tracks itself.
    uv.enable = true;
    venv = {
      enable = true;
      requirements = ./requirements.txt;
    };
  };

  # denver's `command: bash` -- the shell to drop into when no command is
  # given. `devenv shell` uses your login shell; this is the explicit form.
  scripts.shell.exec = "bash";
}
