# About denver

![denver logo](../src/denver_assets/logo.svg)

**D**evelopment **Env**ironment Launch**er** — describe your environments as
Code in a `denver.toml`: reproducible and layerable to fit your project’s needs.

## What problem does denver solve?

Before you can build most projects, you first have to set your machine up.
Install a compiler. Get the right SDK, in the right version. Create a
virtualenv and install packages into it. Export a variable the build expects.

Usually that lives in a README section, or in a `setup.sh` somebody wrote
once. And that works — right up until it doesn’t:

- Nobody re-runs `setup.sh` after a `git pull`, so people quietly drift onto
  different versions of things.
- It works on your machine partly because of something you installed months
  ago and forgot about — and which was never written down anywhere.
- A colleague on a different Linux distribution hits a problem you have never
  seen and cannot reproduce.
- Six months later, nobody remembers why that one `export` is in there, so
  nobody dares remove it.

The common thread is that **the setup is a suggestion, not a definition.**
Nothing enforces it, nothing checks it, and nothing tells you when your
machine has drifted away from it.

denver replaces that suggestion with a file — `denver.toml` — that *defines*
the environment: what it needs, in what order. You then run:

```bash
denver run my-project
```

and denver builds whatever isn’t built yet, reuses whatever is, and drops
you into a shell with everything ready. Same file, same result, on your
machine and your colleague’s.

Three things follow from that, and they are most of why denver exists:

- **It is declarative, not a script.** Reading the `denver.toml` tells you
  what the environment is. There is no “…and then also run this other thing”
  hiding in someone’s shell history.
- **It is fast on repeat.** A second run checks what actually changed and
  skips the rest, so starting an environment you already built costs seconds,
  not minutes.
- **It is disposable.** Type `exit` and your machine is as it was. The
  environment did not install itself into your system.

How *much* setup you hand over to denver is entirely up to you — it ranges
from a single shell script to a full cross-compilation toolchain running
inside a container. [Examples](../quickstart/examples.md) walks that range,
smallest to largest.

## What is a denver environment?

Three words carry most of the model, so they are worth pinning down.

An **environment** is simply a directory containing a `denver.toml`, and it is
the thing denver launches: `denver run <env>`. The file describes it completely —
read the file and you know what the environment does. (`<env>` can also point
straight at a TOML file, so one directory can hold several variants side by
side, e.g. `denver.debug.toml` and `denver.release.toml`.)

`denver.toml` is the recipe; denver is the cook that follows it.

A **stage** is one step of that recipe. A `denver.toml` lists its stages in
order, and order matters: each stage leaves behind `PATH` entries,
environment variables and files that the *next* stage — and finally your
shell — can use. Think of getting dressed: underwear before trousers before
shoes.

Each stage names a **provider**, which is the code that knows *how* to run
that kind of step — creating a virtualenv, fetching a toolchain, entering a
container. You never write provider code; you configure it from
`denver.toml`.

That is the whole model: an environment is stages, in order, each run by a
provider. Everything else in this documentation is a detail of those three
words.

> **Note**

> Precise definitions of these and every other term denver uses live in the
> [Glossary](../concepts/glossary.md).

## Is denver flexible?

Absolutely. denver is designed to be highly flexible, giving you the full
freedom to describe your environment the way it actually works.

A handful of providers come bundled with denver, ready to be reused wherever
they fit. But using any of them is entirely optional — denver has no opinion
about which tools you should use, and a stage exists only because your
`denver.toml` lists it:

| If your project…                                                   | …you may use this provider                                        |
|--------------------------------------------------------------------|-------------------------------------------------------------------|
| has Python dependencies                                            | `uv` — a virtualenv managed by [`uv`](https://docs.astral.sh/uv/) |
| has native tools/toolchains to fetch                               | `conan`                                                           |
| needs a specific OS/system libraries                               | `docker`                                                          |
| is a west-based [Zephyr RTOS](https://zephyrproject.org) workspace | `zephyr`                                                          |
| runs a setup script, or anything else denver has no provider for   | `custom`                                                          |

Those five built-in providers are the *code that knows how to run* a kind of
stage; `custom` is the escape hatch for everything else, and a one-stage
`custom` environment is as legitimate as a five-stage one. **Most projects
need only one or two of these** — and if none of them suits you, describing
your whole environment with `custom` stages that run nothing but your own
scripts is a perfectly normal way to use denver.

Should you ever reach the point where you want a provider of your own — real
lifecycle behavior rather than a script — you can register one out-of-tree,
without forking denver. See “Extension providers” in
[Configuration](../configuration/denver-toml.md).

> **Note**

> **Next:** [Install denver](install.md), then
> [denver in 5 minutes](../quickstart/five-minutes.md) to watch all of this
> run in a real environment.
