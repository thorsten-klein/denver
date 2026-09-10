# Architecture (arc42)

This is denver’s architecture documentation, structured after
[arc42](https://arc42.org). The rest of `doc/` answers *how do I configure
this*; these twelve chapters answer *why is denver built this way* — what
problem it solves, what constrained the design, which decisions were taken
deliberately, and what is known to be weak about the result.

|   # | Chapter                                                          | Answers                                                       |
|-----|------------------------------------------------------------------|---------------------------------------------------------------|
|   1 | [Introduction and Goals](01_introduction_and_goals/index.md)     | What denver is for, who uses it, which qualities it optimizes |
|   2 | [Architecture Constraints](02_architecture_constraints/index.md) | What was fixed before any design choice was made              |
|   3 | [Context and Scope](03_context_and_scope/index.md)               | Where denver stops and the tools it drives begin              |
|   4 | [Solution Strategy](04_solution_strategy/index.md)               | The handful of ideas the whole design follows from            |
|   5 | [Building Block View](05_building_block_view/index.md)           | The static structure: modules, providers, config resolution   |
|   6 | [Runtime View](06_runtime_view/index.md)                         | What actually happens during a run                            |
|   7 | [Deployment View](07_deployment_view/index.md)                   | How denver ships and where its state lives                    |
|   8 | [Cross-cutting Concepts](08_cross_cutting_concepts/index.md)     | Mechanisms no single building block owns                      |
|   9 | [Architecture Decisions](09_design_decisions/index.md)           | The decision records, with their context and consequences     |
|  10 | [Quality Scenarios](10_quality_scenarios/index.md)               | The quality goals, made concrete and checkable                |
|  11 | [Technical Risks](11_technical_risks/index.md)                   | Known risks and technical debt                                |
|  12 | [Glossary](12_glossary/index.md)                                 | The terms these chapters use precisely                        |

#### NOTE
Reading order, if you are new: chapter 1 for the problem, chapter 4 for the
five ideas that answer it, then chapter 5 for the code that implements them.
Chapters 2, 3, 7, 10, 11 are reference material; read them when a specific
question sends you there.

These chapters describe the design; they do not restate the schema or the
CLI. Where a chapter names a key or a flag, it links to the page that owns
its full reference:

- [Configuration](../configuration/denver-toml.md) — every `denver.yml` key,
  the complete merge and resolution rules.
- [Arguments](../cli/arguments.md) — every CLI flag.
- [Providers](../providers/uv.md) — one page per provider, its keys, and what
  `--fast`/`--force` mean for it.
- [Philosophy](../concepts/philosophy.md) — the same principles as chapter 4,
  stated as principles rather than as architecture.
- [Glossary](../concepts/glossary.md) — the user-facing vocabulary; chapter 12
  is the architecture-facing subset of it.
