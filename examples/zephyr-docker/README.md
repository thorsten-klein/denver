# examples/zephyr-docker

**A single `docker` stage: build a Compose service and run everything inside
it. The container half of the Zephyr setup, kept in an env of its own.**

## What it does

`denver examples/zephyr-docker` builds the `dev` service from
`docker-compose.yml` and drops you into a `fish` shell inside the resulting
container — as your own host UID/GID, with your workspace, home directory and
caches mounted at *the same absolute paths* they have outside.

That last detail is the point of most of the configuration here. If
`/home/you/project` is `/home/you/project` on both sides, then absolute paths
in build outputs, compile databases, IDE configuration and ccache entries stay
valid whether they were produced inside or outside the container.

## Why it exists

**Because some problems can't be fixed from inside a virtualenv.** A `pip` or
`conan` stage can pin a package or a toolchain; neither can give you a
specific glibc, a system library, or a distribution that a vendor tool refuses
to run without. That is the whole job of a `docker` stage.

**Because it is a wrapper, not an installer.** `docker` doesn't install a
toolchain, a compiler or a package — it *relocates the rest of the pipeline*.
Everything listed after `docker` in a `stages:` list runs inside the
container. Which is also why it can be removed: `--skip docker` runs the very
same stack directly on the host, and no other stage needs to know.

**Because it is separable.** This env exists apart from
[`../zephyr-devshell`](../zephyr-devshell) so that "how do we build and enter
the container" is one reusable answer, imported wherever it's needed rather
than copied. The devshell inherits this whole section with a
*section-level* import:

```yaml
# ../zephyr-devshell/denver.yml
docker:
  import:
  - ../zephyr-docker      # inherit this env's docker: config, default-cmd included
```

## Purpose as an example

**1. `env-scripts:` — computing what a static file can't express.** Compose
needs an `.env` file, and half its contents can only be known at runtime: the
host UID/GID, the workspace root, cache locations, git credentials, whether
this is CI. So `denver.yml` names a script instead of values:

```yaml
env-scripts:
- create-env.sh     # denver runs it (no arguments) before compose build/run
```

`create-env.sh` renders the whole env-file itself — including walking up the
directory tree to find the outermost `.git` as the workspace root, and asking
`docker compose config` for the image tag so `docker-compose.yml` stays the
single source of truth for it.

**2. `scripts: setup:` — the things a container genuinely cannot do.** You
cannot install Docker from inside Docker, and udev rules belong to the host
kernel. Those live in a named script list that is **not** run on every start:

```bash
denver examples/zephyr-docker --run setup    # once per machine
```

`--run <name>` is open-ended, not a fixed set of flags — an env can declare
`scripts: migrate:` and get `--run migrate` without denver changing.

**3. What a real `docker-compose.yml` ends up carrying.** It is heavily
commented and worth skimming as a catalogue of the problems that show up once
a container is a *development* environment rather than a deployment target:
caches kept outside the container lifecycle, per-tool state (IDE plugins,
accepted EULAs, shell history, AI-assistant credentials) surviving a rebuild,
`/dev` passthrough for flashing hardware, and the git-credentials file that
has to be *copied* in because git rewrites it in place rather than editing it
— which a bind mount cannot satisfy.

## Files

| Path | What it is |
|---|---|
| `denver.yml` | One `docker` stage |
| `docker-compose.yml` | The `dev` service: image, mounts, user, devices |
| `create-env.sh` | Renders the `.env` Compose reads (`env-scripts:`) |
| `container/Dockerfile` | The image itself |
| `container/fixuid/` | Maps the container user onto your host UID/GID |
| `configs/` | Shell/git config mounted into the container |
| `setup/install_host_tools.sh` | Host bootstrap, run via `--run setup` |

## Note

This env is runnable on its own, but it is not part of the `Examples` CI
matrix — building the image is slow and Docker-heavy. `simple-env`,
`raspberry-pico` and `zephyr-devshell-4.3.1` are the three that run there;
the last of those exercises this configuration transitively through its
import chain.

## Next

- [`doc/providers/docker.md`](../../doc/providers/docker.md) — every `docker:`
  key, `env-scripts:`, and how relocation works
- [`doc/architecture.md`](../../doc/architecture.md) — the wrapper/relocation
  model in general
- [`../zephyr-devshell`](../zephyr-devshell) — the env that imports this one
