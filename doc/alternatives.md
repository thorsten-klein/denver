# Alternatives

denver is not the only way to declare a development environment, and for a
lot of projects it is not the best one. This page says which tool to use
instead, and when.

It exists because the question "why not just use devbox?" has an honest
answer that is sometimes "you should." A reader who has to work that out for
themselves will assume it was never considered.

## Use something else if…

| If your project… | …use | rather than denver, because |
|---|---|---|
| is Python-only | [`uv`](https://docs.astral.sh/uv/) on its own | `uv sync` + `uv run` is the whole of denver's `uv` stage. A one-stage denver env is strictly more machinery |
| needs native deps that conda-forge already has | [pixi](https://pixi.sh) | one resolver for Python *and* native packages, with a real lockfile and macOS/Windows support |
| needs pinned CLI tools, no containers | [mise](https://mise.jdx.dev) | installs the toolchain denver requires you to already have, and its `ubi:` backend replaces hand-rolled download/checksum scripts |
| wants declarative environments without learning Nix | [devbox](https://www.jetify.com/devbox) | the widest package availability of anything here, via nixpkgs, with a binary cache |
| wants layered, composable environments and will accept some Nix | [devenv](https://devenv.sh) | reproduces denver's layering, ordered stages *and* skip checks — see below |
| wants Nix-backed envs that feel like a virtualenv | [flox](https://flox.dev) | `[include] environments` composition, and the best runtime env propagation of the group |
| needs an IDE-integrated container | [devcontainers](https://containers.dev) / [DevPod](https://devpod.sh) | ecosystem and editor integration denver will never match |

**Where denver still fits:** you already have Conan recipes and an Artifactory
remote; you need the same stack to run *inside a container or natively on the
host* from one declaration; and you layer that stack across several
repositories. That is a narrow niche. It is also, as far as this comparison
found, an unoccupied one.

## How this was checked

Every one of denver's seven bundled examples was ported to all five
declarative tools below, and the fidelity gaps recorded. The ports live on
five branches — `port/pixi`, `port/mise`, `port/devbox`, `port/devenv`,
`port/flox` — each adding the tool's config next to the `denver.yml` it came
from, plus an `examples/PORTING-<tool>.md` write-up.

**Those ports are written to spec and unverified.** None of the five tools was
installed in the environment that produced them, so nothing was solved, locked
or run; assumed package names and versions are marked `UNVERIFIED` inline.
They are a design comparison, not working configuration. The conclusions below
are about what each tool's model can *express*, which is what a port makes
visible regardless.

## What each tool covers

### pixi

Wins outright on `zephyr-uv`, and would win for most ordinary projects.
Lockfiles, cross-platform solving, and Python plus native dependencies in one
resolver — denver's `uv` → `conan` stage ordering exists only to solve a
bootstrap problem pixi does not have. `zephyr-devshell-4.3.1`'s hand-rolled
`freeze-to:` / `requirements.final.txt` pattern is a worse version of what
`pixi.lock` gives free.

Stops at: no container wrapper, composition only via `[feature]` within one
manifest (no cross-folder inheritance), and anything not in conda-forge —
`pico-sdk` has no package, and denver's recipe also builds `picotool` from
source.

### mise

The best fit for provisioning *tools*. It installs `uv`, `conan` and `west` —
all of which denver's pre-conditions table requires you to already have. Its
`ubi:` backend collapses `howto-env`'s deliberately hand-written
`nvim-by-hand` stage (~60 lines across `install.sh`, `activate.sh` and
`nvim.env`) into one line. `_.source` genuinely reproduces denver's `source:`
merge behaviour.

Stops at: no container concept whatsoever — the largest single gap of the
five. Inheritance is *containment* (configs merge from parent directories),
not naming, so reproducing `zephyr-devshell` + `zephyr-devshell-4.3.1` means
restructuring the folder tree, and an env still cannot inherit two bases.
Installs prebuilt tools; cannot build one.

### devbox

The widest package availability, and the only tool here whose container story
is even partial. `raspberry-pico`'s ARM toolchain — a whole conan recipe in
denver — is one line.

Stops at: no venv primitive, so every `uv` stage degrades to hand-rolled
`init_hook` shell (close to the `system_venv.sh` denver's `uv` provider
replaced, and without fingerprinting). `init_hook` is one hook, not an ordered
pipeline. No inheritance at all. Anything missing from nixpkgs means writing a
Nix derivation — the thing devbox exists to spare you.

### devenv

**The closest thing to denver's own design.** It is the only tool in the
comparison that reproduces three mechanisms assumed to be denver-specific
structure:

- `import:` → Nix module `imports = [ ../zephyr-devshell/devenv.nix ]`, a
  *named sibling*, with merge semantics that accumulate lists across layers
  exactly as denver's do, plus `lib.mkDefault`/`lib.mkForce` for finer
  override control than denver has.
- ordered stages → `tasks` with `before`/`after`. `simple-env` is the only
  port anywhere where the "these variables are not set yet" demonstration
  survives.
- `skip-if:` → `tasks.<name>.status`.

Add `languages.python.venv` with `uv.enable` and it replaces denver's `uv`
provider one-for-one.

Stops at: the container direction (below), `source:` scripts that *compute*
what they export (devenv's `env.*` is static; its tasks are subprocesses whose
exports die with them), and packages nixpkgs lacks.

The honest summary is that "you must learn some Nix" is the main thing
standing between a denver user and devenv — not a missing capability.

### flox

Two pleasant surprises: `[include] environments` is real named-sibling
composition (one of only two here), and `on-activate` is the *best* match
anywhere for denver's `source:` semantics — it runs in a bash subshell whose
exports propagate, so a script that derives its values at runtime still works.
Better than devenv on that specific point.

Stops at: no task graph of any kind. mise has `depends`, devenv has
`after`/`status`, flox has one `on-activate` hook. The five-stage Zephyr
pipeline becomes ordering-by-line-number with no per-step skip check, so
`west update` over a ~40-repo workspace re-runs on every activation unless you
hand-write a guard. Move it out of activation to avoid that cost and denver's
actual promise — entering the environment gives you a *correct* workspace — is
gone. Also no venv primitive.

## Capability matrix

| denver mechanism | pixi | mise | devbox | devenv | flox |
|---|---|---|---|---|---|
| Python venv (`uv` stage) | yes | yes | hand-rolled | yes | hand-rolled |
| `import:` a named sibling | flattened | parent-dir only | flattened | **yes** | **yes** |
| Ordered stages | no | `depends` | no | **`before`/`after`** | none |
| `skip-if:` | no | `sources`/`outputs` | no | **`status`** | no |
| `docker` wrapper | no | no | inverted | inverted | inverted |
| `args:` env CLI flags | no | no | no | no | no |
| Lockfile | **yes** | partial | via Nix | via Nix | **yes** |
| Cross-platform | **yes** | **yes** | yes | yes | yes |

## What none of them do

Four things survived the whole comparison.

1. **The container wrapper, in denver's direction.** devbox, devenv and flox
   can all package a resolved environment *as* an image. denver's `docker`
   stage does the opposite: it takes an image *you* name (`ubuntu:24.04`, with
   its apt packages) and relocates every later stage into it — and
   `--skip docker` runs the identical stack on the host. "Ship this env as a
   container" is a different problem from "this project only builds on Ubuntu
   24.04." No tool here answers the second. `zephyr-docker` has no port
   anywhere.

2. **Dependencies that are not knowable up front.** `uv-zephyr` installs
   whatever `west packages pip` reports *after* `west update` has cloned the
   workspace. This defeated all five, devenv included — a lockfile-first model
   structurally cannot express it, and a task graph can order it but not
   declare it. denver's answer is the `freeze-to:` / `requirements.final.txt`
   pattern: resolve once, commit the pins, let a fresh clone skip the
   discovery step.

3. **Fetching and building something nobody has packaged.** `pico-sdk` is
   absent from conda-forge, nixpkgs and the flox catalog, and denver's recipe
   also builds `picotool` from source. A conan recipe pinning an upstream URL
   and checksum is a lower bar than a Nix derivation or a feedstock — and it
   never depends on a third party having packaged the exact version you need.
   Whether that is an advantage or unpaid packaging work depends entirely on
   whether your dependency is already in someone's catalog.

4. **Environment-specific CLI flags.** denver's `args:` declares real argparse
   flags that `denver <env> --help` lists. No tool here has an equivalent.

## On reproducibility

denver's tagline claims *reproducible*, and it is worth being precise about
what that means next to the Nix-backed tools. denver fingerprints inputs and
skips unchanged work; it does not pin a resolution the way `pixi.lock` or a
Nix store path does. `zephyr-devshell-4.3.1`'s `freeze-to:` pattern is a
deliberate, hand-rolled approximation of a lockfile for the one stage that
most needed it.

[`philosophy.md`](philosophy.md) is already candid about the sharpest case —
the `uv` provider's `append-mode`, which accumulates install arguments across
runs and therefore makes a venv depend on that machine's history, which is why
it defaults to `false`. Read "reproducible" in denver's sense as *the same
config produces the same environment*, not in Nix's sense of *content-addressed
and bit-identical*. Where that distinction matters to you, the Nix-backed tools
are stronger and this page recommends them.
