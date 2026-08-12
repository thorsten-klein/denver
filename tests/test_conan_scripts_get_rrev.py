"""Tests for providers.conan_scripts.get_rrev -- computes a recipe's
revision (RREV) by replicating conan's own conanmanifest.txt hashing, used
by build_catalog.py to keep catalog.yml's pinned revisions in sync with recipe
content.

cli.inspect() (real conan recipe inspection) is monkeypatched throughout --
see recipes.py's _real_conan_api docstring for why a real ConanAPI is never
constructed just by importing/testing this module.
"""

from __future__ import annotations

import types

import pytest
import yaml

from denver_providers.conan_scripts import get_rrev


# --------------------------------------------------------------------------- #
# cli: lazy, memoized construction (through the public interface, not
# implementation details -- see _local_api's docstring)
# --------------------------------------------------------------------------- #
def test_cli_constructs_lazily_and_reuses_instance(monkeypatch):
    constructed = []

    class FakeLocal:
        def inspect(self, *a, **k):
            return "inspected"

    class FakeConanAPI:
        def __init__(self):
            constructed.append(True)
            self.local = FakeLocal()

    monkeypatch.setattr(get_rrev.conan_api, "ConanAPI", FakeConanAPI)
    get_rrev._local_api.cache_clear()
    try:
        assert constructed == []
        assert get_rrev.cli.inspect() == "inspected"
        assert constructed == [True]
        assert get_rrev.cli.inspect() == "inspected"
        assert constructed == [True]  # reused, not reconstructed
    finally:
        get_rrev._local_api.cache_clear()


# --------------------------------------------------------------------------- #
# inspect()
# --------------------------------------------------------------------------- #
class FakeConanfile:
    def __init__(self, **attrs):
        self._attrs = attrs
        for k, v in attrs.items():
            setattr(self, k, v)

    def serialize(self):
        return dict(self._attrs)


def test_inspect_via_attribute(monkeypatch):
    conanfile = FakeConanfile(name="foo", version="1.0")
    monkeypatch.setattr(get_rrev, "cli", types.SimpleNamespace(inspect=lambda *a: conanfile))
    result = get_rrev.inspect("/some/conanfile.py", ["name", "version"])
    assert result == {"name": "foo", "version": "1.0"}


def test_inspect_via_serialized_dict(monkeypatch):
    class Conanfile:
        def serialize(self):
            return {"exports_sources": ["a.tar"]}

    monkeypatch.setattr(get_rrev, "cli", types.SimpleNamespace(inspect=lambda *a: Conanfile()))
    result = get_rrev.inspect("/some/conanfile.py", ["exports_sources"])
    assert result == {"exports_sources": ["a.tar"]}


def test_inspect_missing_attribute_raises(monkeypatch):
    class Conanfile:
        def serialize(self):
            return {}

    monkeypatch.setattr(get_rrev, "cli", types.SimpleNamespace(inspect=lambda *a: Conanfile()))
    with pytest.raises(get_rrev.GetRREVError):
        get_rrev.inspect("/some/conanfile.py", ["nope"])


# --------------------------------------------------------------------------- #
# compute_rrev()
# --------------------------------------------------------------------------- #
def _recipe_dir(tmp_path, name="foo", version="1.0", body="class Foo: pass\n"):
    d = tmp_path / name / version
    d.mkdir(parents=True)
    (d / "conanfile.py").write_text(body)
    return d


def _stub_inspect(monkeypatch, **attrs):
    monkeypatch.setattr(get_rrev, "inspect", lambda conanfile, attributes: dict(attrs))


def _stub_git_tracked(monkeypatch, tracked_paths):
    def fake_check_output(args):
        # args: ['git', '-C', dir, 'ls-files', path]
        path = args[-1]
        return (path + "\n").encode() if path in tracked_paths else b""

    monkeypatch.setattr(get_rrev.subprocess, "check_output", fake_check_output)


def test_get_rrev_no_exports_sources(tmp_path, monkeypatch):
    recipe_dir = _recipe_dir(tmp_path)
    _stub_inspect(monkeypatch, name="foo", version="1.0", exports_sources=[])

    name, version, rrev = get_rrev.compute_rrev(recipe_dir)
    assert (name, version) == ("foo", "1.0")
    assert rrev
    assert (recipe_dir / "conandata.yml").is_file()  # created since missing


def test_get_rrev_denver_conan_file_included_when_referenced(tmp_path, monkeypatch):
    recipe_dir = _recipe_dir(tmp_path, body="from DenverConanFile import DenverConanFile\n")
    (recipe_dir / "conandata.yml").write_text("{}")
    _stub_inspect(monkeypatch, name="foo", version="1.0", exports_sources=[])

    fake_module = types.SimpleNamespace(__file__=str(tmp_path / "DenverConanFile.py"))
    (tmp_path / "DenverConanFile.py").write_text("# stub\n")
    monkeypatch.setattr(get_rrev, "DenverConanFile", fake_module)

    _, _, rrev = get_rrev.compute_rrev(recipe_dir)
    assert rrev


def test_get_rrev_denver_conan_file_none_skipped(tmp_path, monkeypatch):
    recipe_dir = _recipe_dir(tmp_path, body="from DenverConanFile import DenverConanFile\n")
    (recipe_dir / "conandata.yml").write_text("{}")
    _stub_inspect(monkeypatch, name="foo", version="1.0", exports_sources=[])
    monkeypatch.setattr(get_rrev, "DenverConanFile", None)

    _, _, rrev = get_rrev.compute_rrev(recipe_dir)
    assert rrev


def test_get_rrev_tracked_source_removes_url_and_computes_md5(tmp_path, monkeypatch, capsys):
    recipe_dir = _recipe_dir(tmp_path)
    src = "data.tar"
    (recipe_dir / src).write_text("payload")
    (recipe_dir / "conandata.yml").write_text(yaml.safe_dump({"sources": {src: {"url": "http://should-be-removed"}}}))
    _stub_inspect(monkeypatch, name="foo", version="1.0", exports_sources=[src])
    _stub_git_tracked(monkeypatch, {str(recipe_dir / src)})

    get_rrev.compute_rrev(recipe_dir)

    saved = yaml.safe_load((recipe_dir / "conandata.yml").read_text())
    assert "url" not in saved["sources"][src]
    assert "md5" in saved["sources"][src]


def test_get_rrev_tracked_source_missing_file_and_no_pin_errors(tmp_path, monkeypatch):
    recipe_dir = _recipe_dir(tmp_path)
    src = "missing.tar"
    (recipe_dir / "conandata.yml").write_text("{}")
    _stub_inspect(monkeypatch, name="foo", version="1.0", exports_sources=[src])
    # git reports it as tracked, but the file isn't actually present and no
    # md5/url/custom pin exists in conandata.yml either
    _stub_git_tracked(monkeypatch, {str(recipe_dir / src)})

    with pytest.raises(get_rrev.GetRREVError, match="don't know how to get file"):
        get_rrev.compute_rrev(recipe_dir)


def test_get_rrev_untracked_without_url_or_custom_raises(tmp_path, monkeypatch):
    recipe_dir = _recipe_dir(tmp_path)
    src = "external.tar"
    (recipe_dir / "conandata.yml").write_text("{}")
    _stub_inspect(monkeypatch, name="foo", version="1.0", exports_sources=[src])
    _stub_git_tracked(monkeypatch, set())  # not tracked

    with pytest.raises(get_rrev.GetRREVError, match="'url' must be specified"):
        get_rrev.compute_rrev(recipe_dir)


def test_get_rrev_untracked_downloads_via_url(tmp_path, monkeypatch):
    recipe_dir = _recipe_dir(tmp_path)
    src = "external.tar"
    (recipe_dir / "conandata.yml").write_text(yaml.safe_dump({"sources": {src: {"url": "http://example/x"}}}))
    _stub_inspect(monkeypatch, name="foo", version="1.0", exports_sources=[src])
    _stub_git_tracked(monkeypatch, set())

    downloaded = []

    def fake_urlretrieve(url, dst):
        downloaded.append((url, dst))
        dst.write_text("downloaded-content")

    monkeypatch.setattr(get_rrev, "urlretrieve", fake_urlretrieve)

    get_rrev.compute_rrev(recipe_dir)
    assert downloaded == [("http://example/x", recipe_dir / src)]


def test_get_rrev_untracked_downloads_via_custom_command(tmp_path, monkeypatch):
    recipe_dir = _recipe_dir(tmp_path)
    src = "external.tar"
    (recipe_dir / "conandata.yml").write_text(yaml.safe_dump({"sources": {src: {"custom": "fetch-it"}}}))
    _stub_inspect(monkeypatch, name="foo", version="1.0", exports_sources=[src])
    _stub_git_tracked(monkeypatch, set())

    ran = []

    def fake_run(cmd, check, cwd):
        ran.append((cmd, cwd))
        (recipe_dir / src).write_text("fetched")

    monkeypatch.setattr(get_rrev.subprocess, "run", fake_run)

    get_rrev.compute_rrev(recipe_dir)
    assert ran == [(["fetch-it"], recipe_dir)]


def test_get_rrev_existing_valid_md5_skips_recompute(tmp_path, monkeypatch):
    # untracked (no local file at all -- so a tracked-branch recompute can't
    # kick in), pinned with a url *and* an already-correct-length md5: the
    # pin must be trusted as-is, no download needed.
    recipe_dir = _recipe_dir(tmp_path)
    src = "external.tar"
    md5 = "a" * 32
    (recipe_dir / "conandata.yml").write_text(
        yaml.safe_dump({"sources": {src: {"url": "http://example/x", "md5": md5}}})
    )
    _stub_inspect(monkeypatch, name="foo", version="1.0", exports_sources=[src])
    _stub_git_tracked(monkeypatch, set())

    monkeypatch.setattr(get_rrev, "urlretrieve", lambda *a: pytest.fail("must not download"))

    get_rrev.compute_rrev(recipe_dir)  # must not raise


def test_get_rrev_invalid_md5_length_raises(tmp_path, monkeypatch):
    recipe_dir = _recipe_dir(tmp_path)
    src = "external.tar"
    (recipe_dir / "conandata.yml").write_text(
        yaml.safe_dump({"sources": {src: {"url": "http://example/x", "md5": "tooshort"}}})
    )
    _stub_inspect(monkeypatch, name="foo", version="1.0", exports_sources=[src])
    _stub_git_tracked(monkeypatch, set())

    with pytest.raises(get_rrev.GetRREVError, match="not a valid"):
        get_rrev.compute_rrev(recipe_dir)


def test_get_rrev_keeps_sources_not_in_exports_sources(tmp_path, monkeypatch):
    """A source the recipe fetches itself at build time is left alone, not deleted."""
    recipe_dir = _recipe_dir(tmp_path)
    src = "data.tar"
    downloaded = {"url": "http://example/other.tar", "md5": "b" * 32}
    (recipe_dir / src).write_text("payload")
    (recipe_dir / "conandata.yml").write_text(yaml.safe_dump({"sources": {src: {}, "other.tar": downloaded}}))
    _stub_inspect(monkeypatch, name="foo", version="1.0", exports_sources=[src])
    _stub_git_tracked(monkeypatch, {str(recipe_dir / src)})

    get_rrev.compute_rrev(recipe_dir)

    saved = yaml.safe_load((recipe_dir / "conandata.yml").read_text())
    assert saved["sources"]["other.tar"] == downloaded


def test_get_rrev_bare_recipe_conandata_untouched(tmp_path, monkeypatch):
    """A recipe with no 'exports_sources' keeps its conandata.yml verbatim."""
    recipe_dir = _recipe_dir(tmp_path)
    conandata = recipe_dir / "conandata.yml"
    original = yaml.safe_dump({"sources": {"tool.tar.gz": {"url": "http://example/tool.tar.gz", "md5": "c" * 32}}})
    conandata.write_text(original)
    _stub_inspect(monkeypatch, name="foo", version="1.0", exports_sources=[])
    _stub_git_tracked(monkeypatch, set())

    get_rrev.compute_rrev(recipe_dir)

    assert conandata.read_text() == original


def test_get_rrev_no_rewrite_when_nothing_changed(tmp_path, monkeypatch):
    recipe_dir = _recipe_dir(tmp_path)
    src = "data.tar"
    (recipe_dir / src).write_text("payload")
    from conan.internal.util.files import md5sum

    correct_md5 = md5sum(recipe_dir / src)
    (recipe_dir / "conandata.yml").write_text(yaml.safe_dump({"sources": {src: {"md5": correct_md5}}}))
    _stub_inspect(monkeypatch, name="foo", version="1.0", exports_sources=[src])
    _stub_git_tracked(monkeypatch, {str(recipe_dir / src)})

    saved_calls = []
    monkeypatch.setattr(get_rrev, "save", lambda path, content: saved_calls.append(path))

    get_rrev.compute_rrev(recipe_dir)
    assert saved_calls == []


def test_get_rrev_missing_md5_after_all_branches_raises(tmp_path, monkeypatch):
    # md5 stays falsy (empty string) even after the "not md5" branch ran --
    # forces the final safety-net error.
    recipe_dir = _recipe_dir(tmp_path)
    src = "data.tar"
    (recipe_dir / src).write_text("payload")
    (recipe_dir / "conandata.yml").write_text(yaml.safe_dump({"sources": {src: {}}}))
    _stub_inspect(monkeypatch, name="foo", version="1.0", exports_sources=[src])
    _stub_git_tracked(monkeypatch, {str(recipe_dir / src)})

    import denver_providers.conan_scripts.get_rrev as mod

    monkeypatch.setattr(mod, "md5sum", lambda p: "")

    with pytest.raises(get_rrev.GetRREVError, match="Could not get md5"):
        get_rrev.compute_rrev(recipe_dir)
