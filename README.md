# denver

<img src="https://raw.githubusercontent.com/thorsten-klein/denver/develop/src/denver_assets/logo.svg" alt="logo" width="80%"/>

**Development Environments as code — reproducible, flexible, simple and fast.**

[![CI](https://github.com/thorsten-klein/denver/actions/workflows/ci.yml/badge.svg)](https://github.com/thorsten-klein/denver/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/thorsten-klein/denver/branch/develop/graph/badge.svg)](https://codecov.io/gh/thorsten-klein/denver)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=thorsten-klein_denver&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=thorsten-klein_denver)
[![PyPI](https://img.shields.io/pypi/v/denver-tool.svg)](https://pypi.org/project/denver-tool/)
[![Python versions](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fthorsten-klein%2Fdenver%2Fdevelop%2Fpyproject.toml)](https://pypi.org/project/denver-tool/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/thorsten-klein/denver/blob/develop/LICENSE)

**D**evelopment **Env**ironment Launch**er** — declares dev environments in a
`denver.toml`: reproducible and layerable to fit your project's needs.

## What problem does this solve?

Every project needs *some* setup before it builds — a fresh clone, a
submodule init, a venv, a toolchain install. How much varies wildly, but the
pattern is the same: you run a pile of one-off steps to get a working build
the first time, then forget which ones to rerun after switching branches.
The result is broken builds and "works on my machine" debugging that has
nothing to do with your actual code.

`denver` fixes this by making setup declarative. You describe the steps a
project needs in a `denver.toml`, and `denver` runs them the same way every
time — for you, for teammates, and in CI. It only runs the stages your
project actually needs, so it stays fast, and it has no opinion about which
tools you use: `denver` just runs what you declare.

**[Read the full walkthrough →](https://github.com/thorsten-klein/denver/blob/develop/doc/introduction/index.md)**
— the problem built up one step at a time, from that first script to
`import:`-based inheritance across a whole fleet of projects.

## Documentation

Built documentation is available
[on GitHub Pages](https://thorsten-klein.github.io/denver/).

## Install

```bash
pip install denver-tool   # or: uv tool install denver-tool
```

Alternatively, call `src/denver.py` directly, no install needed:
```bash
src/denver.py --version
```
or set up an alias so it behaves like an installed command:
```bash
alias denver="$PWD/src/denver.py"
denver --version
```

For more alternatives how to use or install `denver` see
**[Install denver →](https://github.com/thorsten-klein/denver/blob/develop/doc/introduction/install.md)**.


## Tab Completion

Tab completion works in bash, zsh, and fish.

bash/zsh:
```bash
eval "$(denver complete)"
```
fish:
```fish
denver complete | source
```

See **[Shell completion →](https://github.com/thorsten-klein/denver/blob/develop/doc/cli/completion.md)** for making it permanent.

## Try it

```bash
denver run examples/howto-env -- pytest examples/howto-env/tests
```

A minute or two later (much less on repeat runs), that one command has built
a container, a Python venv, a hand-installed tool, and a conan-installed
toolchain, applied a team convention, and run tests proving all five actually
work — from a single `denver.toml`, identically on your machine or your
colleague's.

**[denver in 15 minutes →](https://github.com/thorsten-klein/denver/blob/develop/doc/quickstart/creating-environments.md)**
walks through each stage and explains it, including all the flags and config options you'll
actually use. Prefer something smaller first? **[denver in 5 minutes →](https://github.com/thorsten-klein/denver/blob/develop/doc/quickstart/five-minutes.md)**
runs a three-line environment end to end instead. For every flag, see
**[CLI arguments →](https://github.com/thorsten-klein/denver/blob/develop/doc/cli/arguments.md)**
or run `denver --help`.

## Known limitations

**denver has zero runtime dependencies.** Its config format is TOML
(`denver.toml`), parsed with the standard library's `tomllib` — hence the
`>=3.11` floor. Nothing extra to install, on the host or inside a
`docker`-wrapped image: a Dockerfile only needs `python3` itself, since denver
re-invoked inside the container needs nothing beyond the interpreter.

> **Note:** stuck on Python < 3.11? Grab the prebuilt executable from a
> [release](https://github.com/thorsten-klein/denver/releases), or build one
> yourself with `uv run poe pyinstaller`, and use that binary instead — see
> [Prebuilt Binary](https://github.com/thorsten-klein/denver/blob/develop/doc/introduction/install.md#prebuilt-binary).
> Alternatively run with `uv run src/denver.py`, e.g by setting `alias denver="uv run $PWD/src/denver.py"`.

## Contributing

Bug reports, feature requests and pull requests are welcome — see
[`doc/contributing/development.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/contributing/development.md) for the workflow (`uv run poe all`
runs lint, format, mypy and the test suite; denver keeps 100% coverage).

## License

Apache License 2.0 — see [`LICENSE`](https://github.com/thorsten-klein/denver/blob/develop/LICENSE).
