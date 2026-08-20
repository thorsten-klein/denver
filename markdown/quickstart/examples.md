# Examples

Eight real, working environments live under
[`examples/`](https://github.com/thorsten-klein/denver/tree/develop/examples/)
in the repository, ordered smallest to largest. Each has its own README
explaining what it does, why it exists and what it is meant to teach. They
are not illustrative snippets — every `examples/*/denver.toml` has its
`--show-config-full` output pinned as a golden-file fixture under `tests/golden/`,
so an example that drifted out of sync with the code fails the build.

## Overview

| Example                                                                                                         | Stages                                          | What it is for                                                                                                                                                                                                                                              |
|-----------------------------------------------------------------------------------------------------------------|-------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`simple-env`](https://github.com/thorsten-klein/denver/tree/develop/examples/simple-env)                       | 3 × `custom`                                    | The whole model at minimum size. Also the `cmd:` vs `source:` demo                                                                                                                                                                                          |
| [`zephyr-uv`](https://github.com/thorsten-klein/denver/tree/develop/examples/zephyr-uv)                         | `uv`                                            | A virtualenv and nothing else — proof that no container or toolchain is required                                                                                                                                                                            |
| [`raspberry-pico`](https://github.com/thorsten-klein/denver/tree/develop/examples/raspberry-pico)               | `uv` → `conan`                                  | A cross-compilation toolchain **without Docker**. Why stage order matters                                                                                                                                                                                   |
| [`zephyr-docker`](https://github.com/thorsten-klein/denver/tree/develop/examples/zephyr-docker)                 | `docker`                                        | The container layer on its own — a wrapper stage, and a `hooks: pre-docker:` script                                                                                                                                                                         |
| [`howto-env`](https://github.com/thorsten-klein/denver/tree/develop/examples/howto-env)                         | `docker` → `uv` → `custom` → `conan` → `custom` | Four providers in one small env — run end to end in [Denver in 5 minutes](five-minutes.md) and built from scratch in [Creating environments](creating-environments.md). Also: a prebuilt binary installed by hand, right next to the same job done by conan |
| [`zephyr-devshell`](https://github.com/thorsten-klein/denver/tree/develop/examples/zephyr-devshell)             | *(base — not runnable)*                         | The shared base: `import:`, layering, `runnable: false`                                                                                                                                                                                                     |
| [`zephyr-devshell-4.3.1`](https://github.com/thorsten-klein/denver/tree/develop/examples/zephyr-devshell-4.3.1) | `docker` → `uv` → `conan` → `zephyr` → `custom` | A full Zephyr RTOS setup — the extreme case, and what `import:` layering looks like at full size                                                                                                                                                            |
| [`doc-env`](https://github.com/thorsten-klein/denver/tree/develop/examples/doc-env)                             | `uv` → `custom`                                 | Builds *this documentation* with Sphinx — denver used on itself                                                                                                                                                                                             |

## Running the bundled examples

```bash
./src/denver.py examples/simple-env                 # start it (opens a shell)
./src/denver.py examples/simple-env -- echo hi      # run one command in it instead
./src/denver.py examples/simple-env --show-config   # print the merged config and exit
```

`--show-config` is the fastest way to understand an example env you didn’t write: it
resolves the whole `import:` chain and prints what denver actually ended up
with. It needs no toolchain, no network and no Docker, so it works for every
env here — including `zephyr-devshell`, which cannot be started.

> **Note**

> **Next:** the reference half of this documentation — the
> [`denver` command](../cli/arguments.md) for every flag, the full
> [`denver.toml` schema](../configuration/denver-toml.md) for every key, and one
> page [per provider](../providers/uv.md) for the keys a given stage type
> accepts.
