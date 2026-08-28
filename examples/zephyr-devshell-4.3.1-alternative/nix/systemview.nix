{ lib
, stdenv
, fetchurl
, autoPatchelfHook
, jlink
, libX11
, libXrender
, libXrandr
, libXfixes
, libXcursor
, libSM
, libICE
, fontconfig
, freetype
}:

# SEGGER's SystemView tracing tool -- the nix equivalent of
# ../../zephyr-devshell/conan/recipes/systemview. Same shape as
# ./jlink.nix, including the license-accepting POST 'fetchurl' needs (see
# that file's own comment for why this isn't a vendored tarball); the
# difference is what buildInputs has to cover: SystemView, unlike J-Link,
# has exactly one binary, and it *is* one of the GUI ones -- the bundled
# Qt4 (libQtGui.so.4.8.7, shipped in the tarball itself) still needs the
# host's X11 and fontconfig, so those are listed here for
# autoPatchelfHook to actually resolve rather than skip.
#
# SystemView also talks to a probe through libjlinkarm.so -- SEGGER's own
# instructions are to copy that file in from a J-Link installation, because
# SystemView's own download does not include it (verified: it isn't in this
# tarball at all). It is dlopen()'d relative to SystemView's own directory,
# not searched for on LD_LIBRARY_PATH the way JLinkExe's own runtime
# dependencies are (verified with LD_DEBUG=libs: no attempt to load it
# appears there at all) -- so the only fix is the literal one SEGGER
# documents, copying the file in during installPhase below.
stdenv.mkDerivation rec {
  pname = "systemview";
  version = "3.62b";

  src =
    let
      versionNoDots = builtins.replaceStrings [ "." ] [ "" ] version;
    in
    fetchurl {
      # note the path: SEGGER serves this file from .../downloads/jlink/,
      # not .../downloads/systemview/ -- matches conandata.yml exactly.
      url = "https://www.segger.com/downloads/jlink/SystemView_Linux_V${versionNoDots}_x86_64.tgz";
      curlOptsList = [
        "-d"
        "accept_license_agreement=accepted&submit=Download+software"
        "-X"
        "POST"
      ];
      # verified against ../../zephyr-devshell/conan/recipes/systemview's
      # own conandata.yml, which records this same download's md5
      # (c4e790c320566ec0bac903edf714af2f).
      hash = "sha256-wCZBFr1WCYkhjj68ojW6H7u3rBpwcJuOddKBcaZni24=";
    };

  nativeBuildInputs = [ autoPatchelfHook ];
  buildInputs = [
    stdenv.cc.cc.lib
    libX11
    libXrender
    libXrandr
    libXfixes
    libXcursor
    libSM
    libICE
    fontconfig
    freetype
  ];

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    mkdir -p "$out/bin"
    # '.', not a 'SystemView_Linux_V*_x86_64/*' glob -- see jlink.nix's own
    # installPhase comment; same reason (nix's unpackPhase already cd'd
    # into the tarball's one top-level directory).
    cp -r . "$out/bin/"
    # See the module comment above: SEGGER's own fix for a SystemView
    # install missing this file.
    cp -d ${jlink}/bin/libjlinkarm.so* "$out/bin/"
    runHook postInstall
  '';

  meta = {
    description = "SEGGER SystemView tracing tool";
    homepage = "https://www.segger.com/downloads/systemview/";
    platforms = [ "x86_64-linux" ];
  };
}
