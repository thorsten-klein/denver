# ADR-0001: Central default resolution

|             |                                                                                                                                                                      |
|-------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Status**  | accepted                                                                                                                                                             |
| **Affects** | [chapter 4.3](../04_solution_strategy/index.md), [chapter 5.7](../05_building_block_view/config_resolution.md), [chapter 8.5](../08_cross_cutting_concepts/index.md) |

## Context

Provider defaults used to be computed inline, in each provider’s `setup()`:

```python
python = cfg.get("python") or "3.12.3"
uv = cfg.get("uv") or shutil.which("uv")
```

Effects:

- `--show-config` showed only *explicitly configured* values. Anything defaulted was invisible.
- Two sources of truth. What the run used and what the tool printed could differ.
- Adding a default meant editing whichever provider happened to own it.
- Validation of a configured path happened halfway through a run, after other stages had already changed the machine.

## Decision

Every default moves into one `resolve_defaults(ctx, cfg, config)` per
provider, called by `resolve_provider_defaults()` in `stages:` order, before
any stage runs.

Rules:

- Explicit values pass through untouched.
- Everything else gets its default here, and only here.
- `setup()` reads keys that are guaranteed present. It never falls back.
- Optional keys with no default stay visible as `null` in `--show-config-full`.
- Defaults are static, PATH-derived, or derived from another section. Never derived from which files happen to exist ([ADR-0002](adr-0002-no-inferred-paths.md)).

## Consequences

Good:

- `--show-config` and the real run cannot disagree. Same function, not a copy.
- One place to add a default. One place to read one.
- Config errors surface before the first stage touches anything.

Cost, accepted:

- Resolution does real work: PATH lookups, existence checks. So `--show-config` can fail.
- That is deliberate — it doubles as an eager config validator. The trade is recorded in [chapter 11](../11_technical_risks/index.md).

Explicitly out of scope: runtime toggles (`--force`, `--ci`, `--fast`,
`-q`). Baking a per-invocation flag into a resolved value would defeat it.
