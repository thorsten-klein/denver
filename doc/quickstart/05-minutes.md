# denver in 5 minutes

This page explains the most simple bundled environment: `examples/simple-env`.


## Have a look first

Open `examples/simple-env/denver.yml` in a file editor of your choice
(or [in your Browser](https://github.com/thorsten-klein/denver/tree/develop/examples/simple-env/denver.yml)),
or ask `denver` to show the resolved config with `denver run examples/simple-env --show-config`.

> [!NOTE]
> `--show-config` never runs anything — it only resolves the file (imports,
> defaults, everything) and prints the result. For this env the output looks
> almost the same as the file on disk, because there is nothing to resolve:
> no imports, no provider defaults to fill in. That will not stay true once
> you look at a bigger example, which is exactly why this flag is worth
> knowing early: it is always safe to run, and it always shows *exactly* what
> a real run would use.

You can see that it is a basic environment — consisting of three stages, each one a `provider: custom` section.
All stages run a shell command (via `cmd:`), one additionally sources a script (`source:`).

```yaml
stages:
- print-vars-before
- set-vars
- print-vars-after

print-vars-before:
  provider: custom
  cmd: 'echo "[print-vars-before] MYVAR=$MYVAR FOO=$FOO BAR=$BAR"'

set-vars:
  provider: custom
  cmd: 'echo "[set-vars] sourcing custom.sh..."'
  source: custom.sh

print-vars-after:
  provider: custom
  cmd: 'echo "[print-vars-after] MYVAR=$MYVAR FOO=$FOO BAR=$BAR"'
```

A closer look shows what each stage does:
- The first stage prints the variables and their current values (most likely empty because you don't have set them on your host system).
- The second stage prints some text, then sources `custom.sh` —
  [open that script](https://github.com/thorsten-klein/denver/tree/develop/examples/simple-env/custom.sh)
  and you'll see it sets those variables to specific values.
- The third stage prints the variables again, to showcase that the values from second stage are really applied.

## Run the environment

Let's run a command (`true`) in this environment:
```bash
denver run examples/simple-env -- true
```

You can see the following output:
```console
-- [1/3] stage 'print-vars-before' (custom)
INFO: custom[print-vars-before]: run cmd: echo "[print-vars-before] MYVAR=$MYVAR FOO=$FOO BAR=$BAR"
[print-vars-before] MYVAR= FOO= BAR=
-- [2/3] stage 'set-vars' (custom)
INFO: custom[set-vars]: run cmd: echo "[set-vars] sourcing custom.sh..."
[set-vars] sourcing custom.sh...
INFO: custom[set-vars]: source /home/klt1re/work/GIT/denver-ws/platform/denver/examples/simple-env/custom.sh
-- [3/3] stage 'print-vars-after' (custom)
INFO: custom[print-vars-after]: run cmd: echo "[print-vars-after] MYVAR=$MYVAR FOO=$FOO BAR=$BAR"
[print-vars-after] MYVAR=1 FOO=2 BAR=3
--------------------------------
| INFO: env simple-env started |
--------------------------------
```
- **denver's own lines**: `-- [i/n] stage ... (custom)` indicates which stage is
  currently running and which step.
- **the stage's own lines**: For example `[print-vars-before] MYVAR=...`,
  `[set-vars] sourcing custom.sh...`, `[print-vars-after] MYVAR=1 FOO=2 BAR=3`
  come from the `echo` command run in `cmd:`.

> [!NOTE]
> for quieter output, run with `-q` or `-qq`. Try it out!

To land in an interactive shell, either name it explicitly, or omit the command entirely
to fall back to the environment's default (which is `bash` by default):

```bash
denver run examples/simple-env -- bash
```

or

```bash
denver run examples/simple-env
```

## What just happened

Let's explain the `denver` naming for this specific example:

- The **environment** is the folder `examples/simple-env`, because that is
  where its `denver.yml` lives.
- Each **stage** is one step, and they run in the order the file lists
  them — `print-vars-before`, then `set-vars`, then `print-vars-after`.
- The **provider** behind all three is `custom`: run a command, and/or
  source a script. You configured what to run; denver runs it.

> [!NOTE]
> `cmd:` and `source:` look interchangeable, but they are different:
> - `cmd:` runs in its own subprocess, so an `export` inside it never leaves that subprocess.
> - `source:` folds its exported variables into the environment, so they are set for every later stage too.

See the [`custom` provider](../providers/custom.md) for the full picture.

## Pre-conditions for a real project

`simple-env` needed nothing but `denver`, since its `custom` stages just run
`echo` commands.

Real commands need real tools, and denver itself never installs anything
automatically — it only runs what a stage's `cmd:`/`source:` tells it to. A
stage whose tool is missing fails when it is run. In this case you can still
let denver handle that setup, though: something that must be
installed must be installed beforehand in a previous stage, or in a stage's
setup script section in the config, so you can install with:

```bash
denver run <env> --setup
```

See [`scripts:`](../configuration/denver-toml.md#hooks-and-scripts) in
Configuration for how that mechanism works.

## The handful of flags you'll use daily

Run the default command of an environment (`bash` by default)
```bash
denver run examples/simple-env
```

Run a specific command instead by passing `--`
```bash
denver run examples/simple-env -- echo inside
```

Print the full environment configuration
```bash
denver run examples/simple-env --show-config
```

Quieter output (`-q` or `-qq`)
```bash
denver run examples/simple-env -qq -- printenv MYVAR
```

See [CLI Arguments](../cli/arguments.md) for the full flag reference, or
[Shell completion](../cli/completion.md) to tab-complete all of it instead
of memorizing it.

> [!NOTE]
> **Next:** [denver in 30 minutes](30-minutes.md) — a bigger,
> more realistic example: four providers, a container, a real toolchain,
> and you build it yourself from an empty folder instead of just reading it.
