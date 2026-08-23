"""git provider: brings a git checkout into the environment, pinned to one revision.

Configured from denver.toml -> a stage declaring ``provider: git``, with
``url:``/``path:``/``revision:`` of its own. If ``path:`` doesn't exist yet
it is cloned; otherwise the existing checkout is fetched and moved (detached,
never on a branch) onto whatever commit ``revision:`` names -- a tag, a
branch, or a raw commit sha.

This is what the ``custom`` provider's "git clone, then fetch/checkout a
pinned tag" shell script (see doc/providers/custom.md) looks like once it is
a provider: the same idempotence every such script has to reimplement by
hand -- recognising an existing checkout, moving it to a *different* pinned
revision when the config changes, never leaving a half-finished clone behind
-- done once, here, instead of in every project that needs it.

This provider has no env-prepend:/env-append: keys of its own: a checkout's
own path is already known in full at config-write time (``path:`` itself),
so the generic per-stage 'env:'/'env-prepend:'/'env-append:' keys every
stage gets (see GENERIC_STAGE_KEYS in denver.py) already cover exporting it
-- no provider-specific mechanism needed on top.

Full key reference, worked examples and design notes: ``doc/providers/git.md``.
"""

from __future__ import annotations

from pathlib import Path

from .base import Provider, fill_unset
from .context import banner, die, info, interpolate

# the remote name a fresh clone is created under, and the one 'revision:' is
# resolved against -- 'origin', same as a plain 'git clone' with no
# '--origin' of its own.
DEFAULT_REMOTE = "origin"


class GitProvider(Provider):
    """Clones (or updates) a git checkout, pinned to one revision -- see doc/providers/git.md for denver.toml keys."""

    name = "git"

    #: every key this provider's stage section understands
    KEYS = ("url", "path", "revision", "remote", "submodules")

    #: keys that must be given, and must be a non-empty string
    REQUIRED_KEYS = ("url", "path", "revision")

    # ---- config defaults --------------------------------------------------- #
    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):  # noqa: ARG003  # shared (ctx, cfg, config) signature
        """Resolve this stage's complete config: interpolated url/revision, an absolute path, every default filled.

        ``path:`` is resolved here (not in setup()) for the same reason
        download's 'unpack-dir:' is: so --show-config shows exactly which
        directory a real run would clone into or update, and setup() never
        has to work one out itself.
        """
        cls._validate(cfg)
        resolved = dict(cfg)
        resolved["url"] = interpolate(cfg["url"], ctx.variables)
        resolved["revision"] = interpolate(cfg["revision"], ctx.variables)
        resolved["path"] = str(ctx.resolve_path(cfg["path"]))
        resolved["remote"] = (cfg.get("remote") or "").strip() or DEFAULT_REMOTE
        resolved["submodules"] = bool(cfg.get("submodules", False))
        return fill_unset(resolved, cls.KEYS)

    # ---- config validation -------------------------------------------------- #
    @classmethod
    def _validate(cls, cfg):
        """Die unless this stage's own keys are well-typed.

        Unknown keys are not this method's job: denver.py itself already
        rejects any stage key that is neither one of GENERIC_STAGE_KEYS nor
        one of cls.KEYS, before resolve_defaults() ever runs (the same
        division download.py's own resolve_defaults relies on).
        """
        cls._validate_required_keys(cfg)
        cls._validate_optional_keys(cfg)

    @classmethod
    def _validate_required_keys(cls, cfg):
        """Die unless every REQUIRED_KEYS entry is a non-empty string."""
        for key in cls.REQUIRED_KEYS:
            value = cfg.get(key)
            if not isinstance(value, str) or not value.strip():
                die(f"git: '{key}:' is required and must be a non-empty string (got {value!r})")

    @staticmethod
    def _validate_optional_keys(cfg):
        """Die unless 'remote:'/'submodules:', if given, are correctly typed."""
        remote = cfg.get("remote")
        if remote is not None and not isinstance(remote, str):
            die(f"git: 'remote:' must be a string (got {remote!r})")
        submodules = cfg.get("submodules")
        if submodules is not None and not isinstance(submodules, bool):
            die(f"git: 'submodules:' must be a boolean (got {submodules!r})")

    # ---- lifecycle ----------------------------------------------------------- #
    def setup(self, ctx):
        """Make sure the checkout is there and on 'revision:' -- clone/fetch/checkout, then submodules."""
        cfg = self.config_section(ctx)
        path = Path(cfg["path"])
        if ctx.fast:
            self._check_fast(ctx, cfg, path)
            return
        self._provision(ctx, cfg, path)

    def _check_fast(self, ctx, cfg, path):
        """Under --fast: skip clone/fetch/checkout entirely, and die if there is no checkout yet."""
        banner(ctx, self.stage, "clone/checkout (skipped by --fast)")
        if not (path / ".git").exists():
            die(f"git[{self.stage}]: --fast needs '{cfg['path']}' already checked out -- run once without --fast first")

    def _provision(self, ctx, cfg, path):
        """Clone if 'path:' isn't a checkout yet, then fetch and move it (detached) onto 'revision:'."""
        self._ensure_clone(ctx, path, cfg["url"], cfg["remote"])
        self._ensure_checked_out(ctx, path, cfg["remote"], cfg["revision"])
        if cfg["submodules"]:
            self._update_submodules(ctx, path)

    # ---- clone ----------------------------------------------------------------- #
    def _ensure_clone(self, ctx, path, url, remote):
        """Clone 'url:' into 'path:' unless it is already a checkout -- never re-clones over an existing one."""
        banner(ctx, self.stage, "clone")
        if (path / ".git").exists():
            info(f"git[{self.stage}]: already cloned: {path}")
            return
        ctx.mkdir(path.parent)
        ctx.run(["git", "clone", "--origin", remote, "--", url, str(path)])

    # ---- fetch / checkout -------------------------------------------------------- #
    def _ensure_checked_out(self, ctx, path, remote, revision):
        """Fetch 'remote:', then detach-checkout 'revision:' -- skipped if already exactly there."""
        banner(ctx, self.stage, "fetch")
        ctx.run(["git", "-C", str(path), "fetch", "--tags", "--prune", remote])
        sha = self._resolve_revision(ctx, path, remote, revision)
        if not sha:
            return  # --dry-run only: _resolve_revision already reported why
        current = ctx.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture=True, check=False)
        if current.returncode == 0 and current.stdout.strip() == sha and not ctx.force:
            info(f"git[{self.stage}]: already at {revision} ({sha[:12]}): {path}")
            return
        banner(ctx, self.stage, "checkout")
        if ctx.force:
            # discard whatever local state (a hand-edited file, a leftover
            # build artifact tracked as untracked) would otherwise make the
            # checkout below anything but exactly 'revision:'
            ctx.run(["git", "-C", str(path), "reset", "--hard"])
            ctx.run(["git", "-C", str(path), "clean", "-fdx"])
        ctx.run(["git", "-C", str(path), "checkout", "--detach", sha])

    def _resolve_revision(self, ctx, path, remote, revision):
        """The commit sha 'revision:' names, fetching it explicitly first if the generic fetch above didn't have it.

        Covers a raw commit sha pinned to something that isn't the tip of
        any branch or tag -- 'fetch --tags' alone never sees those, so a
        second, targeted fetch is tried before giving up. Still fails
        loudly (rather than falling back to something else) if the remote
        genuinely doesn't have it, or refuses to serve an unadvertised
        commit -- see 'unreachable commits' in doc/providers/git.md.
        """
        sha = self._rev_parse(ctx, path, revision)
        if sha:
            return sha
        ctx.run(["git", "-C", str(path), "fetch", remote, revision], check=False)
        sha = self._rev_parse(ctx, path, revision)
        if sha:
            return sha
        if ctx.dry_run:
            # a genuinely missing revision and a --dry-run preview that
            # skipped the clone/fetch above (nothing real to resolve
            # against yet) look identical from here -- reported, not fatal,
            # so the rest of the preview still renders (see download's own
            # 'nothing was fetched, so there is nothing to verify').
            ctx.dry_note("!", f"{self.stage}: cannot resolve '{revision}' in this preview -- {path} isn't fetched yet")
            return ""
        die(f"git[{self.stage}]: revision '{revision}' not found in {path} (fetched from '{remote}')")
        return ""  # pragma: no cover -- unreachable, die() above never returns; satisfies ruff's RET503

    @staticmethod
    def _rev_parse(ctx, path, revision):
        """The commit sha ``revision`` resolves to in ``path`` right now, or "" if it doesn't resolve at all."""
        result = ctx.run(
            ["git", "-C", str(path), "rev-parse", "--verify", "-q", f"{revision}^{{commit}}"],
            capture=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    # ---- submodules -------------------------------------------------------------- #
    def _update_submodules(self, ctx, path):
        """Init/update this checkout's own submodules (not nested ones -- see doc/providers/git.md)."""
        banner(ctx, self.stage, "submodules")
        ctx.run(["git", "-C", str(path), "submodule", "sync"])
        ctx.run(["git", "-C", str(path), "submodule", "update", "--init"])
