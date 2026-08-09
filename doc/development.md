# Development

Contributions are welcome. The short version: fork, branch off `develop`,
make sure `uv run poe all` passes (including 100% coverage), open a PR.

## Quick start

```bash
git clone https://github.com/thorsten-klein/denver.git
cd denver
uv sync --group dev

uv run poe all      # lint + format + mypy + test, in one go
```

Or run each stage on its own:

```bash
uv run poe lint      # ruff check .
uv run poe format    # ruff format .
uv run poe mypy      # mypy
uv run poe test      # pytest, with coverage
```

`uv run poe clean` removes build artifacts (`dist/`, `build/`, `*.egg-info`,
`htmlcov/`, `.coverage`, `coverage.xml`); `uv run poe build` cleans then
builds the wheel/sdist (`uv build`).

## Test suite

Everything lives under `tests/`, run with `pytest` (`pyproject.toml`'s
`[tool.pytest.ini_options]` points it at `src/` via `pythonpath`, so `import
denver` / `import providers` work without installing the package first).

**Nothing here touches a real tool.** `tests/conftest.py` provides three
fakes, requested as fixtures:

- **`run_recorder`** — replaces `subprocess.run`. Records every call
  (`.commands()` for joined-string matching, `.argvs()` for real argv-list
  matching — prefer the latter for flag/value-adjacency checks) and returns
  a configurable canned response (`.responses["substring"] = FakeProc(...)`
  or a callable). A `bash -c ...` call passes through to the *real*
  `subprocess.run` unless explicitly overridden, so `Context.source()`
  behavior stays real even while every other tool is mocked.
- **`which`** — replaces `shutil.which`. A dict `{name: path-or-None}`;
  anything not in the dict resolves to `/usr/bin/<name>` by default.
- **`exec_recorder`** — replaces `os.execvpe` (what `Context.exec()` uses to
  hand off to the final command). Captures `file`/`args`/`env` instead of
  actually replacing the process.

**Golden-file tests** (`tests/test_golden_show_config.py`) are the one place
that *is* end-to-end: for every env under `examples/` tracked in git, it runs
the real `--show-config` resolution against the real `denver.yml` and
compares the output to a checked-in snapshot in `tests/golden/`, with this
checkout's own absolute path normalized to `<REPO>` and the zephyr
provider's workspace-root lookups faked (both documented in that test
module). If you change something that legitimately changes a real env's
resolved config (a provider default, a merge rule, ...), regenerate the
affected golden file(s) rather than hand-editing them — run `--show-config`
for the env, apply the same `<REPO>` substitution, and diff the result
against what changed.

## Coverage

`pyproject.toml`'s `[tool.coverage.report]` sets `fail_under = 100`.
Python has no compiler to catch a branch nobody ever exercises — 100%
coverage is the substitute for that safety net, not a vanity metric.

A branch that's real but that coverage.py can't reliably trace (rare — one
known case: `continue` as a `for` loop's last statement, under Python 3.9)
is marked `# pragma: no cover` with a comment explaining *why* it's excluded
rather than restructured to dodge the tool. Don't reach for `# pragma: no
cover` to paper over a genuinely untested branch — it's an escape hatch for
a tooling limitation, not a way to hit 100% faster.

## Linting / formatting / types

- **`ruff`** does both linting and formatting (`pyproject.toml`'s
  `[tool.ruff]`/`[tool.ruff.lint]`/`[tool.ruff.format]`). `examples/` and `bak/`
  are excluded — they're project-specific conan recipes and scratch, not
  denver's own package. Tests are exempt from a few rules
  (`[tool.ruff.lint.per-file-ignores]`): unused fixture/mock-callback
  arguments are idiomatic pytest, and a test's own name is its
  documentation, so docstrings aren't required there.
- **`mypy`** type-checks `src/` only (`[tool.mypy]`). `conan_scripts/`'s own
  sibling-module imports (`get_rrev`, `DenverConanFile`) and the optional
  `conan`/`colorama` dependencies (test-only, see the `conan-tools`
  dependency group) both need `ignore_missing_imports` overrides — see the
  comments next to each in `pyproject.toml` for why.

## Adding a new provider

1. Subclass `Provider` (`src/providers/base.py`): set `name`, `KEYS` (every
   `denver.yml` key your section understands), and `kind` if it's a wrapper
   (default: `setup`).
2. Implement `resolve_defaults(cls, ctx, cfg, config)` — a classmethod, and
   the *only* place your provider computes a default (a PATH lookup, a
   conventional value, ...). No I/O side effects beyond path
   resolution/existence checks and `die()`ing on a bad config — this runs
   for `--show-config` too, not just a real run, so it must never guess
   differently depending on whether setup() will actually run afterward.
3. Implement `setup(self, ctx)` (a setup provider) and/or `wrap(self, ctx,
   cmd)` (a wrapper provider) — this is where the real work (and any real
   I/O) happens, reading `self.config_section(ctx)` for the
   already-resolved config.
4. Register it: add `"<name>": YourProvider` to `PROVIDERS` in
   `src/providers/__init__.py`.
5. Write tests: unit tests driving your provider directly against a fake
   `Context` (`make_context` fixture) with `run_recorder`/`which` mocking
   whatever it shells out to — see any existing `tests/test_providers_*.py`
   for the pattern. If a bundled example env exercises the new provider,
   the golden-file test picks it up automatically once you regenerate its
   snapshot.
6. Document it: add `doc/providers/<name>.md` (key reference + design
   notes), link it from `doc/README.md`'s provider table and the top-level
   `README.md`, and point the module docstring at the new page.

## CI

`.github/workflows/ci.yml` runs on every push to `develop` and every pull
request, in three jobs:

- **lint** — `ruff format --check`, `ruff check`, `mypy`.
- **test** — `uv run poe test` on Python 3.9 and 3.13: the floor denver
  declares support for (`pyproject.toml`'s `requires-python`) and the newest
  available, so a change that only works at one end doesn't slip through.
  Coverage and test results are uploaded to Codecov from the 3.13 run.
- **build** — builds the wheel/sdist, then installs it into a clean venv and
  runs `denver --help` plus a real `--show-config` against a bundled example,
  with `-c denver-version=null` to drop that example's pin (a dev-versioned
  wheel can never satisfy one naming an untagged release — see "Releasing").
  That installed-mode smoke test exists because a checkout has a sibling
  `src/` layout that an installed wheel doesn't — exactly the gap that once
  let a `DENVER_DIR` bug through (see `denver.py`'s `_default_denver_dir()`
  docstring).

## Releasing

Versions come from git tags via `setuptools-scm` — a released artifact has
no version string in a file. Cutting a release is therefore just tagging:

```bash
git tag 1.1.0
git push origin 1.1.0
```

`.github/workflows/publish.yml` triggers on any `*.*.*` tag: it builds the
distribution with `uv run poe build` and publishes it to
[PyPI](https://pypi.org/project/denver-tool/) via trusted publishing (OIDC,
no API token stored in the repo), through the `pypi` GitHub environment.

Before tagging: make sure CI is green on the commit being tagged, that
`README.md`/`doc/` describe what actually ships, and that
`SUPPORTED_CONFIG_VERSION` in `src/denver.py` still matches the `denver.yml`
schema — it must be bumped together with any breaking schema change, so an
older denver rejects a newer file instead of misreading it.

A `denver.yml` states which denver *tool* version it needs with
`denver-version:` (see [`architecture.md`](architecture.md)), so a file
using a brand-new feature names the release that first shipped it. When an
example under `examples/` is changed to rely on something unreleased, its
`denver-version:` names the version about to be tagged — a pin for a release
that does not exist yet.

`DEV_VERSION` in `src/denver.py` is what keeps that working. A checkout's
tags necessarily lag behind its content: right after the feature lands,
`git describe` still reports the *previous* release, so the example would
refuse to run from source until the tag existed. `scm_version()` therefore
reports an untagged tree against `DEV_VERSION` instead, carrying the commit
suffix over (`1.1.0-17-gabc1234` — seventeen commits into developing 1.1.0,
not a claim to be the release). So:

- **Bump `DEV_VERSION` as soon as a cycle starts**, i.e. at the first commit
  past the release tag. Two tests in `tests/test_dev_version.py` enforce it:
  `test_examples_run_from_a_checkout` fails if any example's merged pin isn't
  satisfied by what this checkout reports, and
  `test_dev_version_keeps_up_with_the_release_tags` fails as soon as there
  are commits past the newest tag and `DEV_VERSION` still names that tag —
  which catches a forgotten bump before any pin has moved to expose it.
- **Tag exactly that number.** Once the tag is pushed, `git describe`
  overtakes `DEV_VERSION` and it stops having any effect until the next
  bump, so a value left stale can only understate an untagged tree — never
  overstate a released one.
- Sitting *exactly* on a tag is never re-based: that tree really is that
  release, whatever `DEV_VERSION` says.

## Known limitations

### `import yaml` must work wherever denver runs

PyYAML is denver's only runtime dependency, and a `pip`/`uv` install brings
it along — but that only settles the *host*. A wrapper provider re-invokes
denver inside the environment it just entered (`reinvoke_command()` in
`src/denver.py` builds `["python3", <this file>, ...]`, a bare command
resolved against the container's `PATH`), so a docker-wrapped env imports
PyYAML from the **image**, which no host-side install can influence. Every
such image has to provide it — `python3-yaml` on Debian/Ubuntu, as both
example images under `examples/` do.

The `try: import yaml` guard at the top of `src/denver.py` exists for this
case alone: it names the failing interpreter (`sys.executable`) and points
at the Dockerfile, because the bare `ImportError` came from a process the
user never invoked by name and reads as a denver bug. It carries
`# pragma: no cover` — it is a property of the interpreter denver is running
on, so the suite cannot reach it.

### PyYAML's version is intentionally unconstrained

The host copy and the image copy come from different package managers, and
denver makes no attempt to keep them in lockstep — no pin forwarded into the
container, no runtime version assertion. The tradeoff is deliberate and rests
on how little of the library is used: **`yaml.safe_load` and
`yaml.safe_dump`, nothing else**. Two functions whose behaviour has been
stable across PyYAML releases for years, so importing whichever version
happens to be present is a risk worth taking against the alternative —
mandating one version and making every downstream image track denver's
choice.

`pyproject.toml` declares `pyyaml>=6`: a floor documenting the API denver
codes against, not a pin, and one that binds only the host install.

Keep it that way when contributing. Reaching for a third `yaml.*` function
(or any second runtime dependency) is what would invalidate the reasoning
above, and turns a documented limitation into a real compatibility matrix.
Dependencies that are only needed for development or tests belong in a
`[dependency-groups]` entry, where they never reach a user's environment.
