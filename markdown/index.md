# denver

![denver logo](../src/denver_assets/logo.svg)

Development Environments as code — reproducible, flexible, simple and fast.

#### NOTE
This documentation is also published as **plain Markdown**, one file per page
mirroring this exact page tree — meant for AI tools/LLMs to ingest directly,
without having to scrape rendered HTML. Start at
<a href="markdown/index.md">markdown/index.md</a>; every page below has a
twin under <code>markdown/</code> at the same path. See <code>doc/conf.py</code>
and <code>examples/doc-env/denver.yml</code> for how it’s built.

## New to denver? Read in this order

The pages below are written to be read front to back — each one picks up
where the last left off, and they share a single worked example
(`examples/howto-env`) so nothing has to be re-explained.

|   # | Page                                                                                | What you get out of it                                             |
|-----|-------------------------------------------------------------------------------------|--------------------------------------------------------------------|
|   1 | [What problem denver solves](introduction/index.md)                                 | Why a `denver.yml` beats a `setup.sh`, built up one step at a time |
|   2 | [What is a denver environment?](introduction/index.md#what-is-a-denver-environment) | The model: environments, stages, providers                         |
|   3 | [Install Denver](introduction/install.md)                                           | Getting the `denver` command onto your machine                     |
|   4 | [Denver in 5 minutes](quickstart/five-minutes.md)                                   | Run a real environment end to end and watch it work                |
|   5 | [Creating environments](quickstart/creating-environments.md)                        | Build that same environment yourself, from an empty folder         |

After that, the rest is reference you dip into as needed: the
[`denver` command](cli/arguments.md), the
[`denver.yml` schema](configuration/denver-yml.md), and one page
[per provider](providers/uv.md).

## Introduction

Start here if you have never seen a `denver.yml`.

## Introduction

* [About denver](introduction/index.md)
* [Install Denver](introduction/install.md)

## Quickstart

Run a real environment, then build that same one yourself from an empty
folder — the whole model in action before any reference material.

## Quickstart

* [Denver in 5 minutes](quickstart/five-minutes.md)
* [Creating your first environment](quickstart/creating-environments.md)
* [Examples](quickstart/examples.md)

## Concepts

The vocabulary and the design principles behind it — read these to
understand *why* denver refuses to guess things other tools guess for you.

## Concepts

* [Glossary](concepts/glossary.md)
* [Philosophy](concepts/philosophy.md)

## `denver` command

Everything you can pass to `denver`, and the environment variables it reads.

## denver command

* [Arguments](cli/arguments.md)
* [Shell completion](cli/completion.md)
* [Environment variables](cli/environment-variables.md)

## Configuration

The complete `denver.yml` reference: every top-level key, every generic
stage key, how `import:` chains merge, and the mechanisms behind layering,
hooks, overrides and fingerprinting.

## Configuration

* [denver.yml](configuration/denver-yml.md)

## Providers

One page per provider: a full key reference for that provider’s
`denver.yml` section (every key, what it does, its default) plus design
notes on the patterns it supports and how it behaves under
`--fast`/`--force`.

| Provider                        | Purpose                                                        |
|---------------------------------|----------------------------------------------------------------|
| [`uv`](providers/uv.md)         | Create/manage a Python virtualenv via `uv`                     |
| [`conan`](providers/conan.md)   | Provision native tools (compilers, cmake, ninja) via Conan     |
| [`docker`](providers/docker.md) | Wrapper: relocate the pipeline into a compose service          |
| [`zephyr`](providers/zephyr.md) | Manage a West (Zephyr RTOS) workspace                          |
| [`custom`](providers/custom.md) | Escape hatch: an arbitrary command, sourced script or launcher |

A project can also register its own provider, without a denver fork — see
“Extension providers” in [Configuration](configuration/denver-yml.md).

## Providers

* [uv provider](providers/uv.md)
* [conan provider](providers/conan.md)
* [docker provider](providers/docker.md)
* [zephyr provider](providers/zephyr.md)
* [custom provider](providers/custom.md)

## Contributing

## Contributing

* [Development](contributing/development.md)
