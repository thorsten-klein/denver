# Glossary

Every term denver uses, defined once. The rest of the documentation assumes
these definitions.

## The core model

**Environment** — a directory containing a `denver.yml`, and the unit denver
launches: `denver <env>`. An environment is fully described by its
`denver.yml` (plus whatever that file explicitly points at); reading the file
tells you everything the environment does. `<env>` may also be a path
directly to a YAML file, so one directory can hold several variants side by
side (`denver.debug.yml`, `denver.release.yml`).

**`denver.yml`** — the config file describing one environment: which stages
run, in what order, and how each is configured. Its schema is documented in
[`architecture.md`](architecture.md).

**Stage** — one entry in `stages:`: a provider type plus its own config
section, run in order. The entry is the *stage id*, and the top-level section
of the same name is that stage's config. A stage id is just a label — the
section must always declare `provider: <name>` explicitly — which is what
lets one environment run two `uv` stages (e.g. `uv` and `uv-zephyr`)
targeting different venvs.

**Provider** — the generic engine behind a stage type. denver ships five:
`uv`, `conan`, `zephyr`, `docker` and `custom`. A provider holds no
project-specific knowledge; everything specific comes from the `denver.yml`
section it is given. See [`providers/`](providers/).

**Step** — a stage's own internal sub-phase, e.g. conan's
prepare/export/install or uv's ensure-python/ensure-venv/install/activate.
Each step prints its own banner line, in whatever order the provider actually
does the work; there is deliberately no "step 3 of 7" numbering to keep in
sync with the code.

**Setup provider** (`kind: setup`) — a provider that builds part of the
environment in place: creates a venv, installs tools, updates a workspace.
`uv`, `conan` and `zephyr` are setup providers; `custom` is one unless it
declares `launcher:`.

**Wrapper provider** (`kind: wrapper`) — a provider that builds nothing
itself and instead relocates the final command somewhere else, e.g. into a
container. `docker` is denver's wrapper provider; a `custom` stage with
`launcher:` acts as one too.

**Wrapper relocation** — what running an environment that stacks a wrapper
actually does: denver builds/enters the container and re-invokes *itself*
inside it with `--skip <that stage>`, so the remaining setup stages build the
environment in there rather than on the host. Skipping the wrapper yourself
(`denver <env> --skip docker`) runs the exact same stack directly on the
host.

## Configuration

**Resolved config** — a stage's config section after every default has been
filled in centrally, before any stage runs. This is exactly what
`--show-config` prints, and exactly what a real run uses — the two can never
disagree, because a provider's `setup()` never computes a default of its own.

**Whole-file `import:`** — one `denver.yml` inheriting another environment's
entire stack as a base, then adding or overriding only what differs. This is
how a version-specific environment reuses a shared base without copy-pasting
its `stages:`/`docker:`/`conan:`/`uv:` config.

**Section-level `import:`** (also called *stacking*) — one stage section
pulling its content from another environment's section, without inheriting
that environment's entire stack. An entry may name a specific section
(`path:section`) instead of the same-named one.

**Merge rules** — how two layers combine: mappings merge key by key
recursively; lists append (lower layer's entries first); two layers setting
the same string key to different values is a hard error unless the override
is prefixed with `!`. See [`architecture.md`](architecture.md) for the
details, including the `<overwrite>` marker.

**Interpolation** — `${VAR}` / `${VAR:-default}` expansion inside `denver.yml`
values, resolved against the environment denver is building (including its
own built-ins such as `DENVER_ENV_DIR`).

**Hook** — a script *sourced* (not merely executed) at a fixed point, so its
exports become part of the environment everything after it runs in. The hook
points are `env` (once, before any stage), `pre-<stage>` / `post-<stage>`
(around each stage), and `pre-cmd` (right before the final command).

**`scripts:` / `--run <name>`** — the generic, open-ended one-shot mechanism,
distinct from hooks: any stage section may declare `scripts: <name>: [...]`,
and `denver <env> --run <name>` runs every stage's `<name>` entries and then
exits without running the pipeline. `<name>` is arbitrary — `setup` and
`login` are conventions, not built-in flags.

## Execution

**Fingerprint** (or *checksum*) — the mechanism a stage uses to detect that
nothing relevant changed since its last successful run (requirement file
contents, recipe content, workspace state, ...) and skip its own expensive
step. This is what makes a repeat run take seconds instead of minutes.
`--force` bypasses it; `--fast` skips the build step without even checking.

**Stage filtering** — restricting which stages run: `--until <stage>`
truncates the pipeline after the named stage, `--skip <stage>` removes
individual stages, and a stage's own `disabled: true` opts it out by default.

**Quiet level** — `-q` silences info lines, echoed commands and build-tool
output while keeping each stage's banner and summary; `-qq` silences those
too, leaving only the launched command's own output. Errors always print.

**`DENVER_STATE_DIR`** — the one environment variable denver itself *reads*:
where it writes per-environment state when running from an installed package.
Defaults to `~/.denver`. Full explanation in the top-level
[`../README.md`](../README.md).
