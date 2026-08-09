# custom provider

A `custom` stage is the escape hatch: an arbitrary shell command, for
whatever a project needs that doesn't fit `uv`/`conan`/`zephyr`/`docker`.

```yaml
my-stage:
  provider: custom
  cmd: "echo hello"
```

(`provider:`/`description:`/`disabled:`/`scripts:` are generic keys every stage has —
see "Generic stage keys" in [`../architecture.md`](../architecture.md). Everything below is specific to `custom`.)

## Key reference

- **`cmd`** — run via `bash -c` in an isolated subprocess, so ordinary
  shell syntax (pipes, `&&`, quoting, `$VAR` expansion against the current
  environment) works the same way it would on a command line — but
  anything it exports dies with that subprocess, denver never sees it.
- **`source`** — different: it names a script *sourced* (not run) right
  after `cmd`, so its exports fold into the environment and persist into
  every later stage and the final command. This is the way to make a
  `custom` stage hand environment variables forward, scoped to this one
  stage's section rather than the global `hooks:` mechanism (see
  [`../architecture.md`](../architecture.md)).
- **`launcher`** — makes this stage a *wrapper*, the same way `docker` is
  one: instead of (only) doing its own work, it prepends its own script(s)
  ahead of whatever command would otherwise run. Each entry is a string,
  split on whitespace into its own tokens (plain whitespace splitting, no
  shell parsing), and every entry's tokens land in order, ahead of the
  actual command:

  ```yaml
  launcher:
  - myscript.sh --
  - otherscript.sh --
  ```

  turns a resolved command of `<cmd>` into `myscript.sh -- otherscript.sh
  -- <cmd>`.

At least one of `cmd`/`source`/`launcher` must be given. `cmd:`/`source:`
(if also given) still run as usual during this stage's own setup even when
`launcher:` is set — `launcher:` only changes what happens to the *final*
command.

## Design notes

- **`cmd:` vs `source:`.** A `provider: custom` stage's own `cmd:` never
  sees its own exports (it's an isolated subprocess) — `source:` exists
  for exactly the case where a stage needs to hand something forward.
- **`--fast`.** An arbitrary command has no "already built, just activate"
  state denver can reason about, so `cmd:` is skipped entirely under
  `--fast`. `source:` still runs under `--fast` — it's what later
  stages'/the final command's environment depends on, not a build step, so
  skipping it would break the very propagation it exists for. `launcher:`
  is likewise never skipped under `--fast` — relocating the command isn't
  a build step either.
