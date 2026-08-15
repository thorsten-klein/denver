# Port of this folder's denver.yml to devenv (https://devenv.sh).
# Written to spec and NOT executed -- devenv and nix are not installed in the
# repo that produced this branch. See ../PORTING-devenv.md for the fidelity
# report.
#
# denver original: three 'custom' stages demonstrating stage-to-stage env
# propagation, plus one env-specific CLI flag (--greeting).
#
# This is the closest any of the five ports gets to the original, because
# devenv's `tasks` are an ordered dependency graph rather than a single hook:
# the before/after ordering the original exists to demonstrate survives.

{ pkgs, lib, config, ... }:

{
  # GAP -- denver's 'args:' declared a real argparse flag (--greeting, default
  # 'hello'), listed by `denver simple-env --help` and delivered into the env
  # as DENVER_ARG_GREETING. devenv has no per-project CLI argument
  # declaration. Nearest equivalent: a default env var, overridable before
  # entering.
  env.GREETING = lib.mkDefault "hello";

  tasks = {
    # Stage 1. `before = [ "simple-env:set-vars" ]` reproduces denver's stage
    # ordering exactly, which means this genuinely runs while the variables
    # are still unset -- the thing the original is demonstrating, and the
    # thing every other tool in this comparison lost.
    "simple-env:print-vars-before" = {
      exec = ''echo "[print-vars-before] MYVAR=$MYVAR FOO=$FOO BAR=$BAR"'';
      before = [ "simple-env:set-vars" ];
    };

    # Stage 2.
    #
    # GAP (subtle but real) -- denver's `source:` sources custom.sh into its
    # own process and folds the resulting exports into ctx.env, so they reach
    # every later stage AND the final command. A devenv task runs as a
    # subprocess: `. ./custom.sh` here would export into that subprocess and
    # die with it, exactly like denver's `cmd:` does. devenv's way to publish
    # variables is `env.*` (below), which is declarative and static -- so a
    # script that *computes* what to export at runtime has no clean
    # equivalent. For this dummy example the values are constants, so
    # restating them is faithful; for a real script that derives them, it
    # would not be.
    "simple-env:set-vars" = {
      exec = ''echo "[set-vars] sourcing custom.sh..."'';
      before = [ "simple-env:print-vars-after" ];
    };

    # Stage 3.
    "simple-env:print-vars-after" = {
      exec = ''
        echo "[print-vars-after] MYVAR=$MYVAR FOO=$FOO BAR=$BAR greeting=$GREETING"
      '';
    };
  };

  # The static half of custom.sh, restated declaratively -- see the GAP note
  # on 'set-vars' above for why this cannot just be `source ./custom.sh`.
  env.MYVAR = "1";
  env.FOO = "2";
  env.BAR = "3";

  enterShell = ''
    echo "simple-env ready"
  '';
}
