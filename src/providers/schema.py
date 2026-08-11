"""Generate a JSON Schema describing the ``denver.yml`` format.

denver is a config-first tool whose config had no machine-readable schema:
a typo was caught only by ``validate_top_level_keys`` /
``validate_stage_section_keys``, and only once you ran it. This produces the
same rules as a document editors can apply while the file is being written.

The schema is *generated*, never hand-maintained, from the very declarations
the run-time validators use -- each provider's ``KEYS`` (which keys exist)
and ``KEY_SPECS`` (what each one accepts). It therefore cannot drift from
what denver actually enforces, which is the same single-source-of-truth
argument doc/philosophy.md already makes for central default resolution.

Two shapes of a real denver.yml the schema has to tolerate, or it would
report valid files as invalid:

* **``${VAR}`` interpolation.** Any value may be a template string, so a key
  that is logically a boolean or a number can legitimately read
  ``"${SOMETHING}"``. Non-string types are widened accordingly -- see
  ``templated``.
* **Merge markers.** ``deep_merge`` gives a leading ``!`` and the bare
  ``<overwrite>`` list entry special meaning, so an enum-valued key must also
  accept its own values ``!``-prefixed, and a list must accept
  ``<overwrite>`` as a member.
* **Layering.** A ``denver.yml`` is often not a whole environment but a
  *layer*: a derived env restates only what differs and inherits the rest
  through ``import:``, so a stage section there legitimately has no
  ``provider:`` at all. Nothing the schema checks may assume it is looking
  at a complete environment.
"""

from __future__ import annotations

# The JSON Schema dialect targeted: draft-07 is what the YAML language
# server used by VS Code, JetBrains and neovim implements, and it is the
# oldest draft with the if/then needed to switch on 'provider:'.
DIALECT = "http://json-schema.org/draft-07/schema#"

SCHEMA_ID = "https://raw.githubusercontent.com/thorsten-klein/denver/develop/schema/denver.schema.json"

# matches any string containing a ${...} interpolation
TEMPLATE_PATTERN = r"\$\{"

# a stage section may pull its content from another env's section ("stacking")
# instead of spelling it out -- including that section's own 'provider:'.
SECTION_IMPORT = "import"


def templated(fragment):
    """Widen a non-string schema so a ``${VAR}`` template is also accepted.

    A string-typed key needs no widening (a template is a string); anything
    else would otherwise reject a perfectly valid interpolated value.
    """
    return {"anyOf": [fragment, {"type": "string", "pattern": TEMPLATE_PATTERN}]}


def string(description, **extra):
    """A plain string key."""
    return {"type": "string", "description": description, **extra}


def boolean(description):
    """A boolean key, also accepting an interpolated string."""
    return {**templated({"type": "boolean"}), "description": description}


def string_list(description):
    """A list of strings.

    No widening needed: every entry is a string already, which covers both a
    ``${VAR}`` template and ``deep_merge``'s ``<overwrite>``/``!`` markers.
    """
    return {"type": "array", "items": {"type": "string"}, "description": description}


def enum(description, values):
    """A closed set of values, plus each one ``!``-prefixed (a deliberate override).

    See deep_merge: a lower layer's value is only overridden by a ``!``-marked
    one, so ``link-mode: "!copy"`` is as valid as ``link-mode: copy``.
    """
    allowed = list(values) + [f"!{value}" for value in values if isinstance(value, str)]
    return {**templated({"enum": allowed}), "description": description}


def _stage_section(provider_name, key_specs, generic_stage_keys):
    """The ``if provider == <name> then <its keys>`` branch for one provider.

    ``then`` restricts the *names* as well as describing them: listing a
    provider's keys under ``properties`` alone would only document them,
    leaving another provider's key (uv's ``requirements:`` in a conan stage)
    silently accepted -- which is exactly what switching on ``provider:``
    exists to prevent.
    """
    allowed = sorted(set(key_specs) | set(generic_stage_keys) | {SECTION_IMPORT})
    return {
        "if": {"properties": {"provider": {"const": provider_name}}, "required": ["provider"]},
        "then": {"properties": dict(key_specs), "propertyNames": {"enum": allowed}},
    }


def build(providers, generic_stage_keys, top_level_keys):
    """Build the whole document.

    ``providers`` is the registry (name -> class), so a schema always
    describes exactly the providers this denver has -- including, once
    providers become pluggable, ones that did not ship with it.

    Stage sections are the interesting part: their *names* come from
    ``stages:``, and their shape is decided by the ``provider:`` key inside
    them. ``additionalProperties`` plus one ``if``/``then`` per provider
    expresses that, so an editor offers exactly the keys of whichever
    provider the section declares -- the same rule
    ``validate_stage_section_keys`` enforces at run time.
    """
    known_stage_keys = set(generic_stage_keys) | {SECTION_IMPORT}
    for cls in providers.values():
        known_stage_keys |= set(cls.KEYS)

    return {
        "$schema": DIALECT,
        "$id": SCHEMA_ID,
        "title": "denver.yml",
        "description": "An environment denver can launch. See https://github.com/thorsten-klein/denver",
        "type": "object",
        "properties": {
            **{name: spec for name, spec in _TOP_LEVEL.items() if name in top_level_keys},
        },
        # every other top-level key is a stage id; its section's shape depends
        # on the provider it declares
        "additionalProperties": {
            "type": "object",
            "properties": {**_GENERIC_STAGE, SECTION_IMPORT: _SECTION_IMPORT_SPEC},
            # Deliberately *not* "required": ["provider"], even though denver
            # requires it: a denver.yml may be a layer rather than a whole
            # environment. A derived env states only what differs and
            # inherits 'provider:' through its whole-file 'import:', and a
            # stacked section pulls it from the section it imports -- so
            # requiring it here would flag valid files, which is worse than
            # missing an error denver itself reports clearly at run time
            # (see providers.make_stage).
            "propertyNames": {"enum": sorted(known_stage_keys)},
            "allOf": [
                _stage_section(name, cls.KEY_SPECS, generic_stage_keys) for name, cls in sorted(providers.items())
            ],
        },
    }


# --------------------------------------------------------------------------- #
# The non-stage half of the schema: denver.py's own keys, mirroring
# KNOWN_TOP_LEVEL_KEYS and GENERIC_STAGE_KEYS.
# --------------------------------------------------------------------------- #
_TOP_LEVEL = {
    "version": {
        **templated({"enum": ["1.0", 1.0]}),
        "description": "denver.yml schema version this file is written against. Only '1.0' is understood.",
    },
    "denver-version": string(
        "Minimum denver *tool* version this file needs, e.g. '>=1.0.4' or '>=1.0.4, <2'.",
    ),
    "import": string_list(
        "Environments (or YAML files) whose whole configuration is inherited as a base, before this file's own."
    ),
    "stages": string_list("The ordered pipeline: each entry is a stage id with a top-level section of that name."),
    "command": {
        "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
        "description": "Default command once the environment is built, when none is given after '--'.",
    },
    "runnable": boolean("false marks this file a base meant only to be imported, never started directly."),
    "env": {
        "type": "object",
        "additionalProperties": {"type": ["string", "number", "boolean"]},
        "description": "Environment variables for the whole environment; values go through ${...} interpolation.",
    },
    "hooks": {
        "type": "object",
        "additionalProperties": {
            "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
        },
        "description": "Scripts sourced at fixed points: 'env', 'pre-<stage>'/'post-<stage>', 'pre-cmd'.",
    },
}

_SECTION_IMPORT_SPEC = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Stack another env's section here: '<path>' or '<path>:<section>'. Supplies 'provider:' too.",
}

_GENERIC_STAGE = {
    "provider": {
        "type": "string",
        "description": "Which provider engine runs this stage. Required -- never guessed from the stage id.",
    },
    "description": string_list("Free text about this stage; denver never reads it (shown in --show-config)."),
    "disabled": boolean("true opts this stage out of the pipeline without deleting its configuration."),
    "scripts": {
        "type": "object",
        "additionalProperties": {"type": "array", "items": {"type": "string"}},
        "description": "One-shot scripts run by 'denver <env> --run <name>' instead of the pipeline.",
    },
}
