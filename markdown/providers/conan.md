# conan provider

A `conan` stage provisions native, non-Python tools (compilers, `cmake`,
`ninja`, …) via [Conan](https://conan.io) and puts them on `PATH`.

```toml
[my-conan-stage]
provider = "conan"
conanfile = "conanfile.py"

[[my-conan-stage.recipes]]
dirs = ["path/to/recipes"]
catalog = "catalog.yml"  # optional
```

(`provider:`/`description:`/`disabled:`/`depends-on:`/`scripts:`/`env:`/`env-prepend:`/`env-append:` are generic keys every stage has —
see “Generic stage keys” in [Configuration](../configuration/denver-toml.md). Everything else is specific to `conan`.)

## Requires

**`conan` must be available** wherever this stage runs — denver never
installs it. In practice an earlier `uv` stage does, by listing `conan` in
its `requirements:`, which puts it on `PATH` via that stage’s venv; a
host-wide or in-image install works just as well, and `exe:` can point at a
specific one. A stage with no conan available fails with `conan provider needs 'conan' on PATH`.

## Key reference

- **`exe`** (default: `conan` on `PATH`) — the conan executable.
- **`deployers`** (default: just denver’s own bundled symlink deployer) — a
  list of scripts that deploy installed packages onto `PATH`, each run as
  its own `conan install --deployer=<script>`. The env-wide recipes-exporter
  (the script that generates/exports recipe references) is not
  user-configurable at this level — only a `recipes:` entry’s own
  `export-tool:` (below) can override it, for that entry only.
- **`base-classes`** — optional; a list of directories of shared conanfile
  base classes recipes can inherit from. Each is put on the
  recipes-exporter’s `PYTHONPATH`, in list order (earlier entries win), and
  may itself live in a base env, resolved the normal way (falling back to an
  imported base env’s own directory). A listed dir must exist (it’s an error
  if it doesn’t). Appends across `import:` layers like any other list (see
  [Configuration](../configuration/denver-toml.md)’s “Merge rules”), so a derived
  env only needs to list the base-classes dirs it adds itself.
- **`conanfile`** — optional; the single conanfile to install via `conan install` (a project only ever has one dependency graph, so this is a
  single path, not a list). Left unset, config/export/prepare still run as
  normal — this is the only toggle for whether `conan install` (and the
  `conanbuildenv.sh` activation after it) runs at all; there is no separate
  `install:` flag to keep in sync with it. Independent of `recipes:` below —
  which recipes get exported has nothing to do with what the conanfile
  itself requires; it installs whatever its own
  `requirements()`/`build_requirements()` reference, whether or not this env
  exports it itself.
- **`recipes`** — a list of entries, each exported into the local conan
  cache before `conanfile:` (if any) is installed. Never guessed from the
  directory layout (see “Explicit over implicit” in
  [`../concepts/philosophy.md`](../concepts/philosophy.md)). Appends across `import:`
  layers like any other list. Each entry is a mapping with these keys:
  - **`dirs`** — optional; directories containing recipes to export. Each
    must be listed explicitly and must exist. An entry may be a whole recipe
    tree or a single recipe directory; recipes are found by their
    `conandata.yml`, wherever it sits.
  - **`catalog`** — optional; a path to write this entry’s catalog to (every
    one of its recipes pinned as `name/version@user/channel#rrev`). Unset,
    the catalog is built in memory, handed straight to the export step and
    never written — so a run leaves no generated file behind. Set it when the
    pins should be reviewable/committed, or when the conanfile installed via
    `conanfile:` reads them back.
    Requires `dirs:` — a catalog with nothing to build it from is rejected.
  - **`export-tool`** — optional; overrides the env-wide recipes-exporter for
    this entry only.

  An entry’s `dirs:` are resolved as **one** catalog, so recipes in one dir
  may require recipes in another dir of the same entry — and an entry’s
  catalog content follows from that entry’s membership. Put recipes in their
  own entry to keep their catalog independent of what another entry does.
- **`build`** (default `"missing"`) — passed as `--build=<value>` (a
  string or a list) to `conan install`.
- **`install-args`** — extra literal `conan install` arguments.
- **`authentication`** (default `true`) — when `false`, `conan install` runs
  with `--no-remote`.
- **`profiles`** — `host`/`build`, each a list; every entry becomes its own
  `-pr:h=<value>` / `-pr:b=<value>` flag, in list order. Empty by default
  (no explicit profile flags).
- **`config`** — optional; a list of directories, each installed via `conan config install <dir>`, in order, before profile detection. Whatever
  conan’s own `config install` understands inside them (profiles,
  `remotes.json`, `credentials.json`, `source_credentials.json`,
  `settings.yml`, …) is installed into the conan cache by conan itself —
  denver never opens or interprets any file inside a `config:` directory,
  it only invokes the command.
- **`remotes`** — optional; a project-owned, *exhaustive* list of the conan
  remotes this env wants. Each entry: `url`, `verify_ssl` (default `true`),
  `enabled` (default `true`). When the prepare stage runs, the recipes-exporter
  adds/renames/enables exactly these remotes and disables every *other*
  remote already present in the conan home — so `remotes:` should list
  every remote the env needs, not just ones being newly added. A remote’s
  own login (if reachable) runs as part of the same stage.
  `CONAN_REMOTE_ENABLE_<NAME>` (an env var, `ON`/`OFF`) overrides a given
  remote’s `enabled:` at run time.
- **`keep-remotes`** (default `false`) — set to `true` to opt out of making
  `remotes:` exhaustive: with no `remotes:` of its own, the env then leaves
  the conan home’s existing remote configuration alone entirely. Left at the
  default `false`, the prepare stage disables *every* remote already present
  in the conan home when `remotes:` is left unset/empty, so each env’s
  remote configuration is fully self-contained regardless of what an earlier
  run of a *different* env left behind. This cleanup is automatically
  skipped (regardless of `keep-remotes:`) whenever `remotes:` is left
  unset/empty *and* the env also has a `config:` — its `conan config install <dir>` may itself have installed a `remotes.json`, and
  reconciling an empty `remotes:` to “exhaustive” would otherwise silently
  disable everything `config:` just set up. An explicit (non-empty)
  `remotes:` still reconciles as normal regardless of `config:`.
- **`user`** (default `"denver"`) / **`channel`** (default `"snapshot"`) —
  become the user/channel half of every reference the recipes-exporter
  generates while exporting recipes (`name/version@user/channel`).

## Design notes

- **When a `custom` stage is enough.** For a *single* prebuilt archive with
  no dependencies, `curl` + `sha256sum -c` + `tar` in a `custom` stage is an
  honest twenty lines and needs no conan on `PATH` — see “Bringing a
  prebuilt binary in by hand” in [`custom.md`](custom.md). What conan adds is
  the per-tool cost: a url and a checksum instead of a script, plus a cache
  shared across envs and checkouts, and dependencies between tools.
- **Monorepo pattern.** Recipes commonly live in the same repository as the
  project using them, not a separate recipes repo.
  A `recipes:` entry’s `dirs:` just points at wherever they
  actually are; there’s no requirement they live in any particular place
  relative to the `denver.toml`. This simplifies the integration as there is no deployment necessary.
- **A base env with recipes but no conanfile.** Since `conanfile:` and
  `recipes:` are independent, a shared base env can ship `recipes:` with no
  `conanfile:` of its own; each derived env that wants to actually install
  something sets its own `conanfile:`, and either inherits the base’s
  `recipes:` or adds more of its own.
- **Works with or without remotes.** An env with no `remotes:` and no
  `config:` at all is a fully offline/local-cache setup — nothing gets
  reconciled, conan just uses whatever’s already in its local cache/home.
  Add `remotes:` (or a `config:` that installs its own remote config) only
  once real remote access is actually needed.
- **Skips `conan install` when the dependency graph is unchanged.** Before
  wiping and reinstalling, the install step runs a fast `conan graph info`
  query (no downloads/builds) and hashes its output; if that hash matches
  the one stored by the last successful install here, the existing install
  tree is left alone entirely. A failed query (conan not on `PATH` yet
  under `--dry-run`, a broken recipe, …) is treated as “changed”, so
  `conan install` runs and reports the real problem itself. Only the
  install step is gated this way — `config`/`prepare`/`export` above it
  always run.
- **`--fast`** activates the already-generated `conanbuildenv.sh` instead
  of re-running `conan install` (or the `conan graph info` check);
  dies with a clear message if it doesn’t exist yet.
- **`--force`** recreates the conan/workspace setup steps’ own on-disk
  state unconditionally, including a fresh `conan install` regardless of
  the graph-info check above.
- **`--dry-run`** prints the `conan`/recipes-exporter commands instead of
  running them, and leaves the install tree (which a real run wipes first)
  untouched. `conan config home` still runs — it is a read-only query whose
  answer decides whether `conan profile detect` would be shown; when conan
  isn’t installed yet (an earlier `uv` stage would have), that query simply
  fails, and denver warns and previews the detection anyway rather than
  stopping there.
