"""zephyr provider: manages a West workspace (Zephyr RTOS).

Configured from denver.yml -> ``zephyr:``:

    zephyr:
      west-yml: ${WEST_TOPDIR}/west.yml         # manifest (default: super-repo)
      base:     ${WEST_TOPDIR}/zephyr-rtos       # ZEPHYR_BASE
      west-config:               # extra/overriding `west config` key=value pairs
        zephyr.base-prefer: env
      blobs-cache: conan/recipes/west-blobs-cache/denver/blobs.txt
      blobs-fetch-args: ["--auto-accept"]
      patch-committer:           # identity used when applying project patches
        GIT_COMMITTER_NAME: denver
      update-args: []            # extra `west update` args

The west executable is never configured here -- always the first `west` on
PATH (installed by an earlier uv stage). Installing `west packages pip`'s
own requirements is likewise not this provider's job: give a *separate* uv
stage a ``requirements: [$(west packages pip)]`` entry instead (see
providers/uv.py's own docstring), with its own ``overrides:``/
``freeze-to:`` for pinning them.

``WEST_CONFIG_SYSTEM`` (west's own base-config env var, e.g. the remotes/
defaults denver ships) is not a denver.yml key -- set it directly via
``env:``/``hooks.env`` like any other real environment variable; west reads
it itself, so no provider-specific handling is needed here.

In CI (``ctx.ci``), `west update` always adds a fixed ``--narrow
-o=--depth=1`` (shallow clone) on top of any configured ``update-args:`` --
not itself a denver.yml key, since there's never a reason to want a
*different* CI shallow-clone strategy per env. To skip this stage
entirely for one invocation, use denver's own ``--skip <stage>`` (see
denver.py's own docstring), not an env var.

Every default above (west-yml/base paths, blobs-fetch-args,
patch-committer) is computed once, centrally, by
``ZephyrProvider.resolve_defaults`` -- not in setup(). By the time this
provider's setup() runs, its config section already has every default
filled in (see ``denver.resolve_provider_defaults``), so nothing here ever
falls back to a conventional path or a PATH lookup itself.

``west`` must already be installed wherever this stage runs (in practice by
an earlier uv stage listing it in ``requirements:``) -- denver never
installs it, and always uses the first one on PATH (see above).

Full key reference, worked examples and design notes: ``doc/providers/zephyr.md``.
"""

import os
from pathlib import Path

from .base import Provider, fill_unset
from .context import banner, die, find_in_parents, find_outermost_in_parents, info

# extra `west update` args added on top of 'update-args:' whenever ctx.ci --
# a fixed shallow-clone strategy, not a denver.yml key (see module docstring).
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
    """Manages a West workspace (Zephyr RTOS) -- see module docstring for denver.yml keys."""

    name = "zephyr"
    KEYS = (
        "west-yml",
        "base",
        "west-config",
        "blobs-cache",
        "blobs-fetch-args",
        "patch-committer",
        "update-args",
    )

    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):  # noqa: ARG003  # shared (ctx, cfg, config) signature
        """Resolve west-yml/base/blobs-fetch-args/patch-committer -- see module docstring."""
        # WEST_TOPDIR is a zephyr concept, not a denver built-in -- computed
        # and exported here (not in Context) so non-zephyr envs never pay for
        # the parent-directory walk or carry an irrelevant env var. setdefault
        # so an already-exported WEST_TOPDIR (e.g. set by the user, or by an
        # outer denver run before re-invoking inside docker) wins.
        top = west_topdir(ctx.env_dir)
        ctx.env.setdefault("WEST_TOPDIR", str(top) if top else "")

        resolved = dict(cfg)

        west_yml = cfg.get("west-yml")
        if west_yml:
            resolved["west-yml"] = str(ctx.resolve_path(west_yml))
        else:
            # anchored on the env dir (where the env being launched lives),
            # not ctx.denver_dir (where denver itself is installed/keeps
            # state) -- those have no relationship when denver is installed.
            super_root = find_outermost_in_parents(ctx.env_dir, ".git")
            if not super_root:
                die("zephyr: no west-yml configured and no enclosing git repo found")
            resolved["west-yml"] = str(Path(super_root) / "west.yml")

        resolved["base"] = str(ctx.resolve_path(cfg.get("base") or "${WEST_TOPDIR}/zephyr-rtos"))
        resolved["blobs-fetch-args"] = cfg.get("blobs-fetch-args") or ["--auto-accept"]

        committer = {
            "GIT_COMMITTER_NAME": "denver",
            "GIT_COMMITTER_EMAIL": "denver@denver",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00",
        }
        committer.update(cfg.get("patch-committer") or {})
        resolved["patch-committer"] = committer

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

        west = ctx.which("west")
        if not west:
            die("zephyr provider needs 'west' on PATH (installed by the uv provider)")

        west_yml = Path(cfg["west-yml"])
        zephyr_base = Path(cfg["base"])

        self._ensure_workspace(ctx, top)
        self._configure(ctx, cfg, west, top, west_yml, zephyr_base)
        self._update(ctx, cfg, west, top, west_yml, zephyr_base)

    # ------------------------------------------------------------------ #
    def _ensure_workspace(self, ctx, top):
        """Create an empty .west/config at ``top`` if missing (or --force), so `west` recognizes the workspace."""
        # bannered first (even though there's nothing to echo/run here today)
        # so a future addition to this step can't print ahead of any banner,
        # the way _configure/_update's own info() lines used to.
        banner(ctx, self.stage, "prepare")
        west_config = top / ".west" / "config"
        if ctx.force and west_config.exists():
            west_config.unlink()
        if not west_config.exists():
            west_config.parent.mkdir(parents=True, exist_ok=True)
            west_config.touch()
        info(f"zephyr: workspace at {west_config}")

    def _configure(self, ctx, cfg, west, top, west_yml, zephyr_base):
        """Set every `west config` key that differs from its current value (manifest.path/file, zephyr.base, ...)."""
        banner(ctx, self.stage, "west config")
        current = ctx.run([west, "config", "-l"], cwd=top, capture=True, echo=False, check=False).stdout

        def ensure(key, value):
            # only actually run `west config` when the value differs --
            # avoids an unnecessary write (and its log line) on every run.
            existing = None
            for line in current.splitlines():
                if line.startswith(f"{key}="):
                    existing = line.split("=", 1)[1]
                    break
            if existing != value:
                ctx.run([west, "config", key, value], cwd=top)
            info(f"zephyr: west config {key}={value}")

        # computed from the (already-resolved) west-yml/base, then any
        # extra/overriding entries from denver.yml -- these three are workspace
        # topology, not denver.yml defaults, so they're computed right here.
        west_config = {
            "manifest.path": os.path.relpath(west_yml.parent, top),
            "manifest.file": west_yml.name,
            "zephyr.base": os.path.relpath(zephyr_base, top),
        }
        west_config.update(cfg.get("west-config") or {})
        for key, value in west_config.items():
            ensure(key, str(value))

    def _west_info(self, ctx, west, top, west_yml, zephyr_base):
        """Build a fingerprint string (west-yml path, zephyr commit, resolved manifest) to detect workspace drift."""
        lines = [f"west-yml: {west_yml}"]
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
        info_file.parent.mkdir(parents=True, exist_ok=True)
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

        info_file.write_text(self._west_info(ctx, west, top, west_yml, zephyr_base))

    def _apply_project_patches(self, ctx, cfg, west, top):
        """Apply each west project's own zephyr/patches.yml (if any), reversed so dependents patch before their deps."""
        committer = cfg["patch-committer"]
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
                    check=False,
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
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# This file is auto-generated by the zephyr provider!\n" + listing)
