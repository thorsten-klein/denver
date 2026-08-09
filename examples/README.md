# examples

Examples of working environments, ordered here from smallest to largest.
Each has its own README explaining what it does, why it exists and what it is
meant to teach.

They are real, not illustrative. Every `denver.yml` here has its
`--show-config` output pinned as a golden-file fixture under `tests/golden/`,
so an example that drifted out of sync with the code fails the build. Three of
them — `simple-env`, `raspberry-pico` and `zephyr-devshell-4.3.1` — are
additionally run end-to-end by the `Examples` workflow on `develop`; that last
one exercises `zephyr-devshell` and `zephyr-docker` through its import chain.

## Start here

| Example | Stages | What it is for |
|---|---|---|
| [`simple-env`](simple-env/) | 3 × `custom` | The whole model at minimum size. Also the `cmd:` vs `source:` demo |
| [`zephyr-uv`](zephyr-uv/) | `uv` | A virtualenv and nothing else — proof that no container or toolchain is required |
| [`raspberry-pico`](raspberry-pico/) | `uv` → `conan` | A cross-compilation toolchain **without Docker**. Why stage order matters |
| [`zephyr-docker`](zephyr-docker/) | `docker` | The container layer on its own — a wrapper stage, and `env-scripts:` |
| [`howto-env`](howto-env/) | `docker` → `uv` → `conan` → `custom` | Four providers in one small env, built step by step in [`doc/how-to.md`](../doc/how-to.md) |
| [`zephyr-devshell`](zephyr-devshell/) | *(base — not runnable)* | The shared base: `import:`, layering, `runnable: false` |
| [`zephyr-devshell-4.3.1`](zephyr-devshell-4.3.1/) | 5 stages | A full Zephyr RTOS setup. The patterns that only appear at scale |

## Mapped onto the top-level walkthrough

The [main README](../README.md#what-problem-does-this-solve) builds the
problem up in five steps. Each has a worked example here:

| Step | The problem | Example |
|---|---|---|
| 1 | "first, run these commands" | [`simple-env`](simple-env/) |
| 2 | ...and a virtualenv with the right packages | [`zephyr-uv`](zephyr-uv/) |
| 3 | ...but half our tools aren't Python | [`raspberry-pico`](raspberry-pico/) |
| 4 | ...and it only builds on Ubuntu 22.04 | [`zephyr-docker`](zephyr-docker/) |
| 5 | ...and five repositories need that base | [`zephyr-devshell`](zephyr-devshell/) + [`zephyr-devshell-4.3.1`](zephyr-devshell-4.3.1/) |

**Most projects stop at step 2 or 3.** The Zephyr envs are the extreme case,
included because they prove the model scales — not because a normal
environment needs to look like them.

## Running one

```bash
denver examples/simple-env                 # start it (opens a shell)
denver examples/simple-env -- echo hi      # run one command in it instead
denver examples/simple-env --show-config   # print the merged config and exit
```

`--show-config` is the fastest way to understand an env you didn't write: it
resolves the whole `import:` chain and prints what denver actually ended up
with. It needs no toolchain, no network and no Docker, so it works for every
env here — including `zephyr-devshell`, which cannot be started.

## A note on the three Zephyr environments

They are one setup split across three folders, not three alternatives:

```
zephyr-docker/           "how to build & enter the container"
      ▲ imported by
zephyr-devshell/         the 5-stage pipeline, shared recipes, common env
      ▲ imported by
zephyr-devshell-4.3.1/   ONLY the 4.3.1-specific pins
```

`zephyr-uv` is unrelated despite the name — see its README.
