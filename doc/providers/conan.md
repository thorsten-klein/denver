# conan provider

A `conan` stage provisions native, non-Python tools (compilers, `cmake`,
`ninja`, ...) via [Conan](https://conan.io) and puts them on `PATH`.

```yaml
my-conan-stage:
  provider: conan
  conanfiles:
  - path: conanfile.py
    recipe-dirs:
    - path/to/recipes
    catalog: catalog.yml   # optional
```

(`provider:`/`description:`/`disabled:`/`scripts:` are generic keys every stage has —
see "Generic stage keys" in [`../architecture.md`](../architecture.md). Everything else is specific to `conan`.)

## Requires

**`conan` must be available** wherever this stage runs — denver never
installs it. In practice an earlier `uv` stage does, by listing `conan` in
its `requirements:`, which puts it on `PATH` via that stage's venv; a
host-wide or in-image install works just as well, and `exe:` can point at a
specific one. A stage with no conan available fails with `conan provider
needs 'conan' on PATH`.

## Key reference

- **`exe`** (default: `conan` on `PATH`) — the conan executable.
- **`recipes-exporter`** / **`deployer`** (default: denver's own bundled
  tools) — the scripts that generate/export recipe references and deploy
  installed packages onto `PATH`. Overriding these is an escape hatch for
  an unusual setup; most environments never set them.
- **`base-classes`** — optional; a list of directories of shared conanfile
  base classes recipes can inherit from. Each is put on the
  recipes-exporter's `PYTHONPATH`, in list order (earlier entries win), and
  may itself live in a base env, resolved the normal way (falling back to an
  imported base env's own directory). A listed dir must exist (it's an error
  if it doesn't). Appends across `import:` layers like any other list (see
  [`../architecture.md`](../architecture.md)'s "Merge rules"), so a derived
  env only needs to list the base-classes dirs it adds itself.
- **`conanfiles`** — a list of *units*, installed in order. A unit is a
  conanfile together with the recipes it is installed from, so an env that
  stacks on another appends whole units rather than merging several parallel
  lists. Appends across `import:` layers like any other list (see
  [`../architecture.md`](../architecture.md)'s "Merge rules"). Each entry is a
  mapping — a bare path string is rejected — with these keys:
  - **`path`** (required) — the conanfile to install.
  - **`recipe-dirs`** — optional; directories containing recipes to export
    before installing. Never guessed from the directory layout (see
    "Explicit over implicit" in [`../philosophy.md`](../philosophy.md)) —
    each dir must be listed, and must exist. An entry may be a whole recipe
    tree or a single recipe directory; recipes are found by their
    `conandata.yml`, wherever it sits.
  - **`catalog`** — optional; a path to write this unit's catalog to (every
    one of its recipes pinned as `name/version@user/channel#rrev`). Unset,
    the catalog is built in memory, handed straight to the export step and
    never written — so a run leaves no generated file behind. Set it when the
    pins should be reviewable/committed, or when the unit's own conanfile
    reads them back (see
    [`../../examples/raspberry-pico/conan/conanfile.py`](../../examples/raspberry-pico/conan/conanfile.py)).
  - **`recipes-exporter`** — optional; overrides the env-wide default for
    this unit only.

  A unit's `recipe-dirs:` are resolved as **one** catalog, so recipes in one
  dir may require recipes in another dir of the same unit — and a unit's
  catalog content follows from that unit's membership. Put recipes in their
  own unit to keep their catalog independent of what another unit does.
- **`build`** (default `"missing"`) — passed as `--build=<value>` (a
  string or a list) to `conan install`.
- **`install-args`** — extra literal `conan install` arguments.
- **`no-auth`** (default `false`) — when `true`, `conan install` runs with
  `--no-remote`.
- **`profiles`** — `host`/`build`, each a list; every entry becomes its own
  `-pr:h=<value>` / `-pr:b=<value>` flag, in list order. Empty by default
  (no explicit profile flags).
- **`config`** — optional; a list of directories, each installed via `conan
  config install <dir>`, in order, before profile detection. Whatever
  conan's own `config install` understands inside them (profiles,
  `remotes.json`, `credentials.json`, `source_credentials.json`,
  `settings.yml`, ...) is installed into the conan cache by conan itself —
  denver never opens or interprets any file inside a `config:` directory,
  it only invokes the command.
- **`remotes`** — optional; a project-owned, *exhaustive* list of the conan
  remotes this env wants. Each entry: `url`, `verify_ssl` (default `true`),
  `enabled` (default `true`). When the prepare stage runs, the recipes-exporter
  adds/renames/enables exactly these remotes and disables every *other*
  remote already present in the conan home — so `remotes:` should list
  every remote the env needs, not just ones being newly added. A remote's
  own login (if reachable) runs as part of the same stage.
  `CONAN_REMOTE_ENABLE_<NAME>` (an env var, `ON`/`OFF`) overrides a given
  remote's `enabled:` at run time.
- **`cleanup-remotes`** (default `true`) — makes `remotes:` exhaustive even
  when it's left unset/empty: the prepare stage then disables *every*
  remote already present in the conan home, so each env's remote
  configuration is fully self-contained regardless of what an earlier run
  of a *different* env left behind. Set to `false` to opt out instead: with
  no `remotes:` of its own, the env then leaves the conan home's existing
  remote configuration alone entirely. `cleanup-remotes` is automatically
  skipped (regardless of its own value) whenever `remotes:` is left
  unset/empty *and* the env also has a `config:` — its `conan config
  install <dir>` may itself have installed a `remotes.json`, and
  reconciling an empty `remotes:` to "exhaustive" would otherwise silently
  disable everything `config:` just set up. An explicit (non-empty)
  `remotes:` still reconciles as normal regardless of `config:`.
- **`user`** (default `"denver"`) / **`channel`** (default `"snapshot"`) —
  become the user/channel half of every reference the recipes-exporter
  generates while exporting recipes (`name/version@user/channel`).

## Design notes

- **Monorepo pattern.** Recipes commonly live in the same repository as the
  project using them, not a separate recipes repo — see "Monorepo" in
  [`../philosophy.md`](../philosophy.md). A unit's `recipe-dirs:` just points at wherever they
  actually are; there's no requirement they live in any particular place
  relative to the `denver.yml`.
- **A base env with recipes but no conanfile.** Since `recipe-dirs:` live
  inside a unit, a shared base env that ships recipes without a conanfile of
  its own has no unit to put them in; each env that installs those recipes
  lists the base's dir in its own unit instead (see
  [`../../examples/zephyr-devshell`](../../examples/zephyr-devshell) and the
  envs that import it).
- **Works with or without remotes.** An env with no `remotes:` and no
  `config:` at all is a fully offline/local-cache setup — nothing gets
  reconciled, conan just uses whatever's already in its local cache/home.
  Add `remotes:` (or a `config:` that installs its own remote config) only
  once real remote access is actually needed.
- **`--fast`** activates the already-generated `conanbuildenv.sh` instead
  of re-running `conan install`; dies with a clear message if it doesn't
  exist yet.
- **`--force`** recreates the conan/workspace setup steps' own on-disk
  state unconditionally.
