# examples/simple-env

**The smallest possible denver environment: three `custom` stages and a shell
script. No Docker, no Conan, no Python packaging.**

## What it does

Running it prints three lines and drops you into a shell:

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

Two kinds of line are mixed together there, and it matters which is which:

- The `-- [i/n] stage ...` lines and the boxed `INFO: env ... started` line
  are **denver's own** progress messages.
- The plain `[print-vars-before] ...` / `[set-vars] ...` /
  `[print-vars-after] ...` lines are **not from denver at all** — they are
  each stage's own `echo`, running as an ordinary shell command and printing
  to its own stdout exactly as it would outside denver.

Three stages run in the order `stages:` lists them. The middle one sources
`custom.sh`, which exports `MYVAR`/`FOO`/`BAR`. The stage before it sees
nothing; the stage after it sees all three — and so does the final command.

## Why it exists

As the demonstration of `cmd:` vs `source:`. These two keys look
interchangeable and are not, and getting it wrong fails in a way that is
genuinely confusing — so this env exists to make the difference visible:

- **`cmd:`** runs via `bash -c` in an *isolated subprocess*. Ordinary shell
  syntax works, but anything it exports dies with that subprocess. An
  `export FOO=1` in a `cmd:` is invisible to every later stage.
- **`source:`** *sources* the script into denver's own environment instead,
  so its exports fold into `ctx.env` and persist into every later stage and
  the final command.

The `print-vars-before` / `print-vars-after` pair is there purely so you can
see that boundary being crossed. It is also the entire denver model at
minimum size: a folder with a `denver.toml`, an ordered `stages:` list, and
each stage handing something forward to the next — the shape your own
`denver.toml` would take if today's setup is a `setup.sh` people run by
hand. See [Denver in 5 minutes](../../doc/quickstart/five-minutes.md) for a
full guided walkthrough of this env.

Note this is stage-scoped: `source:` belongs to the `set-vars` stage's own
section, which is different from the global `hooks:` mechanism that applies
to the whole env.

## Purpose in the test suite

This is the env CI uses for its **packaging smoke test**: after building and
installing the wheel, `ci.yml` runs `denver <abs path>/examples/simple-env
--show-config` against the installed entry point. It is well suited to that
because it needs no network, no daemon and no toolchain — if that command
works, the package is installed and importable. It also runs end-to-end in
the `Examples` workflow.

## Files

| File | What it is |
|---|---|
| `denver.toml` | Three `custom` stages; the middle one wires up `source:` |
| `custom.sh` | The sourced script — just three `export`s |

## Next

- [`doc/providers/custom.md`](../../doc/providers/custom.md) — the full
  `custom` key reference (`cmd`, `source`, `launcher`)
- [`doc/configuration/denver-toml.md`](../../doc/configuration/denver-toml.md) — `hooks:`, the global
  counterpart to a stage's own `source:`
