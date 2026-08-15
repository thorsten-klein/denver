# Porting the examples to flox

An honest attempt to express each of denver's seven bundled examples as a
[flox](https://flox.dev) environment, to find out where the two tools actually
disagree.

**These ports are unverified.** Neither flox nor nix is installed in the
environment this branch was produced in, so nothing here has been resolved,
locked or activated, and no `manifest.lock` is included. Catalog package names
and version availability that were assumed rather than checked are marked
UNVERIFIED inline. Treat this as a design comparison, not as working
configuration.

Ports are additive: each `.flox/env/manifest.toml` sits inside the example
folder whose `denver.yml` it was derived from. No example was modified.

## Result per example

| Example | Ported | Fidelity |
|---|---|---|
| `simple-env` | [`simple-env/.flox/env/manifest.toml`](simple-env/.flox/env/manifest.toml) | Partial — `on-activate` propagates exports well; no stage ordering |
| `zephyr-uv` | [`zephyr-uv/.flox/env/manifest.toml`](zephyr-uv/.flox/env/manifest.toml) | Partial — no venv primitive, so hand-rolled shell |
| `raspberry-pico` | [`raspberry-pico/.flox/env/manifest.toml`](raspberry-pico/.flox/env/manifest.toml) | Partial — pico-sdk not in the catalog |
| `zephyr-docker` | *not ported* | `flox containerize` exists but runs the other direction |
| `howto-env` | [`howto-env/.flox/env/manifest.toml`](howto-env/.flox/env/manifest.toml) | 4 of 5 stages |
| `zephyr-devshell` (base) | [`zephyr-devshell/.flox/env/manifest.toml`](zephyr-devshell/.flox/env/manifest.toml) | **Real base** — `[include] environments` |
| `zephyr-devshell-4.3.1` | [`zephyr-devshell-4.3.1/.flox/env/manifest.toml`](zephyr-devshell-4.3.1/.flox/env/manifest.toml) | Weak — layering ports, the west pipeline does not |

## Two things flox does better than expected

1. **`[include] environments` is real layering.** flox and devenv are the only
   two tools in this five-way comparison that reproduce denver's
   `import: [../sibling]` — naming another environment by directory, rather
   than flattening it (devbox, pixi) or relying on implicit parent-directory
   merging (mise). The merge rules differ from denver's, though: flox is
   last-wins and needs an explicit `flox include upgrade` to pick up changes in
   the base, where denver re-resolves the whole chain on every run and
   accumulates list-valued keys (`hooks:`, `recipe-dirs:`) across layers rather
   than overwriting them.

2. **`on-activate` is the closest match to denver's `source:` semantics** of any
   tool here — devenv included. It runs in a bash subshell whose *exported*
   variables propagate into the activated environment, so a script that
   **computes** what to export at runtime still works. devenv's `env.*` is
   static and its tasks are subprocesses whose exports die with them;
   `simple-env`'s `custom.sh` therefore ports better to flox than to devenv.

## What did not port, and why

1. **No task graph at all.** This is flox's biggest structural gap relative to
   denver, and the one that hurts `zephyr-devshell-4.3.1` most. mise has
   `depends`, devenv has `after`/`status`, flox has a single `on-activate` hook.
   So the five-stage Zephyr pipeline becomes ordering-by-line-number in one
   shell script, with **no per-step skip check** — meaning `west update` over a
   ~40-repo workspace would re-run on every activation unless you hand-write a
   guard. denver's `skip-if:` and per-stage fingerprinting have no counterpart.
   Once you move that work out of activation to avoid the cost, denver's actual
   promise — entering the environment gives you a *correct* workspace — is gone.

2. **No Python venv primitive.** Unlike devenv's `languages.python.venv` and
   mise's `_.python.venv`, flox has nothing first-class here, so every port
   above rebuilds `uv venv` + `activate` + `uv pip install` by hand in
   `on-activate` — close to the `system_venv.sh` script denver's `uv` provider
   says it replaced, and without denver's fingerprinting.

3. **The container wrapper.** `flox containerize` packages an activated
   environment as an OCI image. denver's `docker` stage relocates later stages
   into an image *you* name, with `--skip docker` running the same stack
   natively. Opposite directions, same word. `zephyr-docker` has no port.

4. **Packages that must be built.** `pico-sdk` is not in the catalog, and
   denver's recipe also builds picotool from source. flox installs catalog
   packages; it does not build arbitrary things.

5. **Exact version pins are at the catalog's mercy**, same as devbox and devenv.
   denver's conan recipes pin upstream URLs and checksums directly.

6. **Environment-specific CLI flags** (`args:`) and **`runnable: false`** have
   no equivalent.

## Verdict

flox lands between devenv and devbox. It matches devenv on layering and
actually beats it on runtime environment propagation, but it has no task graph
whatsoever, which makes it the weakest of the five for anything resembling
denver's ordered, fingerprinted, multi-stage pipeline. For a
mostly-declarative environment it is excellent; for `zephyr-devshell-4.3.1` it
is the poorest fit in the comparison.
