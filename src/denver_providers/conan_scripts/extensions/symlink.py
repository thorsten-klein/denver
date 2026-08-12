"""Conan deployer: symlinks every installed dependency's package folder into one flat directory.

Passed to `conan install --deployer=` by providers.conan.ConanProvider (see
its module docstring's `deployer:` key) so denver's PATH/env can point at a
stable location instead of conan's own cache paths, which embed a hash.
"""

from pathlib import Path


def _already_linked(symlink_src: Path, symlink_dst: Path) -> bool:
    """Whether ``symlink_src`` already points at ``symlink_dst``.

    Anything else occupying that path -- a symlink to somewhere else, or a
    real file/directory -- is removed, so the caller can link unconditionally.
    """
    if symlink_src.is_symlink():
        if symlink_src.readlink() == symlink_dst:
            return True
        symlink_src.unlink()
    elif symlink_src.exists():
        symlink_src.unlink()
    return False


def deploy(graph, output_folder: str, **_):
    """Symlink every dependency's package folder into ``output_folder``.

    Idempotent: several conanfiles may deploy into the same output_folder
    (conan.py's --deployer-folder is shared across all of an env's
    conanfiles), and a package's own deploy may run again on a later
    invocation -- an existing symlink that already points at the right
    place is left alone; anything else in the way is replaced.
    """
    print(f"Creating symlink to conan packages in: {output_folder}")
    for req, dep in graph.root.conanfile.dependencies.items():
        symlink_src = Path(output_folder) / req.ref.name
        symlink_dst = Path(dep.package_folder)

        symlink_src.parent.mkdir(parents=True, exist_ok=True)
        if _already_linked(symlink_src, symlink_dst):
            continue

        print(f">> {symlink_src} -> {symlink_dst}")
        symlink_src.symlink_to(symlink_dst)
