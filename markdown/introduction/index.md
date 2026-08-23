# About denver

<img src="https://raw.githubusercontent.com/thorsten-klein/denver/develop/src/denver_assets/logo.svg" alt="denver logo" width="500"/>

**D**evelopment **Env**ironment Launch**er** — describe your environment as
code in a `denver.toml`: it consists of stages that build on top of each other.
That layering is what makes it flexible and adjustable to fit to your project’s needs.

## What problem does denver solve?

Before you can build most projects, you first have to set your machine up.
Install some tools, download dependencies in the right version, create a
virtualenv and install packages into it, export environment variables to
control tools, …

Usually all that lives in a README section, or in a script like `setup.sh`
that somebody wrote once. And that works — right up until it doesn’t:

- Nobody re-runs `setup.sh` after a `git pull`, so people quietly drift onto
  different versions of things.
- It works on your machine partly because of something you installed months
  ago manually and forgot about to add to the README or the script — and it
  was never written down anywhere.
- Six months later, nobody remembers why that one `export` is in your local
  config file, so nobody dares remove it.

The common thread is that **the setup is an initial suggestion, not a definition.**
Nothing enforces it, nothing checks it, and nothing tells you when your
machine has drifted away from it.

denver replaces that suggestion with a file — `denver.toml` — that *defines*
the environment: `denver` creates the environment exactly as you declare it in
this file. You then run:

```bash
denver run my-project
```

and denver takes care about the environment setup. Same command, same result,
on your machine and your colleague’s.

Few things follow from that, and they are most of why denver exists:

- **It is declarative, not a script.** Reading the `denver.toml` tells you
  the facts what the environment is - without scripting language overhead.
- **It is fast on repeat.** A second run checks what can be reused and skips
  the rest, so starting an environment you already built costs milliseconds,
  not minutes.
- **It is scalable.** Simply switch between multiple projects without having
  to modify anything on your host system.

How *much* setup you hand over to denver is entirely up to you — it ranges
from a single shell script to a full cross-compilation toolchain running
inside a container. See [Examples](../quickstart/examples.md).

## Why denver and not writing your own scripts?

For a handful of `apt install`s and few `export`, a `setup.sh` is honestly
the better choice — no config file format to learn, no extra tool dependency.

`denver` starts paying off once a project outgrows that:

- **Reuse.** A `custom` stage is a script too — just one denver can run
  from more than one environment, instead of copy-pasting it around.
- **Composable.** `import:` lets a `denver.toml` inherit another one’s
  entire stage stack as a base, then override or adapt just the sections
  that need to differ — a project-specific environment on top of a shared
  one, instead of forking the whole file.
- **Selective runs.** `--until <stage>` and `--skip <stage>` run *part* of
  the environment — without you hand-rolling flags for that in your own script.
- **A shared shape.** Once more than one project uses denver, every
  `denver.toml` looks the same — a teammate who has read one environment
  already knows how to read the next.

If none of that matters — the project is small, one person maintains it,
nobody needs to skip part of it — a plain script is not a mistake. `denver` is
for when it stops being one.

## What is a denver environment?

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

> [!NOTE]
> Precise definitions of these and every other term denver uses live in the
> [Glossary](../concepts/glossary.md).

## Is denver flexible?

Absolutely. denver is designed to be highly flexible, giving you the full
freedom to describe your environment the way it actually works.

A handful of providers come bundled with denver, ready to be reused wherever
they fit. But using any of them is entirely optional — you can also declare
your environment fully from scratch with own scripts. `denver` has no opinion
about which other tools you use or call in a stage:

| If your project…                                                   | …you may use this provider                                        |
|--------------------------------------------------------------------|-------------------------------------------------------------------|
| has Python dependencies                                            | `uv` — a virtualenv managed by [`uv`](https://docs.astral.sh/uv/) |
| has native tools/toolchains to fetch                               | `download` or `conan`                                             |
| needs a specific OS/system libraries                               | `docker`                                                          |
| is a west-based [Zephyr RTOS](https://zephyrproject.org) workspace | `zephyr`                                                          |
| runs a setup script, or anything else denver has no provider for   | `custom`                                                          |

Those built-in providers are just the *code that knows how to run* a kind of stage.
If none of the built-in providers suits you, describing your whole environment with
`custom` stages that run nothing but your own scripts is a perfectly valid way to use denver.

Should you ever reach the point where you want to create a new provider on your own,
you can register one out-of-tree, without forking denver. See “Extension providers” in
[Configuration](../configuration/denver-toml.md).

> [!NOTE]
> **Next:** [Install denver](install.md), then
> [denver in 5 minutes](../quickstart/05-minutes.md) to watch all of this
> run in a real environment.
