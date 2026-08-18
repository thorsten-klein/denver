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
see "Generic stage keys" in [Configuration](../configuration/denver-yml.md). Everything below is specific to `uv`.)

## Requires

**`uv` must already be installed** wherever this stage runs — denver never
installs it. That's the host for a plain run, or the container when a
`docker` stage relocated the pipeline first (see "Wrapper / relocation" in
[Configuration](../configuration/denver-yml.md)), in which case the image needs
it. Install it per [uv's own instructions](https://docs.astral.sh/uv/getting-started/installation/);
a stage with no `uv` on `PATH` fails with `uv provider needs 'uv' on PATH`.

## Key reference

- **`python`** (optional, no default) — the interpreter version the venv is
  created with, passed to `uv venv -p`. Left unset, denver passes no `-p` at
  all and uv's own discovery decides (`UV_PYTHON`, then a `.python-version`
  file, then the system interpreter) — denver never picks a version nobody
  wrote down. See "One venv, one interpreter" below for what happens when
  this contradicts a venv that already exists.
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
- **`skip-if-0`** — a list of scripts; if every one exits `0`, the install
  step is skipped entirely — `uv pip install` as well as `lock:`'s `uv
  lock`/`uv sync`. If the venv already exists it's still created/activated
  (later stages depend on it); if it doesn't exist yet, the whole stage is
  skipped instead, same as `disabled: true` — nothing creates/activates an
  empty venv nobody would fill in.
- **`skip-if-1`** — same, but for tools with the inverted convention: skips
  when every script instead exits `1`. Mutually independent from
  `skip-if-0:` — an env can give either, both (each checked on its own
  group; either group being fully satisfied skips the install/stage), or
  neither.
- **`venv`** — the full dirname of this stage's venv (default `.venv`), so
  several `uv` stages can target distinct venvs (or share one by using the
  same name, or both leaving it unset). A value here replaces the whole
  leaf name, not just a suffix on it -- `venv: shared` creates
  `shared[.host]`, not `.venv-shared[.host]`.
- **`freeze-to`** — a path; after a real install, `uv pip freeze`'s full
  output is written there. Useful as a lockfile a later run (or a different
  `uv` stage) can read back via `requirements:`.
- **`append-mode`** (default `false`) — see "Reproducibility" in
  [`../concepts/philosophy.md`](../concepts/philosophy.md) for the full trade-off. When `true`, every `uv pip
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

`skip-if-0`/`skip-if-1` and `venv-patcher` are never guessed from the env's
directory layout (see "Explicit over implicit" in [`../concepts/philosophy.md`](../concepts/philosophy.md)) — with
neither `skip-if-0:` nor `skip-if-1:` given there is simply no skip check,
and the venv patcher runs only when `venv-patcher:` names its `patches:`
file explicitly.

## One venv, one interpreter

A venv holds exactly one interpreter, and the one it already has wins:

- An **existing venv's interpreter is authoritative and reused.** denver
  never silently rebuilds a venv because `python:` changed — recreating it
  would also silently discard everything installed into it.
- A **`python:` that contradicts the existing venv is an error**, naming
  both and the two ways out: `--force` to recreate that venv at the new
  version, or give the stage its own `venv:` so both interpreters can
  coexist.
- The same rule covers **several stages sharing one venv** (an unset or
  identical `venv:`). Sharing a venv means sharing its interpreter, so a
  later stage declaring a different `python:` is the same error rather than
  a special case — previously it was silently ignored.
- Comparison is a prefix, exactly as uv resolves it: `python: "3.12"`
  accepts a venv on 3.12.7, while `"3.12.3"` does not accept 3.12.4. A
  `python:` that isn't a plain release number (`cpython@3.12`, a path to an
  interpreter) is passed to uv untouched and not compared — denver does not
  re-implement uv's resolution to second-guess it.
- The one case denver *does* recreate a venv unasked: its base interpreter
  has disappeared (a distro upgrade moved `python3`, a uv-managed
  interpreter was pruned). Such a venv is broken rather than reusable, and
  there is no configured value it could be contradicting.

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
  bypasses every `skip-if-0:`/`skip-if-1:` script.
- **`--dry-run`** prints the `uv` commands (and the checksum/`freeze-to:`
  writes) instead of performing them; an existing venv is never removed. Two
  things still really happen, because the preview depends on them: each
  `skip-if-0:`/`skip-if-1:` script runs (its exit code is what decides
  whether an install would be shown at all), and each `$(...)` entry in
  `install-args:` runs (its output *is* part of the `uv pip install` line
  being shown).
