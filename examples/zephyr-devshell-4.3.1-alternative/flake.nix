{
  description = ''
    Zephyr RTOS 4.3.1 devshell -- the same target as ../zephyr-devshell-4.3.1,
    built with nix + direnv instead of denver's docker/conan/uv stages. See
    README.md for what this does and does not prove.
  '';

  inputs = {
    # An explicit unstable revision, not a numbered release and not a bare
    # branch name: zephyr-nix's own flake.nix tracks unstable too, and
    # 'zephyr-nix.inputs.nixpkgs.follows' below means our choice is what it
    # actually builds pythonEnv against. A numbered release lags Zephyr's
    # own scripts/requirements.txt pins enough to trip its version-constraint
    # check on nearly every package (verified: nixos-24.11 fails 5 of them,
    # nixos-25.11 still fails 2). Plain 'nixos-unstable' does better -- only
    # 'ruff' fails, whose exact '==0.14.2' pin has no realistic chance of
    # matching any nixpkgs snapshot by luck -- but as of 2026-05-09 it also
    # makes zephyr-nix's own use of the now-deprecated 'stdenv.isLinux'
    # print an evaluation warning on every fresh eval, which is upstream
    # code this flake cannot patch. This exact revision (2026-04-14) is the
    # narrow window that has both: new enough that only 'ruff' fails its
    # constraint, old enough to predate that deprecation entirely. Moving
    # this pin forward past 2026-05-09 brings that warning back; there is no
    # revision where neither warning fires.
    nixpkgs.url = "github:NixOS/nixpkgs/02d1c9ad58d56732a5ae2412981aca62ac4777fa";

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
    { self, nixpkgs, zephyr-src, zephyr-nix, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        zephyr = zephyr-nix.packages.${system};

        # A copy of zephyr-src whose scripts/requirements.txt gains one
        # extra '-r' line, pointing at nix/module-requirements.txt --
        # everything else is a symlink back to the original, so this costs
        # one small file, not a copy of the whole multi-hundred-MB checkout.
        #
        # This -- not a separate 'pip install --target' pass after 'west
        # update' -- is how the module-declared pip deps
        # (mcuboot/nanopb/...) get in: feeding them into the *same*
        # 'loadRequirementsTxt' call zephyr-nix's own python.nix makes for
        # the base requirements means they go through one resolve together,
        # through the exact same 'zephyrPackageOverrides' fixups (imgtool
        # among them -- nixpkgs only has it as 'mcuboot-imgtool', a rename
        # python.nix already patches around) and the same
        # 'validateVersionConstraints' check -- so a real conflict between
        # the base and a module's requirements surfaces as a build-time
        # warning against the *combined* set, not silently, the way it
        # would with two disconnected resolves. See
        # nix/module-requirements.txt for what has to be kept in sync and
        # why this is a checked-in lockfile rather than something
        # discovered live: the modules that declare pip requirements are
        # only known *after* 'west update' clones them, and evaluating this
        # flake happens before that.
        zephyrSrcWithModuleReqs = pkgs.runCommand "zephyr-src-with-module-reqs" { } ''
          mkdir -p "$out/scripts"
          for f in ${zephyr-src}/*; do
            [ "$(basename "$f")" = scripts ] || ln -s "$f" "$out/$(basename "$f")"
          done
          for f in ${zephyr-src}/scripts/*; do
            [ "$(basename "$f")" = requirements.txt ] || ln -s "$f" "$out/scripts/$(basename "$f")"
          done
          cp ${zephyr-src}/scripts/requirements.txt "$out/scripts/requirements.txt"
          chmod u+w "$out/scripts/requirements.txt"
          # A same-directory relative '-r', like the lines already in this
          # file (requirements-base.txt, ...) -- not the absolute nix store
          # path '${./nix/module-requirements.txt}' would interpolate to
          # directly: pyproject-nix's requirements.txt parser concatenates a
          # '-r' target onto its own directory unconditionally, so an
          # absolute target ends up doubled (scripts/nix/store/... instead
          # of /nix/store/...) and fails to open. Symlinking it in here
          # sidesteps that rather than working around it.
          ln -s ${./nix/module-requirements.txt} "$out/scripts/module-requirements.txt"
          echo "-r module-requirements.txt" >> "$out/scripts/requirements.txt"
        '';

        # zephyr-nix's own composition root ('lib.mkZephyr'), called again
        # with that patched source -- reuses every fixup its python.nix
        # applies rather than reimplementing them. 'zephyr' above (the
        # flake's own 'packages' output) is still what sdk-0_17/hosttools-nix
        # come from: those never read scripts/requirements.txt, so there is
        # nothing to gain from rebuilding them against the patched source too.
        zephyrForPython = zephyr-nix.lib.mkZephyr { inherit pkgs; zephyr-src = zephyrSrcWithModuleReqs; };

        # '.override' reaches python.nix's own 'extraPackages' argument --
        # adding 'pip' next to the 'west' it already adds, needed only for
        # denver-parity debugging (a plain 'pip install' inside this
        # devShell, mirroring `west packages pip --install`'s own
        # 'sys.executable -m pip' invocation) since every actual dependency
        # this env needs is in the combined resolve above already.
        pythonEnv = zephyrForPython.pythonEnv.override (old: {
          extraPackages = ps: (old.extraPackages or (_: [ ])) ps ++ [ ps.pip ];
        });

        # 'sdk-0_17', not the plain 'sdk' (zephyr-nix's latest, currently a
        # 1.x SDK): zephyr-rtos/SDK_VERSION at the v4.3.1 tag pins 0.17.4 --
        # the same version ../zephyr-devshell-4.3.1/conan/catalog.yml pins --
        # and CMake's find_package() rejects a 1.x SDK as incompatible with
        # the '0.16'-minimum version FindHostTools.cmake requests. arm-zephyr-eabi
        # covers every Cortex-A/M/R board -- both the docker example's
        # nrf52840dk and frdm_rw612 (Cortex-M33) build with it. Add more
        # targets here for other architectures (riscv64-zephyr-elf, xtensa-*, ...).
        zephyrSdk = zephyr.sdk-0_17.override { targets = [ "arm-zephyr-eabi" ]; };

        # nix/jlink.nix, nix/systemview.nix -- not flake inputs, because
        # neither is a nix package anywhere: no public nix packaging of
        # either SEGGER tool exists, so both are hand-written, fetching them
        # the same license-accepting way their conan recipes
        # (../zephyr-devshell/conan/recipes/jlink, .../systemview) do. See
        # those two files.
        jlink = pkgs.callPackage ./nix/jlink.nix { };
        # 'inherit jlink': callPackage auto-fills arguments from pkgs' own
        # attributes by name, which 'jlink' is not -- it is the local
        # binding just above, and systemview.nix needs it (see its own
        # comment on why).
        systemview = pkgs.callPackage ./nix/systemview.nix { inherit jlink; };
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
            pythonEnv
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
            pkgs.clang_21
            jlink
            systemview
            # plain nixpkgs packages -- unlike jlink/systemview, both of
            # these are ordinary public nix packages, nothing hand-written
            # needed. Versions differ from ../zephyr-devshell's pins
            # (doxygen/1.15.0, protoc/33.2): nixpkgs-unstable's own current
            # versions instead, same as every other plain nixpkgs package here.
            pkgs.doxygen
            pkgs.protobuf
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

            # JLinkExe et al only NEED libc/libdl (nix/jlink.nix), but they
            # dlopen() libudev.so at runtime to enumerate USB devices --
            # not a link-time dependency autoPatchelfHook could have wired
            # in, so it has to be reachable via the loader's search path
            # instead. Without this: "Failed to load libudev.so". SystemView
            # dlopen()s libjlinkarm.so the same way, from the *separate*
            # jlink derivation's own $out/bin, to talk to a probe -- without
            # this: "Could not open J-Link shared library".
            #
            # The rest (freetype, X11, fontconfig) is nix/systemview.nix's
            # own buildInputs, in principle already RPATH'd into
            # libQtGui.so.4.8.7 by autoPatchelfHook (verified with 'nix log':
            # it does find and add them). In practice the standard fixup
            # phase's own '--shrink-rpath' step, which runs on this prebuilt,
            # stripped, closed-source Qt4 build, drops freetype's entry
            # again regardless -- verified with 'ldd': "libfreetype.so.6 =>
            # not found" despite that log. Rather than fight that heuristic,
            # this puts the same libraries on LD_LIBRARY_PATH too, which
            # 'patchelf --shrink-rpath' has no say over.
            export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath [
              pkgs.udev
              pkgs.freetype
              pkgs.libX11
              pkgs.libXrender
              pkgs.libXrandr
              pkgs.libXfixes
              pkgs.libXcursor
              pkgs.libSM
              pkgs.libICE
              pkgs.fontconfig
            ]}:${jlink}/bin''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

            # A visible reminder of which shell this is -- devshell.sh's own
            # interactive fallback additionally has to reapply this after
            # ~/.bashrc runs (see its --rcfile), since bashrc commonly
            # overwrites PS1 outright; direnv (.envrc) does not re-source
            # bashrc, so this line alone covers that path.
            export PS1="(zephyr-devshell-4.3.1-nix) ''${PS1-}"
          '';
        };
      });
}
