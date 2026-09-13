# 1. Introduction and Goals

denver (**D**evelopment **Env**ironment Start**er**) starts a complete development
environment from one file.

One command — `denver run <env>` — turns a `denver.yml` into a working shell:
Python venv, native toolchain, Zephyr workspace, container. Whatever the file
declares, in the order it declares it. Same on a laptop and on CI.

## 1.1 Requirements overview

The problem:

- Every project writes its own entry script (`devshell.sh`, `run.sh`, a Makefile).
- Each new project copies the last one and re-adapts a few hundred lines of bash.
- Setup logic and environment description are mixed. Nothing is reusable.
- The only way to see what an environment does is to read the script.

denver splits that in two:

- **`denver.yml`** says *what* an env is: which stages, which files, which versions. No logic.
- **Providers** know *how*: `uv`, `conan`, `zephyr`, `docker`, `download`, `git`, `nix`, `custom`. Generic — every project detail comes from config.

Functional requirements:

| # | Requirement |
|---|---|
| R1 | Start a complete environment with one command; hand it to a shell or to a given command. |
| R2 | Run the same stack in a container or directly on the host (`--skip docker`) — one definition, not two. |
| R3 | Share config between envs by inheritance (whole file, or one section) instead of copy-paste. |
| R4 | Show what an env would do before it does it (`--show-config`, `--dry-run`). |
| R5 | Re-run cheaply — unchanged input, no expensive work. |
| R6 | Add a new kind of step without changing the orchestrator, and add a project's own provider without forking denver. |

## 1.2 Quality goals

Ranked by how much each one actually drove decisions (chapter 9 has the
decisions).

| # | Goal | Why |
|---|---|---|
| 1 | **Transparency** | No hidden defaults, no guessed paths. `--show-config` shows what the run will use, because both call the same function. See [ADR-0001](../09_design_decisions/adr-0001-central-default-resolution.md), [ADR-0002](../09_design_decisions/adr-0002-no-inferred-paths.md). |
| 2 | **Reproducibility** | Same config plus same inputs, same environment — laptop, CI, container, later. |
| 3 | **Speed on re-runs** | First run may be slow. Second must not be. Stages fingerprint their inputs and skip themselves; `--fast` skips even the check. Fingerprints use content, not timestamps. |
| 4 | **Extensibility** | New provider = one file + one registry entry. From outside the repo: one file in an `extensions: providers: dirs:` directory. |
| 5 | **Testability** | A provider reaches the world through two `Context` methods. Tests patch them. Whole suite runs offline, 100 % coverage gate. |
| 6 | **Least duplication in config** | Shared setup written once, inherited or stacked. A derived env states only the difference. |
| 7 | **No Docker lock-in** | Every env must run directly on the host. The container is a wrapper stage, not a precondition. |

## 1.3 Stakeholders

| Role | Concern |
|---|---|
| **Environment user** | Runs `denver run <env>`. Wants a working toolchain, fast on the second run, debuggable when it breaks. |
| **Environment author** | Writes the `denver.yml` and its assets (hooks, Conan recipes, compose files). Wants a small schema, predictable inheritance, errors that name the mistake. |
| **CI** | Runs denver non-interactively. Needs deterministic behavior and no prompt — denver refuses to guess a command when none is given and the terminal is not interactive. |
| **denver maintainer** | Adds providers, keeps the gates green (coverage, complexity, lint, types). Wants chapters 5 and 8 to stay small enough to hold in one head. |
