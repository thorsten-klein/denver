# examples/zephyr-devshell

**The shared base for every `zephyr-devshell-<version>` environment. Not
runnable — it is ingredients, not a meal.**

```console
$ denver examples/zephyr-devshell
ERROR: env 'zephyr-devshell' sets 'runnable: false' -- it's meant to be imported, not started directly.
```

That is intentional. `runnable: false` marks a `denver.yml` as a base that
only exists to be `import:`ed. To actually start something, use
[`../zephyr-devshell-4.3.1`](../zephyr-devshell-4.3.1).

## What it does

It defines the five-stage pipeline a Zephyr workspace needs, and fills in
everything that does **not** depend on which Zephyr version you want:

```yaml
stages:
- docker      # 1. get into the right operating system
- conan       # 2. native tools: SDK, cmake, ninja, clang, ccache, J-Link
- uv          # 3. a venv, with west in it
- zephyr      # 4. west update: clone the workspace repositories
- uv-zephyr   # 5. the Python deps those repositories declare
```

Concretely it contributes: the stage list and its ordering, the `docker:`
config (imported wholesale from [`../zephyr-docker`](../zephyr-docker)), the
Conan base classes, the shared tool recipes, the cross-cutting environment
variables, and the J-Link udev setup script.

What it deliberately does *not* contribute: any pinned version. `uv:`,
`zephyr:` and `uv-zephyr:` appear here declaring nothing but
`provider:`. The version env fills them in.

## Why it exists

**Because copy-paste is how a stack rots.** Without a base, a Zephyr 4.4.0
environment would be a copy of the 4.3.1 one with a few numbers changed —
and from then on every fix to the container, every new shared recipe, every
environment variable has to be applied in N places, and won't be. With it, a
new version env is a folder containing pinned requirements, a conanfile and a
blob list; everything else is inherited.

**Because "which layer does this belong to?" should have an answer.** The
split across three envs is the interesting part of this example:

```
zephyr-docker/           "how to build & enter the container"
      ▲ imported by
zephyr-devshell/         the pipeline, shared recipes, common environment
      ▲ imported by
zephyr-devshell-4.3.1/   only what 4.3.1 pins
```

Anything version-independent moves down; anything version-specific stays up.

## Purpose as an example

This is the reference for **`import:` and the layering rules**, and for
several things denver refuses to guess.

**Section-level import.** `import:` works on a single stage's section, not
just at the top level — the whole `docker:` config is inherited from another
env:

```yaml
docker:
  import:
  - ../zephyr-docker
```

**`runnable: false`, and why it is not inherited.** Marking a base
non-startable would be useless if the property flowed downhill: every env
importing it would be non-startable too. So `runnable:` is deliberately
excluded from inheritance — an importer is runnable unless it says otherwise.

**Hooks are listed, never discovered.** A `hooks/env.sh` sitting next to
`denver.yml` does nothing on its own; it runs because this file names it:

```yaml
hooks:
  env:
  - hooks/env.sh
```

`hooks: <name>:` is a *list*, and every layer of the import chain contributes
its own, base first — so a derived env declaring an `env` hook **adds** to
this one rather than replacing it. (Which is also how you add a personal,
uncommitted `hooks/env.user.sh`.) The hook itself is sourced once before any
stage, and can already use denver built-ins like `WEST_TOPDIR` and
`DENVER_ENV_WORKDIR`; it sets up ccache, ctcache, CodeChecker, CMake's
`find_package(Zephyr)` path and west's system config.

**Recipes without a conanfile.** This base ships a directory of shared Conan
recipes (`cmake`, `ninja`, `clang`, `ccache`, `doxygen`, `protoc`, `jlink`,
`systemview`, `zephyr-sdk`) but declares no `conanfiles:` unit for them, and
that is not an oversight: `recipe-dirs:` live *inside* a unit — a conanfile
plus the recipes it installs from — and a base with no conanfile has no unit
to put them in. Each version env lists
`../zephyr-devshell/conan/recipes` in its own unit instead. `base-classes:`,
by contrast, is env-wide and so is declared once here.

**Two stages, one venv.** `uv-zephyr` has no `venv:` of its own, so it
shares the `uv` stage's. That is required, not tidiness: `west`'s extension
commands are imported into the same running `west` process, so what stage 5
installs has to be importable by the interpreter stage 3 set up.

## Files

| Path | What it is |
|---|---|
| `denver.yml` | The pipeline, the shared config, `runnable: false` |
| `hooks/env.sh` | Env-wide exports, sourced before any stage |
| `conan/base_classes/` | Shared conanfile base classes (`base-classes:`) |
| `conan/recipes/` | Shared tool recipes, claimed by importing envs |
| `configs/west_base_config` | West system config (via `WEST_CONFIG_SYSTEM`) |
| `configs/99-jlink.rules` | The udev rule installed by `--run setup` |
| `setup/install_jlink_udev_rules.sh` | Host setup for the `zephyr` stage |

## Next

- [`../zephyr-devshell-4.3.1`](../zephyr-devshell-4.3.1) — what a version env
  built on this actually has to say
- [`doc/configuration/denver-yml.md`](../../doc/configuration/denver-yml.md) — the `import:` chain,
  merge rules, and conflicting-value resolution with `!`
