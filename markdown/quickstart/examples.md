# Examples

You can find some working denver environments under
[`examples/`](https://github.com/thorsten-klein/denver/tree/develop/examples/)
in the repository. Each has its own README
explaining what it does, why it exists and what it is meant to teach. They
are not illustrative snippets — every `examples/*/denver.toml` has its
`--show-config-full` output pinned as a golden-file fixture under `tests/golden/`,
so an example that drifted out of sync with the code fails the build.

## Overview

| Example                                                                                                         | Stages                                                       | What it is for                                                                                                                                                                                                               |
|-----------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`simple-env`](https://github.com/thorsten-klein/denver/tree/develop/examples/simple-env)                       | 3 × `custom`                                                 | The whole model at minimum size. Also the `cmd:` vs `source:` demo                                                                                                                                                           |
| [`zephyr-uv`](https://github.com/thorsten-klein/denver/tree/develop/examples/zephyr-uv)                         | `uv`                                                         | A virtualenv and nothing else — proof that no container or toolchain is required                                                                                                                                             |
| [`raspberry-pico`](https://github.com/thorsten-klein/denver/tree/develop/examples/raspberry-pico)               | `custom` → `download` → `git` → `download`                   | A cross-compilation toolchain **without Docker, and without Conan**. `git`/`download` instead of hand-vendored archives; `unpack-cmd:` that builds, not just unpacks                                                         |
| [`zephyr-docker`](https://github.com/thorsten-klein/denver/tree/develop/examples/zephyr-docker)                 | `docker`                                                     | The container layer on its own — a wrapper stage, and a `hooks: pre-docker:` script                                                                                                                                          |
| [`firmware-env`](https://github.com/thorsten-klein/denver/tree/develop/examples/firmware-env)                   | `docker` → `uv` → `custom` → `download` → `conan` → `custom` | Five providers in one small env — run end to end and built from scratch in [denver in 30 minutes](30-minutes.md). Also: the same job (a pinned prebuilt binary on `PATH`) done three ways — by hand, by `download`, by conan |
| [`zephyr-devshell`](https://github.com/thorsten-klein/denver/tree/develop/examples/zephyr-devshell)             | *(base — not runnable)*                                      | The shared base: `import:`, layering, `runnable: false`                                                                                                                                                                      |
| [`zephyr-devshell-4.3.1`](https://github.com/thorsten-klein/denver/tree/develop/examples/zephyr-devshell-4.3.1) | `docker` → `uv` → `conan` → `zephyr` → `custom`              | A full Zephyr RTOS setup — the extreme case, and what `import:` layering looks like at full size                                                                                                                             |
| [`doc-env`](https://github.com/thorsten-klein/denver/tree/develop/examples/doc-env)                             | `uv` → `custom`                                              | Builds *this documentation* with Sphinx — denver used on itself                                                                                                                                                              |

## Running the bundled examples

```bash
./src/denver.py examples/simple-env                 # start it (opens a shell)
./src/denver.py examples/simple-env -- echo hi      # run one command in it instead
./src/denver.py examples/simple-env --show-config   # print the resulting config
```

`--show-config` is the fastest way to understand an example env you didn’t write: it
resolves the whole `import:` chain and prints what denver actually ended up
with. It needs no toolchain, no network and no Docker, so it works for every
env.

> [!NOTE]
> **Next:** the reference half of this documentation — the
> [`denver` command](../cli/arguments.md) for every flag, the full
> [`denver.toml` schema](../configuration/denver-toml.md) for every key, and one
> page [per provider](../providers/uv.md) for the keys a given stage type
> accepts.
