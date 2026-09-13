{
  description = "The devShell denver's nix provider enters in this example.";

  # Pinned by rev, not by branch: flake.lock next to this file records exactly
  # this revision, so every machine evaluates the same nixpkgs -- and the nix
  # provider's cache key (see doc/providers/nix.md) only changes when this
  # file or the lock does.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/02d1c9ad58d56732a5ae2412981aca62ac4777fa";

  outputs =
    { self, nixpkgs }:
    let
      # One system, spelled out. A real flake would use flake-utils (or
      # nixpkgs.lib.genAttrs) to cover aarch64-linux/x86_64-darwin too; this
      # example keeps the flake itself as small as the point it is making.
      pkgs = nixpkgs.legacyPackages.x86_64-linux;
    in
    {
      devShells.x86_64-linux.default = pkgs.mkShell {
        # what the devShell puts on PATH -- 'hello' stands in for the real
        # toolchain (a compiler, cmake, an SDK) a project would list here.
        packages = [ pkgs.hello pkgs.jq ];

        # a plain environment variable: exported by 'nix print-dev-env', so
        # denver sources it into the environment like any other stage's.
        DENVER_NIX_EXAMPLE = "the devShell was entered";

        # shellHook runs when the environment is sourced, exactly as it would
        # under 'nix develop' -- denver does nothing extra to make that happen
        # (see "shellHook" in doc/providers/nix.md). 'shell-hook: false' in
        # denver.yml is what opts out of it.
        shellHook = ''
          export DENVER_NIX_EXAMPLE_HOOK="the shellHook ran too"
        '';
      };
    };
}
