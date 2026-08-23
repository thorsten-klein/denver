# denver

<img src="https://raw.githubusercontent.com/thorsten-klein/denver/develop/src/denver_assets/logo.svg" alt="denver logo" width="500"/>

Development Environments as code — reproducible, flexible, simple and fast.

> [!NOTE]
> This documentation is also published as **plain Markdown**, one file per page
> mirroring this exact page tree — meant for AI tools/LLMs to ingest directly,
> without having to scrape rendered HTML. Start at
> <a href="markdown/index.md">markdown/index.md</a>

## Introduction

Start here if you have never seen a `denver.toml`.

- [About denver](introduction/index.md)
- [Install denver](introduction/install.md)

```{toctree}
:maxdepth: 1
:caption: Introduction
:hidden:

introduction/index
introduction/install
```

## Quickstart

There are some examples that show denver in action

- [denver in 5 minutes](quickstart/05-minutes.md)
- [denver in 30 minutes](quickstart/30-minutes.md)
- [Examples](quickstart/examples.md)

```{toctree}
:maxdepth: 1
:caption: Quickstart
:hidden:

quickstart/05-minutes
quickstart/30-minutes
quickstart/examples
```

## Concepts

The vocabulary, the philosophy and the design principles behind `denver`

- [Glossary](concepts/glossary.md)
- [Philosophy](concepts/philosophy.md)

```{toctree}
:maxdepth: 1
:caption: Concepts
:hidden:

concepts/glossary
concepts/philosophy
```

## `denver` command

Full reference documentation of the `denver` command line interface and
environment variables used by `denver`.

- [Arguments](cli/arguments.md)
- [Shell completion](cli/completion.md)
- [Environment variables](cli/environment-variables.md)

```{toctree}
:maxdepth: 1
:caption: denver command
:hidden:

cli/arguments
cli/completion
cli/environment-variables
```

## Configuration

The complete `denver.toml` reference: every top-level key, every generic
stage key, how `import:` chains merge, and the mechanisms behind layering,
hooks, overrides and fingerprinting.

- [denver.toml](configuration/denver-toml.md)

```{toctree}
:maxdepth: 1
:caption: Configuration
:hidden:

configuration/denver-toml
```

## Providers

One page per provider: a full key reference for that provider's
`denver.toml` section (every key, what it does, its default) plus design
notes on the patterns it supports and how it behaves under
`--fast`/`--force`.

| Provider | Purpose |
|---|---|
| [`uv`](providers/uv.md) | Create/manage a Python virtualenv via `uv` |
| [`conan`](providers/conan.md) | Provision native tools (compilers, cmake, ninja) via Conan |
| [`docker`](providers/docker.md) | Wrapper: relocate the pipeline into a compose service |
| [`zephyr`](providers/zephyr.md) | Manage a West (Zephyr RTOS) workspace |
| [`download`](providers/download.md) | Fetch, verify and unpack prebuilt release archives |
| [`custom`](providers/custom.md) | Escape hatch: an arbitrary command, sourced script or launcher |

A project can also register its own provider, without a denver fork — see
"Extension providers" in [Configuration](configuration/denver-toml.md).

- [uv](providers/uv.md)
- [conan](providers/conan.md)
- [docker](providers/docker.md)
- [zephyr](providers/zephyr.md)
- [download](providers/download.md)
- [custom](providers/custom.md)

```{toctree}
:maxdepth: 1
:caption: Providers
:hidden:

providers/uv
providers/conan
providers/docker
providers/zephyr
providers/download
providers/custom
```

## Contributing

- [Development](contributing/development.md)

```{toctree}
:maxdepth: 1
:caption: Contributing
:hidden:

contributing/development
```
