# examples/howto-env

**The environment built step by step in
[`doc/how-to.md`](../../doc/how-to.md) — four stages, one per provider that
how-to uses.** Read the how-to for the reasoning; this folder is the result,
runnable.

## What it does

It turns this onboarding note:

> Use Ubuntu 24.04, as our CI does. `apt install jq net-tools curl`. Download
> `cmake` 3.31.9 and the ARM toolchain 15.3 — exactly those versions. Install
> `uv`, create a python 3.12 virtualenv, install `pytest==9.1.1`. Export
> `PYTEST_ADDOPTS="-v -s"`, our team convention. Then you can run `pytest`.

into one command:

```console
$ denver examples/howto-env -- pytest examples/howto-env/tests
test_environment.py::test_docker_stage_gave_us_ubuntu_24_04 PASSED
test_environment.py::test_docker_stage_installed_the_apt_packages PASSED
test_environment.py::test_uv_stage_gave_us_python_3_12_and_pytest PASSED
test_environment.py::test_conan_stage_gave_us_the_pinned_tool_versions PASSED
test_environment.py::test_custom_stage_exported_the_team_convention PASSED
```

Four stages, each named after the problem it solves rather than after its
provider:

| Stage id | Provider | What it does |
|---|---|---|
| `docker-with-tools` | `docker` | relocates everything below into an Ubuntu 24.04 container |
| `uv-packages` | `uv` | the python 3.12 venv: `pytest` (and `conan`, for the next stage) |
| `tools-from-internet` | `conan` | downloads `cmake` 3.31.9 and `arm-none-eabi` 15.3, exactly |
| `best-practices` | `custom` | exports `PYTEST_ADDOPTS`, sourced so it survives |

## Why it exists

**It is a teaching env, and it is also its own test.** `tests/` asserts one
thing per stage — the OS, the interpreter and packages, the pinned tool
versions, the exported variable. A green run means the environment really was
*built*, not merely configured, which is more than `--show-config` can tell
you.

**It is deliberately ordinary.** Every other example here is a real project's
env that grew over time; this one was written to be read. The conan recipes
are plain `conan.ConanFile`s with no shared base class, the compose file is
ten lines, and nothing is inherited via `import:`.

## Two things worth stealing

**The image contains what denver's own stages need.** A wrapper stage
re-invokes denver *inside* the container, so `container/Dockerfile` installs
`python3` + `python3-yaml` (denver itself), `uv` (the uv stage) and `git`
(the conan stage's recipe exporter) on top of the use case's own apt list.
Getting this wrong is the most common way a first docker-wrapped env fails.

**`python:` must match the image exactly.** In a container denver cannot
install an interpreter, only assert the one that is there — hence
`python: "3.12.3"` (Ubuntu 24.04's) rather than `3.12`.

## Files

| File | What it is |
|---|---|
| `denver.yml` | the whole env: four stages |
| `docker-compose.yml`, `container/Dockerfile` | the container the pipeline is relocated into |
| `create-env.sh` | `env-scripts:` — writes the compose `.env` (host UID/GID/HOME) |
| `setup/install_host_tools.sh` | `scripts: setup:` — one-time host bootstrap, run via `--run setup` |
| `requirements.txt` | the venv's packages (`pytest`, plus `conan` for the next stage) |
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
- There is no `image:` key in `denver.yml`, so denver never builds anything
  itself — the compose file owns the tag and compose builds it when missing.
  After editing the `Dockerfile`, run `docker compose build` (or delete the
  image); see [`doc/providers/docker.md`](../../doc/providers/docker.md) for
  the `image:`-managed alternative.

## Next

- [`doc/how-to.md`](../../doc/how-to.md) — the step-by-step build-up of this
  env, and why each key is there
- [`../simple-env`](../simple-env) — smaller still: three `custom` stages
- [`../zephyr-devshell-4.3.1`](../zephyr-devshell-4.3.1) — the opposite end:
  five stages, `import:` layering, patches and caches
