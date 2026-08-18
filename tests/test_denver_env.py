"""Tests for denver.py env resolution & listing."""

from pathlib import Path

import pytest

import denver


# ---- _default_denver_dir --------------------------------------------------- #
def test_default_denver_dir_checkout_layout(monkeypatch, tmp_path):
    pkg_dir = tmp_path / "checkout" / "src"
    (pkg_dir / "denver_providers").mkdir(parents=True)
    monkeypatch.setattr(denver, "DENVER_PKG_DIR", pkg_dir)
    assert denver._default_denver_dir() == pkg_dir.parent


def test_default_denver_dir_installed_layout_defaults_to_home(monkeypatch, tmp_path):
    # e.g. a wheel install: this file's own dir isn't a checkout's src/ at all
    pkg_dir = tmp_path / "site-packages"
    pkg_dir.mkdir()
    monkeypatch.setattr(denver, "DENVER_PKG_DIR", pkg_dir)
    monkeypatch.delenv("DENVER_STATE_DIR", raising=False)
    assert denver._default_denver_dir() == Path("~/.denver").expanduser()


def test_default_denver_dir_installed_layout_honours_state_dir_override(monkeypatch, tmp_path):
    pkg_dir = tmp_path / "site-packages"
    pkg_dir.mkdir()
    monkeypatch.setattr(denver, "DENVER_PKG_DIR", pkg_dir)
    state_dir = tmp_path / "custom-state"
    monkeypatch.setenv("DENVER_STATE_DIR", str(state_dir))
    assert denver._default_denver_dir() == state_dir


def test_default_denver_dir_src_named_dir_without_providers_sibling(monkeypatch, tmp_path):
    # named 'src' but no providers/ alongside it -- not a real denver checkout
    pkg_dir = tmp_path / "checkout" / "src"
    pkg_dir.mkdir(parents=True)
    monkeypatch.setattr(denver, "DENVER_PKG_DIR", pkg_dir)
    monkeypatch.delenv("DENVER_STATE_DIR", raising=False)
    assert denver._default_denver_dir() == Path("~/.denver").expanduser()


# ---- resolve_env_dir -------------------------------------------------------#
def test_resolve_env_dir_existing_directory(tmp_path):
    envd = tmp_path / "myenv"
    envd.mkdir()
    assert denver.resolve_env_dir(str(envd)) == (envd.resolve(), envd.resolve() / "denver.toml")


def test_resolve_env_dir_direct_file(tmp_path):
    envd = tmp_path / "myenv"
    envd.mkdir()
    toml_path = envd / "denver.toml"
    toml_path.write_text('stages = [\n  "uv",\n]\n')
    assert denver.resolve_env_dir(str(toml_path)) == (envd.resolve(), toml_path.resolve())


def test_resolve_env_dir_custom_named_toml_file(tmp_path):
    # a folder may hold several denver.xxx.toml variants side by side --
    # pointing straight at one must resolve to *that* file, not the default
    # denver.toml name, so run_stages/run_named_scripts re-invoke the same one.
    envd = tmp_path / "myenv"
    envd.mkdir()
    toml_path = envd / "denver.debug.toml"
    toml_path.write_text('stages = ["uv"]\n')
    assert denver.resolve_env_dir(str(toml_path)) == (envd.resolve(), toml_path.resolve())


def test_resolve_env_dir_not_found_dies(tmp_path):
    with pytest.raises(SystemExit):
        denver.resolve_env_dir(str(tmp_path / "does-not-exist"))


# ---- is_runnable_env -------------------------------------------------------#
def test_is_runnable_env_true_by_default(tmp_path):
    toml_path = tmp_path / "denver.toml"
    toml_path.write_text('stages = ["uv"]\n')
    assert denver.is_runnable_env(toml_path)


def test_is_runnable_env_true_when_explicit(tmp_path):
    toml_path = tmp_path / "denver.toml"
    toml_path.write_text('stages = ["uv"]\nrunnable = true\n')
    assert denver.is_runnable_env(toml_path)


def test_is_runnable_env_false_when_explicit(tmp_path):
    toml_path = tmp_path / "denver.toml"
    toml_path.write_text('stages = ["uv"]\nrunnable = false\n')
    assert not denver.is_runnable_env(toml_path)
