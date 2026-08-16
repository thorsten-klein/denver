# Port of this folder's denver.yml to devenv (https://devenv.sh), extended so
# that entering the environment BRINGS IT INTO EXISTENCE from a clean
# checkout -- the thing denver does and all five ported tools originally did
# not. Verified end to end; see ../PORTING-devenv.md, "The full adaptation".
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

  # west is a python package, so it belongs in the venv rather than in
  # `packages` -- Zephyr's own build scripts import from that same venv, and a
  # nixpkgs-level west would sit outside it.
  languages.python.venv.requirements = ''
    west
    conan
  '';

  env.ZEPHYR_VERSION = "4.3.1";
  # native_sim compiles for the host rather than with the Zephyr SDK.
  env.ZEPHYR_TOOLCHAIN_VARIANT = "host";

  packages = [
    pkgs.uv
    # dtc and gperf have to be declared explicitly, and finding that out is a
    # finding in itself: without them the build still worked, because it
    # silently picked up /usr/bin/dtc and /usr/bin/gperf from the host's apt
    # packages. A Nix-backed environment is not automatically hermetic -- a
    # devenv shell keeps the host PATH behind its own, so a missing package
    # degrades to "whatever the machine has" rather than to a clear error.
    # That is exactly the failure mode denver's "explicit over implicit"
    # principle is written against, appearing in the tool with the strongest
    # reproducibility story of the five.
    pkgs.dtc
    pkgs.gperf
  ];

  # The workspace west builds, and the tree inside it Zephyr is rooted at.
  # Both live under devenv's own state dir rather than in the checkout: the
  # manifest renames zephyr's path to `zephyr-rtos` (see west.yml), and
  # `west init -l <dir>` makes <dir>'s PARENT the workspace topdir -- so
  # running it against the checkout would clone ~40 repos into examples/.
  # denver has exactly the same problem and solves it the same way, with
  # DENVER_ENV_WORKDIR.
  env.WEST_TOPDIR = "${config.devenv.state}/ws";
  env.ZEPHYR_BASE = "${config.devenv.state}/ws/zephyr-rtos";

  # Absolute paths into the venv, because the tasks below cannot use `west`
  # or `uv` by name.
  #
  # This is a genuine chicken-and-egg in devenv's model and it cost a failed
  # run to find: `languages.python.venv` puts the venv on PATH as part of
  # *entering the shell*, but every task here runs `before` enterShell -- so
  # at task time the venv exists on disk and is not on PATH. The first
  # attempt failed with a bare `west: command not found`.
  #
  # denver has no equivalent problem: a stage's environment is built up
  # cumulatively and handed to the next stage, so stage 5 sees what stage 3
  # put on PATH. Here, work that must happen before the shell is ready cannot
  # use the shell the work is preparing.

  # --- Stages 4 and 5, wired to run ON ENTERING the environment -------------
  #
  # This is what makes this file "devenv-full" rather than the port it grew
  # out of. Every task below declares `before = [ "devenv:enterShell" ]`, so
  # `devenv shell` on a clean checkout *creates the workspace* rather than
  # assuming one, and `status` skips whatever is already done -- which is
  # denver's stage model, mechanism for mechanism:
  #
  #   denver stages:        ordered, run on entry, fingerprinted, --force
  #   devenv tasks:         after/before, devenv:enterShell, status, --refresh
  #
  # Verified: a first `devenv shell` clones and installs; a second one is a
  # no-op. See ../PORTING-devenv.md, "The full adaptation".
  tasks = {
    "zephyr:west-init" = {
      exec = ''
        mkdir -p "$WEST_TOPDIR/manifest"
        cp "$DEVENV_ROOT/west.yml" "$WEST_TOPDIR/manifest/west.yml"
        cd "$WEST_TOPDIR" && "${config.devenv.state}/venv/bin/west" init -l manifest
      '';
      status = ''test -d "$WEST_TOPDIR/.west"'';
      before = [ "devenv:enterShell" ];
      # Load-bearing, and it took two failed runs to find. The venv is built
      # by devenv's OWN task, `devenv:python:virtualenv`, which is a sibling
      # of this one under enterShell -- so without this line the two are
      # unordered and west-init runs first, against a venv that does not
      # exist yet ("No such file or directory").
      #
      # You have to know devenv's internal task name to express this
      # (`devenv tasks list` reveals it). denver has no equivalent hazard:
      # its stages are a declared, totally-ordered list, and a stage cannot
      # accidentally race the machinery that sets up the stage before it.
      after = [ "devenv:python:virtualenv" ];
    };

    "zephyr:west-update" = {
      # --narrow -o=--depth=1: this clones ~40 repositories, and nothing here
      # needs their history. denver's zephyr provider makes the same call
      # without these, so a faithful port would be considerably slower.
      exec = ''cd "$WEST_TOPDIR" && "${config.devenv.state}/venv/bin/west" update --narrow -o=--depth=1'';
      status = ''test -d "$ZEPHYR_BASE"'';
      after = [ "zephyr:west-init" ];
      before = [ "devenv:enterShell" ];
    };

    # Stage 5, and still the place devenv's declarative model gives out.
    # WHAT gets installed here is whatever the freshly-cloned modules turn out
    # to declare, so it is not knowable until west-update above has run.
    # devenv can *order* it -- which is the whole point of this branch -- but
    # it cannot *declare* it: nothing here lands in devenv.lock. denver has
    # the identical problem and answers it with `freeze-to:` /
    # requirements.final.txt, committing the resolved pins so a fresh clone
    # skips the discovery step entirely. devenv has no counterpart.
    #
    # Note this reads scripts/requirements.txt rather than calling
    # `west packages pip`: the latter needs west's extension commands
    # importable in the running west process, which is a second-order version
    # of the same bootstrap problem and not what this branch is testing.
    "zephyr:west-packages" = {
      # uv (unlike west) comes from `packages` above, so it IS on PATH at task
      # time -- nix-provided tools are available to tasks, venv-provided ones
      # are not. Using it with an explicit --python also avoids needing a pip
      # inside the venv, which uv does not seed by default.
      exec = ''
        uv pip install --python "${config.devenv.state}/venv/bin/python" \
          -r "$ZEPHYR_BASE/scripts/requirements.txt"
      '';
      status = ''"${config.devenv.state}/venv/bin/python" -c "import pykwalify, elftools"'';
      after = [ "zephyr:west-update" ];
      before = [ "devenv:enterShell" ];
    };

    # NOT bound to enterShell, deliberately. denver's stage 4 also runs
    # `west blobs fetch`, but the hal_nordic/hal_nxp blobs are large and
    # native_sim needs none of them, so fetching them on every entry would
    # make this environment slower than denver's for no gain here. Run it by
    # hand (`devenv tasks run zephyr:west-blobs`) for a real board.
    "zephyr:west-blobs" = {
      exec = ''cd "$WEST_TOPDIR" && "${config.devenv.state}/venv/bin/west" blobs fetch'';
      after = [ "zephyr:west-update" ];
    };

    # GAP -- no equivalent of denver's `no-index: auto` offline install
    # against the conan-provided wheel cache (UV_FIND_LINKS), or of
    # `venv-patcher:` rewriting files inside the built venv.
  };

  # The check CI runs: from a clean checkout, `devenv shell` alone must have
  # produced a workspace this builds against.
  scripts.build-hello-world.exec = ''
    west build -p always "$ZEPHYR_BASE/samples/hello_world" \
      -b native_sim/native/64 -d "$WEST_TOPDIR/build"
  '';
}
