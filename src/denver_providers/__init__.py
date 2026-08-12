"""denver providers: generic, denver.yml-driven environment providers.

Each stage listed under ``stages:`` in an env's denver.yml is instantiated and
run in order. Providers are generic -- all project specifics come from config.
"""

import importlib.util
import re
import sys

from .base import Provider
from .conan import ConanProvider
from .context import Context, banner, die, info
from .custom import CustomProvider
from .docker import DockerProvider
from .uv import UvProvider
from .zephyr import ZephyrProvider

# Registry of available providers, keyed by the name used in denver.yml.
# 'extensions.providers.dirs:' (see load_extension_providers) adds to this
# dict at runtime -- own, project-local providers alongside the built-ins,
# with no denver fork required.
PROVIDERS = {
    "uv": UvProvider,
    "conan": ConanProvider,
    "zephyr": ZephyrProvider,
    "docker": DockerProvider,
    "custom": CustomProvider,
}

# the 'extensions:' sub-schema, validated the same way denver.py validates
# its top-level keys and a stage's own section: an unrecognised key is an
# error, never silently ignored (see "Fail loud" in doc/philosophy.md). A
# typo'd 'providrs:'/'dris:' would otherwise disable the whole mechanism
# quietly, surfacing only as an "unknown provider type" much further on.
_EXTENSION_KEYS = {"providers"}
_EXTENSION_PROVIDER_KEYS = {"dirs"}

# absolute paths of the provider modules already imported by this process,
# so a second resolve of the same config (e.g. a stage re-resolving its
# defaults right before it runs) re-uses the classes already registered
# instead of re-importing the file and colliding with itself.
_loaded_extension_files = set()


def load_extension_providers(ctx, extensions_cfg):
    """Register Provider subclasses from 'extensions.providers.dirs:' into PROVIDERS.

    This is how a project adds its own provider (a build system, an
    internal deploy tool, whatever 'custom' can't express as a single
    command) without maintaining a fork of denver: point denver.yml at a
    directory of plain Python files, each defining::

        # my_providers/acme.py
        from denver_providers import Provider

        class AcmeProvider(Provider):
            name = "acme"          # the 'provider: acme' name in denver.yml
            KEYS = (...)
            def setup(self, ctx): ...

        PROVIDER = AcmeProvider    # the module's registration point

    and, in denver.yml::

        extensions:
          providers:
            dirs:
            - my_providers

    Every ``*.py`` file directly inside each listed dir is imported and must
    define ``PROVIDER`` -- a ``Provider`` subclass, registered under its own
    ``name`` the exact same way a built-in provider is, so ``provider: acme``
    picks it up in any stage from then on. Files whose name starts with
    ``_`` are skipped, so a provider too big for one file can put its shared
    code in ``_helpers.py`` (or make the dir a package with ``__init__.py``)
    without every such file having to be a provider of its own; the dir
    itself goes on ``sys.path``, so those helpers are importable by name.

    Dirs resolve the same way ``conan: base-classes:`` does (via
    ctx.resolve_path, falling back to imported base envs), and the list
    itself follows the normal merge rules -- a derived env only needs to
    list the dirs it adds.

    Called once per resolved config, before any stage referencing an
    extension provider's name is instantiated (see resolve_full_config).
    """
    for entry in _extension_provider_dirs(extensions_cfg):
        _load_provider_dir(ctx, entry)


def _load_provider_dir(ctx, entry):
    """Import and register every provider module directly inside one 'extensions.providers.dirs:' entry."""
    d = ctx.resolve_path(entry)
    if not d.is_dir():
        die(f"'extensions.providers.dirs:' directory not found: {d}")
    # so a provider module can 'import _helpers' next to itself.
    # Appended, not prepended: an extension dir can never shadow the
    # stdlib or denver's own modules.
    if str(d) not in sys.path:
        sys.path.append(str(d))
    for py_file in sorted(d.glob("*.py")):
        if not py_file.name.startswith("_"):
            _register_extension_provider(py_file)


def _extension_provider_dirs(extensions_cfg):
    """Validate the whole 'extensions:' section and return its 'providers.dirs:' list (empty if unset)."""
    cfg = extensions_cfg or {}
    if not isinstance(cfg, dict):
        die(f"'extensions:' must be a mapping, got {type(cfg).__name__}")
    _validate_keys(cfg, _EXTENSION_KEYS, "extensions")
    providers_cfg = cfg.get("providers") or {}
    if not isinstance(providers_cfg, dict):
        die(f"'extensions.providers:' must be a mapping, got {type(providers_cfg).__name__}")
    _validate_keys(providers_cfg, _EXTENSION_PROVIDER_KEYS, "extensions.providers")
    return _validated_dirs(providers_cfg.get("dirs"))


def _validated_dirs(dirs):
    """Return the 'extensions.providers.dirs:' list itself, checked to be a list of strings (empty if unset)."""
    if dirs is None:
        return []
    if not isinstance(dirs, list) or not all(isinstance(entry, str) for entry in dirs):
        die(
            "'extensions.providers.dirs:' must be a list of directories "
            f"(got {dirs!r} -- a single directory is written as a one-entry list)"
        )
    return dirs


def _validate_keys(cfg, allowed, where):
    """Die on any key in ``cfg`` that ``where``'s sub-schema doesn't recognise."""
    unknown = sorted(set(cfg) - allowed)
    if unknown:
        die(f"'{where}:': unknown key(s) {', '.join(unknown)} -- known: {', '.join(sorted(allowed))}.")


def _register_extension_provider(py_file):
    """Import one 'extensions.providers.dirs:' file and register its PROVIDER class."""
    if str(py_file) in _loaded_extension_files:
        return
    # a name unique per file (two dirs may each hold an 'acme.py') and
    # stable across re-imports.
    mod_name = "denver_extension_provider_" + re.sub(r"\W+", "_", str(py_file.with_suffix("")).strip("/"))
    spec = importlib.util.spec_from_file_location(mod_name, py_file)
    module = importlib.util.module_from_spec(spec)
    # registered *before* exec_module, per importlib's own recipe: a module
    # missing from sys.modules breaks anything that looks itself back up
    # there (pickle, typing.get_type_hints on a string annotation, ...) --
    # and does so only later, from inside the provider, not here.
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # surfaced as a denver.yml config error, not a crash
        del sys.modules[mod_name]
        die(f"'extensions.providers:' failed to load {py_file}: {exc}")
    _loaded_extension_files.add(str(py_file))
    provider_cls = _extension_provider_class(module, py_file)
    PROVIDERS[provider_cls.name] = provider_cls


def _extension_provider_class(module, py_file):
    """Return the Provider subclass ``module`` registers as PROVIDER, or die saying what's wrong with it."""
    provider_cls = getattr(module, "PROVIDER", None)
    if not (isinstance(provider_cls, type) and issubclass(provider_cls, Provider)):
        die(
            f"'extensions.providers:' {py_file} must define PROVIDER = <a Provider subclass> "
            f"(name a file '_*.py' if it is a helper module, not a provider)"
        )
    name = provider_cls.name
    if not name:
        die(f"'extensions.providers:' {py_file}: its PROVIDER class must set a 'name'")
    # a given file is imported at most once (see the caller), so this can
    # only be a *different* provider already holding the name -- built-in or
    # from another extension dir. Never a silent override.
    if name in PROVIDERS:
        die(
            f"'extensions.providers:' provider '{name}' from {py_file} conflicts with an existing provider of that name"
        )
    return provider_cls


def make_stage(stage_id, config):
    """Instantiate a pipeline stage from its id in the ``stages:`` list.

    The stage's config section must always declare its provider type
    explicitly, via ``provider: <name>`` -- even when the stage id itself
    happens to match a registered provider name. No guessing from the id: a
    stage id is just a label (and, for setup stages, a venv name via
    `venv:`), never an implicit type.
    """
    # the stage's own raw config section (not yet defaults-resolved) --
    # only its 'provider' key is read here, to pick a class.
    section = config.get(stage_id) or {}
    type_name = section.get("provider")
    if not type_name:
        die(
            f"stage '{stage_id}': its config section must declare "
            f"'provider: <name>'. Known providers: "
            f"{', '.join(sorted(PROVIDERS))}."
        )
    cls = PROVIDERS.get(type_name)
    if cls is None:
        die(
            f"stage '{stage_id}': unknown provider type '{type_name}'. Known providers: {', '.join(sorted(PROVIDERS))}."
        )
    # the provider gets the *whole* config (not just its own section): its
    # config_section()/resolve_defaults() re-read the right slice via
    # self.stage, and zephyr's resolver needs to see uv's section too.
    provider = cls(config)
    provider.stage = stage_id
    return provider


__all__ = [
    "PROVIDERS",
    "Context",
    "Provider",
    "banner",
    "die",
    "info",
    "load_extension_providers",
    "make_stage",
]
