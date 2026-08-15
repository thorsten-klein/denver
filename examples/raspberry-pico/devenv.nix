# Port of this folder's denver.yml to devenv (https://devenv.sh).
# Written to spec and NOT executed -- see ../PORTING-devenv.md.
#
# denver original: 'uv' -> 'conan'. A cross-compilation toolchain, no Docker.

{ pkgs, lib, config, ... }:

{
  packages = [
    pkgs.cmake
    pkgs.ninja
    # nixpkgs packages the ARM bare-metal toolchain, so the half of this env
    # that costs denver a whole conan recipe costs one line here.
    #
    # UNVERIFIED VERSION -- this is whatever the pinned nixpkgs input in
    # devenv.yaml happens to carry, not necessarily 15.3. Getting exactly
    # 15.3 means pinning nixpkgs to a revision that built it, or writing an
    # overlay. denver's conan/recipes/arm-none-eabi/15.3/ pins the upstream
    # ARM tarball and its checksum directly, so it never depends on a third
    # party having packaged that release.
    pkgs.gcc-arm-embedded
  ];

  languages.python = {
    enable = true;
    version = "3.12";
    uv.enable = true;
    venv = {
      enable = true;
      requirements = ./requirements.txt;
    };
  };

  # denver kept conan's cache out of ~/.conan2 to avoid colliding with an
  # existing one.
  env.CONAN_HOME = "${config.devenv.root}/.devenv/conan2";

  tasks = {
    # GAP (hard) -- pico-sdk is not in nixpkgs, and denver's
    # conan/recipes/pico-sdk/2.3.0/ does not merely fetch it: it *builds
    # picotool from source* as part of the recipe. devenv's answer to a
    # package that does not exist yet is "write a Nix derivation", which is a
    # real answer but a much steeper one than writing a conan recipe -- and
    # it is the thing devenv's simplified-Nix pitch is meant to spare you.
    #
    # `status` is worth noticing: it is devenv's equivalent of denver's
    # `skip-if:`, so the "don't redo this if it's already there" half of a
    # denver stage does port.
    "pico:sdk" = {
      exec = ''
        echo "TODO: fetch pico-sdk 2.3.0 and build picotool -- not in nixpkgs"
      '';
      status = "test -d $DEVENV_STATE/pico-sdk";
    };

    "pico:conan-install" = {
      exec = "conan install conan/conanfile.py --build=missing";
      after = [ "pico:sdk" ];
    };
  };
}
