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
| Native tools (cmake, ninja, dtc, the ARM cross-compiler, clang, doxygen, protoc, J-Link, SystemView, ...) | `conan`: prebuilt binaries from `conan/catalog.yml`'s pins | `flake.nix`'s `devShells.default.packages`: plain `nixpkgs` derivations pinned by `flake.lock` for everything nixpkgs already has (`clang_21`, `doxygen`, `protobuf`, ...), plus `zephyr-nix`'s `hosttools-nix`/`sdk-0_17`, plus `nix/jlink.nix` and `nix/systemview.nix` for the two SEGGER tools nixpkgs doesn't (see below) |
| Python + `west` | `uv`: a venv from `conan/recipes/python-cache/denver/requirements*.txt` | `zephyr-nix`'s `pythonEnv`, built from `zephyr-src`'s own `scripts/requirements.txt` -- `flake.nix` pins `zephyr-src` to `v4.3.1`, the same tag `west.yml` clones |
| Clone the workspace | `zephyr`: `west update` against `west.yml`, run automatically as a denver stage | `devshell.sh` runs the same `west update` against the same `west.yml` (copied verbatim from `../zephyr-devshell-4.3.1`), but as an explicit bootstrap step, not a stage |
| Modules' own pip deps (`west packages pip`) | `uv-zephyr`: installs them into the same venv, after `west update` | `devshell.sh` runs `west packages pip --install` too, after `west update` -- into a `.pip-extra/` `--target` directory instead of the (read-only) `pythonEnv`, put on `PYTHONPATH` from then on. See "Where this breaks down" for the one real gap this leaves |

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

## J-Link and SystemView: nix packages that have to be hand-written

`nix/jlink.nix` and `nix/systemview.nix` are the nix equivalents of
[`../zephyr-devshell/conan/recipes/jlink`](../zephyr-devshell/conan/recipes/jlink)
and `.../systemview` -- nowhere near as short as reusing `zephyr-nix`,
because there is no public nix package for either SEGGER tool. SEGGER only
serves both tarballs from behind a license click-through (a POST request
accepting their terms; see those conan recipes' `conandata.yml` files),
which plain `pkgs.fetchurl` cannot reproduce on its own -- `curlOptsList`
replays the exact same POST, and `hash` (independently verified against
each recipe's own recorded md5) makes the result a normal, reproducible
fixed-output derivation, no vendored tarball needed. (A first version of
`jlink.nix` *did* vendor a copy instead -- reverted, because this repo's
top-level `.gitignore` deliberately excludes `*.tgz` everywhere, and because
a local flake's own source is copied into the nix store as one unit before
evaluation runs, so a relative path reaching *outside* this directory would
have resolved against that copy anyway, where the sibling example was never
copied.)

Both derivations are plain `stdenv.mkDerivation`s: unpack, then
`autoPatchelfHook` rewrites the prebuilt binaries' ELF interpreter and RPATH
to point into the nix store instead of the FHS paths (`/lib64/...`) they
were built against.

- **J-Link**'s CLI tools (`JLinkExe`, `JLinkGDBServerCLExe`) and
  `libjlinkarm.so` itself need only `libc`/`libdl` (checked with
  `readelf -d`), so autoPatchelfHook alone is enough for them.
  `autoPatchelfIgnoreMissingDeps = true` leaves the pack's GUI tools
  (`JFlash`, `JMem`, `JScope`, ...) unpatched rather than failing the whole
  build over tools this devShell never runs -- they want a bundled Qt4 plus
  the host's X11, same as SystemView below.
- **SystemView** *is* one of those GUI tools -- its one binary needs that
  Qt4 + X11 + fontconfig + freetype, all listed in `buildInputs`. In
  practice the standard fixup phase's `--shrink-rpath` step drops the
  `freetype` RPATH entry back out for this specific stripped,
  closed-source, plugin-loading Qt4 build regardless of what
  autoPatchelfHook wrote (verified with `nix log` vs. `ldd`) -- so
  `flake.nix`'s `shellHook` also puts the same libraries on
  `LD_LIBRARY_PATH`, which that shrink step has no say over. SystemView
  additionally needs `libjlinkarm.so` to talk to a probe, dlopen()'d
  relative to its own directory rather than searched for on
  `LD_LIBRARY_PATH` (verified with `LD_DEBUG=libs`) -- SEGGER's own fix for
  a SystemView install missing this file is to copy it in from a J-Link
  install, which `systemview.nix`'s `installPhase` does literally, from the
  `jlink` derivation next to it.

**Not done:** the `99-jlink.rules` udev rule
(`../zephyr-devshell/setup/install_jlink_udev_rules.sh` installs it for the
docker example) that lets `JLinkExe` open the USB device without root. It
ships inside the pack (`nix/jlink.nix`'s output has it at
`bin/99-jlink.rules`) but installing a udev rule is a host-level, one-time,
outside-any-devShell action -- not something a `nix develop` environment can
or should do for you. And SystemView's window itself is untested here: this
POC was verified in a sandbox with no X display, so "does it crash on
startup" (fixed) is as far as that verification goes.

## Where this breaks down

Being honest about the gaps is the point of a proof of concept:

1. **No container isolation.** denver's `docker` stage guarantees the exact
   same Linux and system libraries regardless of host distro. nix pins tool
   *versions*, not the whole OS -- a bug that depends on host libc/kernel
   behavior could still differ between two developers' machines. (In
   practice this matters far less for nix than for a bare host install,
   because almost nothing here links against the host's system libraries --
   but it is not the same guarantee docker makes.)

2. **The module pip layer isn't cross-checked against the nix layer.**
   `devshell.sh` now runs `west packages pip --install` after `west update`
   (`-m <module>` for each cloned project with a `zephyr/module.yml` pip
   section, `zephyr` itself skipped -- its requirements are already in
   `pythonEnv`), installing into `.pip-extra/` via `pip install --target`
   rather than `pythonEnv`'s read-only nix store path, then exporting
   `PYTHONPATH` to include it. Functionally this ends up with the same
   packages importable as `uv-zephyr` installs -- verified for this
   manifest's `mcuboot` (`imgtool`, `cbor`) and `nanopb` (`grpcio-tools`).
   The gap: denver's `uv-zephyr` resolves the base and module requirements
   *together*, in one `uv`/`pip` solve, so a version conflict between them
   is caught immediately; here they are two separate, unrelated
   resolutions -- `pip install --target` has no visibility into what
   `pythonEnv` already pinned. In practice this surfaced as pip warning
   about `spsdk` (an unrelated tool already in `pythonEnv`) wanting an older
   `click`/`setuptools` than what `.pip-extra/` ended up with -- harmless
   for `west build`, but a real version conflict between the *base* and a
   *module's* own requirements would only show up as a runtime import error,
   not caught at install time the way `uv-zephyr` catches it.

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

5. **A handful of denver-specific extras are not ported.** `fish` --
   `devshell.sh` drops you into `bash`, not fish, by design (see "A visible
   PS1" below for how that shell still says which env it is). `hooks/env.sh`'s
   `CMAKE_COLOR_DIAGNOSTICS`, `CMAKE_PREFIX_PATH`, `CCACHE_NOHASHDIR`,
   `CTCACHE_*` and `CODECHECKER_TRIM_PATH_PREFIX` are not set (only
   `CCACHE_BASEDIR`, `LD_LIBRARY_PATH` and `PS1` are, in `flake.nix`'s
   `shellHook`). The `ctcache` / `codechecker` / `git-nested` / `venv-patcher`
   pip packages are denver-specific additions on top of upstream Zephyr's
   own `scripts/requirements.txt`, so they are invisible to both `pythonEnv`
   and the `west packages pip` layer above; nothing here installs them.

## A visible PS1

`flake.nix`'s `shellHook` prefixes `PS1` with `(zephyr-devshell-4.3.1-nix)`,
so an interactive shell in this env is visibly not your normal one --
denver's docker example gets the same effect for free from the container
prompt. `devshell.sh`'s own `exec bash` (the no-command case) has to redo
this *after* `~/.bashrc` runs, via `--rcfile`, since an interactive
non-login bash sources that file and most distros' default `~/.bashrc`
overwrites `PS1` outright; direnv (`.envrc`) does not re-source `~/.bashrc`,
so the `shellHook` line alone is enough there.

## A nix quirk worth knowing before adding a new file here

Every file this flake reads -- not just `flake.nix` itself, all of
`nix/*.nix`, `configs/west_base_config`, `west.yml` -- only became visible
to `nix develop` once `git add`ed. This directory sits inside a git
repository, and once *any* file in it is tracked, evaluating this flake from
inside it resolves against git's index, not the raw working tree -- so a
brand new, never-staged file (unlike an edit to an already-tracked one) is
invisible until staged. If you add another file here, stage it before
expecting `./devshell.sh` to see it.

## Files

| Path | What it is |
|---|---|
| `flake.nix` / `flake.lock` | The pinned tool set -- nix's equivalent of `conan/catalog.yml` + the `uv` stage's requirements. `flake.lock`'s `nixpkgs` pin is an explicit revision, not a branch name -- see the comment on `nixpkgs.url` for why |
| `nix/jlink.nix`, `nix/systemview.nix` | J-Link and SystemView, hand-packaged -- nix's equivalent of `conan/recipes/jlink` and `.../systemview`; see "J-Link and SystemView" above |
| `west.yml` | The same manifest as `../zephyr-devshell-4.3.1/west.yml`, copied verbatim |
| `configs/west_base_config` | Default board (`native_sim/native/64`) + `pristine = auto`, layered in via `WEST_CONFIG_SYSTEM` -- same mechanism as `../zephyr-devshell/hooks/env.sh` |
| `devshell.sh` | `nix develop` + first-run `west config`/`west update`/`west packages pip --install`, then runs the given command -- the `denver run <env> -- <command>` equivalent |
| `.envrc` | `use flake`, for direnv -- the interactive `denver run <env>` equivalent |

## Next

- [`../zephyr-devshell-4.3.1`](../zephyr-devshell-4.3.1) -- the denver env
  this mirrors, and where every pin here ultimately comes from
- [nix-community/zephyr-nix](https://github.com/nix-community/zephyr-nix) --
  the flake doing the heavy lifting here, equivalent to `conan` + `uv`
- [direnv](https://direnv.net/) -- what makes `cd`-ing into this directory
  enough to get the tools, without running `devshell.sh` by hand
