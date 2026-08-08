"""End-to-end golden-file tests: for each real env under examples/, --show-config's
output must match a checked-in golden file.

This is the gap the rest of the suite leaves open -- everything else mocks
subprocess.run/shutil.which/os.execvpe and drives providers through small,
synthetic configs. Nothing takes a real denver.yml from examples/, runs it
through the whole resolver, and checks the result against a known-good
snapshot -- so a resolver regression in a real env's config only shows up by
manually diffing --show-config output. These tests catch that automatically.

Only shutil.which (via the shared ``which`` fixture), the zephyr provider's
workspace-root lookups (WEST_TOPDIR, and the outermost-``.git`` walk its
'west-yml:' fallback uses independently of WEST_TOPDIR) and the running
denver's own version (which the envs' 'denver-version:' pins are checked
against) are faked, for determinism across machines/CI -- everything else (recipe-dirs,
conanfiles, patches files, ...) is resolved against the real examples/ tree,
so a real "file not found" in an env's own config still fails here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import denver
import providers.zephyr as zephyr_provider

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
FAKE_WEST_TOPDIR = "/fake-west-topdir"
# newer than any 'denver-version:' an example can plausibly pin
FAKE_DENVER_VERSION = "999.0.0"


def _tracked_env_names():
    """Env dir names with a denver.yml tracked in git -- not e.g. a developer's local scratch env under examples/."""
    result = subprocess.run(
        ["git", "ls-files", "examples/*/denver.yml"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return sorted(Path(line).parent.name for line in result.stdout.splitlines() if line)


def _normalize(text):
    """Replace this checkout's own absolute path with a portable placeholder."""
    return text.replace(str(REPO_ROOT), "<REPO>")


@pytest.mark.parametrize("env_name", _tracked_env_names())
def test_show_config_matches_golden(env_name, capsys, monkeypatch, which, tmp_path):
    monkeypatch.setattr(denver, "DENVER_DIR", tmp_path / "denver")
    monkeypatch.setenv("WEST_TOPDIR", FAKE_WEST_TOPDIR)
    # zephyr's 'west-yml:' fallback walks for the outermost enclosing .git
    # independently of WEST_TOPDIR -- real on this machine (this checkout may
    # itself be nested inside another .git, e.g. via git-nested), so it's
    # faked directly rather than left to depend on the surrounding filesystem.
    monkeypatch.setattr(zephyr_provider, "find_outermost_in_parents", lambda start, name: Path(FAKE_WEST_TOPDIR))
    # the running denver's version, which the envs' 'denver-version:' pins are
    # checked against, comes from this checkout's git tags -- absent in a
    # shallow clone or a tarball (CI's default checkout has no tags at all).
    # It's also legitimately *behind* an example's pin between the pin bump
    # and the release it anticipates. Neither says anything about whether
    # --show-config still resolves the env correctly, so it's faked.
    monkeypatch.setattr(denver, "package_version", lambda: FAKE_DENVER_VERSION)

    env_dir = REPO_ROOT / "examples" / env_name
    assert denver.main([str(env_dir), "--show-config"]) == 0
    actual = _normalize(capsys.readouterr().out)

    golden_path = GOLDEN_DIR / f"{env_name}.yml"
    expected = golden_path.read_text()
    assert actual == expected, (
        f"--show-config for examples/{env_name} no longer matches {golden_path} -- if this change is "
        f"intentional, regenerate it (see this test module's docstring)."
    )
