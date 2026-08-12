#!/usr/bin/env python3
"""Catalog tool: prepares conan remotes, then exports/creates/uploads recipes from a recipe catalog.

Invoked by providers.conan.ConanProvider as a subprocess -- see
ConanProvider's module docstring for the denver.yml keys that route to
--prepare/--export/--create/--upload/--ci here. Also runnable standalone for
maintaining a recipe catalog outside of a denver run (--recipes/--remote).

One invocation handles one denver ``conanfiles:`` unit: every
``--recipes-dir`` given (repeatable) is resolved into a *single* catalog, so
recipes in one dir may require recipes in another.

The catalog is built in memory (build_catalog.build()) and consumed straight
from there; it is only written to disk when ``--export-catalog PATH`` says
where (the unit's ``catalog:`` in denver.yml). ``--no-generate`` is the
mirror image: skip the build and read an existing catalog.yml given with
``--catalog-yml``.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import getpass
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import colorama
import yaml
from conan.api.conan_api import ConanAPI
from conan.api.model import PkgReference, RecipeReference, Remote
from conan.cli.commands.test import run_test
from conan.internal.errors import AuthenticationException, ConanConnectionError, ConanException, NotFoundException
from conan.internal.util.files import load

CONANFILE_NAME = 'conanfile.py'


@functools.cache
def _real_conan_api():
    """Construct the real ConanAPI on first use, not at import time.

    ConanAPI() has real side effects (it migrates/creates the conan home
    directory on disk) -- constructing it just by importing this module
    would do that to whatever machine imports it, including a test run that
    never touches conan for real. functools.cache makes this a memoized
    singleton with no hand-rolled instance bookkeeping.
    """
    return ConanAPI()


class _ConanAPIProxy:
    """Delegates every attribute access to the lazily-constructed real ConanAPI.

    Every existing ``conan_api.<attr>`` call site keeps working unchanged;
    tests instead monkeypatch the module-level ``conan_api`` name outright.
    """

    def __getattr__(self, name):
        return getattr(_real_conan_api(), name)


conan_api = _ConanAPIProxy()


class CatalogError(Exception):
    """Raised for catalog/recipe resolution failures in this script."""


# Note: PYTHONPATH will be set later in main(), if --base-classes-dir is given


@contextlib.contextmanager
def redirect():
    """Temporarily redirect stdout and stderr to /dev/null (conan's own graph-loading calls are noisy).

    Every call site wants both streams silenced together -- no caller has
    ever needed just one -- so this isn't parametrised per-stream.
    """
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    with contextlib.ExitStack() as stack:
        devnull = stack.enter_context(Path(os.devnull).open('w'))
        sys.stdout = devnull
        sys.stderr = devnull
        try:
            yield
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr


def print_banner(text):
    """Print ``text`` in a colored box, as a visual step marker."""
    divider = "-----------------------------------------------------"
    box = "\n".join(["", f"{colorama.Fore.GREEN}{divider}", str(text), divider])
    print(f"{box}{colorama.Style.RESET_ALL}")


def _prompt_and_login(remote):
    """Prompt for a username/password on stdin and log in to ``remote`` with them directly."""
    print(f"Enter credentials for conan remote '{remote.name}' ({remote.url}):")
    username = input("Username: ")
    password = getpass.getpass("Password: ")
    conan_api.remotes.user_login(remote, username, password)


def authenticate_remote(remote, *, force=False):
    """conan_api.remotes.user_auth(remote), retrying with an interactive prompt on AuthenticationException.

    conan's own credential resolution (auth-plugin -> credentials.json ->
    CONAN_LOGIN_USERNAME/CONAN_PASSWORD -> its own interactive prompt) stops
    at the first non-interactive source that *matches* -- even a stale/wrong
    one -- and never falls through to its own prompt in that case (see
    conan/internal/rest/remote_credentials.py). This is denver's own
    fallback for exactly that: on AuthenticationException, if stdin is a
    TTY, prompt for credentials ourselves (bypassing whatever non-interactive
    source just failed) and retry once via user_login(). Non-interactive
    (no TTY, e.g. CI) re-raises instead of hanging on a prompt nobody can
    answer.
    """
    try:
        conan_api.remotes.user_auth(remote, force=force)
    except AuthenticationException:
        if not sys.stdin.isatty():
            raise
        print(f"Remote '{remote.name}' needs authentication and the stored credentials didn't work.")
        _prompt_and_login(remote)


def _default_profiles():
    """The default (host, build) Profile pair, as consulted by both graph-loading functions below."""
    host = conan_api.profiles.get_profile([conan_api.profiles.get_default_host()])
    build = conan_api.profiles.get_profile([conan_api.profiles.get_default_build()])
    return host, build


def get_deps_graph_remote(reference: RecipeReference):
    """Resolve ``reference`` against the configured remotes, authenticating to each one first."""
    lockfile = conan_api.lockfile.get_lockfile()
    profile_host, profile_build = _default_profiles()
    remotes = conan_api.remotes.list(only_enabled=True)
    for remote in remotes:
        authenticate_remote(remote)

    with redirect():
        deps_graph = conan_api.graph.load_graph_requires(
            [reference],
            [],
            profile_host,
            profile_build,
            lockfile,
            remotes,
            False,
            check_updates=True,
        )
        # NOTE: analyze_binaries()/install_sources() intentionally omitted -- not needed yet.
    return deps_graph  # noqa: RET504  # named for the deferred calls noted above, once they're enabled


def needs_export(reference: RecipeReference) -> bool:
    """True if ``reference`` isn't in the local cache and isn't resolvable from a remote either."""
    if get_cache_path(reference):
        return False
    return bool(get_deps_graph_remote(reference).error)


def get_deps_graph_local(recipe_path, reference: RecipeReference):
    """Resolve ``reference`` (which must already be exported to the local cache) from ``recipe_path``."""
    lockfile = conan_api.lockfile.get_lockfile()
    profile_host, profile_build = _default_profiles()

    with redirect():
        deps_graph = conan_api.graph.load_graph_consumer(
            os.fspath(recipe_path),
            reference.name,
            reference.version,
            reference.user,
            reference.channel,
            profile_host,
            profile_build,
            lockfile,
            [],
            False,
            is_build_require=True,
        )
        conan_api.graph.analyze_binaries(deps_graph, remotes=[])
    return deps_graph


def get_pref_from_ref(recipe_path, reference: RecipeReference) -> PkgReference:
    """Resolve a recipe reference to its concrete package reference (adds package_id/revision)."""
    try:
        deps_graph = get_deps_graph_local(recipe_path, reference)
    except Exception as e:
        print(e, file=sys.stderr)
        raise CatalogError(f"Cannot resolve {reference!r}. Please export all recipes to local cache.") from e
    return PkgReference(reference, deps_graph.root.pref.package_id, deps_graph.root.pref.revision)


def index_recipe_dirs(recipes_dirs) -> dict[tuple[str, str], Path]:
    """Map every recipe found under ``recipes_dirs`` to its directory, keyed by (name, version).

    Recipes are discovered the same way build_catalog does -- by their
    conandata.yml, wherever it sits -- rather than by assuming
    ``<recipes-dir>/<name>/<version>/``. So a ``recipe-dirs:`` entry may be a
    whole recipe tree *or* a single recipe (`.../recipes/cmake`), and a
    unit's dirs can be laid out differently from one another. The
    ``<name>/<version>/`` directory layout itself is still enforced, by
    build_catalog's Recipe.check().
    """
    index: dict[tuple[str, str], Path] = {}
    for recipes_dir in recipes_dirs:
        for conandata in sorted(Path(recipes_dir).glob("**/conandata.yml")):
            recipe_dir = conandata.parent
            index.setdefault((recipe_dir.parent.name, recipe_dir.name), recipe_dir)
    return index


def get_recipes_from_entries(recipes_dirs, entries) -> dict[Path, PkgReference]:
    """Turn a catalog ({'name/version': reference}) into {conanfile_path: RecipeReference}.

    A catalog covers every dir of its unit at once, so a reference on its own
    doesn't say which dir it came from -- index_recipe_dirs() locates each
    one. Dotted metadata keys (e.g. ``.version``) are skipped: they describe
    the catalog itself, not a recipe.
    """
    recipes_dirs = [Path(d) for d in recipes_dirs]
    index = index_recipe_dirs(recipes_dirs)
    recipes_ref = {}
    for key, ref_str in entries.items():
        if key.startswith('.'):
            continue
        ref = RecipeReference.loads(ref_str)
        # not found (a stale checked-in catalog, --no-generate): fall back to
        # the conventional layout under the first dir, so whatever fails next
        # fails against a concrete path instead of a None.
        recipe_dir = index.get((ref.name, str(ref.version))) or recipes_dirs[0] / ref.name / str(ref.version)
        recipes_ref[(recipe_dir / CONANFILE_NAME).absolute()] = ref
    return recipes_ref


def read_catalog(catalog_yml_path) -> dict:
    """Load a catalog.yml from disk into {'name/version': reference} -- --no-generate's checked-in catalog."""
    return yaml.safe_load(load(catalog_yml_path))


def get_recipes_prefs(recipes_ref):
    """Resolve every {path: RecipeReference} entry to its concrete {path: PkgReference}."""
    return {recipe_path.absolute(): get_pref_from_ref(recipe_path, ref) for recipe_path, ref in recipes_ref.items()}


def _resolve_recipe_arg(recipe):
    """Turn a CLI 'recipes' arg (a conanfile.py path or its containing dir) into an absolute conanfile.py path."""
    path = Path(recipe)
    if path.is_dir():
        path = path / CONANFILE_NAME
    return path.resolve()


def handle_args_recipe(all_recipes, recipes: list[str]) -> dict[Path, PkgReference]:
    """Filter ``all_recipes`` down to just the recipe paths/names given on the command line."""
    filtered = {}
    for recipe in recipes:
        recipe_path = _resolve_recipe_arg(recipe)
        if recipe_path not in all_recipes:
            raise CatalogError(f"Recipe path {recipe_path} does not exist!")
        filtered[recipe_path] = all_recipes[recipe_path]
    return filtered


def conan_list(pref, remotes=None) -> tuple[list, Remote | None]:
    """Find ``pref``'s package revisions locally (remotes=None) or in the first matching, enabled remote."""
    for remote_name in remotes or [None]:
        conan_remote = conan_api.remotes.get(remote_name) if remote_name else None
        if conan_remote and conan_remote.disabled:
            continue
        try:
            revisions = conan_api.list.package_revisions(pref, remote=conan_remote)
        except NotFoundException:
            continue
        if revisions:
            return revisions, conan_remote
    return [], None


def get_cache_path(ref_or_pref):
    """Return the local cache path for a recipe or package reference, or None if it isn't cached.

    Only ``ConanException`` (conan's own "not found"/"folder does not exist"
    signal, see conan.api.subapi.cache) is treated as "not cached" -- a
    permission error or a corrupt cache raises a real exception instead of
    silently looking like a cache miss.
    """
    if type(ref_or_pref) is PkgReference:
        func = conan_api.cache.package_path
    elif type(ref_or_pref) is RecipeReference:
        func = conan_api.cache.export_path
    else:
        raise CatalogError(f"type {type(ref_or_pref)} not supported")
    with contextlib.suppress(ConanException):
        return Path(func(ref_or_pref))
    return None


def _import_build_catalog():
    """Import build_catalog late -- it must not be imported at module import time.

    build_catalog imports get_rrev, which does ``import DenverConanFile`` at
    *its* own import time; that only resolves once main() has put
    --base-classes-dir on sys.path, so importing this chain from the top of
    this module would silently leave DenverConanFile as None. Both spellings
    are tried for the same reason build_catalog itself tries both: this file
    runs as a script (sys.path[0] is its own directory) *and* is imported as
    providers.conan_scripts.recipes by denver's own tests.
    """
    try:
        import build_catalog  # type: ignore[import-not-found]
    except ImportError:
        from . import build_catalog
    return build_catalog


def generate_catalog(recipes_dirs, *, user='denver', channel='snapshot', export_to=None):
    """Build one catalog covering every dir in ``recipes_dirs``, as {'name/version': reference}.

    Nothing is written to disk unless ``export_to`` is given (--export-catalog,
    i.e. denver.yml's ``conan.export-catalog:``): the caller consumes the
    returned mapping directly, so a catalog.yml only ever appears where an env
    explicitly asked for one instead of turning up in the recipe tree as a
    side effect of every run.

    ``user``/``channel`` (denver.yml's ``conan.user:``/``conan.channel:``,
    threaded down from this script's own ``--user``/``--channel``, see
    main()) become every generated reference's user/channel.
    """
    print_banner("Generate catalog from " + ", ".join(str(d) for d in recipes_dirs))
    catalog = _import_build_catalog().build(recipes_dirs, user=user, channel=channel)
    if export_to:
        catalog.write_catalog(export_to)
    return catalog.get_references()


def export(recipe_path, ref):
    """Export ``recipe_path`` to the local conan cache under ``ref``, unless it's already there."""
    print_banner(f"Export: {recipe_path}")

    cached = get_cache_path(ref)
    if cached:
        print(f"Info: {ref!r} already exported to cache: {cached}")
        return
    conan_api.export.export(recipe_path, ref.name, ref.version, ref.user, ref.channel)
    if not get_cache_path(ref):
        print(f"Warning: Failed to verify export of {ref} to cache")


def find_pref(pref, remotes):
    """conan_list(), plus an info line reporting where ``pref`` was found (if anywhere)."""
    found, remote = conan_list(pref, remotes)
    if found:
        location = f"remote '{remote.name}'" if remote else "local cache"
        print(f"Info: Package {pref!r} already in {location}")
    return found, remote


def test(recipe_path, pref):
    """Run ``recipe_path``'s test_package against ``pref``, if a test_package exists."""
    print_banner(f"Test: {recipe_path}")
    test_conanfile_path = recipe_path.parent / 'test_package' / CONANFILE_NAME
    if not test_conanfile_path.exists():
        print(f"Info: No test_package exists: '{test_conanfile_path}'")
        return
    profile_host, profile_build = _default_profiles()
    run_test(
        conan_api,
        os.fspath(test_conanfile_path),
        pref.ref,
        profile_host,
        profile_build,
        remotes=[],
        lockfile=conan_api.lockfile.get_lockfile(),
        update=None,
        build_modes=None,
        tested_python_requires=pref.ref,
    )


# Every option this module ever puts on conan's command line, spelled out:
# anything else that would reach conan's parser as an option is, by
# definition, not one of ours. Add to this when a call site below grows a new
# flag -- that is the point, the list is the thing being enforced.
_CONAN_CLI_OPTIONS = frozenset({'--name', '--version', '--user', '--channel', '--test-missing', '-r'})

# A value: a subcommand, a recipe path, a package reference, a remote name --
# or the '<value>' half of '--name=<value>'. It may not begin with '-', that
# being exactly the shape that gets a path or a reference read as *another
# option* by conan's own parser, which is the injection this guards against.
# It may not contain a control character either: NUL is rejected by exec()
# itself anyway, and the rest have no business in a conan reference or in a
# path denver was pointed at.
_CONAN_CLI_VALUE = re.compile(r'[^-\x00-\x1f][^\x00-\x1f]*')


def _validated_conan_arg(arg):
    """Raise ValueError unless ``arg`` is one argv element of the shape this module builds (see below)."""
    if not isinstance(arg, str):
        raise ValueError(f"invalid conan CLI arguments: not a string: {arg!r}")
    if not arg.startswith('-'):
        if not _CONAN_CLI_VALUE.fullmatch(arg):
            raise ValueError(f"invalid conan CLI arguments: not a usable value: {arg!r}")
        return
    # option-shaped: it has to be one of ours ('--test-missing', bare, or
    # '--name=<value>' / '-r=<value>', whose value is checked like any other)
    option, sep, value = arg.partition('=')
    if option not in _CONAN_CLI_OPTIONS:
        raise ValueError(f"invalid conan CLI arguments: not an option this module passes: {arg!r}")
    if sep and not _CONAN_CLI_VALUE.fullmatch(value):
        raise ValueError(f"invalid conan CLI arguments: not a usable value for {option}: {value!r}")


def _validated_conan_argv(args):
    """Return ``['conan', *args]``, every element of it checked by _validated_conan_arg() first.

    Validation happens on the assembled argv rather than on ``args`` alone:
    what matters is the list that is about to be executed, argv[0] included.

    The elements are built by this module's own callers (create()'s
    ``--name=``/``--version=``/``--user=``/``--channel=``, sourced from a
    RecipeReference; upload()'s ``-r=``, a remote name checked against the
    ones conan has configured), never taken verbatim from raw CLI/network
    input. Checking them here regardless is what keeps that true: a value
    that arrived malformed -- a recipe path, a catalog entry, a --remote --
    or one a future call site passes through unexamined cannot reach conan's
    parser as an option denver never meant to pass. It fails here instead,
    with the offending element named.
    """
    cmd = ['conan', *args]
    for arg in cmd:
        _validated_conan_arg(arg)
    return cmd


def _run_conan_cli(*args):
    """Run `conan <args...>` as a subprocess, raising on a non-zero exit.

    TODO: replace with conan's own python API, if/when one covers this.
    """
    subprocess.run(_validated_conan_argv(args), check=True)


def _validate_remote_name(remote_name):
    """Return ``remote_name`` if conan has a remote of that name configured, else raise ValueError.

    A remote name is the one value here that comes from outside this module
    -- denver.yml's ``conan.remotes:`` keys, or ``--remote`` on this
    script's own command line -- and it is interpolated into a bare
    ``-r=<name>`` argument. Rather than guessing at which shapes are safe,
    this checks the only thing that actually matters: that the name denotes
    a remote conan knows about (``conan remote list``, enabled or not).
    Anything else -- a typo, or a value shaped to smuggle a second argument
    past conan's parser -- is not in that list and never reaches the CLI.
    """
    known = conan_remotes_list()
    if remote_name not in known:
        raise ValueError(f"not a configured conan remote: {remote_name!r} (known: {sorted(known)})")
    return remote_name


def _reject_option_like(value, what):
    """Refuse a ``value`` headed for conan as a bare (non ``--flag=``) argument if it could pass as a CLI option.

    ``create()``'s recipe path and ``upload()``'s package reference are both
    positional, not `--name=`-style -- so, unlike those, a value starting
    with '-' would be read by conan's own arg parser as an option rather
    than the path/reference it actually is. Neither can genuinely produce
    that shape today (see call sites), but this is the one place that can
    still say so plainly instead of letting a future change reach conan as
    an injected flag.
    """
    if value.startswith('-'):
        raise ValueError(f"{what} looks like a CLI option, refusing to pass it to conan: {value!r}")
    return value


def create(recipe_path, pref):
    """Build ``recipe_path`` via `conan create` (or just test it, if already built) unless ``pref`` is found locally."""
    print_banner(f"Create: {recipe_path}")
    found, _ = find_pref(pref, remotes=None)
    if found:
        # already built from source at some point -- (re-)run just its test_package
        test(recipe_path, pref)
        return
    _run_conan_cli(
        'create',
        _reject_option_like(os.fspath(recipe_path), 'recipe path'),
        f'--name={pref.ref.name}',
        f'--version={pref.ref.version}',
        f'--user={pref.ref.user}',
        f'--channel={pref.ref.channel}',
        '--test-missing',
    )


def upload(pref, remote_name):
    """Upload ``pref`` to ``remote_name``, unless it's already there."""
    print_banner(f"Upload: {pref!r}")
    _validate_remote_name(remote_name)
    found, _ = find_pref(pref, remotes=[remote_name])
    if found:
        return
    _run_conan_cli('upload', f'-r={remote_name}', _reject_option_like(repr(pref), 'package reference'))


def run_ci(recipe_path, pref, remotes):
    """Build ``recipe_path`` (via create()) only if ``pref`` isn't already available in ``remotes``."""
    print_banner(f"Build (only if not available remote): {recipe_path}")
    found_remote, _ = find_pref(pref, remotes)
    if found_remote:
        return
    found_local, _ = find_pref(pref, remotes=None)
    if not found_local:
        create(recipe_path, pref)


def conan_remotes_list():
    """Return every remote currently configured in the conan home (enabled or not), keyed by name."""
    return {remote.name: remote for remote in conan_api.remotes.list(only_enabled=False)}


def _find_renamed(remote_name, remote, remotes):
    """If ``remote`` moved to a new name in ``remotes`` (same url, different key), return that new name."""
    for new_name, meta in remotes.items():
        if meta.get('url') == remote.url and new_name != remote_name:
            return new_name
    return None


def conan_ensure_remotes(remotes):
    """Add/rename/update conan home remotes to match ``remotes`` (a {name: {url, verify_ssl}} dict)."""
    print("Ensure custom remotes ...")
    current_remotes = conan_remotes_list()

    for remote_name, remote in current_remotes.items():
        new_name = _find_renamed(remote_name, remote, remotes)
        if new_name:
            print(f"Removing remote with url {remote.url}:")
            print(f"  Old name: {remote_name}")
            print(f"  New name: {new_name}")
            conan_api.remotes.remove(remote_name)

    for remote_name, meta in remotes.items():
        remote = Remote(remote_name, meta.get('url'), meta.get('verify_ssl', True))
        if remote_name in current_remotes:
            current_remote = conan_api.remotes.get(remote_name)
            if remote == current_remote:
                continue  # already correctly present
            conan_api.remotes.remove(remote_name)
        conan_api.remotes.add(remote, index=0)


def _env_enable_override(remote_name):
    """True/False/None for CONAN_REMOTE_ENABLE_<NAME>, overriding whatever the config said."""
    return {"ON": True, "OFF": False}.get(os.getenv(f"CONAN_REMOTE_ENABLE_{remote_name.upper()}"))


def conan_enable_remotes(remotes):
    """Enable every remote named in ``remotes`` (per its own 'enabled:'), disable every other conan home remote."""
    print("Enable custom remotes ...")
    for remote_name in conan_remotes_list():
        configured_enabled = remotes[remote_name].get("enabled", True) if remote_name in remotes else False
        override = _env_enable_override(remote_name)
        enabled = configured_enabled if override is None else override

        if enabled:
            conan_api.remotes.enable(remote_name)
        else:
            print(f"Info: {remote_name} is disabled.")
            conan_api.remotes.disable(remote_name)


def _needs_reauth(user_info, configured_username, *, force):
    """Whether conan_login should (re)authenticate a remote, given its current auth state."""
    if force:
        return True
    if user_info.get('authenticated'):
        return False
    return not (configured_username and user_info.get('username') == configured_username)


def conan_login(remotes, *, force=False):
    """Authenticate to each enabled remote named in ``remotes``, unless already authenticated.

    ``force`` (denver.yml's ``conan.cleanup-remotes:`` sibling, ``--force``
    on this script's own CLI -- see main()) re-authenticates even if a
    remote already looks authenticated; never read from a real environment
    variable.
    """
    print("Login to custom remotes ...")
    for remote_name, remote in conan_remotes_list().items():
        managed = not remote.disabled and remote_name in remotes
        if not managed:
            continue

        user_info = conan_api.remotes.user_info(remote)
        configured_username = os.getenv(f"CONAN_LOGIN_USERNAME_{remote_name.upper()}")
        if _needs_reauth(user_info, configured_username, force=force):
            print_banner(remote)
            try:
                authenticate_remote(remote, force=True)
            except ConanConnectionError as e:
                print(colorama.Fore.YELLOW, end='')
                print(f"\nWARNING: Unable to connect to remote '{remote_name}' at {remote.url}")
                print(f"Reason: {e}")
                print(colorama.Style.RESET_ALL)


def prepare(remotes: dict[str, dict[str, str | bool]], *, cleanup: bool = False, force: bool = False):
    """Reconcile the conan remotes configured via ``conan.remotes:`` in denver.yml.

    A no-op when ``remotes`` is empty and ``cleanup`` is false: without an
    explicit, project-owned list of remotes, this must never touch the
    user's existing conan configuration by default -- in particular
    ``conan_enable_remotes`` disables every remote not named in ``remotes``,
    so calling it with an empty dict would silently disable all of them.
    ``cleanup`` (denver.yml's ``conan.cleanup-remotes:``, default on) opts
    into exactly that: treating ``remotes`` as the *exhaustive* list even
    when it's empty, disabling every remote already present. ``force``
    (denver's own ``--force``) re-authenticates every remote regardless of
    whether it already looks authenticated.
    """
    if not remotes and not cleanup:
        print("Info: no conan remotes configured (denver.yml's conan.remotes:); leaving conan's remote config as-is.")
        return
    print_banner("Prepare conan remotes")
    conan_ensure_remotes(remotes)
    conan_enable_remotes(remotes)
    conan_login(remotes, force=force)


def _export_missing_recipes(recipes_ref):
    """Export every ``{path: RecipeReference}`` entry that ``needs_export()`` (see there)."""
    for recipe_path, ref in recipes_ref.items():
        if needs_export(ref):
            export(recipe_path, ref)


def _run_per_pref_actions(recipes_pref, args):
    """Create/run_ci/upload each ``{path: PkgReference}`` entry, as selected by ``args``."""
    for recipe_path, pref in recipes_pref.items():
        if args.create:
            create(recipe_path, pref)
        if args.ci:
            run_ci(recipe_path, pref, [args.remote])
        if args.upload:
            upload(pref, args.remote)


def _process_catalog(recipes_dirs, args):
    """Run one catalog's generate/export/create/ci/upload pipeline, as selected by ``args``."""
    if args.no_generate:
        entries = read_catalog(args.catalog_yml.resolve())
    else:
        entries = generate_catalog(
            recipes_dirs,
            user=args.user,
            channel=args.channel,
            export_to=args.export_catalog,
        )

    recipes_ref = get_recipes_from_entries(recipes_dirs, entries)
    if args.recipes:
        recipes_ref = handle_args_recipe(recipes_ref, args.recipes)

    if args.export:
        _export_missing_recipes(recipes_ref)

    recipes_pref = get_recipes_prefs(recipes_ref)
    _run_per_pref_actions(recipes_pref, args)


def _build_arg_parser():
    """Build recipes.py's argparse.ArgumentParser -- split out of main() so its shape is easy to scan."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true', help='prepare to work with conan remotes')
    parser.add_argument(
        '--no-generate',
        action='store_true',
        help='do not build the catalog; read an existing one from --catalog-yml instead',
    )
    parser.add_argument('--export', action='store_true', help='run "conan export"')
    parser.add_argument('--create', action='store_true', help='run "conan create"')
    parser.add_argument('--upload', action='store_true', help='run "conan upload"')
    parser.add_argument('--remote', default=None, help='select a conan remote (required by --ci/--upload)')
    parser.add_argument(
        '--remotes-json',
        type=Path,
        default=None,
        help="path to a JSON file of {remote_name: {url, verify_ssl, enabled}} -- denver.yml's conan.remotes:, "
        'written by the conan provider. Without it, remotes are left untouched.',
    )
    parser.add_argument(
        '--cleanup-remotes',
        action='store_true',
        help="treat --remotes-json's content as the exhaustive remote list even when empty, disabling every "
        'other remote already present -- denver.yml\'s conan.cleanup-remotes: (default on)',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='re-authenticate to every remote even if already authenticated -- denver\'s own --force',
    )
    parser.add_argument(
        '--ci',
        action='store_true',
        help='run "create and upload conan packages that are missing remote"',
    )
    parser.add_argument(
        '--user',
        default='denver',
        help='conan user for each generated reference -- denver.yml\'s conan.user: (default "denver")',
    )
    parser.add_argument(
        '--channel',
        default='snapshot',
        help='conan channel for each generated reference -- denver.yml\'s conan.channel: (default "snapshot")',
    )
    parser.add_argument(
        '-d',
        '--recipes-dir',
        type=Path,
        action='append',
        default=[],
        help='Directory searched for conan recipes (repeatable -- every dir given forms one catalog, '
        "denver.yml's per-unit conan.conanfiles[].recipe-dirs:)",
    )
    parser.add_argument(
        '-b',
        '--base-classes-dir',
        type=Path,
        action='append',
        default=[],
        help='Directory of shared conanfile base classes to put on PYTHONPATH (repeatable, in order)',
    )
    parser.add_argument(
        '-c',
        '--catalog-yml',
        type=Path,
        help='Existing catalog.yml to read instead of building one (only with --no-generate)',
    )
    parser.add_argument(
        '-e',
        '--export-catalog',
        type=Path,
        default=None,
        help="Write the generated catalog to this path -- denver.yml's conan.export-catalog:. "
        'Without it, the catalog is built in memory only and no catalog.yml is written.',
    )
    parser.add_argument('recipes', nargs='*', help='Recipe folder names (one or more)')
    return parser


def main():
    """CLI entry point: parse args, prepare remotes, then generate/export/create/upload/ci as requested."""
    parser = _build_arg_parser()
    args = parser.parse_args()

    if (args.ci or args.upload) and not args.remote:
        parser.error('--ci/--upload need --remote (no default remote is assumed)')

    # prepend conan helpers to PYTHONPATH, if given (not needed for a
    # --prepare-only invocation with no recipe-dirs, e.g. remotes-only setup);
    # --base-classes-dir is repeatable, and earlier dirs win over later ones.
    if args.base_classes_dir:
        conan_pythonpath = [os.fspath(d.resolve()) for d in args.base_classes_dir]
        sys.path[0:0] = conan_pythonpath
        os.environ['PYTHONPATH'] = ':'.join([os.getenv('PYTHONPATH', ""), *conan_pythonpath])

    custom_remotes = json.loads(args.remotes_json.read_text()) if args.remotes_json else {}
    prepare(custom_remotes, cleanup=args.cleanup_remotes, force=args.force)
    if args.prepare:
        return

    # after prepare(), which is what puts denver.yml's 'remotes:' into the
    # conan home in the first place -- so this fails a typo'd (or otherwise
    # unknown) --remote before the generate/export/create work rather than
    # after it, and with the list of names that would have worked
    if args.remote is not None:
        try:
            _validate_remote_name(args.remote)
        except ValueError as exc:
            parser.error(str(exc))

    if not args.recipes_dir:
        parser.error('--recipes-dir is required (unless --prepare)')
    if args.no_generate and not args.catalog_yml:
        parser.error('--no-generate needs --catalog-yml (the existing catalog to read instead)')
    if args.catalog_yml and not args.no_generate:
        parser.error('--catalog-yml only applies with --no-generate; --export-catalog writes the generated one')

    _process_catalog([d.resolve() for d in args.recipes_dir], args)
    print_banner("Done!")


if __name__ == "__main__":
    main()
