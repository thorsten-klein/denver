"""Tests for providers.conan_scripts.recipes -- the bundled recipe pipeline tool
conan.py invokes as a subprocess (see providers/conan.py).

recipes.py talks to a real ConanAPI() when actually used; here every test
either exercises pure logic (ConfigFile-free path resolution, argument
parsing) or replaces the module-level ``conan_api`` with a small fake, so no
test ever touches a real conan installation or home directory (see
recipes._real_conan_api's docstring for why that matters).
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest
import yaml
from conan.api.model import PkgReference, RecipeReference, Remote
from conan.internal.errors import AuthenticationException, ConanConnectionError, NotFoundException

from providers.conan_scripts import recipes


# --------------------------------------------------------------------------- #
# conan_api: lazy, memoized construction (through the public interface, not
# implementation details -- see _real_conan_api's docstring)
# --------------------------------------------------------------------------- #
def test_conan_api_constructs_lazily_and_reuses_instance(monkeypatch):
    constructed = []

    class Fake:
        def __init__(self):
            constructed.append(True)
            self.remotes = "the-remotes-object"

    monkeypatch.setattr(recipes, "ConanAPI", Fake)
    recipes._real_conan_api.cache_clear()
    try:
        assert constructed == []
        assert recipes.conan_api.remotes == "the-remotes-object"
        assert constructed == [True]
        assert recipes.conan_api.remotes == "the-remotes-object"
        assert constructed == [True]  # reused, not reconstructed
    finally:
        recipes._real_conan_api.cache_clear()


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def test_redirect_swaps_and_restores_streams():
    original_out, original_err = sys.stdout, sys.stderr
    with recipes.redirect():
        assert sys.stdout is not original_out
        assert sys.stderr is not original_err
    assert sys.stdout is original_out
    assert sys.stderr is original_err


# --------------------------------------------------------------------------- #
# get_cache_path
# --------------------------------------------------------------------------- #
def test_get_cache_path_recipe_reference(monkeypatch):
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")

    class FakeCache:
        def export_path(self, r):
            return "/some/path"

    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(cache=FakeCache()))
    assert recipes.get_cache_path(ref) == Path("/some/path")


def test_get_cache_path_package_reference(monkeypatch):
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")
    pref = PkgReference(ref, "pkgid", "rev")

    class FakeCache:
        def package_path(self, p):
            return "/some/pkg/path"

    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(cache=FakeCache()))
    assert recipes.get_cache_path(pref) == Path("/some/pkg/path")


def test_get_cache_path_unsupported_type():
    with pytest.raises(recipes.CatalogError):
        recipes.get_cache_path("not-a-ref")


def test_get_cache_path_swallows_conan_not_found(monkeypatch):
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")

    class FakeCache:
        def export_path(self, r):
            raise recipes.ConanException("not found in cache")

    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(cache=FakeCache()))
    assert recipes.get_cache_path(ref) is None


def test_get_cache_path_propagates_other_errors(monkeypatch):
    # a permission error or corrupt cache must not look like "not cached"
    # (B7) -- only conan's own ConanException is treated as a cache miss.
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")

    class FakeCache:
        def export_path(self, r):
            raise RuntimeError("boom")

    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(cache=FakeCache()))
    with pytest.raises(RuntimeError, match="boom"):
        recipes.get_cache_path(ref)


# --------------------------------------------------------------------------- #
# catalog.yml / recipe resolution
# --------------------------------------------------------------------------- #
def test_get_recipes_from_catalog_skips_dotted_keys(tmp_path):
    catalog_yml = tmp_path / "catalog.yml"
    catalog_yml.write_text(
        yaml.safe_dump({
            ".version": "1.2.3",
            "foo/1.0": "foo/1.0@denver/snapshot",
        })
    )
    recipes_ref = recipes.get_recipes_from_catalog(tmp_path, catalog_yml)
    assert len(recipes_ref) == 1
    (path, ref) = next(iter(recipes_ref.items()))
    assert path == (tmp_path / "foo" / "1.0" / "conanfile.py").absolute()
    assert ref.name == "foo"


def test_handle_args_recipe_by_dir(tmp_path):
    catalog_yml = tmp_path / "catalog.yml"
    catalog_yml.write_text(yaml.safe_dump({"foo/1.0": "foo/1.0@denver/snapshot"}))
    recipe_dir = tmp_path / "foo" / "1.0"
    recipe_dir.mkdir(parents=True)
    filtered = recipes.handle_args_recipe(tmp_path, catalog_yml, [str(recipe_dir)])
    assert len(filtered) == 1


def test_handle_args_recipe_unknown_dies(tmp_path):
    catalog_yml = tmp_path / "catalog.yml"
    catalog_yml.write_text(yaml.safe_dump({"foo/1.0": "foo/1.0@denver/snapshot"}))
    with pytest.raises(recipes.CatalogError):
        recipes.handle_args_recipe(tmp_path, catalog_yml, ["bar/2.0"])


# --------------------------------------------------------------------------- #
# authenticate_remote
# --------------------------------------------------------------------------- #
def test_authenticate_remote_success_no_prompt(monkeypatch):
    calls = []
    fake_api = types.SimpleNamespace(
        remotes=types.SimpleNamespace(user_auth=lambda r, force=False: calls.append((r.name, force)))
    )
    monkeypatch.setattr(recipes, "conan_api", fake_api)
    prompted = []
    monkeypatch.setattr(recipes, "_prompt_and_login", lambda r: prompted.append(r.name))

    recipes.authenticate_remote(Remote("sdd", "http://sdd"))

    assert calls == [("sdd", False)]
    assert prompted == []


def test_authenticate_remote_force_passed_through(monkeypatch):
    calls = []
    fake_api = types.SimpleNamespace(
        remotes=types.SimpleNamespace(user_auth=lambda r, force=False: calls.append(force))
    )
    monkeypatch.setattr(recipes, "conan_api", fake_api)

    recipes.authenticate_remote(Remote("sdd", "http://sdd"), force=True)

    assert calls == [True]


def test_authenticate_remote_prompts_on_failure_when_interactive(monkeypatch):
    # stale credentials.json entry -> conan's own user_auth fails without
    # ever prompting itself (see authenticate_remote's docstring); denver
    # falls back to its own interactive prompt when stdin is a TTY.
    def failing_user_auth(r, force=False):
        raise AuthenticationException("Wrong user or password")

    fake_api = types.SimpleNamespace(remotes=types.SimpleNamespace(user_auth=failing_user_auth))
    monkeypatch.setattr(recipes, "conan_api", fake_api)
    monkeypatch.setattr(recipes.sys.stdin, "isatty", lambda: True)
    prompted = []
    monkeypatch.setattr(recipes, "_prompt_and_login", lambda r: prompted.append(r.name))

    recipes.authenticate_remote(Remote("sdd", "http://sdd"))

    assert prompted == ["sdd"]


def test_authenticate_remote_reraises_when_not_interactive(monkeypatch):
    def failing_user_auth(r, force=False):
        raise AuthenticationException("Wrong user or password")

    fake_api = types.SimpleNamespace(remotes=types.SimpleNamespace(user_auth=failing_user_auth))
    monkeypatch.setattr(recipes, "conan_api", fake_api)
    monkeypatch.setattr(recipes.sys.stdin, "isatty", lambda: False)
    prompted = []
    monkeypatch.setattr(recipes, "_prompt_and_login", lambda r: prompted.append(r.name))

    with pytest.raises(AuthenticationException):
        recipes.authenticate_remote(Remote("sdd", "http://sdd"))
    assert prompted == []


def test_prompt_and_login_uses_input_and_getpass(monkeypatch):
    logged_in = []
    fake_api = types.SimpleNamespace(
        remotes=types.SimpleNamespace(user_login=lambda r, u, p: logged_in.append((r.name, u, p)))
    )
    monkeypatch.setattr(recipes, "conan_api", fake_api)
    monkeypatch.setattr("builtins.input", lambda prompt="": "alice")
    monkeypatch.setattr(recipes.getpass, "getpass", lambda prompt="": "s3cret")

    recipes._prompt_and_login(Remote("sdd", "http://sdd"))

    assert logged_in == [("sdd", "alice", "s3cret")]


# --------------------------------------------------------------------------- #
# get_deps_graph_remote / get_deps_graph_local / get_pref_from_ref
# --------------------------------------------------------------------------- #
class FakeProfilesAPI:
    def get_default_host(self):
        return "host-profile"

    def get_default_build(self):
        return "build-profile"

    def get_profile(self, paths):
        return f"profile:{paths[0]}"


def test_get_deps_graph_remote_authenticates_each_remote(monkeypatch):
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")
    remotes = [Remote("a", "http://a"), Remote("b", "http://b")]
    authed = []

    fake_api = types.SimpleNamespace(
        lockfile=types.SimpleNamespace(get_lockfile=lambda: "lockfile"),
        profiles=FakeProfilesAPI(),
        remotes=types.SimpleNamespace(
            list=lambda only_enabled: remotes, user_auth=lambda r, force=False: authed.append(r.name)
        ),
        graph=types.SimpleNamespace(load_graph_requires=lambda *a, **k: "deps-graph"),
    )
    monkeypatch.setattr(recipes, "conan_api", fake_api)

    result = recipes.get_deps_graph_remote(ref)
    assert result == "deps-graph"
    assert authed == ["a", "b"]


def test_get_deps_graph_local(monkeypatch):
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")
    analyzed = []
    fake_api = types.SimpleNamespace(
        lockfile=types.SimpleNamespace(get_lockfile=lambda: "lockfile"),
        profiles=FakeProfilesAPI(),
        graph=types.SimpleNamespace(
            load_graph_consumer=lambda *a, **k: "deps-graph",
            analyze_binaries=lambda g, remotes: analyzed.append((g, remotes)),
        ),
    )
    monkeypatch.setattr(recipes, "conan_api", fake_api)

    result = recipes.get_deps_graph_local(Path("/recipe"), ref)
    assert result == "deps-graph"
    assert analyzed == [("deps-graph", [])]


def test_get_pref_from_ref_success(monkeypatch):
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")
    fake_pref = types.SimpleNamespace(package_id="pkgid", revision="rev")
    fake_graph = types.SimpleNamespace(root=types.SimpleNamespace(pref=fake_pref))
    monkeypatch.setattr(recipes, "get_deps_graph_local", lambda recipe_path, r: fake_graph)

    result = recipes.get_pref_from_ref(Path("/recipe"), ref)
    assert result.ref == ref
    assert result.package_id == "pkgid"
    assert result.revision == "rev"


def test_get_pref_from_ref_wraps_exception(monkeypatch, capsys):
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")

    def boom(recipe_path, r):
        raise RuntimeError("no local export")

    monkeypatch.setattr(recipes, "get_deps_graph_local", boom)
    with pytest.raises(recipes.CatalogError, match="Cannot resolve"):
        recipes.get_pref_from_ref(Path("/recipe"), ref)
    assert "no local export" in capsys.readouterr().err


def test_get_recipes_prefs_uses_get_pref_from_ref(monkeypatch):
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")
    pref = PkgReference(ref, "id", "rev")
    monkeypatch.setattr(recipes, "get_pref_from_ref", lambda recipe_path, r: pref)
    result = recipes.get_recipes_prefs({Path("/recipe"): ref})
    assert result == {Path("/recipe").absolute(): pref}


# --------------------------------------------------------------------------- #
# conan_list / find_pref
# --------------------------------------------------------------------------- #
def _pref():
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")
    return PkgReference(ref, "pkgid", "rev")


def test_conan_list_local_cache(monkeypatch):
    pref = _pref()

    class FakeList:
        def package_revisions(self, p, remote=None):
            assert remote is None
            return ["rev1"]

    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(list=FakeList(), remotes=None))
    found, remote = recipes.conan_list(pref)
    assert found == ["rev1"]
    assert remote is None


def test_conan_list_skips_disabled_remote(monkeypatch):
    pref = _pref()
    disabled_remote = Remote("d", "http://d", disabled=True)

    class FakeRemotes:
        def get(self, name):
            return disabled_remote

    class FakeList:
        def package_revisions(self, p, remote=None):
            raise AssertionError("must not be called for a disabled remote")

    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(list=FakeList(), remotes=FakeRemotes()))
    found, remote = recipes.conan_list(pref, remotes=["d"])
    assert found == []
    assert remote is None


def test_conan_list_not_found_tolerated(monkeypatch):
    pref = _pref()

    class FakeList:
        def package_revisions(self, p, remote=None):
            raise NotFoundException("nope")

    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(list=FakeList(), remotes=None))
    found, remote = recipes.conan_list(pref)
    assert found == []
    assert remote is None


def test_find_pref_reports_local_and_remote(monkeypatch, capsys):
    pref = _pref()
    remote = Remote("r", "http://r")

    class FakeList:
        def package_revisions(self, p, remote=None):
            return ["rev"]

    class FakeRemotes:
        def get(self, name):
            return remote

    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(list=FakeList(), remotes=FakeRemotes()))
    found, found_remote = recipes.find_pref(pref, remotes=["r"])
    assert found == ["rev"]
    assert found_remote is remote
    assert "already in remote" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# conan_remotes_list / conan_ensure_remotes / conan_enable_remotes / conan_login
# --------------------------------------------------------------------------- #
class FakeRemotesAPI:
    def __init__(self, remotes):
        self._remotes = list(remotes)
        self.added = []
        self.removed = []
        self.enabled = []
        self.disabled = []
        self.auth_calls = []
        self._user_info = {}

    def list(self, only_enabled=False):
        return list(self._remotes)

    def get(self, name):
        return next(r for r in self._remotes if r.name == name)

    def add(self, remote, index=0):
        self._remotes.insert(index, remote)
        self.added.append(remote)

    def remove(self, name):
        self._remotes = [r for r in self._remotes if r.name != name]
        self.removed.append(name)

    def enable(self, name):
        self.enabled.append(name)

    def disable(self, name):
        self.disabled.append(name)

    def user_auth(self, remote, force=False):
        self.auth_calls.append((remote.name, force))

    def user_info(self, remote):
        return self._user_info.get(remote.name, {})


def test_conan_remotes_list(monkeypatch):
    remotes = [Remote("a", "http://a"), Remote("b", "http://b")]
    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(remotes=FakeRemotesAPI(remotes)))
    result = recipes.conan_remotes_list()
    assert set(result) == {"a", "b"}


def test_conan_ensure_remotes_adds_new(monkeypatch):
    api = FakeRemotesAPI([])
    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(remotes=api))
    recipes.conan_ensure_remotes({"sdd": {"url": "http://sdd", "verify_ssl": False}})
    assert [r.name for r in api.added] == ["sdd"]
    assert api.added[0].verify_ssl is False


def test_conan_ensure_remotes_renames_on_url_match(monkeypatch, capsys):
    api = FakeRemotesAPI([Remote("old-name", "http://sdd")])
    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(remotes=api))
    recipes.conan_ensure_remotes({"new-name": {"url": "http://sdd"}})
    assert "old-name" in api.removed
    assert "Old name: old-name" in capsys.readouterr().out
    assert any(r.name == "new-name" for r in api.added)


def test_conan_ensure_remotes_skips_when_already_correct(monkeypatch):
    api = FakeRemotesAPI([Remote("sdd", "http://sdd", verify_ssl=True)])
    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(remotes=api))
    recipes.conan_ensure_remotes({"sdd": {"url": "http://sdd", "verify_ssl": True}})
    assert api.removed == []
    assert api.added == []


def test_conan_ensure_remotes_replaces_when_different(monkeypatch):
    api = FakeRemotesAPI([Remote("sdd", "http://sdd", verify_ssl=True)])
    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(remotes=api))
    recipes.conan_ensure_remotes({"sdd": {"url": "http://sdd", "verify_ssl": False}})
    assert "sdd" in api.removed
    assert any(r.name == "sdd" and r.verify_ssl is False for r in api.added)


@pytest.mark.parametrize(
    "remote_name, config, env, expect_enabled, expect_disabled",
    [
        ("unmanaged", {}, None, [], ["unmanaged"]),
        ("sdd", {"sdd": {"enabled": True}}, None, ["sdd"], []),
        ("sdd", {"sdd": {"enabled": True}}, "OFF", [], ["sdd"]),
        ("sdd", {}, "ON", ["sdd"], []),
    ],
    ids=["default-disabled", "configured-enabled", "env-var-overrides-off", "env-var-overrides-on"],
)
def test_conan_enable_remotes(monkeypatch, remote_name, config, env, expect_enabled, expect_disabled):
    api = FakeRemotesAPI([Remote(remote_name, f"http://{remote_name}")])
    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(remotes=api))
    if env is not None:
        monkeypatch.setenv(f"CONAN_REMOTE_ENABLE_{remote_name.upper()}", env)
    recipes.conan_enable_remotes(config)
    assert api.enabled == expect_enabled
    assert api.disabled == expect_disabled


def test_conan_login_skips_disabled(monkeypatch):
    api = FakeRemotesAPI([Remote("sdd", "http://sdd", disabled=True)])
    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(remotes=api))
    recipes.conan_login({"sdd": {}})
    assert api.auth_calls == []


def test_conan_login_authenticates_when_needed(monkeypatch):
    api = FakeRemotesAPI([Remote("sdd", "http://sdd")])
    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(remotes=api))
    recipes.conan_login({"sdd": {}})
    assert api.auth_calls == [("sdd", True)]


def test_conan_login_skips_when_already_authenticated(monkeypatch):
    api = FakeRemotesAPI([Remote("sdd", "http://sdd")])
    api._user_info["sdd"] = {"authenticated": True, "username": "u"}
    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(remotes=api))
    recipes.conan_login({"sdd": {}})
    assert api.auth_calls == []


def test_conan_login_force_reauthenticates(monkeypatch):
    api = FakeRemotesAPI([Remote("sdd", "http://sdd")])
    api._user_info["sdd"] = {"authenticated": True, "username": "u"}
    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(remotes=api))
    recipes.conan_login({"sdd": {}}, force=True)
    assert api.auth_calls == [("sdd", True)]


def test_conan_login_skips_when_username_unchanged(monkeypatch):
    api = FakeRemotesAPI([Remote("sdd", "http://sdd")])
    api._user_info["sdd"] = {"username": "denver-bot"}
    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(remotes=api))
    monkeypatch.setenv("CONAN_LOGIN_USERNAME_SDD", "denver-bot")
    recipes.conan_login({"sdd": {}})
    assert api.auth_calls == []


def test_conan_login_warns_on_connection_error(monkeypatch, capsys):
    class FailingAuthRemotesAPI(FakeRemotesAPI):
        def user_auth(self, remote, force=False):
            raise ConanConnectionError("unreachable")

    api = FailingAuthRemotesAPI([Remote("sdd", "http://sdd")])
    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(remotes=api))
    recipes.conan_login({"sdd": {}})
    assert "Unable to connect" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# prepare()
# --------------------------------------------------------------------------- #
def test_prepare_noop_on_empty_remotes(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(recipes, "conan_ensure_remotes", lambda r: calls.append("ensure"))
    monkeypatch.setattr(recipes, "conan_enable_remotes", lambda r: calls.append("enable"))
    monkeypatch.setattr(recipes, "conan_login", lambda r, force=False: calls.append("login"))

    recipes.prepare({})

    assert calls == []
    assert "leaving conan's remote config as-is" in capsys.readouterr().out


def test_prepare_cleanup_runs_even_on_empty_remotes(monkeypatch):
    # cleanup=True (denver.yml's conan.cleanup-remotes:, default on) treats
    # {} as the exhaustive list too, so conan_enable_remotes({}) still runs
    # -- it's what actually disables every already-present remote.
    calls = []
    monkeypatch.setattr(recipes, "conan_ensure_remotes", lambda r: calls.append(("ensure", r)))
    monkeypatch.setattr(recipes, "conan_enable_remotes", lambda r: calls.append(("enable", r)))
    monkeypatch.setattr(recipes, "conan_login", lambda r, force=False: calls.append(("login", r)))

    recipes.prepare({}, cleanup=True)

    assert [c[0] for c in calls] == ["ensure", "enable", "login"]
    assert all(c[1] == {} for c in calls)


def test_prepare_runs_all_three_when_remotes_configured(monkeypatch):
    calls = []
    monkeypatch.setattr(recipes, "conan_ensure_remotes", lambda r: calls.append(("ensure", r)))
    monkeypatch.setattr(recipes, "conan_enable_remotes", lambda r: calls.append(("enable", r)))
    monkeypatch.setattr(recipes, "conan_login", lambda r, force=False: calls.append(("login", r)))

    remotes = {"sdd": {"url": "http://sdd"}}
    recipes.prepare(remotes)

    assert [c[0] for c in calls] == ["ensure", "enable", "login"]
    assert all(c[1] == remotes for c in calls)


def test_prepare_passes_force_to_login(monkeypatch):
    seen = {}
    monkeypatch.setattr(recipes, "conan_ensure_remotes", lambda r: None)
    monkeypatch.setattr(recipes, "conan_enable_remotes", lambda r: None)
    monkeypatch.setattr(recipes, "conan_login", lambda r, force=False: seen.setdefault("force", force))

    recipes.prepare({"sdd": {}}, force=True)

    assert seen["force"] is True


# --------------------------------------------------------------------------- #
# generate_catalog: invoked via sys.executable, not the script's exec bit
# --------------------------------------------------------------------------- #
def test_generate_catalog_invokes_via_interpreter(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, check):
        captured["cmd"] = cmd
        captured["check"] = check

    monkeypatch.setattr(recipes.subprocess, "run", fake_run)
    recipes.generate_catalog(tmp_path / "recipes", tmp_path / "catalog.yml")

    assert captured["cmd"][0] == sys.executable
    assert captured["cmd"][1].endswith("build_catalog.py")
    assert captured["check"] is True
    assert "--user=denver" in captured["cmd"]
    assert "--channel=snapshot" in captured["cmd"]


def test_generate_catalog_passes_custom_user_channel(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, check):
        captured["cmd"] = cmd

    monkeypatch.setattr(recipes.subprocess, "run", fake_run)
    recipes.generate_catalog(tmp_path / "recipes", tmp_path / "catalog.yml", user="acme", channel="stable")

    assert "--user=acme" in captured["cmd"]
    assert "--channel=stable" in captured["cmd"]


# --------------------------------------------------------------------------- #
# export / test / create / upload / run_ci / needs_export
# --------------------------------------------------------------------------- #
def test_export_skips_when_already_cached(monkeypatch, capsys):
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")
    monkeypatch.setattr(recipes, "get_cache_path", lambda r: Path("/cached"))
    recipes.export(Path("/recipe"), ref)
    assert "already exported" in capsys.readouterr().out


def test_export_exports_and_warns_if_verification_fails(monkeypatch):
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")
    calls = {"n": 0}

    def fake_get_cache_path(r):
        calls["n"] += 1  # never verified as cached -> triggers the warning

    exported = []
    monkeypatch.setattr(recipes, "get_cache_path", fake_get_cache_path)
    monkeypatch.setattr(
        recipes,
        "conan_api",
        types.SimpleNamespace(export=types.SimpleNamespace(export=lambda *a, **k: exported.append((a, k)))),
    )
    recipes.export(Path("/recipe"), ref)
    assert len(exported) == 1


def test_test_skips_when_no_test_package(tmp_path, capsys):
    recipe_path = tmp_path / "conanfile.py"
    recipe_path.write_text("x")
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")
    pref = PkgReference(ref, "id", "rev")
    recipes.test(recipe_path, pref)
    assert "No test_package exists" in capsys.readouterr().out


def test_test_runs_run_test_when_test_package_present(monkeypatch, tmp_path):
    recipe_path = tmp_path / "conanfile.py"
    recipe_path.write_text("x")
    (tmp_path / "test_package").mkdir()
    (tmp_path / "test_package" / "conanfile.py").write_text("x")
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")
    pref = PkgReference(ref, "id", "rev")

    monkeypatch.setattr(
        recipes,
        "conan_api",
        types.SimpleNamespace(
            profiles=FakeProfilesAPI(), lockfile=types.SimpleNamespace(get_lockfile=lambda: "lockfile")
        ),
    )
    called = []
    monkeypatch.setattr(recipes, "run_test", lambda *a, **k: called.append((a, k)))

    recipes.test(recipe_path, pref)
    assert len(called) == 1


def test_find_pref_reports_local_cache(monkeypatch, capsys):
    pref = _pref()

    class FakeList:
        def package_revisions(self, p, remote=None):
            return ["rev"]

    monkeypatch.setattr(recipes, "conan_api", types.SimpleNamespace(list=FakeList(), remotes=None))
    found, remote = recipes.find_pref(pref, remotes=None)
    assert found == ["rev"]
    assert remote is None
    assert "already in local cache" in capsys.readouterr().out


def test_needs_export_false_when_cached(monkeypatch):
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")
    monkeypatch.setattr(recipes, "get_cache_path", lambda r: Path("/cached"))
    assert recipes.needs_export(ref) is False


def test_needs_export_true_when_graph_errors(monkeypatch):
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")
    monkeypatch.setattr(recipes, "get_cache_path", lambda r: None)
    monkeypatch.setattr(recipes, "get_deps_graph_remote", lambda r: types.SimpleNamespace(error="boom"))
    assert recipes.needs_export(ref) is True


def test_run_ci_skips_when_found_remote(monkeypatch):
    called = {"create": False}
    monkeypatch.setattr(recipes, "find_pref", lambda pref, remotes: (["found"], None))
    monkeypatch.setattr(recipes, "create", lambda *a: called.__setitem__("create", True))
    recipes.run_ci(Path("/r"), object(), ["remote"])
    assert called["create"] is False


def test_run_ci_creates_when_missing_everywhere(monkeypatch):
    results = iter([([], None), ([], None)])
    monkeypatch.setattr(recipes, "find_pref", lambda pref, remotes: next(results))
    created = []
    monkeypatch.setattr(recipes, "create", lambda recipe_path, pref: created.append((recipe_path, pref)))
    recipes.run_ci(Path("/r"), "pref", ["remote"])
    assert created == [(Path("/r"), "pref")]


def test_upload_skips_when_already_present(monkeypatch):
    called = []
    monkeypatch.setattr(recipes, "find_pref", lambda pref, remotes: (["found"], None))
    monkeypatch.setattr(recipes.subprocess, "run", lambda *a, **k: called.append(a))
    recipes.upload("pref", "remote")
    assert called == []


def test_upload_runs_conan_upload(monkeypatch):
    called = []
    monkeypatch.setattr(recipes, "find_pref", lambda pref, remotes: ([], None))
    monkeypatch.setattr(recipes.subprocess, "run", lambda cmd, check: called.append(cmd))
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")
    pref = PkgReference(ref, "id", "rev")
    recipes.upload(pref, "remote")
    assert called and called[0][0] == "conan"


def test_create_skips_and_tests_when_found(monkeypatch):
    tested = []
    monkeypatch.setattr(recipes, "find_pref", lambda pref, remotes: (["found"], None))
    monkeypatch.setattr(recipes, "test", lambda recipe_path, pref: tested.append(recipe_path))
    recipes.create(Path("/r"), "pref")
    assert tested == [Path("/r")]


def test_create_runs_conan_create_when_missing(monkeypatch):
    called = []
    monkeypatch.setattr(recipes, "find_pref", lambda pref, remotes: ([], None))
    monkeypatch.setattr(recipes.subprocess, "run", lambda cmd, check: called.append(cmd))
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")
    pref = PkgReference(ref, "id", "rev")
    recipes.create(Path("/r"), pref)
    assert called and called[0][0] == "conan"


# --------------------------------------------------------------------------- #
# main(): argument parsing
# --------------------------------------------------------------------------- #
def _stub_pipeline(monkeypatch):
    """Neutralise everything main() calls beyond argument handling +
    prepare(), so these tests exercise CLI/config wiring only."""
    monkeypatch.setattr(recipes, "prepare", lambda remotes, cleanup=False, force=False: None)
    monkeypatch.setattr(recipes, "generate_catalog", lambda *a, **k: None)
    monkeypatch.setattr(recipes, "get_recipes_from_catalog", lambda *a: {})
    monkeypatch.setattr(recipes, "handle_args_recipe", lambda *a: {})
    monkeypatch.setattr(recipes, "get_recipes_prefs", lambda *a: {})


def test_main_ci_without_remote_errors(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["recipes.py", "--ci"])
    with pytest.raises(SystemExit):
        recipes.main()
    assert "--remote" in capsys.readouterr().err


def test_main_upload_without_remote_errors(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["recipes.py", "--upload"])
    with pytest.raises(SystemExit):
        recipes.main()


def test_main_prepare_only_returns_before_generate(monkeypatch, tmp_path):
    _stub_pipeline(monkeypatch)
    generate_called = []
    monkeypatch.setattr(recipes, "generate_catalog", lambda *a, **k: generate_called.append(a))
    monkeypatch.setattr(sys, "argv", ["recipes.py", "--prepare"])
    recipes.main()
    assert generate_called == []


def test_main_loads_remotes_json(monkeypatch, tmp_path):
    remotes = {"sdd": {"url": "http://sdd"}}
    remotes_json = tmp_path / "remotes.json"
    remotes_json.write_text(json.dumps(remotes))

    seen = {}
    monkeypatch.setattr(recipes, "prepare", lambda r, cleanup=False, force=False: seen.setdefault("remotes", r))
    monkeypatch.setattr(sys, "argv", ["recipes.py", "--prepare", f"--remotes-json={remotes_json}"])
    recipes.main()

    assert seen["remotes"] == remotes


def test_main_no_remotes_json_passes_empty_dict(monkeypatch):
    seen = {}
    monkeypatch.setattr(recipes, "prepare", lambda r, cleanup=False, force=False: seen.setdefault("remotes", r))
    monkeypatch.setattr(sys, "argv", ["recipes.py", "--prepare"])
    recipes.main()
    assert seen["remotes"] == {}


def test_main_export_pipeline_default_catalog(monkeypatch, tmp_path):
    _stub_pipeline(monkeypatch)
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")
    monkeypatch.setattr(recipes, "get_recipes_from_catalog", lambda *a: {Path("/r"): ref})
    monkeypatch.setattr(recipes, "needs_export", lambda r: True)
    exported = []
    monkeypatch.setattr(recipes, "export", lambda recipe_path, r: exported.append((recipe_path, r)))
    monkeypatch.setattr(recipes, "get_recipes_prefs", lambda refs: {})

    monkeypatch.setattr(
        sys,
        "argv",
        ["recipes.py", "--export", f"--recipes-dir={tmp_path}", f"--catalog-yml={tmp_path / 'catalog.yml'}"],
    )
    recipes.main()

    assert exported == [(Path("/r"), ref)]


def test_main_recipes_positional_uses_handle_args_recipe(monkeypatch, tmp_path):
    _stub_pipeline(monkeypatch)
    handled = []
    monkeypatch.setattr(recipes, "handle_args_recipe", lambda d, c, recipes: handled.append(recipes) or {})
    monkeypatch.setattr(
        sys,
        "argv",
        ["recipes.py", f"--recipes-dir={tmp_path}", f"--catalog-yml={tmp_path / 'catalog.yml'}", "foo/1.0"],
    )
    recipes.main()
    assert handled == [["foo/1.0"]]


def test_main_no_generate_skips_catalog_generation(monkeypatch, tmp_path):
    _stub_pipeline(monkeypatch)
    generated = []
    monkeypatch.setattr(recipes, "generate_catalog", lambda *a, **k: generated.append(a))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recipes.py",
            "--no-generate",
            f"--recipes-dir={tmp_path}",
            f"--catalog-yml={tmp_path / 'catalog.yml'}",
        ],
    )
    recipes.main()
    assert generated == []


def test_main_requires_recipes_dir_and_catalog_yml_without_prepare(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["recipes.py"])
    with pytest.raises(SystemExit):
        recipes.main()
    assert "--recipes-dir" in capsys.readouterr().err


def test_main_requires_catalog_yml_even_with_recipes_dir(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["recipes.py", f"--recipes-dir={tmp_path}"])
    with pytest.raises(SystemExit):
        recipes.main()
    assert "--recipes-dir" in capsys.readouterr().err


def test_main_ci_and_upload_flow(monkeypatch, tmp_path):
    _stub_pipeline(monkeypatch)
    ref = RecipeReference.loads("foo/1.0@denver/snapshot")
    pref = PkgReference(ref, "id", "rev")
    monkeypatch.setattr(recipes, "get_recipes_from_catalog", lambda *a: {Path("/r"): ref})
    monkeypatch.setattr(recipes, "get_recipes_prefs", lambda refs: {Path("/r"): pref})
    ci_calls, upload_calls, create_calls = [], [], []
    monkeypatch.setattr(recipes, "run_ci", lambda recipe_path, p, remotes: ci_calls.append((recipe_path, p, remotes)))
    monkeypatch.setattr(recipes, "upload", lambda p, remote: upload_calls.append((p, remote)))
    monkeypatch.setattr(recipes, "create", lambda recipe_path, p: create_calls.append((recipe_path, p)))

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recipes.py",
            "--create",
            "--ci",
            "--upload",
            "--remote=sdd",
            f"--recipes-dir={tmp_path}",
            f"--catalog-yml={tmp_path / 'catalog.yml'}",
        ],
    )
    recipes.main()

    assert create_calls == [(Path("/r"), pref)]
    assert ci_calls == [(Path("/r"), pref, ["sdd"])]
    assert upload_calls == [(pref, "sdd")]


def test_main_base_classes_dir_explicit(monkeypatch, tmp_path):
    _stub_pipeline(monkeypatch)
    base_classes = tmp_path / "bc"
    base_classes.mkdir()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recipes.py",
            "--prepare",
            f"--base-classes-dir={base_classes}",
        ],
    )
    recipes.main()
    assert str(base_classes.resolve()) in sys.path
