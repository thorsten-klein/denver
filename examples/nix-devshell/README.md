# examples/nix-devshell

**A nix flake's `devShell` as the environment — sourced, not wrapped.**

Two stages: a [`nix`](../../doc/providers/nix.md) stage that enters this
directory's `flake.nix`, and a [`custom`](../../doc/providers/custom.md)
stage that proves the devShell reached everything after it.

## What it does

`denver run examples/nix-devshell` runs `nix print-dev-env` for
`devShells.x86_64-linux.default`, sources the result into denver's own
environment, and drops you into a `bash` with the devShell's PATH and
variables applied:

```console
$ denver run examples/nix-devshell -- bash -c 'command -v hello'
check: the devShell was entered, the shellHook ran too, jq=/nix/store/…/bin/jq
/nix/store/…-hello-2.12.3/bin/hello
```

The `check` stage's line comes first because it is a *stage* — it already
ran inside the devShell the stage before it built. That is the whole point:
denver does not launch `nix develop --command <your command>`, so a stage
after the nix stage is not outside it.

The first run evaluates the flake (and builds or fetches whatever it needs,
which can take a while). Every run after that reuses the cached environment
until a git-tracked file of the flake changes — `--fast` skips nix entirely,
`--force` re-evaluates.

## Prerequisites

- **nix** on `PATH`. Nothing else: flakes do not have to be enabled in
  `nix.conf`, because the provider passes
  `--extra-experimental-features "nix-command flakes"` on every invocation.
- A nix new enough to resolve `#default` against `devShells.<system>` —
  that is why `shell: default` is spelled out in `denver.yml` rather than
  left to nix's own default-installable rules, which older releases answer
  as `devShell.<system>`.
- The first evaluation needs network access for the pinned `nixpkgs`
  (`flake.lock` next to `flake.nix` records the exact revision).

## Why it exists

**It is the "we already have a flake" case.** A team with a working
`devShell` does not want to re-describe its toolchain as denver stages — it
wants the flake to stay the source of truth and still get what denver adds:
one entry point, `--fast`, stage composition, hooks, and the same
`denver run <env> -- <command>` everywhere including CI.

**It shows what sourcing buys over wrapping.** A hand-written wrapper
(`nix develop --command ...`) ends the conversation: nothing after it can
contribute to the environment. Here the `check` stage — and a `uv`,
`conan` or `download` stage, if this env had one — runs *inside* the
devShell and can extend it further. `keep-path:` (on by default) is what
keeps an earlier stage's tools reachable behind nix's own.

**It is also a cache demo.** Run it twice with `-v` and the second run says
`reusing cached devshell environment …`; touch `flake.nix` and it evaluates
again. The key is a digest of the flake's *git-tracked* file content, the
installable and the architecture — so a new file has to be `git add`ed
before nix, or this cache, notices it at all.

## Things to try

```bash
denver run examples/nix-devshell --show-config      # what the stage resolved to
denver run examples/nix-devshell -v -- true         # watch the devshell/activate steps, then the cache reuse
denver run examples/nix-devshell --fast -- true     # cached environment, nix never invoked
denver run examples/nix-devshell --force -- true    # re-evaluate the flake
denver run examples/nix-devshell --dry-run -- true  # preview: the nix command, nothing evaluated
```

Then edit `flake.nix` — add a package to `packages`, change
`DENVER_NIX_EXAMPLE` — and watch the next run re-evaluate exactly once.

## What it is not

Not a Zephyr, Docker or Conan setup — see
[`firmware-env`](../firmware-env/) for several providers layered in one env,
and [`zephyr-devshell-4.3.1`](../zephyr-devshell-4.3.1/) for the full-size
case. The flake here is deliberately one `mkShell` with two packages: the
example is about the provider, not about writing nix.

Full key reference for the provider: [`doc/providers/nix.md`](../../doc/providers/nix.md).
