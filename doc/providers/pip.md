# pip provider

A `pip` stage creates/manages a Python virtualenv, using
[`uv`](https://docs.astral.sh/uv/) instead of plain `pip` underneath.

```yaml
my-pip-stage:
  provider: pip
  python: "3.12.3"
  requirements:
  - requirements.txt
```

(`provider:`/`description:`/`disabled:`/`scripts:` are generic keys every stage has —
see "Generic stage keys" in [`../architecture.md`](../architecture.md). Everything below is specific to `pip`.)

## Key reference

- **`python`** (default `"3.12.3"`) — the interpreter version the venv is
  created with.
- **`uv`** (default: `uv` on `PATH`) — the `uv` executable.
- **`requirements`** — a list of `-r` files, installed together.
- **`install-args`** — extra literal `uv pip install` arguments, e.g.
  `["--pre"]`. An entry wrapped as `$(...)` is instead run as a shell
  command right before install; its stdout is split on whitespace and each
  token appended as its own arg — the way to pull in a dynamically-computed
  set of packages (e.g. `$(west packages pip)`) without hand-maintaining a
  requirements file for them.
- **`overrides`** — a list of `--override` files.
- **`find-links`** — extra wheel sources (e.g. a local cache directory).
- **`no-index`** (default `"auto"`) — `auto`/`true`/`false`. `auto`
  resolves to `true` inside a docker-wrapped env, `false` otherwise — the
  assumption being a container already has everything it needs baked in
  and shouldn't reach out to the network, while a host run should.
- **`link-mode`** (default `"copy"`) — `uv`'s own link mode
  (`UV_LINK_MODE`); `copy` avoids a hardlink warning when the venv and
  `uv`'s cache are on different filesystems.
- **`venv-patcher`** — optional. `exe` (default: `venv-patcher` on `PATH`)
  and `patches` (**required** if `venv-patcher:` is given at all — a path
  to a patches file). Applies patches to the venv's installed packages
  after install.
- **`skip-if`** — a list of scripts; if every one exits `0`, the install
  step is skipped entirely (the venv is still created/activated).
- **`venv`** — names this stage's venv, so several `pip` stages can target
  distinct venvs (or share one by using the same name, or both leaving it
  unset).
- **`freeze-to`** — a path; after a real install, `uv pip freeze`'s full
  output is written there. Useful as a lockfile a later run (or a different
  `pip` stage) can read back via `requirements:`.
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
  tool meant to be run often, not just once at project setup.
- **`overrides:` for conflict resolution.** Rather than hand-editing (or
  forking) a `requirements.txt` to work around a version conflict between
  two dependencies, an `overrides:` file pins the conflicting package
  directly, without touching the requirements file it's overriding.
- **Several `pip` stages, one venv.** Two (or more) stages sharing a `venv:`
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
