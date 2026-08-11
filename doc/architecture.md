# Architecture

## Overview

Setting up a dev environment usually means running a handful of tools in
the right order, each building on what the last one set up — get into the
right OS, install native toolchains, create a Python venv, fetch source
repos. denver makes that sequence declarative: maintain your environment in
a `denver.yml`, and `denver` will run it.

This document is the reference for that file and for the machinery behind
it. Terms used here (*environment*, *stage*, *step*, *provider*, *resolved
config*, ...) are defined once in [`glossary.md`](glossary.md); each
provider's own config keys are documented under [`providers/`](providers/).

## Core model

An *environment* is a `denver.yml`; it declares an ordered list of *stages*
under `stages:`; each stage names a `provider:` type (`uv`, `conan`,
`zephyr`, `docker`, `custom`) plus some provider-specific keys in a
top-level section of the stage's own name.

A provider is a generic, reusable engine — all project specifics come from
the `denver.yml` itself, never from the provider's own code. Most
providers build a piece of the environment in place (create a venv,
install tools, update a workspace); one, `docker`, is different: instead
of building anything itself, it relocates the rest of the pipeline into a
container (see "Wrapper / relocation" below).

A stage id is only a label. The section must always declare its
`provider:` explicitly, even when the id happens to match a provider name —
no type is ever guessed from an id. That is what lets one environment run
two `uv` stages (say `uv` and `uv-zephyr`, targeting the same or
different venvs) at different points of the pipeline.

## The `denver.yml` schema

### Top-level keys

Exactly eight keys are recognised at the top level; everything else at that
level must be a stage id declared in `stages:` (anything else is an error —
see "Fail loud" in [`philosophy.md`](philosophy.md)).

- **`version`** — the `denver.yml` schema version this file is written
  against. The only value this denver understands is `"1.0"`; any other
  value is rejected with a clear message rather than silently
  misinterpreted. Optional, but worth setting: it exists so a future,
  incompatible schema change can't quietly do the wrong thing to an old
  file. Compared as a string, so YAML parsing `1.0` as a float doesn't
  matter.
- **`denver-version`** — the minimum denver *tool* version this file needs,
  e.g. `denver-version: ">=1.0.4"`. See "Requiring a denver version" below.
- **`import`** — a list of environments (or YAML files) whose configuration
  is inherited as a base, before this file's own content is applied on top.
  See "Layering" below.
- **`stages`** — the ordered list of stage ids to run. This *is* the
  pipeline; order is significant, and each id must have a matching top-level
  section declaring its `provider:`.
- **`command`** — the default command to run once the environment is built,
  when none is given on the command line. If unset, denver falls back to the
  `docker:` section's `default-cmd:`, then `$SHELL`, then `bash`. A command
  passed on the CLI after `--` always wins over this.
- **`runnable`** — set to `false` to mark this file as a base meant only to
  be imported, never started directly; `denver <that env>` then fails with an
  explanatory message. Deliberately *not* inherited: an env importing a
  `runnable: false` base is itself runnable unless it says otherwise.
- **`env`** — a mapping of environment variables to set for the whole
  environment (values go through `${...}` interpolation). Applied once,
  right after the `env` hook, before any stage runs.
- **`hooks`** — scripts sourced at fixed points in the pipeline. See "Hooks"
  below.

### Requiring a denver version

`version:` pins the *schema*; `denver-version:` pins the *tool*. Those are
two different questions, and only the second one has a good answer for the
common case: a purely additive change — a new provider key, a new flag —
never bumps the schema version, but a `denver.yml` relying on it still needs
a denver new enough to have it. Without `denver-version:`, running such a
file on an older denver fails somewhere deep inside a stage, or quietly does
something subtly different; with it, denver says so up front:

```yaml
version: "1.0"
denver-version: ">=1.0.4"   # directly below version:, always
```

- The value is a version requirement, quoted (an unquoted `>=…` is not
  valid YAML). A bare version means "at least this one", so
  `denver-version: "1.0.4"` and `">=1.0.4"` are the same requirement.
- `>=`, `>`, `<=`, `<`, `==` and `!=` are all understood, and several
  comma-separated specifiers are ANDed: `">=1.0.4, <2"`.
- Requirements are checked against the *merged* config, like every other
  top-level key: an env inherits its base's requirement through `import:`.
  Two stacked layers stating a *different* requirement is the usual
  conflicting-strings error — prefix the overriding one with `!` to mean it
  (see "Layering" below).
- The version denver compares against is the one it is really running:
  from the checkout's git tags when denver runs out of a checkout (the
  plain `src/denver.py` script *and* an editable install, whose packaging
  metadata is frozen at install time and would go stale), otherwise from
  the installed distribution's metadata. `denver --version` prints the
  same value. A checkout whose tags haven't caught up with what the tree
  contains reports against `DEV_VERSION` instead (`1.1.0-17-gabc1234`), so
  running from source works at every commit rather than only after a
  release — see "Releasing" in [`development.md`](development.md). In the rare case where neither can answer (a source copy with
  no git history and no install at all), the requirement is reported as
  unverifiable — a warning, not a failure.
- A commit past a tag counts as newer than that tag (`1.0.3-2-gabc1234`
  satisfies `">=1.0.3"`), and a pre-release counts as older than its
  release (`1.1.0.dev3+g1234567` does not satisfy `">=1.1.0"`).

A denver too old to know the key at all (before it was introduced) rejects
the file with `unknown top-level key(s) denver-version` — different wording,
same conclusion: that denver is too old for this `denver.yml`.

### Editor support (JSON Schema)

`denver --schema` prints a JSON Schema describing this file, generated from
the very declarations denver validates against — each provider's own key
list — so it cannot drift from what denver actually enforces. A copy is
committed at [`schema/denver.schema.json`](../schema/denver.schema.json) and
a test fails if the two disagree.

Point an editor at it with a modeline at the top of a `denver.yml`:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/thorsten-klein/denver/develop/schema/denver.schema.json
```

Typing `provider: uv` then offers exactly the `uv` keys and flags anything
else — the same rule denver applies at run time, moved to the moment you
write the file.

Three things the schema deliberately does **not** enforce, because a
`denver.yml` is frequently a *layer* rather than a whole environment:
`provider:` is not required (a derived env inherits it through `import:`,
and a stacked section from the section it imports), a `${VAR}` template is
accepted wherever a boolean or enum is expected, and `deep_merge`'s `!` /
`<overwrite>` markers are accepted as values. Enforcing any of them would
flag valid files, which is worse than missing an error denver reports
clearly anyway.

### Generic stage keys

Four keys may appear in *any* stage's section, whatever its provider:

- **`provider`** (**required**) — which provider engine runs this stage:
  `uv`, `conan`, `zephyr`, `docker` or `custom`.
- **`description`** — free text (a list of strings) with whatever notes are
  useful to whoever reads the config: what this stage is for, why it's
  configured this way. denver never reads it; it is only surfaced in
  `--show-config`.
- **`disabled`** — `true` opts this stage out of the normal pipeline, as if
  it had been `--skip`ped, without deleting its configuration. Must be a
  real boolean.
- **`scripts`** — the generic one-shot mechanism: `scripts: <name>: [...]`
  declares scripts run by `denver <env> --run <name>` instead of the normal
  pipeline. See "Hooks and scripts" below.

Every other key in a stage's section must be one the stage's own provider
recognises — see that provider's page under [`providers/`](providers/). An
unrecognised key is an error, not silently ignored.

### Variable interpolation

Any string value in a `denver.yml` may contain `${VAR}` or
`${VAR:-fallback}`, expanded against the environment denver is building
(so a variable exported by an earlier hook or stage is visible to a later
one). An unset variable with no fallback expands to the empty string.

Alongside the real environment, denver seeds a few built-ins of its own,
which always reflect the current run even if a stale variable of the same
name is already exported:

- **`DENVER_SRC_DIR`** — where denver's own code lives.
- **`DENVER_ENV_DIR`** — this environment's directory (the one holding its
  `denver.yml`).
- **`DENVER_ENV_NAME`** — that directory's name.
- **`DENVER_ENV_WORKDIR`** — denver's own working area for this environment
  (`<DENVER_DIR>/.envs/<env>`): venvs, caches, logs, `performance.jsonl`.
- **`SHELL_PROMPT_PREFIX`** — `(<env>) `: the text marking a shell as
  running inside this environment, so a prompt reads
  `(raspberry-pico) dev@host:~/ws$`. fish reads this natively from **fish
  4.8.0** onwards (see below).

These are exported into the environment too, so scripts, compose files and
the final command can read them as ordinary variables. `DENVER_STATE_DIR` —
the one variable denver *reads* rather than sets — is documented in the
top-level [`../README.md`](../README.md).

### The prompt marker

denver marks the shell it starts by writing the prompt variables **the
shells themselves define** — it contributes to them, it does not own them:

| Variable | Shell | denver writes |
| --- | --- | --- |
| `PROMPT_COMMAND` | bash | its snippet, appended after whatever was there |
| `PROMPT` | zsh | `(<env>) %m%#` — short host, then `%` (or `#` for root) |
| `SHELL_PROMPT_PREFIX` | fish ≥ 4.8.0 | `(<env>) ` |

**`PS1` is deliberately not set.** An interactive bash re-reads its rc files
after denver execs it and assigns `PS1` outright, so anything denver put
there would be discarded before the user ever saw it. `PROMPT_COMMAND` is
bash's answer to exactly that — it runs after those rc files, before every
prompt — so that is where the marker goes for bash instead.

The `PROMPT_COMMAND` snippet is idempotent: it re-applies the prefix only if
it isn't already present (bash would otherwise grow `PS1` by one copy per
prompt), and a wrapper provider re-invoking denver inside a container never
appends a second copy.

One ordering subtlety, handled in `Context._prefix_prompt`: in zsh `PROMPT`
and `PS1` are the *same* parameter, so an inherited `PS1` would win over
denver's `PROMPT` if it happened to come later in `environ`. `PROMPT` is
therefore always written last, so zsh keeps the zsh-syntax value.

## Config resolution

Loading a `denver.yml` goes through a fixed sequence, and understanding it
explains a lot of otherwise-surprising behavior:

1. **`import:` chain.** Each `import:` entry points at another env (or
   directly at a YAML file); that file is loaded the same way, recursively,
   then merged in as the base *before* the importing file's own content is
   applied on top. A circular `import:` chain is an error.
2. **Merge rules.** Mappings merge key by key, recursively. A *list* is
   appended to, not replaced — a lower layer's entries plus this layer's
   own, in that order, so a derived env only needs to list what it *adds*.
   Prefix one entry with `!` to drop everything from lower layers, or use a
   bare `<overwrite>` entry to do the same as a pure marker (it's removed from
   the merged list, unlike `!foo` which keeps `foo`). A *string*
   works the same way: two layers disagreeing on the same string key (e.g.
   `uv.python: "3.11"` vs `"3.12"`) is treated as a likely mistake and is a
   hard error, unless the overriding value is explicitly prefixed with `!`
   (e.g. `python: "!3.12"`) to say "yes, replace it on purpose." For both,
   `!` only means anything when there's an actual lower-layer value to
   override — on a brand new key it's an ordinary character.
3. **Section-level `import:`** ("stacking") lets one stage's section pull
   its content from another env's section, instead of (or in addition to)
   inheriting the whole file — see "Layering" below.
4. **Every provider's defaults are filled in centrally**, before any stage
   actually runs — never guessed inside the stage itself at run time. The
   practical effect: `--show-config` always shows *exactly* the config a
   real run would use, values already defaulted and all. If a value looks
   wrong in `--show-config`, it will be exactly as wrong in the real run —
   there's no separate "what setup() actually decides" to go check.
5. **Unknown keys are an error**, not silently ignored — a typo'd key at
   the top level, or a key not recognised by a stage's own provider, dies
   immediately rather than quietly doing nothing.

`--show-config` prints the result of this whole sequence and exits. It is
the single best way to understand what an environment really does, imports
included.

## Layering

`import:` is what lets several environments share configuration without
copy-pasting it:

- **Whole-file `import:`** inherits another env's entire stack as a base,
  e.g. a project-specific env importing a shared base that already
  declares `stages:`, `docker:`, `conan:`, `uv:`. The importing file only
  needs to state what's actually different for it.
- **Section-level `import:`** stacks just one section from another env,
  e.g. a `docker:` section pulling in a shared base's `docker:` config
  without inheriting that base's *entire* stack. An entry can point at a
  specific section by name (`path:section`) instead of always the
  same-named one.

A base env that only exists to be imported should set `runnable: false`, so
starting it directly fails with an explanation instead of half-building
something nobody meant to run.

## Command-line overrides

- **`-c KEY.PATH=VALUE`** overrides one value in the resolved config,
  addressed by a dotted path (e.g. `-c uv.python=3.13`). Any missing
  parent section along the path is created automatically. `KEY.PATH+=VALUE`
  appends to an existing list/string/number instead of replacing it. The
  value is parsed as YAML, so `-c uv.no-index=true` sets a real boolean,
  not the string `"true"`. Repeatable; later `-c`s win over earlier ones
  targeting the same path.
- **`-cf FILE`** overlays a whole YAML file on top of the env's own
  `denver.yml`, using the exact same merge rules as `import:`. Repeatable,
  applied in the order given.
- **Ordering**: every `-cf` file is applied first (in order), then every
  `-c` override, last — so `-c` always has the final word over `import:`
  and `-cf` alike.

Both compose with `--show-config`, so you can check what an override
actually does before running it for real.

## Hooks and scripts

A hook is a script *sourced* (not just executed) at a fixed point, so its
exports become part of the environment everything after it runs in:

- **`env`** — once, before any stage; its exports apply to the whole
  environment.
- **`pre-<stage>` / `post-<stage>`** — around each individual stage.
- **`pre-cmd`** — right before the final command launches.

Each hook name can be declared by any layer in the `import:` chain
(`hooks: <name>:`, a list or a single path); a derived env's own hook never
silently replaces a base env's hook of the same name — both run, base
first.

Separately, `scripts:` is a generic, open-ended mechanism for one-shot
actions that are *not* part of the normal pipeline: any stage's section can
declare `scripts: <name>: [...]`, and `denver <env> --run <name>` runs
every stage's own `<name>` entries, then exits without doing anything else.
Nothing about `<name>` is fixed — `setup` and `login` are just conventions,
not flags of their own. This is where one-time host setup belongs: installing
Docker itself, `udev` rules for flashing a board, a registry login — things
that must not run on every start.

## Fast by default

Every stage that does real work computes a fingerprint (a checksum of its
relevant inputs — requirement files, recipe content, workspace state, ...)
and compares it against the last successful run's. Nothing changed →
nothing expensive re-runs.

- **`--fast`** skips every stage's (re-)build step entirely and only
  activates what a previous full run already built — it never looks at
  fingerprints, it just assumes there's nothing to do. Run once without
  `--fast` first; a stage dies with a clear message if it has nothing to
  activate yet.
- **`--force`** is the opposite extreme: bypass every fingerprint and
  redo the expensive work unconditionally, even if nothing looks changed.
- **`--ci`** swaps in narrower/faster args a stage judges appropriate for a
  CI runner instead of an interactive host (e.g. a shallow clone).

Neither `--force` nor `--ci` is ever read from a real environment variable
— both only ever come from the flag itself, so behavior can't silently
change based on what happens to be exported in the calling shell.

Each provider's page under [`providers/`](providers/) documents exactly what
`--fast` and `--force` mean for that provider.

## Previewing a run (`--dry-run`)

`--dry-run` runs the pipeline for its *description* instead of its effect:
every stage still runs in order and still resolves its own config, but each
command is printed rather than executed, each file write is reported rather
than performed, and the final command is printed rather than launched.

This works because providers never call `subprocess`/`pathlib` for effect
directly — every subprocess goes through `Context.run`/`Context.exec`, and
every write through `Context.write_text`/`mkdir`/`rmtree`/… . One flag on
`Context` is therefore enough to intercept all of it in one place, which is
also what makes the guarantee checkable: a provider reaching around those
helpers is a review-visible mistake, not a silent hole in `--dry-run`.

Two categories deliberately still execute, because the preview is derived
from them:

- **read-only queries** — a `Context.run(..., capture=True)` call exists for
  its *output*, which the provider immediately branches on (`docker image
  inspect`, `conan config home`, `west list`, a `skip-if:` script). Skipping
  those would leave a dry run with nothing to decide with, and it would stop
  reflecting what a real run does. They are reported with a `?` marker, and a
  missing executable degrades to a failed query instead of aborting.
- **sourced scripts** — `Context.source()` is how denver *computes* the
  environment. Without it, every rendered command would show empty `${...}`
  values and a PATH missing whatever an earlier stage put there.

Two limits follow from the design rather than from the implementation:

- A dry run describes what would happen **given the machine's current
  state**. An already-built env legitimately previews fewer commands than a
  clean one — that is exactly what a real run would do too (see "Fast by
  default" above).
- A **wrapper** stage can't be previewed past its own boundary. Setup stages
  run inside the container via a re-invocation (see
  [Wrapper / relocation](#wrapper--relocation) below), and
  that re-invocation is itself one of the commands not being run — passing
  `--dry-run` inward would mean really starting the wrapper, which is what
  the run promised not to do. denver prints an explicit `!` note there
  naming the `--skip <wrapper>` that previews those stages on the host.

## Stage filtering

- **`--until <stage>`** truncates the pipeline: every stage up to and
  including `<stage>` runs, everything after is dropped. There's no
  "run only this one stage" flag — a stage practically always needs the
  ones before it.
- **`--skip <stage>`** removes individual stages from whatever `--until`
  left; repeatable.
- **`disabled: true`** in a stage's own section opts it out by default,
  without `--skip` having to name it on every invocation.
- Naming a stage id that isn't in `stages:` is an error. A filtered-out
  stage's own section is left out of `--show-config`'s output too, along
  with its id in `stages:`.

## Wrapper / relocation

A wrapper stage (`docker`, or a `custom` stage with `launcher:`) doesn't
build the environment itself — it relocates the rest of the pipeline into
somewhere else (a container). Running an env that stacks a wrapper
builds/enters that container and re-invokes denver *inside* it with that
wrapper stage skipped, so the remaining stages build the environment there
instead of on the host. `--skip <wrapper stage>` (e.g. `--skip docker`) runs
the exact same stack directly on the host instead, skipping the relocation
entirely.

That symmetry is deliberate: the host and container paths run the same
stages from the same config, so "does it work without Docker?" is one flag
away rather than a separate code path.

## Quiet levels

- **`-q`** silences info lines, echoed commands, and the output of
  internally-run build tools — but each stage's own progress banner and
  "finished in Ns" summary still show, so a long-running invocation stays
  legible.
- **`-qq`** additionally silences the banners and summaries too, so only
  the final launched command's own output reaches the terminal.
- Errors are always reported, at any level.

## Performance tracing

Every stage's runtime is appended to
`<DENVER_ENV_WORKDIR>/performance.jsonl` as JSON Lines of Chrome Trace Event
Format events. Concatenate them into a `{"traceEvents": [...]}` document to
load in `chrome://tracing` or <https://ui.perfetto.dev> and see where a slow
first run actually spent its time.

A `--dry-run` records nothing here: no stage did its work, so its durations
would measure printing commands rather than running them, and mixing those
into the file would poison the very timings it exists to answer.
