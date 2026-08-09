# denver documentation

This tree is denver's full reference. It stands on its own: everything here
is explained in terms of the `denver.yml` schema itself, not by pointing at
the example environments under `examples/` (those are self-documenting via
their own comments).

The user-facing walkthrough — installing denver, running your first
environment, and the CLI flag reference — lives in the top-level
[`../README.md`](../README.md). `denver --help` is always the authoritative
flag list.

## Start here

- **[`glossary.md`](glossary.md)** — every term denver uses, defined once:
  environment, stage, step, provider, resolved config, hook, fingerprint,
  wrapper relocation. Read this first if a word in another doc is unfamiliar;
  the rest of the tree assumes these definitions.

- **[`how-to.md`](how-to.md)** — build a new environment from an empty
  folder, one stage at a time: a worked use case, the `stages:` list, then
  each stage in turn with its own provider and the supporting files it needs
  (compose file, requirements, conanfile, west manifest, ...). Read this if
  you'd rather learn the schema by writing one than by reading the reference.

## Reference

- **[`architecture.md`](architecture.md)** — the system, once. The complete
  `denver.yml` schema (every top-level key, every generic stage key), how a
  config is resolved (`import:` chain → merge rules → central default
  resolution), the mechanisms that make it flexible (layering, hooks,
  `-c`/`-cf` overrides, `${...}` interpolation) and fast (fingerprints,
  `--fast`/`--force`), plus stage filtering and the wrapper/relocation model.
  This is the doc to read before writing your own `denver.yml`.

- **[`providers/`](providers/)** — one page per provider: a full key
  reference for that provider's `denver.yml` section (every key, what it
  does, its default) plus design notes on the patterns it supports and how
  it behaves under `--fast`/`--force`.

  | Provider | Purpose |
  |---|---|
  | [`pip`](providers/pip.md) | Create/manage a Python virtualenv via `uv` |
  | [`conan`](providers/conan.md) | Provision native tools (compilers, cmake, ninja) via Conan |
  | [`docker`](providers/docker.md) | Wrapper: relocate the pipeline into a compose service |
  | [`zephyr`](providers/zephyr.md) | Manage a West (Zephyr RTOS) workspace |
  | [`custom`](providers/custom.md) | Escape hatch: an arbitrary command, sourced script or launcher |

  Each provider's module docstring (`src/providers/<name>.py`) carries the
  same key list as a terse lookup table kept next to the code, and points
  here for the full explanation, worked examples and rationale.

## Background

- **[`philosophy.md`](philosophy.md)** — the design principles behind all of
  the above: genericity, explicit over implicit, central default resolution,
  fail loud on the unexpected, fast-but-never-at-the-cost-of-correctness, the
  monorepo rule, and reproducibility as a first-class goal. Read this to
  understand *why* denver refuses to guess things other tools guess for you.

## Contributing

- **[`development.md`](development.md)** — the contributor workflow: `uv run
  poe all`, the test suite and its fakes, why coverage is pinned at 100%, how
  `examples/*` doubles as golden-file fixtures, how to add a new provider,
  and how a release is cut.
