# zephyr provider

A `zephyr` stage manages a [West](https://docs.zephyrproject.org/latest/develop/west/index.html)
workspace (Zephyr RTOS).

```toml
[my-zephyr-stage]
provider = "zephyr"
```

(`provider:`/`description:`/`disabled:`/`depends-on:`/`scripts:`/`env:`/`env-prepend:`/`env-append:` are generic keys every stage has —
see "Generic stage keys" in [Configuration](../configuration/denver-toml.md). Everything below is specific to `zephyr`.)

## Requires

**`west` must be installed** wherever this stage runs — denver never
installs it. In practice an earlier `uv` stage provides it, by listing
`west` in its `requirements:`.

## Key reference

- **`exe`** (default: `"west"`) — the `west` executable, resolved on `PATH`.
- **`west-yml`** (default: `<WEST_TOPDIR>/west.yml`) — the manifest.
  `WEST_TOPDIR` (a zephyr concept, not a denver built-in) is discovered by
  walking up from the env dir: the nearest enclosing `.west`, or failing
  that, the *outermost* enclosing `.git`. Already-exported `WEST_TOPDIR`
  (e.g. set by the user, or by an outer denver run before re-invoking
  inside docker) wins over this discovery.
- **`base`** (default: `${WEST_TOPDIR}/zephyr-rtos`) — `ZEPHYR_BASE`.
- **`west-config`** — extra/overriding `west config` key/value pairs, e.g.
  `{zephyr.base-prefer: env}`.
- **`blobs-cache`** — a path to an auto-generated list of west blobs to
  pre-cache.
- **`blobs-fetch-args`** (default `["--auto-accept"]`) — extra `west blobs
  fetch` args.
- **`patch-committer-name`**/**`patch-committer-email`**/**`patch-committer-date`**
  — the identity used when applying project patches via `west patches`
  (`GIT_COMMITTER_NAME`/`_EMAIL`/`_DATE`), defaulting to
  `denver`/`denver@denver`/`2000-01-01T00:00:00` (a fixed value, so
  applying the same patches twice never produces a different commit).
- **`update-args`** — extra `west update` args.

`WEST_CONFIG_SYSTEM` (west's own base-config env var, e.g. the
remotes/defaults denver ships) is *not* a `denver.yml` key — set it
directly via `env:`/`hooks.env` like any other real environment variable;
west reads it itself, no provider-specific handling needed.

## Design notes

- **`west packages pip` is a separate concern.** Installing the Python
  packages a workspace's own modules declare (`west packages pip`) isn't
  this provider's job — give a *separate* `uv` stage a `requirements:
  [$(west packages pip)]` entry instead (see [`uv.md`](uv.md)), with its own
  `overrides:`/`freeze-to:` for pinning them. It has to run *after* this
  stage, since until the workspace is updated there's no way to know what
  those packages even are.
- **`--ci`** always adds a fixed shallow-clone strategy (`--narrow
  -o=--depth=1`) to `west update`, on top of whatever `update-args:`
  already configures.
- **`--fast`** only checks the workspace is already configured — dies with
  a clear message if it isn't, rather than running `west update`.
- **`--force`** always reruns `west update` (even if this stage's own
  drift check found nothing new) and recreates the workspace setup steps'
  own on-disk state unconditionally.
- **`--dry-run`** prints the `west` commands instead of running them, and
  writes neither `.west/config`, the drift fingerprint, nor `blobs-cache:`.
  The read-only queries this stage branches on (`west config -l`, `west
  manifest --resolve`, `west list`, `git rev-parse`) do still run: they are
  what decide which `west config` keys differ, whether `west update` would
  be skipped as unchanged, and which projects carry a `patches.yml`.
