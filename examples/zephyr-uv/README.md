# examples/zephyr-uv

**A single `uv` stage: a Python virtualenv, and nothing else.**

Despite the name, there is no Zephyr in this environment — no West workspace,
no SDK, no container. It is named for the role it plays in a Zephyr setup
(see below), but as an example it is simply the minimal `uv` env.

## What it does

`denver run examples/zephyr-uv` creates a [uv](https://docs.astral.sh/uv/)-managed
virtualenv, installs the two pinned packages from `requirements.txt`, and
drops you into a `bash` with that venv on `PATH`:

```console
$ denver run examples/zephyr-uv -- conan --version
Conan version 2.23.0
```

The venv is created on the first run and reused afterwards; the install is
redone only when `requirements.txt` actually changes.

## Why it exists

**It replaces a pair of hand-written scripts.** This env is the declarative
form of the `system_venv.sh` / `system_venv_activate.sh` pattern most
projects grow eventually: one script to create the venv and install into it,
another to activate it, and a rule that everyone has to remember to re-run
the first after a `git pull`. As a `denver.yml`, creating, updating and
activating are the same single command, and "is it up to date?" is denver's
problem rather than yours.

**It is the bootstrap venv.** Look at what it installs: `conan` and `uv`
themselves. That is the point of the name — a Zephyr setup needs a `conan`
binary before its `conan` stage can run, and this env is a standalone way to
get one. It is the same job the `uv`-before-`conan` ordering does inside
[`../raspberry-pico`](../raspberry-pico), extracted into an env of its own.

## Purpose as an example

This is the reference for **"denver does not require Docker, or Conan, or a
toolchain"**. One provider, four lines of configuration, no infrastructure:

```yaml
stages:
- uv

command: bash

uv:
  provider: uv
  requirements:
  - requirements.txt
```

It is also where to look for two easily-missed generic keys:

- **`command:`** sets the default command when none is given after `--`.
  Without it denver would fall back to the `docker:` section's
  `default-cmd:`, then `$SHELL`, then `bash`. There is no docker stage here,
  so the env states what it wants explicitly.
- **`requirements:`** is a *list* of `-r` files, installed together in one
  resolve — not one install per entry.

## Files

| File | What it is |
|---|---|
| `denver.yml` | The whole env: one `uv` stage |
| `requirements.txt` | The pinned packages (`conan`, `uv`) |

## Next

- [`doc/providers/uv.md`](../../doc/providers/uv.md) — every `uv:` key,
  including `python:`, `overrides:`, `skip-if-0:`/`skip-if-1:` and
  `freeze-to:`
- [`../raspberry-pico`](../raspberry-pico) — the next step up: this same
  `uv` stage, plus a `conan` stage that uses what it installed
