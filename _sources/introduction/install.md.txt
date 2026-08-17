# Install Denver

There are multiple options how to install denver. Freely choose what fits best to you.

## From PyPi

denver is a small pure-Python package — install it from PyPI or straight from
GitHub:

```bash
# with pip
pip install denver-tool
pip install git+https://github.com/thorsten-klein/denver.git

# or with uv
uv tool install denver-tool
uv tool install git+https://github.com/thorsten-klein/denver.git
```

This installs the `denver` script/entry point.

Tip: once it's on `PATH`, `eval "$(denver complete)"` in your shell rc file
gets you tab completion for subcommands, env paths and flags — see
[Shell completion](../cli/completion.md).

## Prebuilt Binary

On a machine with no Python at all, take the standalone executable attached
to every [release](https://github.com/thorsten-klein/denver/releases)
instead — it bundles denver, its providers and a Python interpreter in one
file:

```bash
# Find Asset from latest release
ASSET=$(curl -sSL https://api.github.com/repos/thorsten-klein/denver/releases/latest | grep -o 'https://[^"]*\.tar\.xz')

# Download the Asset and untar it
curl -sSL "$ASSET" | tar -xJf -

# Run denver
./denver --version
```

x86_64 Linux with glibc 2.28 or newer (Ubuntu 20.04+, Debian 10+, Fedora 29+,
RHEL/Alma/Rocky 8+, Arch, Mint 20+); musl-based distros such as Alpine are not
covered. Build it yourself with `scripts/create-python-exe.sh`. Note this
only replaces *denver's* own installation — the tools its providers drive
(see [Pre-conditions](../quickstart/five-minutes.md#pre-conditions)) still
have to be there.

## Editable mode

To hack on denver itself (or pin it to a specific commit/tag/branch), clone
it and install in editable mode instead:

```bash
git clone https://github.com/thorsten-klein/denver.git
pip install -e ./denver
```


## Vendor with git-nested

If you'd rather vendor denver straight into your own monorepo instead of
depending on it as an installed package, add it via
[git-nested](https://github.com/thorsten-klein/git-nested):

```bash
git-nested clone https://github.com/thorsten-klein/denver.git
```

With that approach nothing needs installing — just call `src/denver.py` (or
the vendored copy's equivalent path) directly, exactly as `denver run <env>
...` is used throughout the rest of this documentation.

```{note}
**Next:** [Denver in 5 minutes](../quickstart/five-minutes.md) — run a real
environment end to end. It starts with the handful of host tools you need
first, which depends on which providers the environment uses.
```
