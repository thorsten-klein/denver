"""pip provider: a generic Python virtualenv managed with uv.

Everything is configured from denver.yml -> ``pip:``:

    pip:
      python: "3.12.3"            # interpreter version for the venv
      uv: uv                      # uv executable (default: uv on PATH)
      requirements:               # -r files, installed together
      - path/to/requirements.txt
      install-args:                # extra literal `uv pip install` args (optional)
      - --pre
      - $(west packages pip)      # a '$(...)' entry is instead run as a shell
                                   # command; its stdout is split on whitespace
                                   # and each token appended as its own arg
      overrides:                  # --override files (optional)
      - path/to/overrides.txt
      find-links:                 # extra wheel sources (optional, e.g. a cache)
      - ${DENVER_ENV_WORKDIR}/.conan/...
      no-index: false             # true|false|auto (default false; 'auto' =>
                                   # true inside docker, false on the host)
      link-mode: copy             # uv link mode
      venv-patcher:                # applies venv patches (optional); when set,
        exe: venv-patcher          # executable (default: venv-patcher on PATH)
        patches: pip/venv-patcher/patches.yml   # 'patches:' is required
      skip-if:                    # skip (re)install if these scripts all exit 0
      - check.sh                  # (optional; never guessed, see below)
      freeze-to:                   # write `uv pip freeze`'s output here after
        path/to/frozen.txt         # a real install (optional)
      append-mode: false           # accumulate every 'uv pip install' arg ever
                                    # seen across runs, instead of installing
                                    # strictly from this run's own resolved
                                    # args (default: false) -- see below

The provider creates the venv (recreating it when the requirement files or
any 'install-args:' command's output change), activates it into ctx.env,
installs the requirements and applies venv patches.

``append-mode`` (default ``false``) makes every 'uv pip install' invocation
reuse every -r/--override/--find-links/--no-index/literal arg any previous
run of this stage ever resolved, appending only what's new this run -- so a
source that drops out later (e.g. a project leaving `west packages pip`'s
output) never causes uv to reconsider, and potentially downgrade or drop, a
package only *that* source ever pulled in. The trade-off: the resulting venv
depends on this machine's run history, not just the current denver.yml, so
turning it on is a deliberate choice, not the default. The accumulated arg
list is kept in ``<DENVER_DIR>/.envs/<env>/.logs/<stage>-install-args.json``,
outside the venv itself (so it survives a checksum-triggered venv
recreation); delete the file to reset it.

Several pip stages may share one venv (via an unset/identical 'venv:') --
e.g. so `west`'s own extension commands (imported into the *same* running
`west` process) can see packages a later stage installs on top of what an
earlier one built. Only the first such stage (in 'stages:' order) to touch a
given venv this run ever decides whether to recreate it; every later stage
sharing it only ever installs on top, never wipes what the first one built.

Every default above (python's version, uv/venv-patcher's executable,
no-index's auto-resolution, ...) is computed once, centrally, by
``PipProvider.resolve_defaults`` -- not in setup(). By the time this
provider's setup() runs, its config section already has every default
filled in (see ``denver.resolve_provider_defaults``), so nothing here ever
falls back to a PATH lookup itself.

``skip-if`` and ``venv-patcher`` are never guessed from the env's directory
layout: denver does not go looking for a ``pip/skip-if.sh``/``.py`` or a
``pip/venv-patcher/patches.yml`` that happens to exist. With no ``skip-if:``
there is no skip check, and the venv patcher runs only when a
``venv-patcher:`` section names its ``patches:`` file explicitly (an
unreadable or missing one is an error, not a silent no-op).

``uv`` itself must already be installed wherever this stage runs -- denver
never installs it: the host for a plain run, or the image when a ``docker``
stage relocated the pipeline first.

Full key reference, worked examples and design notes: ``doc/providers/pip.md``.
"""

import hashlib
import json
import shutil

from .base import Provider, fill_unset
from .context import banner, die, info, sha256_of_files

# uv pip install flags that take exactly one following value -- used to keep
# a flag and its value together as one atomic unit when accumulating args
# across runs (see PipProvider._group_args); anything else (a bare flag like
# --no-index, or a literal install-args token) is its own one-token unit.
_VALUE_FLAGS = ("-r", "--override", "--find-links")


class PipProvider(Provider):
    """A generic Python virtualenv managed with uv -- see module docstring for denver.yml keys."""

    name = "pip"
    KEYS = (
        "python",
        "uv",
        "no-index",
        "link-mode",
        "skip-if",
        "venv-patcher",
        "requirements",
        "install-args",
        "overrides",
        "find-links",
        "venv",
        "freeze-to",
        "append-mode",
    )

    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):  # noqa: ARG003 -- shared (ctx, cfg, config) signature
        """Resolve python/uv/no-index/skip-if/venv-patcher defaults -- see module docstring."""
        resolved = dict(cfg)
        resolved["python"] = str(cfg.get("python") or "3.12.3")
        resolved["uv"] = cfg.get("uv") or ctx.which("uv")
        no_index = cfg.get("no-index", False)
        resolved["no-index"] = ctx.in_docker if no_index == "auto" else bool(no_index)
        resolved["link-mode"] = cfg.get("link-mode", "copy")
        resolved["append-mode"] = cfg.get("append-mode", False)

        # resolved centrally (like every other path in this section) so
        # --show-config shows the real script; whether it actually *exists*
        # is checked at run time, right before we'd run it (see _skip_install).
        resolved["skip-if"] = [str(ctx.resolve_path(s)) for s in cfg.get("skip-if") or []]

        if cfg.get("venv-patcher"):
            vp_cfg = dict(cfg["venv-patcher"])
            patches = vp_cfg.get("patches")
            if not patches:
                die("pip: 'venv-patcher:' needs an explicit 'patches:' file")
            patches_path = ctx.resolve_path(patches)
            if not patches_path.is_file():
                die(f"pip: venv-patcher patches file not found: {patches_path}")
            vp_cfg["patches"] = str(patches_path)
            vp_cfg["exe"] = vp_cfg.get("exe") or ctx.which("venv-patcher")
            resolved["venv-patcher"] = vp_cfg

        return fill_unset(resolved, cls.KEYS)

    def setup(self, ctx):
        """Create/activate the venv, install requirements (unless --fast), and apply venv patches."""
        cfg = self.config_section(ctx)
        # each pip stage may target its own venv (config 'venv:'); default venv
        # otherwise. This is what lets an env have several pip stages.
        venv_dir = ctx.venv_dir_for(cfg.get("venv"))

        if ctx.fast:
            banner(ctx, self.stage, "install (skipped by --fast)")
            if not venv_dir.is_dir():
                die(f"pip[{self.stage}]: --fast needs an existing venv at {venv_dir} -- run once without --fast first")
            banner(ctx, self.stage, "activate")
            self._activate(ctx, venv_dir)
            return

        banner(ctx, self.stage, "install")

        python_version = cfg["python"]

        requirements = [ctx.resolve_path(r) for r in (cfg.get("requirements") or [])]
        overrides = [ctx.resolve_path(o) for o in (cfg.get("overrides") or [])]
        install_args, command_outputs = self._resolve_install_args(ctx, cfg)

        if not requirements and not install_args:
            info(f"pip[{self.stage}]: no requirements configured; only creating the venv")

        uv = cfg.get("uv")
        if not uv:
            die("pip provider needs 'uv' on PATH (see https://docs.astral.sh/uv/)")

        self._ensure_python(ctx, uv, python_version)
        self._ensure_venv(ctx, uv, venv_dir, python_version, requirements + overrides, command_outputs)
        self._activate(ctx, venv_dir)

        if requirements or install_args:
            self._install(ctx, uv, cfg, requirements, overrides, install_args)
            self._apply_patches(ctx, cfg)
            self._store_checksums(venv_dir, requirements + overrides, command_outputs)
            self._freeze(ctx, cfg, uv)

    # ------------------------------------------------------------------ #
    def _resolve_install_args(self, ctx, cfg):
        """Resolve 'install-args:' into a flat list of literal `uv pip install` args.

        A plain entry is used as one literal arg, verbatim (e.g. '--pre', or
        a bare package spec). A '$(shell command)' entry (see module
        docstring) is instead run right here, via bash -- its stdout is
        split on whitespace and each token becomes its own arg. Returns
        (args, command_outputs): command_outputs is each command's raw
        captured output, kept so a later checksum comparison
        (_ensure_venv/_store_checksums) can detect the command's *output*
        changing even when no requirements *file* did (e.g. `west packages
        pip` reacting to a workspace/manifest change).
        """
        args = []
        command_outputs = []
        for entry in cfg.get("install-args") or []:
            if entry.startswith("$(") and entry.endswith(")"):
                output = ctx.run(["bash", "-c", entry[2:-1]], capture=True, echo=False).stdout
                command_outputs.append(output)
                args += output.split()
            else:
                args.append(entry)
        return args, command_outputs

    def _requirements_checksum(self, files, command_outputs):
        """sha256_of_files(files), plus each 'install-args:' command's captured output hashed in too.

        Both must feed the same checksum (compared by _ensure_venv, stored by
        _store_checksums) so drift in either a requirements *file* or a
        command's *output* is detected the same way.
        """
        blob = sha256_of_files(files)
        for i, output in enumerate(command_outputs):
            blob += f"\n{hashlib.sha256(output.encode()).hexdigest()}  <install-args command #{i}>"
        return blob

    def _ensure_python(self, ctx, uv, version):
        """Make interpreter ``version`` available to uv: verified offline in docker, installed otherwise."""
        if ctx.in_docker:
            # in docker the interpreter is fixed (baked into the image); we can't
            # install a different one, so just assert it matches what pip.python
            # asks for, then let uv find (not install) it.
            result = ctx.run(["python3", "--version"], capture=True, echo=False)
            installed = result.stdout.split()[-1]
            if installed != version:
                die(f"docker provides Python {installed}, but pip.python={version}")
            ctx.run([uv, "python", "find", version])
        else:
            # on the host, uv is free to download/install the requested version itself.
            ctx.run([uv, "python", "install", version])

    def _ensure_venv(self, ctx, uv, venv_dir, version, checksum_files, command_outputs):
        """Create the venv if missing, or recreate it if forced or its requirements changed since last run.

        Several pip stages may share one venv_dir (see module docstring) --
        only the first stage (per denver run) to reach here for a given
        venv_dir gets to decide whether to recreate it; a later stage
        sharing the same venv_dir this run just installs on top of it,
        never wiping what the first stage already built this run.

        A checksum of every requirements/overrides file, plus every
        'install-args:' command's captured output, is stored alongside the
        venv on each successful install (_store_checksums, itself
        stage-namespaced so co-located stages don't clobber each other's);
        comparing it here is what lets an unchanged env skip both the
        recreate and the (often slow) reinstall on every single run.
        """
        ensured = getattr(ctx, "_pip_venvs_ensured_this_run", None)
        if ensured is None:
            ensured = ctx._pip_venvs_ensured_this_run = set()
        if venv_dir in ensured:
            return
        ensured.add(venv_dir)

        recreate = ctx.force
        checksum_path = venv_dir / f"{self.stage}-checksums.txt"
        previous = checksum_path.read_text() if checksum_path.is_file() else ""
        current = self._requirements_checksum(checksum_files, command_outputs)

        if not previous:
            recreate = True  # first run (or never completed): be safe
        elif previous != current:
            info("pip: requirement checksums changed; recreating venv")
            recreate = True

        if recreate and venv_dir.exists():
            info(f"pip: removing {venv_dir}")
            shutil.rmtree(venv_dir, ignore_errors=True)

        if not venv_dir.exists():
            ctx.run([uv, "venv", "-p", version, str(venv_dir)])

    def _activate(self, ctx, venv_dir):
        """Activate the venv purely via env vars (equivalent to `activate`)."""
        ctx.set("VIRTUAL_ENV", venv_dir)
        ctx.prepend_path(venv_dir / "bin")
        ctx.env.pop("PYTHONHOME", None)

    def _install(self, ctx, uv, cfg, requirements, overrides, install_args):
        """Run `uv pip install` with every -r/--override/--find-links/--no-index/install-args flag the config implies.

        Under 'append-mode:' (default on), the args actually run are every
        arg any *previous* run of this stage ever resolved, with only this
        run's new ones appended -- see _merge_install_args.
        """
        skip_if = cfg["skip-if"]
        if not ctx.force and skip_if and self._skip_if_satisfied(ctx, skip_if):
            info("pip: skip-if scripts all exited 0; skipping install")
            return

        # build this run's own uv pip install args, in the order uv expects:
        # overrides, then find-links, then --no-index, then -r's, then every
        # 'install-args:' entry last.
        args = []
        for override in overrides:
            args += ["--override", str(override)]
        for link in cfg.get("find-links") or []:
            args += ["--find-links", str(ctx.resolve_path(link))]
        if cfg["no-index"]:
            info("pip: using --no-index (offline install)")
            args += ["--no-index"]
        for req in requirements:
            args += ["-r", str(req)]
        args += install_args

        args = self._merge_install_args(ctx, args, append_mode=cfg["append-mode"])

        # mute uv's hardlink warning across filesystems
        ctx.run([uv, "pip", "install", *args], extra_env={"UV_LINK_MODE": cfg["link-mode"]})

    def _install_args_path(self, ctx):
        """Where this stage's accumulated install args (see 'append-mode:') are stored.

        Inside ctx.logs_dir, not the venv itself, so it survives a
        checksum-triggered venv recreation (_ensure_venv) instead of being
        wiped along with it.
        """
        return ctx.logs_dir / f"{self.stage}-install-args.json"

    def _group_args(self, args):
        """Group a flat uv-pip-install argv into atomic (flag, value) or (token,) units, preserving order.

        A value-taking flag (see _VALUE_FLAGS) stays paired with its value as
        one unit -- so accumulating/deduplicating units later never splits a
        flag from the value it needs.
        """
        units = []
        i = 0
        while i < len(args):
            token = args[i]
            if token in _VALUE_FLAGS and i + 1 < len(args):
                units.append((token, args[i + 1]))
                i += 2
            else:
                units.append((token,))
                i += 1
        return units

    def _merge_install_args(self, ctx, args, *, append_mode):
        """Union this run's args onto every arg a previous run of this stage ever resolved (if 'append-mode:').

        Comparing/deduplicating whole (flag, value) units (see _group_args)
        -- not raw tokens -- so e.g. two different '--override' entries both
        survive instead of the second losing its '--override' prefix. Stored
        units come first (unchanged order), only genuinely new units are
        appended; the merged (or, with append-mode off, just this run's own)
        units are written back so the *next* run sees them too.
        """
        fresh_units = self._group_args(args)
        path = self._install_args_path(ctx)

        merged_units = list(fresh_units)
        if append_mode:
            stored_units = [tuple(u) for u in json.loads(path.read_text())] if path.is_file() else []
            seen = set(stored_units)
            merged_units = list(stored_units)
            for unit in fresh_units:
                if unit not in seen:
                    merged_units.append(unit)
                    seen.add(unit)

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([list(u) for u in merged_units]))
        return [token for unit in merged_units for token in unit]

    def _skip_if_satisfied(self, ctx, scripts):
        """True if every 'skip-if' script exits 0 (install is skipped).

        Unlike the *value* of 'skip-if' (resolved centrally), actually
        running each script is a real side effect and must stay here, at
        install time -- checking whether a script's missing (a config
        error, not something the central resolver should silently paper
        over) belongs right before we'd run it too.
        """
        for script in scripts:
            path = ctx.resolve_path(script)
            if not path.is_file():
                die(f"pip: skip-if script not found: {path}")
            result = ctx.run([path], check=False, capture=True, echo=False)
            if result.returncode != 0:
                return False
        return True

    def _apply_patches(self, ctx, cfg):
        """Run venv-patcher against the resolved patches file, if both a patches file and the tool exist."""
        vp_cfg = cfg.get("venv-patcher") or {}
        patches = vp_cfg.get("patches")
        if not patches:
            return
        patcher = vp_cfg.get("exe")
        if not patcher:
            info("pip: venv-patcher not installed; skipping venv patches")
            return
        ctx.run([patcher, "apply", "-f", patches])

    def _store_checksums(self, venv_dir, checksum_files, command_outputs):
        """Record this stage's requirements checksum, so a later run can detect drift and reinstall.

        Namespaced by stage id (see 'venv:' sharing, module docstring), so a
        co-located stage sharing the same venv_dir never reads or clobbers
        another stage's own checksum file.
        """
        checksum_path = venv_dir / f"{self.stage}-checksums.txt"
        checksum_path.write_text(self._requirements_checksum(checksum_files, command_outputs))

    def _freeze(self, ctx, cfg, uv):
        """Write the venv's fully-resolved `uv pip freeze` output to 'freeze-to:', if configured."""
        freeze_to = cfg.get("freeze-to")
        if not freeze_to:
            return
        target = ctx.resolve_path(freeze_to)
        frozen = ctx.run([uv, "pip", "freeze"], capture=True, echo=False).stdout
        header = (
            "# This file is auto-generated by the pip provider!\n"
            "# To update versions: edit the source requirements and re-run with --force.\n"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(header + frozen)
