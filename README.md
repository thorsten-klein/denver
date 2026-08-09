# denver

<img src="https://raw.githubusercontent.com/thorsten-klein/denver/develop/src/assets/logo.svg" alt="logo" width="80%"/>

**Development Environments as code — reproducible, flexible, simple and fast.**

[![CI](https://github.com/thorsten-klein/denver/actions/workflows/ci.yml/badge.svg)](https://github.com/thorsten-klein/denver/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/thorsten-klein/denver/branch/develop/graph/badge.svg)](https://codecov.io/gh/thorsten-klein/denver)
[![PyPI](https://img.shields.io/pypi/v/denver-tool.svg)](https://pypi.org/project/denver-tool/)
[![Python versions](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fthorsten-klein%2Fdenver%2Fdevelop%2Fpyproject.toml)](https://pypi.org/project/denver-tool/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/thorsten-klein/denver/blob/develop/LICENSE)

**D**evelopment **Env**ironment Launch**er** — declares dev environments in a
`denver.yml`: reproducible and layerable to fit your project's needs.

## What problem does this solve?

Every project needs *some* setup before you can build it — and how much
varies enormously. So rather than one big answer, here is the problem built
up one step at a time. **Each step below is a complete, working denver
environment.** Stop at whichever one matches your project; there is no
requirement to reach the last one.

An environment is described in the denver-world by a `denver.yml`.

### Step 1 — "first, run these commands"

Almost every project starts here: a README section, or a `setup.sh`, listing
what a newcomer has to do before anything works. The trouble is that a script
in the repo is only a *suggestion*. Nobody re-runs it after a `git pull`,
everyone's shell ends up in a slightly different state, and whatever happens
to be installed on your machine silently covers for the steps you forgot to
write down.

The smallest useful denver environment is that script, declared rather than
documented:

```yaml
# my-project/denver.yml
stages:
- setup

setup:
  provider: custom
  source: setup.sh   # sourced, not executed, so what it exports stays in effect
```

```bash
denver my-project           # apply setup.sh, then drop into a shell that has it
denver my-project -- make   # ...or run a single command in that shell
```

One stage. No Docker, no Conan, no Python packaging. Everybody gets the same
setup, applied the same way, every time — and the shell you land in is
disposable: what the stage exported is gone again once you `exit`.

### Step 2 — "...and a Python virtualenv with the right packages in it"

Now the manual part is `python -m venv`, activate, `pip install -r`, and
remembering to redo it whenever `requirements.txt` changes. A `uv` stage
hands that whole job to denver:

```yaml
stages:
- uv

uv:
  provider: uv
  python: "3.12.3"      # a pinned interpreter, not "whatever python3 happens to be"
  requirements:
  - requirements.txt
```

`denver my-project` now gives you a shell with that venv active: created on
the first run, reused afterwards, and re-installed only when
`requirements.txt` actually changed. `examples/zephyr-uv/` is precisely this
and nothing more.

### Step 3 — "...but half our tools aren't Python at all"

Compilers, `cmake`, `ninja`, a vendor SDK, a flashing tool. This is what
"install these six things first, versions X.Y" READMEs are made of, and no
Python virtualenv can help. If a package manager can fetch them, denver can
drive it as a further stage — `conan` ships built in:

```yaml
stages:
- uv      # provides the 'conan' executable itself, via requirements.txt
- conan   # ...which then fetches the native tools

uv:
  provider: uv
  requirements:
  - requirements.txt

conan:
  provider: conan
  conanfiles:
  - path: conanfile.py
```

The order of `stages:` is the whole point: each stage leaves behind `PATH`
entries, environment variables and files on disk that the *next* stage — and
finally your shell — can use. Here that ordering is load-bearing in a way
worth noticing: `conan` is itself a Python package, so the `uv` stage has to
run first to put the `conan` binary on `PATH` for the stage named after it.
`examples/raspberry-pico/` is exactly this two-stage stack.

### Step 4 — "...and it only builds on Ubuntu 22.04"

Some things cannot be papered over from inside a venv: glibc, system
libraries, the distribution itself. A `docker` stage runs the stages after it
*inside a container*, so the layers above stop depending on which laptop you
are sitting at:

```yaml
stages:
- docker   # everything below this line happens inside the container
- conan
- uv
```

`docker` is a **wrapper**: it installs nothing itself, it relocates the rest
of the pipeline. Which is also why you can drop it whenever you like —
`denver my-project --skip docker` runs the very same `conan`/`uv` stack
straight on your host.

### Step 5 — "...and five repositories need that same base"

Copy-pasting a stack into every repository is how it rots. `import:` lets one
env inherit another's entire stack and restate only what differs:

```yaml
import:
- ../our-shared-base   # its stages, its docker config, its variables

uv:                    # ...with only this project's packages layered on top
  requirements:
  - requirements.txt
```

A new project, or the next SDK version, then becomes a folder with a handful
of lines in it instead of another copy of everything.

### You only pay for the steps you need

Nothing above is mandatory, and denver has no opinion about which tools you
should use. A stage exists only because your `denver.yml` lists it:

| If your project… | …you need |
|---|---|
| runs a setup script, or anything else denver has no provider for | `custom` |
| has Python dependencies | `uv` — a virtualenv managed by [`uv`](https://docs.astral.sh/uv/) |
| has native tools/toolchains to fetch | `conan` |
| needs a specific OS/system libraries | `docker` |
| is a west-based [Zephyr RTOS](https://zephyrproject.org) workspace | `zephyr` |

Those five built-in providers are the *code that knows how to run* a kind of
stage; `custom` is the escape hatch for everything else, and a one-stage
`custom` env is as legitimate as the five-stage one walked through below.

Run `denver --help` for every flag, see
[`doc/architecture.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/architecture.md)
for the full `stages:`/`import:`/`-c` schema, or browse
[`examples/`](https://github.com/thorsten-klein/denver/tree/develop/examples/) —
there is one worked environment per step above, each with a README of its own
explaining what it does and why.

## Documentation

| Document | What's in it |
|---|---|
| [`doc/README.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/README.md) | Documentation index — start here |
| [`doc/glossary.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/glossary.md) | Every term denver uses, defined once |
| [`doc/how-to.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/how-to.md) | Step-by-step: build your own environment, one stage at a time |
| [`doc/architecture.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/architecture.md) | The `denver.yml` schema and how the system works |
| [`doc/philosophy.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/philosophy.md) | The design principles behind it |
| [`doc/providers/`](https://github.com/thorsten-klein/denver/tree/develop/doc/providers/) | One key reference per provider: [uv](https://github.com/thorsten-klein/denver/blob/develop/doc/providers/uv.md), [conan](https://github.com/thorsten-klein/denver/blob/develop/doc/providers/conan.md), [docker](https://github.com/thorsten-klein/denver/blob/develop/doc/providers/docker.md), [zephyr](https://github.com/thorsten-klein/denver/blob/develop/doc/providers/zephyr.md), [custom](https://github.com/thorsten-klein/denver/blob/develop/doc/providers/custom.md) |
| [`doc/development.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/development.md) | Contributing: tests, coverage, adding a provider, releasing |
| [`examples/`](https://github.com/thorsten-klein/denver/tree/develop/examples/) | Six working environments, smallest to largest, each with its own README |

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

On a machine with no Python at all, take the standalone executable attached to
every [release](https://github.com/thorsten-klein/denver/releases) instead — it
bundles denver, its providers and a Python interpreter in one file:

```bash
curl -sSL https://github.com/thorsten-klein/denver/releases/latest/download/denver_x64_Linux.tar.xz | tar -xJf -
./denver --version
```

x86_64 Linux with glibc 2.28 or newer (Ubuntu 20.04+, Debian 10+, Fedora 29+,
RHEL/Alma/Rocky 8+, Arch, Mint 20+); musl-based distros such as Alpine are not
covered. Build it yourself with `scripts/create-python-exe.sh`. Note this only
replaces *denver's* own installation — the tools its providers drive
(see [pre-conditions](#pre-conditions)) still have to be there.

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

## Pre-conditions

denver itself only needs Python — it never installs the tools its providers
drive. This table is a lookup, not a checklist: **only the rows for the
providers your own `denver.yml` actually lists apply to you.** Each of those
expects its tool to already be available wherever that stage runs (on the
host, or inside the container once a `docker` stage relocated into it):

| Provider | Needs |
|---|---|
| `docker` | `docker` with the Compose plugin (v2, `docker compose ...`), daemon reachable for your user |
| `uv` | [`uv`](https://docs.astral.sh/uv/getting-started/installation/) |
| `conan` | `conan` — usually installed by an earlier `uv` stage |
| `zephyr` | `west` — usually installed by an earlier `uv` stage |
| `custom` | whatever your own script calls |

A stage whose tool is missing fails up front with a clear message. Each
provider's page under [`doc/providers/`](https://github.com/thorsten-klein/denver/tree/develop/doc/providers/)
has the details, including how to point at a specific binary via `exe:`.
Anything that must be installed on the *host* (docker itself, `udev` rules,
...) typically lives in an env's `scripts: setup:`, so an env can bring its
own host tools along — run those once with:

```bash
denver <env> --run setup
```

See [One-time host setup](#one-time-host-setup).

## Getting started with a bundled example environment

The chapter above built an environment up from one stage. This one goes the
other way and walks through the biggest bundled example end to end —
`examples/zephyr-devshell-4.3.1`, a complete
[Zephyr RTOS](https://zephyrproject.org) 4.3.1 development setup — to show
what the same mechanism looks like at full size. It is deliberately the
extreme case, not the typical one.

If you would rather start from the small end, read
`examples/zephyr-uv/denver.yml` (a venv, nothing else) or
`examples/simple-env/denver.yml` (a couple of shell snippets) instead — both
are a screenful, and the flags in
[The handful of options you'll actually use](#the-handful-of-options-youll-actually-use)
work identically for them.

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

### The vocabulary, in one place

- An **environment** is a folder containing a `denver.yml`, and you point
  denver at that folder. `denver.yml` is the recipe; denver is the cook that
  follows it. If you want to know what an environment does, you read its
  `denver.yml`.
- A **stage** is one step of the setup, listed in `stages:`. Order matters:
  each stage leaves behind `PATH` entries, environment variables and files
  that the next stage — and finally your shell or command — can use. Think
  of getting dressed: underwear before trousers before shoes.
- A **provider** is the code that knows *how* to run a kind of stage. You
  never write provider code; you configure it from `denver.yml`.

Every stage names its provider explicitly (`provider: uv`), so the stage id
itself is just a label. That is what lets a single env run the same provider
twice — this example has two `uv` stages, `uv` and `uv-zephyr`.

`<env>` also accepts a path straight to a YAML file instead of a folder —
handy when a folder holds several variants side by side (e.g.
`denver.debug.yml`, `denver.release.yml`):

```bash
denver examples/zephyr-devshell-4.3.1/denver.debug.yml
```

### The 5 stages of this example

```yaml
stages:
- docker      # 1. get into the right operating system
- conan       # 2. install native tools (compilers, cmake, ninja, ...)
- uv          # 3. install Python packages into a venv
- zephyr      # 4. download the Zephyr source repositories
- uv-zephyr   # 5. install the Python packages Zephyr itself asks for
```

Steps 1–3 are the same `docker`/`conan`/`uv` layers built up earlier; steps
4 and 5 are what a Zephyr workspace adds on top. In plain words:

| # | Stage | What it does |
|---|-------|----------------|
| 1 | `docker` | Builds a Docker image and drops you **inside a container**, so everyone gets the same Linux, the same system libraries and the same tools — regardless of whether your laptop runs Ubuntu, Fedora or WSL. |
| 2 | `conan` | Uses [Conan](https://conan.io) to fetch **native, non-Python tools** — the Zephyr SDK cross-compilers, `cmake`, `ninja`, `ccache`, `clang`, the J-Link tools — and puts them on `PATH`. These are prebuilt binaries, so nothing is compiled on your machine. |
| 3 | `uv` | Creates a **Python virtualenv** (with [uv](https://docs.astral.sh/uv/), a very fast `pip` replacement) and installs the pinned Python packages listed in this env's `requirements.txt` — most importantly `west`, Zephyr's repo-management tool. From here on, `python` and `west` mean *this* venv's versions. |
| 4 | `zephyr` | Runs `west update`, which **clones/updates the many git repositories** that make up a Zephyr workspace (the kernel, HALs, modules, ...) at exactly the revisions pinned for 4.3.1, applies the patches this env carries via `west patches`, fetches binary blobs via `west blobs`, etc... |
| 5 | `uv-zephyr` | The Zephyr modules downloaded in step 4 declare Python dependencies of their own (`west packages pip`). This stage installs **those**, into the same venv from step 3. It has to run *after* step 4, because until then we didn't know what they were. |

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
# needs its predecessors: uv needs conan's tools, zephyr needs uv's west)
denver examples/zephyr-devshell-4.3.1 --until uv

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
copy-pasting of the docker/conan/uv setup. (The shared base sets
`runnable: false`, so starting it directly is rejected: it is ingredients,
not a meal.)

### Where to go next

- Curious what a *minimal* environment looks like? `examples/zephyr-uv/` is
  nothing but a Python venv, and `examples/simple-env/` just runs a shell script.
- Writing your first own env? Start from
  [Step 1](#step-1--first-run-these-commands) above and add a stage only when
  you hit the problem it solves — most environments never need all five.
- Want to write your own `denver.yml`? [`doc/how-to.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/how-to.md)
  walks through building one from an empty folder, stage by stage;
  [`doc/architecture.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/architecture.md)
  documents every key of the schema; or copy the closest `examples/*/denver.yml`.
- Want details on one stage type's config keys? Each provider has its own
  reference under [`doc/providers/`](https://github.com/thorsten-klein/denver/tree/develop/doc/providers/) — e.g.
  [`doc/providers/docker.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/providers/docker.md) for every `docker:` key —
  and a terser lookup table in its module docstring next to the code
  (`src/providers/docker.py`).

## You wanna set up your own environment?

Then start with **[`doc/how-to.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/how-to.md)**.
It documents how a `denver.yml` is created from scratch for a common use case.

Helpful: The result of this is stored under:
[`examples/howto-env/`](https://github.com/thorsten-klein/denver/tree/develop/examples/howto-env/)

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
[`doc/architecture.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/architecture.md).

## Full flag reference

`denver --help` lists every flag; the notes below are for the ones whose
behavior isn't obvious from a one-line description.

- **`-c`/`--config KEY.PATH=VALUE`** overrides a single value in the merged
  `denver.yml` (e.g. `-c uv.python=3.13`); any missing parent section is
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
  [`doc/providers/`](https://github.com/thorsten-klein/denver/tree/develop/doc/providers/) documents exactly what that means for
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
- **`--version`** prints the running denver's version and exits — derived
  from the checkout's git tags when denver runs from a checkout (script or
  editable install), otherwise from the installed package's metadata. A
  checkout ahead of its last tag reports as a development build of the
  release it is heading for (`1.1.0-17-gabc1234`). A
  `denver.yml` can require a minimum with `denver-version: ">=1.1.0"`, and
  is rejected up front by a denver older than that (see
  [`doc/architecture.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/architecture.md)).

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

## Known limitations

**Every place denver runs needs `import yaml` to work.** denver is a Python
program with exactly one runtime dependency, PyYAML, so installing it from
PyPI covers the host automatically. What it cannot cover is a *wrapped*
environment: a `docker` stage relocates the rest of the stack into the
container and re-invokes denver in there with the image's own bare `python3`
— an interpreter that knows nothing about the install on your host. That
image must supply PyYAML itself:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-yaml \
    && rm -rf /var/lib/apt/lists/*
```

If it doesn't, denver stops with an error naming the interpreter that
failed to `import yaml`.

**Which PyYAML that is, is deliberately not pinned.** The host copy and the
image copy are resolved by two different package managers and will drift
apart in general. denver accepts that: it calls exactly two functions from
the library, `yaml.safe_load` and `yaml.safe_dump`, whose behaviour has been
stable across PyYAML releases for years. So any reasonably recent PyYAML is
fine, and denver imports whatever it finds instead of demanding a particular
version and forcing every image to track it. The declared dependency is a
floor (`pyyaml>=6`), not a pin — and it constrains only the host install
anyway, never the image's.

## Contributing

Bug reports, feature requests and pull requests are welcome — see
[`doc/development.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/development.md) for the workflow (`uv run poe all`
runs lint, format, mypy and the test suite; denver keeps 100% coverage).

## License

MIT — see [`LICENSE`](https://github.com/thorsten-klein/denver/blob/develop/LICENSE).
