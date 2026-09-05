# denver.toml

## Overview

Setting up a dev environment usually means running a handful of tools in
the right order, each building on what the last one set up — get into the
right OS, install native toolchains, create a Python venv, fetch source
repos. denver makes that sequence declarative: maintain your environment in
a `denver.toml`, and `denver` will run it.

This page is the complete reference for that file and for the machinery
behind it — every top-level key, every generic stage key, and how a config
is resolved. If you worked through
[denver in 30 minutes](../quickstart/30-minutes.md), you have
already met most of it in context; this is where the remaining keys and the
exact rules live.

Terms used here (*environment*, *stage*, *step*, *provider*, *resolved
config*, ...) are defined once in the
[Glossary](../concepts/glossary.md); each provider's own config keys are
documented on its own page under [Providers](../providers/uv.md).

### denver.yml vs. denver.toml

`denver.yml`/`denver.yaml` is denver's default format: PyYAML is a required
dependency, so it always works, down to denver's `>=3.9` floor. `denver.toml`
is supported too, but only where `tomllib` is importable (stdlib only from
Python 3.11) — on an older interpreter it isn't there, and denver says so
with a clear error instead of silently misreading it. When a directory
holds more than one, denver picks in this order: `denver.yml`, then
`denver.yaml`, then `denver.toml`. Every example on this page is TOML, but
the schema is the same either way — only the syntax differs.

## Core model

An *environment* is a `denver.toml`; it declares an ordered list of *stages*
under `stages:`; each stage names a `provider:` type (`uv`, `conan`,
`zephyr`, `docker`, `download`, `git`, `custom`) plus some provider-specific keys in a
top-level section of the stage's own name.

A provider is a generic, reusable engine — all project specifics come from
the `denver.toml` itself, never from the provider's own code. Most
providers build a piece of the environment in place (create a venv,
install tools, update a workspace); one, `docker`, is different: instead
of building anything itself, it relocates the rest of the pipeline into a
container (see "Wrapper / relocation" below).

A stage id is only a label. The section must always declare its
`provider:` explicitly, even when the id happens to match a provider name —
no type is ever guessed from an id. That is what lets one environment run
two `uv` stages (say `uv` and `uv-zephyr`, targeting the same or
different venvs) at different points of the pipeline.

## The `denver.toml` schema

### Top-level keys

The following keys are recognised at the top level; everything else at that
level must be a stage id declared in `stages:` (anything else is an error —
see "Fail loud" in [`philosophy.md`](../concepts/philosophy.md)).

- **`version`** — the `denver.toml` schema version this file is written
  against. The only value this denver understands is `"1.0"`; any other
  value is rejected with a clear message rather than silently
  misinterpreted. Optional, but worth setting: it exists so a future,
  incompatible schema change can't quietly do the wrong thing to an old
  file. Compared as a string, so TOML's own numeric parsing (a bare `1.0`
  becoming a float) doesn't matter.
- **`denver-version`** — the minimum denver *tool* version this file needs,
  e.g. `denver-version = ">=1.0.4"`. See "Requiring a denver version" below.
- **`import`** — a list of environments (or config files) whose configuration
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
  be imported, never started directly; `denver run <that env>` then fails
  with an explanatory message. Deliberately *not* inherited: an env importing a
  `runnable: false` base is itself runnable unless it says otherwise.
- **`env`** — a mapping of environment variables to set for the whole
  environment (values go through `${...}` interpolation). Applied once,
  right after the `env` hook, before any stage runs. Entries are set one at a
  time, in the order written, so a later entry's `${...}` can reference an
  earlier one from the same map:
  ```yaml
  env:
    PROJECT_ROOT: "${DENVER_ENV_DIR}/.."
    PATH: "${PROJECT_ROOT}/tools:${PATH}"
  ```
- **`hooks`** — scripts sourced at fixed points in the pipeline. See "Hooks"
  below.
- **`extensions`** — own, project-local `Provider` subclasses to register
  alongside the built-ins (`uv`, `conan`, `zephyr`, `docker`, `download`, `git`,
  `custom`), no
  denver fork required. See "Extension providers" below.
- **`denver-custom-args`** — command-line flags of this environment's own, each one
  forwarded to argparse's `add_argument`. See "Environment-specific CLI
  arguments" below.
- **`download-auth`** — a list of `{ host, username, password, headers }`
  entries, the credentials the [`download`](../providers/download.md)
  provider sends per host. Top-level rather than per stage: a token belongs
  to a server, so every stage and every package fetching from that server is
  covered by the one entry. See "Authenticated downloads" in the download
  provider's page.

### Requiring a denver version

`version:` pins the *schema*; `denver-version:` pins the *tool*. Those are
two different questions, and only the second one has a good answer for the
common case: a purely additive change — a new provider key, a new flag —
never bumps the schema version, but a `denver.toml` relying on it still needs
a denver new enough to have it. Without `denver-version:`, running such a
file on an older denver fails somewhere deep inside a stage, or quietly does
something subtly different; with it, denver says so up front:

```toml
version = "1.0"
denver-version = ">=1.0.4"   # directly below version:, always
```

- The value is a version requirement, quoted (an unquoted `>=…` is not a
  valid TOML value at all). A bare version means "at least this one", so
  `denver-version = "1.0.4"` and `">=1.0.4"` are the same requirement.
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
  release — see "Releasing" in [`development.md`](../contributing/development.md). In the rare case where neither can answer (a source copy with
  no git history and no install at all), the requirement is reported as
  unverifiable — a warning, not a failure.
- A commit past a tag counts as newer than that tag (`1.0.3-2-gabc1234`
  satisfies `">=1.0.3"`), and a pre-release counts as older than its
  release (`1.1.0.dev3+g1234567` does not satisfy `">=1.1.0"`).

A denver too old to know the key at all (before it was introduced) rejects
the file with `unknown top-level key(s) denver-version` — different wording,
same conclusion: that denver is too old for this `denver.toml`.

### Generic stage keys

These keys may appear in *any* stage's section, whatever its provider:

- **`provider`** (**required**) — which provider engine runs this stage:
  `uv`, `conan`, `zephyr`, `docker`, `download`, `git` or `custom`.
- **`description`** — free text (a list of strings) with whatever notes are
  useful to whoever reads the config: what this stage is for, why it's
  configured this way. denver never reads it; it is only surfaced in
  `--show-config`.
- **`disabled`** — `true` opts this stage out of the normal pipeline, as if
  it had been `--skip`ped, without deleting its configuration. Must be a
  real boolean.
- **`depends-on`** — a list of other stage ids. If any of them is itself
  skipped this run — for *any* reason: `disabled: true`, `--until`/`--skip`,
  a `skip-on-success:`/`skip-on-failure:` check, or its own `depends-on:`
  cascade — this stage is skipped too, reported as
  `skipped (depends-on '<id>')`. This cascades transitively (A depends on B
  depends on C: C skipped skips both B and A). Every named id must be
  declared *earlier* than this stage in the top-level `stages:` list — a
  forward reference, a self-reference, or an id not declared at all is a
  config error at startup, not a runtime surprise.
- **`skip-on-success`**/**`skip-on-failure`** — each a list of scripts; when
  every script in the list exits `0` (`skip-on-success`) or exits exactly
  `1` (`skip-on-failure`), this stage's whole setup — its `pre-<stage>` hook
  included — is skipped for this run, as if it had been `--skip`ped for this
  one invocation. The two lists are independent: give either, both (each
  checked on its own group; either group being fully satisfied skips the
  stage), or neither (no skip check at all). `--force` bypasses both.
- **`scripts`** — the generic one-shot mechanism: `scripts: <name>: [...]`
  declares scripts run by `denver run <env> --scripts <name>` instead of the
  normal pipeline. See "Hooks and scripts" below.
- **`env`** — a mapping of environment variables to set once this stage's
  own setup is done, as `{ VAR = "value" }` (values go through `${...}`
  interpolation, so a value can read what this stage's own setup() just
  exported, or an earlier entry of this same map, entries being set in the
  order written). The per-stage counterpart of the top-level `env:` — the same
  key, one level down, in scope for one stage rather than the whole
  environment.
- **`env-prepend`** / **`env-append`** — what a stage contributes to an
  existing variable, as `{ VAR = "value" }`: each value is resolved like any
  other denver path (against the env dir, then imported base envs — see
  "Variable interpolation" below) and glued directly onto `ctx.env[VAR]` --
  no separator inserted -- in front of its current value for
  `env-prepend:` (the common case: a stage exists to provide a specific
  version of a tool, and appending would let whatever the OS already has on
  `PATH` win instead), behind it for `env-append:` (a fallback `MANPATH`, a
  low-priority `CMAKE_PREFIX_PATH`). If the result needs a separator (it
  usually does, for a `:`-joined variable like `PATH`), write it into the
  value yourself: a trailing `:` for `env-prepend:` (`"tools/bin:"`), a
  leading `:` for `env-append:` (`":share/man"`) -- denver never guesses
  one.

All three apply after this stage's own `setup()` (so a value can reference what
that just exported), --fast/--dry-run included -- this is activation, not a
build step -- and are skipped outright for a stage `disabled:`/
`skip-on-success:`/`skip-on-failure:`/`depends-on:` skips this run, the same
as everything else about that stage. Every provider gets them for free; `download`'s own
per-*package* `env-prepend:`/`env-append:` (see [`download`](../providers/download.md))
is the same glue-with-no-separator mechanism, just scoped to one package of
a stage that has several, each needing its own directory named — not
replaced by this stage-wide one.

Every other key in a stage's section must be one the stage's own provider
recognises — see that provider's page under [`providers/`](https://github.com/thorsten-klein/denver/tree/develop/doc/providers). An
unrecognised key is an error, not silently ignored.

### Variable interpolation

Any string value in a `denver.toml` may contain `${VAR}` or
`${VAR:-fallback}`, expanded against the environment denver is building
(so a variable exported by an earlier hook or stage is visible to a later
one). An unset variable with no fallback expands to the empty string.

Alongside the real environment, denver seeds a few built-ins of its own,
which always reflect the current run even if a stale variable of the same
name is already exported:

- **`DENVER_SRC_DIR`** — where denver's own code lives.
- **`DENVER_ENV_DIR`** — this environment's directory (the one holding its
  `denver.toml`). Also the variable denver *reads* as the `<env>` CLI
  argument's fallback when it's omitted.
- **`DENVER_ENV_NAME`** — that directory's name.
- **`DENVER_ENV_WORKDIR`** — denver's own working area for this environment
  (`<env dir>/.denver/<config file stem>/` by default): venv, install trees,
  fingerprints, logs, `performance.jsonl`. Per environment and never shared
  by default — overridable directly (`DENVER_ENV_WORKDIR`) — see "Where an
  environment's state lives" in the top-level
  [`README.md`](https://github.com/thorsten-klein/denver/blob/develop/README.md).
- **`DENVER_CACHE_DIR`** — the *shared* cache root (`~/.cache/denver` by
  default), offered for an env to point a tool's own download cache at, e.g.
  `env: {CONAN_HOME: "${DENVER_CACHE_DIR}/conan2"}`. Safe to share across
  envs and checkouts because the tools owning such caches lock them
  themselves; denver writes nothing there itself.
- **`SHELL_PROMPT_PREFIX`** — `(<env>) `: the text marking a shell as
  running inside this environment, so a prompt reads
  `(raspberry-pico) dev@host:~/ws$`. fish reads this natively from **fish
  4.8.0** onwards (see below).

These are exported into the environment too, so scripts, compose files and
the final command can read them as ordinary variables. `DENVER_ENV_DIR` —
the one variable here denver *reads* rather than computes — is documented
alongside them in the top-level
[`README.md`](https://github.com/thorsten-klein/denver/blob/develop/README.md).

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

Loading a `denver.toml` goes through a fixed sequence, and understanding it
explains a lot of otherwise-surprising behavior:

1. **`import:` chain.** Each `import:` entry points at another env (or
   directly at a config file); that file is loaded the same way, recursively,
   then merged in as the base *before* the importing file's own content is
   applied on top. A circular `import:` chain is an error.
2. **Merge rules.** Mappings merge key by key, recursively. A *list* is
   appended to, not replaced — a lower layer's entries plus this layer's
   own, in that order, so a derived env only needs to list what it *adds*.
   Prefix one entry with `!` to drop everything from lower layers, or use a
   bare `<overwrite>` entry to do the same as a pure marker (it's removed from
   the merged list, unlike `!foo` which keeps `foo`). A *string*
   works the same way: two layers disagreeing on the same string key (e.g.
   `uv.python = "3.11"` vs `"3.12"`) is treated as a likely mistake and is a
   hard error, unless the overriding value is explicitly prefixed with `!`
   (e.g. `python = "!3.12"`) to say "yes, replace it on purpose." For both,
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

`--show-config` prints the result of this whole sequence, as TOML, and
exits. It is the single best way to understand what an environment really
does, imports included.

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
  value is parsed as JSON when that succeeds, so `-c uv.no-index=true` sets
  a real boolean, not the string `"true"`; anything that isn't valid JSON on
  its own (a bare word, an unquoted version string like `3.12.3`) is kept as
  a plain string. Repeatable; later `-c`s win over earlier ones targeting
  the same path.
- **`-cf FILE`** overlays a whole TOML file on top of the env's own
  `denver.toml`, using the exact same merge rules as `import:`. Repeatable,
  applied in the order given.
- **Ordering**: every `-cf` file is applied first (in order), then every
  `-c` override, last — so `-c` always has the final word over `import:`
  and `-cf` alike.

Both compose with `--show-config`, so you can check what an override
actually does before running it for real.

## Command-line environment variables

`-e`/`--env NAME[=VALUE]` sets an environment variable for this run — the
"as if you'd exported it in your shell first" counterpart to `-c`'s config
overrides. Applied to denver's own process (`os.environ`), to every stage
and hook (`ctx.env`), and to the final command; repeatable, later entries
win over earlier ones of the same name, the same as `-c`. `NAME` alone (no
`=`) forwards `NAME`'s current value out of denver's own environment,
mirroring `docker run -e`'s own shorthand.

It always wins over the same name set by the env's own declarative `env:`
map (applied *before* any stage runs, right after the `env` hook — see
"Hooks and scripts"), the same way `-c` always wins over `import:`/`-cf`.

A wrapper stage that relocates into an actual container (`docker`) is a
fresh process with its own, separate environment, so `-e` values are handed
across that boundary explicitly two ways: as `--env NAME=VALUE` flags on the
re-invoked denver (see "Wrapper / relocation" below), and as `docker compose
run -e NAME=VALUE` flags on the container itself — the same mechanism that
already carries `DENVER_IN_CONTAINER`/`DENVER_RELOCATED` across (see
`docker.py`'s `_relocation_env`). A `custom` stage's `launcher:` needs
neither: its child simply inherits `ctx.env` like any other exec.

## Environment-specific CLI arguments

`denver-custom-args:` lets an env declare its own command-line flags (e.g.
`--board`, `--release`), each becoming a real argparse flag and a
`DENVER_ARG_<NAME>` variable — see
["An env's own flags: `denver-custom-args:`"](../cli/arguments.md#an-envs-own-flags-denver-custom-args)
in CLI Arguments for the full reference (key syntax, how the value reaches
`${...}` interpolation, how it survives a wrapper relocation).

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
declare `scripts: <name>: [...]`, and `denver run <env> --scripts <name>` runs
every stage's own `<name>` entries, then exits without doing anything else.
Nothing about `<name>` is fixed — `setup`, `login` and `clean` are only
conventions, recognised by denver just far enough to get a shorthand flag
each (`--setup`/`--login`/`--clean`, the last of which also removes the
env's state directory afterwards); any other name works exactly as well and
needs no change to denver. Which is why `denver run <env> --scripts`, with no
name at all, lists the names that env actually defines: they are unguessable
by design, and `scripts:` stacks across the whole `import:` chain, so reading
one file does not answer it either.

```
$ denver run examples/zephyr-devshell-4.3.1 --scripts
available --scripts names for env 'zephyr-devshell-4.3.1':
  setup        docker (1 script), zephyr (1 script)
``` This is where one-time host setup belongs: installing
Docker itself, `udev` rules for flashing a board, a registry login — things
that must not run on every start.

## Extension providers

The built-in providers (`uv`, `conan`, `zephyr`, `docker`, `download`, `git`,
`custom`) cover
the common cases, but a project may need its own — driving an internal
build tool or deploy step with the same `resolve_defaults`/`setup`/`wrap`
lifecycle a built-in provider gets, rather than squeezing it into a single
`custom: cmd:` line. `extensions: providers: dirs:` registers one without
maintaining a fork of denver:

```toml
[extensions.providers]
dirs = ["my_providers"]  # resolved like conan's base-classes: env dir, then imported base envs
```

Every `*.py` file directly inside each listed dir — except those whose name
starts with `_` — is imported and must define `PROVIDER`, a
`denver_providers.Provider` subclass:

```python
# my_providers/acme.py
from denver_providers import Provider


class AcmeProvider(Provider):
    name = "acme"  # the 'provider: acme' name a stage's section sets
    KEYS = ("target",)  # denver.toml keys this provider reads

    def setup(self, ctx):
        cfg = self.config_section(ctx)
        ctx.run(["acme-build", "--target", cfg["target"]])


PROVIDER = AcmeProvider
```

Once registered, any stage can set `provider: acme` exactly like a built-in
one:

```toml
stages = ["build"]

[build]
provider = "acme"
target = "release"
```

A provider too big for one file puts its shared code in a `_`-prefixed file
(`_helpers.py`, or an `__init__.py` making the dir a package): those are
skipped rather than required to be providers of their own. The dir itself
goes on `sys.path`, appended — never prepended, so an extension dir cannot
shadow the stdlib or denver's own modules — which is what makes
`import _helpers` work from a provider module next to it.

Registration happens once per resolved config, before any stage is
instantiated, so an extension provider is indistinguishable from a built-in
one everywhere else — `--show-config`, `--fast`, `-c` overrides, `import:`
layering (the `dirs:` list itself follows the normal list-merge rule, so a
derived env only needs to list the dirs it adds). A `name` colliding with an
existing provider (built-in or from another extension dir) is a hard error,
as is an unknown key under `extensions:` itself — the same "fail loud" rule
every other config mistake gets, never a silent override and never a typo
that quietly disables the whole mechanism.

Loading an extension provider runs its module's code, so an env's
`denver.toml` is only ever as trustworthy as the repository it lives in —
the same already-true statement as for `hooks:`, `custom: cmd:` and a
sourced `source:` script, not a new trust boundary.

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

Being opposites, `--fast` and `--force` are mutually exclusive and giving
both is an error. There is no sensible resolution to guess at: a provider
takes its `--fast` path before it ever looks at `--force`, so the pair would
otherwise mean "`--fast`, and the `--force` you typed did nothing".

Neither `--force` nor `--ci` is ever read from a real environment variable
— both only ever come from the flag itself, so behavior can't silently
change based on what happens to be exported in the calling shell.

Each provider's page under [`providers/`](https://github.com/thorsten-klein/denver/tree/develop/doc/providers) documents exactly what
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

- **read-only queries** — a `Context.run(..., query=True)` call exists so the
  provider can immediately branch on it (`docker image inspect`, `conan
  config home`, `west list`, a `skip-on-success:`/`skip-on-failure:` script's exit
  code). Skipping those would leave a dry run with nothing to decide with,
  and it would stop reflecting what a real run does. They are reported with
  a `?` marker, and a missing executable degrades to a failed query instead
  of aborting. `query` defaults to `capture` — a caller that also needs the
  real *output* back (not just the guarantee it ran) passes `capture=True`
  too; a caller like `skip-on-success:`/`skip-on-failure:` that only branches on the
  exit code passes `query=True` alone, so the script's own stdout/stderr
  stay live on the terminal on a real run.
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

One consequence for `depends-on:`: a setup stage's dependency on a wrapper
stage is checked inside the re-invoked *inner* process, which always sees
that wrapper as `--skip`ped (that's how the relocation works) — so such a
dependency is always treated as skipped there, regardless of whether the
wrapper actually ran on the host in the outer process. Depending on a
wrapper stage is rarely useful in practice; depend on another setup stage
instead when you need the cascade to reflect what actually happened.

### How the inner run knows where it is

Two separate questions, answered separately rather than by one guess:

- **"Did a wrapper relocate me?"** is denver's own bookkeeping, so it is
  *stated*, not detected: the relocating run sets `DENVER_RELOCATED` to the
  wrapper stage ids that put the inner process there. Being denver's own
  variable, it works for a wrapper that relocates into something which is
  not a container at all — a `custom` stage with a `launcher:` — which no
  amount of probing the filesystem could reveal. It is what stops a
  denver-forced `--skip` from being reported as if the user had typed it.
- **"Am I inside a container?"** is a fact about the machine — it decides
  whether an interpreter can be installed, whether an offline install makes
  sense, and which venv directory is used. A wrapper that relocates into a
  container sets `DENVER_IN_CONTAINER` so the inner run never has to infer
  it; failing that, denver probes `/.dockerenv`, `/run/.containerenv`, the
  `container` variable and `/run/systemd/container`, which covers a
  container someone started by hand. (Not `/proc/self/cgroup`: under cgroup
  v2 it commonly reads `0::/` either way, so it answers nothing.)

Being inside a container at all — however denver learned it — is enough to
stop a wrapper stage relocating again, deliberately without regard to
*which* env put you there: starting an env from inside a devshell builds
right there rather than starting a second container.

## One run per environment at a time

Every part of an environment's state is shared between concurrent runs, and
several steps *rebuild* rather than update: the conan provider wipes its whole
install tree before installing, and the uv provider removes and recreates a
venv whose requirements changed — potentially while another run is using
exactly that. There is no useful way to merge two such runs, so denver
serialises them with an exclusive lock on `<DENVER_ENV_WORKDIR>/.lock`.

A second run waits, saying whose run it is waiting for; `--no-wait` makes it
fail instead. The lock is never released explicitly: the descriptor is closed
by `execvpe`, so it lasts exactly as long as denver is mutating state and
drops the moment it hands over to your command. A long-lived devshell
therefore never holds it, and a wrapper relocation cannot deadlock against
itself — the outer process ceases to exist at `exec`, before the inner one
asks.

`--dry-run` takes no lock, since it mutates nothing. Where the filesystem
does not implement `flock` at all (some NFS and overlay mounts), denver warns
and continues rather than pretending the run is serialised.

## Quiet levels and --verbose

By default denver prints only the coarse `-- [i/n] stage 'id' (provider)`
progress trail plus whatever each stage's own build tool prints — its finer
detail (sub-step banners, performance timings, echoed commands) is off
unless asked for with `-v`/`--verbose`.

- **`-q`** silences denver's own output entirely (the progress trail
  included), but a stage's own build tool output still shows, so a
  long-running invocation stays legible.
- **`-qq`** additionally silences that too, so only the final launched
  command's own output reaches the terminal. `-q`/`-qq` always win over
  `-v` -- there is nothing left for it to add once either is given.
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
