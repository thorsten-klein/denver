{
  description = ''
    Zephyr RTOS 4.3.1 devshell -- the same target as ../zephyr-devshell-4.3.1,
    built with nix + direnv instead of denver's docker/conan/uv stages. See
    README.md for what this does and does not prove.
  '';

  inputs = {
    # unstable, not a numbered release: zephyr-nix's own flake.nix tracks
    # unstable too, and 'zephyr-nix.inputs.nixpkgs.follows' below means our
    # choice is what it actually builds pythonEnv against. A numbered
    # release lags Zephyr's own scripts/requirements.txt pins enough to
    # trip its version-constraint check on nearly every package (verified:
    # nixos-24.11 fails 5 of them, nixos-25.11 still fails 2) -- unstable
    # fails only one, 'ruff' (its exact '==0.14.2' pin has no realistic
    # chance of matching any nixpkgs snapshot by luck; see flake.lock for
    # what commit this actually resolved to, which is what makes this
    # reproducible despite tracking a rolling branch).
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    # Pinned to the exact tag ../zephyr-devshell-4.3.1/west.yml builds
    # against. zephyr-nix reads *this* checkout's scripts/requirements.txt to
    # build 'pythonEnv' below (west + Zephyr's own pinned Python deps) --
    # the nix equivalent of that example's 'uv' stage reading
    # conan/recipes/python-cache/denver/requirements*.txt.
    zephyr-src.url = "github:zephyrproject-rtos/zephyr/v4.3.1";
    zephyr-src.flake = false;

    # https://github.com/nix-community/zephyr-nix -- packages the Zephyr SDK
    # and a west-ready Python env as ordinary nix derivations. Stands in for
    # the 'conan' + 'uv' stages of the denver example.
    zephyr-nix.url = "github:nix-community/zephyr-nix";
    zephyr-nix.inputs.nixpkgs.follows = "nixpkgs";
    zephyr-nix.inputs.zephyr.follows = "zephyr-src";

    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    { self, nixpkgs, zephyr-nix, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        zephyr = zephyr-nix.packages.${system};

        # 'sdk-0_17', not the plain 'sdk' (zephyr-nix's latest, currently a
        # 1.x SDK): zephyr-rtos/SDK_VERSION at the v4.3.1 tag pins 0.17.4 --
        # the same version ../zephyr-devshell-4.3.1/conan/catalog.yml pins --
        # and CMake's find_package() rejects a 1.x SDK as incompatible with
        # the '0.16'-minimum version FindHostTools.cmake requests. arm-zephyr-eabi
        # covers every Cortex-A/M/R board -- both the docker example's
        # nrf52840dk and frdm_rw612 (Cortex-M33) build with it. Add more
        # targets here for other architectures (riscv64-zephyr-elf, xtensa-*, ...).
        zephyrSdk = zephyr.sdk-0_17.override { targets = [ "arm-zephyr-eabi" ]; };
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            # mkShell's own default 'bash' is a minimal build with
            # programmable completion compiled out (fine for build scripts,
            # not for the interactive shell devshell.sh drops into) --
            # bashInteractive on PATH ahead of it avoids
            # 'shopt: progcomp: invalid shell option name' /
            # 'complete: command not found' once ~/.bashrc tries to set it up.
            pkgs.bashInteractive

            # west + Zephyr's pinned Python requirements, built once and
            # cached in the nix store -- equivalent to the 'uv' stage.
            zephyr.pythonEnv
            # dtc, bossa, openocd, qemu, ... from nixpkgs rather than the
            # SDK's own prebuilt binaries -- equivalent to the non-toolchain
            # part of the 'conan' stage.
            zephyr.hosttools-nix
            # the ARM cross-compiler -- equivalent to the 'conan' stage's
            # zephyr-sdk pin.
            zephyrSdk

            pkgs.cmake
            pkgs.ninja
            pkgs.gperf
            pkgs.ccache
            pkgs.git
          ];

          # Telling Zephyr's build system where the SDK lives is *all* this
          # needs: 'west build' picks the right compiler per board on its
          # own from there, with no ZEPHYR_TOOLCHAIN_VARIANT to juggle --
          # native_sim/native_posix/unit_testing boards force themselves to
          # the host compiler regardless of this (see FindHostTools.cmake in
          # zephyr-rtos), every other board uses this SDK.
          ZEPHYR_SDK_INSTALL_DIR = "${zephyrSdk}";

          # A system-level west config layered under the per-workspace one --
          # same trick as the docker example's hooks/env.sh setting
          # WEST_CONFIG_SYSTEM, so a fresh workspace already has a default
          # board (native_sim) and doesn't need '-b ...' on every
          # 'west build' -- pass '-b ...' explicitly to target real hardware
          # instead, e.g. '-b frdm_rw612' or '-b nrf52840dk/nrf52840'.
          WEST_CONFIG_SYSTEM = "${self}/configs/west_base_config";

          shellHook = ''
            export CCACHE_BASEDIR="$PWD"
          '';
        };
      });
}
