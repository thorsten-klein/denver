# examples/doc-env

**Builds denver's own `doc/` tree (Sphinx + MyST + the Read the Docs theme)
via denver itself — a `uv` stage for the toolchain, a `custom` stage that runs
`sphinx-build`.**

## What it does

```console
$ denver examples/doc-env -- echo Done
...
build succeeded.

The markdown files are in doc/_build/markdown.
INFO: exec: echo Done
Done
```

Two stages run in order:

1. **`docs-tools`** (`uv`) — creates a venv with Sphinx, MyST-Parser,
   sphinx-rtd-theme, sphinx-copybutton and sphinx-markdown-builder, pinned
   in `requirements.txt`. The top-level project has no `docs`
   dependency-group of its own — this venv is the only place these are
   pinned.
2. **`build-docs`** (`custom`) — runs `sphinx-build -W --keep-going` against
   the repo's real `doc/` folder (`doc/conf.py`) twice: once with the `html`
   builder, once with the `markdown` one (a plain, one-file-per-page mirror
   meant for AI tools/LLMs), then copies the markdown output into the HTML
   output's own `markdown/` subdirectory so one publish step carries both.
   Not fingerprinted, so it reruns on every start — the right behavior for a
   doc build.

With no trailing command, `denver examples/doc-env` rebuilds the docs and
drops into a shell with `sphinx-build` on `PATH`.

## Why it exists

The other worked examples build software; this one builds the documentation
you're reading, using denver itself. It's not a demo of the pattern — it's
the actual build: the top-level `docs` poe task (`uv run poe docs`) and
`.github/workflows/docs.yml` both just run this env (`-- true`, so it builds
and exits instead of dropping into its shell) rather than calling
`sphinx-build` themselves. One recipe, not three kept in sync by hand — see
`doc/conf.py`'s own docstring.

## Files

| File | What it is |
|---|---|
| `denver.yml` | The two stages above |
| `requirements.txt` | Sphinx/MyST/sphinx-rtd-theme pins |

## Next

- [`doc/index.md`](../../doc/index.md) — the documentation this env builds
- [`doc/providers/uv.md`](../../doc/providers/uv.md) /
  [`doc/providers/custom.md`](../../doc/providers/custom.md) — the two
  providers this env uses
