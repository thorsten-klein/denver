"""Tests for the generated denver.yml JSON Schema (providers.schema).

The schema exists to catch, while a file is being written, what
validate_top_level_keys/validate_stage_section_keys only catch once it is
run. So the tests that matter are the two that could make it useless:

* it must accept every real environment in examples/ -- a schema that flags
  valid files is worse than none;
* it must reject what denver itself rejects, or an editor would report a
  file as fine that then fails at run time.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import jsonschema
import pytest
import yaml

import denver
from providers import PROVIDERS

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED = REPO_ROOT / "schema" / "denver.schema.json"


@pytest.fixture(scope="module")
def schema():
    return denver.denver_yml_schema()


@pytest.fixture(scope="module")
def validator(schema):
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def _example_configs():
    """Every *tracked* denver.yml under examples/, as (name, path) pairs.

    Tracked only, like the golden-file test: a developer's local scratch env
    under examples/ is not something this repository promises anything about.
    """
    listed = subprocess.run(
        ["git", "ls-files", "examples/*/denver.yml"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return sorted((Path(line).parent.name, REPO_ROOT / line) for line in listed.stdout.splitlines() if line)


@pytest.mark.parametrize(("name", "path"), _example_configs(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_bundled_example_validates(validator, name, path):
    errors = sorted(validator.iter_errors(yaml.safe_load(path.read_text())), key=str)
    assert not errors, f"examples/{name}/denver.yml is rejected by the generated schema: {errors[0].message}"


def test_schema_flag_prints_the_document(capsys, schema):
    assert denver.main(["--schema"]) == 0
    assert json.loads(capsys.readouterr().out) == schema


def test_committed_schema_is_up_to_date(schema):
    # generated, never hand-edited: regenerate with `denver --schema`
    assert json.loads(COMMITTED.read_text()) == schema


def test_key_specs_and_keys_describe_the_same_set():
    # KEYS is what validation reads; KEY_SPECS is what the schema reads.
    # Neither may quietly gain a key the other doesn't have.
    for name, cls in PROVIDERS.items():
        assert set(cls.KEY_SPECS) == set(cls.KEYS), f"provider '{name}': KEY_SPECS and KEYS disagree"


def test_unknown_stage_key_is_rejected(validator):
    config = {"stages": ["uv"], "uv": {"provider": "uv", "pythonn": "3.12.3"}}
    assert list(validator.iter_errors(config))


def test_key_of_another_provider_is_rejected(validator):
    # 'requirements:' is uv's; a conan stage must not accept it, which is the
    # whole point of switching on 'provider:'
    config = {"stages": ["c"], "c": {"provider": "conan", "requirements": ["r.txt"]}}
    assert list(validator.iter_errors(config))


def test_a_section_without_a_provider_is_accepted(validator):
    # a denver.yml is often a *layer*, not a whole environment: a derived env
    # restates only what differs and inherits 'provider:' through its
    # whole-file 'import:'. Requiring it here would flag valid files -- and
    # denver reports a genuinely missing one clearly at run time.
    config = {"import": ["../base"], "uv": {"requirements": ["r.txt"]}}
    assert not list(validator.iter_errors(config))


def test_a_stacked_section_is_accepted(validator):
    # section-level import supplies the provider from the section it stacks
    config = {"stages": ["docker"], "docker": {"import": ["../zephyr-docker"]}}
    assert not list(validator.iter_errors(config))


def test_interpolated_value_is_accepted_where_a_boolean_is_expected(validator):
    # any value may be a ${VAR} template, so a boolean-typed key must accept
    # one or the schema would reject perfectly valid files
    config = {"stages": ["uv"], "uv": {"provider": "uv", "append-mode": "${APPEND_MODE}"}}
    assert not list(validator.iter_errors(config))


def test_deliberate_override_marker_is_accepted_on_an_enum(validator):
    # deep_merge only lets a lower layer's value be overridden by a
    # '!'-marked one, so "!copy" is as valid as "copy"
    config = {"stages": ["uv"], "uv": {"provider": "uv", "link-mode": "!copy"}}
    assert not list(validator.iter_errors(config))


def test_overwrite_marker_is_accepted_in_a_list(validator):
    config = {"stages": ["uv"], "uv": {"provider": "uv", "requirements": ["<overwrite>", "only.txt"]}}
    assert not list(validator.iter_errors(config))


def test_schema_describes_the_registered_providers(schema):
    # generated from the registry, so a provider added to PROVIDERS is
    # described without anyone remembering to update a list
    described = {branch["if"]["properties"]["provider"]["const"] for branch in schema["additionalProperties"]["allOf"]}
    assert described == set(PROVIDERS)
