# denver

<img src="https://raw.githubusercontent.com/thorsten-klein/denver/develop/src/denver_assets/logo.svg" alt="logo" width="500"/>

**Development Environments as code — reproducible, flexible, simple and fast.**

[![CI](https://github.com/thorsten-klein/denver/actions/workflows/ci.yml/badge.svg)](https://github.com/thorsten-klein/denver/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/thorsten-klein/denver/branch/develop/graph/badge.svg)](https://codecov.io/gh/thorsten-klein/denver)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=thorsten-klein_denver&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=thorsten-klein_denver)
[![PyPI](https://img.shields.io/pypi/v/denver-tool.svg)](https://pypi.org/project/denver-tool/)
[![Python versions](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fthorsten-klein%2Fdenver%2Fdevelop%2Fpyproject.toml)](https://pypi.org/project/denver-tool/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/thorsten-klein/denver/blob/develop/LICENSE)

**D**evelopment **Env**ironment Launch**er** — declares dev environments in a
`denver.yml` (or `denver.toml`): reproducible and layerable to fit your
project's needs.

## What problem does this solve?

Every project needs *some* setup before it builds — a fresh clone, a
submodule init, a venv, a toolchain install. How much varies wildly, but the
pattern is the same: you run a pile of one-off steps to get a working build
the first time.

`denver` gives you a declarative way to setup your environment. You describe the steps a
project needs in a `denver.yml`, and `denver` runs them -- the same way every
time on any machine — for you, for teammates, and in CI. `denver` is optimized to run
only necessary stages, so it stays fast.

## Documentation

You can find the full documentation in Markdown [here](https://github.com/thorsten-klein/denver/blob/develop/doc/introduction/index.md)

Additionally it is hosted [on GitHub Pages](https://thorsten-klein.github.io/denver/).

## Install

You can install `denver` with `pip`:
```bash
pip install denver-tool
```

If you prefer, you can also install with `uv`. Please refer to official uv documentation https://docs.astral.sh/uv/getting-started/installation how to install uv
```bash
uv tool install denver-tool
```

Instead of installing `denver`, you can also run the script `src/denver.py` directly:
```bash
src/denver.py --version
```
For easier usage you can also set up an alias so it behaves like an installed command:
```bash
alias denver="$PWD/src/denver.py"
denver --version
```

> **Note:** denver needs Python `>=3.9`. Stuck below that? Run the script
> with `uv run src/denver.py`, e.g by setting `alias denver="uv run $PWD/src/denver.py"`.
> Alternatively grab a prebuilt executable from a
> [release](https://github.com/thorsten-klein/denver/releases), or build one
> yourself with `uv run poe pyinstaller`.
> (Between `3.9` and `3.11`? Regular installs work fine -- only `denver.toml`
> configs need `>=3.11`, for `tomllib`; `denver.yml`/`denver.yaml` works
> everywhere `denver` itself does.)

For more details about how to install or run `denver` see
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


For more details see **[Shell completion →](https://github.com/thorsten-klein/denver/blob/develop/doc/cli/completion.md)**.

## Try it out

Have a look at the `examples/simple-env/denver.yml`:
```yaml
stages:
- print-vars-before
- set-vars
- print-vars-after

print-vars-before:
  provider: custom
  cmd: 'echo "[print-vars-before] MYVAR=$MYVAR FOO=$FOO BAR=$BAR"'

set-vars:
  provider: custom
  cmd: 'echo "[set-vars] sourcing custom.sh..."'
  source: custom.sh

print-vars-after:
  provider: custom
  cmd: 'echo "[print-vars-after] MYVAR=$MYVAR FOO=$FOO BAR=$BAR"'
```

As you can see, this environment consists of three stages.
- First stage prints out the variables and their values how they are currently set (most probably empty)
- Second stage prints some text, and it sources custom.sh script. In this script each variable is set to a value
- Third stage prints out the variables and their values again so see if the second stage has worked

You can run an own command (e.g. `printenv FOO MYVAR BAR`) within this environment as following:

```bash
denver run examples/simple-env -- printenv MYVAR
```

Output:
```bash
[print-vars-before] MYVAR= FOO= BAR=
[set-vars] sourcing custom.sh...
[print-vars-after] MYVAR=1 FOO=2 BAR=3
2
1
3
```

For available flag (e.g. the `-q`, see
**[CLI arguments →](https://github.com/thorsten-klein/denver/blob/develop/doc/cli/arguments.md)**
or run `denver --help` respectively `denver run --help`.

You want to see some more advanced example? Have a look at **[denver in 5 minutes →](https://github.com/thorsten-klein/denver/blob/develop/doc/quickstart/five-minutes.md)**.

You want to see some even more advanced example? Have a look at **[denver in 30 minutes →](https://github.com/thorsten-klein/denver/blob/develop/doc/quickstart/30-minutes.md)**


## Known limitations

**denver has exactly one runtime dependency: PyYAML.** Its default config
format is YAML (`denver.yml`/`denver.yaml`), parsed with PyYAML — that's
what lets the floor be as low as python `>=3.9`. `denver.toml` is supported
too, but only where `tomllib` is importable (stdlib only from python
`>=3.11`) — on an older interpreter it's rejected with a clear error instead
of a silent misread. See
[install or run denver](https://github.com/thorsten-klein/denver/blob/develop/doc/introduction/install.md).

## Contributing

Bug reports, feature requests and pull requests are very welcome — see
[`doc/contributing/development.md`](https://github.com/thorsten-klein/denver/blob/develop/doc/contributing/development.md) for the workflow.

To sum up: `uv run poe all` should always pass.

## License

Apache License 2.0 — see [`LICENSE`](https://github.com/thorsten-klein/denver/blob/develop/LICENSE).
