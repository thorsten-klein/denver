# Porting the examples to devbox

An honest attempt to express each of denver's seven bundled examples as a
[devbox](https://www.jetify.com/devbox) project, to find out where the two
tools actually disagree.

**These ports are unverified.** Neither devbox nor nix is installed in the
environment this branch was produced in, so nothing here has been resolved or
run. Package versions that were assumed rather than checked against devbox's
search index are marked UNVERIFIED inline. Treat this as a design comparison,
not as working configuration.

Ports are additive: each `devbox.json` sits next to the `denver.yml` it was
derived from. No example was modified. All five files parse as JSON.

## Result per example

| Example | Ported | Fidelity |
|---|---|---|
| `simple-env` | [`simple-env/devbox.json`](simple-env/devbox.json) | Partial — one `init_hook`, not an ordered pipeline |
| `zephyr-uv` | [`zephyr-uv/devbox.json`](zephyr-uv/devbox.json) | Good, but the venv is hand-rolled shell again |
| `raspberry-pico` | [`raspberry-pico/devbox.json`](raspberry-pico/devbox.json) | **Best of the five tools here** — nixpkgs has the ARM toolchain |
| `zephyr-docker` | *not ported* | Partial concept only — see below |
| `howto-env` | [`howto-env/devbox.json`](howto-env/devbox.json) | 4 of 5 stages; docker stage inverted, not reproduced |
| `zephyr-devshell` (base) | *folded into 4.3.1* | No inherit-only environment concept |
| `zephyr-devshell-4.3.1` | [`zephyr-devshell-4.3.1/devbox.json`](zephyr-devshell-4.3.1/devbox.json) | Weak — flattened base, west stages manual |

## The one genuinely interesting finding

devbox is the **only** one of the five tools compared that has any container
story at all: `devbox generate dockerfile` and `devbox generate devcontainer`.

But the direction is reversed, and that matters more than it first looks:

- **denver**: you name an image (`ubuntu:24.04` + its apt packages), and the
  `docker` stage relocates every later stage into it. `--skip docker` runs the
  identical stack on the host.
- **devbox**: it resolves an environment from nixpkgs, then can package *that*
  as an image.

So devbox answers "ship this environment as a container." It does not answer
"this project only builds on Ubuntu 24.04 with these apt packages," which is
what `zephyr-docker` and `howto-env`'s first stage are for. Same word,
different problem.

## What devbox does better

- **nixpkgs breadth.** `raspberry-pico`'s ARM toolchain — a whole conan recipe
  in denver — is one line here, and `howto-env`'s hand-rolled ~60-line
  `nvim-by-hand` stage is also one line. For anything already in nixpkgs,
  devbox wins outright.
- **Binary cache.** Nix's substituters mean prebuilt, so "no compiling on your
  machine" (which denver's README sells as a property of its conan recipes)
  comes free.
- **No Nix language required**, which is the entire pitch and holds up.

## What did not port, and why

1. **No venv concept.** Unlike devenv and mise, devbox has nothing first-class
   for Python virtualenvs, so every port above rebuilds `uv venv` + `activate` +
   `uv pip install` in `init_hook` — which is close to the `system_venv.sh`
   script denver's `uv` provider says it was written to replace. Also loses
   denver's fingerprinting: the install re-runs on every shell entry.

2. **`init_hook` is one hook, not a pipeline.** denver's model is ordered stages
   where stage N's exports feed stage N+1, each fingerprinted, with `--fast` and
   `--force`. devbox has a single hook and on-demand scripts. `simple-env`
   exists entirely to demonstrate that ordering, and cannot be ported faithfully
   — its `print-vars-before` stage would print the variables as *already set*,
   demonstrating the opposite of the point.

3. **No inheritance.** No `import: ../sibling-env`, and no notion of a
   non-runnable base env (`runnable: false`). `zephyr-devshell` has no port at
   all; its contents were flattened into `zephyr-devshell-4.3.1/devbox.json`,
   destroying the property the split exists to show.

4. **Building, not just installing.** `pico-sdk` is not in nixpkgs, and denver's
   recipe also builds picotool from source. Fixing that means writing a Nix
   derivation — precisely the thing devbox positions itself as letting you avoid.

5. **Exact version pinning is at nixpkgs' mercy.** `cmake@3.31.9` and
   `neovim@0.12.4` resolve only if some nixpkgs revision built exactly that.
   denver's conan recipes pin the upstream release URL and checksum, so they do
   not depend on a third party having packaged the version you need.

6. **Environment-specific CLI flags.** No devbox equivalent to `args:`.

## Verdict

devbox is the strongest of the five on *package availability* and the only one
with even a partial container answer. It is the weakest of the five on
*structure*: no venv primitive, no ordered pipeline, no inheritance. For
`raspberry-pico` it is a clear improvement on denver; for
`zephyr-devshell-4.3.1` it is a downgrade on every axis except package sourcing.
