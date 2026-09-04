# Environment variables

Flags are not the only way denver takes input. A handful of environment
variables change where it puts things — and unlike the flags on the previous page,
these are read from your shell rather than passed per run.

## What denver reads

| Variable             | What it does                                                                                                                                                                                                                                      |
|----------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `DENVER_ENV_DIR`     | Fallback for `denver run <env>` argument, so you can run `denver run` instead. Handy in a shell/CI that always runs same env. An `<env>` given on the command line always wins over it. Unset by default — `<env>` must then be given explicitly. |
| `DENVER_ENV_WORKDIR` | Working directory for this environment’s files.                                                                                                                                                                                                   |
| `DENVER_CACHE_DIR`   | Directory where tools can persistently store files, e.g. caches.. Defaults to `~/.cache/denver`.                                                                                                                                                  |

## What denver exports

Going the other way, denver also *sets* a handful of built-in variables in the
environment it builds, so your scripts, compose files and the final command
can read them as ordinary variables. They are also what `${...}`
interpolation inside a `denver.yml` resolves against — see “Variable
interpolation” in [Configuration](../configuration/denver-toml.md).

Denver-owned identifiers always reflect the current run, even if a stale
variable of the same name was already exported in the calling shell:

| Variable              | Default                                 | What it holds                                                                                                                                                                                                     |
|-----------------------|-----------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `DENVER_ENV_DIR`      | the resolved `<env>` directory          | This environment’s own directory — the one holding its `denver.yml`.                                                                                                                                              |
| `DENVER_ENV_NAME`     | that directory’s name                   | e.g. `raspberry-pico` for an env at `.../envs/raspberry-pico/`.                                                                                                                                                   |
| `DENVER_ENV_WORKDIR`  | `<env dir>/.denver/<config file stem>/` | denver’s own working area for this environment (e.g. venv, install trees, fingerprints, logs, …). Can be overridden by `DENVER_ENV_WORKDIR`.                                                                      |
| `DENVER_CACHE_DIR`    | `~/.cache/denver`                       | Directory where tools can persistently store files, e.g. caches. Can be overwritten by `DENVER_CACHE_DIR`.                                                                                                        |
| `DENVER_SRC_DIR`      | wherever denver’s own code is installed | Rarely needed directly — mostly for a `custom` stage that has to reach into denver’s own package.                                                                                                                 |
| `SHELL_PROMPT_PREFIX` | `(<env name>) `                         | The marker text a shell’s prompt uses to show when it is running inside this environment, e.g. `(firmware-env) ` — see “The prompt marker” in [Configuration](../configuration/denver-toml.md#the-prompt-marker). |

## Where an environment’s state lives

By default, **inside the environment’s own directory**:

```default
my-project/env/
├── denver.yml
└── .denver/            # denver's state, ignores itself via its own .gitignore
    └── denver/         # one subdirectory per denver.*.<ext> config in this folder
        ├── .venv.host
        ├── .conan/
        └── performance.jsonl
```

State belongs with the environment that owns it: deleting a checkout deletes
exactly its own state, two checkouts of one project can never share (or
destroy) each other’s, and a `docker` stage carries it into the container
for free, since the workspace is already bind-mounted there.

The `<config file stem>` level exists because one folder may hold several
variants (`denver.debug.yml`, `denver.release.yml`) — those are *different*
environments sharing a folder, and must not share a venv.

Wherever it ended up, `denver run <env> --clean` or `denver clean <env>`
removes it — see [Remove an environment’s state](arguments.md#remove-an-environment-s-state).

> [!NOTE]
> **Next:** [Configuration](../configuration/denver-toml.md) — the complete
> config schema: every key, how imports merge, and the mechanisms behind
> everything you have used so far.
