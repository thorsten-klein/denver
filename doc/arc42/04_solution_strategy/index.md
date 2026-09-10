# 4. Solution Strategy

Seven ideas. Chapter 5 shows the code that implements them; chapter 9 records
why each one won.

## 4.1 Declarative config, generic providers

`denver.yml` describes *what*. Providers know *how*. A provider hard-codes
nothing about a project.

- Change an environment → edit config.
- Need genuinely new behavior → add a provider.

Serves: extensibility, least duplication.

## 4.2 An ordered pipeline of stages

`stages:` is an ordered list of stage ids. Each id has a section, and that
section must name its `provider:` explicitly — even when the id and the
provider name are the same word.

- Two `uv` stages can exist side by side, with different venvs.
- Renaming a stage never silently changes its behavior. See [ADR-0005](../09_design_decisions/index.md#adr-0005-provider-is-mandatory-never-guessed-from-the-stage-id).

Serves: extensibility, transparency.

## 4.3 One central place for every default

Every provider default is computed once, centrally, before any stage runs —
never inside `setup()`. Providers only read keys that are already there.

Consequence: `--show-config` and the real run cannot disagree, because they
call the same function. See
[ADR-0001](../09_design_decisions/adr-0001-central-default-resolution.md).

Serves: transparency.

## 4.4 Nothing is inferred from the directory layout

A file that merely exists changes nothing. A path takes effect only when
config names it, and a named path that is missing is a fatal error.

Serves: transparency, reproducibility. See
[ADR-0002](../09_design_decisions/adr-0002-no-inferred-paths.md).

## 4.5 Two ways to inherit config

| Mechanism | Pulls in | Used for |
|---|---|---|
| Whole-file `import:` | another env's entire config, as a base | "this env is that base, plus a few pins" |
| Section-level `import:` | one named section from another env | "reuse that env's `docker:`, nothing else" |

Both use the same merge rules, so the mental model stays the same: base
first, this layer on top.

Serves: least duplication.

## 4.6 Loud failure over a silent guess

- Unknown top-level key, unknown stage key, unknown stage id, unknown provider → error, with the closest match suggested.
- Two layers setting the same string key to different values → error, unless the override is marked with `!`.
- A configured path that does not exist → error, at resolution time, before anything runs.

Serves: reproducibility, transparency.

## 4.7 Fast by default, and the container is only a relocation

Two speed decisions and one packaging decision, all in service of "the second
run must be cheap and the host run must always be possible":

- Every stage that does real work **fingerprints** its inputs and skips itself when nothing changed. `--fast` skips the check entirely, `--force` bypasses it.
- The `docker` provider does not build anything. It **relocates** the rest of the pipeline: it re-invokes denver inside the container with that wrapper stage skipped. Same stages, same config, host or container.
- Every provider talks to the world only through `Context.run()`/`which()` and `Context.write_text()`/`mkdir()`/…, so `--dry-run` and the offline test suite both work by intercepting one object.

Serves: speed, no Docker lock-in, testability.
