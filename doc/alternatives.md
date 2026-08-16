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
host* from one declaration; you layer that stack across several repositories;
and your setup is enough steps that *a broken step must stop the run* rather
than leave a half-built environment behind. That is a narrow niche. It is
also, as far as this comparison found, an unoccupied one — see
["What denver does better"](#what-denver-does-better) for the evidence, all of
it from builds that were actually run.

## How this was checked

Every one of denver's seven bundled examples was ported to all five
declarative tools below, and the fidelity gaps recorded. The ports live on
five branches — `port/pixi`, `port/mise`, `port/devbox`, `port/devenv`,
`port/flox` — each adding the tool's config next to the `denver.yml` it came
from, plus an `examples/PORTING-<tool>.md` write-up.

The ports for the six smaller examples are written to spec and not executed;
assumed package names are marked `UNVERIFIED` inline.

**`zephyr-devshell-4.3.1` is different: it has been built and run in all five
tools.** Each now builds Zephyr v4.3.1's `hello_world` for
`native_sim/native/64` against a real 54-repo, 2.2 GB west workspace, and the
resulting binary was executed and its output checked. Every finding below
marked **[verified]** comes from those runs rather than from reading
documentation — and **not one of the five ports worked as originally written**,
which is the single most useful thing this exercise produced.

A sixth branch, `port/devenv-full`, goes further: it extends the devenv port
until `devenv shell` on a clean checkout *creates* the workspace, the way
`denver examples/zephyr-devshell-4.3.1` does. That branch is why several
claims in earlier revisions of this page have been corrected.

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

**And it can bring an environment into existence from a clean checkout
[verified].** An earlier revision of this page said no tool could. That was
wrong: `port/devenv-full` binds the west tasks to `devenv:enterShell`, and
`devenv shell` then clones the 54-repo workspace, installs Zephyr's build-time
packages and produces a working build in **1m 15s**, with re-entry costing
**0.19s** because every task's `status` skips. That is denver's stage model
reproduced — ordered, entry-triggered, skip-checked — in about 60 lines.

Stops at: the container direction (below); `source:` scripts that *compute*
what they export (devenv's `env.*` is static, and its tasks are subprocesses
whose exports die with them); packages nixpkgs lacks; and three orchestration
hazards that only appeared when the thing was actually built — a failed task
not failing the shell, tasks unable to use venv-provided tools, and tasks
racing devenv's own internal ones. All three are written up under
["What denver does better"](#what-denver-does-better).

One more, and it is the reason denver's *explicit over implicit* rule exists
**[verified]**: the devenv port **built successfully while under-specified**,
silently using `/usr/bin/dtc` and `/usr/bin/gperf` from the host's apt
packages. A `devenv shell` keeps the host `PATH` behind its own, so a package
you forgot degrades to "whatever the machine has" rather than to an error. A
green build tells you nothing about whether the environment is complete.

The honest summary is still that "you must learn some Nix" is the main thing
standing between a denver user and devenv — but the orchestration edge cases
above are a real second thing.

### flox

Two pleasant surprises, both **[verified]**: `[include] environments` is real
named-sibling composition — `gcc`, `dtc`, `gperf`, `cmake` and `python` all
reached the verified build through it, and flox reports the merge explicitly —
and `on-activate` is the *best* match anywhere for denver's `source:`
semantics, since it runs in a bash subshell whose exports propagate, so a
script that derives its values at runtime still works. Better than devenv on
that second point.

Stops at: no task graph of any kind. mise has `depends`, devenv has
`after`/`status`, flox has one `on-activate` hook. The five-stage Zephyr
pipeline becomes ordering-by-line-number with a hand-written `if` guard in
place of a per-step skip check — without it, `west update` over a ~40-repo
workspace re-runs on every activation. Also no venv primitive.

And one trap **[verified]**: `[profile]` is sourced by *your shell*, so it does
not run under `flox activate -- <command>` — the non-interactive form matching
`denver <env> -- <command>`. Setting up the venv there left `west` missing in
exactly the mode CI uses. `[hook]` exports reach both.

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
| Creates the env on entry | no | interactive only | no | **yes** | interactive only |
| Setup failure fails the run | — | — | — | **no** | — |
| Undeclared tool → error | **yes** | no (host apt) | **yes** | **no** (silent) | **yes** |

The last three rows are the ones the verification runs added, and they are the
rows this page previously got wrong. "Creates the env on entry" is the whole
subject of `port/devenv-full`; "interactive only" means the entry hook exists
but does not fire under `<tool> ... -- <command>`.

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
   workspace. **[verified]** A lockfile-first model structurally cannot express
   this. devenv *can* order it — `port/devenv-full` proves that, and the
   orchestration half of this gap is genuinely closed there — but nothing it
   installs ever reaches `devenv.lock`. denver's answer is the `freeze-to:` /
   `requirements.final.txt` pattern: resolve once, commit the pins, let a fresh
   clone skip the discovery step. No tool here has a counterpart.

3. **Fetching and building something nobody has packaged.** `pico-sdk` is
   absent from conda-forge, nixpkgs and the flox catalog, and denver's recipe
   also builds `picotool` from source. A conan recipe pinning an upstream URL
   and checksum is a lower bar than a Nix derivation or a feedstock — and it
   never depends on a third party having packaged the exact version you need.
   Whether that is an advantage or unpaid packaging work depends entirely on
   whether your dependency is already in someone's catalog.

4. **Environment-specific CLI flags.** denver's `args:` declares real argparse
   flags that `denver <env> --help` lists. No tool here has an equivalent.

## What denver does better

The sections above are deliberately unkind to denver, because a page that only
listed strengths would be worth nothing to someone deciding. This section is
the other half — but restricted to things the verification runs actually
demonstrated, not things the design merely intends.

### 1. A broken setup step is a hard error **[verified]**

The sharpest finding of the whole exercise. `devenv shell -- <command>` exits
**0** while printing `✖ Running tasks (failed)`, and then runs your command
against a half-built environment. Only the command's own later failure
surfaces anything, and a CI job that checks exit codes goes green on an
environment that was never built.

denver's [fail loud on the unexpected](philosophy.md) makes a failed stage
abort the run. That principle reads like housekeeping in the abstract; it is
worth considerably more than that in practice, and it took an actual failed
run to see it. `port/devenv-full`'s CI workflow has to assert on the *built
binary's output* rather than on an exit code, purely to work around this.

### 2. A stage's environment is cumulative **[verified]**

denver builds the environment up stage by stage: whatever stage 3 puts on
`PATH` is visible to stage 4 and to the final command. That sounds obvious
until you try to reproduce it.

In devenv, tasks that run before `enterShell` **cannot use tools the shell
provides** — `port/devenv-full` first failed with a bare
`west: command not found`, and every call had to be rewritten as an absolute
path into `$DEVENV_STATE/venv/bin/`. Tools from `packages` (Nix-provided) are
available; tools from the venv are not. Work that must happen before the shell
is ready cannot use the shell that work is preparing.

### 3. Stages cannot race the framework's own machinery **[verified]**

`port/devenv-full` failed a second time with
`.devenv/state/venv/bin/west: No such file or directory` — the venv did not
exist yet, because it is created by devenv's *own* `devenv:python:virtualenv`
task, a sibling of the west chain under `enterShell` and therefore unordered
against it. Fixing it means knowing devenv's internal task names.

denver's `stages:` is a declared, totally-ordered list. There is no hidden
internal step for a stage to race, and no way to express an ordering bug of
this shape.

### 4. `<env> -- <command>` behaves like the interactive shell **[verified]**

Two of the five tools quietly do less work in non-interactive mode — which is
the mode CI runs in:

- **flox**: `[profile]` is sourced by *your shell*, so it does not run under
  `flox activate -- <command>`. The venv it put on `PATH` was simply missing.
- **mise**: `hooks.enter` never fired under `mise exec --`; it needs
  `mise activate` shell integration, i.e. an interactive `cd`.

`denver <env> -- <command>` runs the identical pipeline as the interactive
form. An environment that works at your prompt works in CI.

### 5. Pins do not depend on a third party having packaged that version **[verified]**

denver's conan recipes name an upstream URL and a checksum. Asking the same of
a catalog-backed tool produced four different problems, for one interpreter:

| Tool | Getting python 3.12.3 |
|---|---|
| devenv | needs a **second flake input** (`nixpkgs-python`); plain nixpkgs carries only whatever 3.12.x its revision sits on |
| mise | **refuses outright** unless `python.github_attestations = false` — the 3.12.3 build predates GitHub artifact attestations, so pinning it means opting out of a supply-chain check |
| flox | silently gave **3.12.13** |
| pixi / devbox | fine |

And `pico-sdk` is in **no** catalog — conda-forge, nixpkgs and flox alike —
while denver's recipe also builds `picotool` from source. Whether this is an
advantage or unpaid packaging work depends entirely on whether what you need
is already packaged by someone else.

### 6. One file, that denver does not own

Every tool needed scaffolding beyond the config the port had written:

- **devenv**: `devenv.yaml` — without it the environment cannot evaluate at all
- **flox**: `.flox/env.json`, generated by `flox init` and committed; a
  manifest alone is *not* an environment, and its absence also broke
  `[include]`
- **pixi**: `[project]` had to become `[workspace]`

And flox's `flox edit` **rewrote the manifest and dropped its header
comments** — the file is owned by the tool, not by you. For a config whose main
job is explaining itself, that matters. A `denver.yml` is a plain file nothing
rewrites.

### 7. `--show-config` has no equivalent anywhere

denver resolves configuration and executes it as two separate steps, so
`--show-config` can promise that what it prints is what runs — see
["central default resolution"](philosophy.md). None of the five tools offers
this. It needs no toolchain, no network and no Docker, which makes it the
fastest way to understand an environment somebody else wrote.

Not exercised by the build runs, so it carries no **[verified]** tag — but it
is a structural property of the design rather than a claim about behaviour.

### The honest scorecard

Of these seven, **three (1–3) only became visible by building the thing**, and
all three are about *orchestration correctness* rather than about packages.
That is the real shape of denver's advantage: not what it can install — every
tool here beats it on breadth — but that a multi-step environment either comes
up correctly or fails loudly, with no silent half-built state and no ordering
you can get wrong.

Set against that, the [capability matrix](#capability-matrix) above is equally
honest: lockfiles, cross-platform support and package breadth are all places
denver loses, and for most projects those matter more.

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
