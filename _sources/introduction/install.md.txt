# Install denver

There are several ways to install denver. Pick whichever fits your project.

## Run from source (no install)

The fastest way to try denver, and the one with nothing to install: clone
the repository and run the script directly.

> [!NOTE]
> python3 (version `>=3.9`) is required to be installed. `denver.toml`
> configs additionally need `>=3.11` (`tomllib` is stdlib only from there) --
> `denver.yml`/`denver.yaml`, denver's default format, works on the full
> `>=3.9` range.

```bash
git clone https://github.com/thorsten-klein/denver.git
```

```bash
./denver/src/denver.py --version
```

That's it — `src/denver.py` *is* the `denver` command, with no packaging
step in between. Every example in this documentation that says `denver run <env> ...`
works exactly the same way as `./denver/src/denver.py run <env> ...`.
For easier usage you may want to set `alias denver=$PWD/src/denver.py`.

## From PyPI

denver is also deployed as pure-Python package to PyPi. Install it with `pip`:

```bash
pip install denver-tool
```

or straight from GitHub:

```bash
pip install git+https://github.com/thorsten-klein/denver.git@develop
```

If you use `uv` instead of `pip`:

> [!NOTE]
> Please refer to official [uv documentation](https://docs.astral.sh/uv/getting-started/installation/)
> how to install `uv`. For example on Linux you can install via
> `curl -LsSf https://astral.sh/uv/install.sh | sh`


```bash
uv tool install denver-tool
```

or straight from GitHub:

```bash
uv tool install git+https://github.com/thorsten-klein/denver.git@develop
```

Any of those commands installs the `denver` command.




## Prebuilt binary

On a machine with no Python at all, or if you are stuck on Python older than
`3.9`, use the standalone executable attached to every
[release](https://github.com/thorsten-klein/denver/releases) instead. It
bundles `denver`, its providers and a Python interpreter into one
executable. (Stuck between `3.9` and `3.11`, specifically? A regular install
still works fine as long as your configs are `denver.yml`/`denver.yaml` --
only `denver.toml` needs `3.11`, see
[Configuration](../configuration/denver-toml.md).)

Find the download link for the latest release
[in your browser](https://github.com/thorsten-klein/denver/releases)
or download via `curl`:


```bash
ASSET=$(curl -sSL https://api.github.com/repos/thorsten-klein/denver/releases/latest | grep -o 'https://[^"]*\.tar\.xz')
```

Download and unpack it:

```bash
curl -sSL "$ASSET" | tar -xJf -
```

Run it:

```bash
./denver --version
```

Covers x86_64 Linux with glibc 2.28 or newer (Ubuntu 20.04+, Debian 10+,
Fedora 29+, RHEL/Alma/Rocky 8+, Arch, Mint 20+); musl-based distros such as
Alpine are not covered yet. Build it yourself with
`scripts/create-python-exe.sh` or `uv run poe pyinstaller`).


## Editable install

If you want to install denver and hack on source code yourself, you
can install it in python's editable mode:

First clone the repository

```bash
git clone https://github.com/thorsten-klein/denver.git
```

Then install in editable mode, e.g. via `pip`
```bash
pip install -e ./denver
```

## Vendor with git-nested

To vendor denver straight into your own monorepo instead of depending on it
as an installed package, add it via
[git-nested](https://github.com/thorsten-klein/git-nested):

```bash
git-nested clone https://github.com/thorsten-klein/denver.git
```

Nothing needs installing here either — same idea as
["Run from source"](install.md#run-from-source-no-install):
just vendored into your own repository instead of cloned standalone.

> [!NOTE]
> **Next:** [denver in 5 minutes](../quickstart/05-minutes.md) — run a real
> environment end to end. It starts with the handful of host tools you need
> first, which depends on which providers the environment uses.
