#!/usr/bin/env python3
"""denver -- Development Environment Launcher.

Launch a reproducible development environment described by a ``denver.yml``
file: denver resolves it (following ``import:`` inheritance), then runs the
generic *providers* its ``stages:`` list names (uv, conan, zephyr, docker,
...) to build/enter the environment purely from config.

denver's own CLI is subcommand-based:

    denver run <env> [flags] [-- command]
    denver run <env> --scripts <name> [flags]
    denver run <env> --show-config [flags]
    denver complete [bash|fish|zsh]

``run`` is the normal entry point: with no command, denver starts an
interactive shell; a command after '--' (e.g. `denver run <env> -- echo
hi`) is forwarded as-is instead. ``--scripts <name>`` runs one of the env's
own ``scripts:`` entries instead of the normal pipeline (e.g. ``setup``,
``login``; with no name, lists the names this env defines). ``--setup`` and
``--login`` are shorthand for ``--scripts setup`` and ``--scripts login``.
``--show-config`` prints the fully resolved, deep-merged config as YAML and
exits, dropping every key left unset so only keys with an actual value
remain -- YAML by default regardless of whether the env itself is a
denver.yml or a denver.toml, or TOML with ``--format toml`` (``--format
yml`` is the explicit spelling of the default). ``--show-config-full``
does the same but keeps every key left unset too, each shown as an
explicit ``key: null`` line (YAML) or a commented-out ``# key = null``
line (TOML has no ``null``).

``complete`` prints a script wiring up completion for the installed
``denver`` command, for the given shell (bash/fish/zsh) or, if omitted,
the one it's run from (auto-detected); wire it up with e.g. ``eval
"$(denver complete)"`` in your shell rc file (fish: ``denver complete |
source``).

<env> is a path to a directory containing a denver.yml/denver.yaml (or
denver.toml, if there's no denver.yml/denver.yaml -- requires Python
3.11+, since tomllib is stdlib only from there), or a path directly to a
config file (any name, e.g. denver.debug.yml). If omitted, it falls back
to the DENVER_ENV_DIR environment variable.

Run `denver run --help` to see more details about the run-specific flags.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import importlib.metadata
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import NoReturn, cast

import yaml

# denver.toml support is optional: tomllib is stdlib only from Python 3.11,
# so on an older interpreter it just isn't there. denver.yml/denver.yaml is
# the default format -- PyYAML is a required dependency (denver's floor,
# ">=3.9", is set by what PyYAML itself supports, not by tomllib). The
# sys.version_info guard (rather than try/except ImportError) is what lets
# mypy -- itself running on 3.11+ -- statically know the else branch is the
# live one whenever it type-checks against an older target.
#
# Both branches below are pragma'd: CI's test matrix runs both a <3.11 and a
# >=3.11 leg (see .github/workflows/ci.yml), so exactly one of these two
# lines is genuinely unreachable on any single interpreter -- there's no
# single run where both could ever be exercised together. What each branch
# actually does (tomllib.load() dispatch, or the "no tomllib" error) is
# covered directly -- see test_load_config_file_toml_dispatches_to_tomllib
# and test_load_config_file_toml_without_tomllib in test_denver_config.py.
tomllib: types.ModuleType | None
if sys.version_info >= (3, 11):
    import tomllib  # pragma: no cover -- see the module-level comment above
else:
    tomllib = None  # pragma: no cover -- see the module-level comment above

# Make the bundled ``providers`` package importable both when run as
# ``src/denver.py`` and when installed as the ``denver`` module.
sys.path.insert(0, str(Path(__file__).resolve().parent))

CONFIG_NAME = "denver.toml"
CONFIG_NAME_YAML = ("denver.yml", "denver.yaml")


def _config_names_text():
    """Every name a config file may go by, joined for error messages -- 'denver.yml/denver.yaml/denver.toml'."""
    return "/".join((*CONFIG_NAME_YAML, CONFIG_NAME))


# where denver's own code lives (this file's directory, containing denver.py
# and denver_providers/) -- always correct in both a checkout and an
# installed package.
DENVER_PKG_DIR = Path(__file__).resolve().parent

# conan_scripts ships alongside this module's denver_providers/ package, so
# it's still found when denver is installed via pip (no repo-root layout).
CONAN_SCRIPTS_DIR = DENVER_PKG_DIR / "denver_providers" / "conan_scripts"

# terminal-friendly rendition of denver_assets/logo.svg (an SVG can't be drawn
# in a plain terminal); kept alongside it under denver_assets/ so both stay in
# sync when the wordmark changes. Same DENVER_PKG_DIR-relative resolution as
# CONAN_SCRIPTS_DIR, so it's found in both a checkout and an installed package.
LOGO_PATH = DENVER_PKG_DIR / "denver_assets" / "logo.txt"


def checkout_root():
    """The source checkout denver itself is running out of, or None.

    True whenever DENVER_PKG_DIR is a ``<checkout>/src`` holding
    ``denver_providers/`` -- i.e. both when running the script directly
    (``src/denver.py``) and under an editable install (``uv pip install
    -e .``), which keeps DENVER_PKG_DIR pointing into the checkout's
    ``src/``. Installed any other way (e.g. a built wheel), DENVER_PKG_DIR is
    wherever the package manager put it (site-packages) and this is None.
    """
    if DENVER_PKG_DIR.name == "src" and (DENVER_PKG_DIR / "denver_providers").is_dir():
        return DENVER_PKG_DIR.parent
    return None


# Not imported from denver_providers.context: denver_providers is only imported lazily
# (inside run_stages()) so --help/--version/etc. stay light. Same logger name,
# so both feed the same "denver" logger regardless of which side configures
# it first.
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stderr)
logger = logging.getLogger("denver")


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def die(message) -> NoReturn:
    """Log ``message`` as an error and exit the process with status 1."""
    logger.error(message)
    sys.exit(1)


def info(message):
    """Log ``message`` at info level (suppressed under --quiet)."""
    logger.info(message)


def _with_hint(name, candidates):
    """``'name'``, plus `` (did you mean 'x'?)`` if a close match is in ``candidates``.

    Used to turn a typo (a misspelled key, id, or name) into a helpful
    hint instead of just a plain "unknown" error.
    """
    # difflib's default cutoff (0.6) misses short swaps like 'vu' vs 'uv'
    # (ratio 0.5) -- 0.5 still catches those without matching unrelated words.
    match = difflib.get_close_matches(name, candidates, n=1, cutoff=0.5)
    hint = f" (did you mean '{match[0]}'?)" if match else ""
    return f"'{name}'{hint}"


def _list_with_hints(names, candidates):
    """``names``, comma-joined, each with its own _with_hint() suggestion."""
    return ", ".join(_with_hint(name, candidates) for name in names)


def print_logo():
    """Print the DENVER wordmark banner (assets/logo.txt) to stderr.

    Shown right before denver's own --help/no-args screen, and right before
    an env's resolved command is actually invoked (the last thing printed,
    after every stage's build output -- see run_stages) --
    skipped for every machine-readable/scriptable mode (--show-config,
    --version, --setup, --login) and under --quiet, so nothing here ever
    contaminates output a caller might parse. A docker-wrapped env reinvokes
    itself inside the container (see reinvoke_command); the banner only
    shows on the host process relocating into it, not a second time once
    already inside (ctx.in_container). Silently does nothing if the asset is
    missing (e.g. a packaging edge case) -- a startup banner is cosmetic,
    never worth dying over.
    """
    if LOGO_PATH.is_file():
        print(LOGO_PATH.read_text(), file=sys.stderr)


# --------------------------------------------------------------------------- #
# denver's own version
#
# Needed twice: for `--version`, and to check a denver.toml's
# 'denver-version:' requirement (see validate_denver_version). Both must
# report the *running* denver in every supported way of running it -- an
# installed wheel, an editable install, or the plain script out of a
# checkout -- so neither source below is enough on its own.
# --------------------------------------------------------------------------- #
# the distribution name on PyPI (the import name is 'denver', the project is
# 'denver-tool' -- see pyproject.toml's [project] name).
DISTRIBUTION_NAME = "denver-tool"

# what `--version` prints when neither source below can answer (e.g. a
# source copy with no git history and no install of any kind).
UNKNOWN_VERSION = "unknown (not installed)"

# The release this working tree is *developing towards* -- i.e. what its next
# tag will be -- or None to switch the mechanism off entirely (nothing is
# re-based; a checkout reports exactly what `git describe` says). Bump it in
# the same commit that first relies on the new release, then tag that same
# number (see doc/contributing/development.md, "Releasing").
#
# It exists because a checkout's git tags necessarily lag behind its content:
# right after a feature lands, `git describe` still reports the *previous*
# release (1.0.4-17-gabc1234), so an example pinning the 'denver-version:'
# that feature ships in would refuse to run from source until the tag exists.
# Running from a checkout must always work, so scm_version() bases a
# checkout's version on this instead whenever the tags haven't caught up.
# Once the tag is pushed, git describe overtakes it and this stops having
# any effect until the next bump -- so a stale value can only ever understate
# an untagged tree, never overstate a released one.
#
# Two tests keep it honest, so a forgotten bump can't go unnoticed: every
# example must still run from this checkout, and DEV_VERSION must stay ahead
# of the newest tag once there are commits past it (a new cycle has started,
# so it has to name the release those commits are heading for) -- see
# tests/test_dev_version.py.
DEV_VERSION = None


def scm_version():
    """Version of the denver running, derived from its checkout's git tags, or None.

    The authoritative source whenever denver runs out of a checkout, because
    the packaging metadata isn't: running ``src/denver.py`` directly there is
    no metadata at all, and an editable install has metadata frozen at
    install time (setuptools-scm resolves the version once, so it still
    claims whatever the tags said back then -- or the ``fallback_version``,
    if installed from a tarball with no git history). ``git describe`` reads
    the tags *now*, which is what a version requirement must be judged
    against.

    A checkout whose tags still name an older release than ``DEV_VERSION``
    (the normal state between a feature landing and the release being
    tagged) is reported against DEV_VERSION instead -- see _dev_version.

    Returns None whenever that can't be answered -- not a checkout, no git
    binary, no tags (e.g. a shallow clone) -- leaving package_version()'s
    metadata fallback to answer instead.
    """
    root = checkout_root()
    if root is None:
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "describe", "--tags", "--match", "*.*.*"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    described = completed.stdout.strip()
    return _dev_version(described) if described else None


def _dev_version(described):
    """Re-base a `git describe` output onto DEV_VERSION when the tags lag behind it.

    With DEV_VERSION set to None the whole mechanism is off and ``described``
    is returned as-is, whatever it says.

    ``described`` is either a bare tag (sitting exactly on a release) or
    ``<tag>-<n>-g<sha>`` (n commits past it). Only the second form is ever
    re-based: sitting *exactly* on a tag means this tree really is that
    release, whatever DEV_VERSION happens to say, so it's returned untouched.

    The commit suffix is carried over verbatim, so the result reads as the
    development build it is (``1.1.0-17-gabc1234``, i.e. 17 commits into
    developing 1.1.0) rather than claiming to be the release itself -- and,
    ranking after 1.1.0 exactly as git describe's own output does, it
    satisfies a ``denver-version: ">=1.1.0"`` pin the tree already honours.
    """
    if DEV_VERSION is None:
        return described
    tag, _, suffix = described.partition("-")
    parsed, wanted = parse_version(tag), parse_version(DEV_VERSION)
    if not suffix or parsed is None or compare_versions(parsed, wanted) >= 0:
        return described
    return f"{DEV_VERSION}-{suffix}"


# setuptools-scm's version for a build off an untagged commit -- 1.5.1.dev27+gdecaf35.
_SCM_DEV_RE = re.compile(r"(\d+(?:\.\d+)*)\.dev(\d+)(?:\+(.+))?")


def _dev_metadata_version(version):
    """Re-base an untagged build's metadata onto DEV_VERSION -- _dev_version's other half.

    setuptools-scm names such a build after the release it heads for, as a
    *pre*-release of it (1.5.1.dev27+gdecaf35), which ranks below 1.5.1 and
    so fails a ">=1.5.1" pin the same commit satisfies from its checkout.
    Re-based, both sources report 1.5.1-27-gdecaf35 -- one commit, one
    verdict.

    A tagged rc is never re-based (it is deliberately incomplete), nor is
    metadata already naming a later release than DEV_VERSION -- so this only
    ever raises an understated version, never lowers an accurate one.
    """
    if DEV_VERSION is None:
        return version
    match = _SCM_DEV_RE.fullmatch(version)
    if not match or compare_versions(parse_version(match.group(1)), parse_version(DEV_VERSION)) > 0:
        return version
    return "-".join(part for part in (DEV_VERSION, match.group(2), match.group(3)) if part)


def package_version():
    """The running denver's version string, or None if it can't be determined.

    The checkout's git tags first (see scm_version), then the installed
    distribution's metadata via importlib.metadata -- which is what answers
    for a normally installed wheel, where there's no checkout to describe.
    setuptools-scm derives that metadata from git tags at build time (see
    pyproject.toml's [tool.setuptools_scm]), so it lags behind them just as
    `git describe` does -- and is re-based for the same reason.
    """
    version = scm_version()
    if version is not None:
        return version
    try:
        return _dev_metadata_version(importlib.metadata.version(DISTRIBUTION_NAME))
    except importlib.metadata.PackageNotFoundError:
        return None


def license_text():
    """The Apache-2.0 LICENSE text denver ships under, or None if it can't be found.

    Mirrors package_version()'s two sources for the same reason: a checkout
    (running the plain script, or an editable install) has the repo's own
    LICENSE file to read, but an installed wheel -- or this frozen into a
    PyInstaller binary via --copy-metadata -- has no checkout at all, only
    the LICENSE file setuptools copied into the dist-info's licenses/ dir
    (per pyproject.toml's 'license-files').
    """
    root = checkout_root()
    if root is not None:
        license_path = root / "LICENSE"
        if license_path.is_file():
            return license_path.read_text()
    try:
        return importlib.metadata.distribution(DISTRIBUTION_NAME).read_text("licenses/LICENSE")
    except importlib.metadata.PackageNotFoundError:
        return None


# --------------------------------------------------------------------------- #
# Config loading & merging
# --------------------------------------------------------------------------- #
class ConfigReadError(Exception):
    """A denver.yml/denver.toml exists but can't be turned into a config mapping.

    Raised, not die()'d, from load_config_file itself: that function is also
    called from clean's best-effort import-chain walk (_readable_imports),
    where a config that can't be read costs only the part of the chain below
    it, not the whole command (see _import_chain's docstring). main() turns
    it into a die() message for every other, non-best-effort caller.
    """


def load_config_file(path):
    """Load a denver.yml/denver.yaml or denver.toml file, returning a dict ({} for empty files).

    Dispatches on suffix: '.toml' is read as TOML -- only if tomllib is
    importable (stdlib only from Python 3.11), else a ConfigReadError
    explains that only denver.yml/denver.yaml is supported on this
    interpreter -- anything else ('.yml'/'.yaml', or no recognised suffix)
    is read as YAML, denver's default format (PyYAML is a required
    dependency, so it's always there).

    TOML needs no "is this a mapping?" check -- its grammar makes that
    structurally impossible to get wrong: a document is always a table
    (dict) at the top level, never a bare array or scalar (unlike
    JSON/YAML), so tomllib.load() already guarantees this. YAML makes no
    such guarantee, so a non-mapping top-level YAML document is rejected
    explicitly below.
    """
    path = Path(path)
    if path.suffix == ".toml":
        return _load_toml_config_file(path)
    return _load_yaml_config_file(path)


def _load_toml_config_file(path):
    """load_config_file's '.toml' branch."""
    if tomllib is None:
        raise ConfigReadError(
            f"{path}: reading a denver.toml config needs tomllib, which is stdlib only from Python 3.11 -- "
            f"this interpreter is older. Without it, only denver.yml/denver.yaml is supported."
        )
    with path.open("rb") as f:
        return tomllib.load(f)


def _load_yaml_config_file(path):
    """load_config_file's YAML branch -- denver's default format ('.yml'/'.yaml', or any other name)."""
    with path.open("rb") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigReadError(f"{path}: a denver.yml's top level must be a mapping, got {type(data).__name__}")
    return data


# Every exception load_config_file() can raise for a config that just plain
# won't parse -- used by _readable_imports (clean's best-effort import-chain
# walk) to tell "unreadable, skip it" apart from a real bug. tomllib.TOMLDecodeError
# only exists to catch when this interpreter has tomllib at all (Python 3.11+).
_CONFIG_READ_ERRORS = (
    (OSError, yaml.YAMLError, ConfigReadError)
    if tomllib is None
    else (OSError, yaml.YAMLError, ConfigReadError, tomllib.TOMLDecodeError)
)


_UNSET = object()  # marks "this key has no value from a lower layer yet"


def deep_merge(base, override, _path=""):
    """Merge ``override`` onto ``base``.

    Mappings are merged recursively (each layer updates the ones below it).
    ``base`` is not mutated.

    A *list* is appended to, not replaced: a lower layer's list plus this
    layer's own entries, in that order (e.g. a derived env's
    ``recipe-dirs:`` doesn't need to repeat its base's dirs to keep them --
    they're kept automatically, and the derived env only lists what it adds).
    Prefix one entry with ``!`` to drop everything from the lower layer
    instead, e.g.::

        recipe-dirs:
        - "!only-this-dir"

    which discards every entry the lower layer set (the ``!`` is stripped
    from the stored entry; every *other* entry in this layer's own list is
    still appended normally, ``!``-marked entry included).

    A bare ``<overwrite>`` entry does the same (drops every lower-layer
    entry), but is itself a pure marker -- it's removed rather than kept
    in the merged list::

        recipe-dirs:
        - "<overwrite>"
        - only-this-dir

    A *string* value works the other way around: if a lower layer already
    set the same key to a different string, that's almost always an
    accidental divergent override (e.g. two stacked layers disagreeing on
    ``uv.python``), so it's a hard error instead of a silent override.
    Prefix the overriding value with ``!`` to do it deliberately, e.g.::

        uv:
          python: "!3.12.3"

    which discards whatever a lower layer set (the ``!`` is stripped from
    the stored value). For both lists and strings, the ``!`` is only an
    escape when there's an actual lower-layer value to override -- a
    genuinely new key (no lower layer at all, ``base is _UNSET``) keeps a
    leading ``!`` as a literal character, so an ordinary value that happens
    to start with ``!`` (e.g. a shell history-expansion string) isn't
    silently mangled with nothing to deliberately override in the first
    place.
    """
    if isinstance(base, dict) and isinstance(override, dict):
        return _merge_dicts(base, override, _path)

    if isinstance(base, list) and isinstance(override, list):
        return _merge_lists(base, override)

    return _merge_scalar(base, override, _path)


def _merge_dicts(base, override, _path):
    """``deep_merge``'s mapping case: every key of ``override`` merged one layer deeper."""
    result = dict(base)
    for key, value in override.items():
        child_path = f"{_path}.{key}" if _path else str(key)
        result[key] = deep_merge(result.get(key, _UNSET), value, child_path)
    return result


def _has_reset_marker(override):
    """Whether a layer's list carries a ``!``/``<overwrite>`` marker, dropping every lower-layer entry."""
    return any(isinstance(entry, str) and (entry.startswith("!") or entry == "<overwrite>") for entry in override)


def _strip_reset_marker(entry):
    """One list entry with its leading ``!`` escape removed (anything else is returned unchanged)."""
    if isinstance(entry, str) and entry.startswith("!"):
        return entry[1:]
    return entry


def _merge_lists(base, override):
    """``deep_merge``'s list case: appended, unless ``override`` carries a ``!``/``<overwrite>`` reset marker."""
    if not _has_reset_marker(override):
        return base + override
    return [_strip_reset_marker(entry) for entry in override if entry != "<overwrite>"]


def _merge_scalar(base, override, path):
    """``deep_merge``'s scalar case: ``override`` wins, but a conflicting string needs an explicit ``!``."""
    if isinstance(override, str) and override.startswith("!") and base is not _UNSET:
        return override[1:]

    if isinstance(base, str) and isinstance(override, str) and base != override:
        die(
            f"conflicting values for '{path}' across stacked layers: {base!r} vs {override!r}. "
            f"Prefix the new value with '!' to override deliberately, e.g. \"!{override}\"."
        )
    return override


def _config_file_in_dir(dir_path):
    """The config file ``dir_path`` holds: ``denver.yml``, else ``denver.yaml``, else ``denver.toml``.

    Falls back to the (nonexistent) ``denver.yml`` path when none is
    present, so callers get a stable path to report as "not found".
    """
    for name in (*CONFIG_NAME_YAML, CONFIG_NAME):
        candidate = dir_path / name
        if candidate.is_file():
            return candidate
    return dir_path / CONFIG_NAME_YAML[0]


def _import_target(entry, base_dir):
    """Where an ``import:`` entry points -- a directory means its ``denver.yml``/``denver.toml`` -- or None if no file is there."""
    target = (base_dir / entry).resolve()
    if target.is_dir():
        target = _config_file_in_dir(target)
    return target if target.is_file() else None


def resolve_import(entry, base_dir):
    """Resolve an ``import:`` entry to the denver.yml/denver.yaml/denver.toml path it refers to.

    An entry may point at a directory (its ``denver.yml``/``denver.yaml``, or ``denver.toml``
    if there's neither, is used -- see _config_file_in_dir) or directly at a config
    file, relative to the importing config's directory.
    """
    target = _import_target(entry, base_dir)
    if target is None:
        die(f"import '{entry}' in {base_dir} does not resolve to a {_config_names_text()} file")
    return target


def load_config(config_path, _seen=None) -> dict:
    """Load a denver.toml and all of its imports into one merged config.

    Imports are merged first (base), then the importing file overlays on top,
    so a version-specific env can override values inherited from its base.
    """
    config_path = config_path.resolve()
    if _seen is None:
        _seen = set()
    if config_path in _seen:
        die(f"circular import detected at {config_path}")
    _seen.add(config_path)

    raw = load_config_file(config_path)
    merged = _merged_imports(raw, config_path.parent, _seen)

    # 'runnable' marks one specific denver.toml (e.g. a shared base meant only
    # to be imported, never started directly) -- it must never leak from an
    # imported base into a derived env's own resolved config, or every env
    # importing a 'runnable: false' base would incorrectly inherit it too.
    # is_runnable_env() already reads it straight from each file's own raw
    # TOML, never through this merge, for exactly this reason; dropping it
    # here keeps --show-config's output consistent with that.
    merged.pop("runnable", None)

    return cast(dict, deep_merge(merged, _without_import(raw)))


def _without_import(mapping):
    """A config layer's own keys, minus the 'import:' directive -- it isn't inheritable data."""
    return {k: v for k, v in mapping.items() if k != "import"}


def _merged_imports(raw, base_dir, _seen) -> dict:
    """Every 'import:' entry of ``raw``, loaded and merged in order -- the base its own keys overlay."""
    merged: dict = {}
    for entry in raw.get("import", []) or []:
        imported_path = resolve_import(entry, base_dir)
        merged = cast(dict, deep_merge(merged, load_config(imported_path, _seen)))
    return merged


def parse_config_override_spec(spec):
    """Split a ``-c``/``--config`` argument into (path_parts, op, raw_value).

    ``op`` is checked for before ``=`` so ``a.b+=c`` isn't mis-split as the
    key ``a.b+`` with value ``c``.
    """
    if "+=" in spec:
        path, _, raw_value = spec.partition("+=")
        op = "+="
    elif "=" in spec:
        path, _, raw_value = spec.partition("=")
        op = "="
    else:
        die(f"--config value {spec!r} must be KEY.PATH=VALUE or KEY.PATH+=VALUE")
    path = path.strip()
    if not path:
        die(f"--config value {spec!r} is missing a key path")
    return path.split("."), op, raw_value


def _combine_config_override(current, op, value, path):
    """Compute the new value at ``path`` for one ``-c`` override.

    ``=`` always replaces. ``+=`` appends/adds onto whatever is already
    there -- a list, string or number -- and behaves like ``=`` if the path
    had no value yet (nothing to append to).
    """
    if op == "=" or current is _UNSET or current is None:
        return value
    combined = _appended_config_value(current, value)
    if combined is None:
        die(f"--config: cannot += onto '{path}' ({current!r} += {value!r}): not a list, string or number")
    return combined


def _as_list(value):
    """``value`` as a list, wrapping a lone (non-list) value in one."""
    return value if isinstance(value, list) else [value]


def _both_are(current, value, types):
    """Whether both sides are of ``types`` -- and neither is a bool (a bool is an int, but += onto one never means this)."""
    if isinstance(current, bool) or isinstance(value, bool):
        return False
    return isinstance(current, types) and isinstance(value, types)


def _appended_config_value(current, value):
    """``+=``'s result for a path that already has a value, or None if the two cannot be combined at all."""
    if isinstance(current, list):
        return current + _as_list(value)
    if _both_are(current, value, (int, float)) or _both_are(current, value, str):
        return current + value
    return None


def _coerce_cli_value(raw_value):
    """Best-effort scalar coercion for a '-c KEY=VALUE' CLI override's raw string.

    Parsed as JSON when that succeeds (numbers, true/false/null, quoted strings, arrays, objects),
    else kept as a plain string -- this is what lets '-c uv.python=3.12.3' stay the string "3.12.3"
    (not valid JSON on its own) while '-c uv.no-index=true' still becomes the bool True.
    """
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return raw_value


def apply_config_override(config, spec):
    """Apply one ``-c``/``--config`` KEY.PATH=VALUE (or +=VALUE) spec.

    Any missing parent section along KEY.PATH is created as an empty dict.
    Does not mutate ``config`` or any of its nested dicts in place.
    """
    path_parts, op, raw_value = parse_config_override_spec(spec)
    value = _coerce_cli_value(raw_value)

    config = dict(config)
    node = config
    for part in path_parts[:-1]:
        child = node.get(part)
        node[part] = dict(child) if isinstance(child, dict) else {}
        node = node[part]

    key = path_parts[-1]
    node[key] = _combine_config_override(node.get(key, _UNSET), op, value, ".".join(path_parts))
    return config


def apply_config_overrides(config, specs):
    """Apply every ``-c``/``--config`` spec, in the order given."""
    for spec in specs:
        config = apply_config_override(config, spec)
    return config


# Top-level denver.toml keys that aren't a stage's own config section.
KNOWN_TOP_LEVEL_KEYS = {
    "version",
    "denver-version",
    "import",
    "stages",
    "command",
    "runnable",
    "env",
    "hooks",
    "extensions",
    "denver-custom-args",
    "download-auth",
}


def validate_top_level_keys(config):
    """Die on a top-level key that's neither a known denver.toml key nor a stage id declared in 'stages:'.

    Without this, a typo'd section (or one left behind after a stage was
    renamed/removed from 'stages:') is just silently ignored -- no stage
    ever reads it, and nothing says so.
    """
    allowed = KNOWN_TOP_LEVEL_KEYS | set(config.get("stages") or [])
    unknown = sorted(set(config) - allowed)
    if unknown:
        die(
            f"denver.toml: unknown top-level key(s) {_list_with_hints(unknown, allowed)} -- "
            f"not a recognised key and not a stage id in 'stages:'"
        )


# the denver.toml schema version this denver understands; bump together with
# an actual breaking change to the schema (doc/configuration/denver-toml.md
# and each provider's doc/providers/*.md page are the schema's documentation).
SUPPORTED_CONFIG_VERSION = "1.0"


def validate_config_version(config):
    """Die if 'version:' is set to a schema version this denver doesn't understand.

    'version:' exists precisely so a future, incompatible denver.toml schema
    change can be rejected with a clear message instead of silently
    misinterpreted -- so it must actually gate something, not just be
    accepted and ignored. Compared as a string so TOML's own numeric parsing
    (a bare ``1.0`` -> a float) doesn't matter.
    """
    version = config.get("version")
    if version is not None and str(version) != SUPPORTED_CONFIG_VERSION:
        die(
            f"denver.toml: unsupported 'version: {version}' -- "
            f"this denver understands version {SUPPORTED_CONFIG_VERSION}."
        )


# A version is compared as (release-numbers, rank): 1.0.3 -> ((1, 0, 3), 0).
# 'rank' orders the three kinds of suffix a release number can carry, so
# every version string denver can be handed sorts sensibly against a plain
# release: a pre-release (1.1.0rc1, or setuptools-scm's 1.1.0.dev3+g1234567
# for an untagged commit) sorts *before* 1.1.0, anything else (git
# describe's 1.0.3-2-gabc1234, i.e. two commits past the 1.0.3 tag) *after*
# the release it builds on. No PEP 440 library is used -- denver has no
# runtime dependency to spend on this (see load_config_file, stdlib tomllib),
# and this ordering is all a 'denver-version:' requirement needs.
_VERSION_RE = re.compile(r"v?(\d+(?:\.\d+)*)(.*)", re.DOTALL)
_PRERELEASE_RE = re.compile(r"[.\-_]?(a|b|c|rc|alpha|beta|dev|pre)\d*", re.IGNORECASE)

# each specifier in a 'denver-version:' value: an optional operator (bare
# means '>=' -- the overwhelmingly common "at least this version" case)
# followed by a version.
_SPEC_RE = re.compile(r"\s*(?:(>=|<=|==|!=|>|<)\s*)?(\S+)\s*")
_SPEC_OPERATORS = {
    ">=": lambda order: order >= 0,
    ">": lambda order: order > 0,
    "<=": lambda order: order <= 0,
    "<": lambda order: order < 0,
    "==": lambda order: order == 0,
    "!=": lambda order: order != 0,
}


def parse_version(text):
    """Parse a version string into a comparable (release, rank) key, or None if it isn't one."""
    match = _VERSION_RE.fullmatch(str(text).strip())
    if not match:
        return None
    release = tuple(int(part) for part in match.group(1).split("."))
    suffix = match.group(2)
    if not suffix:
        rank = 0
    elif _PRERELEASE_RE.match(suffix):
        rank = -1
    else:
        rank = 1
    return release, rank


def compare_versions(left, right):
    """Three-way compare two parse_version() keys (-1 / 0 / 1).

    The release tuples are zero-padded to the same length first, so 1.0 and
    1.0.0 compare equal rather than by length.
    """
    (left_release, left_rank), (right_release, right_rank) = left, right
    width = max(len(left_release), len(right_release))
    left_key = (left_release + (0,) * (width - len(left_release)), left_rank)
    right_key = (right_release + (0,) * (width - len(right_release)), right_rank)
    return (left_key > right_key) - (left_key < right_key)


def parse_version_spec(spec):
    """Parse a 'denver-version:' value into [(operator, parsed_version, text)], or die.

    Comma-separated specifiers are ANDed (``">=1.0.3, <2"``). A specifier
    with no operator means ">=".
    """
    return [_parse_version_requirement(part, spec) for part in str(spec).split(",")]


def _parse_version_requirement(part, spec):
    """One comma-separated specifier as ``(operator, parsed_version, text)``, or die naming the whole spec."""
    match = _SPEC_RE.fullmatch(part)
    wanted = parse_version(match.group(2)) if match else None
    if match is None or wanted is None:
        die(
            f"denver.toml: invalid 'denver-version: {spec}' -- {part.strip()!r} is not a version requirement "
            f"(expected e.g. \">=1.0.3\", \"1.0.3\" or \">=1.0.3, <2\")."
        )
    operator = match.group(1) or ">="
    return operator, wanted, f"{operator}{match.group(2)}"


def validate_denver_version(config):
    """Die if 'denver-version:' isn't satisfied by the denver actually running.

    A denver.toml using a key or behaviour only a newer denver knows would
    otherwise fail somewhere deep in a stage (or, worse, quietly do
    something else); this states the requirement up front, in the file that
    has it, and reports it as exactly that.

    Distinct from 'version:', which pins the *schema* denver.toml is written
    against (bumped only on a breaking schema change, see
    SUPPORTED_CONFIG_VERSION). This one pins the *tool*: a purely additive
    feature -- a new provider key, say -- never changes the schema version,
    but a file relying on it still needs a denver new enough to have it.

    Checked against the merged config (imports applied), like every other
    top-level key: a file inheriting a base's requirement is subject to it,
    and two stacked layers stating a different requirement is deep_merge's
    usual conflicting-strings error unless one is deliberately '!'-marked.

    If the running denver's own version can't be determined at all (see
    package_version), the requirement is reported as unverifiable rather
    than failed: that's an unusual install, not a reason to refuse to run an
    env that may well be fine.
    """
    spec = config.get("denver-version")
    if spec is None:
        return
    requirements = parse_version_spec(spec)

    running = package_version()
    parsed = parse_version(running) if running is not None else None
    if parsed is None:
        logger.warning(
            f"denver.toml requires 'denver-version: {spec}', but this denver's own version is "
            f"{running or 'unknown'} -- cannot verify the requirement, continuing."
        )
        return

    unmet = _unmet_requirements(requirements, parsed)
    if unmet:
        die(
            f"denver.toml requires 'denver-version: {spec}', but this denver is {running} "
            f"(unmet: {', '.join(unmet)}) -- upgrade it, e.g. `pip install --upgrade {DISTRIBUTION_NAME}`."
        )


def _unmet_requirements(requirements, parsed):
    """The requirement texts the running version ``parsed`` does not satisfy."""
    return [
        text
        for operator, wanted, text in requirements
        if not _SPEC_OPERATORS[operator](compare_versions(parsed, wanted))
    ]


def validate_stage_filters(config, until_stage, skip_stages):
    """Die if --until/--skip name a stage id not declared in 'stages:'."""
    declared = _declared_stage_ids(config)
    named = set(skip_stages) | ({until_stage} if until_stage else set())
    unknown = sorted(named - set(declared))
    if unknown:
        # declared, not sorted(declared): a bullet per id, in the pipeline's
        # own 'stages:' order -- alphabetical would misstate the order
        # stages actually run in, which is exactly what --until relies on.
        available = "\n".join(f"  - {s}" for s in declared) or "  (none)"
        die(
            f"--until/--skip: unknown stage id(s) {_list_with_hints(unknown, declared)} -- "
            f"not declared in 'stages:'. Available stages:\n{available}"
        )


def validate_hooks_keys(config):
    """Die on a 'hooks:' key that isn't a recognised hook name.

    Without this, a typo'd hook name (e.g. 'per-uv' for 'pre-uv') is just
    silently ignored -- run_hook() only ever looks up an exact known name
    (see hook_names_for_stages), so the hook never runs and nothing says why.
    Mirrors validate_top_level_keys, one level down.
    """
    hooks = config.get("hooks")
    if hooks is None:
        return
    if not isinstance(hooks, dict):
        die(f"denver.toml: 'hooks:' must be a mapping of hook name to script(s), got {hooks!r}")
    allowed = set(hook_names_for_stages(_declared_stage_ids(config)))
    unknown = sorted(set(hooks) - allowed)
    if unknown:
        die(
            f"denver.toml: unknown hooks key(s) {_list_with_hints(unknown, allowed)} -- "
            f"known: {', '.join(sorted(allowed))}."
        )


# --------------------------------------------------------------------------- #
# Provider config defaults
#
# Every filesystem/convention-based or static default a provider's config
# might fall back to is computed by that provider's own
# ``resolve_defaults(cls, ctx, cfg, config)`` classmethod (see
# providers.base.Provider) -- once, centrally, in resolve_provider_defaults()
# below, not inside setup(). This is what makes --show-config and the real
# run always see the exact same effective config: a provider's setup() never
# guesses a value itself, it just reads what's already there. denver.py
# itself holds no per-provider knowledge -- it just calls whichever
# provider class 'stages:' names.
# --------------------------------------------------------------------------- #
# keys every stage section may carry regardless of provider: 'provider:'
# picks the class (see providers.make_stage), 'scripts:' is the generic
# --scripts <name> mechanism (see _run_stage_scripts_in_context), 'disabled:'
# opts a stage out of the normal pipeline by default (see run_stages),
# 'depends-on:' skips a stage whenever a stage it names is itself skipped,
# for any reason (see _compute_static_skip_reasons/_blocked_by_dependency),
# 'skip-on-success:'/'skip-on-failure:' skip a stage's setup() at run time
# instead (see _stage_skip_reason), 'env:'/'env-prepend:'/'env-append:'
# adapt the environment once this stage's own setup() is done (see
# _apply_stage_env) -- none of these are part of any one provider's own
# KEYS, so every provider gets them for free, uv/conan/zephyr/docker/custom
# included. Order matters: this is also the fixed display order used by
# _ordered_stage_section for --show-config (provider first -- it picks the
# class -- then description, disabled, depends-on, skip-on-success,
# skip-on-failure, env, env-prepend, env-append, scripts), before every
# provider-specific key (alphabetically).
GENERIC_STAGE_KEYS = (
    "provider",
    "description",
    "disabled",
    "depends-on",
    "skip-on-success",
    "skip-on-failure",
    "env",
    "env-prepend",
    "env-append",
    "scripts",
)


def validate_stage_section_keys(stage, section):
    """Die on a key in ``section`` that isn't in the provider's own KEYS or a generic stage key.

    Without this, a typo'd key (or one left behind after being renamed/
    removed) is just silently ignored -- resolve_defaults() never reads it,
    and nothing says so. Mirrors validate_top_level_keys, one level down.
    """
    allowed = set(type(stage).KEYS) | set(GENERIC_STAGE_KEYS)
    unknown = sorted(set(section) - allowed)
    if unknown:
        die(
            f"stage '{stage.stage}': unknown key(s) {_list_with_hints(unknown, allowed)} for provider "
            f"'{stage.name}' -- known: {', '.join(sorted(type(stage).KEYS)) or '(none)'}."
        )


def resolve_stage_section(stage, raw_section, config, ctx):
    """Resolve one stage's *raw* section into its complete effective one.

    Always given the section as the denver.toml spelled it, never a section
    this function already resolved: a resolver reads an unset key's default
    back as though the author had written it (``cfg.get("exe") or
    ctx.which(...)``), so feeding it its own output turns every default it
    ever computed into an explicit value that can no longer be revised.
    That distinction is what lets this run a second time, per stage, once
    earlier stages have changed the world -- see _run_stage_setup.

    'scripts:'/'disabled:'/'skip-on-success:'/'skip-on-failure:' are filled
    in for every stage regardless of provider -- all generic,
    provider-agnostic keys any stage's section may declare (see
    _run_stage_scripts_in_context, run_stages's 'disabled:' handling, and
    _stage_skip_reason), not part of any one provider's own KEYS, so they
    belong here, not in Provider.resolve_defaults's default.
    """
    from denver_providers.base import fill_unset

    validate_stage_section_keys(stage, raw_section)
    section = type(stage).resolve_defaults(ctx, raw_section, config)
    disabled = raw_section.get("disabled", False)
    if not isinstance(disabled, bool):
        die(f"stage '{stage.stage}': 'disabled:' must be true or false, got {disabled!r}")
    section["disabled"] = disabled
    section["depends-on"] = _validated_depends_on(raw_section.get("depends-on"), stage.stage)
    section["skip-on-success"] = _resolved_skip_scripts(ctx, stage.stage, raw_section, "skip-on-success")
    section["skip-on-failure"] = _resolved_skip_scripts(ctx, stage.stage, raw_section, "skip-on-failure")
    description = raw_section.get("description")
    if description is not None and (
        not isinstance(description, list) or not all(isinstance(line, str) for line in description)
    ):
        die(f"stage '{stage.stage}': 'description:' must be a list of strings, got {description!r}")
    section["env"] = _validated_stage_env_map(raw_section.get("env"), stage.stage, "env")
    section["env-prepend"] = _validated_stage_env_map(raw_section.get("env-prepend"), stage.stage, "env-prepend")
    section["env-append"] = _validated_stage_env_map(raw_section.get("env-append"), stage.stage, "env-append")
    return fill_unset(section, ["scripts", "description"])


def _validated_stage_env_map(mapping, stage_id, key):
    """One generic 'env:'/'env-prepend:'/'env-append:' stage key, as a flat {name = "value"} mapping ({} if unset).

    Left uninterpolated here, deliberately: like every other provider's own
    section, this one is re-interpolated fresh right where it's used
    (_apply_stage_env, after this stage's own setup() -- see there for why),
    not baked in once while the config is merely being resolved.
    """
    if mapping is None:
        return {}
    if not isinstance(mapping, dict) or not all(isinstance(v, str) for v in mapping.values()):
        die(f"stage '{stage_id}': '{key}:' must be a mapping of environment variable to string value, got {mapping!r}")
    return dict(mapping)


def _validated_depends_on(value, stage_id):
    """One generic 'depends-on:' stage key, as a list of stage ids (``[]`` when unset).

    Only shape-checked here; cross-stage validity -- every id declared, and
    declared strictly *before* this stage in 'stages:' -- is checked once,
    globally, right after this resolves (see _validate_depends_on, called
    from resolve_provider_defaults).
    """
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        die(f"stage '{stage_id}': 'depends-on:' must be a list of stage ids, got {value!r}")
    return value


def _resolved_skip_scripts(ctx, stage_id, raw_section, key):
    """One generic 'skip-on-success:'/'skip-on-failure:' list, resolved to absolute paths (empty when unset).

    Resolved centrally like every other path (see Context.resolve_path) so
    --show-config shows the real script; whether it actually exists is
    checked at run time, right before it would run (_stage_skip_reason).
    """
    scripts = raw_section.get(key) or []
    if not isinstance(scripts, list) or not all(isinstance(s, str) for s in scripts):
        die(f"stage '{stage_id}': '{key}:' must be a list of script paths, got {scripts!r}")
    return [str(ctx.resolve_path(s)) for s in scripts]


def resolve_provider_defaults(config, ctx):
    """Bake every stage's provider defaults into ``config``, once, in 'stages:' order.

    A later stage's resolver (zephyr) can read an earlier one's
    already-resolved section (uv) this way. Mutates and returns ``config``.

    Each stage's raw section is kept on ``ctx`` first, so a stage can have
    its defaults resolved again from that same starting point right before
    it runs (see resolve_stage_section and _run_stage_setup). Deep-copied,
    because from here on ``config[stage_id]`` is the resolved section and
    nothing else may reach back into what it was resolved from.
    """
    from denver_providers import make_stage

    all_stage_ids = config.get("stages") or []
    seen = []
    for stage_id in all_stage_ids:
        stage = make_stage(stage_id, config)
        raw_section = config.get(stage_id) or {}
        ctx.raw_sections[stage_id] = copy.deepcopy(raw_section)
        config[stage_id] = resolve_stage_section(stage, raw_section, config, ctx)
        _validate_depends_on(stage_id, config[stage_id]["depends-on"], seen, all_stage_ids)
        seen.append(stage_id)
    return config


def _validate_depends_on(stage_id, depends_on, seen, all_stage_ids):
    """Die if 'depends-on:' names an id that isn't declared, or isn't declared strictly *before* ``stage_id``.

    Checked eagerly here, in 'stages:' order (``seen`` is every id already
    walked), the same as validate_stage_filters checks --until/--skip ids --
    a typo or a forward/self-reference fails at startup rather than
    surfacing as a wrong skip cascade once _compute_static_skip_reasons
    walks the list assuming every dependency's reason is already decided.
    """
    for dep in depends_on:
        if dep in seen:
            continue
        if dep not in all_stage_ids:
            die(
                f"stage '{stage_id}': 'depends-on:' names unknown stage id {_with_hint(dep, all_stage_ids)} -- "
                f"not declared in 'stages:'."
            )
        die(
            f"stage '{stage_id}': 'depends-on:' names '{dep}', declared at or after '{stage_id}' in 'stages:' -- "
            "a stage may only depend on one declared earlier."
        )


# --------------------------------------------------------------------------- #
# Environment resolution
# --------------------------------------------------------------------------- #
def resolve_env_dir(env_arg):
    """Resolve the <env> argument to (env_dir, config_path).

    Accepts a path to an env directory (its denver.yml/denver.yaml, or
    denver.toml if there's neither, is used -- see _config_file_in_dir) or a
    path directly to a config file (any name, e.g. denver.debug.yml -- lets
    a folder hold several denver.xxx.yml/denver.xxx.toml variants side by
    side). Mirrors resolve_import()'s own directory-or-file convention for
    'import:' entries, so both the top-level <env> and imports resolve the
    same way.
    """
    candidate = Path(env_arg).expanduser()

    if not candidate.exists():
        die(f"environment '{env_arg}' not found. Give a path to an env directory or a {_config_names_text()} file.")
    if candidate.is_file():
        return candidate.parent.resolve(), candidate.resolve()
    env_dir = candidate.resolve()
    return env_dir, _config_file_in_dir(env_dir)


def is_runnable_env(config_path):
    """An env is runnable unless its denver.toml sets ``runnable: false``.

    Used to reject starting a shared/base env directly (meant to be
    inherited via ``import:`` only) -- see its use in main() below.
    """
    return load_config_file(config_path).get("runnable", True) is not False


# --------------------------------------------------------------------------- #
# Provider orchestration (denver.toml-driven)
# --------------------------------------------------------------------------- #
def collect_import_dirs(config_path, _seen=None):
    """Directories of every env in the whole-file ``import:`` chain, nearest first.

    Breadth-first: the env's own direct imports, then their imports, etc.
    This is the search order ``Context.resolve_path`` uses to fall back to a
    base env's file when the leaf doesn't have one -- e.g. a conventional
    default like ``uv/skip-on-success.sh`` or ``conan/base_classes``. Walking the
    whole chain (not just the direct imports) is what makes those
    conventions work through multiple levels of ``import:`` stacking: each
    layer, closest to the leaf first, is checked in turn, so a more-derived
    layer's own file always wins over one further up the chain, and a layer
    that doesn't have one simply falls through to the next.
    """
    config_path, _seen = _register_seen(config_path, _seen)
    if not config_path.is_file():
        # an env dir need not have its own denver.toml if -f/-c supply the
        # whole config (see _load_cli_config) -- nothing to import from here.
        return []
    imported = _imported_paths(load_config_file(config_path), config_path.parent)

    dirs = [p.parent for p in imported]
    for imported_path in imported:
        dirs += collect_import_dirs(imported_path, _seen)
    return dirs


def _register_seen(config_path, _seen):
    """Resolve ``config_path`` and record it in ``_seen``, dying on a circular import.

    Returns ``(config_path, _seen)`` -- both walkers over the whole-file
    ``import:`` chain start with exactly this, and ``_seen`` is created on
    the first (top-level) call.
    """
    config_path = config_path.resolve()
    if _seen is None:
        _seen = set()
    if config_path in _seen:
        die(f"circular import detected at {config_path}")
    _seen.add(config_path)
    return config_path, _seen


def _imported_paths(raw, base_dir):
    """Every whole-file 'import:' entry of ``raw``, resolved to the denver.toml it names."""
    return [resolve_import(entry, base_dir) for entry in (raw.get("import", []) or [])]


def collect_hook_entries(config_path, name, _seen=None):
    """Collect hook ``name`` script entries across the whole-file ``import:`` chain, base-first.

    Returned as ``(base_dir, raw_value)`` pairs. Each layer in the chain (an
    env and everything it whole-file-imports) contributes its own explicit
    ``hooks: <name>:`` entry (a list of scripts, or a bare string for a
    single one) if it declares one -- so a stacked/inherited env's own hook
    is never silently lost just because a derived env also declares one.

    Nothing is discovered from the directory layout: a ``hooks/<name>.sh``
    (or ``hooks/<name>.user.sh``) sitting next to a ``denver.toml`` is only
    ever run if that ``denver.toml`` actually lists it.
    """
    config_path, _seen = _register_seen(config_path, _seen)
    if not config_path.is_file():
        # see collect_import_dirs's own guard -- an env dir need not have a
        # denver.toml of its own when -f/-c supply the whole config.
        return []
    raw = load_config_file(config_path)
    base_dir = config_path.parent

    entries = []
    for imported_path in _imported_paths(raw, base_dir):
        entries += collect_hook_entries(imported_path, name, _seen)
    return entries + _own_hook_entries(raw, base_dir, name)


def _own_hook_entries(raw, base_dir, name):
    """One layer's own ``hooks: <name>:`` scripts as (base_dir, script) pairs -- a bare string counts as one."""
    own = (raw.get("hooks") or {}).get(name)
    if not own:
        return []
    own_scripts = [own] if isinstance(own, str) else own
    return [(base_dir, script) for script in own_scripts]


def run_hook(ctx, config_path, name):
    """Source the hook script(s) for ``name``, base-first across ``import:``.

    Hooks are *sourced* (not just executed) so they can shape the environment
    the following stage/command runs in. Recognised names: ``env`` runs once,
    before any stage and before the declarative ``env:`` map, so its exports
    apply to the whole devshell; ``pre-<stage>`` / ``post-<stage>`` run
    around each stage; ``pre-cmd`` runs just before the final command. See
    ``collect_hook_entries`` for how each name's script(s) are found.
    """
    for base_dir, script in collect_hook_entries(config_path, name):
        path = ctx.resolve_path(script, base=base_dir)
        if not path.is_file():
            die(f"hook '{name}' script not found: {path}")
        info(f"hook {name}: {path}")
        ctx.source(path)


PERFORMANCE_FILE_NAME = "performance.jsonl"

# what argparse stores for a bare '--scripts' (no name): list the names this
# env defines rather than running one. A sentinel object, not a string, so it
# can never collide with a name an env actually uses.
LIST_SCRIPTS = object()


def _append_trace_event(path, event):
    """Append one JSON-encoded ``event`` to ``path`` as its own line, in a single atomic write.

    A plain os.write() to an O_APPEND-opened fd (not a buffered file object,
    where Python may split one .write() call into several syscalls) is what
    makes this atomic: POSIX guarantees a single write() under PIPE_BUF
    (4096 bytes on Linux; one trace event is nowhere near that) either lands
    whole or not at all, even with several processes appending to the same
    file at once -- e.g. a docker-wrapped run's host and container processes
    both recording stage timings concurrently.
    """
    line = (json.dumps(event) + "\n").encode()
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def record_stage_performance(ctx, provider, start_time, duration_seconds):
    """Append this stage's timing to <env_workdir>/performance.jsonl, one JSON Lines record per event.

    Each line is one Chrome Trace Event Format event -- concatenate them into
    a `{"traceEvents": [...]}` document to load into chrome://tracing or
    https://ui.perfetto.dev, e.g.:
    ``jq -s '{traceEvents: ., displayTimeUnit: "ms"}' performance.jsonl``.
    One line per event (rather than one read-modify-write of a single JSON
    document) is what makes concurrent appends safe -- see
    _append_trace_event.

    Skipped entirely under --dry-run: no stage actually did its work, so the
    durations measured here are of printing commands, not of running them --
    recording those would quietly poison the very timings this file exists
    to answer questions about.
    """
    if ctx.dry_run:
        return
    path = ctx.env_workdir / PERFORMANCE_FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)

    pid = os.getpid()
    if not getattr(ctx, "_perf_process_announced", False):
        _append_trace_event(
            path,
            {
                "ph": "M",
                "name": "process_name",
                "pid": pid,
                "tid": 1,
                "args": {"name": f"denver:{ctx.env_name}"},
            },
        )
        ctx._perf_process_announced = True
    _append_trace_event(
        path,
        {
            "name": provider.stage,
            "cat": "stage",
            "ph": "X",  # complete event: one entry covers the whole duration
            "ts": round(start_time * 1_000_000),  # microseconds, as the format requires
            "dur": round(duration_seconds * 1_000_000),
            "pid": pid,
            "tid": 1,
            "args": {"provider": provider.name},
        },
    )


def parse_section_import_ref(ref):
    """Split a section-level import ``ref`` into (path, section).

    A bare path (``../zephyr-docker``) stacks the current section's
    same-named section from the referenced env. A ``path:section`` suffix
    (``../zephyr-devshell/denver.toml:conan``) makes the source section
    explicit -- pointing straight at a specific env's ``denver.toml`` and
    picking a (possibly differently-named) section out of it -- instead of
    always inferring both from the current key.
    """
    path, sep, section = ref.rpartition(":")
    return (path, section) if sep else (ref, None)


def expand_section_imports(config, env_dir):
    """Resolve section-level ``import`` ("stacking").

    A provider's config section may pull its content from another env, e.g.::

        docker:
          import:
          - ../zephyr-docker      # stack that env's `docker:` section here

    An entry may also point directly at a config file and/or name an explicit
    source section with ``path:section``::

        conan:
          import:
          - ../zephyr-devshell/denver.toml:conan

    The referenced sections are merged in (base-first), then the local keys
    override. Returns (expanded_config, extra_search_dirs) where the extra
    dirs let relative paths in the imported section (compose file, scripts,
    ...) resolve against their source env.
    """
    result = dict(config)
    extra_dirs = []
    for key, value in config.items():
        if not (isinstance(value, dict) and value.get("import")):
            continue
        merged, dirs = _stacked_section(value, key, env_dir)
        extra_dirs += dirs
        result[key] = deep_merge(merged, _without_import(value))
    return result, extra_dirs


def _stacked_section(value, key, env_dir):
    """Merge every section-level 'import:' entry of one section, base-first.

    Returns ``(merged, extra_dirs)``, the extra dirs being each source env's
    own directory (so its relative paths still resolve, see the caller).
    """
    merged = {}
    extra_dirs = []
    for ref in value["import"]:
        path, section = parse_section_import_ref(ref)
        src_path = resolve_import(path, env_dir)
        src_config = load_config(src_path)
        merged = deep_merge(merged, src_config.get(section or key) or {})
        extra_dirs.append(src_path.parent)
    return merged, extra_dirs


def default_command(config, in_container=False):
    """Determine the interactive command when the user gave none.

    ``in_container`` -- true whenever this resolved command is about to run
    inside a docker container, whether that's because this denver process is
    already there (ctx.in_container), or because it's being resolved on the
    host only to be relocated there next (a 'pure wrapper', see
    _wrapper_target_cmd) -- wires up 'denver complete' into it first, via
    _completion_wrapped_shell: unlike the host, the container's image is
    never something the user already has their own shell completion set up
    in. False (the default) leaves ``cmd`` exactly as resolved, e.g. for a
    plain host run.
    """
    if not sys.stdin.isatty():
        die("cannot determine command to run (non-interactive and no command given)")
    cmd = _resolve_default_cmd(config)
    return _completion_wrapped_shell(cmd) if in_container else cmd


def _resolve_default_cmd(config):
    """'command:'/'docker.compose.default-cmd:'/$SHELL/"bash", in that order, normalised to a list. See default_command.

    'command:' is the generic top-level default; a wrapper provider
    (currently only 'docker') may contribute its own fallback via
    <provider>.compose.default-cmd, e.g. the command to land in once relocated
    into a container. With neither set, fall back to the user's own shell
    (not a specific one denver would otherwise be guessing).
    """
    docker_compose = (config.get("docker") or {}).get("compose") or {}
    cmd = config.get("command") or docker_compose.get("default-cmd") or os.environ.get("SHELL") or "bash"
    return [cmd] if isinstance(cmd, str) else [str(c) for c in cmd]


def _completion_wrapped_shell(cmd):
    """Wire 'denver complete' into ``cmd`` before it lands, if ``cmd`` is recognisably an interactive bash/zsh/fish.

    Only a shell denver has a completion script for (see _COMPLETION_SHELLS)
    is touched -- anything else (a custom 'command:', a provider's own tool)
    comes back unchanged, since denver has no idea whether it's even a shell,
    let alone how to wire completion into it. ``cmd``'s own extra args (e.g.
    'docker.compose.default-cmd: [zsh, -l]') are preserved on the final exec,
    right alongside completion's own '-i' (needed since 'sh -c' would
    otherwise hand back a non-interactive shell).

    The wiring itself: run 'denver complete <shell>' (routed through
    _denver_launcher -- the same way reinvoke_command finds itself inside
    the container, since a bare 'denver' isn't guaranteed to be on PATH
    there) via that shell's own idiom -- eval for bash/zsh, `| source` for
    fish -- then exec the real interactive shell. Its own startup files
    (~/.bashrc etc.), if the image has any, still run too: this only adds to
    that, on top, never replaces it.
    """
    if not cmd:
        return cmd
    binary = cmd[0]
    shell = Path(binary).name
    if shell not in _COMPLETION_SHELLS:
        return cmd

    setup = _completion_setup_snippet(shell)
    extra_args = "".join(f" {shlex.quote(a)}" for a in cmd[1:])
    return [binary, "-c", f"{setup}; exec {shlex.quote(binary)} -i{extra_args}"]


def _completion_setup_snippet(shell):
    """The shell-specific line wiring 'denver complete' up before exec -- see _completion_wrapped_shell."""
    launcher = " ".join(shlex.quote(part) for part in _denver_launcher())
    if shell == "fish":
        return f"{launcher} complete fish 2>/dev/null | source"
    return f'eval "$({launcher} complete {shell} 2>/dev/null)"'


def resolve_command(config, forwarded, in_container=False):
    """The command to run: ``forwarded`` verbatim, else the env's default command.

    ``forwarded`` is already the clean command (main() split it off argv's
    first literal '--' before any denver flag parsing even started, so
    there's no '--' marker left in it here to strip). ``in_container`` is
    passed straight through to default_command -- irrelevant here, since an
    explicit ``forwarded`` command is never wrapped, only the env's own
    fallback/configured interactive shell is (see default_command).
    """
    return list(forwarded) or default_command(config, in_container)


def reinvoke_command(config_path, forwarded, wrapper_stage_ids, *, options=None):
    """Re-invoke denver (skipping ``wrapper_stage_ids``) so setup providers run in the wrapper.

    Used inside a wrapper (e.g. docker): the same denver runs again inside the
    container, where the wrapper is inactive and uv/conan/zephyr build/enter
    the environment. denver's sources are available at the same path (the
    workspace is bind-mounted) -- ``Path(__file__).resolve()`` is this exact
    file's own absolute path, so this works unchanged whether denver runs
    from a checkout or an editable install (both keep ``__file__`` pointing
    into the checkout); a non-editable install only re-invokes correctly if
    the container has denver installed at that same absolute path too.
    ``python3`` (a bare command, not this host's interpreter path) is looked
    up wherever the command actually runs -- the container's PATH, not the
    host's -- matching docker.wrap()'s own bare-name commands.
    Running as a frozen single-file executable (see
    scripts/create-python-exe.sh) there is no denver.py to hand to an
    interpreter at all: ``__file__`` then names a file inside PyInstaller's
    per-run extraction directory that is never actually written (the modules
    live in an archive inside the executable), and the container's ``python3``
    would need denver's dependencies -- exactly what that build exists to
    avoid. So the executable re-invokes *itself*, by its own absolute path,
    which the wrapper makes resolvable inside the relocated environment by
    bind-mounting it there (see docker.py's _frozen_denver_mount).
    ``wrapper_stage_ids`` (the active wrapper(s) relocating this command) are
    each passed as their own ``--skip``, so the re-invoked denver's own stage
    filtering drops them from 'stages:' and never tries to relocate again.

    ``options`` is this invocation's own RunOptions, and essentially all of
    it has to be re-passed: every one of these was consumed out of argv by
    the outer main(), and none is read back out of a real environment
    variable, so there is no other way for the inner process to inherit any
    of them.

    * --until/--skip, so a stage the user asked to skip stays skipped inside
      the wrapper too, instead of the inner denver re-computing 'stages:'
      from scratch with no memory of them and running it anyway;
    * --quiet (repeated ``options.quiet`` times, so e.g. -qq's level 2
      survives, not just a single -q), --verbose, --fast/--force/--ci/--no-wait;
    * -e/--env (``options.env_vars``): re-passed as its own ``--env
      NAME=VALUE`` flags, one per entry, for the same reason -- the inner
      denver's own os.environ starts empty of them (a wrapper reinvocation
      is a fresh process, docker included -- see docker.py's
      _relocation_env for how the raw container environment itself gets
      them too);
    * the env's own 'denver-custom-args:' flags (``options.cli_args.argv``): the inner
      denver re-reads the same denver.toml, so it declares the same flags --
      but nobody would have given them to it, and every one would quietly
      fall back to its 'default:';
    * ``start_time`` (the hidden --start-time flag), the outer denver's own
      ``time.time()`` at the very start of this startup -- carried across so
      the "env started in Ns" line the inner denver prints right before
      launching the command reflects the *whole* startup (including the
      outer denver's own wrapper-stage work), not just the inner process's
      own, much shorter, wall-clock.

    ``forwarded`` (already stripped of any '--' marker by the outer main(),
    see resolve_command) is re-introduced with a fresh '--' here, so the
    re-invoked denver's own argv splitting (main() splits on the first
    literal '--') separates it from denver's own flags again instead of
    trying to parse it as one of them.
    """
    options = options or RunOptions()
    filter_flags = []
    if options.until_stage:
        filter_flags += ["--until", options.until_stage]
    for stage_id in (*options.skip_stages, *wrapper_stage_ids):
        filter_flags += ["--skip", stage_id]
    command = ["--", *forwarded] if forwarded else []
    return [
        *_denver_launcher(),
        "run",
        str(config_path),
        *filter_flags,
        *_reinvoke_flags(options),
        *options.cli_args.argv,
        *command,
    ]


def _denver_launcher():
    """The interpreter+script pair to re-run denver with, or the frozen executable on its own.

    See reinvoke_command's docstring for why a frozen build re-invokes itself.
    """
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve())]
    return ["python3", str(Path(__file__).resolve())]


def _reinvoke_flags(options):
    """The denver-own flags re-passed to the inner denver: -q once per level, then each flag that is set."""
    flags = ["-q"] * options.quiet
    toggles = (
        ("--verbose", options.verbose),
        ("--fast", options.fast),
        ("--force", options.force),
        ("--ci", options.ci),
        ("--no-wait", options.no_wait),
    )
    for flag, enabled in toggles:
        if enabled:
            flags.append(flag)
    for name, value in options.env_vars.items():
        flags += ["--env", f"{name}={value}"]
    if options.export_env:
        flags += ["--export-env", options.export_env]
    return [*flags, "--start-time", repr(options.start_time)]


def resolve_full_config(
    env_dir,
    config,
    config_path,
    *,
    quiet=0,
    verbose=False,
    fast=False,
    force=False,
    ci=False,
    dry_run=False,
    cli_args=None,
    env_vars=None,
):
    """Fully resolve an env's config for actual use.

    Section-level ('docker: import:') stacking, then every provider's
    defaults baked in centrally (see resolve_provider_defaults). This is the
    single place both --show-config and the real run get their config from,
    so they can never drift apart -- a provider's setup() never guesses a
    default itself, it just reads what's already there. Returns (config, ctx).

    ``cli_args`` (the env's own 'denver-custom-args:', as this invocation resolved them --
    see CliArgs) is exported into ctx.env *before* any default is resolved,
    for exactly the same reason: a stage section reading
    ``${DENVER_ARG_TARGET}`` must see the same value under --show-config as
    in the real run. ``env_vars`` (-e/--env, see RunOptions) is applied right
    after, for the same reason and overriding it if a name collides -- an
    explicit ``-e FOO=bar`` is a more direct statement of intent than
    whatever 'denver-custom-args:' happened to export under that name.
    """
    from denver_providers import Context, load_extension_providers
    from denver_providers.context import CLI_ENV_VAR_NAMES

    config, extra_dirs = expand_section_imports(config, env_dir)
    import_dirs = collect_import_dirs(config_path) + extra_dirs
    ctx = Context(
        env_dir,
        config,
        config_path=config_path,
        import_dirs=import_dirs,
        quiet=quiet,
        verbose=verbose,
        fast=fast,
        force=force,
        ci=ci,
        dry_run=dry_run,
    )
    ctx.env.update(_cli_args(cli_args).env)
    if env_vars:
        ctx.env.update(env_vars)
        # names only -- see CLI_ENV_VAR_NAMES; each value already lives under
        # its own key above, this is what lets a wrapper crossing a real
        # process boundary (docker) tell which of ctx.env's entries to
        # forward explicitly.
        ctx.set(CLI_ENV_VAR_NAMES, ",".join(env_vars))
    # registers any 'extensions.providers.dirs:' Provider subclasses into
    # providers.PROVIDERS before resolve_provider_defaults below needs to
    # look any of their names up via make_stage.
    load_extension_providers(ctx, config.get("extensions"))
    resolve_provider_defaults(config, ctx)
    return config, ctx


def filtered_stage_ids(config, env_dir, until_stage, skip_stages):
    """The env's declared 'stages:' narrowed by --until/--skip, in order.

    --until truncates the list after the named stage (which still runs);
    --skip then drops individual ids from what's left.

    Shared by run_stages and run_named_scripts so both filter identically;
    validity of the given stage ids was already checked in main() against the
    same (unfiltered) 'stages:' list.
    """
    stage_ids = _declared_stage_ids(config)
    if not stage_ids:
        die(f"env '{env_dir.name}' declares no 'stages:'")
    stage_ids = _apply_stage_filters(stage_ids, until_stage, skip_stages)
    if not stage_ids:
        die("--until/--skip filtered out every stage -- nothing left to run")
    return stage_ids


def _declared_stage_ids(config):
    """Every stage id the env declares in 'stages:', in order (empty when it declares none)."""
    return config.get("stages") or []


def _apply_stage_filters(stage_ids, until_stage, skip_stages):
    """``stage_ids`` truncated after --until's stage (which still runs), then with --skip's ids dropped."""
    if until_stage:
        stage_ids = stage_ids[: stage_ids.index(until_stage) + 1]
    if not skip_stages:
        return stage_ids
    drop = set(skip_stages)
    return [s for s in stage_ids if s not in drop]


def _collect_stage_scripts(ctx, stage_ids, name):
    """Resolve every (stage_id, script_path) from each stage's ``scripts: <name>:`` list.

    Shared by run_named_scripts (via _run_stage_scripts_in_context): 'scripts:'
    is a generic, per-stage config key (any provider's section may declare
    it), not provider-specific -- entries are paths, resolved the same way
    (relative to the env dir, falling back to imported base env dirs).
    """
    resolved = []
    for stage_id in stage_ids:
        for script in _stage_script_entries(ctx, stage_id, name):
            resolved.append((stage_id, _resolved_script_path(ctx, stage_id, name, script)))
    return resolved


def _stage_script_entries(ctx, stage_id, name):
    """One stage's ``scripts: <name>:`` list, validated -- empty when that stage declares none."""
    entry = (ctx.section(stage_id).get("scripts") or {}).get(name)
    if entry is None:
        return []
    if not isinstance(entry, list):
        die(f"stage '{stage_id}': 'scripts.{name}:' must be a list of strings, got {type(entry).__name__}")
    return entry


def _resolved_script_path(ctx, stage_id, name, script):
    """One ``scripts: <name>:`` entry resolved to a file that exists."""
    path = ctx.resolve_path(script)
    if not path.is_file():
        die(f"stage '{stage_id}': scripts.{name} script not found: {path}")
    return path


def run_named_scripts(
    env_dir,
    config,
    config_path,
    names,
    *,
    until_stage=None,
    skip_stages=(),
    quiet=False,
    verbose=False,
    dry_run=False,
    no_wait=False,
    cli_args=None,
    env_vars=None,
):
    """Run every (filtered) stage's ``scripts: <name>:`` entries for each of ``names``, in the order given.

    This is ``denver run <env> --scripts <name>`` (repeatable -- each
    occurrence's name lands in ``names``, run in that order); a name is
    open-ended -- any string an env's ``scripts:`` sections happen to use
    (e.g. ``setup``, ``login``, or a project-specific one like ``migrate``),
    not a fixed, hard-coded set. Unlike the normal pipeline, this never
    builds/enters the environment or runs any provider's setup()/wrap() for
    its own sake -- but a *wrapper* stage's (e.g. docker) own entries run on
    the host, since that's where the wrapper itself operates, while a *setup*
    stage's entries need whatever that stage's own setup() would install
    (e.g. conan, only present once inside a docker-wrapped env's container).
    So when an active wrapper exists and any setup stage declares any of
    ``names``, the wrapper is prepared (its setup() runs, same as
    run_stages() would) and denver is re-invoked `--skip <that wrapper
    stage>`, with one `--scripts <name>` per name that actually had
    something to relocate, for the setup stages' own entries -- mirroring
    run_stages()'s own host/wrapper relocation exactly.

    Under ``dry_run`` each entry is printed rather than executed, with the
    same wrapper caveat as run_stages(): the reinvocation that would carry
    the setup stages' entries into the wrapper is itself not run.
    """
    from denver_providers.context import dry_run_legend

    if dry_run:
        dry_run_legend()

    stage_ids = filtered_stage_ids(config, env_dir, until_stage, skip_stages)
    config, ctx = _prepare_context(
        env_dir,
        config,
        config_path,
        no_wait=no_wait,
        quiet=quiet,
        verbose=verbose,
        dry_run=dry_run,
        cli_args=cli_args,
        env_vars=env_vars,
    )

    stages = _make_stages(config, stage_ids)
    wrappers, setups, _, _ = _partition_stages(stages, set(_stage_ids_of(stages)))
    active_wrappers = [] if _wrappers_inactive(ctx) else wrappers

    if not active_wrappers:
        _run_named_scripts_directly(ctx, setups, names)
        return

    _relocate_named_scripts(
        ctx,
        config,
        config_path,
        stages,
        setups,
        active_wrappers,
        names,
        quiet=quiet,
        until_stage=until_stage,
        skip_stages=skip_stages,
        cli_args=cli_args,
        env_vars=env_vars,
    )


def _run_named_scripts_directly(ctx, setups, names):
    """Run every one of ``names`` for the given setup stages, in order -- the no-active-wrapper case."""
    for name in names:
        if not _run_stage_scripts(ctx, _stage_ids_of(setups), name):
            _warn_no_scripts(ctx, name)


def _run_wrapper_scripts_and_find_relocatable(ctx, active_wrappers, setups, names):
    """Run each of ``names``' wrapper-stage entries on the host; return the subset that also needs relocating.

    The wrapper's own entries (e.g. `docker login` to a private registry)
    run here, on the host, before the wrapper is ever prepared -- one name
    at a time, in order. A name with entries in neither the wrapper nor a
    setup stage never makes it into the returned list (there's nothing left
    to relocate it for), so it's flagged here, once, via _warn_no_scripts --
    otherwise it would run nothing and say nothing.
    """
    setup_names = []
    for name in names:
        ran_on_host = _run_stage_scripts(ctx, _stage_ids_of(active_wrappers), name)
        if _collect_stage_scripts(ctx, _stage_ids_of(setups), name):
            setup_names.append(name)
        elif not ran_on_host:
            _warn_no_scripts(ctx, name)
    return setup_names


def _relocate_named_scripts(
    ctx,
    config,
    config_path,
    stages,
    setups,
    active_wrappers,
    names,
    *,
    quiet,
    until_stage,
    skip_stages,
    cli_args,
    env_vars,
):
    """Run each of ``names``' wrapper-stage entries on the host, then relocate whichever names still need the wrapper.

    See run_named_scripts, whose own docstring covers the host/wrapper split
    this implements.
    """
    setup_names = _run_wrapper_scripts_and_find_relocatable(ctx, active_wrappers, setups, names)
    if not setup_names:
        return  # nothing to relocate into the wrapper for, for any of ``names``

    # same limit as run_stages(): the reinvocation carrying these entries
    # into the wrapper is itself not run under --dry-run, so they can't be
    # previewed -- see _note_not_previewed for why not.
    names_label = ", ".join(f"'{n}'" for n in setup_names)
    _note_not_previewed(ctx, f"{names_label} scripts of stages", setups, active_wrappers)

    stage_index = _stage_positions(stages)
    _setup_wrappers(ctx, config, config_path, active_wrappers, stage_index, len(stages), quiet=quiet)

    cmd = _relocated_run_cmd(
        config_path,
        setup_names,
        quiet=quiet,
        verbose=ctx.verbose,
        until_stage=until_stage,
        skip_stages=(*skip_stages, *_stage_ids_of(active_wrappers)),
        cli_argv=_cli_args(cli_args).argv,
        env_vars=env_vars,
    )
    ctx.exec(_wrap_cmd(ctx, cmd, active_wrappers, stage_index, len(stages)))


def _run_stage_scripts(ctx, stage_ids, name):
    """Run every ``scripts: <name>:`` entry the given stages declare, in order.

    Returns whether any entry was found (and so ran) -- callers use this to
    tell "ran nothing because there was nothing to run" apart from a normal
    run, see _warn_no_scripts.
    """
    entries = _collect_stage_scripts(ctx, stage_ids, name)
    for stage_id, script in entries:
        info(f"{name}-script '{stage_id}': {script}")
        ctx.run([str(script)])
    return bool(entries)


def _warn_no_scripts(ctx, name):
    """Info-log that ``--scripts <name>`` found nothing to run for this env -- see the two run_named_scripts paths.

    Without this, ``denver run <env> --scripts <name>`` for a name no stage
    declares silently exits 0 with no output at all, which reads exactly
    like a successful no-op run of something that *does* exist -- there is
    nothing else here to distinguish "ran zero entries" from "ran and it
    happened to have zero entries by design".
    """
    info(f"no '{name}' scripts to run for env '{ctx.env_dir.name}'")


def _setup_wrappers(ctx, config, config_path, active_wrappers, stage_index, stage_count, *, quiet):
    """Run each active wrapper stage's own setup(), so it is ready to be relocated into."""
    for w in active_wrappers:
        _run_stage_setup(
            ctx, config, config_path, w, quiet=quiet, stage_index=stage_index[w.stage], stage_count=stage_count
        )


def _relocated_run_cmd(
    config_path, names, *, quiet, verbose=False, until_stage, skip_stages, cli_argv=(), env_vars=None
):
    """The ``denver run <config> --scripts <name>`` argv (one pair per name) the wrapper re-invokes.

    This run's own filters are re-passed. ``cli_argv`` -- the tokens this
    env's own 'denver-custom-args:' flags consumed -- is re-passed for the same reason
    reinvoke_command does it: the inner denver declares the same flags and
    would otherwise only see their defaults. ``env_vars`` (-e/--env) is
    re-passed for the same reason.
    """
    cmd = ["python3", str(Path(__file__).resolve()), "run", str(config_path)]
    for name in names:
        cmd += ["--scripts", name]
    cmd += _relocated_run_quiet_verbose_flags(quiet, verbose)
    if until_stage:
        cmd += ["--until", until_stage]
    for stage_id in skip_stages:
        cmd += ["--skip", stage_id]
    for var_name, value in (env_vars or {}).items():
        cmd += ["--env", f"{var_name}={value}"]
    return [*cmd, *cli_argv]


def _relocated_run_quiet_verbose_flags(quiet, verbose):
    """The '-q'/'-v' flags _relocated_run_cmd's reinvocation needs, if either was given."""
    flags = []
    if quiet:
        flags.append("-q")
    if verbose:
        flags.append("-v")
    return flags


def _wrap_cmd(ctx, cmd, active_wrappers, stage_index, stage_count):
    """``cmd`` wrapped by every active wrapper, the outermost one applied last."""
    _mark_relocated(ctx, active_wrappers)
    for w in reversed(active_wrappers):
        ctx.stage_index, ctx.stage_count = stage_index[w.stage], stage_count
        ctx.stage_id = w.stage
        cmd = w.wrap(ctx, cmd)
    return cmd


def list_named_scripts(env_dir, config_path, *, until_stage=None, skip_stages=()):
    """Print every ``scripts: <name>:`` an env defines, grouped by name -- ``denver run <env> --scripts``.

    ``--scripts``'s names are deliberately open-ended (see run_named_scripts):
    any string an env's ``scripts:`` sections happen to use, with ``setup``
    and ``login`` conventions rather than a fixed set. That makes an env's
    own names unguessable, and they stack across the whole ``import:``
    chain, so reading one file does not answer it either.

    Deliberately resolved from the *raw* sections (whole-file and
    section-level ``import:`` applied, nothing else) rather than through
    resolve_provider_defaults: 'scripts:' is a generic stage key, and full
    resolution runs every provider's existence checks, so "which scripts
    exist?" would fail for reasons having nothing to do with scripts.
    """
    # config_path may not exist -- see _load_cli_config's own is_file() guard: an
    # env dir need not have its own denver.toml if -f/-c supply the whole config.
    config = load_config(config_path) if config_path.is_file() else {}
    config, _ = expand_section_imports(config, env_dir)
    stage_ids = filtered_stage_ids(config, env_dir, until_stage, skip_stages)

    by_name = _scripts_by_name(config, stage_ids)
    if not by_name:
        print(f"env '{env_dir.name}' defines no 'scripts:' entries -- nothing to --scripts", file=sys.stderr)
        return
    _print_script_names(env_dir, by_name)


def _stage_scripts_section(config, stage_id):
    """One stage's raw 'scripts:' mapping (empty when that stage declares none)."""
    return (config.get(stage_id) or {}).get("scripts") or {}


def _scripts_by_name(config, stage_ids):
    """``{script name: [(stage id, entry count)]}`` across the given stages, in 'stages:' order."""
    by_name = {}
    for stage_id in stage_ids:
        for name, entries in _stage_scripts_section(config, stage_id).items():
            by_name.setdefault(name, []).append((stage_id, len(entries or [])))
    return by_name


def _script_count_label(stage, count):
    """One stage's contribution to a --scripts name, e.g. ``uv (2 scripts)``."""
    return f"{stage} ({count} script{'s' if count != 1 else ''})"


def _print_script_names(env_dir, by_name):
    """Print every --scripts name this env defines, with the stages contributing to it."""
    print(f"available --scripts names for env '{env_dir.name}':", file=sys.stderr)
    for name in sorted(by_name):
        stages = ", ".join(_script_count_label(stage, count) for stage, count in by_name[name])
        print(f"  {name:<12} {stages}", file=sys.stderr)


def _stage_skip_reason(ctx, cfg):
    """Why this stage's setup() should be a no-op this run, per its generic 'skip-on-*:' scripts -- None if it should run.

    Checked fresh right before setup(), the same way 'disabled:' is checked
    ahead of time in active_stage_ids -- but this can't be decided ahead of
    time: it depends on running each script, a real side effect that must
    happen exactly once, right when the stage would otherwise run (an
    earlier stage may have changed what these scripts would report).
    '--force' bypasses both checks, same as it bypasses every provider's own
    checksum/skip-if logic.
    """
    if ctx.force:
        return None
    return _skip_kind_reason(ctx, cfg, "skip-on-success", 0, "skip-on-success scripts all exited 0") or (
        _skip_kind_reason(ctx, cfg, "skip-on-failure", 1, "skip-on-failure scripts all exited 1")
    )


def _skip_kind_reason(ctx, cfg, key, expected_code, message):
    """``message`` if ``cfg[key]``'s scripts all exit ``expected_code``, else None -- one 'skip-on-*:' kind (see _stage_skip_reason)."""
    scripts = cfg.get(key) or []
    if scripts and _skip_scripts_exit(ctx, key, scripts, expected_code):
        return message
    return None


def _skip_scripts_exit(ctx, label, scripts, expected_code):
    """True if every ``scripts`` entry exits with ``expected_code`` (see _stage_skip_reason)."""
    for script in scripts:
        path = Path(script)
        if not path.is_file():
            die(f"{label}: script not found: {path}")
        result = ctx.run([path], check=False, capture=False, query=True, echo=False)
        if result.returncode != expected_code:
            return False
    return True


def _run_stage_setup(ctx, config, config_path, provider, *, quiet, stage_index=1, stage_count=1, skip_state=None):
    """Resolve defaults, then run one stage's provider.setup(), hooks and all.

    Shared by run_stages() (every stage) and run_named_scripts() (a wrapper
    stage only, to relocate a setup stage's scripts into it -- see there;
    that caller passes no ``skip_state``, so 'depends-on:' never applies to
    it -- '--scripts' is a different, narrower mechanism than the normal
    pipeline). ``stage_index``/``stage_count`` (this stage's position among
    the stages running in *this* process -- see run_stages()) feed
    banner()'s '[i/n]'.

    ``skip_state``, when given, is checked *before* anything else: a
    'depends-on:' target that this same sequential walk already found
    skipped (statically, or dynamically via 'skip-on-success:'/
    'skip-on-failure:' below) means this stage does nothing this run either
    -- announced the same way as any other whole-stage skip, never entering
    it at all (no stage_banner(), no hooks, no setup()).
    """
    from denver_providers.context import stage_banner

    if skip_state is not None and _skip_for_blocked_dependency(ctx, config, provider, skip_state):
        return

    ctx.stage_index = stage_index
    ctx.stage_count = stage_count
    ctx.stage_id = provider.stage
    # Announced centrally, before anything this stage does -- including
    # before its own resolve_defaults, which can die on a bad path, and
    # before providers that check for their tool ahead of their first
    # banner() call. A stage that fails must still have said which stage it
    # is: that id is what --skip takes.
    stage_banner(ctx, provider.stage, provider.name)
    # Re-resolve this stage's defaults right before it actually runs, not
    # just once, upfront, in resolve_full_config(): a value like
    # zephyr.west or conan.exe (a PATH lookup) may resolve differently once
    # an earlier stage's setup() has actually installed/activated it (e.g.
    # the uv stage putting the pinned west/conan in the venv, ahead of any
    # copy the host happens to have). --show-config, which never runs any
    # setup(), can't see that -- this is what makes the real run more
    # accurate than the upfront snapshot for such values.
    #
    # From the *raw* section (kept by resolve_provider_defaults), never the
    # resolved one: a resolver can only fill an unset key, so re-resolving
    # its own output is a no-op for everything it already decided -- which
    # silently pinned conan.exe to whatever conan the host had, venv or no
    # venv. A fresh copy each time, so a resolver that reshapes a nested
    # value can't accumulate that across the two passes.
    raw_section = ctx.raw_sections.get(provider.stage)
    if raw_section is not None:
        config[provider.stage] = resolve_stage_section(provider, copy.deepcopy(raw_section), config, ctx)
    if _skip_for_dynamic_reason(ctx, config, provider, skip_state):
        return
    run_hook(ctx, config_path, f"pre-{provider.stage}")
    start = time.time()
    provider.setup(ctx)
    _apply_stage_env(ctx, provider.stage)
    duration = time.time() - start
    ctx.stage_timings.append((provider.stage, f"{duration:.1f}s"))
    _log_stage_performance(ctx, provider, quiet, duration)
    record_stage_performance(ctx, provider, start, duration)
    run_hook(ctx, config_path, f"post-{provider.stage}")


def _skip_for_blocked_dependency(ctx, config, provider, skip_state):
    """True (having already announced the skip) when a 'depends-on:' target of this stage was itself skipped.

    Split out of _run_stage_setup to keep that function's cognitive
    complexity low; see its docstring for what ``skip_state`` means.
    """
    blocking_dep = _blocked_by_dependency(config, provider.stage, skip_state)
    if not blocking_dep:
        return False
    reason = f"skipped (depends-on '{blocking_dep}')"
    skip_state.reasons[provider.stage] = reason
    _announce_skip(ctx, provider.stage, reason, skip_state)
    return True


def _skip_for_dynamic_reason(ctx, config, provider, skip_state):
    """True (having already announced/recorded the skip) when this stage's own section skips it now.

    Covers 'skip-on-success:'/'skip-on-failure:', which -- unlike a
    'depends-on:' block (_skip_for_blocked_dependency) -- can only be
    decided once the stage is actually reached; see _stage_skip_reason.
    """
    skip_reason = _stage_skip_reason(ctx, config.get(provider.stage) or {})
    if not skip_reason:
        return False
    info(f"{provider.name}[{provider.stage}]: {skip_reason}; skipping stage")
    if skip_state is not None:
        skip_state.reasons[provider.stage] = skip_reason
    return True


def _log_stage_performance(ctx, provider, quiet, duration):
    """Print the '--verbose' performance line for one stage.

    "performance infos in blue" -- --verbose only (and never under -q/-qq,
    denver's own output); the trace file (record_stage_performance) is
    written regardless, this is just the printed line.
    """
    if quiet == 0 and ctx.verbose:
        print(
            f"\033[94mINFO: stage '{provider.stage}' ({provider.name}) finished in {duration:.2f}s\033[39m",
            file=sys.stderr,
        )


def _apply_stage_env(ctx, stage_id):
    """Apply one stage's generic 'env:'/'env-prepend:'/'env-append:' keys into ctx.env -- every provider gets this.

    Read via ``ctx.section`` (not the section _run_stage_setup already has),
    so '${...}' is interpolated fresh, against whatever provider.setup() just
    put in ctx.env -- these keys exist precisely to let a stage reference
    that (a checkout's own path, a version a stage's own resolve_defaults
    pinned), the same way a 'pre-<stage>'/'post-<stage>' hook script could,
    without a project having to write one.

    Called unconditionally right after provider.setup() (see
    _run_stage_setup) -- --fast/--dry-run included: this is activation, the
    same as every provider's own env-prepend:'/'source:'/'env-append:', not
    a build step to skip. Never called at all for a stage
    _stage_skip_reason skipped outright -- consistent with 'disabled:'/
    skip-on-*: meaning "nothing about this stage runs this time", the same
    way a skipped custom stage's own 'source:' doesn't run either.
    """
    section = ctx.section(stage_id)
    ctx.apply_env_map(section.get("env"))
    _extend_stage_env(ctx, section.get("env-prepend"), prepend=True)
    _extend_stage_env(ctx, section.get("env-append"), prepend=False)


def _extend_stage_env(ctx, mapping, *, prepend):
    """Put every entry of one 'env-prepend:'/'env-append:' mapping in front of (or behind) its variable's current value."""
    for key, value in (mapping or {}).items():
        ctx.extend_env_var(key, ctx.resolve_env_value(value), prepend=prepend)


def _print_stage_summary(ctx):
    """Print what each stage cost, in pipeline order, right above the 'env started' line -- --verbose only.

    The per-stage 'finished in Ns' lines are scattered through a run's output
    -- often thousands of lines of build noise apart -- so the question they
    answer ("why did this take four minutes?") is only answerable by
    scrolling. Restated here as one block, in the order the stages ran.

    Stages that did not run keep their row, carrying the reason instead of a
    duration, so the summary matches the '[i/n]' trail above rather than
    silently shrinking.
    """
    if not ctx.verbose or not ctx.stage_timings:
        return
    width = max(len(stage) for stage, _ in ctx.stage_timings)
    for stage, outcome in ctx.stage_timings:
        print(f"\033[94m  {stage:<{width}}  {outcome}\033[39m", file=sys.stderr)


def _print_env_started(ctx, start_time):
    """Print a boxed 'INFO: env <name> started' line to stderr, right before the resolved command launches.

    The elapsed duration -- "performance output" -- is only added under
    --verbose (see _print_stage_summary for the same rule on the per-stage
    breakdown above it); without it, this box is just the plain completion
    marker. Under --dry-run the env was never started at all, elapsed time
    or not, so it says that instead regardless of --verbose.
    """
    _print_stage_summary(ctx)
    if ctx.dry_run:
        text = f"INFO: env {ctx.env_name} NOT started (--dry-run)"
    elif ctx.verbose:
        text = f"INFO: env {ctx.env_name} started in {time.time() - start_time:.2f}s"
    else:
        text = f"INFO: env {ctx.env_name} started"
    line = "-" * (len(text) + 4)
    print(f"\033[94m{line}\n| {text} |\n{line}\033[39m", file=sys.stderr)


def run_stages(env_dir, config, config_path, forwarded, *, options=None):
    """Build/enter the environment via its stages, then exec the command.

    ``options`` is everything this invocation chose about *how* to run
    (--until/--skip, -q, --fast/--force/--ci, --dry-run, --no-wait,
    -e/--env, the env's own 'denver-custom-args:' flags, the startup clock) as one
    RunOptions value; it defaults to "no flag given at all". See RunOptions
    for ``start_time``, which reaches the final "env started in Ns" line.

    banner()'s '[i/n]' numbers every stage the env *declares* in 'stages:'
    (see ``all_stage_ids`` below), not just the ones actually running -- a
    stage --until/--skip filtered out still gets counted and gets its own
    one-line "skipped by --until/--skip" banner, so e.g. skipping the last of
    5 declared stages shows '[5/5] ... skipped by --skip', never silently
    drops to a 4-stage trail. This numbering needs no cross-process
    plumbing (unlike start_time): both the outer and any reinvoked inner
    denver derive it identically, straight from the *same* denver.toml's
    'stages:' list, which --until/--skip never changes.

    Under ``dry_run`` every stage still runs in order and still resolves its
    own config -- only the commands and file writes it would perform are
    printed instead of performed (see Context.run). The one thing a dry run
    cannot show is what happens *inside* an active wrapper: relocating into
    it is itself one of the commands not being run, so
    _run_stages_via_wrapper stops at the reinvocation and says so.
    """
    from denver_providers.context import dry_run_legend

    options = options or RunOptions()

    if options.dry_run:
        dry_run_legend()

    all_stage_ids = _declared_stage_ids(config)
    stage_ids = filtered_stage_ids(config, env_dir, options.until_stage, options.skip_stages)
    config, ctx = _prepare_context(
        env_dir,
        config,
        config_path,
        no_wait=options.no_wait,
        quiet=options.quiet,
        verbose=options.verbose,
        fast=options.fast,
        force=options.force,
        ci=options.ci,
        dry_run=options.dry_run,
        cli_args=options.cli_args,
        env_vars=options.env_vars,
    )

    # each entry in 'stages:' is a pipeline stage (a provider type + config
    # id), run in order -- so an env can order conan before uv, have several
    # uv stages, etc. Instantiated for *every* declared id (not just the
    # runnable ones) purely to learn each skipped stage's kind (wrapper vs.
    # setup) and declared position -- make_stage() itself does no I/O.
    all_stages = _make_stages(config, all_stage_ids)
    static_reasons = _compute_static_skip_reasons(
        config, all_stage_ids, stage_ids, _until_cutoff(all_stage_ids, options.until_stage)
    )
    wrappers, setups, skipped_wrappers, skipped_setups = _partition_stages(
        all_stages, _runnable_stage_ids(stage_ids, static_reasons)
    )

    # A wrapper (e.g. docker) is active only on the host: skip it yourself
    # (e.g. `--skip docker`) to run on the host instead, or it's already
    # excluded above by stage filtering; also inactive once already inside it.
    active_wrappers = [] if _wrappers_inactive(ctx) else wrappers

    # ``reasons`` starts as the static verdict (--until/--skip, 'disabled:',
    # and 'depends-on:' cascaded from either) but is then mutated live as
    # each run_ids stage is actually reached (see _run_stage_setup): a
    # 'skip-on-success:'/'skip-on-failure:' skip can only be decided at that
    # point, and a later stage's own 'depends-on:' needs to see it.
    skip_state = _StageSkipState(
        stage_index=_stage_positions(all_stages),
        total=len(all_stages),
        reasons=static_reasons,
        all_stages=all_stages,
    )

    if active_wrappers:
        _run_stages_via_wrapper(
            ctx,
            config,
            config_path,
            forwarded,
            active_wrappers=active_wrappers,
            setups=setups,
            skipped_wrappers=skipped_wrappers,
            skipped_setups=skipped_setups,
            skip_state=skip_state,
            options=options,
        )
    else:
        _run_stages_directly(
            ctx,
            config,
            config_path,
            forwarded,
            setups=setups,
            skipped_wrappers=skipped_wrappers,
            skipped_setups=skipped_setups,
            skip_state=skip_state,
            quiet=options.quiet,
            start_time=options.start_time,
            export_env=options.export_env,
        )


def _prepare_context(env_dir, config, config_path, *, no_wait, env_vars=None, **resolve_kwargs):
    """Resolve the config, take the env's lock, and apply the whole-devshell environment.

    Shared by run_stages and run_named_scripts, which must set an env up the
    same way before either runs anything: the lock is taken before any stage
    touches shared state (and before a wrapper relocates), and hooks.env
    sources a script once, before anything else -- its exports (and the
    declarative 'env:' map applied right after) are visible to every stage
    and to the final command, i.e. they apply to the whole devshell.

    ``env_vars`` (-e/--env) is re-applied here, *after* the declarative
    'env:' map -- resolve_full_config already applied it once (so
    --show-config/interpolation see it), but that happens before 'env:' is
    even read, so without this second application 'env:' would silently win
    over an explicit -e of the same name. -e is meant to always have the
    final word, the same way '-c' always wins over 'import:'/'-cf' (see
    doc/configuration/denver-toml.md).
    """
    config, ctx = resolve_full_config(env_dir, config, config_path, env_vars=env_vars, **resolve_kwargs)
    ctx.acquire_lock(wait=not no_wait)
    ctx.ensure_state_dir()
    run_hook(ctx, config_path, "env")
    ctx.apply_env_map(config.get("env"))
    ctx.env.update(env_vars or {})
    return config, ctx


def _make_stages(config, stage_ids):
    """Instantiate every stage id in ``stage_ids``, in order."""
    from denver_providers import make_stage

    return [make_stage(stage_id, config) for stage_id in stage_ids]


def _stage_positions(stages):
    """``{stage id: 1-based position}``, feeding banner()'s '[i/n]'."""
    return {s.stage: i for i, s in enumerate(stages, 1)}


def _stage_ids_of(stages):
    """The stage ids of a list of provider instances, in order."""
    return [s.stage for s in stages]


def _runnable_stage_ids(stage_ids, static_reasons):
    """Which of the filtered stage ids actually run: all of them, minus any with a static skip reason.

    See _compute_static_skip_reasons for what counts as static (disabled,
    --until/--skip, and 'depends-on:' cascaded from either) -- unlike
    --skip/--until, none of that drops a stage's section from
    --show-config/filtered_stage_ids: it's about whether the stage's own
    setup() runs, not whether it's part of the declared pipeline.
    """
    return {s for s in stage_ids if static_reasons[s] is None}


def _compute_static_skip_reasons(config, all_stage_ids, stage_ids, cutoff):
    """Every declared stage's *static* skip reason, in 'stages:' order (``None`` if it would run).

    Static = knowable before any stage's setup() runs: --until/--skip
    filtering, 'disabled: true', and 'depends-on:' cascaded from either of
    those (or from another stage's own cascade). Deliberately excludes
    'skip-on-success:'/'skip-on-failure:', which can only be decided once a
    stage is actually reached, right before its own setup() -- see
    _stage_skip_reason and _blocked_by_dependency, which extends this same
    cascade to that dynamic case once run_stages starts walking the
    pipeline for real.

    A single forward pass suffices: _validate_depends_on (run from
    resolve_provider_defaults before this is ever called) already
    guarantees every 'depends-on:' id is declared strictly earlier in
    'stages:', so each dependency's reason is already final by the time its
    dependent is reached.
    """
    reasons = {}
    still_declared = set(stage_ids)
    for index, stage_id in enumerate(all_stage_ids):
        reasons[stage_id] = _static_skip_reason(config, stage_id, index, still_declared, cutoff, reasons)
    return reasons


def _static_skip_reason(config, stage_id, index, still_declared, cutoff, reasons):
    """One stage's static skip reason -- see _compute_static_skip_reasons, which this is split out of.

    ``reasons`` holds every earlier stage's already-decided reason, which is
    all a 'depends-on:' cascade here ever needs to look at (see
    _compute_static_skip_reasons's docstring for why one forward pass
    suffices).
    """
    if stage_id not in still_declared:
        return _cutoff_skip_reason(index, cutoff)
    if (config.get(stage_id) or {}).get("disabled"):
        return "skipped (disabled: true)"
    return _dependency_skip_reason(config, stage_id, reasons)


def _cutoff_skip_reason(index, cutoff):
    """'--until' vs '--skip' wording for a stage that --skip/--until already dropped from 'stage_ids'."""
    past_cutoff = cutoff is not None and index > cutoff
    return "skipped by --until" if past_cutoff else "skipped by --skip"


def _dependency_skip_reason(config, stage_id, reasons):
    """This stage's reason cascaded from a 'depends-on:' target already decided skipped, if any."""
    blocking_dep = next(
        (dep for dep in (config.get(stage_id) or {}).get("depends-on") or [] if reasons.get(dep)),
        None,
    )
    return f"skipped (depends-on '{blocking_dep}')" if blocking_dep else None


def _partition_stages(all_stages, runnable):
    """Split every declared stage by kind and by whether it is going to run.

    Returns ``(wrappers, setups, skipped_wrappers, skipped_setups)`` -- the
    four groups both run paths walk, each in 'stages:' order.
    """
    buckets = {
        ("wrapper", True): [],
        ("setup", True): [],
        ("wrapper", False): [],
        ("setup", False): [],
    }
    for stage in all_stages:
        kind = "wrapper" if stage.kind == "wrapper" else "setup"
        buckets[(kind, stage.stage in runnable)].append(stage)
    return (
        buckets[("wrapper", True)],
        buckets[("setup", True)],
        buckets[("wrapper", False)],
        buckets[("setup", False)],
    )


def _until_cutoff(all_stage_ids, until_stage):
    """The declared index --until truncated at, or None when no --until named a declared stage."""
    if until_stage in all_stage_ids:
        return all_stage_ids.index(until_stage)
    return None


def _wrappers_inactive(ctx):
    """True when no wrapper stage may relocate from here, whatever the env declares.

    Two independent reasons, deliberately OR-ed:

    * denver already relocated this process (``ctx.relocated``) -- its own
      bookkeeping, stated by the outer run, and true for wrapper kinds no
      filesystem marker could reveal (a ``custom`` stage's ``launcher:``);
    * this is a container somebody else started (``ctx.in_container``), where
      relocating again would mean starting a container inside a container.

    The second is deliberately *unscoped*: an env launched from inside a
    devshell builds right there rather than starting a second container, even
    though nothing relocated *this* env. Scoping it per-env would turn that
    into docker-in-docker.
    """
    return bool(ctx.relocated) or ctx.in_container


def _mark_relocated(ctx, wrappers):
    """Record which wrapper stages are relocating this run, for the denver that lands inside.

    Set before the wrappers' ``wrap()`` runs, so a wrapper that has to carry
    the environment across a boundary itself (docker, via ``-e``) can read it
    off ctx.env; a wrapper whose child simply inherits the environment (a
    ``custom`` launcher) needs no such help.
    """
    from denver_providers.context import RELOCATED_VAR

    ctx.set(RELOCATED_VAR, ",".join(w.stage for w in wrappers))


class _StageSkipState:
    """Everything ``_show_skipped``/``_run_stage_setup`` need to explain why a stage isn't running -- see run_stages.

    ``all_stages`` is every declared stage, in 'stages:' order: it is what
    lets both run paths walk the pipeline once, in order, rather than
    running the stages first and reporting the skipped ones afterwards.

    ``reasons`` starts out holding only the *static* verdict
    (_compute_static_skip_reasons) but is mutated live as the walk reaches
    each stage: a 'skip-on-success:'/'skip-on-failure:' skip, or a
    'depends-on:' cascade from one, is only known at that point -- see
    _run_stage_setup/_blocked_by_dependency. It is a plain dict, shared (not
    copied) by every stage's lookup, precisely so a later stage's own
    'depends-on:' sees an earlier stage's just-decided runtime reason.
    """

    def __init__(self, *, stage_index, total, reasons, all_stages):
        self.stage_index = stage_index
        self.total = total
        self.reasons = reasons
        self.all_stages = all_stages


class RunOptions:
    """Everything one invocation chose about *how* to run an env, as one value -- see run_stages.

    These travel together from the command line all the way into a wrapper
    reinvocation (see reinvoke_command), so they are passed as one bundle
    rather than as a dozen parameters that every function in that chain
    would have to name again. Every field defaults, so a caller that cares
    about one flag (a test, mostly) states only that one.

    ``start_time`` is resolved here rather than left as None: it is this
    whole startup's clock origin, and the invocation *is* when the startup
    began. A reinvoked denver is handed the outer run's own value (the
    hidden --start-time), so the "env started in Ns" line always reflects
    the whole startup rather than the inner process's much shorter one.
    """

    def __init__(
        self,
        *,
        until_stage=None,
        skip_stages=(),
        quiet=0,
        verbose=False,
        fast=False,
        force=False,
        ci=False,
        dry_run=False,
        no_wait=False,
        start_time=None,
        cli_args=None,
        env_vars=None,
        export_env=None,
    ):
        """Hold one invocation's options; see the class docstring for ``start_time``."""
        self.until_stage = until_stage
        self.skip_stages = skip_stages
        self.quiet = quiet
        self.verbose = verbose
        self.fast = fast
        self.force = force
        self.ci = ci
        self.dry_run = dry_run
        self.no_wait = no_wait
        self.start_time = time.time() if start_time is None else start_time
        self.cli_args = _cli_args(cli_args)
        self.export_env = export_env
        # -e/--env NAME=VALUE (see build_arg_parser); dict rather than a list
        # of tuples so a later entry naturally overrides an earlier one of
        # the same name, same as -c. Order preserved (dicts remember
        # insertion order), so a wrapper reinvocation re-passes them in the
        # order the user gave them.
        self.env_vars = dict(env_vars or {})


def _show_skipped(ctx, skipped, skip_state):
    """Print a "skipped by ..." banner for each stage in ``skipped`` (disabled: true, --until, --skip, or depends-on)."""
    for s in skipped:
        _announce_skip(ctx, s.stage, skip_state.reasons[s.stage], skip_state)


def _announce_skip(ctx, stage_id, reason, skip_state):
    """Print one "[i/n] stage '<id>' <reason>" banner and record it in ``ctx.stage_timings``.

    Shared by _show_skipped (every statically-skipped stage) and
    _run_stage_setup (a runtime skip-on-*/depends-on skip, discovered only
    once the pipeline walk actually reaches that stage) -- both are a whole
    stage doing nothing this run, so both get the same one-line banner
    rather than stage_banner()'s "entering a stage" line.
    """
    from denver_providers.context import skip_banner

    ctx.stage_index, ctx.stage_count = skip_state.stage_index[stage_id], skip_state.total
    ctx.stage_timings.append((stage_id, reason))
    skip_banner(ctx, stage_id, reason)


def _blocked_by_dependency(config, stage_id, skip_state):
    """The first 'depends-on:' target of ``stage_id`` already known skipped this run, or ``None``.

    Static reasons (disabled/--until/--skip and their own depends-on
    cascade) are already final in ``skip_state.reasons`` by construction --
    see _compute_static_skip_reasons. What this adds is the dynamic case: a
    dependency that looked runnable when that dict was built, but turned out
    to be skip-on-success/skip-on-failure skipped once its own setup() was
    actually reached, earlier in this same sequential walk over
    'stages:' order (_validate_depends_on guarantees every dependency is
    declared -- and so walked -- strictly before ``stage_id``).
    """
    for dep in (config.get(stage_id) or {}).get("depends-on") or []:
        if skip_state.reasons.get(dep):
            return dep
    return None


def _run_stages_via_wrapper(
    ctx,
    config,
    config_path,
    forwarded,
    *,
    active_wrappers,
    setups,
    skipped_wrappers,
    skipped_setups,
    skip_state,
    options,
):
    """Host side: prepare the wrapper(s), then relocate execution into them (see run_stages)."""
    # Same single ordered walk as _run_stages_directly, for the same reason:
    # each declared stage reports in its own pipeline position. A *runnable*
    # setup stage is passed over silently here -- it runs inside the wrapper,
    # and the re-invoked denver banners it there. Its skipped siblings are
    # likewise left to that inner run, unless nothing will re-invoke at all
    # (a pure wrapper), in which case this is the only chance to show them.
    # skipped setup stages are left to the inner run too, unless nothing will
    # re-invoke at all (a pure wrapper), in which case this is the only
    # chance to show them.
    report_ids = set(_stage_ids_of(skipped_wrappers))
    if not setups:
        report_ids |= set(_stage_ids_of(skipped_setups))
    for stage in skip_state.all_stages:
        _prepare_or_report(
            ctx,
            config,
            config_path,
            stage,
            run_ids=set(_stage_ids_of(active_wrappers)),
            report_ids=report_ids,
            skip_state=skip_state,
            quiet=options.quiet,
        )

    cmd = _wrapper_target_cmd(
        ctx, config, config_path, forwarded, active_wrappers=active_wrappers, setups=setups, options=options
    )
    _relocate_and_exec(ctx, cmd, active_wrappers, skip_state, options, has_setups=bool(setups))


def _prepare_or_report(ctx, config, config_path, stage, *, run_ids, report_ids, skip_state, quiet):
    """Run ``stage``'s setup() if it is one of ``run_ids``, report it as skipped if it is one of ``report_ids``.

    Anything in neither group is passed over silently -- it runs somewhere
    else (inside the wrapper), and the denver that lands there banners it.
    """
    if stage.stage in run_ids:
        _run_stage_setup(
            ctx,
            config,
            config_path,
            stage,
            quiet=quiet,
            stage_index=skip_state.stage_index[stage.stage],
            stage_count=skip_state.total,
            skip_state=skip_state,
        )
    elif stage.stage in report_ids:
        _show_skipped(ctx, [stage], skip_state)


def _wrapper_target_cmd(ctx, config, config_path, forwarded, *, active_wrappers, setups, options):
    """What the wrapper relocates: a denver reinvocation for the setup stages, else the command itself."""
    if not setups:
        # pure wrapper: relocate the user's command (or default) directly.
        # skipped_setups were already shown in pipeline position by the walk
        # above, since nothing will re-invoke to show them. Only 'docker'
        # actually relocates into a container -- a 'custom' wrapper's own
        # 'launcher:' could prepend anything at all (ssh, nsenter, a plain
        # wrapper script, ...), so completion is only wired in (in_container)
        # when docker is genuinely among the wrappers relocating this cmd.
        return resolve_command(config, forwarded, in_container=any(w.name == "docker" for w in active_wrappers))
    # setup providers run *inside* the wrapper: re-invoke denver there
    # -- it recomputes skipped_setups identically (same denver.toml,
    # same --until/--skip) and shows those banners itself.
    _note_not_previewed(ctx, "stages", setups, active_wrappers)
    return reinvoke_command(config_path, forwarded, _stage_ids_of(active_wrappers), options=options)


def _note_not_previewed(ctx, what, setups, active_wrappers):
    """--dry-run: say that ``what`` runs inside the wrapper and cannot be previewed, and how to see it.

    Under --dry-run the reinvocation carrying those stages into the wrapper
    is itself one of the commands *not* being run, so they are never reached.
    Nor could they be previewed by passing --dry-run inward: getting inside
    the wrapper means really starting it, which is exactly what this run
    promised not to do (and its image may be something an earlier
    printed-not-run `compose build` would have produced). Said out loud
    rather than left as a silently short pipeline -- run `--skip <wrapper>`
    to preview those stages on the host instead.
    """
    if not ctx.dry_run:
        return
    inside = ", ".join(_stage_ids_of(active_wrappers))
    skips = " --skip ".join(_stage_ids_of(active_wrappers))
    ctx.dry_note(
        "!",
        f"{what} {', '.join(_stage_ids_of(setups))} run inside {inside} and are not previewed -- "
        f"re-run with --skip {skips} to see them",
    )


def _relocate_and_exec(ctx, cmd, active_wrappers, skip_state, options, *, has_setups):
    """Wrap ``cmd`` through the active wrapper(s) and exec it, announcing a ready env if nothing re-invokes."""
    cmd = _wrap_cmd(ctx, cmd, active_wrappers, skip_state.stage_index, skip_state.total)
    # with no setup stages nothing re-invokes, so this is where the env is ready
    if not has_setups and options.quiet == 0:
        _print_env_started(ctx, options.start_time)
    ctx.exec(cmd)


def _run_stages_directly(
    ctx,
    config,
    config_path,
    forwarded,
    *,
    setups,
    skipped_wrappers,
    skipped_setups,
    skip_state,
    quiet,
    start_time,
    export_env=None,
):
    """Host with the wrapper stage skipped (or already inside it): build the env and run the command directly."""
    # One walk over the declared stages, in 'stages:' order, so the progress
    # trail reads as the pipeline it describes: a stage either runs here or
    # says why it didn't, in its own position. Running every stage first and
    # only then reporting the skipped ones (as this did) puts '[4/4] d' above
    # '[2/4] b skipped', and -- worse -- loses the skip lines entirely when an
    # earlier stage dies, which is exactly when they explain the most.
    #
    # skipped_wrappers is shown only where a user's own --until/--skip is what
    # removed them. In a process denver relocated (ctx.relocated), a wrapper
    # stage's absence is either it having already run for real in the outer
    # process or reinvoke_command's own forced --skip -- neither should print
    # a second, spurious "skipped by --skip" banner. Keyed on denver's own
    # bookkeeping rather than on being in a container: a hand-started
    # container where the user really did type --skip should still say so.
    report_ids = set(_stage_ids_of(skipped_setups))
    if not ctx.relocated:
        report_ids |= set(_stage_ids_of(skipped_wrappers))
    for stage in skip_state.all_stages:
        _prepare_or_report(
            ctx,
            config,
            config_path,
            stage,
            run_ids=set(_stage_ids_of(setups)),
            report_ids=report_ids,
            skip_state=skip_state,
            quiet=quiet,
        )
    if not quiet:
        _print_env_started(ctx, start_time)
        print_logo()
    # ctx.in_container covers both ways this path is reached already inside
    # one: the reinvoked-denver-in-docker case, and a container someone else
    # started denver in directly (see _wrappers_inactive) -- either way,
    # completion is wired into the fallback/configured interactive shell the
    # same as the pure-wrapper case in _wrapper_target_cmd. --skip docker
    # (ctx.in_container False here) is the one case genuinely running on the
    # host, so cmd is left alone.
    cmd = resolve_command(config, forwarded, in_container=ctx.in_container)
    run_hook(ctx, config_path, "pre-cmd")
    if export_env:
        ctx.write_export_env(export_env)
    ctx.exec(cmd)


def hook_names_for_stages(stage_ids):
    """Every hook name run_stages actually calls run_hook() for, in order.

    'env' first, then 'pre-<stage>'/'post-<stage>' per declared stage, then
    'pre-cmd' last.
    """
    names = ["env"]
    for stage_id in stage_ids:
        names += [f"pre-{stage_id}", f"post-{stage_id}"]
    names.append("pre-cmd")
    return names


def resolve_hooks(ctx, config_path, stage_ids):
    """The effective 'hooks:' section for --show-config.

    For every recognised hook name (see hook_names_for_stages), resolves the
    script path(s) that would actually run: base-first across the whole
    'import:' chain, from each layer's explicit 'hooks: <name>:' entry --
    exactly what run_hook()/collect_hook_entries() use at real-run time. None
    if nothing would run for that name. Computed here (rather than read
    straight off the raw 'hooks:' key) because the effective list spans every
    layer of the 'import:' chain, not just the env being launched.
    """
    resolved = {}
    for name in hook_names_for_stages(stage_ids):
        entries = collect_hook_entries(config_path, name)
        scripts = [str(ctx.resolve_path(script, base=base_dir)) for base_dir, script in entries]
        resolved[name] = scripts or None
    return resolved


def _sorted_nested(value):
    """Recursively sort every nested dict's keys alphabetically; lists/scalars pass through untouched.

    show_config() controls its own *top-level* key order explicitly (not
    alphabetical -- see there); this gives everything below that (a
    section's own keys, a hook name's own sub-keys, ...) the same
    alphabetical, easy-to-scan ordering.
    """
    if isinstance(value, dict):
        return _sorted_dict(value)
    if isinstance(value, list):
        return _sorted_list(value)
    return value


def _sorted_dict(value):
    """A mapping's keys alphabetically, each value recursively sorted."""
    return {k: _sorted_nested(value[k]) for k in sorted(value)}


def _sorted_list(value):
    """A list's entries recursively sorted (the list's own order is kept)."""
    return [_sorted_nested(v) for v in value]


def _ordered_stage_section(section):
    """Order one resolved stage section for --show-config.

    GENERIC_STAGE_KEYS first (whichever of those keys are present, in that
    fixed order), then every remaining (provider-specific) key
    alphabetically. Each value is still recursively sorted via
    _sorted_nested -- only this section's own top level is special-cased.
    """
    keys = _present_generic_keys(section) + _provider_keys(section)
    return {key: _sorted_nested(section[key]) for key in keys}


def _present_generic_keys(section):
    """The GENERIC_STAGE_KEYS this section actually has, in that fixed order."""
    return [key for key in GENERIC_STAGE_KEYS if key in section]


def _provider_keys(section):
    """This section's provider-specific keys (everything GENERIC_STAGE_KEYS doesn't name), alphabetically."""
    return sorted(k for k in section if k not in GENERIC_STAGE_KEYS)


# --------------------------------------------------------------------------- #
# --show-config rendering
# --------------------------------------------------------------------------- #
def supports_color(stream=None):
    """Whether ``stream`` (default: stdout) is a real terminal that would render ANSI color.

    Used to decide --show-config's own syntax highlighting (see dump_toml).
    ``NO_COLOR`` (https://no-color.org -- any non-empty value) always wins
    when set: an explicit, universal opt-out. ``FORCE_COLOR`` is the
    matching opt-in, for a pipeline that redirects stdout but still wants
    color (e.g. piping through ``less -R``) -- checked after ``NO_COLOR``,
    so an explicit "no" still wins over an explicit "yes" if a caller
    somehow sets both. ``TERM=dumb`` is the traditional "this terminal
    doesn't do escape codes at all" signal. Otherwise, color only makes
    sense talking to a real terminal, not a file or another program's
    stdin -- hence the ``isatty()`` fallback.
    """
    stream = sys.stdout if stream is None else stream
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    return stream.isatty()


# Bright-* (9x) ANSI codes, reset to default foreground (\033[39m) rather than
# a full \033[0m -- same convention as every other color code in this codebase
# (context.py's banner()/stage_banner()/etc, the blue "finished in Ns" lines
# below) needs no different reset story. One color per syntactic role, chosen
# to stay distinct from those existing yellow/blue banners even though the
# two are never on screen at once (--show-config never runs a stage).
_TOML_HEADER_COLOR = "\033[95m"  # [section] / [[section]]
_TOML_KEY_COLOR = "\033[96m"  # # a 'key = ...' line
_TOML_STRING_COLOR = "\033[92m"  # "..." / '''...'''
_TOML_SCALAR_COLOR = "\033[93m"  # true/false, numbers
_TOML_COMMENT_COLOR = "\033[90m"  # a '# key = null' line -- unset, de-emphasized
_TOML_RESET = "\033[39m"

# Set once per dump_toml() call (see there), consulted by _c() -- the same
# "module global, not threaded through every function" pattern context.py's
# _quiet_level/_verbose already use for banner()/info(), for the same reason:
# threading a `color` bool through every one of dump_toml's dozen small
# recursive helpers would obscure what each is actually building.
_toml_color_enabled = False


def _c(code, text):
    r"""Wrap ``text`` in ANSI color ``code``, reset after -- a no-op unless dump_toml() was asked for color.

    Every call site here opens and closes its own span; none ever nests one
    of these inside another. That matters because \033[39m resets to
    *default* foreground, not "whatever the enclosing span's color was" --
    nesting two spans would truncate the outer one right at the inner
    span's own reset. dump_toml()'s own helpers are written so no call site
    ever needs to: a colored key/value pair's two spans are always
    separated by plain punctuation (` = `, `, `, ...), and a '[section]'
    header or '# key = null' comment line colors itself as one single span,
    built from the *plain* (uncolored) _toml_key()/_toml_path(), never the
    already-colored key text a real 'key = value' line uses.
    """
    if not _toml_color_enabled or not text:
        return text
    return f"{code}{text}{_TOML_RESET}"


def dump_toml(data, *, color=False):
    """A minimal, hand-written TOML rendering of ``data`` for --show-config.

    Covers exactly the shapes a resolved denver config can take (nested dicts, lists of scalars or of
    dicts, strings, numbers, bools, ``None``), not the full TOML spec. No dependency needed: this is
    denver's own display format, not something round-tripped through a TOML library.

    TOML has no ``null`` -- a ``None`` value (fill_unset()'s placeholder for a documented-but-unset
    key, see show_config) is rendered as a commented-out ``# key = null`` line instead of dropping it
    silently, so --show-config-full's "every possible key, unset ones included" contract still holds
    (the default --show-config drops ``None`` values before they ever reach here -- see show_config's
    own ``minimal`` handling).

    ``color`` (see supports_color) turns on ANSI syntax highlighting -- off
    by default, since every non-CLI caller (tests writing a synthetic
    denver.toml to disk, mostly) wants the plain text a real TOML file is.
    """
    global _toml_color_enabled
    _toml_color_enabled = color
    try:
        lines = []
        _dump_toml_table(data, (), lines)
        return "\n".join(lines) + ("\n" if lines else "")
    finally:
        _toml_color_enabled = False


def _dump_toml_table(table, path, lines):
    """Append the whole document's TOML rendering to ``lines``.

    Plain keys (scalars, ``None``, arrays) come first, then nested tables/array-of-tables get their
    own '[section]'/'[[section]]' header -- TOML requires a table's own keys to precede its
    sub-tables, so grouping here (rather than emitting strictly in ``table``'s own key order) is
    unavoidable; each group keeps its own relative order.

    Only this top level hands out '[section]' headers to plain tables; everything below one is
    rendered inline -- see _dump_toml_section_body.
    """
    plain, nested = _toml_partition_table(table)
    for key, value in plain:
        _dump_toml_plain_key(key, value, lines)
    for key, value in nested:
        _dump_toml_nested_key(value, (*path, key), lines)


def _dump_toml_section_body(table, path, lines):
    """Append one section's own keys: nested tables inline, arrays of tables as '[[...]]' blocks.

    A sub-table could equally get a '[a.b]' header of its own -- valid TOML, and what the document's
    top level does. Below that, a header buys nothing and costs clarity: it moves the sub-table an
    arbitrary distance away from the section it belongs to, and under a '[[a.b]]' entry it is
    outright ambiguous, since such a header attaches to whichever entry precedes it *by position*
    with nothing on the line saying which one. So a section is rendered self-contained: one line per
    key, nested tables as inline '{ ... }' tables, the way they are written by hand.

    Two shapes are still given a header of their own at any depth -- see _toml_needs_header.
    """
    plain, headed = _toml_partition_section(table)
    for key, value in plain:
        _dump_toml_plain_key(key, value, lines)
    for key, value in headed:
        _dump_toml_nested_key(value, (*path, key), lines)


def _toml_partition_section(table):
    """``table``'s items split into (inline, still-needs-a-header) -- see _dump_toml_section_body."""
    plain = [(k, v) for k, v in table.items() if not _toml_needs_header(v)]
    headed = [(k, v) for k, v in table.items() if _toml_needs_header(v)]
    return plain, headed


def _toml_needs_header(value):
    """Whether ``value`` still needs a '[section]'/'[[section]]' header below the document's top level.

    Only two shapes do. An **array of tables**, because its entries stay far more readable as blocks
    of one key per line than as one flattened line each. And a **table holding an unset key**
    somewhere inside it, because an unset key renders as a ``# key = null`` comment line (see
    _dump_toml_plain_key) and TOML has no way to put a comment inside an inline table -- so such a
    table keeps its header rather than losing the key from --show-config-full altogether.
    """
    if isinstance(value, dict):
        return bool(value) and not _toml_inlinable(value)
    return _toml_is_array_of_tables(value)


def _toml_inlinable(value):
    """Whether ``value`` can be rendered inline at all: nothing unset (``None``) anywhere inside it."""
    pending = [value]
    while pending:
        current = pending.pop()
        if current is None:
            return False
        pending.extend(_toml_nested_values(current))
    return True


def _toml_nested_values(value):
    """The values sitting directly inside ``value``: a table's values, an array's entries, else none."""
    if isinstance(value, dict):
        return list(value.values())
    if isinstance(value, list):
        return value
    return []


def _toml_partition_table(table):
    """``table``'s items split into (plain, nested) -- see _dump_toml_table for why the split matters."""
    plain = [(k, v) for k, v in table.items() if not _toml_is_table_like(v)]
    nested = [(k, v) for k, v in table.items() if _toml_is_table_like(v)]
    return plain, nested


def _dump_toml_plain_key(key, value, lines):
    """Append one plain (non-table-like) ``key = value`` line -- commented out (``None``) if unset."""
    if value is None:
        # the whole line is one comment span -- built from the plain _toml_key(),
        # not a colored one, see _c's own docstring for why
        lines.append(_c(_TOML_COMMENT_COLOR, f"# {_toml_key(key)} = null"))
    else:
        lines.append(f"{_c(_TOML_KEY_COLOR, _toml_key(key))} = {_toml_value(value)}")


def _dump_toml_nested_key(value, child_path, lines):
    """Append one table-like key's '[section]' (a dict) or '[[section]]' blocks (a list of dicts)."""
    if lines and lines[-1] != "":
        lines.append("")
    if isinstance(value, dict):
        lines.append(_c(_TOML_HEADER_COLOR, f"[{_toml_path(child_path)}]"))
        _dump_toml_section_body(value, child_path, lines)
    else:
        _dump_toml_array_of_tables(value, child_path, lines)


def _dump_toml_array_of_tables(entries, child_path, lines):
    """Append one '[[section]]' block per entry of a non-empty list of dicts."""
    for i, entry in enumerate(entries):
        if i:
            lines.append("")
        lines.append(_c(_TOML_HEADER_COLOR, f"[[{_toml_path(child_path)}]]"))
        _dump_toml_section_body(entry, child_path, lines)


def _toml_is_table_like(value):
    """Whether ``value`` needs a '[section]'/'[[section]]' header rather than an inline value.

    True for a non-empty dict, or a non-empty list whose entries are all dicts. An empty dict/list is
    rendered inline instead ('{}'/'[]') -- a header with nothing under it would be a pointless empty
    section.
    """
    if isinstance(value, dict):
        return bool(value)
    return _toml_is_array_of_tables(value)


def _toml_is_array_of_tables(value):
    """Whether ``value`` is a non-empty list of dicts -- the one shape still given '[[section]]' headers at any depth."""
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


_TOML_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_key(key):
    """One TOML key -- bare, or quoted if it isn't plain identifier-ish text.

    Bare covers every real denver.toml key (letters/digits/underscore/dash); anything else is quoted.
    """
    key = str(key)
    return key if _TOML_BARE_KEY_RE.match(key) else _toml_string(key)


def _toml_path(path):
    """A dotted '[a.b.c]'/'[[a.b.c]]' table header path from a tuple of keys."""
    return ".".join(_toml_key(p) for p in path)


def _toml_value(value):
    """One TOML value expression: a scalar, an array, or a table rendered inline.

    A dict reaches here either because it is empty (never worth a header of its own, see
    _toml_is_table_like) or because it sits below the document's top level, which is rendered
    self-contained -- see _dump_toml_section_body.
    """
    if isinstance(value, dict):
        return _toml_inline_table(value)
    if isinstance(value, list):
        return _toml_array(value)
    return _toml_scalar(value)


def _toml_array(values):
    """A multi-line TOML array literal, one entry per line.

    Denver's config lists (file paths, requirements, ...) are usually short, but this stays readable
    even when they aren't.
    """
    if not values:
        return "[]"
    entries = ",\n".join(f"  {_toml_inline_value(v)}" for v in values)
    return f"[\n{entries},\n]"


def _toml_inline_value(value):
    """One TOML value for an array *entry*.

    Like ``_toml_value``, but a dict/list entry can't get its own '[section]' header (arrays have no
    headers), so a non-empty dict renders as an inline table (``{ k = v, ... }``) here instead. Reached
    for e.g. a malformed ``denver-custom-args:`` entry mixing mapping and non-mapping items in one list -- not
    table-like as a whole (see _toml_is_table_like), but each dict entry in it still needs *some*
    rendering.
    """
    if isinstance(value, dict):
        return _toml_inline_table(value)
    if isinstance(value, list):
        return _toml_array(value)
    return _toml_scalar(value)


def _toml_inline_table(value):
    """One inline TOML table (``{ k = v, ... }``) for a dict reached outside a '[section]' context."""
    if not value:
        return "{}"
    items = ", ".join(f"{_c(_TOML_KEY_COLOR, _toml_key(k))} = {_toml_single_line_value(v)}" for k, v in value.items())
    return "{ " + items + " }"


def _toml_single_line_value(value):
    """One TOML value rendered without a line break, as an inline table's values must be.

    TOML allows an array to span lines (_toml_array's normal rendering) but never an inline table:
    everything between its braces has to sit on one line, so an array *inside* one is written out
    flat here rather than one entry per line.
    """
    if isinstance(value, dict):
        return _toml_inline_table(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_single_line_value(v) for v in value) + "]"
    return _toml_scalar(value)


def _toml_scalar(value):
    """One TOML scalar literal -- bool/int/float/str.

    Anything else (e.g. a bare ``None`` reaching here, which shouldn't happen -- fill_unset() only
    ever nulls a dict *value*, never a list entry) is a bug in the caller, so this raises rather than
    silently rendering something wrong.

    Colored here, not in a wrapper around the call site: unlike a key (see
    _c's docstring), a scalar's own text is never reused for anything but
    itself -- there's no header/comment context it also has to serve plain.
    """
    if isinstance(value, bool):
        return _c(_TOML_SCALAR_COLOR, "true" if value else "false")
    if isinstance(value, (int, float)):
        return _c(_TOML_SCALAR_COLOR, repr(value))
    if isinstance(value, str):
        return _c(_TOML_STRING_COLOR, _toml_string(value))
    raise TypeError(f"cannot render {value!r} as a TOML scalar")


def _toml_string(value):
    r"""A TOML string literal for ``value``.

    A multi-line literal string ('''...''') for embedded newlines (matches YAML's '|' block scalars
    almost exactly -- raw content, no escaping needed), falling back to an escaped basic string
    otherwise (or if the content itself contains ''').

    The ``\n`` placed right after the opening ``'''`` below is a pure formatting separator (so the
    content starts on its own line) -- TOML's own "trim one newline immediately after the opening
    delimiter" rule removes exactly that one on read-back, regardless of what ``value`` itself starts
    with, so this always round-trips to ``value`` unchanged.
    """
    if "\n" in value and "'''" not in value:
        return f"'''\n{value}'''"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped.replace("\n", "\\n") + '"'


def show_config(
    env_dir,
    config,
    config_path,
    until_stage=None,
    skip_stages=(),
    *,
    cli_args=None,
    env_vars=None,
    minimal=False,
    format="yml",
):
    """Print the fully resolved config -- exactly what the real run would use.

    Rendered as YAML, or TOML with ``format="toml"`` (see --format).

    Whole-file 'import:' stacking, section-level 'import:' stacking, and
    every provider's defaults are all baked in. 'stages:' is narrowed by
    --until/--skip the same way the real run filters it (see
    filtered_stage_ids), and any stage section dropped by that filtering is
    removed from the output too -- so e.g. `--skip docker` shows a config
    with no 'docker:' section and no 'docker' in 'stages:', matching what
    that run would actually use.

    Printed in four groups, each internally in its own order (not a single
    alphabetical sweep over every top-level key): 'version:' then
    'denver-version:' first (whichever are set at all -- the schema version
    and the denver version this file needs, what a reader wants to know
    before anything else); then the rest of the generic (non-stage) keys,
    alphabetically; then 'stages:' itself; then each stage's own section, in
    pipeline order (the order it appears in 'stages:'), not alphabetically
    -- so scanning top to bottom mirrors the order stages actually run in.
    Within one stage's own section, the generic keys come first in a fixed
    order (see GENERIC_STAGE_KEYS), then the provider-specific keys
    alphabetically. Everything *below* that (a section's own nested keys, a
    hook name's own sub-keys, ...) stays alphabetical (see _sorted_nested).

    ``minimal`` (plain --show-config; --show-config-full turns it off)
    additionally drops every stage-section key the env didn't actually
    configure -- i.e. whatever a provider's resolve_defaults() filled in
    itself, static or computed, fill_unset()'s ``None`` included (see
    _drop_defaulted_stage_keys) -- and then, recursively, every remaining
    key whose value is ``None`` or an empty dict/list (see
    _drop_null_values), so only keys that actually carry an explicit value
    remain. Other falsy scalars (``0``/``""``) are kept as-is.
    """
    resolved, ctx = resolve_full_config(env_dir, config, config_path, cli_args=cli_args, env_vars=env_vars)
    stage_ids = filtered_stage_ids(config, env_dir, until_stage, skip_stages)
    _drop_filtered_sections(resolved, stage_ids)
    resolved["stages"] = stage_ids
    resolved["hooks"] = resolve_hooks(ctx, config_path, stage_ids)

    ordered = _ordered_config(resolved, stage_ids)
    if minimal:
        for stage_id in stage_ids:
            ordered[stage_id] = _drop_defaulted_stage_keys(ordered[stage_id], ctx.raw_sections.get(stage_id, {}))
        ordered = _drop_null_values(ordered)

    if format == "toml":
        print(dump_toml(ordered, color=supports_color()))
    else:
        print(yaml.safe_dump(ordered, sort_keys=False, default_flow_style=False))


def _drop_defaulted_stage_keys(section, raw_section):
    """Keep only ``section``'s keys the env actually wrote, for the default --show-config (minimal).

    Every key resolve_stage_section() added on its own -- a provider's
    static/filesystem/PATH-derived default, or fill_unset()'s ``None`` for a
    documented-but-unset one -- is a default, not something the env
    configured, so it's left out here.
    """
    return {key: value for key, value in section.items() if key in raw_section}


def _drop_null_values(value):
    """Recursively drop dict keys with no real value, for the default --show-config (minimal).

    Dropped: ``None`` (fill_unset()'s placeholder for a documented-but-unset
    key), and any dict/list that is empty -- either to start with, or because
    filtering emptied it (e.g. a stage section that was entirely unset, or a
    hook name whose only entries got dropped). Other falsy scalars (``0``,
    ``""``) came from an actual resolved default or config value and are
    kept untouched.
    """
    if isinstance(value, dict):
        return _drop_null_values_dict(value)
    if isinstance(value, list):
        return [_drop_null_values(v) for v in value]
    return value


def _drop_null_values_dict(section):
    """The dict branch of ``_drop_null_values``, split out to keep both functions simple."""
    kept = {}
    for key, sub in section.items():
        if sub is None:
            continue
        sub = _drop_null_values(sub)
        if _is_empty_container(sub):
            continue
        kept[key] = sub
    return kept


def _is_empty_container(value):
    """Whether ``value`` is a dict/list ``_drop_null_values`` emptied out, for the default --show-config (minimal)."""
    return isinstance(value, (dict, list)) and not value


def _drop_filtered_sections(resolved, stage_ids):
    """Drop the section of every stage --until/--skip filtered out, so the output matches that run."""
    for dropped in set(_declared_stage_ids(resolved)) - set(stage_ids):
        resolved.pop(dropped, None)


def _generic_top_level_keys(resolved, stage_ids, pinned_keys):
    """Every top-level key that is neither pinned, nor 'stages:', nor a stage's own section -- alphabetically."""
    skip = {"stages", *pinned_keys, *stage_ids}
    return sorted(k for k in resolved if k not in skip)


def _ordered_config(resolved, stage_ids):
    """The resolved config arranged into --show-config's four display groups (see show_config)."""
    pinned_keys = ("version", "denver-version")
    ordered = {key: _sorted_nested(resolved[key]) for key in pinned_keys if key in resolved}
    ordered.update((k, _sorted_nested(resolved[k])) for k in _generic_top_level_keys(resolved, stage_ids, pinned_keys))
    ordered["stages"] = resolved["stages"]
    for stage_id in stage_ids:
        ordered[stage_id] = _ordered_stage_section(resolved[stage_id])
    return ordered


# --------------------------------------------------------------------------- #
# denver.toml-declared CLI arguments ('denver-custom-args:')
#
# An env may declare flags of its own: each 'denver-custom-args:' entry is one
# ``parser.add_argument(*flags, **kwargs)`` call, so an env offering a
# per-run knob ("which board?", "release or debug?") gets a real flag that
# `denver run <env> --help` lists, instead of asking its users for a generic
# `-c some.dotted.path=value`. What the user then passes is exported as
# DENVER_ARG_<DEST> (see cli_arg_env), i.e. it reaches the denver.toml's own
# ${...} interpolation, every hook, every stage and the final command
# through the one mechanism all of those already read.
# --------------------------------------------------------------------------- #
# Every 'denver-custom-args:' entry's parsed value is exported under this prefix:
# '--target' -> DENVER_ARG_TARGET.
ARG_ENV_PREFIX = "DENVER_ARG_"


def add_config_args(parser, entries):
    """Add every denver.toml 'denver-custom-args:' entry to ``parser`` as an ordinary argparse flag.

    ``entries`` is the raw 'denver-custom-args:' value (None when the env declares none).
    Each entry is a mapping: 'flags:' names the flag(s), everything else is
    forwarded verbatim as an ``add_argument`` keyword argument -- so an env
    gets argparse's full vocabulary ('help', 'default', 'action', 'nargs',
    'choices', 'required', 'metavar', 'dest', ...) without denver having to
    re-invent, or gate, any of it.
    """
    if entries is None:
        return
    if not isinstance(entries, list):
        die(f"denver.toml: 'denver-custom-args:' must be a list of argument definitions, got {entries!r}")
    # every dest already spoken for: denver's own flags first (so an entry
    # can never quietly overwrite args.force et al.), then each entry added
    # here, so two entries cannot silently collide with each other either.
    taken = {action.dest for action in parser._actions}
    for entry in entries:
        _add_config_arg(parser, entry, taken)


def _add_config_arg(parser, entry, taken):
    """Add one 'denver-custom-args:' entry, refusing a dest that is already spoken for."""
    if not isinstance(entry, dict):
        die(
            f"denver.toml 'denver-custom-args:': every entry must be a mapping of add_argument arguments, got {entry!r}"
        )
    flags = config_arg_flags(entry)
    kwargs = {key: value for key, value in entry.items() if key != "flags"}
    _reject_type_key(flags, kwargs)
    dest = config_arg_dest(flags, entry)
    if dest in taken:
        die(
            f"denver.toml 'denver-custom-args:': {', '.join(flags)} resolves to '{dest}', which denver's own arguments "
            f"already use -- rename the flag or give the entry a different 'dest:'."
        )
    _add_argument(parser, flags, kwargs)
    taken.add(dest)


def config_arg_flags(entry):
    """One entry's flag(s) as a list: ``flags: --target`` and ``flags: [-t, --target]`` both work.

    Only option flags, never a positional: denver's own <env> is the one
    positional argument, and a second one would silently compete with it for
    whatever the user typed.
    """
    flags = entry.get("flags")
    if isinstance(flags, str):
        flags = [flags]
    if not isinstance(flags, list) or not flags:
        die(f"denver.toml 'denver-custom-args:': entry {entry!r} needs 'flags:' -- a flag string, or a list of them")
    for flag in flags:
        _validate_flag(flag, entry)
    return flags


def _validate_flag(flag, entry):
    """Die unless one 'flags:' element is a string starting with '-'."""
    if not isinstance(flag, str) or not flag.startswith("-"):
        die(
            f"denver.toml 'denver-custom-args:': flag {flag!r} in entry {entry!r} must be a string "
            f"starting with '-' -- an env cannot declare a positional argument (denver's own <env> is the only one)."
        )


def _reject_type_key(flags, kwargs):
    """Die on 'type:', which a denver.toml cannot express.

    argparse's ``type=`` is a *callable*, and TOML cannot express one -- a config file only ever hands
    over a string/number/bool/list/mapping. Accepting one anyway would mean denver
    picking which conversions exist, or importing whatever a config names. Neither is needed: every
    value arrives as a string anyway (an environment variable is a string), and 'choices:'/'action:'
    cover the cases a type would have.
    """
    if "type" in kwargs:
        die(
            f"denver.toml 'denver-custom-args:': {', '.join(flags)} sets 'type:', which denver.toml cannot express "
            f"(argparse needs a callable) -- values are always strings; use 'choices:' or 'action:' instead."
        )


def _add_argument(parser, flags, kwargs):
    """``parser.add_argument(*flags, **kwargs)``, reporting argparse's own complaints as denver errors."""
    try:
        parser.add_argument(*flags, **kwargs)
    except (argparse.ArgumentError, TypeError, ValueError) as exc:
        die(f"denver.toml 'denver-custom-args:': cannot add {', '.join(flags)} -- {exc}")


def config_arg_dest(flags, entry):
    """The attribute argparse stores this entry under: its own 'dest:', else the first long flag.

    Mirrors argparse's own rule, because denver needs the name *before*
    add_argument runs (to reject a collision with its own flags) and after
    parsing (to name the DENVER_ARG_<DEST> variable).
    """
    if "dest" in entry:
        return entry["dest"]
    long_flags = [flag for flag in flags if flag.startswith("--")]
    return (long_flags or flags)[0].lstrip("-").replace("-", "_")


def cli_arg_env(entries, args):
    """``{DENVER_ARG_<DEST>: text}`` for every 'denver-custom-args:' entry this invocation has a value for."""
    env = {}
    for entry in entries or []:
        dest = config_arg_dest(config_arg_flags(entry), entry)
        text = _arg_value_text(getattr(args, dest, None))
        if text is not None:
            env[f"{ARG_ENV_PREFIX}{dest.upper()}"] = text
    return env


def _arg_value_text(value):
    """One parsed value as environment-variable text, or None when there is nothing to export.

    A value of None (an entry with no 'default:', whose flag wasn't given)
    deliberately exports *nothing* rather than an empty string, so
    ``${DENVER_ARG_X:-fallback}`` still takes its fallback -- interpolation
    treats a set-but-empty variable as set (see context.interpolate).

    An environment variable is a string, so everything else becomes one:
    'action: store_true'/'store_false' as "1"/"0" (what a shell's `[ "$X" =
    1 ]` and a compose file both expect), a multi-value 'nargs:'/'action:
    append' as its space-joined items.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (list, tuple)):
        return " ".join(map(str, value))
    return str(value)


class CliArgs:
    """The env's own 'denver-custom-args:' as one invocation resolved them.

    Two views of the same thing, both needed: ``env`` is what the
    environment gets to see (DENVER_ARG_<DEST> for every entry with a
    value), while ``argv`` is the user's own tokens, kept verbatim so a
    wrapper reinvocation can re-pass them (see reinvoke_command) -- the
    inner denver re-reads the same denver.toml and would otherwise fall back
    to each entry's 'default:'.
    """

    def __init__(self, env=None, argv=()):
        """Hold one invocation's 'denver-custom-args:' values: ``env`` to export, ``argv`` to re-pass."""
        self.env = dict(env or {})
        self.argv = list(argv)


def _cli_args(cli_args):
    """``cli_args``, or an empty CliArgs -- so every caller can just read .env/.argv.

    An env declaring no 'denver-custom-args:' at all, and every caller that predates them
    (a provider driven directly, a test), both land here.
    """
    return cli_args or CliArgs()


# --------------------------------------------------------------------------- #
# Removing an env's state ('run --clean' and 'denver clean')
# --------------------------------------------------------------------------- #
def clean_env(env_dir, config_path, *, dry_run=False):
    """Remove denver's own state for one env, and report what went.

    Everything denver writes for an env lives under one directory (see
    denver_providers.context.state_dir_for): every venv, conan's install
    tree, unpacked downloads, the logs, the fingerprints that decide what
    can be skipped, the run lock. So removing that directory is the whole
    job -- the next run rebuilds from the config alone, which is the point
    of an environment being code.

    What is deliberately *not* touched: anything the env's own config points
    somewhere else, e.g. ``CONAN_HOME = "${DENVER_ENV_DIR}/.conan2"``. That
    is a location the project chose for a tool's own cache, not denver's
    state, and denver has no business deciding it is disposable.

    The conventional ``<env>/.denver`` parent goes too, once nothing but the
    .gitignore denver itself wrote is left inside it -- so a single-config
    env comes back to exactly the files its author checked in. A parent
    still holding another config's state (``denver.debug.toml``'s, say) is
    kept, along with that state.

    Reads no config *values*: where the state dir sits depends only on <env>
    and which config file was picked, so this is the same directory a run
    would have used, whatever that run's config happens to say.
    """
    from denver_providers.context import state_dir_for

    state_dir = state_dir_for(env_dir, config_path)
    if not _remove_state_dir(state_dir, env_dir, dry_run=dry_run):
        info(f"clean: nothing to remove -- no denver state at {state_dir}")


def clean_env_and_imports(config_path, *, dry_run=False, all_dirs=False, assume_yes=False):
    """'denver clean <env>': remove every directory denver keeps for <env> and for the envs it imports.

    Every place state can live is removed, not only the one a run would pick
    right now: an explicit ``DENVER_ENV_WORKDIR`` (if currently set), or the
    workdir inside the env (``<env>/.denver/<config stem>``). An env built
    under both across different runs (workdir override set for one run, not
    another) has state in each, and state left in the other would quietly
    survive every clean.

    An imported env is cleaned too: it is built as part of building whatever
    imports it, so leaving its state behind leaves the env half-built.

    ``all_dirs`` (``--all``) additionally removes denver's shared cache root
    (``DENVER_CACHE_DIR``) -- unlike everything else here, that directory is
    not this env's own, so it is only ever touched when asked for explicitly.

    Outside ``--dry-run``, each directory is shown and confirmed on its own
    (``assume_yes`` -- ``-y``/``--yes`` -- standing in for every one of those
    confirmations) rather than once for the whole batch: declining one
    directory keeps it and moves on to the next, it does not abort the rest.
    A state dir's spent ``<env>/.denver`` parent (see _state_dir_plan) is the
    one exception -- declining the state dir keeps its parent too, without
    asking, since removing the parent would take the declined state dir with
    it.
    """
    chains = _removal_chains(config_path, all_dirs=all_dirs)
    if not chains:
        info("clean: nothing to remove -- denver keeps no state for this env")
        return
    removed_any = _remove_chains(chains, dry_run=dry_run, assume_yes=assume_yes)
    if not dry_run and not removed_any:
        info("clean: nothing removed")


def _removal_chains(config_path, *, all_dirs):
    """Every directory 'denver clean' would consider removing for <config_path> and its imports, plus the shared cache dir if ``all_dirs``.

    Grouped into dependent chains (see _state_dir_plan) -- a chain's later
    entries only make sense once its earlier ones are actually gone.
    """
    chains = []
    for path in [Path(config_path), *_import_chain(config_path)]:
        chains += _state_dir_chains_for(path)
    if all_dirs:
        chains += _cache_dir_chain()
    return chains


def _state_dir_chains_for(config_path):
    """Every non-empty removal chain for one env's own state -- one per place it may live (see _state_dirs_for)."""
    env_dir = config_path.parent
    plans = (_state_dir_plan(state_dir, env_dir) for state_dir in _state_dirs_for(env_dir, config_path))
    return [chain for chain in plans if chain]


def _cache_dir_chain():
    """``[[<shared cache dir>]]`` if it exists, else ``[]`` -- denver's own contribution to '--all'."""
    cache_dir = _shared_cache_dir()
    return [[cache_dir]] if cache_dir.exists() else []


def _remove_chains(chains, *, dry_run, assume_yes):
    """Remove every chain, confirming each directory on its own outside --dry-run. True if anything was removed.

    Every chain is processed, even once an earlier one has already removed
    something -- unlike ``any(_remove_chain(...) for chain in chains)``,
    which would short-circuit on the first True and leave the rest of
    ``chains`` untouched.
    """
    removed_any = False
    for chain in chains:
        removed_any |= _remove_chain(chain, dry_run=dry_run, assume_yes=assume_yes)
    return removed_any


def _remove_chain(chain, *, dry_run, assume_yes):
    """Remove one dependent chain of directories (see _state_dir_plan), stopping at the first declined one.

    True if anything in the chain was actually removed -- a state dir
    confirmed and removed still counts even if its parent is then declined.
    Declining a directory keeps it -- and, for a two-entry chain, its
    dependent parent too -- without aborting the rest of 'denver clean'.
    """
    removed_any = False
    for path in chain:
        if not dry_run and not _confirm_removal(path, assume_yes=assume_yes):
            info(f"clean: kept {path}")
            break  # its parent (if any) would only remove it along the way -- kept too
        _remove_tree(path, dry_run=dry_run)
        removed_any = True
    return removed_any


def _confirm_removal(path, *, assume_yes):
    """Ask to confirm removing one directory -- unless ``assume_yes``. True if it should be removed.

    Non-interactive (no tty) and no ``--yes`` dies rather than silently doing
    nothing or silently removing anything -- the same tradeoff
    default_command makes for "no command and can't ask".
    """
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        die("clean: refusing to remove without confirmation in a non-interactive session -- pass -y/--yes")
    return input(f"Remove {path}? [y/N] ").strip().lower() in ("y", "yes")


def _shared_cache_dir():
    """Denver's shared, content-addressed cache root (``DENVER_CACHE_DIR``, default ``~/.cache/denver``).

    Mirrors Context.cache_dir, which is the property every provider actually
    reads; this is the same computation done here with no Context to hand.
    """
    from denver_providers.context import CACHE_DIR_VAR

    configured = os.environ.get(CACHE_DIR_VAR)
    return Path(configured).expanduser() if configured else Path("~/.cache/denver").expanduser()


def _import_chain(config_path):
    """Every denver.toml in ``config_path``'s whole-file 'import:' chain, nearest first.

    Nothing here is fatal: a config that will not parse, or an 'import:'
    entry pointing nowhere, costs only the part of the chain below it and is
    reported as a warning -- an env is worth cleaning exactly when its
    config is broken.
    """
    start = Path(config_path).resolve()
    seen, queue, chain = {start}, [start], []
    while queue:
        layer = [path for path in _readable_imports(queue.pop(0)) if path not in seen]
        seen.update(layer)
        chain += layer
        queue += layer
    return chain


def _readable_imports(config_path):
    """The configs one layer's 'import:' entries name -- warning about, and skipping, whatever cannot be read."""
    try:
        raw = load_config_file(config_path)
    except _CONFIG_READ_ERRORS:
        logger.warning(f"clean: cannot read {config_path} -- the envs it imports are left alone")
        return []

    targets = []
    for entry in raw.get("import", []) or []:
        target = _import_target(entry, config_path.parent)
        if target is None:
            logger.warning(f"clean: import '{entry}' in {config_path.parent} points nowhere -- left alone")
            continue
        targets.append(target)
    return targets


def _state_dirs_for(env_dir, config_path):
    """Every directory denver may keep this env's state in -- an explicit DENVER_ENV_WORKDIR first (if currently set), then the env's own workdir."""
    from denver_providers.context import ENV_WORKDIR_VAR, STATE_DIRNAME

    dirs = []
    workdir_override = os.environ.get(ENV_WORKDIR_VAR)
    if workdir_override:
        dirs.append(Path(workdir_override).expanduser().resolve())
    dirs.append(Path(env_dir) / STATE_DIRNAME / Path(config_path).stem)
    return list(dict.fromkeys(dirs))


def _state_dir_plan(state_dir, env_dir):
    """The paths removing this env's state at ``state_dir`` would delete -- itself, and a '<env>/.denver' left spent around it. [] if there is nothing there.

    Only plans -- nothing is touched here. Used both by _remove_state_dir
    (removes the plan immediately, for 'run --clean') and by
    clean_env_and_imports (collects the plan across every env first, to show
    and confirm once before anything is removed, for 'denver clean').
    """
    from denver_providers.context import STATE_DIRNAME

    if not state_dir.exists():
        return []
    parent = state_dir.parent
    # compared against this env's own path, not merely by name -- the only
    # parent that is ever safe to remove along with its child is the
    # conventional '<env>/.denver' one; an explicit DENVER_ENV_WORKDIR's
    # parent never is, however it's named. Asked before any removal, so
    # --dry-run (and 'denver clean''s own preview) answers it exactly as a
    # real run would.
    in_env_dir = parent == Path(env_dir) / STATE_DIRNAME
    if in_env_dir and _only_denver_leftovers(parent, state_dir):
        return [state_dir, parent]
    return [state_dir]


def _remove_state_dir(state_dir, env_dir, *, dry_run):
    """Remove one state directory, and a '<env>/.denver' left spent around it. False if there was nothing there."""
    plan = _state_dir_plan(state_dir, env_dir)
    for path in plan:
        _remove_tree(path, dry_run=dry_run)
    return bool(plan)


def _only_denver_leftovers(parent, state_dir):
    """Whether ``parent`` holds nothing besides ``state_dir`` and the .gitignore denver wrote into it."""
    return not {entry.name for entry in parent.iterdir()} - {state_dir.name, ".gitignore"}


def _remove_tree(path, *, dry_run):
    """Delete the directory ``path`` -- or, under --dry-run, only say that it would be."""
    if dry_run:
        info(f"clean: would remove {path}")
        return
    shutil.rmtree(path)
    info(f"clean: removed {path}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parsed_env_vars(entries):
    """``{name: value}`` from repeated ``-e``/``--env`` entries, in the order given.

    ``NAME=VALUE`` sets that literal value; a bare ``NAME`` (no ``=``) forwards
    NAME's current value out of denver's own environment (``""`` if it isn't
    set there either) -- the same shorthand ``docker run -e`` offers, for the
    common case of passing something like a secret through unchanged rather
    than retyping it. A later entry for the same name overrides an earlier
    one, same as ``-c``.
    """
    result = {}
    for entry in entries:
        name, sep, value = entry.partition("=")
        if not name:
            die(f"--env: expected NAME=VALUE or NAME, got {entry!r}")
        result[name] = value if sep else os.environ.get(name, "")
    return result


def _add_env_positional(parser):
    """The <env> positional, taken by the 'run' subcommand."""
    parser.add_argument(
        "env",
        nargs="?",
        help="path to an env directory or a denver.yml/denver.yaml/denver.toml file (falls back to $DENVER_ENV_DIR if omitted)",
    )


def _add_config_selection_args(parser):
    """Flags that pick *which config* to act on, for the 'run' subcommand."""
    parser.add_argument(
        "-c",
        "--config",
        action="append",
        default=[],
        metavar="KEY.PATH=VALUE",
        help="override a config value (repeatable)",
    )
    parser.add_argument(
        "-cf", "--config-file", action="append", default=[], metavar="FILE", help="overlay a config file (repeatable)"
    )
    parser.add_argument(
        "-e",
        "--env",
        action="append",
        default=[],
        dest="env_vars",
        metavar="NAME[=VALUE]",
        help="set an environment variable for this run (repeatable): applied to denver's own process, to every "
        "stage, and to a wrapped stage's container too (e.g. docker); 'NAME' alone (no '=') forwards NAME's "
        "current value out of denver's own environment, the way `docker run -e` does",
    )
    parser.add_argument(
        "--until",
        metavar="STAGE",
        help="run the stages up to and including the given one, dropping every stage after it",
    )
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="STAGE",
        help="run every stage except the given one(s) (repeatable)",
    )


def _add_help_flag(parser):
    """A plain store_true -h/--help, not argparse's own exiting action.

    'run' needs this instead of the default add_help=True: its
    --help must reflect the env's own 'denver-custom-args:' once known, which only the
    second, config-aware parse in _run_cli adds -- an exiting help action
    would fire during the first, config-agnostic pass and never see them.
    See _handle_info_flags for where this ends up printed from.
    """
    parser.add_argument("-h", "--help", action="store_true", help="show this help and exit")


class _CleanAction(argparse.Action):
    """'run --clean': '--scripts clean', *plus* removing this env's own state directory.

    Two dests at once, which is why it is an action of its own rather than
    the plain ``append_const`` its ``--setup``/``--login`` siblings use: the
    name goes into ``scripts`` (so it keeps --scripts' ordering and mixes
    with other names the same way), and ``clean_workdir`` records that the
    state directory itself is to go too -- the part a plain ``--scripts
    clean`` deliberately does not do (see _handle_scripts_subcommand).

    The state directory is the *env's* own (its venvs, tool trees,
    downloads, logs) -- see clean_env.
    """

    def __call__(self, parser, namespace, values, option_string=None):  # noqa: ARG002 -- argparse's own Action signature
        """Append 'clean' to the names --scripts collects, and set clean_workdir."""
        namespace.scripts = [*(namespace.scripts or []), "clean"]
        namespace.clean_workdir = True


def _add_run_parser(subparsers, config_args):
    """'denver run <env>': the normal pipeline, or (with --scripts) the 'scripts:' mechanism."""
    run_p = subparsers.add_parser(
        "run",
        add_help=False,
        help="build/enter an env (or run one of its 'scripts:' entries, or just show its resolved config)",
        description="Build/enter <env>, or (with --scripts) run one of its 'scripts:' entries instead, or "
        "(with --show-config) just print its fully resolved denver.toml.",
    )
    _add_help_flag(run_p)
    _add_env_positional(run_p)
    run_p.add_argument(
        "--scripts",
        metavar="NAME",
        nargs="?",
        action="append",
        const=LIST_SCRIPTS,
        help="run each stage's 'scripts: NAME:' entries, then exit (e.g. 'setup', 'login'); repeatable, "
        "each name's entries run in the order given; with no NAME (on any occurrence), list the names "
        "this env defines instead",
    )
    run_p.add_argument(
        "--setup",
        dest="scripts",
        action="append_const",
        const="setup",
        help="shorthand for --scripts setup",
    )
    run_p.add_argument(
        "--login",
        dest="scripts",
        action="append_const",
        const="login",
        help="shorthand for --scripts login",
    )
    run_p.add_argument(
        "--clean",
        nargs=0,
        action=_CleanAction,
        dest="clean_workdir",
        default=False,
        help="run this env's 'scripts: clean:' entries, then remove its state directory (--scripts clean "
        "alone runs only the scripts)",
    )
    run_p.add_argument(
        "--show-config",
        action="store_true",
        help="print the fully resolved (deep-merged) denver.toml and exit, dropping every key left unset "
        "so only keys with a value remain -- see --show-config-full for every key, unset ones included",
    )
    run_p.add_argument(
        "--show-config-full",
        action="store_true",
        help="like --show-config, but keeps every key left unset too (shown as an explicit 'key: null' line, "
        "or a commented-out '# key = null' line with --format toml)",
    )
    run_p.add_argument(
        "--format",
        choices=["yml", "toml"],
        default="yml",
        help="with --show-config/--show-config-full: render the output as this format instead of the default yml",
    )
    run_p.add_argument(
        "-q",
        "--quiet",
        action="count",
        default=0,
        help="suppress denver's own output (repeatable: -q keeps each stage's own command output, -qq "
        "additionally discards that too, so only the final launched command speaks)",
    )
    run_p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show denver's own diagnostic detail, hidden by default: each stage's sub-step banners, "
        "performance timings (in blue), and the '+ cmd' echo of every command run; silenced by -q/-qq "
        "the same as everything else denver prints",
    )
    # --fast and --force ask for opposite things ("don't build anything" vs
    # "rebuild everything"), and --fast wins by construction: every provider
    # takes its --fast path before ctx.force is ever read, silently
    # discarding the --force. Rejected here rather than resolved, because
    # either resolution would be a guess about which one the user meant.
    # A group (not a hand-written check) so argparse reports it itself, the
    # same way it reports every other malformed invocation.
    rebuild = run_p.add_mutually_exclusive_group()
    rebuild.add_argument("--fast", action="store_true", help="only activate what's already built; never (re-)build it")
    rebuild.add_argument(
        "--force",
        action="store_true",
        help="force expensive recomputation (recreate venv, rerun west update, ...), bypassing every "
        "checksum/skip-on-success/skip-on-failure-based skip",
    )
    run_p.add_argument(
        "--ci", action="store_true", help="CI mode: swap in narrower/faster args (e.g. a shallow `west update`)"
    )
    run_p.add_argument(
        "--no-wait",
        action="store_true",
        help="fail instead of waiting when another denver run already holds this env",
    )
    run_p.add_argument(
        "--dry-run",
        action="store_true",
        help="show what each stage would run instead of running it: no command is executed for its effect, "
        "nothing is written, and the final command is printed rather than launched (read-only queries and "
        "sourced scripts do still run -- they are what the shown commands are derived from)",
    )
    run_p.add_argument(
        "--export-env",
        metavar="FILE",
        default=None,
        help="write the built env as shell-sourceable 'export KEY=VALUE' lines to FILE, right before "
        "launching the final command (e.g. for a container's interactive shells to pick up via "
        "/etc/bash.bashrc)",
    )
    _add_config_selection_args(run_p)
    # internal-only: how reinvoke_command() carries run_stages()'s own
    # startup clock across a wrapper reinvocation, for the "env started in
    # Ns" line -- never meant to be typed by a user, hence SUPPRESS instead
    # of a real --help entry.
    run_p.add_argument("--start-time", type=float, default=None, help=argparse.SUPPRESS)
    add_config_args(run_p, config_args)
    return run_p


def _add_complete_parser(subparsers):
    """'denver complete [bash|fish|zsh]': print that shell's completion script for the installed 'denver' command.

    The shell name is optional -- omitted, it's auto-detected from the
    parent process (see _detect_shell); only worth spelling out explicitly
    if that guess is ever wrong (e.g. under tmux/su/some login-shell chains).
    """
    complete_p = subparsers.add_parser(
        "complete",
        help="print a completion script for the installed 'denver' command",
        description="Print a completion script for the 'denver' command, for the given shell (or the "
        "current one, auto-detected, if omitted); wire it up with e.g. eval \"$(denver complete)\" in "
        "your shell rc file (fish: denver complete | source).",
    )
    complete_p.add_argument(
        "shell",
        nargs="?",
        choices=sorted(_COMPLETION_SHELLS),
        default=None,
        help="bash, zsh, or fish -- auto-detected from the parent process if omitted",
    )
    return complete_p


def _add_clean_parser(subparsers):
    """'denver clean <env>': remove the directories denver keeps for that env and the ones it imports."""
    clean_p = subparsers.add_parser(
        "clean",
        help="remove the directories denver keeps for an env and the envs it imports",
        description="Remove denver's workdir and state dir for <env> and for every env it imports -- venvs, "
        "installed tool trees, downloads, logs and the fingerprints deciding what can be skipped. The next "
        "`denver run` rebuilds all of it from the config. Anything the env's own config points elsewhere "
        "(e.g. a CONAN_HOME under the env dir) is left alone. Shows and asks to confirm each directory before "
        "removing it, unless -y/--yes is given; declining one keeps it and moves on to the next.",
    )
    _add_env_positional(clean_p)
    clean_p.add_argument("--dry-run", action="store_true", help="only print what would be removed, and remove nothing")
    clean_p.add_argument("-y", "--yes", action="store_true", help="remove without asking for confirmation")
    clean_p.add_argument(
        "--all",
        action="store_true",
        help="also remove denver's shared cache dir (DENVER_CACHE_DIR, default ~/.cache/denver) -- shared by "
        "every env and checkout, so this clears them all too",
    )
    return clean_p


def build_arg_parser(config_args=None):
    """The argparse.ArgumentParser for every denver-own flag (not the forwarded command).

    A fresh instance per call (see main()), so its 'append' actions' default
    lists are never shared/mutated across repeated main() calls in the same
    process (as happens across denver.main() calls in the test suite).

    denver's own CLI is subcommand-based ('run'/'clean'/'complete'): every
    ``-h``/``--help`` in it, top-level or per-subcommand, is a plain
    store_true rather than argparse's own exiting action (see
    _add_help_flag), so it survives both the first, config-agnostic parse
    and the second, config-aware one -- only the second run's --help can
    show 'run''s own env-declared 'denver-custom-args:' alongside denver's own, and only
    if help exits immediately does that never happen (see _handle_info_flags,
    which prints the right (sub)parser's own format_help() based on
    ``args.subcommand``). 'complete' has no config args of its own to wait
    for, so it keeps argparse's normal ``add_help=True``. Every other flag is
    a normal argparse action, so unknown flags, a missing required value,
    etc. are all argparse's own problem to report (its usual `usage: ...` +
    `error: ...` on stderr, exit code 2).

    ``config_args`` is the env's own 'denver-custom-args:' (see add_config_args), added
    last to 'run' so those flags are indistinguishable from denver's own
    everywhere after this -- in --help, in the parse, in the error messages.
    Omitted (None) wherever the env isn't known yet, which is why parsing is
    a two pass affair: the flags an env declares live in the file the <env>
    argument names (see _run_cli).
    """
    parser = _DenverArgParser(prog="denver", add_help=False)
    parser.add_argument("-h", "--help", action="store_true", help="show this help and exit")
    parser.add_argument("--version", action="store_true", help="show the installed denver version and exit")
    parser.add_argument("--license", action="store_true", help="show denver's LICENSE (Apache-2.0) and exit")
    subparsers = parser.add_subparsers(dest="subcommand")
    run_p = _add_run_parser(subparsers, config_args)
    clean_p = _add_clean_parser(subparsers)
    complete_p = _add_complete_parser(subparsers)
    # Looked up by _handle_info_flags to print the *invoked* subcommand's own
    # help (this parser instance's, config_args and all) rather than the
    # top-level summary -- see the docstring above.
    parser.subcommand_parsers = {"run": run_p, "clean": clean_p, "complete": complete_p}
    return parser


class _DenverArgParser(argparse.ArgumentParser):
    """argparse.ArgumentParser, plus a name -> subparser lookup -- see build_arg_parser/_handle_info_flags.

    A subclass (declaring the attribute) rather than just assigning it onto
    a plain ArgumentParser dynamically, so pyright/mypy see it as real,
    typed state instead of an unknown attribute.
    """

    subcommand_parsers: dict[str, argparse.ArgumentParser]


#: The one closing pointer print_help() adds after argparse's own summary --
#: deliberately not the module's own (necessarily longer) docstring, see
#: print_help.
_HELP_FOOTER = "Run `denver run --help` to see more details about the run-specific flags."


def print_help(parser):
    """Print the logo, argparse's own usage/options summary, then one pointer sentence -- nothing else.

    Shown identically for a bare no-args invocation and an explicit
    -h/--help (see main()). argparse's summary is the actual flag-by-flag
    reference; `denver run --help` has run's own, and README.md/the doc
    site has the full behavioural detail -- this print stays that short
    rather than also dumping the module's own (necessarily longer)
    docstring.
    """
    print_logo()
    print(parser.format_help())
    print(_HELP_FOOTER)


def _command_failure_message(exc):
    """Render a CalledProcessError as denver's own error text, including whatever output the call captured.

    A ``capture=True`` call (Context.run) holds the failing command's own
    stdout/stderr in the exception rather than having printed it, so it is
    appended here -- that message is usually the only thing that explains
    the failure at all. A call that inherited stdout/stderr carries nothing
    here and needs nothing: it already printed where the user could see it.
    """
    lines = [f"command failed (exit {exc.returncode}): {_failed_command_text(exc.cmd)}"]
    lines += _captured_output(exc)
    return "\n".join(lines)


def _failed_command_text(cmd):
    """The failing command as one string, whether subprocess held it as a list or as a string.

    shlex.join, not a bare ' '.join -- an argument with its own spaces/quotes
    (e.g. ["bash", "-c", 'echo "a $b"'], the shape every custom 'cmd:' takes)
    would otherwise print as if it were several separate words, same
    reasoning as context.py's _printable.
    """
    if isinstance(cmd, (list, tuple)):
        return shlex.join(str(c) for c in cmd)
    return str(cmd)


def _stream_text(stream):
    """One captured stream as text ("" when the command captured nothing there)."""
    if isinstance(stream, bytes):
        return stream.decode(errors="replace")
    return str(stream or "")


def _captured_output(exc):
    """Whatever the failing command printed, for a call that captured it instead of showing it."""
    texts = []
    for stream in (exc.stdout, exc.stderr):
        text = _stream_text(stream)
        if text.strip():
            texts.append(text.rstrip())
    return texts


def main(argv=None):
    """Entry point: run the CLI, reporting any subprocess failure no provider handled itself.

    Providers run plenty of subprocesses (Context.run, ``check=True`` by
    default) and any of them may fail. Without this, such a failure reaches
    the user as a raw Python traceback whose frames say nothing about the
    actual problem -- and, for a ``capture=True`` call, without even the
    failing command's own message. Both are recovered by
    ``_command_failure_message``.

    This is the last line of defence, not the only one: a provider that can
    say something *more specific* about its own failing command should
    still catch it and ``die()`` with that (see
    ConanProvider._ensure_profile), and everything else lands here.
    """
    try:
        _run_cli(argv)
    except subprocess.CalledProcessError as exc:
        die(_command_failure_message(exc))
    except ConfigReadError as exc:
        die(str(exc))
    return 0


def _complete_candidates(words):
    """Completion candidates for the (possibly partial, possibly empty) trailing word of ``words``.

    ``words`` are the argv tokens typed after 'denver' up to and including
    the word currently being completed (see _completion_script's shell
    functions -- bash's forwards ``${COMP_WORDS[@]:1:COMP_CWORD}``,
    zsh's and fish's the equivalent slice in their own words). Bare-except
    on purpose (even SystemExit, hence not just ``Exception``): a completion
    request must never dump a traceback or die() message into the user's
    terminal mid-keystroke, and returning fewer candidates than ideal is a
    fine failure mode here.
    """
    try:
        return _complete_candidates_unsafe(words)
    except BaseException:  # NOSONAR -- deliberately broad, see this function's own docstring
        return []


_TOP_LEVEL_COMPLETIONS = ["run", "clean", "complete", "--help", "--version", "--license"]
_CLEAN_FLAGS = ["--dry-run", "-y", "--yes", "--all", "-h", "--help"]
_RUN_FLAGS = [
    "--scripts",
    "--setup",
    "--login",
    "--clean",
    "--show-config",
    "--show-config-full",
    "--format",
    "--fast",
    "--force",
    "--ci",
    "--no-wait",
    "--dry-run",
    "-q",
    "--quiet",
    "-v",
    "--verbose",
    "-c",
    "--config",
    "-cf",
    "--config-file",
    "-e",
    "--env",
    "--until",
    "--skip",
    "--export-env",
    "-h",
    "--help",
]
_VALUE_FLAGS = {
    "-c",
    "--config",
    "-cf",
    "--config-file",
    "-e",
    "--env",
    "--until",
    "--skip",
    "--scripts",
    "--export-env",
    "--format",
}
_PATH_VALUE_FLAGS = ("-c", "--config", "-cf", "--config-file")  # see _pending_flag_value_candidates


def _matching(candidates, cur):
    """Every one of ``candidates`` that starts with ``cur`` -- the shape every completion list here takes."""
    return [c for c in candidates if c.startswith(cur)]


def _complete_candidates_unsafe(words):
    if not words:
        return _TOP_LEVEL_COMPLETIONS
    cur = words[-1]
    prior = words[:-1]
    if not prior:
        return _matching(_TOP_LEVEL_COMPLETIONS, cur)

    subcommand = prior[0]
    if subcommand == "complete":
        return _complete_shell_names(prior, cur)
    if subcommand == "clean":
        return _complete_clean_candidates(prior[1:], cur)
    if subcommand != "run":
        return []  # anything unrecognised takes no further args
    return _complete_run_candidates(prior[1:], cur)


def _complete_clean_candidates(rest, cur):
    """Completions for 'denver clean ...<TAB>' -- the <env> path first, then clean's own flags.

    No flag of 'clean' takes a value, so no token in ``rest`` can have been
    consumed as one and the first non-flag token is the <env> positional.
    """
    env_value = _first_positional(rest, [False] * len(rest))
    if env_value is None and not cur.startswith("-"):
        return _completion_path_candidates(cur)
    return _matching(_CLEAN_FLAGS, cur)


def _complete_shell_names(prior, cur):
    """Completions for 'denver complete <TAB>' -- the shell names, or [] once one's already given."""
    if len(prior) > 1:
        return []  # 'complete' takes at most one positional (the shell name)
    return _matching(sorted(_COMPLETION_SHELLS), cur)


def _complete_run_candidates(rest, cur):
    """Completions for 'denver run ...<TAB>' -- everything past the subcommand word itself."""
    if "--" in rest:
        return []  # past the forwarded-command boundary: denver has no opinion on it

    env_value, pending_flag = _run_completion_state(rest)
    flag_value_candidates = _pending_flag_value_candidates(pending_flag, env_value, cur)
    if flag_value_candidates is not None:
        return flag_value_candidates
    return _complete_env_or_flag(env_value, cur)


def _complete_env_or_flag(env_value, cur):
    """<env> path completions, or denver's own (and this env's declared) run flags, depending on ``cur``.

    Once <env> is resolved, a flag is the only thing that can come next -- so flags
    are offered there even before ``cur`` starts with '-' (an empty ``cur`` then
    matches all of them), rather than only once the user's typed the dash themselves.
    """
    if env_value is None and not cur.startswith("-"):
        return _completion_path_candidates(cur)
    return _matching(_RUN_FLAGS + _completion_declared_flags(env_value), cur)


def _run_completion_state(rest):
    """(env_value, pending_flag) so far in 'rest' -- see _complete_run_candidates.

    Walks 'rest' the same way argparse would: each _VALUE_FLAGS token
    consumes the very next token as its own value (whatever that token
    looks like), so a token only counts as the <env> positional if nothing
    earlier swallowed it. 'pending_flag' is set only when the walk ends
    mid-flag -- i.e. rest's own last token is a value flag with nothing
    after it yet for ``cur`` to be.
    """
    consumed_as_value = [False] * len(rest)
    for i in range(1, len(rest)):
        consumed_as_value[i] = not consumed_as_value[i - 1] and rest[i - 1] in _VALUE_FLAGS

    env_value = _first_positional(rest, consumed_as_value)
    is_open_value_flag = rest and not consumed_as_value[-1] and rest[-1] in _VALUE_FLAGS
    pending_flag = rest[-1] if is_open_value_flag else None
    return env_value, pending_flag


def _first_positional(rest, consumed_as_value):
    """The first token in 'rest' that's neither a flag nor already consumed as one's value, or None."""
    # zip(strict=True) is Python 3.10+ only (denver's floor is 3.9); plain zip() is fine
    # here since consumed_as_value is built as [False] * len(rest) right above, in the caller.
    for tok, consumed in zip(rest, consumed_as_value):
        if not consumed and not tok.startswith("-"):
            return tok
    return None


def _pending_flag_value_candidates(pending_flag, env_value, cur):
    """Completions for the value of a flag still awaiting one, or None if 'rest' isn't mid-flag at all.

    None means "no pending flag" -- the caller falls through to <env>/flag
    completion instead; every other return (including []) is final.
    """
    if pending_flag in ("--until", "--skip"):
        return _matching(_completion_stage_ids(env_value), cur)
    if pending_flag == "--scripts":
        return _matching(_completion_script_names(env_value), cur)
    if pending_flag in ("-e", "--env"):
        return _matching(list(os.environ), cur)
    if pending_flag == "--format":
        return _matching(["yml", "toml"], cur)
    if pending_flag is not None:
        return []  # _PATH_VALUE_FLAGS: no sensible dynamic completion; shell filename fallback takes over
    return None


def _completion_env_paths(env_value):
    """(env_dir, config_path) for completion purposes, or (None, None) if ``env_value`` isn't a real env yet."""
    if not env_value:
        return None, None
    candidate = Path(env_value).expanduser()
    if not candidate.exists():
        return None, None
    if candidate.is_file():
        return candidate.parent, candidate
    return candidate, _config_file_in_dir(candidate)


def _completion_config(env_value):
    """This env's raw, whole-file-import-merged config, or None -- best-effort, for completion only."""
    env_dir, config_path = _completion_env_paths(env_value)
    if env_dir is None or config_path is None or not config_path.is_file():
        return None
    config = load_config(config_path)
    config, _ = expand_section_imports(config, env_dir)
    return config


def _completion_stage_ids(env_value):
    """This env's declared stage ids, for completing --until/--skip -- [] if unknown/unreadable."""
    config = _completion_config(env_value)
    stages = (config or {}).get("stages") or []
    return [s for s in stages if isinstance(s, str)]


def _completion_script_names(env_value):
    """This env's 'scripts:' names, for completing --scripts -- [] if unknown/unreadable."""
    config = _completion_config(env_value)
    if config is None:
        return []
    stage_ids = _completion_stage_ids(env_value)
    return sorted(_scripts_by_name(config, stage_ids))


def _completion_declared_flags(env_value):
    """This env's own 'denver-custom-args:'-declared flag spellings, for completing them alongside denver's own.

    [] if none.
    """
    config = _completion_config(env_value)
    entries = (config or {}).get("denver-custom-args")
    if not isinstance(entries, list):
        return []
    flags = []
    for entry in entries:
        flags += _entry_flag_spellings(entry)
    return flags


def _entry_flag_spellings(entry):
    """One 'denver-custom-args:' entry's own 'flags:' spelling(s), normalised to a list of strings.

    [] if malformed.
    """
    if not isinstance(entry, dict):
        return []
    return _flags_value_as_list(entry.get("flags"))


def _flags_value_as_list(raw):
    """A 'flags:' value normalised to a list of strings -- a bare string becomes a one-item list."""
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [f for f in raw if isinstance(f, str)]
    return []


# ---- descriptions ('name\thelp text') for fish/zsh, which both show them in their own pager --- #
# bash's plain COMPREPLY has no equivalent convention (a literal tab would land in the command
# line itself, not a pager), so this is fish/zsh-only: see _complete_candidates_described and
# _completion_script_fish/_completion_script_zsh's own '__complete --describe' invocations.

_SHELL_HELP = {
    "bash": "the Bourne Again Shell",
    "fish": "the friendly interactive shell",
    "zsh": "the Z shell",
}


def _action_help_by_flag(parser):
    """{flag spelling: help text} for every argparse.Action on ``parser`` that has a real (non-SUPPRESS) one.

    Reads straight off the actual argparse actions -- the same objects
    ``--help`` itself formats from -- so a flag's completion description can
    never drift out of sync with its ``--help`` wording the way a
    hand-copied second string would.
    """
    return {
        flag: action.help for action in parser._actions for flag in action.option_strings if _real_help(action.help)
    }


def _real_help(text):
    """Whether ``text`` (an argparse action's own .help) is real help, not None/'' /argparse.SUPPRESS."""
    return bool(text) and text != argparse.SUPPRESS


def _top_level_help():
    """{candidate: help text} for _TOP_LEVEL_COMPLETIONS, all sourced from build_arg_parser() (see _action_help_by_flag).

    'run'/'complete's own one-line blurbs live on the hidden pseudo-actions
    argparse's own add_subparsers()/add_parser(help=...) machinery creates
    for the top-level --help listing (parser._subparsers' one _SubParsersAction,
    its own ._choices_actions) -- there's no public accessor for those, but
    build_arg_parser already pokes at ._actions directly elsewhere (see
    add_config_args), so this follows the same established pattern.
    """
    parser = build_arg_parser()
    help_by_name = _action_help_by_flag(parser)
    sub_action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for pseudo in sub_action._choices_actions:
        if pseudo.help:
            help_by_name[pseudo.dest] = pseudo.help
    return help_by_name


def _run_flag_help():
    """{flag: help text} for _RUN_FLAGS -- denver's own 'run' flags, sourced from build_arg_parser().

    config_args=None, so this never loads or validates any env's own denver.toml -- see this function's
    caller, _completion_description_lookup, for where an env's own declared flags get theirs instead.
    """
    run_p = build_arg_parser().subcommand_parsers["run"]
    return _action_help_by_flag(run_p)


def _completion_declared_flag_help(env_value):
    """{flag: help text} for this env's own 'denver-custom-args:' entries that set a 'help:' -- {} if none/unreadable.

    Reads the raw 'help:' key straight off each entry, deliberately without
    add_config_args' own validation (a dest collision, an unknown kwarg, ...)
    -- same best-effort spirit as _completion_declared_flags itself: a
    malformed denver.toml must degrade a completion request, never die() it.
    """
    config = _completion_config(env_value)
    entries = (config or {}).get("denver-custom-args")
    if not isinstance(entries, list):
        return {}
    help_by_flag = {}
    for entry in entries:
        help_by_flag.update(_entry_flag_help(entry))
    return help_by_flag


def _entry_flag_help(entry):
    """One 'denver-custom-args:' entry's own {flag: help text}, from its 'help:' -- {} if malformed/missing.

    See _completion_declared_flag_help, the only caller.
    """
    if not isinstance(entry, dict):
        return {}
    help_text = entry.get("help")
    if not isinstance(help_text, str):
        return {}
    return dict.fromkeys(_entry_flag_spellings(entry), help_text)


def _completion_description_lookup(words):
    """{candidate: help text} for whichever completion context 'words' is in right now -- best-effort.

    Covers only the fixed, self-describing vocabularies (denver's own subcommands/flags, 'complete's
    shell names, an env's declared flags). {} wherever the context has no such vocabulary (an <env> path,
    a flag's own pending value, a stage id, ...), so _describe leaves those candidates undecorated rather
    than guessing. Mirrors _complete_candidates_unsafe's own branching, but isn't called from there: bash
    uses plain _complete_candidates, unaffected by any of this.
    """
    if not words:
        return _top_level_help()
    prior = words[:-1]
    if not prior:
        return _top_level_help()
    subcommand = prior[0]
    if subcommand == "complete":
        return _complete_shell_names_help(prior)
    if subcommand == "clean":
        return _action_help_by_flag(build_arg_parser().subcommand_parsers["clean"])
    if subcommand == "run":
        return _run_description_lookup(prior[1:])
    return {}


def _complete_shell_names_help(prior):
    """{shell: blurb} for 'complete <TAB>' -- {} once a shell's already given. See _complete_shell_names."""
    return {} if len(prior) > 1 else _SHELL_HELP


def _run_description_lookup(rest):
    """{flag: help text} for 'run ...<TAB>' -- see _completion_description_lookup, the only caller."""
    if "--" in rest:
        return {}
    env_value, pending_flag = _run_completion_state(rest)
    if pending_flag is not None:
        return {}
    return {**_run_flag_help(), **_completion_declared_flag_help(env_value)}


def _describe(candidate, lookup):
    r"""One fish completion line for ``candidate``: 'candidate\tdescription' if ``lookup`` has one.

    Just ``candidate`` bare otherwise -- fish's 'complete -a' treats the part after a tab as the
    description column, and shows a candidate with none exactly the way it always has.
    """
    description = lookup.get(candidate)
    return f"{candidate}\t{description}" if description else candidate


def _complete_candidates_described(words):
    """_complete_candidates(words), each candidate with a tab-separated help blurb where one's known.

    See _completion_script_fish's '__complete --describe' invocation, the only caller.

    The lookup itself (unlike _complete_candidates) isn't wrapped in
    _complete_candidates_unsafe's own try/except -- so it gets one of its
    own here, on the same "never dump a traceback mid-keystroke" grounds:
    falling back to ``candidates`` themselves (already safe) rather than [].
    """
    candidates = _complete_candidates(words)
    try:
        lookup = _completion_description_lookup(words)
    except BaseException:  # NOSONAR -- deliberately broad, see this function's own docstring
        return candidates
    return [_describe(c, lookup) for c in candidates]


def _completion_path_candidates(cur):
    """Directory/denver.toml completions for the <env> positional, honouring any 'dir/' prefix already in ``cur``."""
    base, prefix = _completion_base_and_prefix(cur)
    names = [name for name in sorted(_listdir_or_empty(base)) if name.startswith(prefix)]
    candidates = [_completion_path_candidate(base, name) for name in names]
    return [c for c in candidates if c is not None]


def _completion_base_and_prefix(cur):
    """Split ``cur`` into (dir to list, name prefix to filter by) -- see _completion_path_candidates.

    A manual rpartition, not Path(cur).parent/.name: pathlib collapses a
    trailing '/' (Path("foo/") == Path("foo")), which would wrongly turn
    "already typed a whole dir, tab for its contents" (cur == "foo/") into
    "list '.' for names starting with 'foo'" instead of "list 'foo/'
    itself, with no prefix filter".
    """
    if "/" not in cur:
        return ".", cur
    base, _, prefix = cur.rpartition("/")
    return base or "/", prefix


def _listdir_or_empty(base):
    """``base``'s entry names, or [] if it can't be listed (doesn't exist, not a dir, no permission, ...)."""
    try:
        return [p.name for p in Path(base).iterdir()]
    except OSError:
        return []


def _is_denver_config_name(name):
    """Whether ``name`` names a denver.*.<ext> config file completion should offer.

    A '.yml'/'.yaml' always qualifies (denver's default format, PyYAML is a
    required dependency); '.toml' only if tomllib is importable (Python
    3.11+ -- see the module-level 'tomllib' import at the top of this file).
    """
    if not name.startswith("denver."):
        return False
    return name.endswith((".yml", ".yaml")) or (tomllib is not None and name.endswith(".toml"))


def _completion_path_candidate(base, name):
    """One dir entry as a completion candidate -- a subdirectory (trailing '/') or a denver config file, else None."""
    full = str(Path(base) / name) if base != "." else name
    if (Path(base) / name).is_dir():
        return full + "/"
    return full if _is_denver_config_name(name) else None


_COMPLETION_SHELLS = ("bash", "zsh", "fish")


def _completion_bind_names():
    """Every command word 'denver complete's own script should wire completion up for.

    Always 'denver' -- the installed, on-PATH entry point most users have --
    plus, so a checkout works too: however this denver was actually invoked
    (``sys.argv[0]``, e.g. a checkout's './src/denver.py') and the absolute
    path to the running script/executable itself (whatever _denver_launcher
    would re-invoke, e.g. '/home/x/denver/src/denver.py'). Shell completion
    is keyed on the literal word typed at the prompt, so registering all
    three (deduplicated, order preserved) is what lets both
    `eval "$(denver complete)"` and `./src/denver.py complete | source`
    -- the same script either way -- end up completing whatever you actually
    type later, not just an on-PATH 'denver'.
    """
    names = ["denver"]
    for candidate in (sys.argv[0], _denver_launcher()[-1]):
        if candidate and candidate not in names:
            names.append(candidate)
    return names


def _completion_script(shell, names):
    """The full 'denver complete <shell>' script, wired to every command word in ``names``.

    Each function body re-invokes whatever word the shell resolved as the
    command actually being completed (bash's ``${COMP_WORDS[0]}``, zsh's
    ``${words[1]}``, fish's ``$tokens[1]``) rather than a hardcoded 'denver'
    -- one shared function then works correctly no matter which of ``names``
    triggered it (see _completion_bind_names for what ends up in that list).
    '__complete' itself is the hidden subcommand all three shell out to.
    """
    quoted = [shlex.quote(name) for name in names]
    if shell == "bash":
        return _completion_script_bash(quoted)
    if shell == "zsh":
        return _completion_script_zsh(names)
    return _completion_script_fish(names, quoted)


def _completion_script_bash(quoted):
    """The bash branch of _completion_script -- see there for what ``quoted`` is."""
    # COMP_WORDS[0] is the literal word typed at the prompt -- if that word is a
    # bash alias (as with `alias denver=/path/to/denver.py`), it's not something
    # `"$cmd" __complete ...` can exec directly: alias expansion happens at parse
    # time, on the literal token, never on a variable's value.
    #
    # Resolved via the 'alias' *builtin* itself ('alias -- "$cmd"'), not by
    # indexing $BASH_ALIASES directly the way an earlier version of this did:
    # $BASH_ALIASES is an *associative* array, a bash-4.0+ feature, and macOS's
    # own system /bin/bash is still 3.2.57 (frozen there pre-GPLv3) -- indexing
    # it as if it always existed silently falls back to bash 3.2's only other
    # interpretation of an array subscript, arithmetic evaluation of "$cmd"
    # itself, which either crashes outright ("operand expected") for any 'cmd'
    # containing a '/' (any path -- the normal way to run a checkout) or
    # silently misresolves for one that doesn't. The 'alias' builtin, unlike
    # associative-array indexing, has always existed and behaved identically
    # since bash 3.2.
    #
    # 'alias -- "$cmd"' prints 'alias NAME=VALUE' with VALUE as one single
    # quoted string (round-trippable as a whole, the same way declare -p prints
    # any string) -- not as separately-quoted words the way real alias
    # expansion's later re-parse would treat it. So this unwraps it in two
    # eval'd steps: 'raw=${aliased#*=}' first (ordinary scalar assignment,
    # eval'd once to strip that one quoting layer down to VALUE's own literal
    # characters), then 'resolved=($raw)' -- unquoted, so bash's normal
    # word-splitting on $raw does what real alias expansion's re-parse would
    # have: split it into a command plus its own arguments.
    #
    # 'local -a args=("${COMP_WORDS[@]:1:COMP_CWORD}")' has to happen *before*
    # IFS is ever touched below, not inlined into the same command substitution
    # as the IFS=$'\n' change: bash 3.2 has a real bug where a double-quoted
    # *sliced* array expansion ('${arr[@]:offset:length}', unlike a plain
    # '${arr[@]}') stops treating each element as its own word once IFS no
    # longer contains a space, silently IFS-joining the whole slice into ONE
    # word instead ("run --show" instead of "run", "--show") -- verified
    # against real bash 3.2 via Docker. __complete would then see one mangled
    # argument and match nothing, exactly the empty-candidates failure this
    # guards against. Building 'args' first, while IFS is still bash's own
    # default, sidesteps the bug entirely; IFS is only ever changed afterward,
    # for its real purpose -- splitting __complete's own output into COMPREPLY.
    #
    # Every statement below ends in ';', and the closing '}' is followed by one
    # too, so the whole thing still parses if run as `eval $(denver complete)`
    # (unquoted): unquoted command substitution word-splits on IFS, which turns
    # every newline into a plain space before eval ever sees it. Without explicit
    # ';'s that collapse would run separate statements together with nothing to
    # separate them; the closing comment line is last for the same reason -- a
    # '#' earlier in the (now single-line) script would silently comment out
    # everything after it, since there'd be no real newline left to end it on.
    return (
        "_denver_complete() {"
        " local cmd=${COMP_WORDS[0]};"
        ' local -a resolved=("$cmd");'
        " local aliased raw;"
        ' aliased=$(alias -- "$cmd" 2>/dev/null) && eval "raw=${aliased#*=}" && resolved=($raw);'
        ' local -a args=("${COMP_WORDS[@]:1:COMP_CWORD}");'
        " local out;"
        ' out=$("${resolved[@]}" __complete "${args[@]}" 2>/dev/null);'
        " local IFS=$'\\n';"
        " COMPREPLY=($out);"
        " };\n"
        f"complete -F _denver_complete -o default -o bashdefault {' '.join(quoted)};\n"
        '# denver bash completion -- wire up with: eval "$(denver complete)"\n'
    )


def _completion_script_zsh(names):
    """The zsh branch of _completion_script -- see there for what ``names`` is."""
    # zsh's own completion system (compsys), not bash's -F/COMPREPLY -- $words is
    # the full command line (1-indexed), $CURRENT the index of the word being
    # completed, so $words[2,CURRENT] is the same slice bash's script passes to
    # __complete -- but the slice needs the '(@)' flag to stay a real array
    # through the double quotes: plain "${words[2,CURRENT]}" collapses the
    # whole slice into one IFS-joined string (zsh quotes an array the same
    # way it quotes a scalar unless '@' says otherwise), so __complete would
    # see a single mangled argument like "run --show" instead of two ("run",
    # "--show") and match nothing. compadd, not COMPREPLY, is how compsys
    # widgets report candidates.
    # Same alias problem as bash (see above) -- zsh's own $aliases associative
    # array is the equivalent of BASH_ALIASES; the (z) flag splits its value the
    # way the shell itself would, quoting included. Same unquoted-eval hazard as
    # bash too (zsh word-splits unquoted command substitutions on IFS just like
    # bash does) -- same ';'-everywhere, comment-last fix applies.
    #
    # 'compdef'-ing only ``names`` themselves (as bash/fish both correctly do)
    # isn't enough for zsh: its own dispatcher (_set_command, in zsh's own
    # Completion/Base/Core function library) never looks a slash-containing
    # command word up by its own literal spelling. For ANY such word -- even
    # an absolute path -- it tries the word's *basename* first ('denver.py'),
    # falling back to the literal word only if that basename lookup misses.
    # Worse, for a word starting with one or more '.'s ('./denver.py',
    # '../checkout/denver.py', the normal way to run a checkout) that
    # fallback isn't the literal word either -- it's '$PWD/' plus the word,
    # '.'s and all, which is never a real path and so never matches
    # anything either. Net effect without this: every leading-dot-relative
    # invocation silently falls through to zsh's own default (file) completion,
    # with no error to explain why. Registering each path-like name's own
    # basename too fixes every such spelling at once, since that's the string
    # zsh's dispatch always tries before anything else, regardless of how the
    # command was actually typed -- see
    # https://github.com/zsh-users/zsh/blob/master/Completion/Base/Core/_set_command
    #
    # '__complete --describe' (rather than plain '__complete') asks for
    # 'candidate\tdescription' lines (see _complete_candidates_described) --
    # zsh's own compadd, unlike bash's plain COMPREPLY, understands a
    # description column natively via its '-d' flag (a same-length array of
    # per-candidate display strings). But 'display strings', per compadd(1),
    # REPLACE what's shown for each candidate rather than annotating it --
    # "the completion code will then display [array element N] instead of
    # [candidate N]" -- so a descriptions[] entry set to the raw help text
    # alone would show only that text, the candidate itself never appearing.
    # zsh's own '_describe' utility (Completion/Base/Utility/_describe) hits
    # this exact same API and works around it by building each display
    # string as "value -- description" itself; this does the same (using
    # the same '--' separator '_describe' defaults to), so what's shown is
    # both, and only an empty description[] entry (nothing to add) falls
    # back to compadd's own default of showing the candidate alone.
    # A line with no tab at all (nothing known to describe it) keeps its
    # description empty for exactly that reason.
    # 'tab=$'"'"'\t'"'"'' -- not a literal tab byte in the script text itself --
    # is the same trick '_completion_script_bash' uses for its own IFS=$'\n':
    # written as the literal 4 characters $ ' \ t ', it's immune to the
    # unquoted-eval word-splitting problem discussed above (a literal tab
    # byte there would itself be an IFS character, and get split on same as
    # a space would), and only becomes a real tab when zsh itself evaluates
    # it, long after that word-splitting has already happened.
    # Column-aligned ('value␣␣␣␣-- description', every '--' lined up), the
    # way zsh's own completions (and '_describe', its own utility for
    # exactly this) normally look -- needs each value's own width known
    # before any description string can be built, hence the two passes:
    # first split every line into parallel values[]/helps[] arrays, then
    # (knowing every value's length) build descriptions[] padding each
    # value out to the widest one with '${(r:$width:)...}' (pads on the
    # right with spaces -- zsh's own left-justify-to-width flag).
    basenames = [Path(name).name for name in names if "/" in name]
    quoted = [shlex.quote(name) for name in dict.fromkeys(names + basenames)]
    lines = [
        "_denver_complete() {",
        "  local cmd=${words[1]};",
        '  local -a resolved=("$cmd");',
        "  (( ${+aliases[$cmd]} )) && resolved=(${(z)aliases[$cmd]});",
        "  local -a raw values helps descriptions;",
        '  raw=("${(@f)$("${resolved[@]}" __complete --describe "${(@)words[2,CURRENT]}" 2>/dev/null)}");',
        "  local line tab=$'\\t';",
        '  for line in "${raw[@]}"; do',
        "    if [[ $line == *${tab}* ]]; then",
        "      values+=(\"${line%%${tab}*}\");",
        '      helps+=("${line#*${tab}}");',
        "    else",
        '      values+=("$line");',
        '      helps+=("");',
        "    fi;",
        "  done;",
        "  local width=0 v;",
        '  for v in "${values[@]}"; do (( ${#v} > width )) && width=${#v}; done;',
        "  local i;",
        "  for (( i = 1; i <= $#values; i++ )); do",
        '    if [[ -n ${helps[i]} ]]; then',
        '      descriptions+=("${(r:$width:)values[i]} -- ${helps[i]}");',
        "    else",
        '      descriptions+=("");',
        "    fi;",
        "  done;",
        '  compadd -d descriptions -- "${values[@]}";',
        "};",
        f"compdef _denver_complete {' '.join(quoted)};",
        '# denver zsh completion -- wire up with: eval "$(denver complete)"',
    ]
    return "\n".join(lines) + "\n"


def _completion_script_fish(names, quoted):
    """The fish branch of _completion_script -- see there for what ``names``/``quoted`` are."""
    # fish's own completion system: 'commandline -opc' is the current command's tokens
    # before the cursor (command name included, current partial word excluded),
    # 'commandline -ct' is that partial word -- together the same words __complete
    # expects. One 'complete' line per name -- fish takes only one command per
    # invocation, unlike bash/zsh's space-separated list. '-c NAME' only ever matches
    # a bare command name -- a path (any name with a '/' in it, e.g. a checkout's
    # './src/denver.py' or the absolute path) needs '-p PATH' instead, or it silently
    # never matches anything typed at the prompt.
    #
    # '-f' (no-files) on the main entry keeps denver's own candidates from being
    # drowned out by every file in the cwd -- unlike bash's '-o default', fish shows
    # filenames alongside custom candidates by default. That's exactly right for most
    # positions (denver's own __complete already returns the relevant directories/
    # denver.toml files for the <env> positional itself, see _completion_path_candidate),
    # but _PATH_VALUE_FLAGS (-c/--config/-cf/--config-file) take an arbitrary path
    # __complete can't enumerate -- so a second entry, gated by '-n __denver_expects_path'
    # and forcing files back on with '-F', covers just that one case.
    #
    # The documented wiring is `denver complete | source`, not eval -- but users
    # coming from bash/zsh naturally try `eval "$(denver complete)"` for fish
    # too (fish 3.4+ even accepts that `$(...)` syntax), and unlike bash/zsh
    # this used to silently define nothing at all: unquoted command
    # substitution word-splits on newlines, `eval` rejoins with plain spaces,
    # and fish (unlike bash) never treats a bare space as a statement
    # separator -- so without explicit ';'s the entire script collapsed onto
    # one line, and a leading '#' comment then ate that whole line, comment
    # included. Every statement below ends in ';' for exactly the same
    # reason the bash/zsh branches do, and the closing comment line is last
    # so a real '#' can never swallow anything after it once newlines are gone.
    #
    # '__complete --describe' (rather than plain '__complete') is fish/zsh-only:
    # it switches _handle_dunder_complete over to _complete_candidates_described,
    # whose 'candidate\tdescription' lines fish's own completion pager (and
    # zsh's own compadd -d, see _completion_script_zsh) already know how to
    # split and show -- bash's plain COMPREPLY has no such convention (a
    # literal tab would land in the command line itself), so it keeps
    # calling plain '__complete', undecorated.
    path_value_flags = " ".join(shlex.quote(flag) for flag in _PATH_VALUE_FLAGS)
    lines = [
        "function __denver_complete;",
        "    set -l tokens (commandline -opc) (commandline -ct);",
        "    $tokens[1] __complete --describe $tokens[2..-1] 2>/dev/null;",
        "end;",
        "function __denver_expects_path;",
        "    set -l prev (commandline -opc)[-1];",
        f"    contains -- $prev {path_value_flags};",
        "end;",
    ]
    # zip(strict=True) is Python 3.10+ only (denver's floor is 3.9); plain zip() is fine
    # here since 'quoted' is built 1:1 from 'names' in the caller, _completion_script.
    for name, quoted_name in zip(names, quoted):
        flag = "-p" if "/" in name else "-c"
        lines.append(f"complete {flag} {quoted_name} -f -a '(__denver_complete)';")
        lines.append(f"complete {flag} {quoted_name} -n __denver_expects_path -F -a '(__denver_complete)';")
    lines.append("# denver fish completion -- wire up with: denver complete | source")
    return "\n".join(lines) + "\n"


def _detect_shell():
    """Best-effort guess at the interactive shell invoking us, for a shell-less 'denver complete'.

    Looked up from the nearest *ancestor* process's own command name -- 'denver
    complete' run directly at a prompt (or from `eval "$(denver
    complete)"`/`denver complete | source`) has that shell as its parent, or
    grandparent past a frozen build's PyInstaller bootloader hop (see
    _parent_process_name). Falls back to $SHELL, then to "bash", if no ancestor
    can be identified at all or none is recognised as one of bash/zsh/fish
    (e.g. it's a script, or wrapped by tmux/su/uv run/... with an unrelated
    process in between -- that wrapper's own name is still truthy, so $SHELL
    has to be tried explicitly rather than only when there's no ancestor name
    at all) -- never raises. An explicit `denver complete <shell>` bypasses
    this outright.
    """
    name = _normalised_shell_name(_parent_process_name())
    if name in _COMPLETION_SHELLS:
        return name
    name = _normalised_shell_name(os.environ.get("SHELL", ""))
    return name if name in _COMPLETION_SHELLS else "bash"


def _normalised_shell_name(name):
    """``name`` (a process name or $SHELL value, possibly None) as a bare shell name. See _detect_shell."""
    return Path(name or "").name.lstrip("-")  # login shells prefix their name, e.g. "-zsh"


def _own_process_names():
    """Basenames this process could show up as in its own ancestry. See _parent_process_name."""
    names = {Path(sys.argv[0]).name}
    if getattr(sys, "frozen", False):  # a frozen build's PyInstaller bootloader shares its own name
        names.add(Path(sys.executable).name)
    return names


# Launchers that run denver as a child without themselves being the invoking shell -- e.g.
# README's own recommended dev-checkout alias, `alias denver="uv run $PWD/src/denver.py"`,
# whose immediate parent is "uv" itself, not the shell the alias was typed into. Walked past
# just like a frozen build's own bootloader hop (see _parent_process_name), so the real shell
# underneath is found instead of stopping one hop too early.
_NON_SHELL_LAUNCHER_NAMES = {"uv", "uvx"}


def _parent_process_name():
    """The nearest ancestor process's command name that isn't this program itself, or None. See _detect_shell.

    A frozen (PyInstaller ``--onefile``) build's bootloader clone()s a child
    to run the actual interpreter in -- that child's immediate parent is the
    bootloader itself, whose command name is this same program's, never the
    shell that invoked it. Walking past ancestors that match one of
    _own_process_names' names, or one of _NON_SHELL_LAUNCHER_NAMES (e.g. a
    `uv run` wrapper), skips that hop so the shell underneath is found
    instead. Bounded to a handful of hops so a pathological /proc (or ``ps``)
    can never spin forever.
    """
    pid = _own_ppid()
    skip_names = _own_process_names() | _NON_SHELL_LAUNCHER_NAMES
    for _ in range(5):
        if pid is None:
            return None
        name = _process_name(pid)
        if name not in skip_names:  # also covers name is None -- never a member of skip_names
            return name
        pid = _parent_pid(pid)
    return None


def _own_ppid():
    """os.getppid(), or None if it can't be determined. See _parent_process_name."""
    try:
        return os.getppid()
    except OSError:
        return None


def _process_name(pid):
    """``pid``'s own command name, or None if it can't be determined. See _parent_process_name."""
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        pass
    try:  # no /proc (e.g. macOS) -- ask the same question of 'ps' instead
        completed = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True)
    except OSError:
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _parent_pid(pid):
    """``pid``'s own parent pid, or None if it can't be determined. See _parent_process_name."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        pass
    else:
        # comm (2nd field) is parenthesised and may itself contain spaces/parens, so
        # only the tail after its closing ')' is safe to split on whitespace --
        # state is first there, ppid second.
        fields = stat.rsplit(")", 1)[-1].split()
        try:
            return int(fields[1])
        except (IndexError, ValueError):
            return None
    try:  # no /proc (e.g. macOS) -- ask the same question of 'ps' instead
        completed = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    try:
        return int(completed.stdout.strip())
    except ValueError:
        return None


def _split_argv(argv):
    """Split ``argv`` on the first literal ``--``: denver's own flags, then the command to forward verbatim.

    So a command's own flags (e.g. `denver env -- west build --pristine`)
    are never mistaken for denver's, and conversely a mistyped/unknown
    denver flag is never silently mistaken for (part of) the command: with
    no `--`, an unconsumed token is just an extra positional argparse itself
    rejects.
    """
    if "--" in argv:
        separator = argv.index("--")
        return argv[:separator], argv[separator + 1 :]
    return argv, []


def _handle_info_flags(args, parser):
    """Handle --help/--version/--license, if given. Returns True if one was (each prints and/or dies)."""
    if args.help:
        _print_help_for(args, parser)
        return True
    if args.version:
        print(f"denver {package_version() or UNKNOWN_VERSION}")
        return True
    if args.license:
        _print_license()
        return True
    return False


def _print_help_for(args, parser):
    """Print the invoked subcommand's own help, or the top-level one with no (recognised) subcommand."""
    subcommand_parser = parser.subcommand_parsers.get(getattr(args, "subcommand", None))
    if subcommand_parser is not None:
        print(subcommand_parser.format_help())
    else:
        print_help(parser)


def _print_license():
    """Print denver's own LICENSE text, or die if it can't be found (neither a checkout nor installed metadata)."""
    text = license_text()
    if text is None:
        die("LICENSE not found -- neither a checkout nor installed package metadata has it")
    print(text)


def _run_cli(argv=None):
    """Parse argv, resolve the env, and either exec its command or dispatch a denver-only subcommand.

    ``argv`` defaults to ``sys.argv[1:]`` (real CLI invocation); tests pass
    an explicit list instead. Never returns at all when the resolved command
    replaces this process (``os.execvpe``, via ``Context.exec``); otherwise
    returns normally on success, or exits via ``die()``.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if _handle_dunder_complete(argv):
        return
    if not argv:
        print_help(build_arg_parser())
        return
    _run_resolved_cli(argv)


def _handle_dunder_complete(argv):
    r"""Handle the hidden 'denver __complete' subcommand, if that's what this is. Returns True if it ran.

    Deliberately bypasses argparse and every bit of env resolution below
    (see _run_resolved_cli), so a completion request can never itself
    trigger a usage error or a die() while the user is mid-keystroke.

    An optional leading '--describe' (only fish's and zsh's own wiring ever
    send one -- see _completion_script_fish/_completion_script_zsh) switches
    to _complete_candidates_described, whose 'candidate\tdescription' lines
    their own completion pagers know how to show; bash never passes it, so
    it's unaffected.
    """
    if not _is_dunder_complete(argv):
        return False
    for candidate in _dunder_complete_candidates(argv[1:]):
        print(candidate)
    return True


def _is_dunder_complete(argv):
    """Whether ``argv`` is 'denver __complete ...' -- see _handle_dunder_complete, the only caller."""
    return bool(argv) and argv[0] == "__complete"


def _dunder_complete_candidates(words):
    """The candidates for '__complete's own argv (past '__complete' itself).

    See _handle_dunder_complete for what its optional leading '--describe' does.
    """
    if words and words[0] == "--describe":
        return _complete_candidates_described(words[1:])
    return _complete_candidates(words)


def _run_resolved_cli(argv):
    """The real argv parse: resolve the env, then either exec its command or dispatch a denver-only subcommand.

    Only reached once _run_cli's own argv-shape guards (empty argv, the
    hidden __complete subcommand) are out of the way.
    """
    head, forwarded = _split_argv(argv)
    preliminary, extra_argv = _preliminary_args(head)

    if preliminary.subcommand == "complete":
        _print_completion_script(preliminary)
        return

    if _handle_env_less_argv(preliminary, head):
        return

    # Handled here, ahead of everything below: none of the run-only
    # machinery -- '-e' env vars, the config-aware second parse, the
    # denver-version check -- applies to removing directories, and cleaning
    # has to work for an env whose denver.toml is broken (see clean_env_and_imports).
    if preliminary.subcommand == "clean":
        _, config_path = resolve_env_dir(preliminary.env)
        clean_env_and_imports(
            config_path, dry_run=preliminary.dry_run, all_dirs=preliminary.all, assume_yes=preliminary.yes
        )
        return

    # Applied to denver's own process environment as early as possible --
    # before the env's config is even loaded -- so it reaches everything
    # downstream (${...} interpolation, hooks, every stage, the final
    # command) the exact same way a real shell export would, with no
    # separate plumbing path of its own; see resolve_full_config for how
    # env_vars is *also* carried explicitly (RunOptions -> ctx.env), which
    # is what makes it survive a wrapper reinvocation (docker) too, a
    # boundary a real os.environ mutation here cannot cross on its own.
    env_vars = _parsed_env_vars(preliminary.env_vars)
    os.environ.update(env_vars)

    env_dir, config_path = resolve_env_dir(preliminary.env)
    config = _load_cli_config(preliminary, config_path)

    # Second pass, this time with the env's own 'denver-custom-args:' known: an unknown
    # flag is once again argparse's own `usage:`/`error:` + exit 2, and
    # --help lists this env's flags alongside denver's own.
    parser = build_arg_parser(config.get("denver-custom-args"))
    args = parser.parse_args(head)

    if _handle_info_flags(args, parser):
        return

    _require_config_source(config_path, args.config_file)

    cli_args = CliArgs(cli_arg_env(config.get("denver-custom-args"), args), extra_argv)

    if _handle_config_subcommands(args, env_dir, config, config_path, cli_args=cli_args, env_vars=env_vars):
        return

    _require_runnable(env_dir, config, config_path)
    run_stages(env_dir, config, config_path, forwarded, options=_run_options(args, cli_args, env_vars))


def _print_completion_script(preliminary):
    """Print the wiring script for 'denver complete [<shell>]' -- the given shell, or auto-detected."""
    shell = preliminary.shell or _detect_shell()
    print(_completion_script(shell, _completion_bind_names()), end="")


def _preliminary_args(head):
    """First-pass parse of denver's own flags, with <env> resolved. Returns (namespace, extra_argv).

    Only denver's own flags are known here: the extra flags this env declares
    (its 'denver-custom-args:') live in the very file <env> names, so they cannot be parsed
    before <env> itself has been. Unknown tokens are tolerated and re-parsed
    for real by the caller's second pass -- they are this env's own flags, or
    a typo, and only that second pass can tell the two apart.

    <env> falls back to DENVER_ENV_DIR when omitted from argv entirely, so a
    shell/CI that already exports it (e.g. one denver invocation per project
    checkout) need not repeat it on every command line. An <env> actually
    given on the command line always wins. Only meaningful for the
    subcommands that take an <env> at all ('run'/'clean'): with no
    subcommand (or 'complete', which has no <env> of its own) the namespace
    simply has no ``env`` attribute, since argparse only adds one when a
    subparser that declares it actually ran.
    """
    preliminary, extra_argv = build_arg_parser().parse_known_args(head)
    if preliminary.subcommand in ("run", "clean") and preliminary.env is None:
        preliminary.env = os.environ.get("DENVER_ENV_DIR") or None
    return preliminary, extra_argv


def _run_options(args, cli_args, env_vars):
    """Everything the parsed command line chose about *how* to run, as one RunOptions."""
    return RunOptions(
        until_stage=args.until,
        skip_stages=args.skip,
        quiet=args.quiet,
        verbose=args.verbose,
        fast=args.fast,
        force=args.force,
        ci=args.ci,
        dry_run=args.dry_run,
        no_wait=args.no_wait,
        start_time=args.start_time,
        cli_args=cli_args,
        env_vars=env_vars,
        export_env=args.export_env,
    )


def _handle_env_less_argv(preliminary, head):
    """Handle an invocation with no env to read 'denver-custom-args:' from. Returns True if the run is over.

    `denver --help`/`--version`/`--license` must keep working with no <env>
    at all, or with one naming something that isn't there -- but neither can
    wait for the second parse, which needs an env's config to exist. So
    those invocations are answered here instead, and parsed *strictly*
    against denver's own flags: with no env, denver's own flags are the
    entire vocabulary, so a mistyped one is still argparse's usual error
    rather than a silently ignored token.

    Also where a missing subcommand entirely is reported: 'complete' is
    already handled by the caller before this runs, so by construction
    ``preliminary.subcommand`` here is None, "run" or "clean" -- and only
    those last two even have an ``env`` attribute to check (see
    _preliminary_args).

    A non-existent <env> falls through (False) rather than being reported
    here: resolve_env_dir says that far better.
    """
    env = getattr(preliminary, "env", None)
    if env is not None and Path(env).expanduser().exists():
        return False

    parser = build_arg_parser()
    if _handle_info_flags(parser.parse_args(head), parser):
        return True

    if preliminary.subcommand is None:
        die("no subcommand given -- use 'run', 'clean' or 'complete'; see `denver --help`")

    if env is None:
        die("no environment given -- pass one, set $DENVER_ENV_DIR, or see `denver --help`")

    return False


def _load_cli_config(args, config_path) -> dict:
    """The env's config as the CLI asked for it: its own file, plus -f files, plus -c overrides -- validated.

    ``config_path`` not existing is tolerated here rather than reported --
    ``--help``/``--version``/``--license`` (handled right after this, in
    _run_resolved_cli) must still work for a directory with no denver.toml
    of its own, and -f/--config-file may yet supply the whole config even
    for a real run. See _require_config_source for where the "no
    denver.toml" case actually gets reported, once neither of those
    excuses applies.
    """
    config = load_config(config_path) if config_path.is_file() else {}
    for config_file in args.config_file:
        config = deep_merge(config, load_config(Path(config_file)))
    config = apply_config_overrides(config, args.config)
    # both version gates run before validate_top_level_keys: a file written
    # for a newer denver may well use a key this one doesn't know, and
    # "upgrade denver" explains that far better than "unknown top-level key".
    validate_config_version(config)
    validate_denver_version(config)
    validate_top_level_keys(config)
    validate_stage_filters(config, args.until, args.skip)
    validate_hooks_keys(config)
    # deep_merge/apply_config_overrides are typed for config *values* (a
    # mapping, a list, a scalar); a whole denver.toml is always the mapping.
    return cast(dict, config)


def _require_config_source(config_path, config_files):
    """Die if <env> resolved to a directory with no config file of its own, and no -f/--config-file either.

    Checked once ``--help``/``--version``/``--license`` are ruled out (see
    _load_cli_config, which tolerates the same situation so those can still
    work) -- everything past this point (--show-config, --scripts, a real
    run) needs an actual config from somewhere.
    """
    if not config_path.is_file() and not config_files:
        die(
            f"no {_config_names_text()} in '{config_path.parent}' -- give a path to an env directory that has one, "
            f"a path directly to a config file, or supply --config-file."
        )


def _handle_config_subcommands(args, env_dir, config, config_path, *, cli_args=None, env_vars=None):
    """Handle 'run --show-config'/'run --show-config-full', or 'run --scripts', if that's what this is. Returns True if one ran (nothing is launched then)."""
    if args.show_config or args.show_config_full:
        show_config(
            env_dir,
            config,
            config_path,
            until_stage=args.until,
            skip_stages=args.skip,
            cli_args=cli_args,
            env_vars=env_vars,
            # --show-config-full wins if both are given -- more keys is the
            # more inclusive ask, so it's the natural tiebreaker.
            minimal=not args.show_config_full,
            format=args.format,
        )
        return True

    if args.scripts:
        _handle_scripts_subcommand(args, env_dir, config, config_path, cli_args=cli_args, env_vars=env_vars)
        return True

    return False


def _handle_scripts_subcommand(args, env_dir, config, config_path, *, cli_args, env_vars):
    """Handle 'run --scripts' and its --setup/--login/--clean shorthands -- nothing is launched.

    A bare '--scripts' (no name at all) lists what the env declares and
    stops there: listing is a query, so it removes nothing -- --clean's own
    state-directory removal included.
    """
    if LIST_SCRIPTS in args.scripts:
        list_named_scripts(env_dir, config_path, until_stage=args.until, skip_stages=args.skip)
        return

    run_named_scripts(
        env_dir,
        config,
        config_path,
        args.scripts,
        until_stage=args.until,
        skip_stages=args.skip,
        quiet=args.quiet,
        verbose=args.verbose,
        dry_run=args.dry_run,
        no_wait=args.no_wait,
        cli_args=cli_args,
        env_vars=env_vars,
    )
    # --clean only, and after the env's own 'clean' scripts rather than
    # before: those scripts run against the built environment (a venv's
    # tools, a container), which is exactly what is about to be removed.
    if args.clean_workdir:
        clean_env(env_dir, config_path, dry_run=args.dry_run)


def _require_runnable(env_dir, config, config_path):
    """Die unless this env may be started directly, with stages to run.

    'runnable: false' marks a shared/base env (meant to be inherited via
    import:, not started directly). '--show-config'/'run --scripts' are
    diagnostic, not "running", so they're exempt (see
    _handle_config_subcommands, called before this): inspecting a base env's
    resolved config is still useful.
    """
    if config_path.is_file() and not is_runnable_env(config_path):
        die(f"env '{env_dir.name}' sets 'runnable: false' -- it's meant to be imported, not started directly.")

    if not config.get("stages"):
        die(f"env '{env_dir.name}' declares no 'stages:' in its {config_path.name}")


def _run_main_or_die_quietly_on_broken_pipe():
    """sys.exit(main()), tolerating a reader that hung up on us mid-write.

    E.g. piping ``denver complete`` into a shell 'source' builtin that --
    unlike fish's -- refuses piped stdin and closes it unread immediately:
    both bash's and zsh's do this, and print() only notices on the next write.

    Without this, such a hang-up surfaces as a scary, unhandled-looking
    "Exception ignored in: ..." / "BrokenPipeError" dump during Python's own
    atexit flush of stdout, well after main() has already returned -- because
    stdout is block-buffered (not line-buffered) once it isn't a tty, so a
    few KB of completion-script output can sit unflushed in the buffer the
    whole time main() runs, and the broken pipe is only actually discovered
    once something tries to flush it. The explicit flush() right after
    main() forces that discovery to happen here, inside this try, instead of
    during interpreter teardown where nothing can catch it; redirecting
    stdout to devnull first stops the same error from replaying once more
    when the interpreter does its own final flush regardless.
    """
    try:
        exit_code = main()
        sys.stdout.flush()
    except BrokenPipeError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        sys.exit(1)
    sys.exit(exit_code)


if __name__ == "__main__":
    _run_main_or_die_quietly_on_broken_pipe()
