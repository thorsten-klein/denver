# Arguments

You have already used a handful of these in
[Quickstart](../quickstart/05-minutes.md) — `--scripts`, `--fast`,
`--skip`, `--dry-run`. This page is the full list.

denver’s CLI is subcommand-based: `denver run <env> ...` is the normal entry
point, and every flag below except `--version`/`--license` belongs to it.
(`--version` and `--license` are top-level, before the subcommand, e.g.
`denver --version`. `denver complete` is the other subcommand — see
[Shell completion](completion.md) — and takes no flags of its own.)

`denver run --help` is always authoritative and lists *every* `run` flag;
the notes below are for the ones whose behavior isn’t obvious from a
one-line description.

## Control the stages: Choosing what runs

- **`--until <stage>`** truncates the pipeline: every stage up to and
  including `<stage>` runs, everything after it is dropped — there’s no
  “run only this one stage” flag, since a stage practically always needs
  the ones before it. The command (if any) still runs afterwards, in
  whatever partial environment those stages built.
- **`--skip <stage>`** removes individual stages from whatever `--until`
  left; repeatable. Skipping a wrapper stage (`--skip docker`) is how you
  run the stack directly on the host instead of relocating into a
  container.
- **`--scripts <name>`** runs every (filtered) stage’s own `scripts: <name>:` list, then exits without running the rest of the pipeline — see
  [“`scripts:`”](../configuration/denver-toml.md#hooks-and-scripts) in
  Configuration for an example (one-time host setup, e.g. installing Docker
  itself, is the usual use). `<name>` is open-ended, not a fixed set of flags: a project can
  declare `scripts: migrate:` and run `denver run <env> --scripts migrate`
  without denver itself changing. Repeatable — each name’s entries run in
  the order given. With no `<name>` (on any occurrence), it lists the names
  this env defines instead of running anything. If `<name>` isn’t declared
  by any (filtered) stage, denver logs `no '<name>' scripts to run for env '<env>'` at info level rather than exiting silently.
- **`--setup`**/**`--login`** are shorthand for `--scripts setup`/`--scripts login` — the two conventional names most envs use. They share `--scripts`’
  ordering and repeat rules, so `--login --setup` runs `login` then `setup`,
  and either can still be combined with a plain `--scripts <name>`.

## Control the config: Change values for one run

- **`-c`/`--config KEY.PATH=VALUE`** overrides a single value in the merged
  `denver.toml` (e.g. `-c uv.python=3.13`); any missing parent section is
  created as an empty mapping. `KEY.PATH+=VALUE` appends to an existing
  list/string/number instead of replacing it (behaves like `=` if the path
  doesn’t exist yet). `VALUE` is parsed as JSON when that succeeds, so
  `"true"`/`"3"`/`'["a", "b"]'` become their real type; anything that isn’t
  valid JSON on its own (a bare word, an unquoted version string like
  `3.12.3`) is kept as a plain string. Repeatable; later `-c`s win when they
  target the same path.
- **`-cf`/`--config-file FILE`** overlays a whole TOML file on top of the
  env’s `denver.toml`, using the same merge rules as `import:`. Repeatable,
  applied in the order given; `-c` overrides are applied last, on top of
  every `-cf` file.

Both follow the same merge rules as `import:`, explained in
[Configuration](../configuration/denver-toml.md).

## An env’s own flags: `denver-custom-args:`

`-c` can set *anything*, which is exactly why it is a poor fit for the one
or two knobs an env actually wants to offer (“which board?”, “debug or
release?”): its users have to know the dotted path, nothing shows up in
`--help`, and a typo just becomes a new config key instead of an error.

`denver-custom-args:`, in the env’s own `denver.toml`, declares those knobs
as real flags instead. Each entry is one `parser.add_argument()` call:
`flags:` names the flag (a string, or a list to give it aliases), and
**every other key is forwarded verbatim as a keyword argument** — so an env
gets argparse’s whole vocabulary (`help:`, `default:`, `action:`, `nargs:`,
`choices:`, `required:`, `metavar:`, `dest:`, …) without denver
re-inventing, or restricting, any of it:

```toml
[[denver-custom-args]]
flags = ["--board", "-b"]
default = "nrf52840dk"
help = "which board to build for"

[[denver-custom-args]]
flags = ["--release"]
action = "store_true"
help = "build with optimisations"
```

```bash
denver run my-project --board nrf5340dk --release -- west build
```

What the user passed is exported as **`DENVER_ARG_<DEST>`**, where `<DEST>`
is argparse’s own destination name uppercased (`--board` → `DENVER_ARG_BOARD`,
`--build-type` → `DENVER_ARG_BUILD_TYPE`, or whatever an explicit `dest:`
says). That is one variable in the environment denver is building, so it
reaches everything through the same mechanisms as any other variable —
`${...}` interpolation in the same `denver.toml`, hooks, `scripts:`, and the
final command:

```toml
[zephyr-build]
provider = "custom"
cmd = "west build -b ${DENVER_ARG_BOARD}"
```

The value is always a string, since an environment variable is:

- `action: store_true`/`store_false` → `"1"` / `"0"`.
- a multi-value flag (`nargs:`, `action: append`) → its items, space-joined.
- a flag with no `default:` that wasn’t given → **not exported at all**,
  rather than exported empty — so `${DENVER_ARG_BOARD:-nrf52840dk}` still
  falls back the way it reads.

For the same reason, `type:` is rejected: argparse’s `type=` is a callable,
which TOML cannot express, and every value ends up a string anyway. Use
`choices:` (argparse validates it, and lists it in `--help`) or an
`action:`.

A few more properties worth knowing:

- The flags are ordinary argparse flags, so `denver run <env> --help` lists
  them alongside denver’s own, and a mistyped one is argparse’s usual
  `usage:`/`error:` on stderr — never a silently ignored token.
- Write them **after** `<env>` (`denver run my-project --board x`): the flags
  an env declares live in the very file `<env>` names, so denver has to
  resolve `<env>` before it can know them.
- A flag that would collide with one of denver’s own (`--force`, or a `dest:`
  of `ci`) is a hard error, not a silent override.
- `denver-custom-args:` is a list, so it follows the normal list-merge rule: an env
  inherits every flag its `import:` chain declares and adds its own (see
  “Layering” in [Configuration](../configuration/denver-toml.md)).
- They survive a wrapper relocation: the tokens are re-passed to the denver
  re-invoked inside the container, which would otherwise only see each
  flag’s `default:` (see “Wrapper / relocation” in
  [Configuration](../configuration/denver-toml.md)).

## Control the speed: Trading speed against freshness

- **`--fast`** skips every provider’s (re-)build step and only activates
  what a previous full run already built (each provider’s own page under
  [Providers](../providers/uv.md) documents exactly what that means for
  it). Run once without `--fast` first — a provider dies with a clear
  message if what it needs isn’t there yet.
- **`--force`** forces a provider to redo expensive work it would otherwise
  skip because nothing looked like it changed (again, see each provider’s
  own page for specifics). Like `--ci` below, this is only ever set by
  the flag itself, never inherited from a same-named real environment
  variable.
- **`--ci`** swaps in narrower/faster args a provider judges appropriate
  for a CI runner (currently just zephyr’s `west update`, adding a shallow-clone
  strategy on top of whatever `update-args:` already configures).

## Inspect an environment

- **`--show-config`** prints the fully merged configuration — `import:`
  chain resolved, overrides applied, every default filled in — then exits,
  showing only the keys that actually carry a value (whatever the env itself
  configured, or a provider default computed from it); a key nothing ever
  set is left out rather than shown as unset. The fastest way to understand
  an env you didn’t write, and it needs no toolchain, no network and no
  Docker.
- **`--show-config-full`** is `--show-config` plus every key left unset,
  each shown as a commented-out `# key = null` line (TOML has no `null`) —
  the complete key reference for a stage’s own section, not just what this
  particular env happens to set.

Both are syntax-highlighted (table headers, keys, strings, numbers/booleans
and `# key = null` comments each their own color) whenever stdout looks like
a real terminal — auto-detected, off the moment output is piped or
redirected, so a golden-file snapshot, `| grep`, or a redirect into a file
all still get plain text. Two environment variables override the
auto-detection, the same convention most CLI tools use:
[`NO_COLOR`](https://no-color.org) (any non-empty value) always forces it
off, and `FORCE_COLOR` forces it on even when piped (e.g. into `less -R`).

- **`--dry-run`** shows what each stage *would* do instead of doing it: no
  command is executed for its effect, no file is written, and the final
  command is printed rather than launched. Useful for answering “what does
  this env actually run?” without waiting for (or committing to) a real
  build. Every line is tagged `[dry-run <marker>]`, each marker in its own
  color (a legend stating the same key prints once, up front):

  | marker   | color   | meaning                                                                              |
  |----------|---------|--------------------------------------------------------------------------------------|
  | `+`      | green   | a command that would run (skipped)                                                   |
  | `?`      | cyan    | a read-only query, **really run** — its output is what decides the commands below it |
  | `~`      | yellow  | a file or directory write that would happen (skipped)                                |
  | `.`      | blue    | a script sourced into the environment, **really done**                               |
  | `!`      | red     | a note about what this preview cannot show                                           |

  The two “really” rows are the deliberate limit. A dry run has to answer
  questions like *is the image already cached?*, *which conan home?*, *what
  does `west list` say?* to render the commands that follow — so those
  read-only queries execute, and scripts are still sourced (that is how
  denver computes the environment a command is rendered against). Two
  further consequences worth knowing: the preview reflects the state your
  machine is in *now*, so an already-built env legitimately shows fewer
  commands than a clean one (that is what a real run would do too); and a
  wrapper stage (`docker`) can’t be previewed past its own boundary, since
  entering the container is itself one of the commands not being run —
  denver says so and points you at `--skip docker` to preview those stages
  on the host instead.

## Hand the built env to something else

- **`--export-env <file>`** writes what denver itself changed in the
  environment as `export KEY=VALUE` lines to `<file>`, right before
  launching the final command — for bash or zsh (not fish) to source.
  Useful when something other than that final command needs the same env:
  a container’s interactive terminals, say, started fresh by an IDE and
  unaware denver ever ran. Carried along automatically into a docker
  wrapper’s own reinvocation, so it still reaches the process that
  actually builds the env rather than the outer one that only relocates
  into the container.

  Only variables that differ from what this process already had are
  written — a plain dump of the whole (inherited-plus-built) environment
  would re-assert a hundred unrelated variables (`SSH_AUTH_SOCK`, say) that
  the sourcing shell already has its own, possibly different, value for.
  A variable denver only prepended or appended to (`PATH` via a provider’s
  own setup, most commonly) is written as `prefix"$VAR"` or `"$VAR"suffix`
  rather than the whole resolved value, so it composes with whatever that
  variable already is in the sourcing shell instead of overwriting it
  outright. A value set with `-e`/`--env` is always written, even if it
  happens to match what the process already had.

## Output and version

By default denver prints only the coarse progress trail — one line per
stage (`-- [i/n] stage 'id' (provider)` or its “skipped by …” reason) —
plus whatever each stage’s own build tool prints on its own. denver’s own
finer detail (sub-step banners, performance timings, the `+ cmd` echo of
every command it runs) is off unless asked for with `-v`/`--verbose`.

- **`-q`/`-qq`** are two quiet levels. `-q` silences denver’s own output
  entirely (the progress trail included), but leaves each stage’s own build
  tool output showing — that’s usually still what you want to watch. `-qq`
  additionally discards that too, so only the launched command’s own output
  reaches the terminal. `-q`/`-qq` always win over `-v`: there is nothing
  left for `-v` to add once either is given. Errors are always reported.
- **`-v`/`--verbose`** turns on denver’s own diagnostic detail, hidden by
  default: each stage’s finer sub-step banners (e.g. a `custom` stage’s
  `cmd`/`source`, a `uv` stage’s `install`/`activate`), the per-stage
  “finished in Ns” line and the closing stage-timing summary (both in blue),
  the env’s own “started in Ns” duration, and the `+ cmd` echo ahead of
  every command denver runs (including the final launched command’s own `+ exec: cmd` line). Silenced by `-q`/`-qq` like everything else denver
  prints.
- **`--version`** (top-level: `denver --version`, before any subcommand)
  prints the running denver’s version and exits — derived
  from the checkout’s git tags when denver runs from a checkout (script or
  editable install), otherwise from the installed package’s metadata. A
  checkout ahead of its last tag reports as a development build of the
  release it is heading for (`1.1.0-17-gabc1234`). A `denver.toml` can
  require a minimum with `denver-version: ">=1.1.0"`, and is rejected up
  front by a denver older than that (see
  [Configuration](../configuration/denver-toml.md)).

Each stage’s runtime is also appended to
`$DENVER_ENV_WORKDIR/performance.jsonl` as JSON Lines of Chrome Trace
Event Format events — concatenate them into a `{"traceEvents": [...]}`
document to load at chrome://tracing or https://ui.perfetto.dev.

> **Note**

> **Next:** [Shell completion](completion.md) — tab-complete subcommands, env
> paths and flags with `denver complete`. Then
> [Environment variables](environment-variables.md) — the two variables
> denver itself reads, and where an environment’s state lives on disk.
