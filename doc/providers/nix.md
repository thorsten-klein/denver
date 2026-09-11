# nix provider

A `nix` stage brings a [nix flake](https://nixos.wiki/wiki/Flakes)'s
`devShell` into the environment: the shell's PATH, compilers, SDKs and
variables become *this* process's environment, and every later stage — and
the final command — simply runs inside it.

Nothing is wrapped. denver does not launch `nix develop --command <your
command>`, and there is no second "post-nix" script: the environment is
fetched once with `nix print-dev-env`, cached, and sourced into `ctx.env`
like any other stage's contribution. One process throughout, so a `uv` stage
before it and a `conan` stage after it compose with it the ordinary way.

```yaml
stages:
  - devshell

devshell:
  provider: nix
  flake: nix-env          # the directory holding flake.nix
  root: .                 # what nix copies into the store (see 'root:')
  shell: default          # devShells.<system>.default
  expected-version: 2.35.2
```

(`provider:`/`description:`/`disabled:`/`scripts:`/`env:`/`env-prepend:`/`env-append:` are generic keys every
stage has — see "Generic stage keys" in [Configuration](../configuration/config-file.md). Everything below is
specific to `nix`.)

## Key reference

- **`flake`** — the flake to enter, as a **directory** (resolved like every
  other denver path: against the env dir, then imported base envs) or as a
  **flake reference** with a scheme (`github:owner/repo`, `git+https://…`,
  `path:/abs/dir`). Default: `"."`, the env dir itself.
- **`root`** — the directory nix copies into the store, when it has to be a
  *parent* of `flake:`. The reference becomes `path:<root>?dir=<rel>`.
  Needed whenever the flake declares a relative input pointing outside its
  own directory (a sibling flake stacked on top of it) — nix copies only the
  reference's own root, and an input below that root fails evaluation with
  *"access to absolute path … forbidden in pure evaluation mode"*. Unset:
  the flake's own directory is the root. Requires a nix new enough to accept
  `?dir=` on a `path:` URL — an older one (2.6, say) rejects it outright
  with *"path URL … has unsupported parameter 'dir'"*.
- **`shell`** — the devShell attribute to enter, appended as `#<shell>`
  (e.g. `default`, or a full `devShells.aarch64-linux.ci`). Unset: nix picks
  the default installable for this system itself — which older releases
  resolve as `devShell.<system>`, *not* `devShells.<system>.default`, so
  spell `shell: default` out when an env has to work across nix versions.
- **`exe`** — the nix executable (default: `nix`, found on PATH).
- **`args`** — extra arguments passed to `nix print-dev-env`, verbatim (e.g.
  `["--option", "sandbox", "false"]`). Default: none.
- **`impure`** — `true` adds `--impure`, letting the flake read the
  environment and absolute paths (default: `false`).
- **`experimental-features`** — passed as
  `--extra-experimental-features` (default: `"nix-command flakes"`, which is
  what `print-dev-env` and flake refs still need on a stock nix). An empty
  string drops the flag, for a nix.conf that already enables them.
- **`expected-version`** — the nix version this env was tested against. A
  different one only **warns** (with install instructions) — a missing nix
  is the fatal case. Unset: no version check at all.
- **`cache`** — `false` re-runs `nix print-dev-env` on every single denver
  run (default: `true`; see "The cache" below).
- **`keep-path`** — `true` (default) re-appends PATH entries the devShell's
  own PATH replaced, so a venv or toolchain an *earlier* stage put there is
  still reachable — behind the devShell's own tools, never in front of them.
  `false` leaves PATH exactly as the devShell exported it.
- **`shell-hook`** — `false` drops the devShell's own `shellHook` instead of
  running it (default: `true`; see "shellHook" below).

## What runs, and what is skipped

Per stage, in order:

1. **nix check** — `nix` missing from PATH is fatal, with the two install
   commands spelled out. With `expected-version:` set, a mismatch warns.
2. **devshell** — `nix print-dev-env <flake-ref>[#<shell>]`, its stdout
   written to this stage's cache file. **Skipped** whenever a cache file for
   exactly these inputs already exists (and `--force` wasn't given, and
   `cache:` isn't `false`). This is the slow step: a cold evaluation builds
   every input the devShell needs.
3. **activate** — the cached script is sourced into `ctx.env`, `shellHook`
   and all. Then `keep-path:` re-appends whatever PATH entries the devShell
   dropped.

Under `--fast` steps 1 and 2 are skipped entirely and the cached environment
is sourced verbatim — nix is never invoked, not even to check its version.
With no cache yet, `--fast` fails saying to run once without it.

Under `--dry-run` nothing is evaluated: the `nix print-dev-env` command is
printed, and a `[dry-run !]` line says the preview's remaining commands are
rendered *without* the devShell's PATH and variables — unless a cache file
is already there, which is sourced for real (that is how denver computes an
environment at all).

## The cache

`nix print-dev-env`'s output is cached under
`${DENVER_ENV_WORKDIR}/nix-devshell/<stage>-<digest>.sh`, where the digest
covers everything that decides what nix would print:

- the installable (flake ref + `shell:`), `args:`, `impure:` and
  `shell-hook:`,
- the machine's architecture — a cached `x86_64` environment is meaningless
  on `aarch64`, and the two legitimately share one checkout over a network
  mount,
- the **content** of every **git-tracked** file under the copy root
  (`root:`, else the flake's directory).

Git-tracked, because that is exactly the set a `path:` flake can see: nix
copies a working tree's tracked files into the store and ignores the rest,
so hashing more would invalidate the cache on a build artifact nix never
looked at — and hashing less would miss a change it does see. **A new file
has to be `git add`ed before nix (or this cache) notices it at all.** When
the copy root is not a git checkout, `flake.nix` and `flake.lock` are hashed
instead.

Because the key is a digest of the inputs and not a "latest" pointer,
switching a flake back and forth (a branch with a different `flake.lock`,
say) re-uses both cached environments instead of re-evaluating each time.

A **remote** reference has no local files to hash: a pinned one
(`github:owner/repo/<rev>`) never changes, and a moving one
(`github:owner/repo`) is refreshed with `--force` or `cache: false`.

## shellHook

A current `nix print-dev-env` ends its output with `eval "${shellHook:-}"` —
so simply sourcing it runs the devShell's `shellHook` exactly as `nix
develop` would, and denver does nothing extra to make that happen. That is
the default, and it is what a flake doing real work there (generating a
config, seeding a directory) needs.

`shell-hook: false` is for the other kind of hook — one written for an
interactive `nix develop`, which prints a banner or expects a tty. nix has
no flag to leave it out, so denver drops that trailing `eval` line from the
**cached script** when it is written. It cannot be undone afterwards: by the
time the environment is in `ctx.env` the hook has already run.

An older nix that does not emit the `eval` line at all never runs the hook
either way, and `shell-hook: false` then has nothing to strip.

## Design notes

- **Why not `nix develop --command`.** Wrapping puts a second shell between
  denver and the command, and nothing after it can contribute to the
  environment any more: a later `conan` or `custom` stage would run
  *outside* what it was supposed to extend. Sourcing `print-dev-env`'s
  output instead keeps denver's whole model — stages assembling one
  environment, in order — intact, and makes the devShell just another
  contributor to it.
- **Why cache at all.** Even a fully-cached devShell costs a nix evaluation
  (a second or two at best) on every single run, and denver is meant to be
  in the path of every build command. Keying on file *content* is what
  makes the skip safe: the cache is stale exactly when nix's own answer
  would have changed.
- **`keep-path:` is on by default.** The devShell exports an absolute PATH,
  which would otherwise discard what earlier stages put there. Appending the
  dropped entries (never prepending them) keeps `denver` composable without
  ever letting a host tool shadow one the devShell provides.
- **A version mismatch warns, it doesn't fail.** `expected-version:` records
  which nix an env was tested against. Refusing to run on a different one
  would make every env unusable the day a machine upgrades, for a
  difference that is usually invisible.
