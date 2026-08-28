# examples/zephyr-devshell-4.3.1-alternative

**A proof of concept: the same Zephyr 4.3.1 workspace as
[`../zephyr-devshell-4.3.1`](../zephyr-devshell-4.3.1), built with **nix +
direnv** instead of denver's `docker` → `conan` → `uv` → `zephyr` →
`uv-zephyr` pipeline.**

```console
$ cd examples/zephyr-devshell-4.3.1-alternative
$ ./devshell.sh west build zephyr-rtos/samples/hello_world
... nix builds the devShell, then bootstraps the west workspace ...
-- west build: build configuration ... zephyr/samples/hello_world
[100%] Linking C executable zephyr/zephyr.exe
$ ./devshell.sh west build -b frdm_rw612 zephyr-rtos/samples/hello_world
... same env, real hardware this time -- the ARM cross-compiler kicks in ...
[100%] Linking C executable zephyr/zephyr.elf
$
```

This is **not** a denver env -- there is no `denver.toml` here, and denver
never runs. It answers one question a colleague raised: *could this same
environment be built without denver at all, on nix's own model of pinned,
reproducible tool installation?* The answer is yes, for the parts nix is
good at -- with real gaps documented below, not glossed over.

## What it does

| Layer | denver (`../zephyr-devshell-4.3.1`) | Here |
|---|---|---|
| Isolation | `docker`: builds an image, runs everything inside a container | None -- `nix develop` runs directly on the host. Every tool below is still pinned and reproducible; only the "same Linux regardless of host distro" guarantee is gone (see below) |
| Native tools (cmake, ninja, dtc, the ARM cross-compiler, ...) | `conan`: prebuilt binaries from `conan/catalog.yml`'s pins | `flake.nix`'s `devShells.default.packages`: `nixpkgs` derivations pinned by `flake.lock`, plus `zephyr-nix`'s `hosttools-nix` and `sdk-0_17` |
| Python + `west` | `uv`: a venv from `conan/recipes/python-cache/denver/requirements*.txt` | `zephyr-nix`'s `pythonEnv`, built from `zephyr-src`'s own `scripts/requirements.txt` -- `flake.nix` pins `zephyr-src` to `v4.3.1`, the same tag `west.yml` clones |
| Clone the workspace | `zephyr`: `west update` against `west.yml`, run automatically as a denver stage | `devshell.sh` runs the same `west update` against the same `west.yml` (copied verbatim from `../zephyr-devshell-4.3.1`), but as an explicit bootstrap step, not a stage |
| Modules' own pip deps (`west packages pip`) | `uv-zephyr`: installs them into the same venv, after `west update` | **Not done.** `pythonEnv` is a read-only nix store path -- there is nowhere to `pip install` into. Not needed for `hello_world`; see "Where this breaks down" |

`devshell.sh` is the one-shot equivalent of `denver run <env> -- <command>`.
`.envrc` (`use flake`) is the interactive equivalent of `denver run <env>`
with no command -- `direnv allow` once, then every `cd` into this directory
drops the tools on `PATH` automatically.

## Why nix can do this at all

`zephyr-nix` ([nix-community/zephyr-nix](https://github.com/nix-community/zephyr-nix))
does for nix what `conan/catalog.yml` + the `uv` stage do for denver: it
packages the Zephyr SDK and a `west`-ready Python environment as ordinary,
content-addressed nix derivations, built once and cached in `/nix/store`
forever after. `flake.lock` pins its revision (and everything it pulls in)
exactly the way `conan/catalog.yml` pins recipe revisions -- reviewable in a
diff, reproducible from a bare clone.

`flake.nix` points `zephyr-nix` at `zephyr-src` (this repo's own `v4.3.1`
checkout of upstream Zephyr, `zephyr.flake = false` so it's fetched as plain
source) instead of `zephyr-nix`'s own default pin -- the nix equivalent of
denver's `[uv] requirements:` listing pinned files instead of taking whatever
`uv` would resolve on its own.

## `native_sim` by default, real hardware too

`configs/west_base_config` makes `native_sim` the default board (`board =`),
so `west build zephyr-rtos/samples/hello_world` with no `-b` builds a native
host binary -- no toolchain download, no flashing, `west` → `cmake` →
`ninja` → a linked, runnable Zephyr image in seconds. That alone proves the
whole chain works.

But `flake.nix` also ships the Zephyr SDK's `arm-zephyr-eabi` cross-compiler
(`zephyrSdk`, `targets = [ "arm-zephyr-eabi" ]`) -- SDK **0.17**, because
`zephyr-src`'s own `SDK_VERSION` file pins `0.17.4` at the `v4.3.1` tag, the
same version `../zephyr-devshell-4.3.1/conan/catalog.yml` pins
(`zephyr-sdk/0.17.4`). `zephyr-nix`'s plain `sdk` attribute means something
different -- its own latest package, a 1.x SDK -- and CMake rejects that as
incompatible with the `0.16`-minimum version Zephyr 4.3.1 itself requests;
use the versioned `sdk-0_17` attribute instead. Same cross-compiler covers
both `nrf52840dk/nrf52840` (Cortex-M4, this env's docker sibling's default
target) and `frdm_rw612` (Cortex-M33).

Both live in the *same* devShell; which one `west build` uses is decided per
build, automatically, by Zephyr's own `FindHostTools.cmake`/
`FindZephyr-sdk.cmake`:

- `flake.nix` sets exactly one thing, `ZEPHYR_SDK_INSTALL_DIR` -- pointing at
  `zephyrSdk` -- and leaves `ZEPHYR_TOOLCHAIN_VARIANT` unset. That is
  deliberate, not an oversight: hardcoding it to `host` (so `native_sim`
  works with no flags) would silently break every cross build, since nothing
  would then override it back for a real board.
- For `native_sim` (or any `boards/native/*` board, or `ARCH=posix`, or
  `unit_testing`), `FindHostTools.cmake` force-sets the toolchain to `host`
  regardless of `ZEPHYR_SDK_INSTALL_DIR` -- the host compiler `pkgs.mkShell`
  already provides.
- For every other board, `FindZephyr-sdk.cmake` finds `zephyrSdk` via
  `ZEPHYR_SDK_INSTALL_DIR` and uses its cross-compiler -- no
  `ZEPHYR_TOOLCHAIN_VARIANT` needed at all.

So both of these work in this one devShell, no flag or rebuild needed to
switch between them:

```console
$ ./devshell.sh west build zephyr-rtos/samples/hello_world              # native_sim, host compiler
$ ./devshell.sh west build -b frdm_rw612 zephyr-rtos/samples/hello_world       # ARM, SDK cross-compiler
$ ./devshell.sh west build -b nrf52840dk/nrf52840 zephyr-rtos/samples/hello_world  # ditto
```

Add more `targets` to `zephyrSdk` in `flake.nix` for other architectures
(`riscv64-zephyr-elf`, `xtensa-*`, ...) the same way.

## Where this breaks down

Being honest about the gaps is the point of a proof of concept:

1. **No container isolation.** denver's `docker` stage guarantees the exact
   same Linux and system libraries regardless of host distro. nix pins tool
   *versions*, not the whole OS -- a bug that depends on host libc/kernel
   behavior could still differ between two developers' machines. (In
   practice this matters far less for nix than for a bare host install,
   because almost nothing here links against the host's system libraries --
   but it is not the same guarantee docker makes.)

2. **`west packages pip` has no home.** The denver example's `uv-zephyr`
   stage exists specifically because modules cloned by `west update` can
   declare their own pip dependencies, discoverable only *after* the clone.
   `pythonEnv` here is built ahead of time from `zephyr-src`'s own
   requirements and is a read-only nix store path -- there is nothing to
   `pip install` into afterwards. `hello_world` on `native_sim` needs none of
   that, so it isn't visible here; a real project would need to know its
   modules' extra pip deps up front and add them via
   `zephyr.pythonEnv.override { extraPackages = ps: [ ... ]; }` in
   `flake.nix` -- closer to denver's `requirements.final.txt` lockfile
   pattern (pin what an earlier run discovered) than to `uv-zephyr`'s
   fully-automatic discovery.

3. **No patches.** `../zephyr-devshell-4.3.1` carries five patches against
   `west` and the Zephyr repos themselves (`uv/venv-patcher/`,
   `zephyr/patches.yml`) -- fixes and features not yet upstream. This POC
   applies none of them; `west build` for `hello_world` doesn't need any of
   them, but a real port would have to re-apply (or nix-patch) each one.

4. **No blob cache, no wheel cache.** `conan/recipes/python-cache` and
   `west-blobs-cache` let the denver example install and fetch blobs fully
   offline. This POC always needs network on first run, for `west update`,
   for the nix store paths not already cached locally (`zephyrSdk` included
   -- it is not a small download), and again for any hardware sample that
   fetches its own binary blobs.

## Files

| Path | What it is |
|---|---|
| `flake.nix` / `flake.lock` | The pinned tool set -- nix's equivalent of `conan/catalog.yml` + the `uv` stage's requirements |
| `west.yml` | The same manifest as `../zephyr-devshell-4.3.1/west.yml`, copied verbatim |
| `configs/west_base_config` | Default board (`native_sim/native/64`) + `pristine = auto`, layered in via `WEST_CONFIG_SYSTEM` -- same mechanism as `../zephyr-devshell/hooks/env.sh` |
| `devshell.sh` | `nix develop` + first-run `west config`/`west update`, then runs the given command -- the `denver run <env> -- <command>` equivalent |
| `.envrc` | `use flake`, for direnv -- the interactive `denver run <env>` equivalent |

## Next

- [`../zephyr-devshell-4.3.1`](../zephyr-devshell-4.3.1) -- the denver env
  this mirrors, and where every pin here ultimately comes from
- [nix-community/zephyr-nix](https://github.com/nix-community/zephyr-nix) --
  the flake doing the heavy lifting here, equivalent to `conan` + `uv`
- [direnv](https://direnv.net/) -- what makes `cd`-ing into this directory
  enough to get the tools, without running `devshell.sh` by hand
