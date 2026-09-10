# 9. Architecture Decisions

Each record: what the situation was, what was decided, what it cost.

Four decisions are long enough to get their own page:

```{toctree}
:maxdepth: 1

adr-0001-central-default-resolution
adr-0002-no-inferred-paths
adr-0003-config-format
adr-0004-wrapper-relocation
```

| ID | Decision | Status |
|---|---|---|
| [ADR-0001](adr-0001-central-default-resolution.md) | Every provider default resolved centrally, before any stage runs | accepted |
| [ADR-0002](adr-0002-no-inferred-paths.md) | No path is ever inferred from the directory layout | accepted |
| [ADR-0003](adr-0003-config-format.md) | YAML is the default config format, TOML optional | accepted |
| [ADR-0004](adr-0004-wrapper-relocation.md) | A container is a wrapper stage that relocates the run | accepted |
| [ADR-0005](#adr-0005-provider-is-mandatory-never-guessed-from-the-stage-id) | `provider:` is mandatory, never guessed from the stage id | accepted |
| [ADR-0006](#adr-0006-stages-and-providers-as-the-two-names) | `stages:` names the pipeline, `provider:` names the type | accepted |
| [ADR-0007](#adr-0007-lists-append-instead-of-replacing) | Lists append across layers instead of replacing | accepted |
| [ADR-0008](#adr-0008-die-raises-denvererror-instead-of-exiting) | `die()` raises `DenverError`, caught once in `main()` | accepted |
| [ADR-0009](#adr-0009-state-lives-with-the-env) | An env's state lives with the env, not in a shared pool | accepted |
| [ADR-0010](#adr-0010-one-run-per-environment) | Concurrent runs of one env are serialised by a lock | accepted |
| [ADR-0011](#adr-0011-fingerprint-content-not-absolute-paths) | Fingerprints cover content and layout, not absolute paths | accepted |
| [ADR-0012](#adr-0012-extension-providers-instead-of-forks) | A project registers its own provider instead of forking denver | accepted |
| [ADR-0013](#adr-0013-download-and-git-as-providers) | Recurring shell scripts became the `download` and `git` providers | accepted |
| [ADR-0014](#adr-0014-subcommand-cli) | The CLI is subcommand-based (`run`, `clean`, `complete`) | accepted |
| [ADR-0015](#adr-0015-top-level-package-names-that-do-not-squat) | Shipped packages are `denver_providers`, `denver_assets`, `denver_errors` | accepted |
| [ADR-0016](#adr-0016-pip-provider-renamed-to-uv) | The `pip` provider is called `uv` | accepted |

## ADR-0005: provider is mandatory, never guessed from the stage id

**Context.** `make_stage()` used to infer a stage's type from its id when the
id happened to match a provider name. A stage's behavior then depended on its
name, and renaming it could change or break it silently.

**Decision.** Every stage's section declares `provider: <name>`. Always. No
inference, even when the id matches.

**Consequences.** Every shipped env gained a `provider:` line. `make_stage()`
dies with the known provider names when the key is missing. One env can now
hold two stages of the same type without ambiguity.

## ADR-0006: stages and providers as the two names

**Context.** The pipeline key was once `nature:`, and the concept was called
`Nature`. It named an ordered list of stages, not a list of types — confusing
as soon as two stages shared a type.

**Decision.** `stages:` names the ordered pipeline. "Provider" is the type a
stage's section declares. Classes and the package were renamed to match.

**Consequences.** A wide rename across code, tests, docs and every env. No
compatibility alias — pre-1.0, no external consumers (chapter 2.2).

## ADR-0007: lists append instead of replacing

**Context.** Lists used to be replaced outright by the overriding layer. A
derived env that wanted its base's recipe dirs *and* its own had to repeat the
base's entries — and silently lost them when the base changed.

**Decision.** Lists append: lower layers first, then this layer. `!entry`
drops everything below; a bare `<overwrite>` entry does the same as a pure
marker and is removed itself.

**Consequences.** Derived envs list only what they add. Dropping an inherited
entry is now an explicit act with a visible marker.

## ADR-0008: die() raises DenverError instead of exiting

**Context.** `die()` called `sys.exit(1)` directly, from both `denver.py` and
the provider package. Tests asserted on `SystemExit`, and no caller could add
context to a failure.

**Decision.** `die()` logs and raises `DenverError`. `main()` catches it once
and exits 1. Both live in the leaf module `denver_errors.py`.

**Consequences.** One exit path. A caller that knows more catches the concrete
error and calls `die()` with better context. `DenverError` is never caught
anywhere else — it has already been logged.

## ADR-0009: state lives with the env

**Context.** State used to go into a shared, name-keyed pool. Two checkouts of
the same env collided, and deleting a checkout left its state behind.

**Decision.** State goes to `<env dir>/.denver/<config file stem>/`.
`DENVER_ENV_WORKDIR` overrides it.

**Consequences.** Checkouts are independent. A bind-mounted workspace carries
its state into the container. Variants in one folder (`denver.debug.yml`,
`denver.release.yml`) stay separate. A read-only env dir is a hard error
naming the override, not a silent fallback.

## ADR-0010: one run per environment

**Context.** Parts of an env's state are rebuilt, not updated — conan wipes
its install tree, uv recreates a changed venv. A second concurrent run could
be using exactly that.

**Decision.** An exclusive lock on `<state dir>/.lock`. A second run waits and
says whose run it waits for; `--no-wait` fails instead.

**Consequences.** The lock is released by `exec()` closing the descriptor, so
it covers exactly the mutating phase. A devshell never holds it. Relocation
cannot deadlock. No `flock` support → warn and continue.

## ADR-0011: fingerprint content, not absolute paths

**Context.** Fingerprints that include absolute paths change when a checkout
moves, forcing pointless rebuilds — and can match across genuinely different
inputs that happen to share a path.

**Decision.** Fingerprint file content and relative layout.

**Consequences.** Moving a checkout keeps the cache valid. Content changes
invalidate it, which is the whole point.

## ADR-0012: extension providers instead of forks

**Context.** A project needing its own provisioning step had two bad options:
squeeze it into `custom: cmd:`, or fork denver.

**Decision.** `extensions: providers: dirs:` imports `*.py` files, each
defining `PROVIDER`, and registers them like built-ins.

**Consequences.** Project-specific providers get the full lifecycle
(`resolve_defaults`/`setup`/`wrap`). Unknown keys under `extensions:` are an
error, so a typo cannot disable the mechanism quietly.

## ADR-0013: download and git as providers

**Context.** Two shell scripts kept being rewritten per project: fetch an
archive, verify a checksum, unpack, extend `PATH`; and clone or fetch a
checkout at a pinned revision.

**Decision.** Make both providers.

**Consequences.** Idempotence, checksum verification, mirrors and `--fast`
awareness exist once instead of per project. `examples/raspberry-pico` moved
onto them and dropped its Conan usage.

## ADR-0014: subcommand CLI

**Context.** Everything was flags on one command. Shell completion and
`clean` had nowhere to live.

**Decision.** `denver run <env>` is the normal entry point.
`denver clean <env>` and `denver complete` are the others. `--version` and
`--license` stay top-level.

**Consequences.** Completion for bash, zsh and fish. An env's own flags
(`denver-custom-args:`) attach to `run`. Docs and examples all use
`denver run`.

## ADR-0015: top-level package names that do not squat

**Context.** The shipped package used generic top-level names (`providers`,
`assets`), which collide with anything else installed in the same
environment.

**Decision.** Ship `denver_providers/`, `denver_assets/`, `denver_errors.py`,
flat next to `denver.py`.

**Consequences.** No collisions. Import paths in every env, test and doc
updated once.

## ADR-0016: pip provider renamed to uv

**Context.** The provider was called `pip` but drove `uv` throughout — `uv
venv`, `uv pip install`, `uv python install`. The name described the
ecosystem, not the tool.

**Decision.** Rename it to `uv`, matching the executable it actually runs.

**Consequences.** Every env's `pip:` section became `uv:`. Related keys were
generalised at the same time (`install-args:`, `freeze-to:`, `amend:`), so a
Zephyr workspace's `west packages pip` step is now an ordinary `uv` stage
rather than zephyr-specific code.
