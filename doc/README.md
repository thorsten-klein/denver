# denver documentation

This tree is denver's full reference. It stands on its own: everything here
is explained in terms of the `denver.toml` schema itself, not by pointing at
the example environments under `examples/` (those are self-documenting via
their own comments).

**Browsing on GitHub, this page is the index.** Reading it as a built site
instead — search, sidebar nav, one page per topic — is usually nicer:
[`index.md`](index.md) is the same tree, built by
[`examples/doc-env/`](../examples/doc-env/) and published to `gh-pages` by
[`.github/workflows/docs.yml`](../.github/workflows/docs.yml). That build
also emits a plain-Markdown mirror of every page under `/markdown/` — meant
for AI tools/LLMs to fetch directly rather than scrape rendered HTML.

## New to denver? Read in this order

1. [`introduction/index.md`](introduction/index.md) — what problem denver
   solves, what an environment is, and how flexible it is
2. [`introduction/install.md`](introduction/install.md) — get the `denver`
   command onto your machine
3. [`quickstart/five-minutes.md`](quickstart/five-minutes.md) — run the
   smallest possible environment end to end, in a few minutes
4. [`quickstart/creating-environments.md`](quickstart/creating-environments.md)
   — a bigger, more realistic environment, and you build it yourself, from
   an empty folder

Everything after that is reference you dip into as needed.

## Introduction

- **[`introduction/index.md`](introduction/index.md)** — three questions
  answered before you install anything: what problem denver solves, what a
  denver environment is (environment / stage / provider), and how free you
  are to ignore the bundled providers entirely.
- **[`introduction/install.md`](introduction/install.md)** — installing
  denver (PyPI, the standalone executable, editable mode, vendoring via
  git-nested).

## Quickstart

- **[`quickstart/five-minutes.md`](quickstart/five-minutes.md)** — a
  hands-on, end-to-end walkthrough of `examples/simple-env`: three shell
  commands and nothing else, run for real and then previewed with
  `--dry-run`, with the difference between denver's own output and a
  stage's own output made explicit.
- **[`quickstart/creating-environments.md`](quickstart/creating-environments.md)**
  — a bigger, more realistic environment (`examples/howto-env`: a
  container, a venv, two hand/conan-installed tools, a team convention),
  built from an empty folder, one stage at a time: the `stages:` list, then
  each stage in turn with its own provider and the supporting files it
  needs (compose file, requirements, conanfile, ...). Read this if you'd
  rather learn the schema by writing one than by reading the reference.
- **[`quickstart/examples.md`](quickstart/examples.md)** — the eight
  bundled, real (not illustrative) environments under `examples/`, smallest
  to largest, with what each one is meant to teach.

## Concepts

- **[`concepts/glossary.md`](concepts/glossary.md)** — every term denver
  uses, defined once: environment, stage, step, provider, resolved config,
  hook, fingerprint, wrapper relocation. Read this if a word in another doc
  is unfamiliar; the rest of the tree assumes these definitions.
- **[`concepts/philosophy.md`](concepts/philosophy.md)** — the design
  principles behind all of the above: genericity, explicit over implicit,
  central default resolution, fail loud on the unexpected,
  fast-but-never-at-the-cost-of-correctness, the monorepo rule, and
  reproducibility as a first-class goal. Read this to understand *why*
  denver refuses to guess things other tools guess for you.

## `denver` command

- **[`cli/arguments.md`](cli/arguments.md)** — every flag `denver run
  --help` lists, grouped by what you reach for it for: choosing what runs,
  changing the config for one run, trading speed against freshness, looking
  without running.
- **[`cli/completion.md`](cli/completion.md)** — tab-complete subcommands,
  env paths, flags and an env's own `denver-custom-args:` with `denver complete`; wired up
  automatically inside a docker-relocated shell too.
- **[`cli/environment-variables.md`](cli/environment-variables.md)** — the
  two variables denver itself reads (`DENVER_STATE_DIR`,
  `DENVER_CACHE_DIR`), the ones it exports for `${...}` interpolation, and
  where an environment's state lives on disk.

## Configuration

- **[`configuration/denver-toml.md`](configuration/denver-toml.md)** — the
  system, once. The complete `denver.toml` schema (every top-level key, every
  generic stage key), how a config is resolved (`import:` chain → merge
  rules → central default resolution), the mechanisms that make it flexible
  (layering, hooks, extension providers, `-c`/`-cf` overrides, `${...}`
  interpolation) and fast (fingerprints, `--fast`/`--force`), plus stage
  filtering and the wrapper/relocation model. This is the page to read
  before writing your own `denver.toml`.

## Providers

- **[`providers/`](providers/)** — one page per provider: a full key
  reference for that provider's `denver.toml` section (every key, what it
  does, its default) plus design notes on the patterns it supports and how
  it behaves under `--fast`/`--force`.

  | Provider | Purpose |
  |---|---|
  | [`uv`](providers/uv.md) | Create/manage a Python virtualenv via `uv` |
  | [`conan`](providers/conan.md) | Provision native tools (compilers, cmake, ninja) via Conan |
  | [`docker`](providers/docker.md) | Wrapper: relocate the pipeline into a compose service |
  | [`zephyr`](providers/zephyr.md) | Manage a West (Zephyr RTOS) workspace |
  | [`custom`](providers/custom.md) | Escape hatch: an arbitrary command, sourced script or launcher |

  Each provider's module docstring (`src/denver_providers/<name>.py`) carries
  the same key list as a terse lookup table kept next to the code, and
  points here for the full explanation, worked examples and rationale.

  A project can also register its own provider, without a denver fork —
  see "Extension providers" in
  [`configuration/denver-toml.md`](configuration/denver-toml.md).

## Contributing

- **[`contributing/development.md`](contributing/development.md)** — the
  contributor workflow: `uv run poe all`, the test suite and its fakes, why
  coverage is pinned at 100%, how `examples/*` doubles as golden-file
  fixtures, how to add a new provider, and how a release is cut.
