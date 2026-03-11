"""Tests for providers.base.Provider and the providers.__init__ registry."""

import pytest

import providers
from providers.base import Provider


def test_provider_default_stage_is_name():
    n = Provider({"a": 1})
    n.name = "pip"
    n.stage = n.name
    assert n.section_name == "pip"
    assert n.config == {"a": 1}


def test_provider_config_defaults_to_empty_dict():
    assert Provider(None).config == {}


def test_provider_config_section_reads_own_section(make_context):
    class Sub(Provider):
        name = "pip"

    ctx = make_context(config={"pip": {"x": 1}})
    sub = Sub({"pip": {"x": 1}})
    assert sub.config_section(ctx) == {"x": 1}


def test_provider_setup_and_wrap_defaults(make_context):
    ctx = make_context()
    n = Provider({})
    assert n.setup(ctx) is None  # base setup is a no-op
    assert n.wrap(ctx, ["cmd"]) == ["cmd"]  # base wrap passes through


def test_make_stage_explicit_provider_key():
    stage = providers.make_stage("pip", {"pip": {"provider": "pip", "python": "3.9"}})
    assert isinstance(stage, providers.PROVIDERS["pip"])
    assert stage.stage == "pip"


def test_make_stage_type_key_alone_dies():
    with pytest.raises(SystemExit):
        providers.make_stage("pip", {"pip": {"type": "pip", "python": "3.9"}})


def test_make_stage_custom_id_with_provider():
    config = {"stages": ["pip-2"], "pip-2": {"provider": "pip", "venv": "second"}}
    stage = providers.make_stage("pip-2", config)
    assert isinstance(stage, providers.PROVIDERS["pip"])
    assert stage.stage == "pip-2"


def test_make_stage_missing_provider_dies():
    # a bare id matching a registered provider name is NOT enough -- it must
    # be declared explicitly.
    with pytest.raises(SystemExit):
        providers.make_stage("pip", {"pip": {"python": "3.9"}})


def test_make_stage_custom_provider():
    stage = providers.make_stage("my-stage", {"my-stage": {"provider": "custom", "cmd": "true"}})
    assert isinstance(stage, providers.PROVIDERS["custom"])
    assert stage.stage == "my-stage"


def test_make_stage_unknown_dies():
    with pytest.raises(SystemExit):
        providers.make_stage("mystery", {"stages": ["mystery"], "mystery": {"provider": "mystery"}})
