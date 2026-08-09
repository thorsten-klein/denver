# examples/simple-env

**The smallest possible denver environment: three `custom` stages and a shell
script. No Docker, no Conan, no Python packaging.**

## What it does

Running it prints three lines and drops you into a shell:

```console
$ denver examples/simple-env -- true
[print-vars-before] MYVAR= FOO= BAR=
[set-vars] sourcing custom.sh...
[print-vars-after] MYVAR=1 FOO=2 BAR=3
```

Three stages run in the order `stages:` lists them. The middle one sources
`custom.sh`, which exports `MYVAR`/`FOO`/`BAR`. The stage before it sees
nothing; the stage after it sees all three — and so does the final command.

## Why it exists

Two reasons, and the second is the important one.

**As a first environment to read.** It is the entire denver model at minimum
size: a folder with a `denver.yml`, an ordered `stages:` list, and each stage
handing something forward to the next. Nothing here needs a container, a
toolchain or a package manager, so it is the example to start with if the
Zephyr ones look intimidating. If your project's setup today is a `setup.sh`
that people are told to run by hand, this is the shape your `denver.yml`
would take.

**As the demonstration of `cmd:` vs `source:`.** These two keys look
interchangeable and are not, and getting it wrong fails in a way that is
genuinely confusing — so this env exists to make the difference visible:

- **`cmd:`** runs via `bash -c` in an *isolated subprocess*. Ordinary shell
  syntax works, but anything it exports dies with that subprocess. An
  `export FOO=1` in a `cmd:` is invisible to every later stage.
- **`source:`** *sources* the script into denver's own environment instead,
  so its exports fold into `ctx.env` and persist into every later stage and
  the final command.

The `print-vars-before` / `print-vars-after` pair is there purely so you can
see that boundary being crossed.

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
| `denver.yml` | Three `custom` stages; the middle one wires up `source:` |
| `custom.sh` | The sourced script — just three `export`s |

## Next

- [`doc/providers/custom.md`](../../doc/providers/custom.md) — the full
  `custom` key reference (`cmd`, `source`, `launcher`)
- [`doc/architecture.md`](../../doc/architecture.md) — `hooks:`, the global
  counterpart to a stage's own `source:`
