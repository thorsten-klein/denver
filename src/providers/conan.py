"""conan provider: provisions native tools via Conan and exposes them.

Configured from denver.yml -> ``conan:``:

    conan:
      exe: conan                                # conan executable (default: PATH)
      recipes-exporter: path/to/recipes.py      # default: bundled conan_scripts/recipes.py
      deployer:     path/to/symlink.py          # default: bundled conan_scripts/extensions/symlink.py
      base-classes: conan/base_classes          # optional; resolved (may live in base env)
      recipe-dirs:                              # dirs directly containing recipes
      - path/to/recipes                         # (optional, but never guessed:
      - path/to/more-recipes                    #  each dir must be listed here)
      conanfiles:                               # installed in order (optional)
      - conan/conanfile.py
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

`base-classes`, `recipe-dirs` and `conanfiles` are never guessed from the
env's directory layout: denver does not go looking for a `conan/recipes`,
`conan/base_classes` or `conan/conanfile.py` that happens to exist. Each is
simply unset/empty unless the `denver.yml` says otherwise, and a path that
*is* listed must exist (it's an error if it doesn't) -- so what conan
exports and installs is always exactly what the config names, and an env
that inherits a base's recipes says so explicitly.

Full key reference, worked examples and design notes: ``doc/providers/conan.md``.
"""

import json
import shutil
from pathlib import Path

from .base import Provider, fill_unset
from .context import banner, die

# ships alongside this module, so it's found regardless of whether denver
# runs from a checkout or an installed package (see providers/conan_scripts).
CONAN_SCRIPTS_DIR = Path(__file__).resolve().parent / "conan_scripts"


class ConanProvider(Provider):
    """Provisions native tools via Conan and exposes them on PATH -- see module docstring for denver.yml keys."""

    name = "conan"
    KEYS = (
        "exe",
        "recipes-exporter",
        "deployer",
        "base-classes",
        "recipe-dirs",
        "conanfile",
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

    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):  # noqa: ARG003 -- shared (ctx, cfg, config) signature
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
        resolved["base-classes"] = str(ctx.resolve_path(cfg["base-classes"])) if cfg.get("base-classes") else None

        recipe_dirs = []
        for entry in cfg.get("recipe-dirs") or []:
            d = ctx.resolve_path(entry)
            if not d.is_dir():
                die(f"conan: recipe dir not found: {d}")
            recipe_dirs.append(str(d))
        resolved["recipe-dirs"] = recipe_dirs

        configured_conanfiles = cfg.get("conanfiles")
        if configured_conanfiles is None and cfg.get("conanfile"):
            configured_conanfiles = [cfg["conanfile"]]
        conanfiles = []
        for entry in configured_conanfiles or []:
            p = ctx.resolve_path(entry)
            if not p.is_file():
                die(f"conan: conanfile not found: {p}")
            conanfiles.append(str(p))
        resolved["conanfiles"] = conanfiles

        resolved["build"] = cfg.get("build", "missing")
        resolved["no-auth"] = bool(cfg.get("no-auth", False))

        profiles_cfg = cfg.get("profiles") or {}
        resolved["profiles"] = {
            "host": list(profiles_cfg.get("host") or []),
            "build": list(profiles_cfg.get("build") or []),
        }

        configured_config_dirs = cfg.get("config")
        if configured_config_dirs:
            config_dirs = []
            for entry in configured_config_dirs:
                p = ctx.resolve_path(entry)
                if not p.is_dir():
                    die(f"conan: config dir not found: {p}")
                config_dirs.append(str(p))
            resolved["config"] = config_dirs

        resolved["remotes"] = cfg.get("remotes") or {}
        resolved["cleanup-remotes"] = bool(cfg.get("cleanup-remotes", True))
        resolved["user"] = cfg.get("user") or "denver"
        resolved["channel"] = cfg.get("channel") or "snapshot"

        return fill_unset(resolved, cls.KEYS)

    def setup(self, ctx):
        """Detect a conan profile, export/install every recipe, then activate the resulting conanbuildenv.sh."""
        cfg = self.config_section(ctx)

        recipe_dirs = [Path(d) for d in cfg["recipe-dirs"]]
        remotes = cfg.get("remotes") or {}
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
        if ctx.fast:
            # same banner sequence as a full run (see below), so --fast's
            # progress trail looks identical -- every substep says it was
            # skipped instead of silently vanishing, plus one extra
            # 'activate' substep for the real work --fast actually does.
            banner(ctx, self.stage, "config (skipped by --fast)")
            if recipe_dirs or reconcile_remotes:
                banner(ctx, self.stage, "prepare (skipped by --fast)")
            banner(ctx, self.stage, "export (skipped by --fast)")
            banner(ctx, self.stage, "install (skipped by --fast)")
            buildenv = ctx.env_workdir / ".conan" / "conanbuildenv.sh"
            if not buildenv.is_file():
                die(f"conan[{self.stage}]: --fast needs an existing {buildenv} -- run once without --fast first")
            banner(ctx, self.stage, "activate")
            ctx.source(buildenv)
            return

        conan = cfg.get("exe")
        if not conan:
            die("conan provider needs 'conan' on PATH (installed by the pip provider)")
        python = ctx.which("python3") or "python3"

        recipes_exporter = Path(cfg["recipes-exporter"])
        deployer = Path(cfg["deployer"])
        base_classes = Path(cfg["base-classes"]) if cfg.get("base-classes") else None
        conanfiles = [Path(p) for p in cfg["conanfiles"]]

        # config install/profile detection always run (cheap, and every later
        # step needs a working conan home) -- bannered first, so their own
        # 'conan config install'/'conan config home' output (visible via
        # ctx.run's '+ cmd' echo) doesn't print ahead of any progress banner.
        banner(ctx, self.stage, "config")
        self._install_config(ctx, conan, cfg)
        self._ensure_profile(ctx, conan)

        if recipe_dirs or reconcile_remotes:
            prepare_cmd = [python, str(recipes_exporter), "--prepare"]
            if recipe_dirs:
                shared = recipe_dirs[0]
                prepare_cmd += [
                    "--recipes-dir",
                    str(shared),
                    "--catalog-yml",
                    str(shared / "catalog.yml"),
                ]
                if base_classes:
                    prepare_cmd += ["--base-classes-dir", str(base_classes)]
            if reconcile_remotes:
                prepare_cmd += ["--remotes-json", str(self._write_remotes_json(ctx, remotes))]
            if cleanup_remotes:
                prepare_cmd += ["--cleanup-remotes"]
            if ctx.force:
                prepare_cmd += ["--force"]
            ctx.run(prepare_cmd, step="prepare")

        # (re)generate catalog.yml and export every recipe to the local
        # cache, for *every* recipe-dir (unlike prepare above).
        banner(ctx, self.stage, "export")
        for recipe_dir in recipe_dirs:
            export_cmd = [
                python,
                str(recipes_exporter),
                "--export",
                "--recipes-dir",
                str(recipe_dir),
                "--catalog-yml",
                str(recipe_dir / "catalog.yml"),
                "--user",
                cfg["user"],
                "--channel",
                cfg["channel"],
            ]
            if base_classes:
                export_cmd += ["--base-classes-dir", str(base_classes)]
            ctx.run(export_cmd)

        # `conan install` every conanfile in order, each aggregating its own
        # conanbuildenv.sh into one overall file.
        self._install(ctx, conan, cfg, deployer, conanfiles)

        # activate the tools conan just installed
        buildenv = ctx.env_workdir / ".conan" / "conanbuildenv.sh"
        if buildenv.is_file():
            ctx.source(buildenv)

    # ------------------------------------------------------------------ #
    def _write_remotes_json(self, ctx, remotes):
        """Serialize the resolved 'remotes:' config to a JSON file recipes.py's --remotes-json can read."""
        path = ctx.env_workdir / ".conan" / "remotes.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(remotes))
        return path

    def _install_config(self, ctx, conan, cfg):
        """Run `conan config install <dir>` for each configured dir, in order."""
        # dirs are already resolved + existence-checked centrally
        # (ConanProvider.resolve_defaults); conan itself does the rest.
        for config_dir in cfg.get("config") or []:
            ctx.run([conan, "config", "install", config_dir])

    def _ensure_profile(self, ctx, conan):
        """Auto-detect a conan profile, unless one already exists at <conan home>/profiles/default."""
        home = ctx.run([conan, "config", "home"], capture=True, echo=False).stdout.strip()
        if not (Path(home) / "profiles" / "default").is_file():
            ctx.run([conan, "profile", "detect"])

    def _install(self, ctx, conan, cfg, deployer, conanfiles):
        """`conan install` every conanfile in order, symlink-deploying each into a shared tree."""
        banner(ctx, self.stage, "install")
        install_root = ctx.env_workdir / ".conan"
        symlinks_dir = install_root / "symlinks"
        overall_buildenv = install_root / "conanbuildenv.sh"

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
                fh.write(f"source {out_dir / 'conanbuildenv.sh'}\n")
