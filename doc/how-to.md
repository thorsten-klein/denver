# How to build a new environment, step by step

This is the hands-on companion creating a new environment from scratch.

All terms used here (*environment*, *stage*, *provider*, *wrapper*, ...) are
defined in [`glossary.md`](glossary.md).

## The use case

A team maintains **`howto-example`**, a small firmware repository. A new colleague
who wants to build it is told:

> Use Ubuntu 24.04, as our CI does. Make sure you have installed following
> packages via `apt install`:
>
> - jq
> - net-tools
> - curl
>
> Grab the editor we all use, `neovim` 0.12.4, from its GitHub releases page —
> it ships as a prebuilt tarball, just unpack it somewhere and put it on
> `PATH`.
>
> Download and install the following tools from the internet:
> (Important: use exactly those specified versions):
> - `cmake` 3.31.9
> - ARM toolchain 15.3
>
> Make sure uv is installed (install from  `PyPi`).
> Then create a Python 3.12 virtualenv via `uv` and install `pytest==9.1.1`.
> 
> PS: In our team it is a best practice to export environment variable
> `PYTEST_ADDOPTS="-v -s` so pytest always runs verbose and show live logs.
>
> Finally you should be able to run `pytest`.

Every step of that is one simple action. 
But in total it is a lot of setup effort for the user.

Let's create a `denver.yml` for this so the user can just run:

```bash
denver envs/howto-example
```

## Pre-Requisites for denver

- denver installed (`pip install denver-tool`) — see the top-level
  [`../README.md`](../README.md).
- The docker stage will run on your host. Per documentation this means,
  that only `docker` with Compose plugin must be installed on the host system.
- Analog for the uv stage: `uv` must be available.

## Step 0 — the skeleton

The environment is described by a `denver.yml`, which can live
anywhere in your repo. Let's use `envs/howto-example/`.


```bash
mkdir -p envs/howto-example
touch envs/howto-example/denver.yml
```

We add the first general entries to `envs/howto-example/denver.yml`:

```yaml
version: "1.0"            # the denver.yml schema version -- currently only "1.0" exists
denver-version: ">=1.1.0" # the minimum denver *tool* this file needs
```

Two different questions, so two keys: `version:` pins the schema, and
`denver-version:` pins the tool, so for example a colleague on an older denver
gets a clear message upfront.

Check it loads:

```bash
denver envs/howto-example --show-config
```

`--show-config` resolves the whole config — imports, merges, defaults — and
fully prints it, without running anything. It needs no toolchain, no network and no Docker.
Tip: **You may use it after every step below as it might support understanding.**

## Step 1 — declare the stages

Let's identify the necessary stages. From the use case description we can see that we might need following stages:

| What the colleague was told | Provider | Stage id we will use |
|---|---|---|
| "use Ubuntu 24.04" + `apt install jq net-tools curl` | [`docker`](providers/docker.md) | `docker-with-tools` |
| "create a python 3.12 virtualenv, install `pytest==9.1.1`" | [`uv`](providers/uv.md) | `uv-packages` |
| "unpack the neovim 0.12.4 tarball and put it on `PATH`" | [`custom`](providers/custom.md) | `nvim-by-hand` |
| "`cmake` 3.31.9 and ARM 15.3 — exactly those versions" | [`conan`](providers/conan.md) | `tools-from-internet` |
| "export `PYTEST_ADDOPTS`" | [`custom`](providers/custom.md) | `best-practices` |

**Note**: A stage id is a name we freely choose. `uv-packages` is a `uv`
stage because its section says `provider: uv`, not because of what it is called.

Now Write the `stages:` list first: it *is* the core of the environment. Note that the order is significant here.

```yaml
stages:
- docker-with-tools
- uv-packages
- nvim-by-hand
- tools-from-internet
- best-practices
```

Two things about that list:

- **A stage id is only a label.** Each id needs a matching top-level section
  that declares `provider:` explicitly — nothing is ever guessed from the id,
  which is exactly what lets this one environment run two `custom` stages
  (`nvim-by-hand` and `best-practices`) under different ids.
- **Order is the dependency chain.** `conan` comes after `uv` because the
  conan provider never installs conan itself. As `conan` is a Python package,
  it is installed into the venv by the uv stage. For simplicity we only want one uv stage, where we install all Python packages. This is why it must be before the conan stage.

In the next step we can create each stage.

## Step 2 — the `docker-with-tools` stage

A `docker` stage is a **wrapper**: it builds nothing itself, it *relocates*
the rest of the pipeline into a compose service. denver builds/enters the
container and re-invokes itself inside it with this stage skipped, so the
`uv` and `conan` stages build their work in there rather than on your host.

```yaml
docker-with-tools:      # the id from stages:, not the provider name
  provider: docker
  compose:
    file: docker-compose.yml # required -- never guessed from the directory
    service: dev
```

### The files it needs

`envs/howto-example/docker-compose.yml` — an ordinary compose file. Let's create a simple one

```yaml
services:
  dev:
    image: howto-example:2026-08-09
    build:
      context: container
      dockerfile: Dockerfile
    user: ${HOST_UID:-1000}:${HOST_GID:-1000}
    volumes:
    - $HOME:$HOME
    env_file:
    - .env # generated below by create-env.sh
```

`envs/howto-example/container/Dockerfile` . an ordinary Dockerfile:

```dockerfile
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
      jq net-tools curl \
      ca-certificates git \
      python3 python3-yaml \
    && rm -rf /var/lib/apt/lists/*

RUN export UV_INSTALL_DIR=/usr/local/bin; \
    curl -LsSf https://astral.sh/uv/0.12.3/install.sh | sh
```

**Note**:
- `uv` is installed into the docker container as it is required by the uv stage later (which runs in the docker).
- `python3` and `python3-yaml` are for **denver itself**: a wrapper stage
  re-invokes denver *inside* the container, and denver is a python program
  (PyYAML is its only dependency). `git` is for the conan stage, whose recipe
  exporter shells out to it. The rule of thumb for the image: whatever has to
  exist before denver's own stages can run in there.
- `curl` is in the apt list because the use case asked for it — and the
  `nvim-by-hand` stage later downloads with it, so it would have to be there
  anyway.
- `cmake`, the ARM toolchain and `neovim` are **not** installed via apt. The
  use case requires exact versions, and pinning versions through a distro's
  package manager is a losing game. This is why the two stages below fetch
  them at pinned versions instead — one by hand, one via `conan`.

`envs/howto-example/create-env.sh` — This script will generate `.env` file with user-specific informations.
When we specify it in `denver.yml` as `env-scripts:`, it will run right before build/run (on the host machine). 
Background: Dynamic values a compose file cannot compute itself (host UID/GID, user-specific cache dirs, ...).

```bash
#!/bin/bash -e
SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
HOST_HOME=$HOME
(
    echo HOST_UID=$(id -u)
    echo HOST_GID=$(id -g)
    echo HOST_HOME=$HOST_HOME
    echo HOME=$HOST_HOME
) > $SELF_DIR/.env
```

`envs/howto-example/setup/install_host_tools.sh` — This script will ensure the host requirements for the docker are installed.
Note: This setup script will only run when the user invokes `denver denver envs/howto-example --run setup`.

```bash
#!/bin/bash
command -v docker || sudo apt install docker.io
command -v containerd || sudo apt install containerd
command -v jq || sudo apt install jq

if [ "$(docker compose version | grep 'Docker Compose version')" = "" ]; then
    apt_install docker-compose-v2
fi
```

Your final `denver.yml` should look as follows now:

```yaml
docker-with-tools:
  provider: docker
  compose:
    file: docker-compose.yml # required -- never guessed from the directory
    service: dev
  env-scripts:
  - create-env.sh
  scripts:
    setup:
    - setup/install_host_tools.sh
```

Hint: You may add `.env` (the generated file) to `envs/howto-example/.gitignore`.

### Check it

```bash
denver envs/howto-example --until docker-with-tools -- cat /etc/os-release
```

`--until` only runs the pipeline until the named stage, so this builds and
enters the container and does nothing else. We expect to see Ubuntu 24.04 in the output.

One thing to know before you start editing that `Dockerfile` again: with no
`image:` key in the `denver.yml`, **denver never builds anything itself** —
it leaves the image to the compose file, and compose only builds when the
tag is missing. So a changed `Dockerfile` does *not* rebuild on the next
`denver` run. Run `docker compose build` yourself, or give the docker stage
an `image:` and let denver manage the build-once behaviour
([`providers/docker.md`](providers/docker.md)).

## Step 3 — the `uv-packages` stage

This one already runs *inside* the container, because the `docker` provider
above relocated everything after it in there.

```yaml
uv-packages:
  provider: uv
  # in a docker-wrapped env this is checked against the image's own python
  # rather than installed -- and checked as an exact string, so it has to be
  # the container's full `python3 --version` (Ubuntu 24.04: 3.12.3)
  python: "3.12.3"
  requirements:
  - requirements.txt
```

Create `envs/howto-example/requirements.txt`:

```
# needed by the conan provider
conan==2.31.2

# what the use case asked for
pytest==9.1.1
```

`pytest` is what the use case asked for. `conan` is there because of the
*next* stage: the conan provider never installs conan itself, it expects it
on `PATH`, and this venv is what puts it there. That is the whole reason
`uv-packages` sits before `tools-from-internet` in `stages:`.

The provider drives [`uv`](https://docs.astral.sh/uv/) rather than plain
`pip` — which is why the `Dockerfile` installs `uv`, and why the provider is
named after it. Three keys worth knowing early:

- **`requirements:`** is a *list* of `-r` files resolved together in one
  install, not one install per entry.
- **`overrides:`** pins a conflicting transitive dependency without editing
  (or forking) the requirements file it overrides.
- **`freeze-to:`** writes the fully-resolved `uv pip freeze` output to a
  file — a lockfile a later run can read back via `requirements:`.

Check the environment now with

```bash
denver envs/howto-example --until uv-packages -- pytest --version
```

## Step 4 — the `nvim-by-hand` stage

The next line of the use case is a prebuilt binary: download a tarball,
unpack it, put it on `PATH`. Nothing about that needs a package manager, so
for this simple case let's do it with a `custom` stage — a shell script that
takes care about the provisioning of this tool:

```yaml
nvim-by-hand:
  provider: custom
  # the build step: an isolated subprocess, so it prints what it does and
  # nothing it exports has to survive. Skipped by --fast.
  cmd: bash ${DENVER_ENV_DIR}/nvim/install.sh
  # the activation: sourced, so the PATH entry it exports survives into
  # every later stage and the final command
  source: nvim/activate.sh
```

**Why two scripts and not one.** `cmd:` runs via `bash -c` in a subprocess of
its own — perfect for downloading and unpacking, useless for `export PATH=`,
because that export dies with the subprocess. `source:` is the opposite: the
script is *sourced* into the environment denver is assembling, so whatever it
exports reaches everything after it. Installing and activating are two
different jobs, so they are two scripts. (More on this pair in
[`providers/custom.md`](providers/custom.md); `examples/simple-env` is a
three-stage demo of nothing else.)

**Note**: one sourced script would do the whole job just as well — check,
download, unpack, `export PATH=`, all in `source: install-nvim.sh` and no
`cmd:` at all. It is split into two here to make the two roles visible, and
the split has small advantages of its own: `cmd:`'s output is shown while it
downloads (denver captures a *sourced* script's output, since it has to read
the resulting environment out of it), and `--fast`/`--dry-run` skip a `cmd:`
while they always run a `source:`. If you go with one script, mind that a
sourced script must never call `exit` — it would end denver's own sourcing
shell before it reads the environment back, and the `PATH` entry would be
lost.

Note also `${DENVER_ENV_DIR}` in `cmd:`. A `cmd:` inherits denver's working
directory — wherever the user happened to be — so a relative path would be a
coin flip; `${DENVER_ENV_DIR}` is a built-in denver expands to the directory
holding this `denver.yml` (see
[`architecture.md`](architecture.md#variable-interpolation)). `source:` needs
none of that: it is resolved relative to the `denver.yml` already.

### The files it needs

`envs/howto-example/nvim/nvim.env` — the pin itself, in one place, so the
installing and the activating script can never disagree about which version
they mean or where it lives:

```bash
NVIM_VERSION="0.12.4"
NVIM_URL="https://github.com/neovim/neovim/releases/download/v${NVIM_VERSION}/nvim-linux-x86_64.tar.gz"
NVIM_SHA256="012bf3fcac5ade43914df3f174668bf64d05e049a4f032a388c027b1ebd78628"
NVIM_PREFIX="${DENVER_ENV_WORKDIR}/nvim/${NVIM_VERSION}"
```

`DENVER_ENV_WORKDIR` is the other built-in worth knowing here: denver's own
state directory for this environment (`<env dir>/.denver/<denver.yml stem>/`).
Unpacking into it means the install belongs to this env, is not shared with
any other, survives the `--rm` container (it lives inside the env directory,
which is bind-mounted), and disappears with the checkout. `/usr/local/bin`
would need root and would leak into every other environment on the machine
(if run without docker). The version is part of the path, so bumping
`NVIM_VERSION` installs next to the old release rather than on top of it.

`envs/howto-example/nvim/install.sh` — download, verify, unpack:

```bash
#!/bin/bash -e
SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SELF_DIR/nvim.env"

# runs on every start, so it has to recognise what it already installed
if [ -x "$NVIM_PREFIX/bin/nvim" ]; then
    echo "nvim $NVIM_VERSION already installed: $NVIM_PREFIX"
    exit 0
fi

cd "$DENVER_ENV_DIR"
mkdir -p "$(dirname "$NVIM_PREFIX")"
STAGING=$(mktemp -d "$NVIM_PREFIX.XXXXXX")
trap 'rm -rf "$STAGING"' EXIT

# download and unpack
curl -fLsS -o "$STAGING/nvim.tar.gz" "$NVIM_URL"
echo "$NVIM_SHA256  $STAGING/nvim.tar.gz" | sha256sum -c -
tar -xzf "$STAGING/nvim.tar.gz" -C "$STAGING" --strip-components=1
rm -f "$STAGING/nvim.tar.gz"

# moved into place only once complete: a failed download must not leave a
# half-unpacked tree that the check above would then accept forever
mv "$STAGING" "$NVIM_PREFIX"
trap - EXIT
```

`envs/howto-example/nvim/activate.sh` — the one line that makes it usable:

```bash
#!/bin/bash
SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SELF_DIR/nvim.env"

export PATH="$NVIM_PREFIX/bin:$PATH"
```

### Check it

```bash
denver envs/howto-example --until nvim-by-hand -- nvim --version
```

Expected: `NVIM v0.12.4`. Run it a second time — the download is gone and the
stage costs milliseconds, because of that `if [ -x ... ]`.

**Read the install script again before the next step.** Twenty lines, and
almost none of them are about neovim: a checksum, a staging directory, an
idempotence check, a place to put things — every one of them a decision you
now own, for exactly one tool. The next step is the same job for two more
tools, and it is where that stops scaling.

## Step 5 — the `tools-from-internet` stage

This is the other way to get exact tool versions into an environment: conan.

conan is a package manager, and it does for you what you just did by hand in
`nvim-by-hand`: it downloads a pinned archive, verifies its checksum, unpacks
it into a package of its own and puts that package on `PATH`. No `apt`, no
version drift, same result on every machine.

What changes is how much of that you write yourself. Here it is which tool,
in which version — the download, the checksum check, the "already installed"
shortcut and the cache belong to conan rather than to a script of yours.

```yaml
tools-from-internet:
  provider: conan
  # a *unit*: one conanfile plus every recipe dir it installs from
  conanfiles:
  - path: conan/conanfile.py
    recipe-dirs:
    - conan/recipes
```

### The files it needs

`envs/howto-example/conan/conanfile.py` — what this environment wants, and in which
versions:

```python
from conan import ConanFile


class HowtoExample(ConanFile):
    name = "howto-example"
    version = "1.0"

    def build_requirements(self):
        self.tool_requires("cmake/3.31.9@denver/snapshot")
        self.tool_requires("arm-none-eabi/15.3@denver/snapshot")
```

**Note**: The `@denver/snapshot` half is the user/channel denver's recipes-exporter
stamps onto every recipe it exports. It is the default of `user:`/`channel:`.

Denver expects one directory per recipe, laid out as **`<name>/<version>/`**. That
layout is what names the reference, so `conan/recipes/cmake/3.31.9/` becomes
`cmake/3.31.9`:

Let's create the conan recipes:
```
conan/recipes/
├── cmake/3.31.9/{conanfile.py,conandata.yml}
└── arm-none-eabi/15.3/{conanfile.py,conandata.yml}
```

`conanfile.py` is an ordinary conan recipe. Please refer to official conan documentation.
`conandata.yml` is used to store data — e.g. a url plus the checksum of each file used in the conan recipe.

For example:

```yaml
sources:
  cmake-3.31.9-linux-x86_64.tar.gz:
    md5: 7c102bf491091679895362bb8ae3f4bb
    url: https://github.com/Kitware/CMake/releases/download/v3.31.9/cmake-3.31.9-linux-x86_64.tar.gz
```

```python
from conan import ConanFile
from conan.tools.files import get


class ConanRecipe(ConanFile):
    name = "cmake"
    version = "3.31.9"
    settings = "os", "arch"

    def build(self):
        # the one archive pinned in conandata.yml, unpacked straight into the
        # package folder -- there is nothing to build
        (source,) = self.conan_data["sources"].values()
        get(self, **source, destination=self.package_folder, strip_root=True)

    def package(self):
        pass  # build() already put the unpacked release where it belongs
```


Now check if it works:

```bash
denver envs/howto-example --until tools-from-internet -- cmake --version
denver envs/howto-example --until tools-from-internet -- arm-none-eabi-gcc --version
```

It is expected that both tools are present in their pinned versions.

### By hand or via conan?

Both stages solved the same sentence — "this exact prebuilt tool, on `PATH`" —
so it is worth naming what actually differs:

| | `nvim-by-hand` (`custom`) | `tools-from-internet` (`conan`) |
|---|---|---|
| What you write per tool | ~20 lines of bash | a `conandata.yml` (url + checksum) and a small recipe |
| Download, checksum, unpack | your script's job | conan's |
| "already installed" shortcut | your `if [ -x ... ]` | conan's cache, keyed by reference |
| Cache shared between envs/checkouts | no — one copy per env | yes, one `CONAN_HOME` |
| Dependencies between tools | none possible | conan resolves them |
| Needs `conan` on `PATH` first | no | yes (hence the `uv` stage before it) |

Rules of thumb:

- **One tool, one archive, nothing depends on it** → by hand is honest and
  has no moving parts. `curl | sha256sum -c | tar` is not a thing worth a
  package manager.
- **Several tools, or one you keep re-pinning, or several environments
  wanting the same 800 MB toolchain** → conan. The per-tool cost drops to a
  url and a checksum, and the second environment on the machine pays nothing.

The example keeps both on purpose: `nvim-by-hand` is the escape hatch you
will need eventually for the tool nobody packaged, and it is also the
clearest description of what the conan stage next to it is doing for you.

## Step 6 — the `best-practices` stage

The last line of the use case is solved with a `custom` stage again — the
second one in this environment, and this time with no `cmd:` at all, since
there is nothing to install.

`envs/howto-example/best-practices.sh`:

```bash
#!/bin/bash
export PYTEST_ADDOPTS="-v -s"
```


```yaml
best-practices:
  provider: custom
  source: best-practices.sh
```

The key is `source:`, not `cmd:`, as the script shall be sourced so that the export of environment variables persists for subsequent commands.

**Note**: For a plain constant, by the way, a whole stage is more than you need. Actually you could also do this via docker environment variables (`create-env.sh` or `env:` in `docker-compose.yml`):

```yaml
env:
  PYTEST_ADDOPTS: "-v -s"    # top-level, applied once, before any stage
```

A `custom` stage earns its place as soon as the script is reused (e.g. reused in other environments), or if actual shell logic is necessary
— e.g. in case of conditional settings (`if ...; then ...; fi`) or dynamic path computations.

## Step 7 — what actually starts

Everything so far *builds* the environment. The last line of the use case —
"finally you should be able to run `pytest`" — is about *using* it, and that
is one top-level key:

```yaml
command: bash
```

Command resolution, first hit wins:

1. whatever follows `--` on the command line
2. `command:`
3. the docker stage's `default-cmd:`
4. `$SHELL`, then `bash`

`command: bash` drops you into a shell with everything set up, and `pytest`
is then just something you type. Setting `command: pytest` instead would make
`denver envs/howto-example` *run the tests* and exit — a good choice for an
environment whose whole job is one command (a CI env, a linter env), and
`denver envs/howto-example -- bash` still gets you the shell.

## The finished `denver.yml`

See [`../examples/howto-env`](../examples/howto-env) for all of this as a
real, runnable environment: every file below, complete and working, ready to
be started with `denver examples/howto-env`.

The many manual steps a new colleague used to be told about are now:

```bash
denver envs/howto-example
```

## Prove it, rather than hope

A worthwhile habit for an environment like this: let it carry a test that
checks what each stage promised. [`../examples/howto-env/tests/`](../examples/howto-env/tests/)
does exactly that — one assertion per stage:

```python
def test_docker_stage_gave_us_ubuntu_24_04():
    assert "Ubuntu 24.04" in platform.freedesktop_os_release()["PRETTY_NAME"]


def test_uv_stage_gave_us_python_3_12_and_pytest():
    assert sys.version_info[:2] == (3, 12)
    assert pytest.__version__ == "9.1.1"


def test_custom_stage_put_the_hand_installed_nvim_on_path():
    assert "NVIM v0.12.4" in run(["nvim", "--version"], capture_output=True, text=True).stdout


def test_conan_stage_gave_us_the_pinned_tool_versions():
    assert "3.31.9" in run(["cmake", "--version"], capture_output=True, text=True).stdout
    assert "15.3" in run(["arm-none-eabi-gcc", "--version"], capture_output=True, text=True).stdout


def test_custom_stage_exported_the_team_convention():
    assert os.environ["PYTEST_ADDOPTS"] == "-v -s"
```

```console
$ denver examples/howto-env -q -- pytest examples/howto-env/tests

test_environment.py::test_docker_stage_gave_us_ubuntu_24_04 PASSED
test_environment.py::test_docker_stage_installed_the_apt_packages PASSED
test_environment.py::test_uv_stage_gave_us_python_3_12_and_pytest PASSED
test_environment.py::test_custom_stage_put_the_hand_installed_nvim_on_path PASSED
test_environment.py::test_conan_stage_gave_us_the_pinned_tool_versions PASSED
test_environment.py::test_custom_stage_exported_the_team_convention PASSED
```

## Everyday use

```bash
denver envs/howto-example --run setup          # one-time initial host setup, then never again
denver envs/howto-example                      # open interactive bash in the environment (bash is configured as command:)
denver envs/howto-example -- pytest            # run one command instead of interactive shell

denver envs/howto-example --fast               # activate what is already built; run no build step
denver envs/howto-example --force              # ignore every "nothing changed" shortcut
denver envs/howto-example --skip docker-with-tools      # same stack, directly on the host
denver envs/howto-example -c uv-packages.python=3.13    # override one value, this run
```

The first run is the slow one. After that, each stage fingerprints its own
inputs — the requirement files, the recipe contents — and skips its expensive
step when nothing relevant changed, so a repeat run costs seconds. `--force`
is how you bypass that; `--fast` is the other extreme, skipping the build step
without even checking, which needs one full run to have happened first. A
`custom` stage is the exception on both counts: denver has no idea what an
arbitrary command changes, so `nvim-by-hand` re-runs `install.sh` every time
(which is why that script checks for itself and exits) and under `--fast` its
`cmd:` is skipped entirely, while its `source:` still runs — the `PATH` entry
is not a build step.

**Note on `--skip docker-with-tools`**: worth trying at least once — it builds
the very same stack directly on the host instead of in the container, so "does
this work without Docker?" is one flag away rather than a separate code path.
Whatever the container was providing must then exist on the host itself: for
this env that is `uv` (the uv stage), `curl` (the `nvim-by-hand` stage's
`install.sh`; `tar` and `sha256sum` it also uses are on every Linux host) and
`git` (the conan stage's recipe exporter). `conan` is *not* one of them — it arrives through the uv stage's
venv on the host exactly as it does in the container. Note also that
`create-env.sh` never runs on this path, so `CONAN_HOME` is unset and conan
falls back to `~/.conan2` rather than the env's own cache.
