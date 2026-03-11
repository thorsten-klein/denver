# denver

<img src="https://raw.githubusercontent.com/thorsten-klein/denver/develop/src/assets/logo.svg" alt="logo" width="80%"/>

**Development Environments as code — reproducible, flexible, simple and fast.**

[![CI](https://github.com/thorsten-klein/denver/actions/workflows/ci.yml/badge.svg)](https://github.com/thorsten-klein/denver/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/thorsten-klein/denver/branch/develop/graph/badge.svg)](https://codecov.io/gh/thorsten-klein/denver)
[![PyPI](https://img.shields.io/pypi/v/denver-tool.svg)](https://pypi.org/project/denver-tool/)
[![Python versions](https://img.shields.io/pypi/pyversions/denver-tool.svg)](https://pypi.org/project/denver-tool/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/thorsten-klein/denver/blob/main/LICENSE)

**D**evelopment **Env**ironment Launch**er** — declares dev environments in a
`denver.yml`: reproducible and layerable to fit your project's needs.

## What problem does this solve?

Many projects end up with several different ways to set up their dev environment —
some via `uv`, some via `conan`, some both, some by shelling into Docker.
Keeping that reproducible over the years, and consistent across contributors'
machines, gets hard fast.

But actually, creating a dev environment usually just means to run several tools one
after another — say, drop into a container, then let a package manager pull
in native toolchains, then let another one install the Python packages that
need them — each layer building on top of what the previous one just set
up.

denver makes that sequence even more declarative and simple: a `denver.yml`'s
`stages:` list is exactly that stack of layers. For example:

```yaml
stages:
- docker       # layer 1: drop into a container (or --skip it to stay on the host)
- conan        # layer 2: install additional native toolchains/tools
- pip          # layer 3: create a venv and install Python packages
```

Most projects' dev environments boil down to exactly this stack, which is
why `docker`, `conan` and `pip` ship as built-in providers — plus `zephyr`
for west-based embedded workspaces, and `custom` as an escape hatch for
anything else.

Layers also compose *across* environments: `import:` lets one `denver.yml`
inherit another's entire layer stack as a base and add/override its own
layers on top — e.g. every project-specific env importing a shared
`zephyr-devshell` base instead of redefining the same `docker`/`conan`/`pip`
layers again. Run `denver --help` for every flag, see
[`doc/architecture.md`](https://github.com/thorsten-klein/denver/blob/main/doc/architecture.md) for the full
`stages:`/`import:`/`-c` schema, or look at any `examples/*/denver.yml` for a
working example.

## Documentation

| Document | What's in it |
|---|---|
| [`doc/README.md`](https://github.com/thorsten-klein/denver/blob/main/doc/README.md) | Documentation index — start here |
| [`doc/glossary.md`](https://github.com/thorsten-klein/denver/blob/main/doc/glossary.md) | Every term denver uses, defined once |
| [`doc/architecture.md`](https://github.com/thorsten-klein/denver/blob/main/doc/architecture.md) | The `denver.yml` schema and how the system works |
| [`doc/philosophy.md`](https://github.com/thorsten-klein/denver/blob/main/doc/philosophy.md) | The design principles behind it |
| [`doc/providers/`](https://github.com/thorsten-klein/denver/tree/main/doc/providers/) | One key reference per provider: [pip](https://github.com/thorsten-klein/denver/blob/main/doc/providers/pip.md), [conan](https://github.com/thorsten-klein/denver/blob/main/doc/providers/conan.md), [docker](https://github.com/thorsten-klein/denver/blob/main/doc/providers/docker.md), [zephyr](https://github.com/thorsten-klein/denver/blob/main/doc/providers/zephyr.md), [custom](https://github.com/thorsten-klein/denver/blob/main/doc/providers/custom.md) |
| [`doc/development.md`](https://github.com/thorsten-klein/denver/blob/main/doc/development.md) | Contributing: tests, coverage, adding a provider, releasing |

## Install denver

denver is a small pure-Python package — install it from PyPI or straight from GitHub:

```bash
# with pip
pip install denver-tool
pip install git+https://github.com/thorsten-klein/denver.git

# or with uv
uv tool install denver-tool
uv tool install git+https://github.com/thorsten-klein/denver.git
```

This installs the `denver` script/entry point. 

To hack on denver itself (or pin it to a specific commit/tag/branch),
clone it and install in editable mode instead:

```bash
pip install -e .
```

If you'd rather vendor denver straight into your own monorepo instead of
depending on it as an installed package, add it via
[git-nested](https://github.com/thorsten-klein/git-nested):

```bash
git-nested clone https://github.com/thorsten-klein/denver.git
```

With that approach nothing needs installing — just call `src/denver.py`
(or the vendored copy's equivalent path) directly, as in the quick start
below.

## Getting started with a bundled example environment

This chapter assumes you have never seen denver before. It walks through one
real environment — `examples/zephyr-devshell-4.3.1`, a complete
[Zephyr RTOS](https://zephyrproject.org) 4.3.1 development setup — and
explains what happens, step by step.

Note that every command below works as `denver <env> ...` or `src/denver.py <env> ...`.

### The one command you need

```bash
denver examples/zephyr-devshell-4.3.1
```

That's it. A minute or two later (much less on repeat runs) you are sitting
in a shell where `west`, `cmake`, `ninja`, the Zephyr SDK compilers and all
required Python packages are installed and ready:

```
$ denver examples/zephyr-devshell-4.3.1
... denver builds/enters each layer ...
dev@container ~/workspace> west build -b nrf52840dk/nrf52840 samples/hello_world
dev@container ~/workspace> exit      # back to your normal shell
```

You did not install a compiler. You did not create a virtualenv. You did not
write a bootstrap script. denver did all of it, and it will do exactly the
same on your colleague's machine.

### What is an "environment"?

**An environment is simply a folder that contains a `denver.yml` file.**

That's the whole concept. `denver.yml` is the recipe; denver is the cook that
follows it. You point denver at the folder:

```bash
denver examples/zephyr-devshell-4.3.1
#             ^^^^^^^^^^^^^^^^^^^^^^^^^^ just a folder path
```

`<env>` also accepts a path straight to a YAML file instead of a folder --
handy if a folder holds several variants side by side (e.g.
`denver.debug.yml`, `denver.release.yml`):

```bash
denver examples/zephyr-devshell-4.3.1/denver.debug.yml
```

If you want to know what an environment does, you read its `denver.yml`.

### What is a "stage"?

Setting up a dev environment is really just *running a few tools in the right
order*, where each tool builds on top of the previous one. denver calls each
of those steps a **stage**, and a `denver.yml` lists them in order:

```yaml
stages:
- docker      # 1. get into the right operating system
- conan       # 2. install native tools (compilers, cmake, ninja, ...)
- pip         # 3. install Python packages into a venv
- zephyr      # 4. download the Zephyr source repositories
- pip-zephyr  # 5. install the Python packages Zephyr itself asks for
```

Think of it like getting dressed: underwear before trousers before shoes. 
Each stage prepares something (`PATH` entries, environment variables, files on
disk) that the next stage — and finally *your* shell or command — can use.

The code that knows *how* to run each kind of stage is called a
**provider**. denver ships five of them: `docker`, `conan`, `pip`, `zephyr`
and `custom` (the "run my own script" escape hatch). You don't have to write
provider code; you only configure it via `denver.yml`. Every stage names its
provider explicitly (`provider: pip`), so a stage id is just a label — which
is what lets one env have two `pip` stages, as `pip` and `pip-zephyr` above.

### Walkthrough: what the 5 stages of `zephyr-devshell-4.3.1` do

| # | Stage | In plain words |
|---|-------|----------------|
| 1 | `docker` | Builds a Docker image and drops you **inside a container**, so everyone gets the same Linux, the same system libraries and the same tools — regardless of whether your laptop runs Ubuntu, Fedora or WSL. |
| 2 | `conan` | Uses [Conan](https://conan.io) to fetch **native, non-Python tools** — the Zephyr SDK cross-compilers, `cmake`, `ninja`, `ccache`, `clang`, the J-Link tools — and puts them on `PATH`. These are prebuilt binaries, so nothing is compiled on your machine. |
| 3 | `pip` | Creates a **Python virtualenv** (with [uv](https://docs.astral.sh/uv/), which is a very fast `pip`) and installs the pinned Python packages listed in this env's `requirements.txt` — most importantly `west`, Zephyr's repo-management tool. From here on, `python` and `west` mean *this* venv's versions. |
| 4 | `zephyr` | Runs `west update`, which **clones/updates the many git repositories** that make up a Zephyr workspace (the kernel, HALs, modules, ...) at exactly the revisions pinned for 4.3.1, applies the patches this env carries via `west patches`, fetches binary blobs via `west blobs`, etc... |
| 5 | `pip-zephyr` | The Zephyr modules downloaded in step 4 declare Python dependencies of their own (`west packages pip`). This stage installs **those**, into the same venv from step 3. It has to run *after* step 4, because until then we didn't know what they were. |

At the end, denver hands control over to your shell — `fish`, in this
environment — with everything from steps 1–5 active. When you type `exit`,
you are back on your host machine and nothing was installed on it.

### One-time host setup

A few things cannot be done from inside a container — installing Docker
itself, or the `udev` rules that let you flash a board over USB without
`sudo`. Those live in `scripts: setup:` and are **not** run on every start.
Run them once, explicitly:

```bash
denver examples/zephyr-devshell-4.3.1 --run setup
```

### First run vs. every run after

The first run is slow: the image is built, packages are downloaded, the
Zephyr repos are cloned. **Later runs are fast**, because every stage checks whether
its inputs changed (via checksums and fingerprints) and skips its expensive
work if they didn't. So starting the environment again is normally just a few
seconds.

Two useful flags around this:

```bash
# don't build anything, only activate what already exists (fastest)
denver examples/zephyr-devshell-4.3.1 --fast

# ignore all "nothing changed" shortcuts and redo the expensive work
denver examples/zephyr-devshell-4.3.1 --force
```


### The handful of options you'll actually use

```bash
# run ONE command inside the environment instead of opening a shell.
# everything after '--' is passed through untouched.
denver examples/zephyr-devshell-4.3.1 -- echo 123

# don't use docker; run the same stack directly on your host
denver examples/zephyr-devshell-4.3.1 --skip docker

# stop after a given stage: that stage and every stage before it runs.
# (there is no "run just this one stage" -- a stage practically always
# needs its predecessors: pip needs conan's tools, zephyr needs pip's west)
denver examples/zephyr-devshell-4.3.1 --until pip

# print the final, fully merged configuration and exit -- the best way to
# understand what an environment really does, imports included
denver examples/zephyr-devshell-4.3.1 --show-config

# quieter output (-q keeps stage banners, -qq silences denver completely)
denver examples/zephyr-devshell-4.3.1 -qq -- west --version
```

### Why is `zephyr-devshell-4.3.1/denver.yml` so short?

If you open it, you'll find under 60 lines — half of them comments, and no
`stages:` list or docker config at all. That's because of **`import:`**,
denver's inheritance mechanism:

```yaml
import:
- ../zephyr-devshell     # inherit that env's entire setup as a base
```

The chain looks like this:

```
examples/zephyr-docker/          "how to build & enter the container"
        ▲ imported by
examples/zephyr-devshell/        the shared base: the 5-stage pipeline,
        ▲ imported by        shared conan recipes, common env variables
examples/zephyr-devshell-4.3.1/  ONLY the 4.3.1-specific bits:
                             pinned requirements, conanfile, blob list
```

So a Zephyr 4.4.0 environment would be a new folder whose `denver.yml`
imports the very same base and changes only the pinned versions — no
copy-pasting of the docker/conan/pip setup. (The shared base sets
`runnable: false`, so starting it directly is rejected: it is ingredients,
not a meal.)

### Where to go next

- Curious what a *minimal* environment looks like? `examples/zephyr-uv/` is
  nothing but a Python venv, and `examples/simple-env/` just runs a shell script.
- Want to write your own `denver.yml`? [`doc/architecture.md`](https://github.com/thorsten-klein/denver/blob/main/doc/architecture.md)
  documents every key of the schema; or copy the closest `examples/*/denver.yml`.
- Want details on one stage type's config keys? Each provider has its own
  reference under [`doc/providers/`](https://github.com/thorsten-klein/denver/tree/main/doc/providers/) — e.g.
  [`doc/providers/docker.md`](https://github.com/thorsten-klein/denver/blob/main/doc/providers/docker.md) for every `docker:` key —
  and a terser lookup table in its module docstring next to the code
  (`src/providers/docker.py`).

## Environment variables

denver reads exactly one environment variable of its own:

- **`DENVER_STATE_DIR`** — where denver writes its per-env state (venvs,
  conan caches, `performance.jsonl`, ...) when running from an installed
  package rather than a checkout. Defaults to `~/.denver`. Running from a
  source checkout (or an editable install) instead uses the checkout root,
  matching every example above — this variable only matters once denver is
  installed as a regular (non-editable) package.

Every other flag (`--force`, `--ci`, ...) is set purely by its own CLI flag,
never inherited from a same-named real environment variable — see `denver
--help`.

denver also *exports* a handful of built-in variables into the environment
it builds (`DENVER_ENV_DIR`, `DENVER_ENV_NAME`, ...), usable in `${...}`
interpolation inside a `denver.yml` — see "Variable interpolation" in
[`doc/architecture.md`](https://github.com/thorsten-klein/denver/blob/main/doc/architecture.md).

## Full flag reference

`denver --help` lists every flag; the notes below are for the ones whose
behavior isn't obvious from a one-line description.

- **`-c`/`--config KEY.PATH=VALUE`** overrides a single value in the merged
  `denver.yml` (e.g. `-c pip.python=3.13`); any missing parent section is
  created as an empty mapping. `KEY.PATH+=VALUE` appends to an existing
  list/string/number instead of replacing it (behaves like `=` if the path
  doesn't exist yet). `VALUE` is parsed as YAML, so `"true"`/`"3"`/`"[a,
  b]"` become their real type, not a string. Repeatable; later `-c`s win
  when they target the same path.
- **`-cf`/`--config-file FILE`** overlays a whole YAML file on top of the
  env's `denver.yml`, using the same merge rules as `import:`. Repeatable,
  applied in the order given; `-c` overrides are applied last, on top of
  every `-cf` file.
- **`--until <stage>`** truncates the pipeline: every stage up to and
  including `<stage>` runs, everything after it is dropped — there's no
  "run only this one stage" flag, since a stage practically always needs
  the ones before it. The command (if any) still runs afterwards, in
  whatever partial environment those stages built.
- **`--skip <stage>`** removes individual stages from whatever `--until`
  left; repeatable. Skipping a wrapper stage (`--skip docker`) is how you
  run the stack directly on the host instead of relocating into a
  container.
- **`--run <name>`** runs every (filtered) stage's own `scripts: <name>:`
  list, then exits without running the rest of the pipeline — see "One-time
  host setup" above for an example. `<name>` is open-ended, not a fixed set
  of flags: a project can declare `scripts: migrate:` and run
  `denver <env> --run migrate` without denver itself changing.
- **`-q`/`-qq`** are two quiet levels. `-q` silences info lines, `+ cmd`
  echoes, and build-tool subprocess output, but leaves each stage's own
  banner and "stage finished" summary visible. `-qq` additionally silences
  those too, so only the launched command's own output reaches the
  terminal. Errors are always reported.
- **`--fast`** skips every provider's (re-)build step and only activates
  what a previous full run already built (each provider's own page under
  [`doc/providers/`](https://github.com/thorsten-klein/denver/tree/main/doc/providers/) documents exactly what that means for
  it). Run once without `--fast` first — a provider dies with a clear
  message if what it needs isn't there yet.
- **`--force`** forces a provider to redo expensive work it would otherwise
  skip because nothing looked like it changed (again, see each provider's
  own page for specifics). Like `--ci` below, this is only ever set by
  the flag itself, never inherited from a same-named real environment
  variable.
- **`--ci`** swaps in narrower/faster args a provider judges appropriate
  for a CI runner (currently just zephyr's `west update`, adding a shallow-clone
  strategy on top of whatever `update-args:` already configures).
- **`--version`** prints the installed denver version and exits.

Each stage's runtime is also appended to
`<DENVER_DIR>/.envs/<env>/performance.jsonl` as JSON Lines of Chrome Trace
Event Format events — concatenate them into a `{"traceEvents": [...]}`
document to load at chrome://tracing or https://ui.perfetto.dev.

## Quick start

The same commands as above, as a cheat sheet:

```bash
# run the according setup scripts to install (host) requirements for this env
denver examples/zephyr-devshell-4.3.1 --run setup

# run the according login scripts from this env
denver examples/zephyr-devshell-4.3.1 --run login

# start the env (default command, which is fish)
denver examples/zephyr-devshell-4.3.1

# run a specific command in the development environment instead
denver examples/zephyr-devshell-4.3.1 -- echo 1

# same stack, but on the host instead of in docker
denver examples/zephyr-devshell-4.3.1 --skip docker
```

(If denver is vendored via git-nested, call `src/denver.py` directly instead.)

## Contributing

Bug reports, feature requests and pull requests are welcome — see
[`doc/development.md`](https://github.com/thorsten-klein/denver/blob/main/doc/development.md) for the workflow (`uv run poe all`
runs lint, format, mypy and the test suite; denver keeps 100% coverage).

## License

MIT — see [`LICENSE`](https://github.com/thorsten-klein/denver/blob/main/LICENSE).
