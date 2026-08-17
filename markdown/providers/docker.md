# docker provider

A `docker` stage is a *wrapper*, not a builder — see “Wrapper / relocation”
in [Configuration](../configuration/denver-yml.md). Instead of building anything itself, it relocates the
rest of the pipeline into a docker compose service.

```yaml
my-docker-stage:
  provider: docker
  compose:
    file: docker-compose.yml
```

(`provider:`/`description:`/`disabled:`/`scripts:` are generic keys every stage has —
see “Generic stage keys” in [Configuration](../configuration/denver-yml.md). Everything below is specific to `docker`.)

## Requires

**`docker` with the Compose plugin must be available** on the machine this
stage runs on — denver never installs it, and every command it issues is a
`docker compose ...` one (v2, the plugin — not the standalone
`docker-compose` script). The stage checks both upfront (`docker` on `PATH`,
then `docker compose version`) and dies naming whichever is missing, rather
than failing deep inside the first `compose build`/`compose run`. Skipped
under `--dry-run`, so an env can still be previewed on a machine without
docker. The daemon has to be reachable for the invoking user, too. Point
`exe:` at a specific docker binary if `docker` isn’t the right exe.

## Key reference

- **`exe`** (default: `docker` on `PATH`) — the docker executable.
- **`default-cmd`** — the fallback interactive command once relocated into
  the container, read by denver’s own command resolution (`command:` at
  the top level still wins over this if set) — not read by this provider
  itself, but still a real `docker:` key, shown in `--show-config`. When
  it (or the top-level `command:`, or the plain `$SHELL`/`bash` fallback)
  names a bare `bash`/`zsh`/`fish`, denver wires up `denver complete` for
  it automatically before landing there — the container’s own image,
  unlike the host, is never something a user already has completion set
  up in themselves. Anything else (extra args aside, which are preserved)
  is left untouched. See [Shell completion](../cli/completion.md) for the
  full mechanism.
- **`image`** — the canonical local tag denver checks for before falling
  back to a build — checked whenever `image:` is set, whether or not
  `registries:` is also configured; a hit skips the build entirely
  (`--force` overrides this, see below). Exported as `$DENVER_DOCKER_IMAGE`
  before env-scripts/build/run, so the compose file can say `image: "${DENVER_DOCKER_IMAGE}"` instead of hard-coding the same tag a second
  time — denver doesn’t cross-check the two, this is just how they stay in
  sync. If unset, `$DENVER_DOCKER_IMAGE` is an empty string, the local
  check never runs, and `registries:` is silently ignored (nothing to
  search a registry *for* without a tag) rather than an error.
- **`registries`** (default `[]`) — ordered list of `{url, username, password}` entries to check, each as `<url>/<image>`, once `image:` has
  missed locally (or `--force` is set — see below), before ever
  considering a build: each entry in turn, via `docker manifest inspect`
  — **never** a real `docker pull` — first hit wins and no further entry
  is tried. On a hit, `$DENVER_DOCKER_IMAGE` is repointed from the bare
  `image:` tag to that entry’s full `<url>/<image>` ref, so the actual
  pull happens lazily, later, whenever `docker compose run` itself needs
  the image. Empty (the default) disables this entirely. If nothing is
  found anywhere and `compose.build: false`, denver dies with a clear
  error instead of silently doing nothing. `url:` is required per entry;
  `username:`/`password:` are optional but, if either is set, both must
  be — when present, `docker login <url>` runs automatically, credentials
  piped via stdin (never argv, never logged), right before the manifest
  check against that entry; an entry with neither is assumed
  already-authenticated or public. Both fields go through denver’s normal
  `${VAR}` interpolation, so a literal (`myusername`) and an
  env-var-sourced secret (`${DOCKER_PASSWORD_DOCKERHUB}`) are written the same
  way.
- **`compose`** — `file` (**required**, a single path or a list for
  multiple `-f` overlays — never guessed, see “Explicit over implicit” in
  [`../concepts/philosophy.md`](../concepts/philosophy.md)), `service` (default `"dev"`), `build` (default `true`),
  `args` (extra `docker compose` args, e.g. `["--project-name", "x"]`).
- **`run-args`** (default `["--rm"]`) — extra `docker compose run` args.
- **`env-scripts`** — script(s) run before build/run, e.g. to write a
  compose `.env` file the compose file itself references. denver has no
  notion of a compose env-file — it just runs `env-scripts:` and lets
  `docker compose`/the scripts sort out their own file naming and lookup.

## Design notes

- **`compose.build: true` (the default) does nothing without `image:`.**
  Denver never calls `docker compose build` unless `image:` is set — with
  no tag to check next time, it would just rebuild on every single run,
  defeating the point. Set `image:` to get denver’s own build-once
  behavior: it builds the first time, then a local (or `registries:`) hit
  skips the build on every run after that. Without `image:`, `compose build`/`compose run` are left to the compose file’s own `build:` section
  to sort out, exactly as if denver’s docker provider weren’t managing
  images at all. Set `compose.build: false` for the opposite of the
  `image:`-managed case: nothing gets built, ever, denver just relocates
  into whatever image is already there — e.g. one pulled as part of
  `env-scripts:` or a CI step outside denver entirely.
- **Host vs. container.** `setup()` (build the image, run `env-scripts:`)
  always runs on the host — that’s the only place the `docker` CLI
  operates. `wrap()` (turn the resolved command into `docker compose run ...`) is what actually relocates execution. Each prints its own progress
  banner — `setup()`’s `prepare` and then whichever of “build”/”found
  locally”/”found on a registry”/skipped applies, `wrap()`’s `run` last —
  so the relocation itself is visible, not silent between the last
  `setup()` line and the container’s own output.
- **`--fast` has no effect here.** Unlike uv/conan/zephyr, this provider
  doesn’t thread `--fast` through `setup()` at all — `compose.build:` is
  read exactly as configured, so a real `docker compose build` still runs
  under `--fast` if the image wasn’t found locally or on a registry. The
  local/`registries:` lookup itself is cheap and read-only, so it always
  runs regardless.
- **`--force`** is the escape hatch back to “always rebuild”: it ignores a
  locally-cached `image:` hit, though a `registries:` entry that already
  has it still wins over a forced local rebuild — a rebuild only actually
  happens if none of the configured registries have it either.
- **`--dry-run`** prints the `env-scripts:`, `docker compose build` and
  `docker compose run` commands instead of running them; no container is
  ever started. The local/`registries:` existence checks still run (they’re
  the read-only queries deciding whether a build would be shown at all),
  but the `docker login` that would precede a private-registry check does
  not — so a private entry may report a miss it wouldn’t report for real.
  The bigger limit is structural: since the container is never entered,
  the setup stages that run *inside* it can’t be previewed. denver prints
  a note saying so; use `--skip <docker stage>` to preview that same stack
  on the host instead.
- **Multi-registry check, local-first, never pulling in `setup()`.** When
  several places might already have the image — a shared registry, a
  personal mirror, whatever a CI run pushed to — `registries:` lets denver
  search them in a fixed order instead of only knowing about one, using
  `docker manifest inspect` to check existence without downloading
  anything. It’s still fully explicit: nothing is scanned or guessed, only
  the entries named in the list are ever tried, in the order given. The
  actual image transfer is left entirely to `docker compose run` itself,
  once denver has pointed `$DENVER_DOCKER_IMAGE` at whichever ref (local
  tag or a specific registry’s) should be used.
- **Automated per-registry login, inline.** Each `registries:` entry can
  carry its own `username:`/`password:` right alongside its `url:`, so a
  private registry in the search list doesn’t need a separate manual
  login step (e.g. a `scripts: login:` entry run via `denver run <env> --scripts login`, [Configuration](../configuration/denver-yml.md)’s generic one-shot mechanism) — denver logs in
  for you, right before it’s actually needed, only for entries that carry
  credentials.
