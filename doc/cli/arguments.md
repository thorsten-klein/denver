# Arguments

You have already used a handful of these in
[Quickstart](../quickstart/five-minutes.md) — `--scripts`, `--fast`,
`--skip`, `--dry-run`. This page is the full list.

denver's CLI is subcommand-based: `denver run <env> ...` is the normal entry
point, and every flag below except `--version`/`--license` belongs to it.
(`--version` and `--license` are top-level, before the subcommand, e.g.
`denver --version`. `denver complete` is the other subcommand — see
[Shell completion](completion.md) — and takes no flags of its own.)

`denver run --help` is always authoritative and lists *every* `run` flag;
the notes below are for the ones whose behavior isn't obvious from a
one-line description.

## Control the stages: Choosing what runs

- **`--until <stage>`** truncates the pipeline: every stage up to and
  including `<stage>` runs, everything after it is dropped — there's no
  "run only this one stage" flag, since a stage practically always needs
  the ones before it. The command (if any) still runs afterwards, in
  whatever partial environment those stages built.
- **`--skip <stage>`** removes individual stages from whatever `--until`
  left; repeatable. Skipping a wrapper stage (`--skip docker`) is how you
  run the stack directly on the host instead of relocating into a
  container.
- **`--scripts <name>`** runs every (filtered) stage's own `scripts:
  <name>:` list, then exits without running the rest of the pipeline — see
  "One-time host setup" in
  [Quickstart](../quickstart/five-minutes.md#one-time-host-setup) for an
  example. `<name>` is open-ended, not a fixed set of flags: a project can
  declare `scripts: migrate:` and run `denver run <env> --scripts migrate`
  without denver itself changing. Repeatable — each name's entries run in
  the order given. With no `<name>` (on any occurrence), it lists the names
  this env defines instead of running anything.

## Control the config: Change values for one run

- **`-c`/`--config KEY.PATH=VALUE`** overrides a single value in the merged
  `denver.toml` (e.g. `-c uv.python=3.13`); any missing parent section is
  created as an empty mapping. `KEY.PATH+=VALUE` appends to an existing
  list/string/number instead of replacing it (behaves like `=` if the path
  doesn't exist yet). `VALUE` is parsed as JSON when that succeeds, so
  `"true"`/`"3"`/`'["a", "b"]'` become their real type; anything that isn't
  valid JSON on its own (a bare word, an unquoted version string like
  `3.12.3`) is kept as a plain string. Repeatable; later `-c`s win when they
  target the same path.
- **`-cf`/`--config-file FILE`** overlays a whole TOML file on top of the
  env's `denver.toml`, using the same merge rules as `import:`. Repeatable,
  applied in the order given; `-c` overrides are applied last, on top of
  every `-cf` file.

Both follow the same merge rules as `import:`, explained in
[Configuration](../configuration/denver-toml.md).

## Control the speed: Trading speed against freshness

- **`--fast`** skips every provider's (re-)build step and only activates
  what a previous full run already built (each provider's own page under
  [Providers](../providers/uv.md) documents exactly what that means for
  it). Run once without `--fast` first — a provider dies with a clear
  message if what it needs isn't there yet.
- **`--force`** forces a provider to redo expensive work it would otherwise
  skip because nothing looked like it changed (again, see each provider's
  own page for specifics). Like `--ci` below, this is only ever set by
  the flag itself, never inherited from a same-named real environment
  variable.
- **`--ci`** swaps in narrower/faster args a provider judges appropriate
  for a CI runner (currently just zephyr's `west update`, adding a shallow-clone
  strategy on top of whatever `update-args:` already configures).

## Inspect an environment

- **`--show-config`** prints the fully merged configuration — `import:`
  chain resolved, overrides applied, every default filled in — then exits.
  The fastest way to understand an env you didn't write, and it needs no
  toolchain, no network and no Docker.
- **`--dry-run`** shows what each stage *would* do instead of doing it: no
  command is executed for its effect, no file is written, and the final
  command is printed rather than launched. Useful for answering "what does
  this env actually run?" without waiting for (or committing to) a real
  build. Every line is prefixed `[dry-run]`:

  | marker | meaning |
  | --- | --- |
  | `+` | a command that would run (skipped) |
  | `?` | a read-only query, **really run** — its output is what decides the commands below it |
  | `~` | a file or directory write that would happen (skipped) |
  | `.` | a script sourced into the environment, **really done** |
  | `!` | a note about what this preview cannot show |

  The two "really" rows are the deliberate limit. A dry run has to answer
  questions like *is the image already cached?*, *which conan home?*, *what
  does `west list` say?* to render the commands that follow — so those
  read-only queries execute, and scripts are still sourced (that is how
  denver computes the environment a command is rendered against). Two
  further consequences worth knowing: the preview reflects the state your
  machine is in *now*, so an already-built env legitimately shows fewer
  commands than a clean one (that is what a real run would do too); and a
  wrapper stage (`docker`) can't be previewed past its own boundary, since
  entering the container is itself one of the commands not being run —
  denver says so and points you at `--skip docker` to preview those stages
  on the host instead.

## Output and version

- **`-q`/`-qq`** are two quiet levels. `-q` silences info lines, `+ cmd`
  echoes, and build-tool subprocess output, but leaves each stage's own
  banner and "stage finished" summary visible. `-qq` additionally silences
  those too, so only the launched command's own output reaches the
  terminal. Errors are always reported.
- **`--version`** (top-level: `denver --version`, before any subcommand)
  prints the running denver's version and exits — derived
  from the checkout's git tags when denver runs from a checkout (script or
  editable install), otherwise from the installed package's metadata. A
  checkout ahead of its last tag reports as a development build of the
  release it is heading for (`1.1.0-17-gabc1234`). A `denver.toml` can
  require a minimum with `denver-version: ">=1.1.0"`, and is rejected up
  front by a denver older than that (see
  [Configuration](../configuration/denver-toml.md)).

Each stage's runtime is also appended to
`<DENVER_DIR>/.envs/<env>/performance.jsonl` as JSON Lines of Chrome Trace
Event Format events — concatenate them into a `{"traceEvents": [...]}`
document to load at chrome://tracing or https://ui.perfetto.dev.

```{note}
**Next:** [Shell completion](completion.md) — tab-complete subcommands, env
paths and flags with `denver complete`. Then
[Environment variables](environment-variables.md) — the two variables
denver itself reads, and where an environment's state lives on disk.
```
