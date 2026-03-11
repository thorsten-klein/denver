"""denver providers: generic, denver.yml-driven environment providers.

Each stage listed under ``stages:`` in an env's denver.yml is instantiated and
run in order. Providers are generic -- all project specifics come from config.
"""

from .base import Provider
from .conan import ConanProvider
from .context import Context, banner, die, info
from .custom import CustomProvider
from .docker import DockerProvider
from .pip import PipProvider
from .zephyr import ZephyrProvider

# Registry of available providers, keyed by the name used in denver.yml.
PROVIDERS = {
    "pip": PipProvider,
    "conan": ConanProvider,
    "zephyr": ZephyrProvider,
    "docker": DockerProvider,
    "custom": CustomProvider,
}


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
    # self.stage, and zephyr's resolver needs to see pip's section too.
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
    "make_stage",
]
