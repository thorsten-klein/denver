# denver in 30 minutes

[denver in 5 minutes](05-minutes.md) ran the smallest possible env, three
lines of shell script. This page is the bigger, more realistic follow-up:
**a real firmware project’s setup, built into a `denver.toml` from an empty
folder**, one stage at a time, explaining why each key is there — so by the
end you can write your own instead of copying one.

Build along in your own repo under `envs/firmware-env/`; the finished result is
bundled as
[`examples/firmware-env`](https://github.com/thorsten-klein/denver/tree/develop/examples/firmware-env)
if you want to compare, or skip ahead. (The bundled copy is a `denver.yml` –
denver’s default format – while this walkthrough builds a `denver.toml`;
same schema either way, see
[denver.yml vs. denver.toml](../configuration/denver-toml.md#denver-yml-vs-denver-toml).)

Terms used here (*environment*, *stage*, *provider*, *wrapper*, …) were
introduced in
[What is a denver environment?](../introduction/index.md#what-is-a-denver-environment)
and are each defined precisely in the
[Glossary](../concepts/glossary.md).

## The use case

A team maintains **`firmware-env`**, a small firmware repository. A new colleague
who wants to build it is told:

> Use Ubuntu 24.04, as our CI does. We recommend to use docker.

> Make sure you have installed following
> packages via `apt install`:

> - gcc
> - curl

> Grab the editor we all use, `neovim` 0.12.4, from its GitHub releases page —
> it ships as a prebuilt tarball, just unpack it somewhere and put it on
> `PATH`.

> Download and install the following tools from the internet:
> (Important: use exactly those specified versions):

> - `cmake` 3.31.9
> - `ninja` 1.13.2 (a prebuilt zip, same as neovim)

> Make sure uv is installed (install from  `PyPi`).
> Then create a Python 3.12 virtualenv via `uv` and install `pytest==9.1.1`.

> PS: In our team it is a best practice to export two environment variables –
> `PYTEST_ADDOPTS="-v -s"` so pytest always runs verbose and shows live logs,
> and `CMAKE_GENERATOR="Ninja"` so cmake uses the ninja above instead of make.

> Finally you should be able to run our `pytest` tests and compile our `hello-world` cmake project.

Every step of that is one simple action.
But in total it is a lot of setup effort for the user.

Let’s create a `denver.toml` for this so the user can instead open the environment with:

```bash
denver run envs/firmware-env
```

## What you need before starting

If `docker` is used, every stage after it runs inside the container.
As a result nothing else needs to be installed on the host, but only:

- denver — see [Install denver](../introduction/install.md).
- Docker, with the Compose plugin, on your host — see [Setting up Docker](../providers/docker.md#setting-up-docker).

## Step 0 — the skeleton

The environment is described by a `denver.toml`, which can live
anywhere in your repo. Let’s use `envs/firmware-env/`.

```bash
mkdir -p envs/firmware-env
touch envs/firmware-env/denver.toml
```

We add the first general entries to `envs/firmware-env/denver.toml`:

```toml
version = "1.0"            # the denver.toml schema version -- currently only "1.0" exists
denver-version = ">=1.1.0" # the minimum denver *tool* this file needs
```

`version:` pins the schema, and `denver-version:` pins the tool, so for example a colleague on an older denver gets a clear message upfront.

Check that denver can load it:

```bash
denver run envs/firmware-env --show-config
```

Tip: run `--show-config` after any step below to see how it changed the
resolved config. `--show-config-full` also shows every key denver supports,
even the ones you haven’t set.

## Step 1 — declare the stages

Let’s identify the necessary stages. From the use case description we might need the following stages:

| What the colleague was told                                         | Provider                               | Stage id we will use   |
|---------------------------------------------------------------------|----------------------------------------|------------------------|
| “use Ubuntu 24.04” + `apt install gcc curl`                         | [`docker`](../providers/docker.md)     | `docker-base`          |
| “create a python 3.12 virtualenv, install `pytest==9.1.1`”          | [`uv`](../providers/uv.md)             | `uv-packages`          |
| “unpack the neovim 0.12.4 tarball and put it on `PATH`”             | [`custom`](../providers/custom.md)     | `nvim-setup`           |
| “ninja 1.13.2” – the same job again, but now using bundled provider | [`download`](../providers/download.md) | `ninja-setup`          |
| “cmake 3.31.9”                                                      | [`conan`](../providers/conan.md)       | `conan-packages`       |
| “export `PYTEST_ADDOPTS`, `CMAKE_GENERATOR`”                        | [`custom`](../providers/custom.md)     | `best-practices`       |
> [!NOTE]
> A stage id is a name we freely choose. `uv-packages` is a `uv`
> stage because its section says `provider: uv`, not because of what it is called.

Now write the `stages:` list first: it *is* the core of the environment. Note that the order is significant here.

```toml
stages = [
  "docker-base",
  "uv-packages",
  "nvim-setup",
  "ninja-setup",
  "conan-packages",
  "best-practices",
]
```

Two things about that list:

- **A stage id is only a label.** Each id needs a matching top-level section
  that declares `provider:` explicitly — nothing is ever guessed from the id,
  which is exactly what lets this one environment run two `custom` stages
  (`nvim-setup` and `best-practices`) under different ids.
- **Order is the dependency chain.** `conan` comes after `uv` because the
  conan provider never installs conan itself — conan is a Python package, so
  the uv stage’s venv is what puts it on `PATH`. One `uv` stage installs
  every Python package this env needs, which is why it comes before
  `conan-packages`.

In the next step we create each stage.

## Step 2 — the `docker-base` stage

The `docker-base` stage is a **wrapper**: it uses denver’s `docker`
provider, which *relocates* every stage after it into the container. The
provider builds/enters the container and re-invokes denver inside it, so
`uv-packages` and everything after it do their work in there instead of on
your host.

```toml
[docker-base]      # the id from stages:, not the provider name
provider = "docker"

[docker-base.compose]
file = "docker-compose.yml"  # required -- never guessed from the directory
service = "dev"
```

Best practice: put packages that don’t need an exact version straight into
the docker image. Anything version-pinned, or that changes often, should
come from a package manager stage on top instead.

### The files the docker stage needs

`envs/firmware-env/docker-compose.yml` is an ordinary compose file. Let’s create a simple one

```yaml
services:
  dev:
    image: firmware-env:latest
    build:
      context: container
      dockerfile: Dockerfile
    user: ${HOST_UID:-1000}:${HOST_GID:-1000}
    env_file:
    - .env # generated below by create-env.sh
    environment:
    - HOME=${CONTAINER_HOME}  # the container-only home create-env.sh creates
    volumes:
    # this env's own directory, and denver's own source, each at the same
    # path inside and out -- so paths match, and the docker provider can
    # re-invoke denver from inside the container it just relocated into.
    # ${DENVER_ENV_DIR}/${DENVER_SRC_DIR} are denver's own built-ins, more on
    # those in the next step.
    - ${DENVER_ENV_DIR}:${DENVER_ENV_DIR}
    - ${DENVER_SRC_DIR}:${DENVER_SRC_DIR}
    - ${CONTAINER_HOME}:${CONTAINER_HOME}  # created below by create-env.sh
```

Note the real host `$HOME` is never mounted in at all — only this env’s own
directory, denver’s own source, and a dedicated, empty home for the
container’s non-root user.

`envs/firmware-env/container/Dockerfile` is an ordinary Dockerfile:

```dockerfile
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc libc6-dev make curl \
      ca-certificates git \
      python3 \
    && rm -rf /var/lib/apt/lists/*

RUN export UV_INSTALL_DIR=/usr/local/bin; \
    curl -LsSf https://astral.sh/uv/0.12.3/install.sh | sh
```

> [!NOTE]

> - `uv` is installed into the docker container as it is required by the uv stage later (which runs in the docker).
> - `python3` is for **denver itself**: a wrapper stage re-invokes denver
>   *inside* the container, and denver is a Python program that needs nothing
>   installed for its own sake (`denver.toml` is read with the standard
>   library’s `tomllib`) beyond a `>=3.11` interpreter – Ubuntu 24.04’s
>   `python3` is already 3.12.
> - `git` is for the conan stage, whose recipe
>   exporter shells out to it. The rule of thumb for the image: whatever has to
>   exist before denver’s own stages can run in there.
> - `make`, which `cmake`’s default generator drives, is what later compiles
>   the `hello-world` cmake project
> - `curl` is needed to download files, e.g. in the `nvim-setup` stage later
> - `cmake` and `neovim` are **not** installed via apt. The use case requires
>   exact versions, and pinning versions through a distro’s package manager is
>   a losing game. This is why the two stages below fetch them at pinned
>   versions instead — one `custom`, one via `conan`.

`envs/firmware-env/create-env.sh` — this script generates a `.env` file with
user-specific information used by `docker-compose.yml`, and creates any
mounted folders that need to exist with the right permissions beforehand.
We wire it into `denver.toml` under `[hooks]` as a `pre-docker-base:` entry,
so it always runs right before that stage.

The `create-env.sh` script:

```bash
#!/bin/bash -e
SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# Created here, not left to docker: a bind-mount target that does not exist
# yet is created by docker as root, which the non-root container user could
# then not write to.
CONTAINER_HOME=$SELF_DIR/.denver/container-home
mkdir -p "$CONTAINER_HOME"

(
    echo HOST_UID=$(id -u)
    echo HOST_GID=$(id -g)
    echo CONTAINER_HOME=$CONTAINER_HOME
) > $SELF_DIR/.env
```

This is the only host-specific work left: your user/group id (so the
container writes files you can edit back on the host, not files owned by
root) and a folder for the container’s own `$HOME`, created up front for the
reason in the comment above. Everything else the container needs —
`${DENVER_ENV_DIR}`, `${DENVER_SRC_DIR}`, `${CONAN_HOME}` (Step 6) — is
already a denver built-in or a `denver.toml` `[env]` entry, so it doesn’t
have to be computed by hand here at all.

`envs/firmware-env/setup/install_host_tools.sh` — This script will ensure the host requirements for the docker are installed.

> [!NOTE]
> This setup script will only run when the user invokes `denver run envs/firmware-env --scripts setup`.
```bash
#!/bin/bash
command -v docker || sudo apt install docker.io
command -v containerd || sudo apt install containerd
command -v jq || sudo apt install jq

if [ "$(docker compose version | grep 'Docker Compose version')" = "" ]; then
    apt_install docker-compose-v2
fi
```

Your final `denver.toml` should look as follows now:

```toml
[hooks]
pre-docker-base = ["create-env.sh"]

[docker-base]
provider = "docker"

[docker-base.compose]
file = "docker-compose.yml"
service = "dev"

[docker-base.scripts]
setup = ["setup/install_host_tools.sh"]
```

Hint: In order to ignore this generated machine-specific `.env`, it is added to `envs/firmware-env/.gitignore`.

### Check it — the container

```bash
denver run envs/firmware-env --until docker-base -- cat /etc/os-release
```

`--until` only runs the pipeline until the named stage (including it), so this builds and
enters the container and does nothing else. We expect to see Ubuntu 24.04 in the output.

One thing to know before you start editing that `Dockerfile` again: with no
`image:` key in the `denver.toml`, **denver never builds anything itself** —
docker compose only builds when the tag changes. So a changed `Dockerfile` does *not* rebuild on the next
`denver` run. Run `docker compose build` yourself to rebuild the container, or give the docker stage
an `image:` and let denver manage the build automatically. For more details see
([`providers/docker.md`](../providers/docker.md)).

## Step 3 — the `uv-packages` stage

This one already runs *inside* the container, because the `docker` provider
above relocated everything after it in there.

```toml
[uv-packages]
provider = "uv"
python = "3.12.3"
requirements = ["requirements.txt"]
```

`python:` has to be the container’s exact `python3 --version` (`3.12.3`),
not just `3.12` — denver can’t install an interpreter inside a container, it
can only check the one that’s already there.

Create `envs/firmware-env/requirements.txt`:

```default
# needed by the conan provider
conan==2.31.2

# what the use case asked for
pytest==9.1.1
```

`pytest` is what the use case asked for. `conan` is there for the next
stage: the conan provider never installs conan itself, it just expects it
on `PATH` — and this venv is what puts it there. That is the whole reason
`uv-packages` sits before `conan-packages` in `stages:`.

Check the environment now, for example by running `pytest --version`:

```bash
denver run envs/firmware-env --until uv-packages -- pytest --version
```

## Step 4 — the `nvim-setup` stage

The next line of the use case is a prebuilt binary: download a tarball,
unpack it, put it on `PATH`. Nothing about that needs a package manager, so
for this simple case let’s do it with a `custom` stage:

```toml
[nvim-setup]
provider = "custom"
cmd = "bash ${DENVER_ENV_DIR}/nvim/install.sh"
source = "nvim/activate.sh"
```

**Why two scripts and not one.** `cmd:` runs via `bash -c` in a subprocess of
its own — perfect for downloading and unpacking, useless for `export PATH=`,
because that export dies with the subprocess. `source:` is the solution here: the
script is *sourced* into the environment denver is assembling, so whatever it
exports reaches everything after it. Installing and activating are two
different jobs, so they are two scripts. (More on this pair in
[`providers/custom.md`](../providers/custom.md))

> [!NOTE]
> one sourced script would do the whole job just as well — check,
> download, unpack, `export PATH=`, all in `source: install-nvim.sh` and no
> `cmd:` at all. It is split into two here to make the two roles visible, and
> the split has small advantages of its own: `--fast`/`--dry-run` skips a `cmd:`
> while they always run a `source:`. If you go with one script, mind that a
> sourced script must never call `exit` — it would end denver’s own sourcing
> shell before it reads the environment back, and the `PATH` entry would be
> lost.

Note also `${DENVER_ENV_DIR}` in `cmd:`. A `cmd:` inherits denver’s working
directory — wherever the user happened to be — so a relative path would be a
coin flip; `${DENVER_ENV_DIR}` is a built-in denver expands to the directory
holding this `denver.toml` (see
[Configuration](../configuration/denver-toml.md#variable-interpolation)). `source:` needs
none of that: it is resolved relative to the `denver.toml` already.

### The files the nvim stage needs

`envs/firmware-env/nvim/nvim.env` — the pin itself, in one place, so the
installing and the activating script can never disagree about which version
they mean or where it lives:

```bash
NVIM_VERSION="0.12.4"
NVIM_URL="https://github.com/neovim/neovim/releases/download/v${NVIM_VERSION}/nvim-linux-x86_64.tar.gz"
NVIM_SHA256="012bf3fcac5ade43914df3f174668bf64d05e049a4f032a388c027b1ebd78628"
NVIM_PREFIX="${DENVER_ENV_WORKDIR}/nvim/${NVIM_VERSION}"
```

`DENVER_ENV_WORKDIR` is the other built-in worth knowing here: denver’s own
state directory for this environment (`<env dir>/.denver/<config file stem>/`).
Unpacking into it means the install belongs to this env, is not shared with
any other, and survives the `--rm` container — it lives inside the env
directory, which `docker-compose.yml` bind-mounts in (Step 2). The version
is part of the path, so bumping `NVIM_VERSION` installs next to the old
release rather than on top of it.

`envs/firmware-env/nvim/install.sh` — download, verify, unpack:

```bash
#!/bin/bash -e
SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SELF_DIR/nvim.env"

# runs on every start, so it can exit early if already installed
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

`envs/firmware-env/nvim/activate.sh` — the one line that makes it usable:

```bash
#!/bin/bash
SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SELF_DIR/nvim.env"

export PATH="$NVIM_PREFIX/bin:$PATH"
```

### Check it — nvim on PATH

Check the environment now, for example by running `nvim --version`:

```bash
denver run envs/firmware-env --until nvim-setup -- nvim --version
```

After some short time for downloading and unpacking, we expect:
`NVIM v0.12.4`.

Run the command a second time — the download is gone and the output comes in
milliseconds as the installation is skipped as `nvim` is already present.

## Step 5 — the `ninja-setup` stage

Look at what that install script actually does: check whether the tool is
already there, download it, verify the checksum, unpack it, put it on
`PATH`. Almost none of that is about neovim. The `download` provider does
those five things for you, so the next prebuilt tool — ninja 1.13.2 — needs
no script at all. Instead we use `denver`’s bundled `download` provider:

```toml
[ninja-setup]
provider = "download"

[[ninja-setup.packages]]
name = "ninja"
url = "https://github.com/ninja-build/ninja/releases/download/v1.13.2/ninja-linux.zip"
sha256sum = "5749cbc4e668273514150a80e387a957f933c6ed3f5f11e03fb30955e2bbead6"
env-prepend = { PATH = "." }
```

One `[[ninja-setup.packages]]` table per tool. Three keys carry the whole
stage:

- **`url:`** — what to download. The file name at the end of it (here
  `ninja-linux.zip`) is also the name the archive is stored under.
- **`sha256sum:`** — what the downloaded file must hash to. Leave it out and
  you trust whatever that url serves today.
- **`env-prepend:`** — what the unpacked tool adds to the environment.
  Values are paths *inside* the package: `"."` is its root, `"bin"` its
  `bin/` folder. This zip holds a single `ninja` executable at the root, so
  the root is what goes on `PATH`.

Check it:

```bash
denver run envs/firmware-env --until ninja-setup -- ninja --version
```

Expect `1.13.2`, and a second run in milliseconds — everything is reused
from the previous run. The careful parts of the hand-written
script are covered too: an interrupted download or unpack is never mistaken
for a finished one, and a file whose checksum no longer matches is fetched
again.

The full documentation of the `download` provider you can find in
[`providers/download.md`](../providers/download.md).

## Step 6 — the `conan-packages` stage

This is the third way to get exact tool versions into an environment: `conan`.

conan is a package manager, and it does what the two stages before it did:
it downloads a pinned archive, verifies its checksum, unpacks
it into a package of its own and puts that package on `PATH`. No `apt`, no
version drift, same result on every machine.

What changes is how much you write yourself: you can write your own recipe
(a `conanfile.py`, as we do here), or use an existing package from a remote
like conan-center. See the
[official conan documentation](https://docs.conan.io/) for the full
picture.

```toml
[conan-packages]
provider = "conan"
conanfile = "conan/conanfile.py"

[[conan-packages.recipes]]
dirs = ["conan/recipes"]
```

### The files the conan stage needs

denver expects one directory per recipe, laid out as **`<name>/<version>/`**. That
layout is what names the reference, so `conan/recipes/cmake/3.31.9/` becomes
`cmake/3.31.9`:

Let’s create the conan recipe:

```default
conan/recipes/
└── cmake/3.31.9/{conanfile.py,conandata.yml}
```

`conanfile.py` is an ordinary conan recipe. Please refer to the official conan documentation.
`conandata.yml` is used to store data — e.g. a url plus the checksum of each file used in the conan recipe.
Its name and YAML format are Conan’s own, not denver’s — unlike `denver.toml` above, this one always
stays `conandata.yml`.

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

Now let’s use this conan package in `envs/firmware-env/conan/conanfile.py`:

```python
from conan import ConanFile


class FirmwareEnv(ConanFile):
    name = "firmware-env"
    version = "1.0"

    def build_requirements(self):
        self.tool_requires("cmake/3.31.9@denver/snapshot")
```

> [!NOTE]
> The `@denver/snapshot` half is the user/channel denver’s recipes-exporter
> stamps onto every recipe it exports. It is the default of `user:`/`channel:`.

Now check that it works, by running `cmake --version`:

```bash
denver run envs/firmware-env --until conan-packages -- cmake --version
```

It is expected that the tool is present in its pinned version.

### Keeping conan’s cache

conan’s own cache lives wherever `$CONAN_HOME` points, downloaded archives
included — worth keeping around rather than losing it every time the `--rm`
container is thrown away and both toolchains get re-downloaded from
scratch. `denver.toml`’s top-level `[env]` table (more on it in the next
step) is the simplest way to pin it:

```toml
[env]
CONAN_HOME = "${DENVER_ENV_DIR}/.conan2"
```

`${DENVER_ENV_DIR}` — one of denver’s own built-ins, see
[Configuration](../configuration/denver-toml.md#variable-interpolation) —
keeps the cache inside this env’s own directory rather than somewhere
shared. Since that directory is already bind-mounted into the container
(Step 2), the conan cache persists too, with no extra mount needed.

### By hand, `download` or conan?

Three stages solved the same sentence — “this exact prebuilt tool, on
`PATH`” — with a different amount of code each. Rule of thumb: `download`
for a plain release archive (the normal case); by hand only when the install
really needs shell logic of its own; conan once you have several tools, want
the cache shared across environments, or need one tool to depend on
another.

## Step 7 — the `best-practices` stage

The last line of the use case — applying the team’s best practice — is
solved with a `custom` stage again. This time with no `cmd:`, only
`source:`, since nothing has to run, only environment variables are set.

```toml
[best-practices]
provider = "custom"
source = "best-practices.sh"
```

This is the sourced script `envs/firmware-env/best-practices.sh`:

```bash
#!/bin/bash
export PYTEST_ADDOPTS="-v -s"
export CMAKE_GENERATOR="Ninja"
```

The key is `source:`, not `cmd:` — sourcing is what makes the exported variables survive into later commands.
`CMAKE_GENERATOR="Ninja"` is what makes the cmake of Step 6 build with the
ninja of Step 5, instead of falling back to `make`.

> [!NOTE]
> For a plain constant, by the way, a whole stage is more than you
> need — alternatively the `[env]` table (already used for `CONAN_HOME` in Step 6)
> would work as well:

> ```toml
> [env]
> PYTEST_ADDOPTS = "-v -s"
> CMAKE_GENERATOR = "Ninja"
> ```

A `custom` stage earns its place as soon as shell logic is necessary — e.g. in case of
conditions (`if ...; then ...; fi`) or if paths are computed dynamically.

## Step 8 — the default command

Everything so far *builds* the environment. But when we run denver, we
usually want a specific command by default — here, an interactive shell:

```toml
command = "bash"
```

This is only the *default*. `denver run envs/firmware-env -- <command>` always
runs `<command>` instead. With no `-- <command>` given, denver drops you
into a shell with everything set up, and you can invoke `pytest` or `cmake`
yourself.

## The finished `denver.toml`

See [`examples/firmware-env`](https://github.com/thorsten-klein/denver/tree/develop/examples/firmware-env) for all of this as a
real, runnable environment: every file below, complete and working, ready to
be started with `denver run examples/firmware-env`.

The many manual steps a new colleague used to be told about are now:

```bash
denver run examples/firmware-env
```

## Prove it, rather than hope

A worthwhile habit for an environment like this: let it carry a test that
checks what each stage promised. [`examples/firmware-env/tests/`](https://github.com/thorsten-klein/denver/tree/develop/examples/firmware-env/tests)
does exactly that.

So let’s run those tests:

```bash
denver run examples/firmware-env -- pytest examples/firmware-env/tests
```

All of them should pass — a green run means the environment was really
*built*, not just configured.

## Everyday use

```bash
denver run envs/firmware-env                            # open interactive bash in the environment (bash is configured as command:)
denver run envs/firmware-env -- nvim                    # open nvim editor in the environment

denver run envs/firmware-env --fast                     # activate what is already built; run no build step
denver run envs/firmware-env --force                    # ignore every "nothing changed" shortcut
denver run envs/firmware-env --skip docker-base          # same stack, directly on the host
denver run envs/firmware-env -c uv-packages.python=3.13  # override one specific config value (for this run)

denver run envs/firmware-env --clean                    # run its 'scripts: clean:', then throw the built state away
denver clean envs/firmware-env                          # remove every directory denver keeps for it and its imports
```

> [!NOTE]
> **On `--skip docker-base`:** worth trying at least once — it builds
> the very same stack directly on the host instead of in the container, so “does
> this work without Docker?” is one flag away rather than a separate code path.
> Whatever the container was providing must then exist on the host itself —
> for this env that’s `uv`, `git`, `curl`, and a C toolchain (`gcc`,
> `libc6-dev`, `make`).
> [!NOTE]
> **Next:** [Examples](examples.md) — the other bundled environments, from a
> three-line one up to a full Zephyr RTOS setup, so you can find the one
> closest to your own project and start from it.

> From here on the documentation is reference rather than narrative: the
> [`denver` command](../cli/arguments.md), the full
> [`denver.toml` schema](../configuration/denver-toml.md), and one page
> [per provider](../providers/uv.md).
