# Port of this folder's denver.yml to devenv (https://devenv.sh).
# Written to spec and NOT executed -- see ../PORTING-devenv.md.
#
# denver original: five stages, inheriting ../zephyr-devshell via `import:`
# and restating only the 4.3.1-specific pins.

{ pkgs, lib, config, ... }:

{
  # denver's `import: [../zephyr-devshell]`, one for one. This is the only
  # faithful reproduction of denver's layering in the whole five-tool
  # comparison -- see the long note in ../zephyr-devshell/devenv.nix.
  imports = [ ../zephyr-devshell/devenv.nix ];

  # Version-specific bits only, exactly as the original intends. Everything
  # else comes from the imported base.
  languages.python.version = "3.12.3";

  env.ZEPHYR_VERSION = "4.3.1";

  packages = [ pkgs.uv ];

  tasks = {
    # Stages 4 and 5. devenv's task graph orders these correctly via
    # `after`, and `status` is a real equivalent of denver's `skip-if:` --
    # so this ports better than under any other tool here.
    "zephyr:west-init" = {
      exec = "west init -l .";
      status = "test -d .west";
    };

    "zephyr:west-update" = {
      exec = "west update";
      after = [ "zephyr:west-init" ];
    };

    "zephyr:west-blobs" = {
      exec = "west blobs fetch";
      after = [ "zephyr:west-update" ];
    };

    # GAP (hard) -- this is stage 5, and it is where devenv's declarative
    # model finally gives out. What gets installed here is whatever the
    # freshly-cloned Zephyr modules turn out to declare (`west packages
    # pip`), which is not knowable until `zephyr:west-update` above has run.
    # devenv can *order* it correctly, but it cannot declare it: this has to
    # be an imperative `uv pip install` into the venv that
    # languages.python.venv already built, reaching around devenv's own
    # dependency management rather than through it.
    #
    # denver has the same fundamental problem and solves it with the
    # `freeze-to:` / requirements.final.txt lockfile pattern -- run it once,
    # commit the resolved output, and a fresh clone gets the pins without
    # needing to run west first. Nothing in devenv corresponds to that.
    "zephyr:west-packages" = {
      exec = ''
        uv pip install $(west packages pip)
      '';
      after = [ "zephyr:west-update" ];
    };

    # GAP -- no equivalent of denver's `no-index: auto` offline install
    # against the conan-provided wheel cache (UV_FIND_LINKS), or of
    # `venv-patcher:` rewriting files inside the built venv.
  };
}
