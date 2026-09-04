"""Repo-level invariants for ``denver.DEV_VERSION``.

DEV_VERSION (see its comment in ``src/denver.py``) is what an untagged
checkout reports instead of its stale ``git describe`` output, so that
running denver from source works at every commit rather than only after a
release. It is hand-maintained, so it is exactly the kind of thing that gets
forgotten; these two tests are what notice.

* ``test_examples_run_from_a_checkout`` is the requirement itself, stated
  directly: every example must be runnable from this working tree, right
  now. It resolves the version this checkout really reports (DEV_VERSION
  applied or not) and checks every example's pin against it -- so it fails
  whether the cause was a bumped pin, a forgotten DEV_VERSION bump, or
  DEV_VERSION being switched off while something still needed it.
* ``test_dev_version_keeps_up_with_the_release_tags`` is the earlier warning
  for the same mistake: once there are commits past the newest tag, this
  tree is developing *something*, and DEV_VERSION has to name the release it
  is heading for. That catches a forgotten bump at the first commit of a new
  cycle, before any pin has moved to expose it.

Unlike the rest of the suite these read the real repository (its tags, its
checked-in golden files) rather than synthetic fixtures -- that's the point:
the constant is only ever wrong *relative to this repo*.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

import denver

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def _tracked_env_names():
    """Env dir names with a denver.yml tracked in git -- mirrors test_golden_show_config."""
    result = subprocess.run(
        ["git", "ls-files", "examples/*/denver.yml"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return sorted(Path(line).parent.name for line in result.stdout.splitlines() if line)


def _git_describe():
    """This checkout's raw `git describe` output (``1.0.4`` or ``1.0.4-17-gabc1234``), or None.

    None whenever git can't answer -- a shallow/tagless clone, a source
    tarball, no git binary -- in which case there is no release history to
    judge DEV_VERSION against and the tests below skip rather than invent one.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "describe", "--tags", "--match", "*.*.*"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover -- environment-dependent
        return None
    return (completed.stdout.strip() or None) if completed.returncode == 0 else None


@pytest.mark.parametrize("env_name", _tracked_env_names())
def test_examples_run_from_a_checkout(env_name, monkeypatch):
    """Every example must run straight out of this working tree, tagged or not.

    An example's 'denver-version:' pin routinely names a release that is not
    tagged yet (see doc/development.md, "Releasing"), and DEV_VERSION is what
    honours it from source in the meantime. This asserts the outcome rather
    than the mechanism: whatever ``scm_version()`` reports for this checkout
    -- re-based onto DEV_VERSION or not -- has to satisfy every pin, or
    `python src/denver.py examples/<env>` is broken for everyone right now.

    The pin is read from the golden file, i.e. the *merged* config, so a pin
    an env only inherits through 'import:' is covered too.
    """
    config = yaml.safe_load((GOLDEN_DIR / f"{env_name}.yml").read_text())
    if config.get("denver-version") is None:
        pytest.skip(f"examples/{env_name} pins no 'denver-version:'")

    running = denver.scm_version()
    if running is None:  # pragma: no cover -- only in a tagless clone/tarball
        pytest.skip("this checkout has no reachable tags (shallow clone or tarball)")

    monkeypatch.setattr(denver, "package_version", lambda: running)
    denver.validate_denver_version(config)  # dies if the pin is unmet


def test_dev_version_keeps_up_with_the_release_tags():
    """DEV_VERSION must not lag the tags: at least the newest, and past it once work starts.

    Two ways to get it wrong, both caught here:

    * **behind the newest tag** — a release has overtaken it, so it re-bases
      nothing and only misstates what the tree is heading for;
    * **equal to the newest tag, with commits past that tag** — a new cycle
      has started and DEV_VERSION was never advanced, so this tree claims to
      be the release it is already building on top of. This is the one that
      would otherwise stay invisible until some example's pin moved.

    ``DEV_VERSION = None`` deliberately switches the whole mechanism off, so
    there is nothing to keep in sync and this skips -- whether that is safe
    is not a matter of opinion, and ``test_examples_run_from_a_checkout``
    settles it by checking what the checkout actually reports.
    """
    if denver.DEV_VERSION is None:
        pytest.skip("DEV_VERSION is None -- the mechanism is off, nothing to keep in sync")
    described = _git_describe()
    if described is None:  # pragma: no cover -- only in a tagless clone/tarball
        pytest.skip("this checkout has no reachable tags (shallow clone or tarball)")

    latest_tag = described.partition("-")[0]
    dev, released = denver.parse_version(denver.DEV_VERSION), denver.parse_version(latest_tag)
    assert dev is not None, f"DEV_VERSION = {denver.DEV_VERSION!r} is not a version"

    order = denver.compare_versions(dev, released)
    assert order >= 0, (
        f"DEV_VERSION = {denver.DEV_VERSION!r} is behind the latest release tag {latest_tag!r} -- "
        f"bump it to the release now being developed, or set it to None."
    )
    if described != latest_tag:  # commits exist past that tag
        assert order > 0, (
            f"DEV_VERSION = {denver.DEV_VERSION!r} is still the latest release tag {latest_tag!r}, but this "
            f"tree has commits past it ({described}) -- bump DEV_VERSION to the release those commits are "
            f"heading for, so a checkout reports what it actually contains."
        )
