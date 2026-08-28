{ lib
, stdenv
, fetchurl
, autoPatchelfHook
}:

# SEGGER's J-Link software and documentation pack, packaged as a plain nix
# derivation -- the nix equivalent of ../../zephyr-devshell/conan/recipes/jlink,
# which this example's flake.nix installs into the devShell alongside
# zephyr-nix's sdk-0_17.
#
# Fetched, not vendored: SEGGER only serves the tarball behind a license
# click-through -- a POST with 'accept_license_agreement=accepted', reproduced
# below via 'curlOptsList' the same way that conan recipe's own conandata.yml
# does it with plain curl. (A first attempt at this file instead copied that
# recipe's own locally-downloaded copy of the tarball into this directory --
# reverted, because this repo's top-level .gitignore deliberately excludes
# '*.tgz'/'*.tar.gz'/'*.tar.xz'/'*.zip' everywhere, and a fixed-output fetch
# with a known hash is the more correct fit anyway: no 60+ MB binary sitting
# in git history, and 'nix build' re-verifies the download against 'hash'
# below on every fetch instead of trusting whatever was checked in.)
stdenv.mkDerivation rec {
  pname = "jlink";
  version = "8.82";

  src =
    let
      versionNoDots = builtins.replaceStrings [ "." ] [ "" ] version;
    in
    fetchurl {
      url = "https://www.segger.com/downloads/jlink/JLink_Linux_V${versionNoDots}_x86_64.tgz";
      curlOptsList = [
        "-d"
        "accept_license_agreement=accepted&submit=Download+software"
        "-X"
        "POST"
      ];
      # verified against ../../zephyr-devshell/conan/recipes/jlink's own
      # conandata.yml, which records this same download's md5
      # (1691b1c79764bf1caade424cc39c2e0c) -- re-derive this the same way
      # (or just run the build once and take nix's "got: ..." hash) if this
      # version pin ever moves.
      hash = "sha256-BojgccHxhhraFNAf7d/rMB2s0h85hFTKu8Fzwyws2K0=";
    };

  nativeBuildInputs = [ autoPatchelfHook ];
  buildInputs = [ stdenv.cc.cc.lib ];

  # JLinkExe, JLinkGDBServerCLExe and libjlinkarm.so itself only NEED libc
  # and libdl (verified with 'readelf -d'); the GUI tools in the same pack
  # (JFlash, JMem, JScope, ...) need a bundled Qt4 plus the host's X11 --
  # not worth wiring up for a devShell that only ever runs the CLI tools, so
  # autoPatchelf leaves those few binaries un-patched instead of failing the
  # whole derivation over them.
  autoPatchelfIgnoreMissingDeps = true;

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    mkdir -p "$out/bin"
    # Same directory as the conan recipe's own 'bin' output, and for the
    # same reason: JLinkExe and friends dlopen() libjlinkarm.so, and look up
    # Firmwares/ and ETC/, relative to their own location, not via RPATH --
    # so the whole extracted tree has to land in $out/bin together, not
    # split into separate bin/lib/share outputs. '.', not a
    # 'JLink_Linux_V*_x86_64/*' glob: the tarball has exactly one top-level
    # directory, so nix's unpackPhase already cd'd into it (sourceRoot).
    cp -r . "$out/bin/"
    runHook postInstall
  '';

  meta = {
    description = "SEGGER J-Link software and documentation pack";
    homepage = "https://www.segger.com/downloads/jlink/";
    platforms = [ "x86_64-linux" ];
  };
}
