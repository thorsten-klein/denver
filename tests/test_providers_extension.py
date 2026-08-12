"""Tests for providers.load_extension_providers ('extensions.providers.dirs:')."""

import sys

import pytest

import denver_providers as providers

# process-wide PROVIDERS/_loaded_extension_dirs mutations are undone by the
# autouse _reset_provider_registry fixture in conftest.py.


def write_provider(dir_path, filename, name, class_name="ExtProvider"):
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / filename).write_text(
        f"""
from denver_providers import Provider


class {class_name}(Provider):
    name = "{name}"


PROVIDER = {class_name}
"""
    )


def test_no_extensions_cfg_is_a_noop(make_context):
    ctx = make_context()
    providers.load_extension_providers(ctx, None)
    providers.load_extension_providers(ctx, {})


def test_loads_provider_and_make_stage_picks_it_up(make_context):
    ctx = make_context()
    write_provider(ctx.env_dir / "my_providers", "acme.py", "acme")

    providers.load_extension_providers(ctx, {"providers": {"dirs": ["my_providers"]}})

    assert "acme" in providers.PROVIDERS
    stage = providers.make_stage("my-stage", {"my-stage": {"provider": "acme"}})
    assert isinstance(stage, providers.PROVIDERS["acme"])


def test_reloading_same_dir_is_a_noop_not_a_conflict(make_context):
    ctx = make_context()
    write_provider(ctx.env_dir / "my_providers", "acme.py", "acme")
    cfg = {"providers": {"dirs": ["my_providers"]}}

    providers.load_extension_providers(ctx, cfg)
    providers.load_extension_providers(ctx, cfg)  # must not die the second time


def test_underscore_files_are_helpers_not_providers(make_context):
    """A '_'-prefixed file is shared code, importable by name -- not a provider that must define PROVIDER."""
    ctx = make_context()
    d = ctx.env_dir / "my_providers"
    d.mkdir(parents=True)
    (d / "_helpers.py").write_text("GREETING = 'hi'\n")
    (d / "__init__.py").write_text("raise AssertionError('must never be imported as a provider')\n")
    (d / "acme.py").write_text(
        """
from denver_providers import Provider

from _helpers import GREETING


class ExtProvider(Provider):
    name = "acme"
    KEYS = (GREETING,)


PROVIDER = ExtProvider
"""
    )

    providers.load_extension_providers(ctx, {"providers": {"dirs": ["my_providers"]}})

    assert providers.PROVIDERS["acme"].KEYS == ("hi",)


def test_provider_module_lands_in_sys_modules(make_context):
    """Anything looking itself back up in sys.modules (pickle, get_type_hints, ...) must work from a provider module."""
    ctx = make_context()
    d = ctx.env_dir / "my_providers"
    d.mkdir(parents=True)
    (d / "acme.py").write_text(
        """
import sys

from denver_providers import Provider


class ExtProvider(Provider):
    name = "acme"
    KEYS = (__name__ in sys.modules,)


PROVIDER = ExtProvider
"""
    )

    providers.load_extension_providers(ctx, {"providers": {"dirs": ["my_providers"]}})

    assert providers.PROVIDERS["acme"].KEYS == (True,)


def test_failed_import_leaves_no_half_built_module_behind(make_context):
    ctx = make_context()
    d = ctx.env_dir / "my_providers"
    d.mkdir(parents=True)
    (d / "acme.py").write_text("raise RuntimeError('boom')\n")

    with pytest.raises(SystemExit):
        providers.load_extension_providers(ctx, {"providers": {"dirs": ["my_providers"]}})

    assert not [name for name in sys.modules if name.startswith("denver_extension_provider_")]


def test_same_stem_in_two_dirs_does_not_collide(make_context):
    """Two dirs may each hold an 'acme.py'; neither may silently win the other's module name."""
    ctx = make_context()
    write_provider(ctx.env_dir / "dir_a", "acme.py", "acme-a", class_name="AcmeA")
    write_provider(ctx.env_dir / "dir_b", "acme.py", "acme-b", class_name="AcmeB")

    providers.load_extension_providers(ctx, {"providers": {"dirs": ["dir_a", "dir_b"]}})

    assert providers.PROVIDERS["acme-a"].__name__ == "AcmeA"
    assert providers.PROVIDERS["acme-b"].__name__ == "AcmeB"


@pytest.mark.parametrize(
    "cfg",
    [
        {"providrs": {"dirs": ["my_providers"]}},
        {"providers": {"dris": ["my_providers"]}},
    ],
)
def test_typo_under_extensions_dies(make_context, cfg):
    """A typo must fail loud here, not silently disable the whole mechanism."""
    ctx = make_context()
    with pytest.raises(SystemExit):
        providers.load_extension_providers(ctx, cfg)


@pytest.mark.parametrize("dirs", ["my_providers", 42, [42], {"a": 1}])
def test_dirs_not_a_list_of_strings_dies(make_context, dirs):
    ctx = make_context()
    with pytest.raises(SystemExit):
        providers.load_extension_providers(ctx, {"providers": {"dirs": dirs}})


def test_missing_dir_dies(make_context):
    ctx = make_context()
    with pytest.raises(SystemExit):
        providers.load_extension_providers(ctx, {"providers": {"dirs": ["does-not-exist"]}})


def test_extensions_not_a_mapping_dies(make_context):
    ctx = make_context()
    with pytest.raises(SystemExit):
        providers.load_extension_providers(ctx, ["not", "a", "mapping"])


def test_providers_not_a_mapping_dies(make_context):
    ctx = make_context()
    with pytest.raises(SystemExit):
        providers.load_extension_providers(ctx, {"providers": ["not", "a", "mapping"]})


def test_module_without_provider_attr_dies(make_context):
    ctx = make_context()
    d = ctx.env_dir / "my_providers"
    d.mkdir(parents=True, exist_ok=True)
    (d / "broken.py").write_text("x = 1\n")
    with pytest.raises(SystemExit):
        providers.load_extension_providers(ctx, {"providers": {"dirs": ["my_providers"]}})


def test_module_provider_not_a_provider_subclass_dies(make_context):
    ctx = make_context()
    d = ctx.env_dir / "my_providers"
    d.mkdir(parents=True, exist_ok=True)
    (d / "broken.py").write_text("PROVIDER = object\n")
    with pytest.raises(SystemExit):
        providers.load_extension_providers(ctx, {"providers": {"dirs": ["my_providers"]}})


def test_module_provider_without_name_dies(make_context):
    ctx = make_context()
    d = ctx.env_dir / "my_providers"
    d.mkdir(parents=True, exist_ok=True)
    (d / "broken.py").write_text(
        """
from denver_providers import Provider


class Broken(Provider):
    pass


PROVIDER = Broken
"""
    )
    with pytest.raises(SystemExit):
        providers.load_extension_providers(ctx, {"providers": {"dirs": ["my_providers"]}})


def test_name_conflicting_with_builtin_dies(make_context):
    ctx = make_context()
    write_provider(ctx.env_dir / "my_providers", "fake_uv.py", "uv", class_name="FakeUv")
    with pytest.raises(SystemExit):
        providers.load_extension_providers(ctx, {"providers": {"dirs": ["my_providers"]}})


def test_two_extension_dirs_with_same_name_conflict(make_context):
    ctx = make_context()
    write_provider(ctx.env_dir / "dir_a", "acme.py", "acme", class_name="AcmeA")
    write_provider(ctx.env_dir / "dir_b", "acme.py", "acme", class_name="AcmeB")
    with pytest.raises(SystemExit):
        providers.load_extension_providers(ctx, {"providers": {"dirs": ["dir_a", "dir_b"]}})
