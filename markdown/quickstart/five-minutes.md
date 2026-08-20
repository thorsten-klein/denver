# denver in 5 minutes

This page runs a real environment end to end: `examples/simple-env`, three
small steps and nothing else. No Docker, no compiler, no package manager —
just Python, which you already need to run denver at all. That makes it the
fastest way to see the model working before you look at a bigger, more
realistic example.

## Have a look first

Open [`examples/simple-env/denver.toml`](https://github.com/thorsten-klein/denver/tree/develop/examples/simple-env/denver.toml).
It is short — three stages, each one a `provider: custom` section running a
shell command. Read it before running anything; the rest of this page just
explains what you are looking at.

Then ask denver what it actually resolved to:

```bash
denver run examples/simple-env --show-config
```

```console
$ denver run examples/simple-env --show-config
version = "1.0"
denver-version = ">=1.1.0"
stages = [
  "print-vars-before",
  "set-vars",
  "print-vars-after",
]

[print-vars-before]
provider = "custom"
cmd = '''
echo "[print-vars-before] MYVAR=$MYVAR FOO=$FOO BAR=$BAR"
'''

[set-vars]
provider = "custom"
cmd = '''
echo "[set-vars] sourcing custom.sh..."
'''
source = "custom.sh"

[print-vars-after]
provider = "custom"
cmd = '''
echo "[print-vars-after] MYVAR=$MYVAR FOO=$FOO BAR=$BAR"
'''
```

`--show-config` never runs anything — it only resolves the file (imports,
defaults, everything) and prints the result. For this env the output looks
almost the same as the file on disk, because there is nothing to resolve:
no imports, no provider defaults to fill in. That will not stay true once
you look at a bigger example, which is exactly why this flag is worth
knowing early: it is always safe to run, and it always shows *exactly* what
a real run would use.

## Preview it: `--dry-run`

```bash
denver run examples/simple-env --dry-run
```

Read this output carefully, because it can look like nothing happened:

```console
$ denver run examples/simple-env --dry-run
[dry-run] no command below is executed for its effect. Legend:
[dry-run +]  command that would run
[dry-run ?]  read-only query, really run (its output decides what follows)
[dry-run ~]  file/directory write that would happen
[dry-run .]  script sourced into the environment, really done
[dry-run !]  note about what this preview cannot show
-- [1/3] stage 'print-vars-before' (custom)
[dry-run +] bash -c 'echo "[print-vars-before] MYVAR=$MYVAR FOO=$FOO BAR=$BAR"'
-- [2/3] stage 'set-vars' (custom)
[dry-run +] bash -c 'echo "[set-vars] sourcing custom.sh..."'
[dry-run .] .../examples/simple-env/custom.sh
-- [3/3] stage 'print-vars-after' (custom)
[dry-run +] bash -c 'echo "[print-vars-after] MYVAR=$MYVAR FOO=$FOO BAR=$BAR"'
| INFO: env simple-env NOT started (--dry-run) |
```

**Nothing is missing.** `--dry-run` really does not run any `cmd:` — that is
the entire point of the flag, so you can preview a build without doing it.
Every `[dry-run +] ...` line is a command that *would* run for real; none of
them actually ran, so you never see `[print-vars-before] MYVAR=...` the way
a real run prints it. Only `[dry-run .]` (`source:`) really executes, because
denver needs its exports to render the commands after it correctly — see
[Previewing a run](../configuration/denver-toml.md#previewing-a-run-dry-run)
for exactly which two things a dry run really does, and why.

## Run it for real

```bash
denver run examples/simple-env -- true
```

```console
$ denver run examples/simple-env -- true
-- [1/3] stage 'print-vars-before' (custom)
[print-vars-before] MYVAR= FOO= BAR=
-- [2/3] stage 'set-vars' (custom)
[set-vars] sourcing custom.sh...
-- [3/3] stage 'print-vars-after' (custom)
[print-vars-after] MYVAR=1 FOO=2 BAR=3
--------------------------------
| INFO: env simple-env started |
--------------------------------
```

Two different things printed those lines, and it is worth being able to
tell them apart on sight:

- **denver’s own lines**: `-- [i/n] stage ... (custom)` (which stage is
  running) and the boxed `INFO: env ... started` at the end. These are
  denver telling you what it is doing.
- **the stage’s own lines**: `[print-vars-before] MYVAR=...`,
  `[set-vars] sourcing custom.sh...`, `[print-vars-after] MYVAR=1 FOO=2 BAR=3`. These come from the `echo` each stage’s `cmd:` actually ran — the
  same text you would get typing that `echo` yourself. denver did not write
  a word of it.

That distinction matters for any real env: a stage’s own build tool
(`pip`, `cmake`, `docker build`, …) prints its *own* output the same way,
mixed in with denver’s progress lines around it.

Read the three lines and you can see stage-to-stage handoff happening:
`print-vars-before` sees nothing (`MYVAR=` is empty), `set-vars` sources
`custom.sh` (three `export`s), and `print-vars-after` sees all three. The
`true` after `--` was the command denver ran once the env was built — the
same way you would tell it to run a real program instead.

## What just happened

Three words from
[What is a denver environment?](../introduction/index.md#what-is-a-denver-environment),
now with something concrete attached to them:

- The **environment** is the folder `examples/simple-env`, because that is
  where its `denver.toml` lives.
- Each **stage** is one step, and they ran in the order the file lists
  them — `print-vars-before`, then `set-vars`, then `print-vars-after`.
- The **provider** behind all three is `custom`: run a command, and/or
  source a script. You configured what to run; denver ran it.

`cmd:` and `source:` look interchangeable and are not — that difference is
the whole reason this env exists. `cmd:` runs in its own subprocess, so an
`export` inside it never leaves that subprocess. `source:` folds its
exports into the environment every later stage (and the final command) can
see. See the [`custom` provider](../providers/custom.md) for the full
picture, or
[`examples/simple-env/README.md`](https://github.com/thorsten-klein/denver/tree/develop/examples/simple-env)
for this exact env explained a second way.

## Pre-conditions for a real project

`simple-env` needed nothing but Python, because a `custom` stage just runs
whatever shell command you give it. A real environment usually names one of
denver’s other providers, and each of those expects its own tool to already
be on the machine:

| Provider   | Needs                                                                                                                                                           |
|------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `uv`       | [`uv`](https://docs.astral.sh/uv/getting-started/installation/) — see [Install denver](../introduction/install.md#installing-uv) if you don’t have it yet       |
| `conan`    | `conan` — usually installed by an earlier `uv` stage                                                                                                            |
| `zephyr`   | `west` — usually installed by an earlier `uv` stage                                                                                                             |
| `docker`   | `docker` with the Compose plugin (v2, `docker compose ...`), daemon reachable for your user — see [Setting up Docker](../providers/docker.md#setting-up-docker) |
| `custom`   | whatever your own command calls                                                                                                                                 |

denver itself never installs any of these — only the tools its providers
drive. A stage whose tool is missing fails up front with a clear message,
naming what it looked for.

Something that must be installed on the *host* before anything else works
(Docker itself, a `udev` rule, …) usually belongs in an env’s own
`scripts: setup:` — run once, by hand, not on every start:

```bash
denver run <env> --scripts setup
```

See [`scripts:`](../configuration/denver-toml.md#hooks-and-scripts) in
Configuration for how that mechanism works.

## The handful of flags you’ll use daily

```bash
# run one command instead of opening a shell -- everything after
# '--' is passed through untouched
denver run examples/simple-env -- echo inside

# preview what would run, without running it
denver run examples/simple-env --dry-run

# print the resolved config and exit
denver run examples/simple-env --show-config

# quieter output (-q keeps each stage's own command output but
# silences denver's own; -qq silences that too)
denver run examples/simple-env -q -- echo inside
```

See [CLI Arguments](../cli/arguments.md) for the full flag reference, or
[Shell completion](../cli/completion.md) to tab-complete all of it instead
of memorizing it.

> **Note**

> **Next:** [denver in 15 minutes](creating-environments.md) — a bigger,
> more realistic example: four providers, a container, a real toolchain,
> and you build it yourself from an empty folder instead of just reading it.
