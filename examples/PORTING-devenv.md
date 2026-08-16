# Porting the examples to devenv.sh

An honest attempt to express each of denver's seven bundled examples as a
[devenv](https://devenv.sh) project, to find out where the two tools actually
disagree.

**Most of these ports are unverified**, with one important exception:
`zephyr-devshell-4.3.1` (and the `zephyr-devshell` base it imports) has since
been **built and run for real** — see "Verified build" below. Everything else
was written to spec and not executed; package versions assumed rather than
checked are marked UNVERIFIED inline.

## Verified build (`zephyr-devshell-4.3.1`)

```
devenv shell -- west build -p always $ZEPHYR_BASE/samples/hello_world \
    -b native_sim/native/64
```

**Result: passes.** 92/92 ninja targets, and the resulting `zephyr.exe` runs:

```
*** Booting Zephyr OS build 75f67d766726 ***
Hello World! native_sim/native/64
```

Verified with devenv 2.2.0 on Determinate Nix 2.34.8, **CPython 3.12.3 exactly**,
cmake 4.3.4, gcc 15.3.0, west 1.5.0, against a workspace at Zephyr v4.3.1
(54 repos, 2.2 GB).

`imports = [ ../zephyr-devshell/devenv.nix ]` works as claimed: the base
module's packages and settings reached the build through Nix module
composition. Of the five tools, only devenv and flox have a real counterpart
to denver's `import:`, and this is that claim executed rather than asserted.

### The finding that matters most

**A Nix-backed environment is not automatically hermetic.**

The port built successfully *before* `dtc` and `gperf` were declared — because
it silently picked up `/usr/bin/dtc` and `/usr/bin/gperf`, the host's apt
packages. A `devenv shell` keeps the host `PATH` behind its own, so a package
you forgot to declare degrades to *whatever the machine happens to have*
rather than to a clear error.

That is precisely the failure mode denver's
["explicit over implicit"](../doc/philosophy.md) principle exists to prevent —
appearing in the tool with the strongest reproducibility story of the five, and
appearing *silently*. The build passing is what makes it dangerous: nothing
tells you the environment is under-specified. Both are now declared.

### `hooks/env.sh` behaves differently here than under devbox

Worth reading next to [`PORTING-devbox.md`](PORTING-devbox.md), which found
that the shared base's `hooks/env.sh` **crashes** devbox's `init_hook`
(`dirname: missing operand`, because `$BASH_SOURCE` is empty there).

Under devenv it does **not** crash: `${./hooks/env.sh}` copies the file into
the Nix store and sources it by absolute path, so `$BASH_SOURCE` is set. But it
still reads `WEST_TOPDIR` and `DENVER_ENV_WORKDIR` — denver built-ins that
exist nowhere else — so it quietly exports a `CMAKE_PREFIX_PATH` ending in
`/zephyr-rtos` and a `CTCACHE_DIR` of `/ctcache`.

devbox fails loudly and devenv succeeds wrongly. The second is worse, and
neither is a portable use of that file.

### Other changes needed

1. **`devenv.yaml` did not exist.** The original port omitted it entirely, so
   the environment could never have evaluated — `devenv.nix` alone is not an
   environment.
2. **`languages.python.version = "3.12.3"` needs a second flake input.**
   devenv refuses to evaluate without `nixpkgs-python`, because plain nixpkgs
   carries only whatever 3.12.x its revision sits on. denver pins 3.12.3 by
   simply fetching it. Once added, devenv delivered 3.12.3 *exactly* — the only
   tool of the five to match denver's pin precisely (flox gave 3.12.13; mise
   needed a supply-chain check disabled to get 3.12.3 at all).
3. **west was never installed.** Added to `languages.python.venv.requirements`
   rather than `packages`, so it lands in the venv Zephyr's build scripts
   import from.
4. **`ZEPHYR_TOOLCHAIN_VARIANT=host`.**

## The full adaptation

The verification above deliberately left one gap: `ZEPHYR_BASE` pointed at a
workspace devenv did not create. **That gap has since been closed on this
branch**, which is what makes it `devenv-full` rather than a straight port.

Every west task now declares `before = [ "devenv:enterShell" ]`, so
`devenv shell` on a clean checkout *creates* the workspace instead of assuming
one — clones ~40 repositories, installs Zephyr's build-time python packages,
and hands back a shell that can build.

Measured, from `rm -rf .devenv`:

| | |
|---|---|
| clean checkout → running `zephyr.exe` | **1m 15s** (2.2 GB workspace, 54 repos, 92/92 ninja targets) |
| re-entering the environment | **0.19s**, every task skipped |

That is denver's stage model, mechanism for mechanism:

| denver | devenv |
|---|---|
| ordered `stages:` | `tasks` with `after`/`before` |
| runs on entering the env | `before = [ "devenv:enterShell" ]` |
| `skip-if:` / fingerprinting | `tasks.<name>.status` |
| `--force` | `devenv tasks run --refresh` |
| `import:` | Nix module `imports` |

CI enforces it on every PR touching these files
([`.github/workflows/devenv-full.yml`](../.github/workflows/devenv-full.yml)):
clean checkout → `devenv shell -- build-hello-world` → run the binary and
assert its banner → re-enter and assert **nothing re-ran** → assert the
checkout is unmodified. The west workspace is deliberately **not** cached,
since creation-from-nothing is the entire thing under test.

### Three failures it took to get there

Each one is a real difference from denver's model, and none was visible
without running it.

1. **Tasks cannot use venv-provided tools.** The first attempt died on
   `west: command not found`. `languages.python.venv` puts the venv on `PATH`
   as part of *entering the shell*, but these tasks run `before` that — so at
   task time the venv is on disk and not on `PATH`. Every invocation has to
   hardcode an absolute path into `$DEVENV_STATE/venv/bin/`. Tools from
   `packages` (nix-provided, e.g. `uv`) *are* available; venv-provided ones
   are not. denver has no equivalent split: each stage's environment
   accumulates and is handed to the next.

2. **Tasks can race devenv's own internal tasks.** The second attempt died on
   `.devenv/state/venv/bin/west: No such file or directory` — the venv did not
   exist *yet*. It is created by devenv's own `devenv:python:virtualenv` task,
   which is a **sibling** of the west chain under `enterShell`, so the two are
   unordered. The fix is `after = [ "devenv:python:virtualenv" ]`, which
   requires knowing devenv's internal task name (`devenv tasks list` reveals
   it). denver's stages are a declared, totally-ordered list; a stage cannot
   accidentally race the machinery that prepared the one before it.

3. **Failed tasks do not fail the shell.** `devenv shell -- <cmd>` exited **0**
   while printing `✖ Running tasks (failed)`, then ran the command anyway
   against a half-built environment. Only the command's own later failure
   surfaced the problem. denver's "fail loud on the unexpected" makes a broken
   stage a hard error; here a broken setup step is a warning you can miss in
   CI unless you separately assert on the artefact — which is why the workflow
   asserts on the binary's output rather than trusting the exit code.

### What still does not port, even fully adapted

1. **Stage 1, `docker`, is unreachable.** `devenv container build` packages the
   *resolved* environment as an image; denver's `docker` stage relocates later
   stages into an image *you* name (`ubuntu:24.04` and its apt packages), and
   `--skip docker` runs the same stack natively. This adaptation covers stages
   2–5 of five.

2. **`west packages pip` is executable but not declarable.** devenv orders it
   correctly — more than any other tool here manages — but nothing it installs
   lands in `devenv.lock`. denver's `freeze-to:` / `requirements.final.txt`
   pattern commits the resolved pins so a fresh clone skips discovery
   entirely. There is no devenv counterpart.

3. **The hermeticity hole above still applies.** An undeclared package silently
   falls through to the host, so a fully-adapted env can pass in CI and fail on
   a colleague's machine.

Ports are additive: each `devenv.nix` sits next to the `denver.yml` it was
derived from. No example was modified.

## Result per example

| Example | Ported | Fidelity |
|---|---|---|
| `simple-env` | [`simple-env/devenv.nix`](simple-env/devenv.nix) | **Best of the five** — task ordering survives; `source:` semantics do not |
| `zephyr-uv` | [`zephyr-uv/devenv.nix`](zephyr-uv/devenv.nix) | **Full, like-for-like** |
| `raspberry-pico` | [`raspberry-pico/devenv.nix`](raspberry-pico/devenv.nix) | Partial — pico-sdk needs a Nix derivation |
| `zephyr-docker` | *not ported* | Container concept exists but runs the other direction |
| `howto-env` | [`howto-env/devenv.nix`](howto-env/devenv.nix) | 4 of 5 stages |
| `zephyr-devshell` (base) | [`zephyr-devshell/devenv.nix`](zephyr-devshell/devenv.nix) | **Real base module** — the only faithful one in the comparison |
| `zephyr-devshell-4.3.1` | [`zephyr-devshell-4.3.1/devenv.nix`](zephyr-devshell-4.3.1/devenv.nix) | Good, except `west packages pip` |

## devenv is the highest-fidelity port of the five

Three denver mechanisms that no other tool in this comparison reproduced:

1. **`import:` → `imports`.** `devenv.nix` is a Nix module, and modules compose.
   `imports = [ ../zephyr-devshell/devenv.nix ]` is a genuine like-for-like
   replacement for `import: [../zephyr-devshell]` — a *named sibling*, not an
   implicit parent directory (mise) and not a flattening (devbox, flox, pixi).
   Module merge semantics even reproduce denver's rule that every layer's lists
   accumulate rather than replace, and `lib.mkDefault`/`lib.mkForce` give finer
   override control than denver has.

2. **Ordered stages → `tasks` with `before`/`after`.** `simple-env` exists to
   demonstrate that stage 1 observes state stage 2 has not yet created. devenv
   is the only tool here where that survives the port; everywhere else the
   "before" stage would print the variables as already set, demonstrating the
   opposite of the point.

3. **`skip-if:` → `tasks.<name>.status`.** A direct equivalent of denver's
   per-stage "don't redo this" check.

Plus `languages.python.venv` with `uv.enable`, which is a straight replacement
for denver's whole `uv` provider.

## What still did not port

1. **The container wrapper.** `devenv container build` packages *this
   environment* as an OCI image. denver's `docker` stage relocates later stages
   into an image *you* name (`ubuntu:24.04` and its apt packages), with
   `--skip docker` running the same stack natively. Opposite directions. devenv
   answers "ship this env as a container"; it does not answer "this only builds
   on Ubuntu 24.04." `zephyr-docker` has no port.

2. **`source:` that computes its exports.** denver sources a script *into its
   own process* and folds the result into the env, so a script that derives
   values at runtime publishes them to every later stage. A devenv task is a
   subprocess — its exports die with it — and devenv's way to publish variables
   (`env.*`) is static. `simple-env`'s constants restate fine; a script that
   computed them would not port at all.

3. **Dependencies unknowable up front.** `uv-zephyr` installs whatever
   `west packages pip` reports *after* `west update` cloned the workspace.
   devenv can order it but cannot declare it, so it becomes an imperative
   `uv pip install` reaching around devenv's dependency management. denver has
   the same problem and answers it with `freeze-to:` /
   `requirements.final.txt` — commit the resolved pins so a fresh clone skips
   the discovery step. devenv has no counterpart.

4. **Packages that must be built.** `pico-sdk` is not in nixpkgs and denver's
   recipe also builds picotool from source. The fix is writing a Nix
   derivation — a real answer, but a steeper one than a conan recipe, and
   exactly what devenv's simplified-Nix pitch is meant to spare you.

5. **Exact version pins are at nixpkgs' mercy.** `cmake@3.31.9`,
   `neovim@0.12.4`, `arm-none-eabi@15.3` resolve only if the pinned nixpkgs
   input built precisely those. denver's conan recipes pin upstream URLs and
   checksums directly.

6. **Environment-specific CLI flags.** No devenv equivalent to `args:`.

7. **`runnable: false`.** Nothing stops `devenv shell` in the base folder; it
   just yields an incomplete env instead of denver's explicit error.

## Verdict

If denver did not exist, **devenv is the tool its own design is closest to** —
same layering model, same ordered-stages-with-skip-checks model, plus lockfiles
and cross-platform support denver lacks. The honest remaining differences are
the container-wrapper direction, runtime-computed environment, post-clone
dependency resolution, and packaging something nixpkgs does not have. That is a
narrower list than for any other tool here, and it is worth being clear-eyed
that "you must learn some Nix" is the main thing standing between a denver user
and devenv — not a missing capability.
