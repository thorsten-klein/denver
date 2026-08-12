"""Base class for denver providers.

A provider is a generic, reusable engine (uv, conan, zephyr, docker, ...).
It is configured entirely from the env's denver.yml -- a provider never
hard-codes project specifics.

Lifecycle:
  * kind == "setup":   setup(ctx) prepares the shared environment in-place
                       (creates the venv, installs tools, updates the
                       workspace) and mutates ctx.env. The final command then
                       runs in that assembled environment.
  * kind == "wrapper": wrap(ctx, cmd) rewrites the final command so it runs
                       somewhere else (e.g. inside a docker container).

Under ctx.fast (denver's --fast), setup() must skip whatever it would
otherwise run to (re-)build its piece of the environment, and only activate
what an earlier full run already built (source the venv/buildenv, put tools
back on PATH, ...) -- dying with a clear message if there's nothing there
yet to activate. A provider with no "already built, just activate" state to
fall back on (e.g. an arbitrary one-off command) should just skip itself
entirely under ctx.fast.
"""

from __future__ import annotations


def fill_unset(resolved, keys):
    """Show every documented-but-unconfigured key explicitly as ``null`` in --show-config.

    Without this, an unset key would just be omitted, hiding the full set
    of keys a provider understands whenever nothing sets or defaults it.
    ``resolved`` already having a key (even falsy, e.g. ``[]``) is left
    untouched: only genuinely absent keys are filled.
    """
    for key in keys:
        resolved.setdefault(key, None)
    return resolved


class Provider:
    """Base class every denver provider (uv, conan, zephyr, docker, custom, ...) extends.

    Subclasses override ``name``/``kind``/``KEYS`` as class attributes, and
    ``resolve_defaults``/``setup``/``wrap`` as needed -- see each method's
    own docstring for what the default (no-op passthrough) does and when to
    override it.
    """

    #: the denver.yml section this provider reads (defaults to its name)
    name: str | None = None
    #: "setup" providers build the local environment; "wrapper" providers
    #: relocate the final command (e.g. into a container).
    kind: str = "setup"
    #: every denver.yml key this provider's section understands -- shown
    #: (as null if unset) in --show-config; see resolve_defaults.
    KEYS: tuple[str, ...] = ()

    def __init__(self, config):
        """Store the whole merged denver.yml ``config`` and default this provider's stage id to its type name."""
        # the full merged denver.yml config; a provider reads its own section
        self.config = config or {}
        # the stage id: the key of this provider's config section and its
        # hook prefix. Defaults to the provider type, but multiple stages of
        # the same type can each have a distinct id (e.g. two uv stages).
        self.stage = self.name

    @property
    def section_name(self):
        """The config key this provider's section lives under -- its stage id."""
        return self.stage

    def config_section(self, ctx):
        """This provider's interpolated config section from denver.yml."""
        return ctx.section(self.section_name)

    # ---- config defaults (override what you need) ------------------------ #
    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):  # noqa: ARG003  # shared (ctx, cfg, config) signature
        """Compute this stage's *complete* effective config section, once, centrally.

        Explicit values pass through untouched, everything else gets this
        provider's static/filesystem/PATH-derived default. Called by
        denver.resolve_provider_defaults, in 'stages:' order, for every
        stage before any provider's setup() runs -- so setup() never
        guesses a default itself, it just reads what's already there, and
        --show-config always reflects exactly what a real run would use.

        ``cfg`` is this stage's own raw section; ``config`` is the whole
        (already section-stacked) config, available to resolvers that
        default from another stage's section (zephyr's overrides fall back
        to uv's). Default: passthrough, with every key in ``KEYS`` shown
        (as null if unset).
        """
        return fill_unset(dict(cfg), cls.KEYS)

    # ---- lifecycle (override what you need) ----------------------------- #
    def setup(self, ctx):
        """Prepare the environment. Mutate ctx.env; run tools as needed."""

    def wrap(self, ctx, cmd):  # noqa: ARG002 -- default no-op; overriders use ctx
        """Return the command that should actually be executed."""
        return cmd
