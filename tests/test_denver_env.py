"""Tests for denver.py env resolution & listing."""

from pathlib import Path

import pytest

import denver


# ---- _default_denver_dir --------------------------------------------------- #
def test_default_denver_dir_checkout_layout(monkeypatch, tmp_path):
    pkg_dir = tmp_path / "checkout" / "src"
    (pkg_dir / "providers").mkdir(parents=True)
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
    assert denver.resolve_env_dir(str(envd)) == (envd.resolve(), envd.resolve() / "denver.yml")


def test_resolve_env_dir_denver_yml_file(tmp_path):
    envd = tmp_path / "myenv"
    envd.mkdir()
    yml = envd / "denver.yml"
    yml.write_text("stages: [pip]\n")
    assert denver.resolve_env_dir(str(yml)) == (envd.resolve(), yml.resolve())


def test_resolve_env_dir_custom_named_yml_file(tmp_path):
    # a folder may hold several denver.xxx.yml variants side by side --
    # pointing straight at one must resolve to *that* file, not the default
    # denver.yml name, so run_stages/run_named_scripts re-invoke the same one.
    envd = tmp_path / "myenv"
    envd.mkdir()
    yml = envd / "denver.debug.yml"
    yml.write_text("stages: [pip]\n")
    assert denver.resolve_env_dir(str(yml)) == (envd.resolve(), yml.resolve())


def test_resolve_env_dir_not_found_dies(tmp_path):
    with pytest.raises(SystemExit):
        denver.resolve_env_dir(str(tmp_path / "does-not-exist"))


# ---- is_runnable_env -------------------------------------------------------#
def test_is_runnable_env_true_by_default(tmp_path):
    yml = tmp_path / "denver.yml"
    yml.write_text("stages: [pip]\n")
    assert denver.is_runnable_env(yml)


def test_is_runnable_env_true_when_explicit(tmp_path):
    yml = tmp_path / "denver.yml"
    yml.write_text("stages: [pip]\nrunnable: true\n")
    assert denver.is_runnable_env(yml)


def test_is_runnable_env_false_when_explicit(tmp_path):
    yml = tmp_path / "denver.yml"
    yml.write_text("stages: [pip]\nrunnable: false\n")
    assert not denver.is_runnable_env(yml)
