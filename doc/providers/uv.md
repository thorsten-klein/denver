# uv provider

A `uv` stage creates/manages a Python virtualenv with
[`uv`](https://docs.astral.sh/uv/) (rather than plain `pip`/`venv`).

```yaml
my-uv-stage:
  provider: uv
  python: "3.12.3"
  requirements:
  - requirements.txt
```

(`provider:`/`description:`/`disabled:`/`scripts:` are generic keys every stage has —
see "Generic stage keys" in [`../architecture.md`](../architecture.md). Everything below is specific to `uv`.)

## Requires

**`uv` must already be installed** wherever this stage runs — denver never
installs it. That's the host for a plain run, or the container when a
`docker` stage relocated the pipeline first (see "Wrapper / relocation" in
[`../architecture.md`](../architecture.md)), in which case the image needs
it. Install it per [uv's own instructions](https://docs.astral.sh/uv/getting-started/installation/);
a stage with no `uv` on `PATH` fails with `uv provider needs 'uv' on PATH`.

## Key reference

- **`python`** (default `"3.12.3"`) — the interpreter version the venv is
  created with.
- **`uv`** (default: `uv` on `PATH`) — the `uv` executable itself. (Yes,
  `uv.uv`: the provider is named after the tool, and this key still points at
  the binary — e.g. `-c uv.uv=/opt/uv/bin/uv`.)
- **`requirements`** — a list of `-r` files, installed together.
- **`install-args`** — extra literal `uv pip install` arguments, e.g.
  `["--pre"]`. An entry wrapped as `$(...)` is instead run as a shell
  command right before install; its stdout is split on whitespace and each
  token appended as its own arg — the way to pull in a dynamically-computed
  set of packages (e.g. `$(west packages pip)`) without hand-maintaining a
  requirements file for them.
- **`lock`** — optional; the uv-*project* (`pyproject.toml` + `uv.lock`) way
  of filling the same venv, independent of `requirements:` above (either,
  both or neither may be set). Two keys, each a path to a `uv.lock` (uv only
  ever reads/writes `<project>/uv.lock`, so any other filename is a config
  error, and the project is the directory the lockfile sits in — it must
  hold that project's `pyproject.toml`):
  - **`create`** — runs `uv lock` for that project, i.e. *writes* the
    lockfile (an output, like `freeze-to:`).
  - **`sync`** — runs `uv sync` for that project, installing the lockfile
    into the venv this stage just activated (`--active`), exactly as locked
    (`--frozen` — re-resolving it is `create:`'s job, not a silent side
    effect of syncing) and without pruning packages the lockfile doesn't
    mention (`--inexact`, so a venv shared with another stage, or filled by
    this stage's own `requirements:`, survives).

  With both set, `create:` runs first, so one stage can relock and then
  install what it locked. `sync:`'s lockfile counts as an install input, so
  changing it recreates the venv the way a changed requirements file does;
  `create:`'s doesn't (it's this run's own output). Both commands get the
  same `find-links:`/`no-index:` wheel sources as `uv pip install`.
- **`overrides`** — a list of `--override` files.
- **`find-links`** — extra wheel sources (e.g. a local cache directory).
- **`no-index`** (default `false`) — `true`/`false`/`auto`. `false` installs
  from an index normally, wherever the stage runs. Set `auto` for an env
  whose *container* is meant to install offline: it then resolves to `true`
  inside a docker-wrapped env and `false` on the host — the assumption being
  that the image already has everything it needs baked in (a wheel cache
  reachable via `find-links:`) and shouldn't reach out to the network, while
  a host run should. `auto` is opt-in rather than the default because a
  container that has *not* had its wheels baked in is the far more common
  case, and there the default has to be a working install, not an offline
  one.
- **`link-mode`** (default `"copy"`) — `uv`'s own link mode
  (`UV_LINK_MODE`); `copy` avoids a hardlink warning when the venv and
  `uv`'s cache are on different filesystems.
- **`venv-patcher`** — optional. `exe` (default: `venv-patcher` on `PATH`)
  and `patches` (**required** if `venv-patcher:` is given at all — a path
  to a patches file). Applies patches to the venv's installed packages
  after install.
- **`skip-if`** — a list of scripts; if every one exits `0`, the install
  step is skipped entirely — `uv pip install` as well as `lock:`'s `uv
  lock`/`uv sync` (the venv is still created/activated).
- **`venv`** — names this stage's venv, so several `uv` stages can target
  distinct venvs (or share one by using the same name, or both leaving it
  unset).
- **`freeze-to`** — a path; after a real install, `uv pip freeze`'s full
  output is written there. Useful as a lockfile a later run (or a different
  `uv` stage) can read back via `requirements:`.
- **`append-mode`** (default `false`) — see "Reproducibility" in
  [`../philosophy.md`](../philosophy.md) for the full trade-off. When `true`, every `uv pip
  install` invocation reuses every `-r`/`--override`/`--find-links`/
  `--no-index`/literal arg any *previous* run of this stage ever resolved,
  appending only what's new this run — so a source that drops out later
  (e.g. a project losing a dynamic `install-args:` command) never causes
  `uv` to reconsider a package only that source pulled in. The accumulated
  arg list is kept outside the venv itself
  (`<DENVER_DIR>/.envs/<env>/.logs/<stage>-install-args.json`), so it
  survives a checksum-triggered venv recreation; delete that file to reset
  it. Off by default because it makes the resulting venv depend on this
  machine's run history, not just the current `denver.yml`.

`skip-if` and `venv-patcher` are never guessed from the env's directory
layout (see "Explicit over implicit" in [`../philosophy.md`](../philosophy.md)) — with no
`skip-if:` there is simply no skip check, and the venv patcher runs only
when `venv-patcher:` names its `patches:` file explicitly.

## Design notes

- **Why `uv`, not plain `pip`.** Speed (a full resolve+install that takes
  `pip` tens of seconds typically takes `uv` a fraction of that) and
  robustness (a more thorough, deterministic resolver) matter more for a
  tool meant to be run often, not just once at project setup. The provider
  is named after the tool it actually runs, so the config says what happens.
- **`overrides:` for conflict resolution.** Rather than hand-editing (or
  forking) a `requirements.txt` to work around a version conflict between
  two dependencies, an `overrides:` file pins the conflicting package
  directly, without touching the requirements file it's overriding.
- **Several `uv` stages, one venv.** Two (or more) stages sharing a `venv:`
  name build up the *same* venv in sequence — e.g. so a later stage's
  packages are importable by whatever a tool installed by an earlier stage
  needs to see. Only the first such stage (in `stages:` order) to touch a
  given venv this run decides whether to recreate it (based on its own
  checksum); later stages sharing it only ever install on top.
- **`--fast`** sources the existing venv instead of creating/installing it;
  dies with a clear message if the venv doesn't exist yet — run once
  without `--fast` first.
- **`--force`** recreates the venv from scratch unconditionally and
  bypasses every `skip-if:` script.
