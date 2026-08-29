"""conan provider: provisions native tools via Conan and exposes them.

Detects a conan profile, (re)generates + exports every ``recipes:`` entry,
installs ``conanfile:`` (at most one -- a project only ever has a single
dependency graph) via each ``deployers:`` script (the symlink deployer by
default), then sources the resulting conanbuildenv.sh into ctx.env.
Configured from denver.toml -> ``conan:``. ``conanfile:`` (what to install)
and ``recipes:`` (what to export into the local cache first) are
independent: which recipes get exported has nothing to do with what the
conanfile itself requires.

Full key reference, worked examples and design notes: ``doc/providers/conan.md``.
"""

import hashlib
import json
from pathlib import Path

from .base import Provider, fill_unset
from .context import banner, die, info, warn

# ships alongside this module, so it's found regardless of whether denver
# runs from a checkout or an installed package (see providers/conan_scripts).
CONAN_SCRIPTS_DIR = Path(__file__).resolve().parent / "conan_scripts"

# the env-wide recipes exporter: not user-configurable (only a 'recipes:'
# entry's own 'export-tool:' is, see RECIPE_KEYS/_resolve_export_tool).
DEFAULT_RECIPES_EXPORTER = CONAN_SCRIPTS_DIR / "recipes.py"

# the default symlink deployer, used when 'deployers:' is left unset.
DEFAULT_DEPLOYER = CONAN_SCRIPTS_DIR / "extensions" / "symlink.py"

# name of conan's own install tree (under ctx.env_workdir) and the buildenv
# script `conan install` writes into it -- referenced from several stages below.
CONAN_INSTALL_DIRNAME = ".conan"
CONANBUILDENV_NAME = "conanbuildenv.sh"


class ConanProvider(Provider):
    """Provisions native tools via Conan and exposes them on PATH -- see doc/providers/conan.md for denver.toml keys."""

    name = "conan"
    KEYS = (
        "exe",
        "deployers",
        "base-classes",
        "conanfile",
        "recipes",
        "build",
        "install-args",
        "authentication",
        "profiles",
        "config",
        "remotes",
        "keep-remotes",
        "user",
        "channel",
    )

    RECIPE_KEYS = ("dirs", "catalog", "export-tool")

    @classmethod
    def _resolve_recipe(cls, ctx, entry, *, index, default_exporter):
        """Resolve one 'recipes:' entry -- its dirs, catalog and export tool.

        Every path is resolved and existence-checked here (except ``catalog``,
        which is an *output*), so setup() only ever handles absolute paths
        that are known to be there. See doc/providers/conan.md for the shape.
        """
        cls._validate_recipe_entry(entry)

        dirs = cls._resolve_recipe_dirs(ctx, entry)
        catalog = cls._resolve_catalog(entry, dirs, index)

        return {
            "dirs": dirs,
            "catalog": str(ctx.resolve_path(catalog)) if catalog else "",
            "export-tool": cls._resolve_export_tool(ctx, entry, default_exporter),
        }

    @classmethod
    def _validate_recipe_entry(cls, entry):
        """Die unless a 'recipes:' entry is a mapping with no keys this provider doesn't know."""
        if not isinstance(entry, dict):
            die(f"conan: each 'recipes:' entry must be a mapping (got {entry!r})")
        unknown = sorted(set(entry) - set(cls.RECIPE_KEYS))
        if unknown:
            die(f"conan: unknown key(s) in a 'recipes:' entry: {', '.join(unknown)}")

    @classmethod
    def _resolve_export_tool(cls, ctx, entry, default_exporter):
        """One recipe's own 'export-tool:', resolved and existence-checked -- the env-wide one if it has none."""
        if not entry.get("export-tool"):
            return default_exporter
        exporter = ctx.resolve_path(entry["export-tool"])
        if not exporter.is_file():
            die(f"conan: export-tool not found: {exporter}")
        return str(exporter)

    @classmethod
    def _resolve_recipe_dirs(cls, ctx, entry):
        """Validate and resolve one recipe's 'dirs:' entries."""
        dirs = []
        for name in entry.get("dirs") or []:
            d = ctx.resolve_path(name)
            if not d.is_dir():
                die(f"conan: recipe dir not found: {d}")
            dirs.append(str(d))
        return dirs

    @classmethod
    def _resolve_catalog(cls, entry, dirs, index):
        """Validate one recipe's 'catalog:' entry (never resolved here -- it's an output, not an input)."""
        catalog = entry.get("catalog") or ""
        if not isinstance(catalog, str):
            die(f"conan: 'catalog:' must be a single path, not {catalog!r}")
        if catalog and not dirs:
            die(f"conan: 'catalog: {catalog}' has no 'dirs:' to build a catalog from (recipes[{index}])")
        return catalog

    @classmethod
    def _resolve_conanfile(cls, ctx, cfg):
        """Validate and resolve 'conanfile:' -- at most one conanfile.py path, installed via `conan install`."""
        configured = cfg.get("conanfile")
        if not configured:
            return ""
        if not isinstance(configured, str):
            die(f"conan: 'conanfile:' must be a single path, not {configured!r}")
        p = ctx.resolve_path(configured)
        if not p.is_file():
            die(f"conan: conanfile not found: {p}")
        return str(p)

    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):  # noqa: ARG003  # shared (ctx, cfg, config) signature
        """Resolve exe/deployers/base-classes paths, conanfile, recipes, build, etc.

        See doc/providers/conan.md.
        """
        resolved = dict(cfg)
        cls._resolve_script_paths(ctx, cfg, resolved)

        resolved["conanfile"] = cls._resolve_conanfile(ctx, cfg)
        resolved["recipes"] = [
            cls._resolve_recipe(ctx, entry, index=i, default_exporter=str(DEFAULT_RECIPES_EXPORTER))
            for i, entry in enumerate(cfg.get("recipes") or [])
        ]

        resolved["build"] = cfg.get("build", "missing")
        resolved["authentication"] = bool(cfg.get("authentication", True))
        resolved["profiles"] = cls._resolve_profiles(cfg)

        config_dirs = cls._resolve_config_dirs(ctx, cfg)
        if config_dirs is not None:
            resolved["config"] = config_dirs

        cls._resolve_remote_settings(cfg, resolved)

        return fill_unset(resolved, cls.KEYS)

    @classmethod
    def _resolve_script_paths(cls, ctx, cfg, resolved):
        """Fill in the conan executable and the script paths this provider shells out to."""
        # bare name, not a resolved path -- existence is checked in
        # _require_conan (see there), same as the docker provider's 'exe:'
        # default.
        resolved["exe"] = cfg.get("exe") or "conan"
        resolved["deployers"] = cls._resolve_deployers(ctx, cfg)
        resolved["base-classes"] = cls._resolve_base_classes(ctx, cfg)

    @classmethod
    def _resolve_deployers(cls, ctx, cfg):
        """Validate and resolve 'deployers:' scripts (default: just the built-in symlink deployer)."""
        configured_deployers = cfg.get("deployers")
        if isinstance(configured_deployers, str):
            die(
                "conan: 'deployers:' must be a list of scripts, not a single string "
                f"(got {configured_deployers!r} -- write it as a one-entry list)"
            )
        deployers = []
        for entry in configured_deployers or [DEFAULT_DEPLOYER]:
            p = ctx.resolve_path(entry)
            if not p.is_file():
                die(f"conan: deployer script not found: {p}")
            deployers.append(str(p))
        return deployers

    @staticmethod
    def _resolve_profiles(cfg):
        """The 'profiles:' section as two explicit host/build lists."""
        profiles_cfg = cfg.get("profiles") or {}
        return {
            "host": list(profiles_cfg.get("host") or []),
            "build": list(profiles_cfg.get("build") or []),
        }

    @staticmethod
    def _resolve_remote_settings(cfg, resolved):
        """Fill in the remotes/user/channel keys the remote reconciliation and generated references use."""
        resolved["remotes"] = cfg.get("remotes") or {}
        resolved["keep-remotes"] = bool(cfg.get("keep-remotes", False))
        resolved["user"] = cfg.get("user") or "denver"
        resolved["channel"] = cfg.get("channel") or "snapshot"

    @classmethod
    def _resolve_base_classes(cls, ctx, cfg):
        """Validate and resolve 'base-classes:' dirs."""
        configured_base_classes = cfg.get("base-classes")
        if isinstance(configured_base_classes, str):
            die(
                "conan: 'base-classes:' must be a list of directories, not a single string "
                f"(got {configured_base_classes!r} -- write it as a one-entry list)"
            )
        base_classes = []
        for entry in configured_base_classes or []:
            d = ctx.resolve_path(entry)
            if not d.is_dir():
                die(f"conan: base-classes dir not found: {d}")
            base_classes.append(str(d))
        return base_classes

    @classmethod
    def _resolve_config_dirs(cls, ctx, cfg):
        """Validate and resolve 'config:' dirs, if any are configured (None => leave unset)."""
        configured_config_dirs = cfg.get("config")
        if not configured_config_dirs:
            return None
        config_dirs = []
        for entry in configured_config_dirs:
            p = ctx.resolve_path(entry)
            if not p.is_dir():
                die(f"conan: config dir not found: {p}")
            config_dirs.append(str(p))
        return config_dirs

    def setup(self, ctx):
        """Detect a conan profile, export/install every recipe, then activate the resulting conanbuildenv.sh."""
        cfg = self.config_section(ctx)

        conanfile = cfg["conanfile"]
        recipes = cfg["recipes"]
        has_recipes = self._has_recipes(recipes)
        remotes, cleanup_remotes, reconcile_remotes = self._resolve_remote_reconciliation(cfg)

        if ctx.fast:
            self._run_fast(ctx, has_recipes, reconcile_remotes)
            return

        conan = self._require_conan(ctx, cfg)
        python = ctx.which("python3") or "python3"
        base_classes_args = self._base_classes_args(cfg)

        # config install/profile detection always run (cheap, and every later
        # step needs a working conan home) -- bannered first, so their own
        # 'conan config install'/'conan config home' output (visible via
        # ctx.run's '+ cmd' echo) doesn't print ahead of any progress banner.
        banner(ctx, self.stage, "config")
        self._install_config(ctx, conan, cfg)
        self._ensure_profile(ctx, conan)

        if has_recipes or reconcile_remotes:
            self._run_prepare(
                ctx,
                python,
                DEFAULT_RECIPES_EXPORTER,
                base_classes_args,
                has_recipes,
                remotes,
                cleanup_remotes,
                reconcile_remotes,
            )

        self._export_recipes(ctx, cfg, python, recipes, base_classes_args)

        # 'conanfile:' is the only toggle: unset means "export/pin recipes
        # without installing anything" -- there is no separate 'install:'
        # flag to keep in sync with it.
        if not conanfile:
            banner(ctx, self.stage, "install (skipped: no conanfile configured)")
            return

        # `conan install` the configured 'conanfile:', writing its
        # conanbuildenv.sh straight into the install tree activate() reads.
        self._install(ctx, conan, cfg, cfg["deployers"], Path(conanfile))

        self._activate_buildenv(ctx)

    @staticmethod
    def _has_recipes(recipes):
        """Whether any 'recipes:' entry brings dirs of its own to export."""
        return any(recipe["dirs"] for recipe in recipes)

    def _require_conan(self, ctx, cfg):
        """The conan executable this stage runs, warned about if it shadows the active venv's own."""
        conan = cfg.get("exe")
        # dry_fallback: under --dry-run the uv stage that installs conan only
        # printed its commands, so conan legitimately isn't on PATH yet and
        # the bare name still renders this stage (see Context.which).
        resolved = ctx.which(conan, dry_fallback=True)
        if not resolved:
            die(f"conan[{self.stage}]: needs 'conan' on PATH -- normally installed by an earlier uv stage")
        # the shadow check needs an absolute path to compare against the
        # venv, which 'conan' itself (a bare 'conan:' default, or dry-run's
        # bare fallback) may not be -- 'resolved' is used only for this.
        self._warn_if_shadowing_venv(ctx, resolved)
        return conan

    @staticmethod
    def _base_classes_args(cfg):
        """Every 'base-classes:' dir as its own --base-classes-dir flag, in list order."""
        return [arg for d in cfg.get("base-classes") or [] for arg in ("--base-classes-dir", d)]

    def _activate_buildenv(self, ctx):
        """Activate the tools conan just installed, by sourcing the conanbuildenv.sh it wrote."""
        buildenv = ctx.env_workdir / CONAN_INSTALL_DIRNAME / CONANBUILDENV_NAME
        if buildenv.is_file():
            ctx.source(buildenv)

    @classmethod
    def _resolve_remote_reconciliation(cls, cfg):
        """Decide whether/how this run reconciles conan remotes -- see doc/providers/conan.md for the 'config:' exception.

        Returns ``(remotes, cleanup_remotes, reconcile_remotes)``.
        """
        remotes = cfg.get("remotes") or {}
        # cleanup is skipped when 'keep-remotes:' says so, or when both
        # 'remotes:' is left unset/empty *and* this env has its own
        # 'config:' (whose `conan config install` may itself have installed
        # a remotes.json denver never interprets, see doc/providers/conan.md):
        # reconciling an empty 'remotes:' to "exhaustive" in that case would
        # silently disable every remote config install just set up. An
        # explicit (non-empty) 'remotes:' still reconciles/cleans up as
        # normal regardless of 'config:'.
        cleanup_remotes = not cfg["keep-remotes"] and not (cfg.get("config") and not remotes)
        # cleanup makes 'remotes:' exhaustive even when empty (see module
        # docstring): reconciliation must then run regardless of whether any
        # are actually configured, to disable every remote already present.
        reconcile_remotes = cleanup_remotes or bool(remotes)
        return remotes, cleanup_remotes, reconcile_remotes

    def _run_fast(self, ctx, has_recipes, reconcile_remotes):
        """Run the --fast path: banner every stage as skipped, then just activate the existing conanbuildenv.sh."""
        # same banner sequence as a full run (see below), so --fast's
        # progress trail looks identical -- every substep says it was
        # skipped instead of silently vanishing, plus one extra
        # 'activate' substep for the real work --fast actually does.
        banner(ctx, self.stage, "config (skipped by --fast)")
        if has_recipes or reconcile_remotes:
            banner(ctx, self.stage, "prepare (skipped by --fast)")
        banner(ctx, self.stage, "export (skipped by --fast)")
        banner(ctx, self.stage, "install (skipped by --fast)")
        buildenv = ctx.env_workdir / CONAN_INSTALL_DIRNAME / CONANBUILDENV_NAME
        if not buildenv.is_file():
            die(f"conan[{self.stage}]: --fast needs an existing {buildenv} -- run once without --fast first")
        banner(ctx, self.stage, "activate")
        ctx.source(buildenv)

    def _run_prepare(
        self, ctx, python, recipes_exporter, base_classes_args, has_recipes, remotes, cleanup_remotes, reconcile_remotes
    ):
        """Run the exporter's --prepare pass: base classes on PYTHONPATH, remotes reconciliation, or both."""
        # remotes-only work, always via the env-wide exporter: --prepare
        # returns before any catalog is touched, so it takes no unit of
        # its own -- just the base classes, for their PYTHONPATH side.
        prepare_cmd = [python, str(recipes_exporter), "--prepare"]
        if has_recipes:
            prepare_cmd += base_classes_args
        if reconcile_remotes:
            prepare_cmd += ["--remotes-json", str(self._write_remotes_json(ctx, remotes))]
        if cleanup_remotes:
            prepare_cmd += ["--cleanup-remotes"]
        if ctx.force:
            prepare_cmd += ["--force"]
        ctx.run(prepare_cmd, step="prepare")

    def _export_recipes(self, ctx, cfg, python, recipes, base_classes_args):
        """Export every recipe's dirs (and write its catalog, if configured) into the local conan cache."""
        # build each recipe's catalog and export its recipes to the local
        # cache -- one invocation per 'recipes:' entry, over all of that
        # entry's dirs at once, so recipes in one dir can depend on recipes
        # in another dir of the same entry. A catalog file is only written
        # where the entry's 'catalog:' names one.
        banner(ctx, self.stage, "export")
        for recipe in recipes:
            if recipe["dirs"]:
                ctx.run(self._export_cmd(cfg, python, recipe, base_classes_args))

    @staticmethod
    def _export_cmd(cfg, python, recipe, base_classes_args):
        """The exporter's `--export` argv for one 'recipes:' entry, covering all of its dirs at once."""
        export_cmd = [python, recipe["export-tool"], "--export"]
        for recipe_dir in recipe["dirs"]:
            export_cmd += ["--recipes-dir", recipe_dir]
        export_cmd += ["--user", cfg["user"], "--channel", cfg["channel"]]
        if recipe["catalog"]:
            export_cmd += ["--export-catalog", recipe["catalog"]]
        return export_cmd + base_classes_args

    # ------------------------------------------------------------------ #
    def _write_remotes_json(self, ctx, remotes):
        """Serialize the resolved 'remotes:' config to a JSON file recipes.py's --remotes-json can read."""
        path = ctx.env_workdir / CONAN_INSTALL_DIRNAME / "remotes.json"
        ctx.mkdir(path.parent)
        ctx.write_text(path, json.dumps(remotes))
        return path

    def _install_config(self, ctx, conan, cfg):
        """Run `conan config install <dir>` for each configured dir, in order."""
        # dirs are already resolved + existence-checked centrally
        # (ConanProvider.resolve_defaults); conan itself does the rest.
        config_dirs = cfg.get("config") or []
        if not config_dirs:
            info("conan: no 'config:' key set -- skipping `conan config install`")
        for config_dir in config_dirs:
            ctx.run([conan, "config", "install", config_dir])

    def _warn_if_shadowing_venv(self, ctx, conan):
        """Warn when a venv is active but the conan about to run isn't the one inside it.

        A host-wide or in-image conan is a legitimate setup (see module
        docstring), so this is never fatal -- an env may well activate a
        venv for something else entirely and get conan from the image.

        But when an env installs conan via an earlier uv stage (as
        examples/raspberry-pico does), 'conan' on PATH is *meant* to be
        that venv's pinned one. ``exe``'s lookup now runs after that stage
        (see denver._run_stage_setup), so reaching this point means the
        venv really has no conan of its own -- an interrupted install a
        later run still considers satisfied, or a requirements file that
        simply never named conan -- and the host's is standing in for it,
        unpinned. That only shows up as an unrelated conan failure much
        further down, so say it here instead.
        """
        venv = ctx.env.get("VIRTUAL_ENV")
        # a bare name only ever reaches here from --dry-run's stand-in for a
        # conan no stage has installed yet (see Context.which): it names no
        # location at all, so there is nothing to compare against the venv,
        # and resolving it would silently make it cwd-relative.
        if ctx.dry_run and not Path(conan).is_absolute():
            return
        if not venv or Path(conan).resolve().is_relative_to(Path(venv).resolve()):
            return
        warn(
            f"conan[{self.stage}]: using {conan}, from outside the active venv ({venv}) -- "
            f"that venv has no conan of its own, so this run is not using a pinned version. "
            f"If it should be pinned, name conan in the uv stage's requirements (and --force "
            f"to rebuild the venv if it should already be there)."
        )

    def _ensure_profile(self, ctx, conan):
        """Auto-detect a conan profile, unless one already exists at <conan home>/profiles/default."""
        # `conan config home` is the first thing asked of conan here, and it
        # fails whenever conan's *home* is unusable at all (an invalid
        # global.conf, a cache written by another conan version, permissions)
        # -- nothing to do with this env's config. Handled here rather than
        # left to main()'s generic subprocess-failure message so it can name
        # the home involved: this is a capture=True call, so conan's own
        # stderr is sitting in the result rather than on the terminal.
        result = ctx.run([conan, "config", "home"], capture=True, echo=False, check=False)
        if result.returncode != 0:
            conan_home = ctx.env.get("CONAN_HOME") or "<unset -- conan's own default, usually ~/.conan2>"
            if not ctx.dry_run:
                die(
                    f"conan[{self.stage}]: `{conan} config home` failed (exit {result.returncode}) -- "
                    f"conan cannot use its home (CONAN_HOME={conan_home}):\n"
                    f"{(result.stderr or '').rstrip()}"
                )
            # a dry run reports and carries on: a conan that isn't installed
            # yet (an earlier uv stage would have) can't answer this, and
            # aborting here would hide every conan command below.
            warn(f"conan[{self.stage}]: `{conan} config home` failed -- showing profile detection as if it were needed")
            ctx.run([conan, "profile", "detect"])
            return
        home = result.stdout.strip()
        if not (Path(home) / "profiles" / "default").is_file():
            ctx.run([conan, "profile", "detect"])

    def _install(self, ctx, conan, cfg, deployers, conanfile):
        """`conan install` the configured conanfile, deploying it (via every 'deployers:' script) into a fresh tree.

        Skipped -- leaving the existing install tree untouched -- when a
        fast `conan graph info` query hashes to the same value the last
        successful install here stored, unless --force (which always
        reinstalls). See doc/providers/conan.md.
        """
        banner(ctx, self.stage, "install")
        install_root = ctx.env_workdir / CONAN_INSTALL_DIRNAME
        symlinks_dir = install_root / "symlinks"
        build_args = self._build_args(cfg)
        profile_args = self._profile_args(cfg)
        extra_args = self._extra_install_args(cfg)
        install_args = (build_args, profile_args, extra_args)

        skip, graph_hash = self._graph_unchanged(ctx, conan, install_root, conanfile, install_args)
        if skip:
            info(f"conan[{self.stage}]: dependency graph unchanged -- skipping `conan install`")
            return

        # start from a clean install tree
        ctx.rmtree(install_root)
        ctx.mkdir(install_root)

        deployer_args = [f"--deployer={d}" for d in deployers]

        # conan writes conanbuildenv.sh straight into install_root (the
        # --output-folder), exactly where _activate_buildenv reads it from --
        # a single conanfile needs no per-conanfile subdir or aggregation.
        ctx.run(
            [
                conan,
                "install",
                str(conanfile),
                *build_args,
                *profile_args,
                f"--output-folder={install_root}",
                *deployer_args,
                f"--deployer-folder={symlinks_dir}",
                *extra_args,
            ],
            cwd=install_root,
            # conan packages must be standalone: don't leak host PYTHONPATH
            extra_env={"PYTHONPATH": ""},
        )

        self._store_graph_hash(ctx, conan, install_root, conanfile, install_args, graph_hash)

    def _graph_unchanged(self, ctx, conan, install_root, conanfile, install_args):
        """Whether `_install` can skip entirely, plus the graph hash computed along the way (None if not queried).

        False/None whenever there's nothing to compare against yet --
        --force, no prior successful install here, or no stored hash from
        one -- without spending a `conan graph info` call to find out.
        """
        buildenv = install_root / CONANBUILDENV_NAME
        hash_path = install_root / f"{self.stage}-graph.sha256"
        if ctx.force or not buildenv.is_file() or not hash_path.is_file():
            return False, None
        graph_hash = self._graph_info_hash(ctx, conan, conanfile, *install_args)
        return graph_hash is not None and graph_hash == hash_path.read_text(), graph_hash

    def _store_graph_hash(self, ctx, conan, install_root, conanfile, install_args, graph_hash):
        """Record `conan graph info`'s hash for this install, reusing ``graph_hash`` if `_graph_unchanged` already computed it.

        `conan install` itself never changes what `conan graph info`
        reports (conanfile/profiles/recipe cache -- all fixed before this
        runs), so a hash from right before the install is still accurate
        to store after it -- avoiding a second query on every changed run.
        """
        if graph_hash is None:
            graph_hash = self._graph_info_hash(ctx, conan, conanfile, *install_args)
        if graph_hash is not None:
            ctx.write_text(install_root / f"{self.stage}-graph.sha256", graph_hash)

    @staticmethod
    def _graph_info_hash(ctx, conan, conanfile, build_args, profile_args, extra_args):
        """sha256 of `conan graph info`'s stdout for conanfile at the given args, or None if the query failed.

        No downloads/builds, so it's cheap enough to run on every non-fast,
        non-force setup -- comparing its hash against what the last
        successful install stored is what lets _install skip the rmtree/
        mkdir and the (often slow) `conan install` for an unchanged
        dependency graph. A failed query (conan not on PATH yet under
        --dry-run, a broken recipe, ...) is treated as "changed": the real
        `conan install` reports the actual problem itself.
        """
        result = ctx.run(
            [conan, "graph", "info", str(conanfile), *build_args, *profile_args, *extra_args, "--format=json"],
            capture=True,
            echo=False,
            check=False,
        )
        if result.returncode != 0:
            return None
        return hashlib.sha256(result.stdout.encode()).hexdigest()

    @staticmethod
    def _build_args(cfg):
        """What to build from source: 'build:' may be a string or a list, each value becoming a --build= flag."""
        build = cfg["build"]
        build = [build] if isinstance(build, str) else list(build)
        return [f"--build={b}" for b in build]

    @staticmethod
    def _profile_args(cfg):
        """'profiles:' as conan's own -pr:h=/-pr:b= flags, every host profile first."""
        profiles = cfg.get("profiles") or {}
        profile_args = [f"-pr:h={p}" for p in profiles.get("host") or []]
        profile_args += [f"-pr:b={p}" for p in profiles.get("build") or []]
        return profile_args

    @staticmethod
    def _extra_install_args(cfg):
        """'install-args:' as configured, plus conan's --no-remote when 'authentication:' is off."""
        extra_args = list(cfg.get("install-args") or [])
        if not cfg["authentication"]:
            extra_args.append("--no-remote")
        return extra_args
