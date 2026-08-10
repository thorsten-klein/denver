"""conan provider: provisions native tools via Conan and exposes them.

Configured from denver.yml -> ``conan:``:

    conan:
      exe: conan                                # conan executable (default: PATH)
      recipes-exporter: path/to/recipes.py      # default: bundled conan_scripts/recipes.py
      deployer:     path/to/symlink.py          # default: bundled conan_scripts/extensions/symlink.py
      base-classes:                             # optional; dirs of shared
      - conan/base_classes                      #  conanfile base classes, each
      - ../other-env/conan/base_classes         #  resolved (may live in a base env)
      conanfiles:                               # list of units, in install order
      - path: conan/conanfile.py                # required
        recipe-dirs:                            # optional; dirs directly
        - conan/recipes                         #  containing recipes, exported
        - ../other-env/conan/recipes            #  before this unit installs
        catalog: conan/catalog.yml              # optional; where this unit's
                                                #  catalog is written (unset =>
                                                #  built in memory, never written)
        recipes-exporter: path/to/recipes.py    # optional; overrides the
                                                #  top-level default above
      build: missing                            # --build=<value> (str or list)
      install-args: []                          # extra `conan install` args
      no-auth: false                            # true => conan install --no-remote
      profiles:                                 # each entry becomes its own
        host: []                                # -pr:h=<value> / -pr:b=<value> flag,
        build: []                               # in list order (default: none)
      config:                                   # optional list of dirs, each
      - path/to/config-dir                      # installed via `conan config
                                                 # install <dir>` (see below)
      remotes:                                  # optional, see below
        sdd:
          url: https://example.invalid/artifactory/api/conan/conan
          verify_ssl: true                      # default: true
          enabled: true                         # default: true
      cleanup-remotes: true                     # default: true, see below
      user: denver                              # conan user for each generated reference
      channel: snapshot                         # conan channel for each generated reference

`config` entries are plain directories the env author controls: whatever
conan's own `config install` understands there (profiles, `remotes.json`,
`credentials.json`, `source_credentials.json`, `settings.yml`, ...) is
installed into the conan cache by conan itself -- denver never opens or
interprets any file inside them, it only runs `conan config install <dir>`
for each, in order, before profile detection.

`remotes` is a project-owned, exhaustive list of the conan remotes this env
wants: when the prepare stage runs, the recipes-exporter adds/renames/enables
exactly these remotes and disables every *other* remote already present in
the conan home -- so `remotes:` should list every remote the env needs, not
just ones being added. A remote's own login (if reachable) runs as part of
the same stage. `CONAN_REMOTE_ENABLE_<NAME>` (env var, `ON`/`OFF`) overrides
a given remote's `enabled:` at run time.

`cleanup-remotes` (default `true`) makes `remotes:` exhaustive even when
it's left unset/empty: the prepare stage then disables *every* remote
already present in the conan home, so each env's remote configuration is
fully self-contained regardless of what an earlier run of a *different* env
left behind. Set `cleanup-remotes: false` to opt out instead: with no
`remotes:` of its own, this env leaves the conan home's existing remote
configuration alone entirely, rather than reconciling it to nothing.

`cleanup-remotes` is automatically skipped (regardless of its own value)
whenever `remotes:` is left unset/empty *and* this env also has a
`config:` (its `conan config install <dir>` may itself have installed a
`remotes.json` enabling/disabling remotes -- denver never opens or
interprets that file, see `config` above, so it can't tell which remotes it
owns): reconciling an empty `remotes:` to "exhaustive" would otherwise
silently disable everything `config:` just set up. An explicit (non-empty)
`remotes:` still reconciles/cleans up as normal regardless of `config:`; an
env with no `config:` of its own also still gets the full exhaustive-cleanup
behavior.

`user`/`channel` (default `"denver"`/`"snapshot"`) become the user/channel
half of every reference the recipes-exporter generates while exporting recipes
(`name/version@user/channel`) -- never read from a real environment
variable.

The provider then detects a conan profile, (re)generates + exports each
recipe source via the recipes-exporter, installs every conanfile with the
symlink deployer, then sources the aggregated conanbuildenv.sh into ctx.env
so the tools are on PATH.

Every default above (exe/recipes-exporter/deployer paths, build) is computed
once, centrally, by ``ConanProvider.resolve_defaults`` -- not in setup(). By
the time this provider's setup() runs, its config section already has every
default filled in (see ``denver.resolve_provider_defaults``), so nothing
here ever falls back to a PATH lookup itself.

`conanfiles` is a list of *units*, not of paths: each entry keeps a
conanfile together with the recipes it is installed from, so stacking envs
appends whole units instead of merging several parallel lists that the
author has to keep aligned in their head. Only `path:` is required; a bare
string entry is rejected outright, so a unit always says what it is. A
unit's `recipe-dirs:` are exported (as one catalog, see below) immediately
before the install pass, in unit order.

`catalog:` is where a unit's catalog -- every one of its recipes pinned as
`name/version@user/channel#rrev` -- is written. Without it the catalog is
built in memory, handed straight to the export step and never touches disk,
which is the default: a run leaves no generated file behind in the recipe
tree unless an env asks for one, e.g. to review/commit the pinned
references, or to have the unit's own `conanfile.py` read its pins back.
Nothing else changes either way -- export/create/upload read the in-memory
catalog regardless.

A unit's catalog covers *all* of its `recipe-dirs:` together, so recipes in
one dir may depend on recipes in another dir of the same unit, and so a
unit's catalog content is determined by that unit's membership. Splitting
recipes into their own unit is what keeps their catalog independent of what
another unit does.

`recipes-exporter:` may be set per unit, overriding the top-level default
(itself defaulting to the bundled `conan_scripts/recipes.py`) for that unit
only. `deployer`, `base-classes`, `user` and `channel` stay env-wide.

`base-classes` and `conanfiles` are never guessed from the env's directory
layout: denver does not go looking for a `conan/recipes`,
`conan/base_classes` or `conan/conanfile.py` that happens to exist. Each is
simply unset/empty unless the `denver.yml` says otherwise, and a path that
*is* listed must exist (it's an error if it doesn't) -- so what conan
exports and installs is always exactly what the config names, and an env
that inherits a base's recipes says so explicitly.

conan itself must already be available wherever this stage runs -- denver
never installs it: in practice an earlier uv stage does, by listing
``conan`` in its ``requirements:``, but a host-wide or in-image install
works just as well (and ``exe:`` can name a specific one).

Full key reference, worked examples and design notes: ``doc/providers/conan.md``.
"""

import json
import shutil
from pathlib import Path

from .base import Provider, fill_unset
from .context import banner, die, warn

# ships alongside this module, so it's found regardless of whether denver
# runs from a checkout or an installed package (see providers/conan_scripts).
CONAN_SCRIPTS_DIR = Path(__file__).resolve().parent / "conan_scripts"

# name of conan's own install tree (under ctx.env_workdir) and the buildenv
# script it aggregates into -- referenced from several stages below.
CONAN_INSTALL_DIRNAME = ".conan"
CONANBUILDENV_NAME = "conanbuildenv.sh"


class ConanProvider(Provider):
    """Provisions native tools via Conan and exposes them on PATH -- see module docstring for denver.yml keys."""

    name = "conan"
    KEYS = (
        "exe",
        "recipes-exporter",
        "deployer",
        "base-classes",
        "conanfiles",
        "build",
        "install-args",
        "no-auth",
        "profiles",
        "config",
        "remotes",
        "cleanup-remotes",
        "user",
        "channel",
    )

    UNIT_KEYS = ("path", "recipe-dirs", "catalog", "recipes-exporter")

    @classmethod
    def _resolve_unit(cls, ctx, entry, *, default_exporter):
        """Resolve one 'conanfiles:' unit -- its conanfile, recipe dirs, catalog and exporter.

        Every path is resolved and existence-checked here (except ``catalog``,
        which is an *output*), so setup() only ever handles absolute paths
        that are known to be there. See the module docstring for the shape.
        """
        if not isinstance(entry, dict):
            die(
                "conan: each 'conanfiles:' entry must be a mapping with a 'path:' "
                f"(got {entry!r} -- write it as '- path: {entry}')"
            )
        unknown = sorted(set(entry) - set(cls.UNIT_KEYS))
        if unknown:
            die(f"conan: unknown key(s) in a 'conanfiles:' entry: {', '.join(unknown)}")
        if not entry.get("path"):
            die(f"conan: a 'conanfiles:' entry needs a 'path:' (got {entry!r})")

        path = ctx.resolve_path(entry["path"])
        if not path.is_file():
            die(f"conan: conanfile not found: {path}")

        recipe_dirs = cls._resolve_recipe_dirs(ctx, entry)
        catalog = cls._resolve_catalog(entry, recipe_dirs, path)

        exporter = ctx.resolve_path(entry["recipes-exporter"]) if entry.get("recipes-exporter") else None
        if exporter and not exporter.is_file():
            die(f"conan: recipes-exporter not found: {exporter}")

        return {
            "path": str(path),
            "recipe-dirs": recipe_dirs,
            "catalog": str(ctx.resolve_path(catalog)) if catalog else "",
            "recipes-exporter": str(exporter) if exporter else default_exporter,
        }

    @classmethod
    def _resolve_recipe_dirs(cls, ctx, entry):
        """Validate and resolve one unit's 'recipe-dirs:' entries."""
        recipe_dirs = []
        for name in entry.get("recipe-dirs") or []:
            d = ctx.resolve_path(name)
            if not d.is_dir():
                die(f"conan: recipe dir not found: {d}")
            recipe_dirs.append(str(d))
        return recipe_dirs

    @classmethod
    def _resolve_catalog(cls, entry, recipe_dirs, path):
        """Validate one unit's 'catalog:' entry (never resolved here -- it's an output, not an input)."""
        catalog = entry.get("catalog") or ""
        if not isinstance(catalog, str):
            die(f"conan: 'catalog:' must be a single path, not {catalog!r}")
        if catalog and not recipe_dirs:
            die(f"conan: 'catalog: {catalog}' has no 'recipe-dirs:' to build a catalog from ({path})")
        return catalog

    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):  # noqa: ARG003  # shared (ctx, cfg, config) signature
        """Resolve exe/recipes-exporter/deployer/base-classes paths, recipe-dirs, conanfiles, build, etc.

        See module docstring.
        """
        resolved = dict(cfg)
        resolved["exe"] = cfg.get("exe") or ctx.which("conan")
        resolved["recipes-exporter"] = str(
            ctx.resolve_path(cfg.get("recipes-exporter") or CONAN_SCRIPTS_DIR / "recipes.py")
        )
        resolved["deployer"] = str(
            ctx.resolve_path(cfg.get("deployer") or CONAN_SCRIPTS_DIR / "extensions" / "symlink.py")
        )
        resolved["base-classes"] = cls._resolve_base_classes(ctx, cfg)

        resolved["conanfiles"] = [
            cls._resolve_unit(ctx, entry, default_exporter=resolved["recipes-exporter"])
            for entry in cfg.get("conanfiles") or []
        ]

        resolved["build"] = cfg.get("build", "missing")
        resolved["no-auth"] = bool(cfg.get("no-auth", False))

        profiles_cfg = cfg.get("profiles") or {}
        resolved["profiles"] = {
            "host": list(profiles_cfg.get("host") or []),
            "build": list(profiles_cfg.get("build") or []),
        }

        config_dirs = cls._resolve_config_dirs(ctx, cfg)
        if config_dirs is not None:
            resolved["config"] = config_dirs

        resolved["remotes"] = cfg.get("remotes") or {}
        resolved["cleanup-remotes"] = bool(cfg.get("cleanup-remotes", True))
        resolved["user"] = cfg.get("user") or "denver"
        resolved["channel"] = cfg.get("channel") or "snapshot"

        return fill_unset(resolved, cls.KEYS)

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

        units = cfg["conanfiles"]
        has_recipes = any(unit["recipe-dirs"] for unit in units)
        remotes = cfg.get("remotes") or {}
        cleanup_remotes, reconcile_remotes = self._resolve_remote_reconciliation(cfg, remotes)

        if ctx.fast:
            self._run_fast(ctx, has_recipes, reconcile_remotes)
            return

        conan = cfg.get("exe")
        if not conan:
            die("conan provider needs 'conan' on PATH (installed by the uv provider)")
        self._warn_if_shadowing_venv(ctx, conan)
        python = ctx.which("python3") or "python3"

        recipes_exporter = Path(cfg["recipes-exporter"])
        deployer = Path(cfg["deployer"])
        # each dir becomes its own --base-classes-dir flag, in list order
        base_classes_args = [arg for d in cfg.get("base-classes") or [] for arg in ("--base-classes-dir", d)]

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
                recipes_exporter,
                base_classes_args,
                has_recipes,
                remotes,
                cleanup_remotes,
                reconcile_remotes,
            )

        self._export_units(ctx, cfg, python, units, base_classes_args)

        # `conan install` every unit's conanfile in order, each aggregating
        # its own conanbuildenv.sh into one overall file.
        self._install(ctx, conan, cfg, deployer, [Path(unit["path"]) for unit in units])

        # activate the tools conan just installed
        buildenv = ctx.env_workdir / CONAN_INSTALL_DIRNAME / CONANBUILDENV_NAME
        if buildenv.is_file():
            ctx.source(buildenv)

    @classmethod
    def _resolve_remote_reconciliation(cls, cfg, remotes):
        """Decide whether/how this run reconciles conan remotes -- see module docstring for the 'config:' exception."""
        # cleanup-remotes is skipped when both 'remotes:' is left unset/empty
        # *and* this env has its own 'config:' (whose `conan config install`
        # may itself have installed a remotes.json denver never interprets,
        # see module docstring): reconciling an empty 'remotes:' to
        # "exhaustive" in that case would silently disable every remote
        # config install just set up. An explicit (non-empty) 'remotes:'
        # still reconciles/cleans up as normal regardless of 'config:'.
        cleanup_remotes = cfg["cleanup-remotes"] and not (cfg.get("config") and not remotes)
        # cleanup-remotes makes 'remotes:' exhaustive even when empty (see
        # module docstring): reconciliation must then run regardless of
        # whether any are actually configured, to disable every remote
        # already present.
        reconcile_remotes = cleanup_remotes or bool(remotes)
        return cleanup_remotes, reconcile_remotes

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

    def _export_units(self, ctx, cfg, python, units, base_classes_args):
        """Export every unit's recipe-dirs (and write its catalog, if configured) into the local conan cache."""
        # build each unit's catalog and export its recipes to the local cache
        # -- one invocation per unit, over all of that unit's recipe-dirs at
        # once, so recipes in one dir can depend on recipes in another dir of
        # the same unit. A catalog file is only written where the unit's
        # 'catalog:' names one.
        banner(ctx, self.stage, "export")
        for unit in units:
            if not unit["recipe-dirs"]:
                continue
            export_cmd = [python, unit["recipes-exporter"], "--export"]
            for recipe_dir in unit["recipe-dirs"]:
                export_cmd += ["--recipes-dir", recipe_dir]
            export_cmd += ["--user", cfg["user"], "--channel", cfg["channel"]]
            if unit["catalog"]:
                export_cmd += ["--export-catalog", unit["catalog"]]
            export_cmd += base_classes_args
            ctx.run(export_cmd)

    # ------------------------------------------------------------------ #
    def _write_remotes_json(self, ctx, remotes):
        """Serialize the resolved 'remotes:' config to a JSON file recipes.py's --remotes-json can read."""
        path = ctx.env_workdir / CONAN_INSTALL_DIRNAME / "remotes.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(remotes))
        return path

    def _install_config(self, ctx, conan, cfg):
        """Run `conan config install <dir>` for each configured dir, in order."""
        # dirs are already resolved + existence-checked centrally
        # (ConanProvider.resolve_defaults); conan itself does the rest.
        for config_dir in cfg.get("config") or []:
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
            die(
                f"conan[{self.stage}]: `{conan} config home` failed (exit {result.returncode}) -- "
                f"conan cannot use its home (CONAN_HOME={conan_home}):\n"
                f"{(result.stderr or '').rstrip()}"
            )
        home = result.stdout.strip()
        if not (Path(home) / "profiles" / "default").is_file():
            ctx.run([conan, "profile", "detect"])

    def _install(self, ctx, conan, cfg, deployer, conanfiles):
        """`conan install` every conanfile in order, symlink-deploying each into a shared tree."""
        banner(ctx, self.stage, "install")
        install_root = ctx.env_workdir / CONAN_INSTALL_DIRNAME
        symlinks_dir = install_root / "symlinks"
        overall_buildenv = install_root / CONANBUILDENV_NAME

        # start from a clean install tree, then aggregate each conanbuildenv.sh
        shutil.rmtree(install_root, ignore_errors=True)
        install_root.mkdir(parents=True, exist_ok=True)
        overall_buildenv.touch()

        no_auth = cfg["no-auth"]

        # what to build from source: `build:` may be a string or a list of
        # values, each becoming a --build=<value> flag.
        build = cfg["build"]
        build = [build] if isinstance(build, str) else list(build)
        build_args = [f"--build={b}" for b in build]

        profiles = cfg.get("profiles") or {}
        profile_args = [f"-pr:h={p}" for p in profiles.get("host") or []]
        profile_args += [f"-pr:b={p}" for p in profiles.get("build") or []]

        extra_args = list(cfg.get("install-args") or [])
        if no_auth:
            extra_args.append("--no-remote")

        for index, conanfile in enumerate(conanfiles):
            out_dir = install_root / f"conanfile-{index}"
            out_dir.mkdir(parents=True, exist_ok=True)
            ctx.run(
                [
                    conan,
                    "install",
                    str(conanfile),
                    *build_args,
                    *profile_args,
                    f"--output-folder={out_dir}",
                    f"--deployer={deployer}",
                    f"--deployer-folder={symlinks_dir}",
                    *extra_args,
                ],
                cwd=out_dir,
                # conan packages must be standalone: don't leak host PYTHONPATH
                extra_env={"PYTHONPATH": ""},
            )
            with overall_buildenv.open("a") as fh:
                fh.write(f"source {out_dir / CONANBUILDENV_NAME}\n")
