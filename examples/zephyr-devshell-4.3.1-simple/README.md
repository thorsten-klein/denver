# examples/zephyr-devshell-4.3.1-simple

**The same Zephyr RTOS 4.3.1 development environment as
[`zephyr-devshell-4.3.1`](../zephyr-devshell-4.3.1) — one `denver.toml`, no
`docker`, no `conan`.**

```console
$ denver run examples/zephyr-devshell-4.3.1-simple
... denver builds each stage, straight on your host ...
dev@host ~/workspace> west build -b nrf52840dk/nrf52840 samples/hello_world
dev@host ~/workspace> exit      # back to your normal shell
```

Same result, same pins, same patches. What's different is entirely in how
it gets there.

## What it does

Eight stages, in order:

| # | Stage | Provider | What happens |
|---|-------|----------|--------------|
| 1 | `host-tools` | `custom` | Checks the handful of apt packages the rest of this needs are on `PATH` (`git`, `gperf`, a device tree compiler, ...) — installed via `--scripts setup`, never silently |
| 2 | `native-tools` | `download` | Fetches cmake, ninja, clang, fish, doxygen, protoc and SystemView as prebuilt release archives |
| 3 | `zephyr-sdk-download` | `download` | Fetches the Zephyr SDK's minimal base plus its three cross toolchains (arm/riscv64/x86_64), as four separate archives |
| 4 | `zephyr-sdk` | `custom` | Assembles the four into one working SDK install and runs its own `setup.sh` |
| 5 | `jlink` | `custom` | SEGGER J-Link tools — the one download that needs a license-accepting `POST`, so it can't be a `download` package |
| 6 | `uv` | `uv` | Creates a **virtualenv** with the pinned Python packages, above all `west` |
| 7 | `zephyr` | `zephyr` | `west update`: clones the Zephyr workspace at the revisions pinned for 4.3.1, applies this env's patches, fetches binary blobs |
| 8 | `uv-zephyr` | `uv` | Installs the Python dependencies the modules `zephyr` just cloned declare (`west packages pip`) |

Then denver hands control to `fish` with all eight layers active — the same
shell `zephyr-devshell`'s `docker.compose.default-cmd:` used to pick, now
this env's own top-level `command:` (see denver.toml).

## Why it exists

**It shows the same environment without a container or a package manager.**
[`zephyr-devshell-4.3.1`](../zephyr-devshell-4.3.1) is denver's proof that
the model scales to a genuinely large, three-file, `docker` + `conan`
environment. This is the other side of that proof: the same workspace, the
same pins, the same patches, with `docker` and `conan` both thrown away and
`download`/`custom` doing their jobs instead — the same trade-off
[`raspberry-pico`](../raspberry-pico) makes at a much smaller scale, made
here at the scale that actually stresses it.

**It shows what that trade-off costs, honestly.** Not everything conan did
translates for free:

- **The Zephyr SDK ships as four archives**, and a `download` stage only
  ever owns one archive per `unpack-dir:` (see
  [`download.md`](../../doc/providers/download.md)) — there is no way to make
  four packages share one the way conan's single `build()` step could. So
  `zephyr-sdk-download` fetches all four into their own trees, and a second
  stage, `zephyr-sdk`, assembles them by hand (symlink the toolchains in,
  run `setup.sh`) — see `setup/setup_zephyr_sdk.sh`.
- **`ccache` used to be built from source** — a real conan recipe with a
  cmake build and a carried patch, exactly the kind of dependency graph
  conan exists for. `download` only ever unpacks a *prebuilt* archive, so
  this env gets `ccache` from apt instead: still there, just unpinned. See
  the comment in `setup/install_host_tools.sh`.
- **No `requirements.final.txt` lockfile.** The full env resolves its
  Python packages once (offline, against a conan-built wheel cache) and
  commits the fully-pinned result, so a fresh clone never re-resolves
  anything. This env has no offline cache to resolve against, so it installs
  straight from `uv/requirements.txt` + `uv/requirements-from-git.txt` on
  every run instead — simpler, at the cost of the exact-transitive-version
  reproducibility that pattern buys.
- **No container.** `docker` gave the full env a fixed Linux userland
  (same system libraries on Ubuntu, Fedora or WSL) on top of the toolchain
  pins. This env only pins the toolchain — the handful of apt packages in
  `setup/install_host_tools.sh` still come from whatever your host's package
  manager currently has.

None of this is a defect in `download`/`custom` — it's what "no dependency
resolver, no build sandbox" actually means once a real tool (the SDK, an
unpinned system package, a compiled-from-source cache) doesn't fit "fetch
one archive and unpack it". Reach for
[`conan`](../../doc/providers/conan.md) when that's the tool you're adding;
reach for [`docker`](../../doc/providers/docker.md) when the host's own
libraries stop being close enough. Neither is required just because the
project is a large one — this env is the evidence.

## Purpose as an example

**1. `download` for everything that ships a prebuilt archive.** Seven
conan recipes whose entire `build()` step was some variant of "extract this
tarball, maybe strip a leading directory" become seven
`[[native-tools.packages]]` entries:

```toml
[native-tools]
provider = "download"

[[native-tools.packages]]
name = "ninja"
url = "https://github.com/ninja-build/ninja/releases/download/v1.13.2/ninja-linux.zip"
md5sum = "54a25b8d5b5bed15c1bf051a629b42bb"
env-prepend = { PATH = "${DENVER_UNPACK_DIR}:" }
```

`unpack-cmd:` (see [`download.md`](../../doc/providers/download.md)) covers
the two cases a bare unpack doesn't: `clang` strips LLVM's own wrapping
directory and symlinks `ld -> ld.lld`; `cmake`/`doxygen`/`systemview` just
strip their own wrapping directory. `ninja`/`fish`/`protoc` need nothing
extra — their archives already unpack flat.

**2. `custom` where `download` genuinely can't reach.** Two stages, for two
different reasons a plain `GET` isn't enough:

- **`zephyr-sdk`** — assembly. `download` fetched and checksummed the four
  archives; something still has to turn four trees into one SDK install (see
  "Why it exists" above). Its `cmd:` is `setup/setup_zephyr_sdk.sh`.
- **`jlink`** — SEGGER's download only serves the real archive after a
  `POST` that accepts its license agreement; a plain `GET` to the same url
  returns an HTML page instead (`systemview`, right next to it in
  `native-tools`, happens to serve the real file on a plain `GET`, which is
  why it's a `download` package and `jlink` isn't). See
  `setup/download_jlink.sh` — the "Worked example" in
  [`custom.md`](../../doc/providers/custom.md), just with a `POST`.

**3. `env`/`env-prepend` instead of a `source:` script.** Neither `custom`
stage above has a `source:` of its own — a one-line `export` is exactly what
the generic per-stage `env:`/`env-prepend:` keys are for (see "Generic stage
keys" in [`denver-toml.md`](../../doc/configuration/denver-toml.md)), no
script needed:

```toml
[jlink]
provider = "custom"
cmd = "${DENVER_ENV_DIR}/setup/download_jlink.sh"
env-prepend = { PATH = "${JLINK_DIR}/bin:", LD_LIBRARY_PATH = "${JLINK_DIR}/bin:" }
```

Both stages need the *same* path in two places — the `cmd:` script that
builds the thing, and the `env`/`env-prepend:` that activates it — so rather
than hardcode it twice, it's set once, in the top-level `[env]`, before any
stage runs:

```toml
[env]
ZEPHYR_SDK_INSTALL_DIR = "${DENVER_ENV_WORKDIR}/download/zephyr-sdk-minimal"
JLINK_DIR = "${DENVER_ENV_WORKDIR}/jlink"
```

`setup_zephyr_sdk.sh`/`download_jlink.sh` read theirs back as a real `$VAR`
(it's already in the subprocess's environment); `[jlink]`'s `env-prepend:`
above reads `JLINK_DIR` back as `${VAR}` (denver's own interpolation, see
"Variable interpolation" in that same doc page). `zephyr-sdk` doesn't even
need an `env:` of its own — `ZEPHYR_SDK_INSTALL_DIR` is already set, globally,
before that stage (or any stage) has run at all.

**4. One `denver.toml`, not three.** The full env is `zephyr-docker` +
`zephyr-devshell` + `zephyr-devshell-4.3.1`, three files chained by
`import:` so the container config, the shared pipeline and the
version-specific pins can each live where they're declared once and reused.
This env has no container config to share and no second Zephyr version
reusing its pipeline, so there is nothing left to split apart — every stage
this needs is declared once, here.

## Files

| Path | What it is |
|---|---|
| `denver.toml` | All eight stages, merged from the three-file `zephyr-docker`/`zephyr-devshell`/`zephyr-devshell-4.3.1` chain |
| `setup/check_host_tools.sh` / `install_host_tools.sh` | `host-tools`' `cmd:` / `scripts: setup:` |
| `setup/setup_zephyr_sdk.sh` | `zephyr-sdk`'s `cmd:` — the SDK assembly (activation is the top-level `[env]`'s `ZEPHYR_SDK_INSTALL_DIR`, no `source:` needed) |
| `setup/download_jlink.sh` | `jlink`'s `cmd:` — the license-`POST` download (activation is that stage's own `env-prepend:`) |
| `setup/install_jlink_udev_rules.sh` | `zephyr`'s `scripts: setup:` — same as the full env |
| `uv/requirements.txt` + `requirements-from-git.txt` + `overrides.txt` | The pinned Python packages, minus `conan` itself |
| `uv/venv-patcher/` | The same three `west` patches the full env applies |
| `zephyr/module.yml` + `patches.yml` + `patches/` | The same Zephyr workspace patches as the full env |
| `west.yml` | The same 4.3.1 manifest as the full env |
| `hooks/env.sh` | Same exports as the full env's base (`zephyr-devshell/hooks/env.sh`) |

## Next

- [`../zephyr-devshell-4.3.1`](../zephyr-devshell-4.3.1) — the full,
  `docker` + `conan` version this merges and simplifies
- [`../raspberry-pico`](../raspberry-pico) — the same `download`/`git`/
  `custom` trade-off at a much smaller scale
- [`doc/providers/download.md`](../../doc/providers/download.md) — `packages:`,
  checksums, `unpack-cmd:`
- [`doc/providers/custom.md`](../../doc/providers/custom.md) — the worked
  example `jlink`/`zephyr-sdk` build on
- ["Generic stage keys"](../../doc/configuration/denver-toml.md) — `env:`/
  `env-prepend:`, the top-level `[env]` block
