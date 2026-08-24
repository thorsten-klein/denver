# examples/raspberry-pico

**An embedded C/C++ cross-compilation environment — an ARM toolchain, the
Pico SDK and `picotool` — with no container and no build system of its own
involved.**

## What it does

`denver run examples/raspberry-pico` gives you a shell in which you can build
firmware for the [Raspberry Pi Pico](https://www.raspberrypi.com/documentation/microcontrollers/):

- `arm-none-eabi-gcc` 15.3 — the ARM bare-metal cross-compiler
- `pico-sdk` 2.3.0, with `PICO_SDK_PATH` set
- `picotool` 2.3.0, built against that SDK

None of it is installed on your machine in the usual sense. The ARM toolchain
arrives as a prebuilt archive; the Pico SDK and `picotool` as pinned git
checkouts (submodules included, for the SDK) — only `picotool` is actually
built locally, against the SDK checkout next to it.

## Why it exists

**It is the "our tools aren't Python" case, in its smallest honest form.**
Embedded projects are where the classic README —  *"install the GNU Arm
toolchain (version X), then the Pico SDK, set `PICO_SDK_PATH`, and build
picotool against it"* — does the most damage, because every one of those
steps is a place for two developers' machines to diverge. This env is that
README made executable, and pinned.

**It shows that `docker` is optional.** This is a full cross-compilation
toolchain and there is no `docker` stage anywhere in it. Everything runs
directly on the host. Reach for a container when you need a specific OS or
system libraries (see [`../zephyr-docker`](../zephyr-docker)) — not
reflexively, just because the project is an embedded one.

**It shows that a package manager is optional too.** Nothing here needs
Conan, or even Python: every tool arrives via a `git`/`download` stage
talking straight to its own upstream, plus two `custom` stages — one that
only *checks* for the handful of apt packages the rest needs, one that
builds `picotool` with a plain `cmake` invocation. Reach for
[`conan`](../../doc/providers/conan.md) when a build genuinely has a
dependency graph to resolve (see [`zephyr-devshell-4.3.1`](../zephyr-devshell-4.3.1)
for that case) — not for "fetch one archive and unpack it".

## Purpose as an example

**1. `git`/`download` instead of hand-vendored archives.** This env used to
vendor its own copy of every third-party source it needed: `pico-sdk`'s
release tarball, five *more* tarballs for the submodules that tarball ships
as empty `lib/<name>` placeholders (each hand-pinned to the commit `pico-sdk`
2.3.0 itself points its submodules at), and a Conan recipe around a prebuilt
ARM toolchain archive whose whole `build()` step was `tar -xf`. All of that
is now four stages, none of them a Conan recipe:

```toml
stages = ["host-tools", "arm-none-eabi", "pico-sdk", "picotool", "build-picotool"]

[arm-none-eabi]
provider = "download"                    # was a whole conan recipe for "tar -xf"

[pico-sdk]
provider = "git"
url = "https://github.com/raspberrypi/pico-sdk.git"
revision = "2.3.0"
submodules = true                        # git submodule update --init -- no more hand-pinned shas
env = { PICO_SDK_PATH = "..." }          # a scalar path, not PATH -- 'env:', not 'env-prepend:'

[picotool]
provider = "git"                         # same provider as pico-sdk, just no submodules
url = "https://github.com/raspberrypi/picotool.git"
revision = "2.3.0"
env = { PICOTOOL_SRC = "..." }
```

**2. `pico-sdk`/`picotool` fetch; `build-picotool` builds.** A `git` stage
only ever gets you a checkout — `picotool` ships as source, not a prebuilt
binary, so something still has to compile it. That's `build-picotool`, a
plain `custom` stage using the same `cmd:` builds / `source:` activates split
as the "Worked example" in [`custom.md`](../../doc/providers/custom.md):

```toml
[build-picotool]
provider = "custom"
cmd = "bash ${DENVER_ENV_DIR}/setup/build_picotool.sh"  # cmake -S/-B/--build/--install
source = "setup/activate_picotool.sh"                   # export PATH=...
```

`build_picotool.sh` reads `PICO_SDK_PATH` and `PICOTOOL_SRC` straight out of
its environment — both are already there, set by the `pico-sdk`/`picotool`
git stages above (stages accumulate). It has to recognise its own previous
build and skip it (denver fingerprints a `uv`/`conan` stage's inputs, but not
an arbitrary `cmd:`'s) — see "`cmd:` vs `source:`" in
[`custom.md`](../../doc/providers/custom.md).

**3. `host-tools`: check, don't install.** `cmake`/`build-essential`/
`libusb-1.0-0-dev` need `sudo apt-get install`, which a normal `denver run`
must never do silently. So `host-tools`' `cmd:` only *checks* they are on
`PATH` and fails with the fix if not; the actual install is `scripts: setup`,
run once, explicitly:

```bash
denver run examples/raspberry-pico --scripts setup
```

**4. Stage order here is about dependencies, not tool bootstrapping.**
Unlike an env whose `conan`/`uv` stage needs an earlier stage's own binary on
`PATH` (see [`firmware-env`](../firmware-env)'s `uv` → `conan`), these stages
are independent processes that only need *values*, not tools, from each
other: `build-picotool` reads `$PICO_SDK_PATH`/`$PICOTOOL_SRC`, so `pico-sdk`
and `picotool` have to run first — that's the only real ordering constraint.
`host-tools` and `arm-none-eabi` could run anywhere before `build-picotool`.

## Files

| Path | What it is |
|---|---|
| `denver.toml` | Five stages: `host-tools`, `arm-none-eabi`, `pico-sdk`, `picotool`, `build-picotool` |
| `setup/check_host_tools.sh` | `host-tools`' `cmd:` — checks `git`/`cmake` are on `PATH` |
| `setup/install_host_tools.sh` | `host-tools`' `scripts: setup` — the one-time, sudo apt-get install |
| `setup/build_picotool.sh` | `build-picotool`'s `cmd:` — builds `picotool` via cmake, idempotently |
| `setup/activate_picotool.sh` | `build-picotool`'s `source:` — puts the build on `PATH` |
| `setup/picotool.env` | the build dir / install prefix pin, shared by both scripts above |

## Next

- [`doc/providers/git.md`](../../doc/providers/git.md) — the `pico-sdk`/
  `picotool` stages: `url:`, `revision:`, `submodules:`
- [`doc/providers/download.md`](../../doc/providers/download.md) — the
  `arm-none-eabi` stage: `packages:`, checksums, `unpack-cmd:`
- [`doc/providers/custom.md`](../../doc/providers/custom.md) — the
  `host-tools`/`build-picotool` stages: `cmd:` vs `source:` vs `scripts:`
- [`../zephyr-devshell-4.3.1`](../zephyr-devshell-4.3.1) — where reaching for
  Conan (a real dependency graph, reused recipes, a generated catalog) pays
  for itself
