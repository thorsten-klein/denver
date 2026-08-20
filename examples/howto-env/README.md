# examples/howto-env

**The environment built step by step in
[`doc/quickstart/creating-environments.md`](../../doc/quickstart/creating-environments.md) — five stages over the four providers
how-to uses.** Read the how-to for the reasoning; this folder is the result,
runnable.

## What it does

It turns this onboarding note:

> Use Ubuntu 24.04, as our CI does. `apt install jq net-tools curl`. Grab the
> prebuilt `neovim` 0.12.4 tarball from GitHub and put it on `PATH`. Download
> `cmake` 3.31.9 and the ARM toolchain 15.3 — exactly those versions. Install
> `uv`, create a python 3.12 virtualenv, install `pytest==9.1.1`. Export
> `PYTEST_ADDOPTS="-v -s"`, our team convention. Then you can run `pytest`.

into one command:

```console
$ denver run examples/howto-env -- pytest examples/howto-env/tests
test_environment.py::test_docker_stage_gave_us_ubuntu_24_04 PASSED
test_environment.py::test_docker_stage_installed_the_apt_packages PASSED
test_environment.py::test_uv_stage_gave_us_python_3_12_and_pytest PASSED
test_environment.py::test_custom_stage_put_the_hand_installed_nvim_on_path PASSED
test_environment.py::test_conan_stage_gave_us_the_pinned_tool_versions PASSED
test_environment.py::test_custom_stage_exported_the_team_convention PASSED
```

Five stages, each named after the problem it solves rather than after its
provider:

| Stage id | Provider | What it does |
|---|---|---|
| `docker-base` | `docker` | relocates everything below into an Ubuntu 24.04 container |
| `uv-packages` | `uv` | the python 3.12 venv: `pytest` (and `conan`, for the next stage) |
| `nvim-setup` | `custom` | downloads, checksums and unpacks one prebuilt release, by hand |
| `conan-packages` | `conan` | downloads `cmake` 3.31.9 and `arm-none-eabi` 15.3, exactly |
| `best-practices` | `custom` | exports `PYTEST_ADDOPTS`, sourced so it survives |

The middle two are deliberately the same job twice — "this exact prebuilt
tool, on `PATH`" — once written out by hand and once handed to conan. Read
them next to each other: `nvim/install.sh` is what a package manager saves
you, in twenty lines.

## Why it exists

**It is a teaching env, and it is also its own test.** `tests/` asserts one
thing per stage — the OS, the interpreter and packages, the hand-unpacked
`nvim`, the pinned tool versions, the exported variable. A green run means the
environment really was *built*, not merely configured, which is more than
`--show-config` can tell you.

**It is deliberately ordinary.** Every other example here is a real project's
env that grew over time; this one was written to be read. The conan recipes
are plain `conan.ConanFile`s with no shared base class, the compose file is
ten lines, and nothing is inherited via `import:`.

## Three things worth stealing

**The image contains what denver's own stages need.** A wrapper stage
re-invokes denver *inside* the container, so `container/Dockerfile` installs
`python3` + `python3-yaml` (denver itself), `uv` (the uv stage) and `git`
(the conan stage's recipe exporter) on top of the use case's own apt list.
Getting this wrong is the most common way a first docker-wrapped env fails.

**`python:` must match the image exactly.** In a container denver cannot
install an interpreter, only assert the one that is there — hence
`python: "3.12.3"` (Ubuntu 24.04's) rather than `3.12`.

**A hand-installed tool is two scripts, not one.** `nvim-setup` splits
installing (`cmd:`, an isolated subprocess that prints its progress and is
skipped by `--fast`) from activating (`source:`, folded into the environment
so the `PATH` entry reaches every later stage and the final command). Put the
`export` in the install script and it dies with that subprocess; put the
download in the sourced script and it re-runs under `--fast` and `--dry-run`.
The pin they share lives in `nvim/nvim.env`, so the two can never disagree.

## Files

| File | What it is |
|---|---|
| `denver.toml` | the whole env: five stages |
| `docker-compose.yml`, `container/Dockerfile` | the container the pipeline is relocated into |
| `.devcontainer/` | opens this same container in VS Code -- reuses `docker-compose.yml`, runs `create-env.sh` via `initializeCommand`, then `denver run . --skip docker-base --export-env /tmp/denver.env` via `onCreateCommand` to bring up the remaining stages and hand the built env to every terminal VS Code opens afterwards |
| `create-env.sh` | `hooks: pre-docker-base:` — writes the compose `.env` (host UID/GID/HOME) |
| `setup/install_host_tools.sh` | `scripts: setup:` — one-time host bootstrap, run via `--scripts setup` |
| `requirements.txt` | the venv's packages (`pytest`, plus `conan` for the next stage) |
| `nvim/nvim.env` | the pin — version, url, sha256, install prefix — shared by the two scripts below |
| `nvim/install.sh` | `cmd:` — downloads, verifies and unpacks the release (idempotent) |
| `nvim/activate.sh` | `source:` — the one `export PATH=` that has to survive the stage |
| `conan/conanfile.py` | which tools, in which versions |
| `conan/recipes/<name>/<version>/` | one recipe each: `conanfile.py` + the pinned url/md5 in `conandata.yml` |
| `best-practices.sh` | the `source:`d script exporting `PYTEST_ADDOPTS` |
| `tests/` | the per-stage assertions above |

## Notes

- `create-env.sh` points `CONAN_HOME` at `.conan2/` in this folder and writes
  it into `.env`; the compose file mounts that same path into the container.
  Without it the cache would live inside a `--rm` container and both
  toolchains would be re-downloaded on every run — the difference between
  2m57s and 2.3s on the second run. Both `.conan2/` and the generated `.env`
  are `.gitignore`d.
- `nvim` is unpacked into `${DENVER_ENV_WORKDIR}/nvim/<version>/`, i.e.
  `.denver/denver/` in this folder — denver's own state dir for this env,
  which already ignores itself in git. Per env rather than shared (that is
  what `.conan2/` above is for), inside the env dir so it survives the `--rm`
  container, and version-keyed so bumping `NVIM_VERSION` in `nvim/nvim.env`
  installs beside the old release rather than on top of it. `rm -rf .denver/`
  is the uninstall.
- `onCreateCommand` runs once, in a subprocess whose own exports die with it --
  a fresh VS Code terminal started afterwards would otherwise see none of the
  env denver just built. `--export-env /tmp/denver.env` writes it out as
  `export KEY=VALUE` lines instead, and `container/Dockerfile` patches
  `/etc/bash.bashrc` (at build time, so no runtime sudo) to source that file
  if present -- every interactive bash shell in the container picks it up.
- There is no `image:` key in `denver.toml`, so denver never builds anything
  itself — the compose file owns the tag and compose builds it when missing.
  After editing the `Dockerfile`, run `docker compose build` (or delete the
  image); see [`doc/providers/docker.md`](../../doc/providers/docker.md) for
  the `image:`-managed alternative.

## Next

- [`doc/quickstart/creating-environments.md`](../../doc/quickstart/creating-environments.md) — the step-by-step build-up of this
  env, and why each key is there
- [`../simple-env`](../simple-env) — smaller still: three `custom` stages
- [`../zephyr-devshell-4.3.1`](../zephyr-devshell-4.3.1) — the opposite end:
  five stages, `import:` layering, patches and caches
