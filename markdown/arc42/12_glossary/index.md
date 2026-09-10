# 12. Glossary

Architecture terms, as these chapters use them. The user-facing vocabulary is
in [Glossary](../../concepts/glossary.md).

| Term                                     | Meaning                                                                                                                                                                         |
|------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **env**                                  | A directory holding a `denver.yml` (or `denver.toml`). The unit denver starts.                                                                                                  |
| **stage**                                | One entry in `stages:`, identified by a **stage id**. One provider instance.                                                                                                    |
| **stage id**                             | A label, nothing more. Never used to guess a type ([ADR-0005](../09_design_decisions/index.md#adr-0005-provider-is-mandatory-never-guessed-from-the-stage-id)).                 |
| **provider**                             | The generic engine a stage uses: `uv`, `conan`, `zephyr`, `docker`, `download`, `git`, `custom`. Named by the mandatory `provider:` key.                                        |
| **setup provider**                       | `kind = "setup"`. Builds part of the environment in place.                                                                                                                      |
| **wrapper provider**                     | `kind = "wrapper"`. Builds nothing; relocates the run (`docker`, or `custom` with `launcher:`).                                                                                 |
| **relocation**                           | Re-invoking denver elsewhere — usually in a container — with the wrapper stage skipped ([ADR-0004](../09_design_decisions/adr-0004-wrapper-relocation.md)).                     |
| **`Context`** (`ctx`)                    | The one object every provider gets: paths, `env`, resolved config, and every process and filesystem helper. The seam `--dry-run` and the tests intercept.                       |
| **whole-file `import:`**                 | Top-level. Pulls in another env’s entire config as a base.                                                                                                                      |
| **section-level `import:`** (“stacking”) | Inside one section. Pulls in one named section from another env.                                                                                                                |
| **`deep_merge`**                         | The one merge function behind both. Mappings merge, lists append, conflicting strings die unless marked `!`.                                                                    |
| **`!` marker**                           | “Override on purpose.” Replaces a string, or drops lower layers of a list. Only meaningful when a lower layer set the key.                                                      |
| **`<overwrite>`**                        | A bare list entry that drops lower layers and is itself removed.                                                                                                                |
| **`resolve_defaults`**                   | Per provider, called centrally before any stage runs. The only place a default is computed ([ADR-0001](../09_design_decisions/adr-0001-central-default-resolution.md)).         |
| **`resolve_full_config`**                | Stacking + `Context` + defaults, in one call. What both `--show-config` and the real run use.                                                                                   |
| **fingerprint**                          | A hash of a stage’s real inputs — content and layout, not absolute paths. Match means skip.                                                                                     |
| **hook**                                 | A script *sourced* at a fixed point (`env`, `pre-<stage>`, `post-<stage>`, `pre-cmd`), so its exports persist. Stacks additively across layers.                                 |
| **`scripts:`**                           | Open-ended one-shot actions per stage, run only via `--scripts <name>` (or `--setup`/`--login`/`--clean`).                                                                      |
| **state dir**                            | `<env dir>/.denver/<config stem>/`. venvs, install trees, fingerprints, logs, `performance.jsonl`, `.lock`. Overridable with `DENVER_ENV_WORKDIR`.                              |
| **`DENVER_RELOCATED`**                   | Names the wrapper stages that put this process where it is. Stated by denver, not detected.                                                                                     |
| **`DENVER_IN_CONTAINER`**                | Says this process is in a container. Set by a relocating wrapper, or probed.                                                                                                    |
| **extension provider**                   | A project’s own `Provider` subclass, registered via `extensions: providers: dirs:` ([ADR-0012](../09_design_decisions/index.md#adr-0012-extension-providers-instead-of-forks)). |
| **`runnable: false`**                    | Marks an env that exists only to be imported. Starting it directly fails with an explanation.                                                                                   |
| **`DenverError`**                        | The one user-facing failure type. Raised by `die()`, caught once in `main()`, turned into exit 1.                                                                               |
