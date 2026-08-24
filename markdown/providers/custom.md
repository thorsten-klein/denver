# custom provider

A `custom` stage is the escape hatch: an arbitrary shell command, for
whatever a project needs that doesn’t fit `uv`/`conan`/`zephyr`/`docker`.

```toml
[my-stage]
provider = "custom"
cmd = "echo hello"
```

(`provider:`/`description:`/`disabled:`/`depends-on:`/`scripts:`/`env:`/`env-prepend:`/`env-append:` are generic keys every stage has —
see “Generic stage keys” in [Configuration](../configuration/denver-toml.md). Everything below is specific to `custom`.)

## Key reference

- **`cmd`** — run via `bash -c` in an isolated subprocess, so ordinary
  shell syntax (pipes, `&&`, quoting, `$VAR` expansion against the current
  environment) works the same way it would on a command line — but
  anything it exports dies with that subprocess, denver never sees it.
- **`source`** — different: it names a script *sourced* (not run) right
  after `cmd`, so its exports fold into the environment and persist into
  every later stage and the final command. This is the way to make a
  `custom` stage hand environment variables forward, scoped to this one
  stage’s section rather than the global `hooks:` mechanism (see
  [Configuration](../configuration/denver-toml.md)).
- **`launcher`** — makes this stage a *wrapper*, the same way `docker` is
  one: instead of (only) doing its own work, it prepends its own script(s)
  ahead of whatever command would otherwise run. Each entry is a string,
  split on whitespace into its own tokens (plain whitespace splitting, no
  shell parsing), and every entry’s tokens land in order, ahead of the
  actual command:
  ```toml
  launcher = [
    "myscript.sh --",
    "otherscript.sh --",
  ]
  ```

  turns a resolved command of `<cmd>` into `myscript.sh -- otherscript.sh -- <cmd>`.

At least one of `cmd`/`source`/`launcher` must be given. `cmd:`/`source:`
(if also given) still run as usual during this stage’s own setup even when
`launcher:` is set — `launcher:` only changes what happens to the *final*
command.

## Worked example: bringing a prebuilt binary in by hand

The common shape for “download a release tarball and put it on `PATH`” uses
both keys, because installing and activating are two different jobs
([`examples/firmware-env`](https://github.com/thorsten-klein/denver/tree/develop/examples/firmware-env)’s `nvim-setup` stage):

```toml
[nvim-setup]
provider = "custom"
cmd = "bash ${DENVER_ENV_DIR}/nvim/install.sh"  # download, checksum, unpack
source = "nvim/activate.sh"                     # export PATH=...
```

- **`cmd:`** is the build step: an isolated subprocess is exactly right for
  it (it prints its progress, and it is correctly skipped by `--fast`), and
  it needs to export nothing. It must be idempotent by itself — denver
  fingerprints a `uv`/`conan` stage’s inputs, but it cannot know what an
  arbitrary command changed, so the script runs on every start and has to
  recognise its own previous result.
- **`source:`** is the activation: one `export PATH=`, sourced so it reaches
  every later stage and the final command. Keeping it out of `cmd:` is not
  style — an export in `cmd:` dies with that subprocess; and keeping the
  download out of `source:` matters just as much, since a sourced script
  also runs under `--fast` and `--dry-run`.
- **One script would work too**: check, download, unpack and `export PATH=`
  all in a single `source:` script, with no `cmd:` at all. The split buys
  visible download progress (a sourced script’s output is captured, since
  denver reads the resulting environment out of it) and `--fast`/`--dry-run`
  skipping the expensive half. A single sourced script must never `exit`,
  which would end denver’s sourcing shell before it reads the environment
  back.
- The two scripts share their pin (version, url, checksum, install prefix)
  via a third file both source, so they cannot drift apart. Unpack into
  `${DENVER_ENV_WORKDIR}` (denver’s per-env state dir) rather than
  `/usr/local/bin`: no root needed, no leaking into other environments, and
  deleting the env deletes the tool.

When there are several such tools, or a cache worth sharing between
environments, this is the point to move the job to
[`conan`](conan.md) — which does all of the above, per tool, for a url and a
checksum.

## Design notes

- **`cmd:` vs `source:`.** A `provider: custom` stage’s own `cmd:` never
  sees its own exports (it’s an isolated subprocess) — `source:` exists
  for exactly the case where a stage needs to hand something forward.
- **`--fast`.** An arbitrary command has no “already built, just activate”
  state denver can reason about, so `cmd:` is skipped entirely under
  `--fast`. `source:` still runs under `--fast` — it’s what later
  stages’/the final command’s environment depends on, not a build step, so
  skipping it would break the very propagation it exists for. `launcher:`
  is likewise never skipped under `--fast` — relocating the command isn’t
  a build step either.
- **`--dry-run`** prints `cmd:` instead of running it. `source:` is still
  sourced, for the same reason it survives `--fast`: its exports are what
  every later stage’s commands are rendered against, so skipping it would
  make the preview show `${...}` values a real run would never use.
