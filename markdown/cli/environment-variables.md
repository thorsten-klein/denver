# Environment variables

Flags are not the only way denver takes input. Two environment variables
change where it puts things — and unlike the flags on the previous page,
these are read from your shell rather than passed per run.

## What denver reads

- **`DENVER_ENV_DIR`** — the fallback for the `<env>` argument itself, when
  you omit it: `denver run` (with no path at all) uses this instead. Handy
  in a shell/CI that always works against the same env — export it once
  (e.g. in `.envrc`) and drop `<env>` from every `denver run ...` after
  that. An `<env>` actually given on the command line always wins over it.
- **`DENVER_STATE_DIR`** — an explicit root for denver’s per-env state
  (venv, install trees, fingerprints, logs, `performance.jsonl`), overriding
  the default location described in [Where an environment’s state lives]()
  below. Useful to put that state on a larger or faster disk.
- **`DENVER_CACHE_DIR`** — the shared *cache* root denver exports as
  `${DENVER_CACHE_DIR}` for an env to point a tool’s own download cache at
  (e.g. `CONAN_HOME`). Defaults to `~/.cache/denver`. denver never creates
  or reads it; it only offers the location, because such caches are
  content-addressed, safe to share between envs and checkouts, and expensive
  to duplicate.

**Those three are the whole list.** Every flag from the previous page
(`--force`, `--ci`, `--fast`, …) is set purely by the flag itself, never
inherited from a same-named real environment variable — so nothing about a
run silently changes because of what happens to be exported in the calling
shell.

## What denver exports

Going the other way, denver *sets* a handful of built-in variables in the
environment it builds — `DENVER_ENV_DIR`, `DENVER_ENV_NAME`,
`DENVER_ENV_WORKDIR`, `DENVER_SRC_DIR`, `DENVER_CACHE_DIR` — so your
scripts, compose files and the final command can read them as ordinary
variables. They are also what `${...}` interpolation inside a `denver.toml`
resolves against. The full list and their exact values are documented under
“Variable interpolation” in [Configuration](../configuration/denver-toml.md).

## Where an environment’s state lives

By default, **inside the environment’s own directory**:

```default
my-project/env/
├── denver.toml
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
variants (`denver.debug.toml`, `denver.release.toml`) — those are *different*
environments sharing a folder, and must not share a venv.

denver falls back to `~/.denver/<env>-<hash>` when it cannot write to the
env directory (a read-only mount, a vendored base env, an env shipped inside
an image), and `DENVER_STATE_DIR` overrides both.

> **Note**

> **Next:** [Configuration](../configuration/denver-toml.md) — the complete
> `denver.toml` schema: every key, how imports merge, and the mechanisms behind
> everything you have used so far.
