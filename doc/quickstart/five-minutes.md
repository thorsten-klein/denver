# Denver in 5 minutes

## Pre-conditions

denver itself only needs Python — it never installs the tools its providers
drive. This table is a lookup, not a checklist: **only the rows for the
providers your own `denver.toml` actually lists apply to you.** Each of those
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
provider's own page under [Providers](../providers/uv.md) has the details,
including how to point at a specific binary via `exe:`. Anything that must be
installed on the *host* (docker itself, `udev` rules, ...) typically lives in
an env's `scripts: setup:`, so an env can bring its own host tools along —
run those once with:

```bash
denver run <env> --scripts setup
```

See [One-time host setup](#one-time-host-setup) below.

## Running `examples/howto-env`

The [Introduction](../introduction/index.md) built an environment up from one
stage. This page goes the other way and walks through a complete, ordinary
bundled example end to end — `examples/howto-env`, five stages over four
providers, written to be read rather than grown organically like a real
project's env. It turns this onboarding note:

> Use Ubuntu 24.04, as our CI does. `apt install jq net-tools curl`. Grab the
> prebuilt `neovim` 0.12.4 tarball from GitHub and put it on `PATH`. Download
> `cmake` 3.31.9 and the ARM toolchain 15.3 — exactly those versions. Install
> `uv`, create a python 3.12 virtualenv, install `pytest==9.1.1`. Export
> `PYTEST_ADDOPTS="-v -s"`, our team convention. Then you can run `pytest`.

into one command.

If you would rather start from the small end, read
`examples/simple-env/denver.toml` (a couple of shell snippets) instead.
For the *biggest* bundled example — a full
[Zephyr RTOS](https://zephyrproject.org) toolchain across five stages — see
[`examples/zephyr-devshell-4.3.1`](https://github.com/thorsten-klein/denver/tree/develop/examples/zephyr-devshell-4.3.1)
instead.

Note that every command below works as `denver run <env> ...` or
`src/denver.py run <env> ...`.

### The one command you need

You can run pytest in the final example environment like this:

```bash
denver run examples/howto-env -- pytest examples/howto-env/tests
```

That's it. A minute or two later (much less on repeat runs) that onboarding
note's every claim has been turned into a passing test:

```console
$ denver run examples/howto-env -- pytest examples/howto-env/tests
test_environment.py::test_docker_stage_gave_us_ubuntu_24_04 PASSED
test_environment.py::test_docker_stage_installed_the_apt_packages PASSED
test_environment.py::test_uv_stage_gave_us_python_3_12_and_pytest PASSED
test_environment.py::test_custom_stage_put_the_hand_installed_nvim_on_path PASSED
test_environment.py::test_conan_stage_gave_us_the_pinned_tool_versions PASSED
test_environment.py::test_custom_stage_exported_the_team_convention PASSED
```

You did not install a compiler. You did not create a virtualenv. You did not
write a bootstrap script. denver did all of it, and it will do exactly the
same on your colleague's machine.

Info: Run `denver run examples/howto-env` with no trailing command and you get a shell instead.

### What just happened

Three words from
[What is a denver environment?](../introduction/index.md#what-is-a-denver-environment),
now with something concrete attached to them:

- The **environment** is the folder `examples/howto-env`, because that is
  where its `denver.toml` lives.
- Each **stage** is one step of that setup, and they ran in the order the
  file lists them — the container first, because everything after it had to
  happen *inside* that container.
- Each **provider** is the code that knew how to run one kind of stage.
  You configured them; you did not write any of them.

One detail worth pinning down here, because the stage list below depends on
it: **a stage id is only a label.** Every stage names its provider
explicitly (`provider: docker`, `provider: uv`, ...), so ids are free to
describe the *problem* rather than the tool — which is what lets this one
environment run two different `custom` stages, `nvim-setup` and
`best-practices`, without them colliding.

`<env>` also accepts a path straight to a config file instead of a folder —
handy when a folder holds several variants side by side (e.g.
`denver.debug.toml`, `denver.release.toml`):

```bash
denver run examples/howto-env/denver.toml -- pytest examples/howto-env/tests
```

### The 5 stages of this example

```toml
stages = [
  "docker-base",     # 1. Ubuntu 24.04 + the apt packages + uv
  "uv-packages",     # 2. the python 3.12 venv
  "nvim-setup",      # 3. one prebuilt release, fetched by hand
  "conan-packages",  # 4. cmake 3.31.9 and the ARM toolchain 15.3, exactly
  "best-practices",  # 5. the team's PYTEST_ADDOPTS convention
]
```

| # | Stage | Provider | What it does |
|---|-------|----------|----------------|
| 1 | `docker-base` | `docker` | Relocates everything below into an **Ubuntu 24.04 container** with the `apt` packages the use case asked for — so the OS and system libraries are the same for everyone. |
| 2 | `uv-packages` | `uv` | Creates a **Python 3.12 virtualenv** (with [uv](https://docs.astral.sh/uv/)) and installs `pytest` — plus `conan`, since the stage below needs it on `PATH`. |
| 3 | `nvim-setup` | `custom` | Downloads, checksums and unpacks **one prebuilt release** (neovim 0.12.4) by hand — the manual way to bring a tool onto `PATH`, so the stage below can be read as what a package manager saves you. |
| 4 | `conan-packages` | `conan` | Uses [Conan](https://conan.io) to fetch `cmake` 3.31.9 and the `arm-none-eabi` 15.3 toolchain, exactly — the same job as the stage above, minus the shell script. |
| 5 | `best-practices` | `custom` | Exports `PYTEST_ADDOPTS="-v -s"`, the team's pytest convention — sourced, not run, so the export survives into the final command. |

At the end, denver hands control over to your shell — plain `bash` in this
environment — with everything from steps 1–5 active. When you type `exit`,
you are back on your host machine and nothing was installed on it.

### One-time host setup

A few things cannot be done from inside a container — e.g. installing Docker
itself, or the `udev` rules that let you flash a board over USB without
`sudo`. Such things may live in `scripts: setup:` and are **not** run on every start.
They must be run explicitly (but necessary only once):

```bash
denver run examples/howto-env --scripts setup
```

### First run vs. every run after

The first run is slow: the docker image is built, huge packages (neovim,
cmake + the ARM compiler) are downloaded. **Later runs are fast**, because
in many cases caches are used. Additionally each provider checks whether
its inputs changed and skip its expensive work if they didn't — for this
example's conan stage specifically, the difference between roughly three
minutes and about two seconds.

Two useful flags around this:

```bash
# don't build anything, only activate what already exists (fastest)
denver run examples/howto-env --fast

# ignore all "nothing changed" shortcuts and redo the expensive work
denver run examples/howto-env --force
```

### The handful of options you'll actually use

```bash
# run ONE command inside the environment instead of opening a shell.
# everything after '--' is passed through untouched.
denver run examples/howto-env -- echo inside

# show what the stages would run, without running any of it
denver run examples/howto-env --dry-run

# stop after a given stage: that stage and every stage before it runs.
# (there is no "run just this one stage" -- a stage practically always
# needs its predecessors: nvim-setup needs uv-packages' venv on PATH)
denver run examples/howto-env --until uv-packages

# print the final, fully merged configuration and exit -- the best way to
# understand what an environment really does, imports included
denver run examples/howto-env --show-config

# quieter output (-q keeps stage banners, -qq silences denver completely)
denver run examples/howto-env -qq -- nvim --version
```

See [CLI Arguments](../cli/arguments.md) for the full flag reference, or
[Shell completion](../cli/completion.md) to tab-complete all of it instead
of memorizing it.

### Sharing one setup across repositories

`howto-env` is deliberately self-contained — nothing here is inherited via
`import:`. For the pattern that shares one base setup across a whole fleet
of projects (Step 5 of the [Introduction](../introduction/index.md)), see
[`examples/zephyr-devshell-4.3.1`](https://github.com/thorsten-klein/denver/tree/develop/examples/zephyr-devshell-4.3.1),
whose `denver.toml` is under 60 lines because it imports its entire pipeline
from a shared base and restates only what's project-specific.

```{note}
**Next:** [Creating environments](creating-environments.md) —
build the environment you just ran, from an empty folder, one stage at a
time. Having seen what it does, you now get to see *why* every key in it is
there.

Two shortcuts, if you'd rather not build one yet: `examples/zephyr-uv/` is a
Python venv and nothing else, `examples/simple-env/` just runs a shell
script — both are a screenful. Or jump straight to the reference: the
[`denver` command](../cli/arguments.md), the
[`denver.toml` schema](../configuration/denver-toml.md), and one page
[per provider](../providers/uv.md).
```
