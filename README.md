# denver

<img src="https://raw.githubusercontent.com/thorsten-klein/denver/develop/src/denver_assets/logo.svg" alt="logo" width="80%"/>

**Development Environments as code — reproducible, flexible, simple and fast.**

[![CI](https://github.com/thorsten-klein/denver/actions/workflows/ci.yml/badge.svg)](https://github.com/thorsten-klein/denver/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/thorsten-klein/denver/branch/develop/graph/badge.svg)](https://codecov.io/gh/thorsten-klein/denver)
[![PyPI](https://img.shields.io/pypi/v/denver-tool.svg)](https://pypi.org/project/denver-tool/)
[![Python versions](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fthorsten-klein%2Fdenver%2Fdevelop%2Fpyproject.toml)](https://pypi.org/project/denver-tool/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/thorsten-klein/denver/blob/develop/LICENSE)

**D**evelopment **Env**ironment Launch**er** — declares dev environments in a
`denver.yml`: reproducible and layerable to fit your project's needs.

## What problem does this solve?

Every project needs *some* setup before you can build it — and how much
varies enormously. A `setup.sh` is only a suggestion nobody re-runs after a
`git pull`; whatever happens to already be installed on your machine quietly
covers for the steps that were never written down. denver declares that
setup instead, in a `denver.yml`, and runs it the same way every time —
anywhere from a one-stage script replacement to a five-stage cross-compile
toolchain relocated into a container. **You only pay for the stages your
project actually needs** — nothing here is mandatory, and denver has no
opinion about which tools you use.

**[Read the full walkthrough →](https://github.com/thorsten-klein/denver/blob/develop/doc/introduction/index.md)**
— the problem built up one step at a time, from that first script to
`import:`-based inheritance across a whole fleet of projects.

## Documentation

The built site (search, sidebar nav, one page per topic — and a plain-Markdown
mirror of every page under `/markdown/` for AI tools/LLMs) is published from
[`doc/`](https://github.com/thorsten-klein/denver/tree/develop/doc/) by
[`.github/workflows/docs.yml`](https://github.com/thorsten-klein/denver/blob/develop/.github/workflows/docs.yml).
Browsing on GitHub instead:

| Document | What's in it |
|---|---|
| [`doc/README.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/README.md) | Documentation index — start here |
| [`doc/introduction/index.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/introduction/index.md) | What problem denver solves, what an environment is, how flexible it is |
| [`doc/introduction/install.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/introduction/install.md) | Installing denver, all the ways |
| [`doc/quickstart/five-minutes.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/quickstart/five-minutes.md) | End-to-end walkthrough of a complete bundled example |
| [`doc/quickstart/creating-environments.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/quickstart/creating-environments.md) | Step-by-step: build that same environment yourself, one stage at a time |
| [`doc/quickstart/examples.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/quickstart/examples.md) | The eight bundled environments, smallest to largest |
| [`doc/concepts/glossary.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/concepts/glossary.md) | Every term denver uses, defined once |
| [`doc/concepts/philosophy.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/concepts/philosophy.md) | The design principles behind it |
| [`doc/cli/arguments.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/cli/arguments.md) | Every `denver run --help` flag, grouped by what you reach for it for |
| [`doc/cli/completion.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/cli/completion.md) | Tab completion via `denver complete` (bash/zsh/fish) |
| [`doc/cli/environment-variables.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/cli/environment-variables.md) | The variables denver reads and exports, and where env state lives |
| [`doc/configuration/denver-yml.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/configuration/denver-yml.md) | The `denver.yml` schema and how the system works |
| [`doc/providers/`](https://github.com/thorsten-klein/denver/tree/develop/doc/providers/) | One key reference per provider: [uv](https://github.com/thorsten-klein/denver/blob/develop/doc/providers/uv.md), [conan](https://github.com/thorsten-klein/denver/blob/develop/doc/providers/conan.md), [docker](https://github.com/thorsten-klein/denver/blob/develop/doc/providers/docker.md), [zephyr](https://github.com/thorsten-klein/denver/blob/develop/doc/providers/zephyr.md), [custom](https://github.com/thorsten-klein/denver/blob/develop/doc/providers/custom.md) |
| [`doc/contributing/development.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/contributing/development.md) | Contributing: tests, coverage, adding a provider, releasing |
| [`examples/`](https://github.com/thorsten-klein/denver/tree/develop/examples/) | Eight working environments, smallest to largest, each with its own README |

## Install

```bash
pip install denver-tool   # or: uv tool install denver-tool
```

Also available: a standalone executable that needs no Python at all, an
editable install for hacking on denver itself, and vendoring straight into
your own monorepo via [git-nested](https://github.com/thorsten-klein/git-nested)
— see **[Install denver →](https://github.com/thorsten-klein/denver/blob/develop/doc/introduction/install.md)**.

Tab completion (bash/zsh/fish): `eval "$(denver complete)"` in your shell rc
file. Works for the installed `denver` command and, run straight from a
checkout instead (`./src/denver.py complete`), for that too — no alias
needed — see
**[Shell completion →](https://github.com/thorsten-klein/denver/blob/develop/doc/cli/completion.md)**.

## Try it

```bash
denver run examples/howto-env -- pytest examples/howto-env/tests
```

A minute or two later (much less on repeat runs) that command has built a
container, a Python venv, a hand-installed tool, a conan-installed toolchain
and a team convention — and run tests proving all five actually work — from
that one `denver.yml`, on your machine or your colleague's, identically. See
**[Denver in 5 minutes →](https://github.com/thorsten-klein/denver/blob/develop/doc/quickstart/five-minutes.md)**
for the full walkthrough (pre-conditions, what each stage does, the flags
you'll actually use), or **[CLI arguments →](https://github.com/thorsten-klein/denver/blob/develop/doc/cli/arguments.md)**
/ `denver --help` for every flag.

## Known limitations

**Every place denver runs needs `import yaml` to work.** denver is a Python
program with exactly one runtime dependency, PyYAML, so installing it from
PyPI covers the host automatically. What it cannot cover is a *wrapped*
environment: a `docker` stage relocates the rest of the stack into the
container and re-invokes denver in there with the image's own bare `python3`
— an interpreter that knows nothing about the install on your host. That
image must supply PyYAML itself:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-yaml \
    && rm -rf /var/lib/apt/lists/*
```

If it doesn't, denver stops with an error naming the interpreter that
failed to `import yaml`.

**Which PyYAML that is, is deliberately not pinned.** The host copy and the
image copy are resolved by two different package managers and will drift
apart in general. denver accepts that: it calls exactly two functions from
the library, `yaml.safe_load` and `yaml.safe_dump`, whose behaviour has been
stable across PyYAML releases for years. So any reasonably recent PyYAML is
fine, and denver imports whatever it finds instead of demanding a particular
version and forcing every image to track it. The declared dependency is a
floor (`pyyaml>=6`), not a pin — and it constrains only the host install
anyway, never the image's.

## Contributing

Bug reports, feature requests and pull requests are welcome — see
[`doc/contributing/development.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/contributing/development.md) for the workflow (`uv run poe all`
runs lint, format, mypy and the test suite; denver keeps 100% coverage).

## License

Apache License 2.0 — see [`LICENSE`](https://github.com/thorsten-klein/denver/blob/develop/LICENSE).
