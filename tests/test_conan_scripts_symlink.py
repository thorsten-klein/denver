"""Tests for providers.conan_scripts.extensions.symlink -- the deployer
conan.py passes as --deployer to every `conan install` (see conan.py's
_install, which shares one --deployer-folder across every conanfile in an
env, and reruns on every build)."""

from __future__ import annotations

import types

from providers.conan_scripts.extensions import symlink


class _Req:
    """Identity-hashable stand-in for a conan Requirement (SimpleNamespace
    defines __eq__ by value, which makes it unusable as a dict key)."""

    def __init__(self, name):
        self.ref = types.SimpleNamespace(name=name)


def _graph(deps):
    """A minimal stand-in for conan's install graph, shaped exactly as
    ``deploy()`` reads it: graph.root.conanfile.dependencies is a mapping
    of {req: dep}, req.ref.name is the package name, dep.package_folder is
    its cache path."""
    return types.SimpleNamespace(
        root=types.SimpleNamespace(
            conanfile=types.SimpleNamespace(
                dependencies={
                    _Req(name): types.SimpleNamespace(package_folder=str(folder)) for name, folder in deps.items()
                }
            )
        )
    )


def test_deploy_creates_symlinks(tmp_path, capsys):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    output_folder = tmp_path / "out"

    symlink.deploy(_graph({"foo": pkg}), str(output_folder))

    link = output_folder / "foo"
    assert link.is_symlink()
    assert link.resolve() == pkg.resolve()
    assert "Creating symlink" in capsys.readouterr().out


def test_deploy_is_idempotent_for_unchanged_symlink(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    output_folder = tmp_path / "out"
    output_folder.mkdir()
    link = output_folder / "foo"
    link.symlink_to(pkg)
    original_target = link.readlink()

    symlink.deploy(_graph({"foo": pkg}), str(output_folder))

    assert link.readlink() == original_target


def test_deploy_replaces_stale_symlink(tmp_path):
    old_pkg = tmp_path / "old"
    old_pkg.mkdir()
    new_pkg = tmp_path / "new"
    new_pkg.mkdir()
    output_folder = tmp_path / "out"
    output_folder.mkdir()
    link = output_folder / "foo"
    link.symlink_to(old_pkg)

    symlink.deploy(_graph({"foo": new_pkg}), str(output_folder))

    assert link.resolve() == new_pkg.resolve()


def test_deploy_replaces_non_symlink_in_the_way(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    output_folder = tmp_path / "out"
    output_folder.mkdir()
    (output_folder / "foo").write_text("not a symlink")

    symlink.deploy(_graph({"foo": pkg}), str(output_folder))

    link = output_folder / "foo"
    assert link.is_symlink()
    assert link.resolve() == pkg.resolve()


def test_deploy_multiple_conanfiles_shared_output_folder(tmp_path):
    # two conanfiles' deploy() calls sharing one --deployer-folder (conan.py's
    # real usage) must not collide on an overlapping dependency.
    pkg = tmp_path / "shared-dep"
    pkg.mkdir()
    output_folder = tmp_path / "out"

    symlink.deploy(_graph({"shared": pkg}), str(output_folder))
    symlink.deploy(_graph({"shared": pkg}), str(output_folder))  # second conanfile, same dep

    link = output_folder / "shared"
    assert link.is_symlink()
    assert link.resolve() == pkg.resolve()
