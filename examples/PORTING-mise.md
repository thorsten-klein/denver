# Porting the examples to mise

An honest attempt to express each of denver's seven bundled examples as a
[mise](https://mise.jdx.dev) config, to find out where the two tools actually
disagree.

**Most of these ports are unverified**, with one important exception:
`zephyr-devshell-4.3.1` has since been **built and run for real** — see
"Verified build" below. Everything else was written to spec and not executed;
backend/tool identifiers that were assumed rather than checked are marked
UNVERIFIED inline.

## Verified build (`zephyr-devshell-4.3.1`)

```
mise run build-hello-world
  -> west build -p always $ZEPHYR_BASE/samples/hello_world -b native_sim/native/64
```

**Result: passes.** 92/92 ninja targets, `zephyr.elf` linked, and the
resulting `zephyr.exe` runs:

```
*** Booting Zephyr OS build 75f67d766726 ***
Hello World! native_sim/native/64
```

Verified with mise 2026.8.6, python 3.12.3, cmake 4.4.2, ninja 1.13.2 and
west 1.5.0 (all installed by mise), against a workspace at Zephyr v4.3.1
(54 repos, 2.2 GB) cloned from this env's own
[`west.yml`](zephyr-devshell-4.3.1/west.yml).

### What that does and does not prove

Three changes were needed, each a finding in its own right:

1. **`python.github_attestations = false` in `[settings]`.** Without it,
   installing python 3.12.3 fails outright — mise verifies GitHub artifact
   attestations by default, and the python-build-standalone release for 3.12.3
   (April 2024) predates them. Pinning the interpreter denver pins therefore
   means opting out of a supply-chain check. That is a genuinely awkward
   trade, and it is not one denver's `uv` stage has to make.
2. **`uv_create_args = ["--seed"]` on the venv.** mise creates the venv with
   uv, which does not seed pip. Zephyr's build-time python packages cannot be
   declared up front (gap 4 below), so they must be installed imperatively into
   that venv — which then needs a pip inside it. Without `--seed` the install
   fails with "No module named pip".
3. **`ZEPHYR_TOOLCHAIN_VARIANT=host`**, since `native_sim` compiles for the
   host.

And two things it does **not** prove:

> **`ZEPHYR_BASE` points at a workspace mise did not create.** The build ran
> against a workspace cloned beforehand by a bootstrap `west`, because
> `west update` has to happen before the dependency set in point 2 is even
> knowable. This verifies that a mise environment can build Zephyr; it does
> not verify that mise can bring that environment into existence from a clean
> checkout the way `denver examples/zephyr-devshell-4.3.1` does.

> **The environment is not self-contained.** `gcc`, `g++`, `dtc` and `gperf`
> had to be present on the host already — they came from `apt` when this was
> verified. `native_sim` compiles for the host, and mise has no backend for a
> C toolchain or for distro packages. The pixi port of the same env takes all
> four from conda-forge and *is* self-contained; this one is not. That is
> mise's documented gap showing up in practice rather than in prose.

Ports are additive: each `mise.toml` sits next to the `denver.yml` it was
derived from. No example was modified.

## Result per example

| Example | Ported | Fidelity |
|---|---|---|
| `simple-env` | [`simple-env/mise.toml`](simple-env/mise.toml) | Partial — `_.source` ports well; no CLI args, no ordered pipeline |
| `zephyr-uv` | [`zephyr-uv/mise.toml`](zephyr-uv/mise.toml) | **Near-full** — mise even installs `uv` itself, which denver requires pre-installed |
| `raspberry-pico` | [`raspberry-pico/mise.toml`](raspberry-pico/mise.toml) | Partial — pico-sdk needs *building*, which mise does not do |
| `zephyr-docker` | *not ported* | No equivalent — mise has no container concept at all |
| `howto-env` | [`howto-env/mise.toml`](howto-env/mise.toml) | **4 of 5 stages, very cleanly** — see below |
| `zephyr-devshell` (base) | [`zephyr-devshell/mise.toml`](zephyr-devshell/mise.toml) | Only inherits if the version envs become child *directories* |
| `zephyr-devshell-4.3.1` | [`zephyr-devshell-4.3.1/mise.toml`](zephyr-devshell-4.3.1/mise.toml) | Duplicates the base; west stages run on demand, not on entry |

## The sharpest finding

`howto-env`'s `nvim-by-hand` stage is ~60 lines across three files
([`install.sh`](howto-env/nvim/install.sh) — download, checksum, atomic unpack,
idempotence; [`activate.sh`](howto-env/nvim/activate.sh) — PATH;
[`nvim.env`](howto-env/nvim/nvim.env) — the pins). It is deliberately written
by hand so `doc/how-to.md` can show what that job costs, immediately before
showing conan doing it properly.

In mise that stage is:

```toml
"ubi:neovim/neovim" = "0.12.4"
```

That is not a criticism of the example — the example is *teaching* exactly this
— but it is worth being clear-eyed that a reader who already knows mise will
look at that stage and ask why denver is involved.

## What mise does better

- **Installs the tools denver requires.** denver's pre-conditions table says
  `uv` must already be on PATH, `conan` usually via an earlier `uv` stage, and
  `west` likewise. mise installs all three as managed, version-pinned tools.
  denver's `uv` → `conan` stage ordering exists solely to solve a bootstrap
  problem mise does not have.
- **Binary releases from GitHub.** The `ubi:` backend is a direct, declarative
  replacement for hand-rolled download/checksum/unpack scripts.
- **Venv handling.** `_.python.venv = { create = true }` is close to a
  like-for-like replacement for denver's `uv` stage.
- **`_.source`** genuinely reproduces denver's `source:` semantics, including
  merging across config layers the way denver's `hooks:` lists do.

## What did not port, and why

1. **The container wrapper.** mise has no container concept. `zephyr-docker`
   has no port, and `howto-env` loses its first stage. If a project genuinely
   only builds on Ubuntu 24.04, mise does not address that at all — this is the
   largest single gap of the five tools compared.

2. **Inheritance is containment, not naming.** denver's
   `import: [../zephyr-devshell]` names its base explicitly, from a sibling
   folder, and one env can import several. mise merges configs from *parent
   directories*, so inheritance is a property of where a folder sits in the
   tree. Reproducing the base/derived split would require moving
   `zephyr-devshell-4.3.1/` to `zephyr-devshell/4.3.1/`, and even then an env
   could not inherit two bases or a base elsewhere in the repo.

3. **Building, as opposed to installing.** `pico-sdk` needs fetching *and*
   compiling picotool. mise installs prebuilt tools; there is no backend for
   "clone this and cmake it". That stage degrades to a shell script — the
   unmaintained-`setup.sh` problem denver's README opens with.

4. **Activation-time pipelines.** denver runs stages in order when you enter
   the env, fingerprinted so unchanged work is skipped. mise's task graph can
   express the *ordering* (`depends`), but tasks run on demand. Under mise,
   whether your west workspace is current is something you remember; under
   denver it is checked every time.

5. **Environment-specific CLI flags.** No mise equivalent to denver's `args:`.

## Verdict

For `zephyr-uv` and most of `howto-env`, mise is simpler than denver and pulls
its own weight. Its gap is the opposite of pixi's: pixi can supply almost any
*library* but no container; mise can supply almost any *binary tool* but also
no container, and cannot build anything that is not already published as a
release artifact. Both leave `zephyr-docker` and the harder half of
`zephyr-devshell-4.3.1` untouched.
