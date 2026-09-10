# ADR-0002: No path is inferred from the directory layout

| | |
|---|---|
| **Status** | accepted |
| **Affects** | [chapter 4.4](../04_solution_strategy/index.md), [chapter 8.4](../08_cross_cutting_concepts/index.md), [chapter 8.7](../08_cross_cutting_concepts/index.md) |

## Context

denver used to treat conventional paths as load-bearing. A file took effect
just by existing:

- `hooks/<name>.sh`, plus an always-sourced `hooks/<name>.user.sh`
- `pip/skip-if.sh`, `pip/venv-patcher/patches.yml`
- `conan/recipes`, `conan/conanfile.py`, `conan/base_classes`
- `docker-compose.yml`

Framed as convention over configuration. In practice it broke the central
promise — that config plus `--show-config` tell you what happens:

- **Config stopped being the source of truth.** "Does this env patch its venv?" needed denver's probing rules *and* a look at the disk, in every layer of the import chain.
- **Creating a file changed behavior with no reviewable diff.** Dropping in `hooks/env.sh` armed it for everyone.
- **Conventions leaked across stages.** Two `uv` stages in one env probed the same env dir, so both silently shared one `skip-if.sh`.
- **Failures were silent.** A renamed or misspelled conventional file simply stopped taking effect.

## Decision

Remove every filesystem convention.

- Each of those keys is explicit-only.
- A configured path that does not exist is a fatal config error, not a silent skip.
- `docker.compose.file` became mandatory. `conan.base-classes` became optional.
- `hooks: <name>:` runs exactly what it lists, in order. A personal layer is just another entry, placed last.

Kept on purpose: `Context.resolve_path()`'s nearest-import-dir-first fallback.
It resolves paths the author wrote. It does not guess which paths exist.

## Consequences

- Envs had to spell out what they had been getting implicitly — explicit `hooks:`, `conan.recipe-dirs`, `conan.conanfiles`, `skip-if:` per stage.
- More lines in config. Each one reviewable.
- Resolved `--show-config` output was verified identical before and after. The change was in how behavior is *declared*, not in what the envs do.
- A missing file now fails loudly, at resolution time.
