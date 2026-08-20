"""zephyr provider: manages a West workspace (Zephyr RTOS).

Configured from denver.toml -> ``zephyr:``. The west executable defaults to
the first ``west`` on PATH (installed by an earlier uv stage), overridable
via ``exe:``.

Full key reference, worked examples and design notes: ``doc/providers/zephyr.md``.
"""

import os
from pathlib import Path

from .base import Provider, fill_unset
from .context import banner, die, find_in_parents, find_outermost_in_parents, fingerprint_label, info

# extra `west update` args added on top of 'update-args:' whenever ctx.ci --
# a fixed shallow-clone strategy, not a denver.toml key (see doc/providers/zephyr.md).
CI_UPDATE_ARGS = ("--narrow", "-o=--depth=1")

# west's own per-workspace marker dir, holding its config file.
WEST_DIRNAME = ".west"


def west_topdir(start):
    """Locate the workspace top: nearest ``.west``, else the outermost ``.git``."""
    for parent in find_in_parents(start, WEST_DIRNAME):
        return parent
    outermost_git = find_outermost_in_parents(start, ".git")
    return outermost_git if outermost_git else None


class ZephyrProvider(Provider):
    """Manages a West workspace (Zephyr RTOS) -- see doc/providers/zephyr.md for denver.toml keys."""

    name = "zephyr"
    KEYS = (
        "exe",
        "west-yml",
        "base",
        "west-config",
        "blobs-cache",
        "blobs-fetch-args",
        "patch-committer-name",
        "patch-committer-email",
        "patch-committer-date",
        "update-args",
    )

    @staticmethod
    def _resolved_west_yml(ctx, west_yml):
        """The manifest path: the configured 'west-yml:' if there is one, else the super-repo's own west.yml."""
        if west_yml:
            return str(ctx.resolve_path(west_yml))
        # anchored on the env dir (where the env being launched lives),
        # not ctx.denver_dir (where denver itself is installed/keeps
        # state) -- those have no relationship when denver is installed.
        super_root = find_outermost_in_parents(ctx.env_dir, ".git")
        if not super_root:
            die("zephyr: no west-yml configured and no enclosing git repo found")
        return str(Path(super_root) / "west.yml")

    @staticmethod
    def _resolved_patch_committer(cfg):
        """'patch-committer-name:'/'-email:'/'-date:', each defaulting to denver's own fixed identity.

        Fixed, so applying the same patches twice never produces a
        different commit -- overridden per field, independently.
        """
        return {
            "patch-committer-name": cfg.get("patch-committer-name") or "denver",
            "patch-committer-email": cfg.get("patch-committer-email") or "denver@denver",
            "patch-committer-date": cfg.get("patch-committer-date") or "2000-01-01T00:00:00",
        }

    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):  # noqa: ARG003  # shared (ctx, cfg, config) signature
        """Resolve west-yml/base/blobs-fetch-args/patch-committer-* -- see doc/providers/zephyr.md."""
        # WEST_TOPDIR is a zephyr concept, not a denver built-in -- computed
        # and exported here (not in Context) so non-zephyr envs never pay for
        # the parent-directory walk or carry an irrelevant env var. setdefault
        # so an already-exported WEST_TOPDIR (e.g. set by the user, or by an
        # outer denver run before re-invoking inside docker) wins.
        top = west_topdir(ctx.env_dir)
        ctx.env.setdefault("WEST_TOPDIR", str(top) if top else "")

        resolved = dict(cfg)
        resolved["exe"] = cfg.get("exe") or "west"
        resolved["west-yml"] = cls._resolved_west_yml(ctx, cfg.get("west-yml"))
        resolved["base"] = str(ctx.resolve_path(cfg.get("base") or "${WEST_TOPDIR}/zephyr-rtos"))
        resolved["blobs-fetch-args"] = cfg.get("blobs-fetch-args") or ["--auto-accept"]
        resolved.update(cls._resolved_patch_committer(cfg))

        return fill_unset(resolved, cls.KEYS)

    def setup(self, ctx):
        """Configure and update the West workspace."""
        cfg = self.config_section(ctx)

        top = west_topdir(ctx.env_dir)
        if not top:
            die("zephyr: could not determine WEST_TOPDIR (no .west or .git found)")
        top = Path(top)

        if ctx.fast:
            # same banner sequence as a full run (_ensure_workspace/_configure/
            # _update below), so --fast's progress trail looks identical --
            # every substep says it was skipped instead of silently
            # vanishing, plus one extra 'activate' substep for the real work
            # --fast actually does.
            banner(ctx, self.stage, "prepare (skipped by --fast)")
            banner(ctx, self.stage, "west config (skipped by --fast)")
            banner(ctx, self.stage, "west update (skipped by --fast)")
            west_config = top / WEST_DIRNAME / "config"
            if not west_config.exists():
                die(
                    f"zephyr[{self.stage}]: --fast needs an existing workspace at "
                    f"{west_config} -- run once without --fast first"
                )
            banner(ctx, self.stage, "activate")
            return

        # dry_fallback: under --dry-run the uv stage that installs west only
        # printed its commands, so west legitimately isn't on PATH yet and
        # the bare name still renders this stage (see Context.which).
        west = ctx.which(cfg["exe"], dry_fallback=True)
        if not west:
            die(f"zephyr[{self.stage}]: needs '{cfg['exe']}' on PATH -- normally installed by an earlier uv stage")

        west_yml = Path(cfg["west-yml"])
        zephyr_base = Path(cfg["base"])

        self._ensure_workspace(ctx, top)
        self._configure(ctx, cfg, west, top, west_yml, zephyr_base)
        self._update(ctx, cfg, west, top, west_yml, zephyr_base)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _needs_west_config(ctx, west_config, removed):
        """Whether the empty .west/config still has to be (re)created.

        Under --dry-run the removal only *reported* itself, so ask whether
        the file would have survived, not whether it is still on disk --
        otherwise its recreation goes missing from the preview.
        """
        if removed and ctx.dry_run:
            return True
        return not west_config.exists()

    def _ensure_workspace(self, ctx, top):
        """Create an empty .west/config at ``top`` if missing (or --force), so `west` recognizes the workspace."""
        # bannered first (even though there's nothing to echo/run here today)
        # so a future addition to this step can't print ahead of any banner,
        # the way _configure/_update's own info() lines used to.
        banner(ctx, self.stage, "prepare")
        west_config = top / ".west" / "config"
        removed = ctx.force and west_config.exists()
        if removed:
            ctx.unlink(west_config)
        if self._needs_west_config(ctx, west_config, removed):
            ctx.mkdir(west_config.parent)
            ctx.touch(west_config)
        info(f"zephyr: workspace at {west_config}")

    @staticmethod
    def _current_config_value(listing, key):
        """The value ``west config -l`` currently reports for ``key``, or None if it has none."""
        for line in listing.splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1]
        return None

    def _ensure_config(self, ctx, west, top, listing, key, value):
        """Run `west config <key> <value>` only when the value differs -- no write (or log line) on every run."""
        if self._current_config_value(listing, key) != value:
            ctx.run([west, "config", key, value], cwd=top)
        info(f"zephyr: west config {key}={value}")

    def _configure(self, ctx, cfg, west, top, west_yml, zephyr_base):
        """Set every `west config` key that differs from its current value (manifest.path/file, zephyr.base, ...)."""
        banner(ctx, self.stage, "west config")
        current = ctx.run([west, "config", "-l"], cwd=top, capture=True, echo=False, check=False).stdout

        # computed from the (already-resolved) west-yml/base, then any
        # extra/overriding entries from denver.toml -- these three are workspace
        # topology, not denver.toml defaults, so they're computed right here.
        west_config = {
            "manifest.path": os.path.relpath(west_yml.parent, top),
            "manifest.file": west_yml.name,
            "zephyr.base": os.path.relpath(zephyr_base, top),
        }
        west_config.update(cfg.get("west-config") or {})
        for key, value in west_config.items():
            self._ensure_config(ctx, west, top, current, key, str(value))

    def _west_info(self, ctx, west, top, west_yml, zephyr_base):
        """Build a fingerprint string (west-yml path, zephyr commit, resolved manifest) to detect workspace drift.

        The manifest is named relative to the env dir: a fingerprint must
        answer "did the workspace change", not "did this tree move", and an
        absolute path makes a second checkout of the same project look like
        drift and re-run `west update` in full (see context.fingerprint_label).
        """
        lines = [f"west-yml: {fingerprint_label(west_yml, ctx.env_dir)}"]
        commit = ctx.run(
            ["git", "-C", str(zephyr_base), "rev-parse", "HEAD"],
            capture=True,
            echo=False,
            check=False,
        ).stdout.strip()
        lines.append(f"zephyr-commit: {commit}")
        resolved = ctx.run(
            [west, "manifest", "--resolve"],
            cwd=top,
            capture=True,
            echo=False,
            check=False,
        ).stdout
        lines.append(resolved)
        return "\n".join(lines)

    def _update(self, ctx, cfg, west, top, west_yml, zephyr_base):
        """`west update` (skipped if nothing changed since last run), apply patches, then fetch/cache blobs."""
        banner(ctx, self.stage, "west update")
        info_file = ctx.logs_dir / "west-update.info"
        ctx.mkdir(info_file.parent)
        previous = info_file.read_text() if info_file.is_file() else ""
        current = self._west_info(ctx, west, top, west_yml, zephyr_base)

        if not ctx.force and previous and previous == current:
            info("zephyr: no need to rerun west update (enforce with --force)")
            return

        update_args = list(cfg.get("update-args") or [])
        if ctx.ci:
            update_args += CI_UPDATE_ARGS
        ctx.run([west, "update", *update_args], cwd=top)

        self._apply_project_patches(ctx, cfg, west, top)

        ctx.run([west, "-v", "blobs", "fetch", *cfg["blobs-fetch-args"]], cwd=top)
        self._update_blobs_cache(ctx, cfg, west, top)

        ctx.write_text(info_file, self._west_info(ctx, west, top, west_yml, zephyr_base))

    def _apply_project_patches(self, ctx, cfg, west, top):
        """Apply each west project's own zephyr/patches.yml (if any), reversed so dependents patch before their deps."""
        committer = {
            "GIT_COMMITTER_NAME": cfg["patch-committer-name"],
            "GIT_COMMITTER_EMAIL": cfg["patch-committer-email"],
            "GIT_COMMITTER_DATE": cfg["patch-committer-date"],
        }
        listing = ctx.run(
            [west, "list", "-f", "{abspath}"],
            cwd=top,
            capture=True,
            echo=False,
            check=False,
        ).stdout.split()
        for project in reversed(listing):
            if (Path(project) / "zephyr" / "patches.yml").is_file():
                ctx.run(
                    [west, "-v", "patch", "--src-module", project, "apply"],
                    cwd=top,
                    extra_env=committer,
                )

    def _update_blobs_cache(self, ctx, cfg, west, top):
        """Regenerate 'blobs-cache:' (a path:url listing of every west blob), if configured."""
        blobs_cache = cfg.get("blobs-cache")
        if not blobs_cache:
            return
        target = ctx.resolve_path(blobs_cache)
        listing = ctx.run(
            [west, "blobs", "list", "-f", "{path}:{url}"],
            cwd=top,
            capture=True,
            echo=False,
            check=False,
        ).stdout
        ctx.mkdir(target.parent)
        ctx.write_text(target, "# This file is auto-generated by the zephyr provider!\n" + listing)
