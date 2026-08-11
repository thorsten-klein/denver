#!/usr/bin/env python3
"""denver -- Development Environment Launcher.

Launch a reproducible development environment described by a ``denver.yml``
file: denver resolves it (following ``import:`` inheritance), then runs the
generic *providers* its ``stages:`` list names (uv, conan, zephyr, docker,
...) to build/enter the environment purely from config.

A command to run, if given, must be introduced with '--' (e.g.
`denver <env> -- echo hi`); everything after it is forwarded as-is. With no
command, denver starts an interactive shell.

If an env stacks a wrapper provider (e.g. docker), running it builds/enters
the container and re-invokes denver inside with --skip <that stage>, so the
remaining providers build the environment there; skip the wrapper yourself
(e.g. `denver <env> --skip docker`) to run that same stack on the host
instead.

<env> is a path to a directory containing a denver.yml, or a path directly
to a YAML config file (any name, e.g. denver.debug.yml -- lets one folder
hold several denver.xxx.yml variants).

--dry-run answers "what does this env actually run?" without running it:
every stage is walked in order, but its commands and file writes are printed
(prefixed '[dry-run]') instead of performed, and the final command is printed
instead of launched. Read-only queries and sourced scripts do still run --
they are what the printed commands are derived from; see README.md for the
full marker legend and the wrapper-boundary caveat.

Run `denver --help` to see every flag, and README.md for the full
behavioural reference (-c's dotted-path/+= syntax, --run's 'scripts:'
mechanism, the wrapper relocation model, ...).

Examples:
    src/denver.py examples/zephyr-devshell-4.3.1
    src/denver.py examples/zephyr-devshell-4.3.1 -- echo hello
"""

import argparse
import copy
import importlib.metadata
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

try:
    import yaml
except ImportError:  # pragma: no cover - depends on the interpreter denver runs on
    # PyYAML is denver's only runtime dependency, so a pip/uv install always
    # has it -- but a docker-wrapped env re-invokes denver with the
    # *container's* bare `python3` (see reinvoke_command), which resolves
    # imports against the image, not against whatever installed denver on the
    # host. That process is one the user never asked for by name, so the bare
    # ImportError traceback points at neither the cause nor the fix. Printed
    # rather than logged: logging isn't configured this early, and the same
    # "ERROR: " prefix keeps it looking like every other denver error.
    sys.stderr.write(
        f"ERROR: denver requires PyYAML, but 'import yaml' failed in {sys.executable} "
        f"(python {'.'.join(str(n) for n in sys.version_info[:3])}).\n"
        f"ERROR: Install it there, e.g. 'pip install pyyaml' / 'uv pip install pyyaml', "
        f"or 'apt-get install python3-yaml' on Debian/Ubuntu.\n"
        f"ERROR: If this ran inside a container, that interpreter is the image's: "
        f"add PyYAML to the Dockerfile of the docker stage's image.\n"
    )
    sys.exit(1)

# Make the bundled ``providers`` package importable both when run as
# ``src/denver.py`` and when installed as the ``denver`` module.
sys.path.insert(0, str(Path(__file__).resolve().parent))

CONFIG_NAME = "denver.yml"


# where denver's own code lives (this file's directory, containing denver.py
# and providers/) -- always correct in both a checkout and an installed
# package, unlike DENVER_DIR below (per-run state, not code).
DENVER_PKG_DIR = Path(__file__).resolve().parent

# conan_scripts ships alongside this module's providers/ package, not
# DENVER_DIR, so it's still found when denver is installed via pip (no
# repo-root layout).
CONAN_SCRIPTS_DIR = DENVER_PKG_DIR / "providers" / "conan_scripts"

# terminal-friendly rendition of assets/logo.svg (an SVG can't be drawn in a
# plain terminal); kept alongside it under assets/ so both stay in sync when
# the wordmark changes. Same DENVER_PKG_DIR-relative resolution as
# CONAN_SCRIPTS_DIR, so it's found in both a checkout and an installed package.
LOGO_PATH = DENVER_PKG_DIR / "assets" / "logo.txt"


def checkout_root():
    """The source checkout denver itself is running out of, or None.

    True whenever DENVER_PKG_DIR is a ``<checkout>/src`` holding
    ``providers/`` -- i.e. both when running the script directly
    (``src/denver.py``) and under an editable install (``uv pip install
    -e .``), which keeps DENVER_PKG_DIR pointing into the checkout's
    ``src/``. Installed any other way (e.g. a built wheel), DENVER_PKG_DIR is
    wherever the package manager put it (site-packages) and this is None.
    """
    if DENVER_PKG_DIR.name == "src" and (DENVER_PKG_DIR / "providers").is_dir():
        return DENVER_PKG_DIR.parent
    return None


def _default_denver_dir():
    """Where denver stores per-env state (venvs, conan caches, performance.jsonl).

    When running from a source checkout, the checkout root is used, matching
    every example in README.md: "no install required to run" is a deliberate
    constraint, not an oversight.

    Installed any other way, there's no checkout at all -- DENVER_PKG_DIR is
    wherever the package manager put it (site-packages), which is no place
    to write a venv. DENVER_STATE_DIR (default: ~/.denver) is used instead;
    set it explicitly to control where denver keeps its state when running
    installed.
    """
    root = checkout_root()
    if root is not None:
        return root
    return Path(os.environ.get("DENVER_STATE_DIR", "~/.denver")).expanduser()


DENVER_DIR = _default_denver_dir()

# Not imported from providers.context: providers is only imported lazily
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
# Needed twice: for `--version`, and to check a denver.yml's
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
# number (see doc/development.md, "Releasing").
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


def package_version():
    """The running denver's version string, or None if it can't be determined.

    The checkout's git tags first (see scm_version), then the installed
    distribution's metadata via importlib.metadata -- which is what answers
    for a normally installed wheel, where there's no checkout to describe.
    setuptools-scm derives that metadata from git tags at build time -- see
    pyproject.toml's [tool.setuptools_scm].
    """
    version = scm_version()
    if version is not None:
        return version
    try:
        return importlib.metadata.version(DISTRIBUTION_NAME)
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
def load_yaml(path):
    """Load a YAML file, returning a dict ({} for empty files)."""
    with Path(path).open() as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        die(f"{path}: expected a mapping at the top level, got {type(data).__name__}")
    return data


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
        result = dict(base)
        for key, value in override.items():
            child_path = f"{_path}.{key}" if _path else str(key)
            result[key] = deep_merge(result.get(key, _UNSET), value, child_path)
        return result

    if isinstance(base, list) and isinstance(override, list):
        return _merge_lists(base, override)

    return _merge_scalar(base, override, _path)


def _merge_lists(base, override):
    """``deep_merge``'s list case: appended, unless ``override`` carries a ``!``/``<overwrite>`` reset marker."""
    if any(isinstance(entry, str) and (entry.startswith("!") or entry == "<overwrite>") for entry in override):
        return [
            entry[1:] if isinstance(entry, str) and entry.startswith("!") else entry
            for entry in override
            if entry != "<overwrite>"
        ]
    return base + override


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


def resolve_import(entry, base_dir):
    """Resolve an ``import:`` entry to the denver.yml path it refers to.

    An entry may point at a directory (its ``denver.yml`` is used) or directly
    at a YAML file, relative to the importing config's directory.
    """
    target = (base_dir / entry).resolve()
    if target.is_dir():
        target = target / CONFIG_NAME
    if not target.is_file():
        die(f"import '{entry}' in {base_dir} does not resolve to a {CONFIG_NAME}")
    return target


def load_config(config_path, _seen=None):
    """Load a denver.yml and all of its imports into one merged config.

    Imports are merged first (base), then the importing file overlays on top,
    so a version-specific env can override values inherited from its base.
    """
    config_path = config_path.resolve()
    if _seen is None:
        _seen = set()
    if config_path in _seen:
        die(f"circular import detected at {config_path}")
    _seen.add(config_path)

    raw = load_yaml(config_path)
    base_dir = config_path.parent

    merged = {}
    for entry in raw.get("import", []) or []:
        imported_path = resolve_import(entry, base_dir)
        merged = deep_merge(merged, load_config(imported_path, _seen))

    # 'runnable' marks one specific denver.yml (e.g. a shared base meant only
    # to be imported, never started directly) -- it must never leak from an
    # imported base into a derived env's own resolved config, or every env
    # importing a 'runnable: false' base would incorrectly inherit it too.
    # is_runnable_env() already reads it straight from each file's own raw
    # YAML, never through this merge, for exactly this reason; dropping it
    # here keeps --show-config's output consistent with that.
    merged.pop("runnable", None)

    # 'import' is a directive, not inheritable data: drop it from the result.
    overlay = {k: v for k, v in raw.items() if k != "import"}
    return deep_merge(merged, overlay)


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
    if isinstance(current, list):
        return current + (value if isinstance(value, list) else [value])
    if isinstance(current, bool) or isinstance(value, bool):
        die(f"--config: cannot += onto '{path}' ({current!r} += {value!r}): not a list, string or number")
    if isinstance(current, (int, float)) and isinstance(value, (int, float)):
        return current + value
    if isinstance(current, str) and isinstance(value, str):
        return current + value
    die(f"--config: cannot += onto '{path}' ({current!r} += {value!r}): not a list, string or number")


def apply_config_override(config, spec):
    """Apply one ``-c``/``--config`` KEY.PATH=VALUE (or +=VALUE) spec.

    Any missing parent section along KEY.PATH is created as an empty dict.
    Does not mutate ``config`` or any of its nested dicts in place.
    """
    path_parts, op, raw_value = parse_config_override_spec(spec)
    value = yaml.safe_load(raw_value)

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


# Top-level denver.yml keys that aren't a stage's own config section.
KNOWN_TOP_LEVEL_KEYS = {
    "version",
    "denver-version",
    "import",
    "stages",
    "command",
    "runnable",
    "env",
    "hooks",
}


def validate_top_level_keys(config):
    """Die on a top-level key that's neither a known denver.yml key nor a stage id declared in 'stages:'.

    Without this, a typo'd section (or one left behind after a stage was
    renamed/removed from 'stages:') is just silently ignored -- no stage
    ever reads it, and nothing says so.
    """
    allowed = KNOWN_TOP_LEVEL_KEYS | set(config.get("stages") or [])
    unknown = sorted(set(config) - allowed)
    if unknown:
        die(
            f"denver.yml: unknown top-level key(s) {', '.join(unknown)} -- "
            f"not a recognised key and not a stage id in 'stages:'"
        )


# the denver.yml schema version this denver understands; bump together with
# an actual breaking change to the schema (this module's own docstring and
# each provider's module docstring are the schema's documentation).
SUPPORTED_CONFIG_VERSION = "1.0"


def validate_config_version(config):
    """Die if 'version:' is set to a schema version this denver doesn't understand.

    'version:' exists precisely so a future, incompatible denver.yml schema
    change can be rejected with a clear message instead of silently
    misinterpreted -- so it must actually gate something, not just be
    accepted and ignored. Compared as a string so YAML's own numeric parsing
    (``1.0`` -> a float) doesn't matter.
    """
    version = config.get("version")
    if version is not None and str(version) != SUPPORTED_CONFIG_VERSION:
        die(
            f"denver.yml: unsupported 'version: {version}' -- "
            f"this denver understands version {SUPPORTED_CONFIG_VERSION}."
        )


# A version is compared as (release-numbers, rank): 1.0.3 -> ((1, 0, 3), 0).
# 'rank' orders the three kinds of suffix a release number can carry, so
# every version string denver can be handed sorts sensibly against a plain
# release: a pre-release (1.1.0rc1, or setuptools-scm's 1.1.0.dev3+g1234567
# for an untagged commit) sorts *before* 1.1.0, anything else (git
# describe's 1.0.3-2-gabc1234, i.e. two commits past the 1.0.3 tag) *after*
# the release it builds on. No PEP 440 library is used -- denver's only
# runtime dependency is pyyaml, and this ordering is all a 'denver-version:'
# requirement needs.
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
    requirements = []
    for part in str(spec).split(","):
        match = _SPEC_RE.fullmatch(part)
        wanted = parse_version(match.group(2)) if match else None
        if wanted is None:
            die(
                f"denver.yml: invalid 'denver-version: {spec}' -- {part.strip()!r} is not a version requirement "
                f"(expected e.g. \">=1.0.3\", \"1.0.3\" or \">=1.0.3, <2\")."
            )
        requirements.append((match.group(1) or ">=", wanted, f"{match.group(1) or '>='}{match.group(2)}"))
    return requirements


def validate_denver_version(config):
    """Die if 'denver-version:' isn't satisfied by the denver actually running.

    A denver.yml using a key or behaviour only a newer denver knows would
    otherwise fail somewhere deep in a stage (or, worse, quietly do
    something else); this states the requirement up front, in the file that
    has it, and reports it as exactly that.

    Distinct from 'version:', which pins the *schema* denver.yml is written
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
            f"denver.yml requires 'denver-version: {spec}', but this denver's own version is "
            f"{running or 'unknown'} -- cannot verify the requirement, continuing."
        )
        return

    unmet = [
        text
        for operator, wanted, text in requirements
        if not _SPEC_OPERATORS[operator](compare_versions(parsed, wanted))
    ]
    if unmet:
        die(
            f"denver.yml requires 'denver-version: {spec}', but this denver is {running} "
            f"(unmet: {', '.join(unmet)}) -- upgrade it, e.g. `pip install --upgrade {DISTRIBUTION_NAME}`."
        )


def validate_stage_filters(config, until_stage, skip_stages):
    """Die if --until/--skip name a stage id not declared in 'stages:'."""
    declared = set(config.get("stages") or [])
    named = set(skip_stages) | ({until_stage} if until_stage else set())
    unknown = sorted(named - declared)
    if unknown:
        die(
            f"--until/--skip: unknown stage id(s) {', '.join(unknown)} -- "
            f"not declared in 'stages:' ({', '.join(sorted(declared)) or 'none'})"
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
# --run <name> mechanism (see _run_stage_scripts_in_context), 'disabled:'
# opts a stage out of the normal pipeline by default (see run_stages) --
# none of these are part of any one provider's own KEYS. Order matters:
# this is also the fixed display order used by _ordered_stage_section for
# --show-config (provider first -- it picks the class -- then description,
# disabled, scripts), before every provider-specific key (alphabetically).
GENERIC_STAGE_KEYS = ("provider", "description", "disabled", "scripts")


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
            f"stage '{stage.stage}': unknown key(s) {', '.join(unknown)} for provider '{stage.name}' -- "
            f"known: {', '.join(sorted(type(stage).KEYS)) or '(none)'}."
        )


def resolve_stage_section(stage, raw_section, config, ctx):
    """Resolve one stage's *raw* section into its complete effective one.

    Always given the section as the denver.yml spelled it, never a section
    this function already resolved: a resolver reads an unset key's default
    back as though the author had written it (``cfg.get("exe") or
    ctx.which(...)``), so feeding it its own output turns every default it
    ever computed into an explicit value that can no longer be revised.
    That distinction is what lets this run a second time, per stage, once
    earlier stages have changed the world -- see _run_stage_setup.

    'scripts:'/'disabled:' are filled in for every stage regardless of
    provider -- both are generic, provider-agnostic keys any stage's
    section may declare (see _run_stage_scripts_in_context and
    run_stages's 'disabled:' handling), not part of any one provider's own
    KEYS, so they belong here, not in Provider.resolve_defaults's default.
    """
    from providers.base import fill_unset

    validate_stage_section_keys(stage, raw_section)
    section = type(stage).resolve_defaults(ctx, raw_section, config)
    disabled = raw_section.get("disabled", False)
    if not isinstance(disabled, bool):
        die(f"stage '{stage.stage}': 'disabled:' must be true or false, got {disabled!r}")
    section["disabled"] = disabled
    description = raw_section.get("description")
    if description is not None and (
        not isinstance(description, list) or not all(isinstance(line, str) for line in description)
    ):
        die(f"stage '{stage.stage}': 'description:' must be a list of strings, got {description!r}")
    return fill_unset(section, ["scripts", "description"])


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
    from providers import make_stage

    for stage_id in config.get("stages") or []:
        stage = make_stage(stage_id, config)
        raw_section = config.get(stage_id) or {}
        ctx.raw_sections[stage_id] = copy.deepcopy(raw_section)
        config[stage_id] = resolve_stage_section(stage, raw_section, config, ctx)
    return config


# --------------------------------------------------------------------------- #
# Environment resolution
# --------------------------------------------------------------------------- #
def resolve_env_dir(env_arg):
    """Resolve the <env> argument to (env_dir, config_path).

    Accepts a path to an env directory (its denver.yml is used) or a path
    directly to a YAML config file (any name, e.g. denver.debug.yml -- lets
    a folder hold several denver.xxx.yml variants side by side). Mirrors
    resolve_import()'s own directory-or-file convention for 'import:'
    entries, so both the top-level <env> and imports resolve the same way.
    """
    candidate = Path(env_arg).expanduser()

    if not candidate.exists():
        die(f"environment '{env_arg}' not found. Give a path to an env directory or a denver.yml file.")
    if candidate.is_file():
        return candidate.parent.resolve(), candidate.resolve()
    env_dir = candidate.resolve()
    return env_dir, env_dir / CONFIG_NAME


def is_runnable_env(config_path):
    """An env is runnable unless its denver.yml sets ``runnable: false``.

    Used to reject starting a shared/base env directly (meant to be
    inherited via ``import:`` only) -- see its use in main() below.
    """
    return load_yaml(config_path).get("runnable", True) is not False


# --------------------------------------------------------------------------- #
# Provider orchestration (denver.yml-driven)
# --------------------------------------------------------------------------- #
def collect_import_dirs(config_path, _seen=None):
    """Directories of every env in the whole-file ``import:`` chain, nearest first.

    Breadth-first: the env's own direct imports, then their imports, etc.
    This is the search order ``Context.resolve_path`` uses to fall back to a
    base env's file when the leaf doesn't have one -- e.g. a conventional
    default like ``uv/skip-if.sh`` or ``conan/base_classes``. Walking the
    whole chain (not just the direct imports) is what makes those
    conventions work through multiple levels of ``import:`` stacking: each
    layer, closest to the leaf first, is checked in turn, so a more-derived
    layer's own file always wins over one further up the chain, and a layer
    that doesn't have one simply falls through to the next.
    """
    config_path = config_path.resolve()
    if _seen is None:
        _seen = set()
    if config_path in _seen:
        die(f"circular import detected at {config_path}")
    _seen.add(config_path)

    raw = load_yaml(config_path)
    base_dir = config_path.parent
    imported = [resolve_import(entry, base_dir) for entry in (raw.get("import", []) or [])]

    dirs = [p.parent for p in imported]
    for imported_path in imported:
        dirs += collect_import_dirs(imported_path, _seen)
    return dirs


def collect_hook_entries(config_path, name, _seen=None):
    """Collect hook ``name`` script entries across the whole-file ``import:`` chain, base-first.

    Returned as ``(base_dir, raw_value)`` pairs. Each layer in the chain (an
    env and everything it whole-file-imports) contributes its own explicit
    ``hooks: <name>:`` entry (a list of scripts, or a bare string for a
    single one) if it declares one -- so a stacked/inherited env's own hook
    is never silently lost just because a derived env also declares one.

    Nothing is discovered from the directory layout: a ``hooks/<name>.sh``
    (or ``hooks/<name>.user.sh``) sitting next to a ``denver.yml`` is only
    ever run if that ``denver.yml`` actually lists it.
    """
    config_path = config_path.resolve()
    if _seen is None:
        _seen = set()
    if config_path in _seen:
        die(f"circular import detected at {config_path}")
    _seen.add(config_path)

    raw = load_yaml(config_path)
    base_dir = config_path.parent
    entries = []

    for entry in raw.get("import", []) or []:
        imported_path = resolve_import(entry, base_dir)
        entries += collect_hook_entries(imported_path, name, _seen)

    own = (raw.get("hooks") or {}).get(name)
    if own:
        own_scripts = [own] if isinstance(own, str) else own
        entries += [(base_dir, script) for script in own_scripts]

    return entries


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

# what argparse stores for a bare '--run' (no name): list the names this env
# defines rather than running one. A sentinel object, not a string, so it can
# never collide with a name an env actually uses.
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
    (``../zephyr-devshell/denver.yml:conan``) makes the source section
    explicit -- pointing straight at a specific env's ``denver.yml`` and
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

    An entry may also point directly at a YAML file and/or name an explicit
    source section with ``path:section``::

        conan:
          import:
          - ../zephyr-devshell/denver.yml:conan

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
        merged = {}
        for ref in value["import"]:
            path, section = parse_section_import_ref(ref)
            src_path = resolve_import(path, env_dir)
            src_config = load_config(src_path)
            merged = deep_merge(merged, src_config.get(section or key) or {})
            extra_dirs.append(src_path.parent)
        overlay = {k: v for k, v in value.items() if k != "import"}
        result[key] = deep_merge(merged, overlay)
    return result, extra_dirs


def default_command(config):
    """Determine the interactive command when the user gave none."""
    if not sys.stdin.isatty():
        die("cannot determine command to run (non-interactive and no command given)")

    # 'command:' is the generic top-level default; a wrapper provider
    # (currently only 'docker') may contribute its own fallback via
    # <provider>.default-cmd, e.g. the command to land in once relocated
    # into a container. With neither set, fall back to the user's own shell
    # (not a specific one denver would otherwise be guessing).
    cmd = config.get("command") or (config.get("docker") or {}).get("default-cmd") or os.environ.get("SHELL") or "bash"
    return [cmd] if isinstance(cmd, str) else [str(c) for c in cmd]


def resolve_command(config, forwarded):
    """The command to run: ``forwarded`` verbatim, else the env's default command.

    ``forwarded`` is already the clean command (main() split it off argv's
    first literal '--' before any denver flag parsing even started, so
    there's no '--' marker left in it here to strip).
    """
    return list(forwarded) or default_command(config)


def reinvoke_command(
    config_path,
    forwarded,
    wrapper_stage_ids,
    *,
    until_stage=None,
    skip_stages=(),
    quiet=0,
    fast=False,
    force=False,
    ci=False,
    no_wait=False,
    start_time=None,
):
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
    ``until_stage``/``skip_stages`` are the user's *original* --until/--skip
    (consumed out of ``forwarded`` by the outer main(), same as
    quiet/fast/force/ci below) -- re-passed explicitly so a stage the user
    asked to skip stays skipped inside the wrapper too, instead of the inner
    denver re-computing 'stages:' from scratch with no memory of them and
    running it anyway.
    --quiet (repeated ``quiet`` times, so e.g. -qq's level 2 survives, not
    just a single -q)/--fast/--force/--ci are re-passed explicitly: all were
    consumed out of ``forwarded`` by the outer main(), so the inner denver
    wouldn't otherwise know to honour them too -- none of these are read
    back out of a real environment variable, so there's no other way for the
    inner process to inherit them. ``forwarded`` (already stripped of any
    '--' marker by the outer main(), see resolve_command) is re-introduced
    with a fresh '--' here, so the re-invoked denver's own argv splitting
    (main() splits on the first literal '--') separates it from denver's own
    flags again instead of trying to parse it as one of them.
    ``start_time`` (hidden --start-time flag) is the outer denver's own
    ``time.time()`` at the very start of run_stages() -- carried across so
    the "env started in Ns" line the inner denver prints right before
    launching the command reflects the *whole* startup (including the
    outer denver's own wrapper-stage work), not just the inner process's
    own, much shorter, wall-clock.
    """
    # the interpreter+script pair to re-run denver with, or the frozen
    # executable on its own -- see this function's docstring
    if getattr(sys, "frozen", False):
        launcher = [str(Path(sys.executable).resolve())]
    else:
        launcher = ["python3", str(Path(__file__).resolve())]
    filter_flags = []
    if until_stage:
        filter_flags += ["--until", until_stage]
    for stage_id in (*skip_stages, *wrapper_stage_ids):
        filter_flags += ["--skip", stage_id]
    extra_flags = (
        (["-q"] * quiet)
        + (["--fast"] if fast else [])
        + (["--force"] if force else [])
        + (["--ci"] if ci else [])
        + (["--no-wait"] if no_wait else [])
    )
    if start_time is not None:
        extra_flags += ["--start-time", repr(start_time)]
    command = ["--", *forwarded] if forwarded else []
    return [*launcher, str(config_path), *filter_flags, *extra_flags, *command]


def resolve_full_config(env_dir, config, config_path, *, quiet=0, fast=False, force=False, ci=False, dry_run=False):
    """Fully resolve an env's config for actual use.

    Section-level ('docker: import:') stacking, then every provider's
    defaults baked in centrally (see resolve_provider_defaults). This is the
    single place both --show-config and the real run get their config from,
    so they can never drift apart -- a provider's setup() never guesses a
    default itself, it just reads what's already there. Returns (config, ctx).
    """
    from providers import Context

    config, extra_dirs = expand_section_imports(config, env_dir)
    import_dirs = collect_import_dirs(config_path) + extra_dirs
    ctx = Context(
        DENVER_DIR,
        env_dir,
        config,
        config_path=config_path,
        import_dirs=import_dirs,
        quiet=quiet,
        fast=fast,
        force=force,
        ci=ci,
        dry_run=dry_run,
    )
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
    stage_ids = config.get("stages") or []
    if not stage_ids:
        die(f"env '{env_dir.name}' declares no 'stages:'")
    if until_stage:
        stage_ids = stage_ids[: stage_ids.index(until_stage) + 1]
    if skip_stages:
        drop = set(skip_stages)
        stage_ids = [s for s in stage_ids if s not in drop]
    if not stage_ids:
        die("--until/--skip filtered out every stage -- nothing left to run")
    return stage_ids


def _collect_stage_scripts(ctx, stage_ids, name):
    """Resolve every (stage_id, script_path) from each stage's ``scripts: <name>:`` list.

    Shared by run_named_scripts (via _run_stage_scripts_in_context): 'scripts:'
    is a generic, per-stage config key (any provider's section may declare
    it), not provider-specific -- like docker's env-scripts, entries are
    paths, resolved the same way (relative to the env dir, falling back to
    imported base env dirs).
    """
    resolved = []
    for stage_id in stage_ids:
        entry = (ctx.section(stage_id).get("scripts") or {}).get(name)
        if entry is None:
            continue
        if not isinstance(entry, list):
            die(f"stage '{stage_id}': 'scripts.{name}:' must be a list of strings, got {type(entry).__name__}")
        for script in entry:
            path = ctx.resolve_path(script)
            if not path.is_file():
                die(f"stage '{stage_id}': scripts.{name} script not found: {path}")
            resolved.append((stage_id, path))
    return resolved


def run_named_scripts(
    env_dir, config, config_path, name, *, until_stage=None, skip_stages=(), quiet=False, dry_run=False, no_wait=False
):
    """Run every (filtered) stage's ``scripts: <name>:`` entries, each in the context it actually needs.

    This is ``denver <env> --run <name>``; ``name`` is open-ended -- any
    string an env's ``scripts:`` sections happen to use (e.g. ``setup``,
    ``login``, or a project-specific one like ``migrate``), not a fixed,
    hard-coded set of names. Unlike the normal pipeline, this never
    builds/enters the environment or runs any provider's setup()/wrap() for
    its own sake -- but a *wrapper* stage's (e.g. docker) own entries run on
    the host, since that's where the wrapper itself operates, while a *setup*
    stage's entries need whatever that stage's own setup() would install
    (e.g. conan, only present once inside a docker-wrapped env's container).
    So when an active wrapper exists and any setup stage declares this name,
    the wrapper is prepared (its setup() runs, same as run_stages() would)
    and denver is re-invoked `--skip <that wrapper stage> --run <name>`
    inside it for the setup stages' own entries -- mirroring run_stages()'s
    own host/wrapper relocation exactly.

    Under ``dry_run`` each entry is printed rather than executed, with the
    same wrapper caveat as run_stages(): the reinvocation that would carry
    the setup stages' entries into the wrapper is itself not run.
    """
    from providers import make_stage
    from providers.context import dry_run_legend

    if dry_run:
        dry_run_legend()

    stage_ids = filtered_stage_ids(config, env_dir, until_stage, skip_stages)
    config, ctx = resolve_full_config(env_dir, config, config_path, quiet=quiet, dry_run=dry_run)
    ctx.acquire_lock(wait=not no_wait)
    ctx.ensure_state_dir()
    run_hook(ctx, config_path, "env")
    ctx.apply_env_map(config.get("env"))

    stages = [make_stage(stage_id, config) for stage_id in stage_ids]
    wrappers = [s for s in stages if s.kind == "wrapper"]
    setups = [s for s in stages if s.kind != "wrapper"]
    active_wrappers = [] if _wrappers_inactive(ctx) else wrappers

    def run_for(scoped_stage_ids):
        for stage_id, script in _collect_stage_scripts(ctx, scoped_stage_ids, name):
            info(f"{name}-script '{stage_id}': {script}")
            ctx.run([str(script)])

    if not active_wrappers:
        run_for([s.stage for s in setups])
        return

    # the wrapper's own entries (e.g. `docker login` to a private registry)
    # run here, on the host, before preparing it
    run_for([w.stage for w in active_wrappers])

    if not _collect_stage_scripts(ctx, [s.stage for s in setups], name):
        return  # nothing to relocate into the wrapper for

    # same limit as run_stages(): the reinvocation carrying these entries
    # into the wrapper is itself not run under --dry-run, so they can't be
    # previewed -- see _run_stages_via_wrapper for why not.
    if ctx.dry_run:
        ctx.dry_note(
            "!",
            f"'{name}' scripts of stages {', '.join(s.stage for s in setups)} run inside "
            f"{', '.join(w.stage for w in active_wrappers)} and are not previewed -- "
            f"re-run with --skip {' --skip '.join(w.stage for w in active_wrappers)} to see them",
        )

    stage_index = {s.stage: i for i, s in enumerate(stages, 1)}
    for w in active_wrappers:
        _run_stage_setup(
            ctx, config, config_path, w, quiet=quiet, stage_index=stage_index[w.stage], stage_count=len(stages)
        )

    cmd = ["python3", str(Path(__file__).resolve()), str(config_path), "--run", name]
    if quiet:
        cmd.append("-q")
    if until_stage:
        cmd += ["--until", until_stage]
    for stage_id in skip_stages:
        cmd += ["--skip", stage_id]
    for w in active_wrappers:
        cmd += ["--skip", w.stage]
    _mark_relocated(ctx, active_wrappers)
    for w in reversed(active_wrappers):
        ctx.stage_index, ctx.stage_count = stage_index[w.stage], len(stages)
        ctx.stage_id = w.stage
        cmd = w.wrap(ctx, cmd)
    ctx.exec(cmd)


def list_named_scripts(env_dir, config_path, *, until_stage=None, skip_stages=()):
    """Print every ``scripts: <name>:`` an env defines, grouped by name -- ``denver <env> --run``.

    ``--run``'s names are deliberately open-ended (see run_named_scripts):
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
    config = load_config(config_path)
    config, _ = expand_section_imports(config, env_dir)
    stage_ids = filtered_stage_ids(config, env_dir, until_stage, skip_stages)

    by_name = {}
    for stage_id in stage_ids:
        scripts = (config.get(stage_id) or {}).get("scripts") or {}
        for name, entries in scripts.items():
            by_name.setdefault(name, []).append((stage_id, len(entries or [])))

    if not by_name:
        print(f"env '{env_dir.name}' defines no 'scripts:' entries -- nothing to --run", file=sys.stderr)
        return
    print(f"available --run names for env '{env_dir.name}':", file=sys.stderr)
    for name in sorted(by_name):
        stages = ", ".join(f"{stage} ({count} script{'s' if count != 1 else ''})" for stage, count in by_name[name])
        print(f"  {name:<12} {stages}", file=sys.stderr)


def _run_stage_setup(ctx, config, config_path, provider, *, quiet, stage_index=1, stage_count=1):
    """Resolve defaults, then run one stage's provider.setup(), hooks and all.

    Shared by run_stages() (every stage) and run_named_scripts() (a wrapper
    stage only, to relocate a setup stage's scripts into it -- see there).
    ``stage_index``/``stage_count`` (this stage's position among the stages
    running in *this* process -- see run_stages()) feed banner()'s '[i/n]'.
    """
    from providers.context import stage_banner

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
    run_hook(ctx, config_path, f"pre-{provider.stage}")
    start = time.time()
    provider.setup(ctx)
    duration = time.time() - start
    ctx.stage_timings.append((provider.stage, f"{duration:.1f}s"))
    if quiet < 2:
        print(
            f"\033[94mINFO: stage '{provider.stage}' ({provider.name}) finished in {duration:.2f}s\033[39m",
            file=sys.stderr,
        )
    record_stage_performance(ctx, provider, start, duration)
    run_hook(ctx, config_path, f"post-{provider.stage}")


def _print_stage_summary(ctx):
    """Print what each stage cost, in pipeline order, right above the 'env started' line.

    The per-stage 'finished in Ns' lines are scattered through a run's output
    -- often thousands of lines of build noise apart -- so the question they
    answer ("why did this take four minutes?") is only answerable by
    scrolling. Restated here as one block, in the order the stages ran.

    Stages that did not run keep their row, carrying the reason instead of a
    duration, so the summary matches the '[i/n]' trail above rather than
    silently shrinking.
    """
    if not ctx.stage_timings:
        return
    width = max(len(stage) for stage, _ in ctx.stage_timings)
    for stage, outcome in ctx.stage_timings:
        print(f"\033[94m  {stage:<{width}}  {outcome}\033[39m", file=sys.stderr)


def _print_env_started(ctx, start_time):
    """Print a boxed, blue 'INFO: env <name> started in Ns' line to stderr, right before the resolved command launches.

    Under --dry-run the env was never started and the elapsed time is the
    cost of printing commands, not of running them -- so it says what
    actually happened instead of quoting a meaningless duration.
    """
    _print_stage_summary(ctx)
    if ctx.dry_run:
        text = f"INFO: env {ctx.env_name} NOT started (--dry-run)"
    else:
        text = f"INFO: env {ctx.env_name} started in {time.time() - start_time:.2f}s"
    line = "-" * (len(text) + 4)
    print(f"\033[94m{line}\n| {text} |\n{line}\033[39m", file=sys.stderr)


def run_stages(
    env_dir,
    config,
    config_path,
    forwarded,
    *,
    until_stage=None,
    skip_stages=(),
    quiet=0,
    fast=False,
    force=False,
    ci=False,
    dry_run=False,
    no_wait=False,
    start_time=None,
):
    """Build/enter the environment via its stages, then exec the command.

    ``start_time`` is this whole startup's own ``time.time()`` origin,
    threaded across a wrapper reinvocation (see reinvoke_command) for the
    final "env started in Ns" line right before the resolved command
    actually launches -- it must reflect the *whole* startup, not just the
    reinvoked (inner) process's own, much shorter, wall-clock.

    banner()'s '[i/n]' numbers every stage the env *declares* in 'stages:'
    (see ``all_stage_ids`` below), not just the ones actually running -- a
    stage --until/--skip filtered out still gets counted and gets its own
    one-line "skipped by --until/--skip" banner, so e.g. skipping the last of
    5 declared stages shows '[5/5] ... skipped by --skip', never silently
    drops to a 4-stage trail. This numbering needs no cross-process
    plumbing (unlike start_time): both the outer and any reinvoked inner
    denver derive it identically, straight from the *same* denver.yml's
    'stages:' list, which --until/--skip never changes.

    Under ``dry_run`` every stage still runs in order and still resolves its
    own config -- only the commands and file writes it would perform are
    printed instead of performed (see Context.run). The one thing a dry run
    cannot show is what happens *inside* an active wrapper: relocating into
    it is itself one of the commands not being run, so
    _run_stages_via_wrapper stops at the reinvocation and says so.
    """
    from providers import make_stage
    from providers.context import dry_run_legend

    if start_time is None:
        start_time = time.time()

    if dry_run:
        dry_run_legend()

    all_stage_ids = config.get("stages") or []
    stage_ids = filtered_stage_ids(config, env_dir, until_stage, skip_stages)
    config, ctx = resolve_full_config(
        env_dir, config, config_path, quiet=quiet, fast=fast, force=force, ci=ci, dry_run=dry_run
    )
    # before any stage touches shared state, and before the wrapper relocates
    ctx.acquire_lock(wait=not no_wait)

    ctx.ensure_state_dir()

    # hooks.env sources a script once, before anything else -- its exports
    # (and the declarative 'env:' map applied right after) are visible to
    # every stage and the final command, i.e. they apply to the whole devshell.
    run_hook(ctx, config_path, "env")
    ctx.apply_env_map(config.get("env"))

    # each entry in 'stages:' is a pipeline stage (a provider type + config
    # id), run in order -- so an env can order conan before uv, have several
    # uv stages, etc. Instantiated for *every* declared id (not just the
    # runnable ones) purely to learn each skipped stage's kind (wrapper vs.
    # setup) and declared position -- make_stage() itself does no I/O.
    all_stages = [make_stage(stage_id, config) for stage_id in all_stage_ids]
    stage_index = {s.stage: i for i, s in enumerate(all_stages, 1)}
    total = len(all_stages)
    # 'disabled: true' (a stage's own config, resolved above -- so this
    # reflects import:/-c overrides too) drops a stage from the runnable set
    # the same way --skip does, but -- unlike --skip/--until -- never drops
    # its section from --show-config/filtered_stage_ids: it's a declarative
    # default about the pipeline, not a per-invocation exclusion, so it stays
    # visible and inspectable (and overridable via '-c <stage>.disabled=false').
    runnable = {s for s in stage_ids if not (config.get(s) or {}).get("disabled")}

    wrappers = [s for s in all_stages if s.kind == "wrapper" and s.stage in runnable]
    setups = [s for s in all_stages if s.kind != "wrapper" and s.stage in runnable]
    skipped_wrappers = [s for s in all_stages if s.kind == "wrapper" and s.stage not in runnable]
    skipped_setups = [s for s in all_stages if s.kind != "wrapper" and s.stage not in runnable]

    # A wrapper (e.g. docker) is active only on the host: skip it yourself
    # (e.g. `--skip docker`) to run on the host instead, or it's already
    # excluded above by stage filtering; also inactive once already inside it.
    active_wrappers = [] if _wrappers_inactive(ctx) else wrappers

    # a stage past the --until cut-off was dropped by --until; a stage that
    # survived --until/--skip filtering (still in stage_ids) but is missing
    # from `runnable` was dropped by its own 'disabled: true'; anything else
    # missing from `runnable` was named by an explicit --skip.
    cutoff = all_stage_ids.index(until_stage) if until_stage in all_stage_ids else None
    skip_state = _StageSkipState(
        stage_index=stage_index,
        total=total,
        stage_ids=stage_ids,
        cutoff=cutoff,
        all_stage_ids=all_stage_ids,
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
            until_stage=until_stage,
            skip_stages=skip_stages,
            quiet=quiet,
            fast=fast,
            force=force,
            ci=ci,
            no_wait=no_wait,
            start_time=start_time,
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
            quiet=quiet,
            start_time=start_time,
        )


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
    from providers.context import RELOCATED_VAR

    ctx.set(RELOCATED_VAR, ",".join(w.stage for w in wrappers))


class _StageSkipState:
    """Everything ``_show_skipped`` needs to explain why a stage isn't running -- see run_stages.

    ``all_stages`` is every declared stage, in 'stages:' order: it is what
    lets both run paths walk the pipeline once, in order, rather than
    running the stages first and reporting the skipped ones afterwards.
    """

    def __init__(self, *, stage_index, total, stage_ids, cutoff, all_stage_ids, all_stages):
        self.stage_index = stage_index
        self.total = total
        self.stage_ids = stage_ids
        self.cutoff = cutoff
        self.all_stage_ids = all_stage_ids
        self.all_stages = all_stages


def _show_skipped(ctx, skipped, skip_state):
    """Print a "skipped by ..." banner for each stage in ``skipped`` (disabled: true, --until, or --skip)."""
    from providers.context import skip_banner

    for s in skipped:
        if s.stage in skip_state.stage_ids:
            reason = "skipped (disabled: true)"
        else:
            past_cutoff = skip_state.cutoff is not None and skip_state.all_stage_ids.index(s.stage) > skip_state.cutoff
            reason = "skipped by --until" if past_cutoff else "skipped by --skip"
        ctx.stage_index, ctx.stage_count = skip_state.stage_index[s.stage], skip_state.total
        ctx.stage_timings.append((s.stage, reason))
        skip_banner(ctx, s.stage, reason)


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
    until_stage,
    skip_stages,
    quiet,
    fast,
    force,
    ci,
    no_wait,
    start_time,
):
    """Host side: prepare the wrapper(s), then relocate execution into them (see run_stages)."""
    # Same single ordered walk as _run_stages_directly, for the same reason:
    # each declared stage reports in its own pipeline position. A *runnable*
    # setup stage is passed over silently here -- it runs inside the wrapper,
    # and the re-invoked denver banners it there. Its skipped siblings are
    # likewise left to that inner run, unless nothing will re-invoke at all
    # (a pure wrapper), in which case this is the only chance to show them.
    active_wrapper_ids = {w.stage for w in active_wrappers}
    skipped_wrapper_ids = {s.stage for s in skipped_wrappers}
    skipped_setup_ids = {s.stage for s in skipped_setups}
    for stage in skip_state.all_stages:
        if stage.stage in active_wrapper_ids:
            _run_stage_setup(
                ctx,
                config,
                config_path,
                stage,
                quiet=quiet,
                stage_index=skip_state.stage_index[stage.stage],
                stage_count=skip_state.total,
            )
        elif stage.stage in skipped_wrapper_ids or (stage.stage in skipped_setup_ids and not setups):
            _show_skipped(ctx, [stage], skip_state)
    if setups:
        # setup providers run *inside* the wrapper: re-invoke denver there
        # -- it recomputes skipped_setups identically (same denver.yml,
        # same --until/--skip) and shows those banners itself.
        #
        # Under --dry-run that reinvocation is one of the commands *not*
        # being run, so those stages are never reached and can't be
        # previewed here. Nor could they be by passing --dry-run inward:
        # getting inside the wrapper means really starting it, which is
        # exactly what this run promised not to do (and its image may be
        # something an earlier printed-not-run `compose build` would have
        # produced). Said out loud rather than left as a silently short
        # pipeline -- run `--skip <wrapper>` to preview those stages on the
        # host instead.
        if ctx.dry_run:
            ctx.dry_note(
                "!",
                f"stages {', '.join(s.stage for s in setups)} run inside "
                f"{', '.join(w.stage for w in active_wrappers)} and are not previewed -- "
                f"re-run with --skip {' --skip '.join(w.stage for w in active_wrappers)} to see them",
            )
        cmd = reinvoke_command(
            config_path,
            forwarded,
            [w.stage for w in active_wrappers],
            until_stage=until_stage,
            skip_stages=skip_stages,
            quiet=quiet,
            fast=fast,
            force=force,
            ci=ci,
            no_wait=no_wait,
            start_time=start_time,
        )
    else:
        # pure wrapper: relocate the user's command (or default) directly.
        # skipped_setups were already shown in pipeline position by the walk
        # above, since nothing will re-invoke to show them.
        cmd = resolve_command(config, forwarded)
    _mark_relocated(ctx, active_wrappers)
    for w in reversed(active_wrappers):
        ctx.stage_index, ctx.stage_count = skip_state.stage_index[w.stage], skip_state.total
        ctx.stage_id = w.stage
        cmd = w.wrap(ctx, cmd)
    if not setups and quiet < 2:
        _print_env_started(ctx, start_time)
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
    setup_ids = {n.stage for n in setups}
    skipped_wrapper_ids = {s.stage for s in skipped_wrappers}
    skipped_setup_ids = {s.stage for s in skipped_setups}
    for stage in skip_state.all_stages:
        if stage.stage in setup_ids:
            _run_stage_setup(
                ctx,
                config,
                config_path,
                stage,
                quiet=quiet,
                stage_index=skip_state.stage_index[stage.stage],
                stage_count=skip_state.total,
            )
        elif stage.stage in skipped_setup_ids or (stage.stage in skipped_wrapper_ids and not ctx.relocated):
            _show_skipped(ctx, [stage], skip_state)
    if quiet < 2:
        _print_env_started(ctx, start_time)
    if not quiet:
        print_logo()
    cmd = resolve_command(config, forwarded)
    run_hook(ctx, config_path, "pre-cmd")
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
        return {k: _sorted_nested(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_sorted_nested(v) for v in value]
    return value


def _ordered_stage_section(section):
    """Order one resolved stage section for --show-config.

    GENERIC_STAGE_KEYS first (whichever of those keys are present, in that
    fixed order), then every remaining (provider-specific) key
    alphabetically. Each value is still recursively sorted via
    _sorted_nested -- only this section's own top level is special-cased.
    """
    ordered = {}
    for key in GENERIC_STAGE_KEYS:
        if key in section:
            ordered[key] = _sorted_nested(section[key])
    for key in sorted(k for k in section if k not in GENERIC_STAGE_KEYS):
        ordered[key] = _sorted_nested(section[key])
    return ordered


def show_config(env_dir, config, config_path, until_stage=None, skip_stages=()):
    """Print the fully resolved config as YAML -- exactly what the real run would use.

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
    """
    resolved, ctx = resolve_full_config(env_dir, config, config_path)
    stage_ids = filtered_stage_ids(config, env_dir, until_stage, skip_stages)
    for dropped in set(resolved.get("stages") or []) - set(stage_ids):
        resolved.pop(dropped, None)
    resolved["stages"] = stage_ids
    resolved["hooks"] = resolve_hooks(ctx, config_path, stage_ids)

    pinned_keys = ("version", "denver-version")
    generic_keys = sorted(k for k in resolved if k not in ("stages", *pinned_keys) and k not in stage_ids)
    ordered = {}
    for key in pinned_keys:
        if key in resolved:
            ordered[key] = _sorted_nested(resolved[key])
    ordered.update((k, _sorted_nested(resolved[k])) for k in generic_keys)
    ordered["stages"] = resolved["stages"]
    for stage_id in stage_ids:
        ordered[stage_id] = _ordered_stage_section(resolved[stage_id])

    print(yaml.safe_dump(ordered, sort_keys=False, default_flow_style=False))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_arg_parser():
    """The argparse.ArgumentParser for every denver-own flag (not the forwarded command).

    A fresh instance per call (see main()), so its 'append' actions' default
    lists are never shared/mutated across repeated main() calls in the same
    process (as happens across denver.main() calls in the test suite).
    ``add_help=False``: denver prints its own help (the module docstring, via
    -h/--help handled explicitly in main()), not argparse's auto-generated
    one -- but every other flag is a normal argparse action, so unknown
    flags, a missing required value, etc. are all argparse's own problem to
    report (its usual `usage: ...` + `error: ...` on stderr, exit code 2).
    """
    parser = argparse.ArgumentParser(prog="denver", add_help=False)
    parser.add_argument("env", nargs="?", help="path to an env directory or a denver.yml file")
    parser.add_argument("-h", "--help", action="store_true", help="show this help and exit")
    parser.add_argument("--version", action="store_true", help="show the installed denver version and exit")
    parser.add_argument("--license", action="store_true", help="show denver's LICENSE (Apache-2.0) and exit")
    parser.add_argument("--show-config", action="store_true", help="print the final deep-merged denver.yml and exit")
    parser.add_argument(
        "--run",
        metavar="NAME",
        nargs="?",
        const=LIST_SCRIPTS,
        help="run each stage's 'scripts: NAME:' entries, then exit (e.g. 'setup', 'login'); "
        "with no NAME, list the names this env defines",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="count",
        default=0,
        help="suppress denver's own output (repeatable: -q keeps the stage banner visible, -qq silences "
        "everything, only the launched command speaks)",
    )
    # --fast and --force ask for opposite things ("don't build anything" vs
    # "rebuild everything"), and --fast wins by construction: every provider
    # takes its --fast path before ctx.force is ever read, silently
    # discarding the --force. Rejected here rather than resolved, because
    # either resolution would be a guess about which one the user meant.
    # A group (not a hand-written check) so argparse reports it itself, the
    # same way it reports every other malformed invocation.
    rebuild = parser.add_mutually_exclusive_group()
    rebuild.add_argument("--fast", action="store_true", help="only activate what's already built; never (re-)build it")
    rebuild.add_argument(
        "--force",
        action="store_true",
        help="force expensive recomputation (recreate venv, rerun west update, ...), bypassing every "
        "checksum/skip-if-based skip",
    )
    parser.add_argument(
        "--ci", action="store_true", help="CI mode: swap in narrower/faster args (e.g. a shallow `west update`)"
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="fail instead of waiting when another denver run already holds this env",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what each stage would run instead of running it: no command is executed for its effect, "
        "nothing is written, and the final command is printed rather than launched (read-only queries and "
        "sourced scripts do still run -- they are what the shown commands are derived from)",
    )
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
    # internal-only: how reinvoke_command() carries run_stages()'s own
    # startup clock across a wrapper reinvocation, for the "env started in
    # Ns" line -- never meant to be typed by a user, hence SUPPRESS instead
    # of a real --help entry.
    parser.add_argument("--start-time", type=float, default=None, help=argparse.SUPPRESS)
    return parser


def print_help(parser):
    """Print the logo, argparse's own usage/options summary, then this module's short docstring.

    Shown identically for a bare no-args invocation and an explicit
    -h/--help (see main()) -- argparse's summary is the actual flag-by-flag
    reference; the docstring below it is just a short synopsis plus a
    pointer to README.md for the full behavioural detail (the forwarded-command
    convention, -c's += semantics, the wrapper relocation model, ...).
    """
    print_logo()
    print(parser.format_help())
    print(__doc__.strip())


def _command_failure_message(exc):
    """Render a CalledProcessError as denver's own error text, including whatever output the call captured.

    A ``capture=True`` call (Context.run) holds the failing command's own
    stdout/stderr in the exception rather than having printed it, so it is
    appended here -- that message is usually the only thing that explains
    the failure at all. A call that inherited stdout/stderr carries nothing
    here and needs nothing: it already printed where the user could see it.
    """
    command = " ".join(str(c) for c in exc.cmd) if isinstance(exc.cmd, (list, tuple)) else str(exc.cmd)
    lines = [f"command failed (exit {exc.returncode}): {command}"]
    for stream in (exc.stdout, exc.stderr):
        if not stream:
            continue
        text = stream.decode(errors="replace") if isinstance(stream, bytes) else str(stream)
        if text.strip():
            lines.append(text.rstrip())
    return "\n".join(lines)


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
    return 0


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
        print_help(parser)
        return True

    if args.version:
        print(f"denver {package_version() or UNKNOWN_VERSION}")
        return True

    if args.license:
        text = license_text()
        if text is None:
            die("LICENSE not found -- neither a checkout nor installed package metadata has it")
        print(text)
        return True

    return False


def _run_cli(argv=None):
    """Parse argv, resolve the env, and either exec its command or dispatch a denver-only subcommand.

    ``argv`` defaults to ``sys.argv[1:]`` (real CLI invocation); tests pass
    an explicit list instead. Never returns at all when the resolved command
    replaces this process (``os.execvpe``, via ``Context.exec``); otherwise
    returns normally on success, or exits via ``die()``.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_arg_parser()

    if not argv:
        print_help(parser)
        return

    head, forwarded = _split_argv(argv)
    args = parser.parse_args(head)

    if _handle_info_flags(args, parser):
        return

    if args.env is None:
        die("no environment given -- see `denver --help`")

    env_dir, config_path = resolve_env_dir(args.env)
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

    if args.show_config:
        show_config(env_dir, config, config_path, until_stage=args.until, skip_stages=args.skip)
        return

    if args.run == LIST_SCRIPTS:
        list_named_scripts(env_dir, config_path, until_stage=args.until, skip_stages=args.skip)
        return

    if args.run:
        run_named_scripts(
            env_dir,
            config,
            config_path,
            args.run,
            until_stage=args.until,
            skip_stages=args.skip,
            quiet=args.quiet,
            dry_run=args.dry_run,
            no_wait=args.no_wait,
        )
        return

    # 'runnable: false' marks a shared/base env (meant to be inherited via
    # import:, not started directly). --show-config/--setup/--login above
    # are diagnostic, not "running", so they're exempt: inspecting a base
    # env's resolved config is still useful.
    if config_path.is_file() and not is_runnable_env(config_path):
        die(f"env '{env_dir.name}' sets 'runnable: false' -- it's meant to be imported, not started directly.")

    if not config.get("stages"):
        die(f"env '{env_dir.name}' declares no 'stages:' in its {CONFIG_NAME}")
    run_stages(
        env_dir,
        config,
        config_path,
        forwarded,
        until_stage=args.until,
        skip_stages=args.skip,
        quiet=args.quiet,
        fast=args.fast,
        force=args.force,
        ci=args.ci,
        dry_run=args.dry_run,
        no_wait=args.no_wait,
        start_time=args.start_time,
    )


if __name__ == "__main__":
    sys.exit(main())
