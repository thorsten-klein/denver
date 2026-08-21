# Install denver

There are several ways to install denver. Pick whichever fits your project.

## Run from source (no install)

The fastest way to try denver, and the one with nothing to install: clone
the repository and run the script directly.

```bash
git clone https://github.com/thorsten-klein/denver.git
```

```bash
./denver/src/denver.py --version
```

That's it — `src/denver.py` *is* the `denver` command, with no packaging
step in between. Every example in this documentation that says `denver run
<env> ...` works exactly the same way as `./denver/src/denver.py run <env>
...`. This is also how you'd pin denver to a specific commit, tag or branch
for a project: `git checkout` whatever you need inside that clone.

## From PyPI

denver is a small pure-Python package. Install it with `pip`:

```bash
pip install denver-tool
```

or straight from GitHub:

```bash
pip install git+https://github.com/thorsten-klein/denver.git
```

If you use [`uv`](#installing-uv) instead of `pip`:

```bash
uv tool install denver-tool
```

```bash
uv tool install git+https://github.com/thorsten-klein/denver.git
```

Any of the four installs the `denver` script/entry point.

Tip: once it's on `PATH`, add this to your shell's rc file for tab
completion of subcommands, env paths and flags — see
[Shell completion](../cli/completion.md):

```bash
eval "$(denver complete)"
```

## Installing uv

Several of denver's providers (`uv` itself, and anything a `uv` stage sets
up first, like `conan` or `west`) need [`uv`](https://docs.astral.sh/uv/) on
your machine. denver never installs it for you — install it once, the same
way for any project:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

(Windows, or other install methods: see uv's own
[installation guide](https://docs.astral.sh/uv/getting-started/installation/).)

## Prebuilt binary

On a machine with no Python at all, or one stuck on Python older than 3.11
(denver needs `>=3.11` for `tomllib`), use the standalone executable
attached to every [release](https://github.com/thorsten-klein/denver/releases)
instead. It bundles denver, its providers and a Python interpreter into one
file.

Find the download link for the latest release:

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
Alpine are not covered. Build it yourself with
`scripts/create-python-exe.sh` (or, from a dev checkout, `uv run poe
pyinstaller`). Note this only replaces *denver's own* installation — the
tools its providers drive (see
[Pre-conditions](../quickstart/05-minutes.md#pre-conditions-for-a-real-project))
still have to be there.

## Editable install

To hack on denver itself:

```bash
git clone https://github.com/thorsten-klein/denver.git
```

```bash
pip install -e ./denver
```

This is the same clone as ["Run from source"](install.md#run-from-source-no-install)
above, plus registering it as an installed package (so e.g. `denver
--version` reports it correctly) while still editing the same checkout.

## Vendor with git-nested

To vendor denver straight into your own monorepo instead of depending on it
as an installed package, add it via
[git-nested](https://github.com/thorsten-klein/git-nested):

```bash
git-nested clone https://github.com/thorsten-klein/denver.git
```

Nothing needs installing here either — same idea as ["Run from
source"](install.md#run-from-source-no-install), just vendored into your own
repository instead of cloned standalone.

> **Note**
>
> **Next:** [denver in 5 minutes](../quickstart/05-minutes.md) — run a real
> environment end to end. It starts with the handful of host tools you need
> first, which depends on which providers the environment uses.
