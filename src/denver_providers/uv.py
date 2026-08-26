"""uv provider: a generic Python virtualenv managed with uv.

Creates/activates the venv, installs requirements, applies venv patches.
Configured from denver.toml -> ``uv:``.

Full key reference, worked examples and design notes: ``doc/providers/uv.md``.
"""

import hashlib
import json
from pathlib import Path

from .base import Provider, fill_unset
from .context import banner, die, info, sha256_of_files

# uv pip install flags that take exactly one following value -- used to keep
# a flag and its value together as one atomic unit when accumulating args
# across runs (see UvProvider._group_args); anything else (a bare flag like
# --no-index, or a literal install-args token) is its own one-token unit.
_VALUE_FLAGS = ("-r", "--override")


def _pyvenv_cfg(venv_dir):
    """Parse ``<venv>/pyvenv.cfg`` into a dict, or {} when there isn't one.

    Written by every venv creator (uv's own, and the stdlib's), and the only
    record of which interpreter a venv was built on that can be read without
    executing anything inside it.
    """
    path = Path(venv_dir) / "pyvenv.cfg"
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    return values


def _venv_python_version(venv_dir):
    """The Python version an existing venv was built on, or None if that can't be read.

    ``version_info`` is what uv writes; ``version`` is the stdlib's spelling
    of the same thing.
    """
    cfg = _pyvenv_cfg(venv_dir)
    return cfg.get("version_info") or cfg.get("version")


def _venv_base_interpreter_missing(venv_dir):
    """True when a venv exists but the interpreter it was built on no longer does.

    Only ever True for a venv that really is there and really does name a
    ``home``: an absent pyvenv.cfg means there's no venv to judge, which is
    the "create it" case rather than the "it's broken" one.
    """
    home = _pyvenv_cfg(venv_dir).get("home")
    return bool(home) and not Path(home).is_dir()


def _is_release_number(value):
    """True if ``value`` is a plain dotted release number (``3.12``, ``3.12.3``).

    'python:' is passed to uv, which also accepts forms denver cannot compare
    against a venv's recorded version without re-implementing uv's own
    resolution (``cpython@3.12``, ``>=3.11``, a path to an interpreter). Those
    are left alone rather than guessed at.
    """
    parts = str(value).split(".")
    return all(part.isdigit() for part in parts)


def _release_matches(wanted, actual):
    """True if ``actual`` (a full version) satisfies ``wanted``, compared component-wise.

    A prefix, so ``3.12`` accepts 3.12.7 exactly as uv itself would, while
    ``3.12.3`` does not accept 3.12.4.
    """
    wanted_parts = str(wanted).split(".")
    return str(actual).split(".")[: len(wanted_parts)] == wanted_parts


class UvProvider(Provider):
    """A generic Python virtualenv managed with uv -- see doc/providers/uv.md for denver.toml keys."""

    name = "uv"
    KEYS = (
        "exe",
        "python",
        "no-index",
        "patches-apply",
        "requirements",
        "lockfile",
        "install-args",
        "overrides",
        "venv",
        "freeze-to",
        "amend",
    )

    @staticmethod
    def _resolved_no_index(cfg):
        """'no-index:' as configured (bool, or the literal 'auto') -- resolved lazily in '_index_args'."""
        no_index = cfg.get("no-index", False)
        return no_index if no_index == "auto" else bool(no_index)

    @staticmethod
    def _resolved_patches_apply(ctx, cfg):
        """'patches-apply:' with any relative-path-looking token resolved (see ctx.resolve_command) -- None if unset."""
        patches_apply = cfg.get("patches-apply")
        return ctx.resolve_command(patches_apply) if patches_apply else None

    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):  # noqa: ARG003  # shared (ctx, cfg, config) signature
        """Resolve python/exe/no-index/lockfile/patches-apply defaults -- see doc/providers/uv.md."""
        resolved = dict(cfg)
        # No default: denver does not pick an interpreter nobody wrote down.
        # Unset means uv's own discovery decides (see _ensure_venv), and
        # --show-config shows it as null rather than as a version that looks
        # like configuration.
        resolved["python"] = str(cfg["python"]) if cfg.get("python") else None
        # bare name, not a resolved path -- existence is checked in setup()
        # (see there), same as the docker provider's 'exe:' default.
        resolved["exe"] = cfg.get("exe") or "uv"
        resolved["no-index"] = cls._resolved_no_index(cfg)
        resolved["amend"] = cfg.get("amend", True)
        resolved["lockfile"] = cls._resolved_lock_path(ctx, "lockfile", cfg.get("lockfile"))
        resolved["patches-apply"] = cls._resolved_patches_apply(ctx, cfg)

        return fill_unset(resolved, cls.KEYS)

    @staticmethod
    def _resolved_lock_path(ctx, key, value):
        """'lockfile:' resolved to an absolute uv.lock path -- None when it's unset."""
        if not value:
            return None
        path = ctx.resolve_path(value)
        # uv only ever reads/writes '<project>/uv.lock', so a path
        # naming anything else could never be the file uv acts on --
        # say so here rather than silently acting on a different one.
        if path.name != "uv.lock":
            die(f"uv: '{key}:' must name a 'uv.lock' file, got: {path}")
        return str(path)

    @staticmethod
    def _resolved_paths(ctx, cfg, key):
        """``cfg[key]``'s entries resolved to paths (an empty list when the key is unset)."""
        return [ctx.resolve_path(p) for p in (cfg.get(key) or [])]

    def _install_inputs(self, ctx, cfg):
        """Everything this stage installs from, as one dict: requirements/overrides/install-args/lockfile/checksums."""
        requirements = self._resolved_paths(ctx, cfg, "requirements")
        overrides = self._resolved_paths(ctx, cfg, "overrides")
        install_args, command_outputs = self._resolve_install_args(ctx, cfg)
        lockfile = cfg.get("lockfile")
        return {
            "requirements": requirements,
            "overrides": overrides,
            "install_args": install_args,
            "command_outputs": command_outputs,
            "lockfile": lockfile,
            # 'lockfile:' is an install *input*, like a requirements file, so
            # drift in it recreates the venv the same way.
            "checksum_files": requirements + overrides + ([Path(lockfile)] if lockfile else []),
        }

    @staticmethod
    def _installs_anything(inputs):
        """Whether this stage installs anything at all, or only creates the venv."""
        return bool(inputs["requirements"] or inputs["install_args"] or inputs["lockfile"])

    def setup(self, ctx):
        """Create/activate the venv, install requirements (unless --fast), and apply venv patches.

        Whether this whole stage is a no-op this run (the generic
        'skip-on-success:'/'skip-on-failure:' keys) is decided one layer up,
        before setup() is ever called -- see denver.py's _stage_skip_reason.
        """
        cfg = self.config_section(ctx)
        # each uv stage may target its own venv (config 'venv:'); default venv
        # otherwise. This is what lets an env have several uv stages.
        venv_dir = ctx.venv_dir_for(cfg.get("venv"))

        if ctx.fast:
            self._setup_fast(ctx, venv_dir)
            return

        banner(ctx, self.stage, "install")

        python_version = cfg["python"]
        inputs = self._install_inputs(ctx, cfg)
        installs_anything = self._installs_anything(inputs)
        if not installs_anything:
            info(f"uv[{self.stage}]: no requirements configured; only creating the venv")

        exe = cfg.get("exe")
        # dry_fallback: under --dry-run an unavailable uv still renders every
        # command below it, instead of aborting the preview (see Context.which).
        if not ctx.which(exe, dry_fallback=True):
            die(f"uv[{self.stage}]: needs 'exe' on PATH (see https://docs.astral.sh/uv/)")

        self._ensure_python(ctx, exe, python_version)
        self._ensure_venv(ctx, exe, venv_dir, python_version, inputs["checksum_files"], inputs["command_outputs"])
        self._activate(ctx, venv_dir)

        if installs_anything:
            self._install_and_patch(ctx, exe, cfg, venv_dir, inputs)

    # ------------------------------------------------------------------ #
    def _setup_fast(self, ctx, venv_dir):
        """Handle '--fast': activate the existing venv verbatim, without touching requirements at all."""
        banner(ctx, self.stage, "install (skipped by --fast)")
        if not venv_dir.is_dir():
            die(f"uv[{self.stage}]: --fast needs an existing venv at {venv_dir} -- run once without --fast first")
        banner(ctx, self.stage, "activate")
        self._activate(ctx, venv_dir)

    def _install_everything(self, ctx, uv, cfg, venv_dir, inputs):
        """Run the `uv sync`/`uv pip install` commands this stage's own inputs call for."""
        self._sync(ctx, uv, cfg, inputs["lockfile"])
        if inputs["requirements"] or inputs["install_args"]:
            self._install(ctx, uv, cfg, venv_dir, inputs["requirements"], inputs["overrides"], inputs["install_args"])

    def _install_and_patch(self, ctx, uv, cfg, venv_dir, inputs):
        """Install requirements/lockfile, apply venv patches, record checksums."""
        self._install_everything(ctx, uv, cfg, venv_dir, inputs)
        self._apply_patches(ctx, cfg)
        self._store_checksums(ctx, venv_dir, inputs["checksum_files"], inputs["command_outputs"])
        self._freeze(ctx, cfg, uv)

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
            entry_args, output = self._expand_install_arg(ctx, entry)
            args += entry_args
            if output is not None:
                command_outputs.append(output)
        return args, command_outputs

    @staticmethod
    def _expand_install_arg(ctx, entry):
        """One 'install-args:' entry as ``(args, output)``: a '$(cmd)' entry is run and split, anything else is literal."""
        if not (entry.startswith("$(") and entry.endswith(")")):
            return [entry], None
        output = ctx.run(["bash", "-c", entry[2:-1]], capture=True, echo=False).stdout
        return output.split(), output

    def _requirements_checksum(self, ctx, files, command_outputs):
        """sha256_of_files(files), plus each 'install-args:' command's captured output hashed in too.

        Both must feed the same checksum (compared by _ensure_venv, stored by
        _store_checksums) so drift in either a requirements *file* or a
        command's *output* is detected the same way.

        Files are named relative to the env dir, so the same requirements at
        a different absolute path (a second checkout, a renamed directory, a
        git worktree) are not mistaken for changed ones -- see
        context.fingerprint_label.
        """
        blob = sha256_of_files(files, base=ctx.env_dir)
        for i, output in enumerate(command_outputs):
            blob += f"\n{hashlib.sha256(output.encode()).hexdigest()}  <install-args command #{i}>"
        return blob

    def _ensure_python(self, ctx, uv, version):
        """Make interpreter ``version`` available to uv: verified offline in a container, installed otherwise.

        A no-op when no ``python:`` is configured: uv's own discovery then
        decides which interpreter the venv is built on, and there is nothing
        for denver to install or assert.
        """
        if not version:
            return
        if ctx.in_container:
            self._find_container_python(ctx, uv, version)
            return
        # on the host, uv is free to download/install the requested version itself.
        ctx.run([uv, "python", "install", version])

    @staticmethod
    def _find_container_python(ctx, uv, version):
        """Assert the container's baked-in interpreter is ``version``, then let uv find (not install) it.

        In a container the interpreter is fixed by the image; denver can't
        install a different one, so a mismatch is an error rather than
        something to resolve.
        """
        result = ctx.run(["python3", "--version"], capture=True, echo=False)
        installed = result.stdout.split()
        # no output at all only happens when that query couldn't run --
        # under --dry-run, where a failed query is reported, not fatal
        # (see Context.run); there is simply no version to compare then.
        if installed and installed[-1] != version:
            die(f"docker provides Python {installed[-1]}, but uv.python={version}")
        ctx.run([uv, "python", "find", version])

    def _ensure_venv(self, ctx, uv, venv_dir, version, checksum_files, command_outputs):
        """Create the venv if missing, or recreate it if forced or its requirements changed since last run.

        Several uv stages may share one venv_dir (see doc/providers/uv.md) --
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
        ensured = self._venvs_ensured_this_run(ctx)
        if venv_dir in ensured:
            # a later stage sharing this venv: it is whatever the first stage
            # left, so its own 'python:' is checked against that decision
            # rather than against the filesystem (under --dry-run nothing was
            # actually created, and the venv on disk may be the old one).
            self._check_python_matches(ctx, ensured[venv_dir], version, f"the venv this run built at {venv_dir}")
            return

        recreate = self._needs_recreate(ctx, venv_dir, checksum_files, command_outputs)

        # An existing venv's interpreter is authoritative: it is never
        # silently rebuilt just because 'python:' changed, because that would
        # also silently discard everything installed into it. Contradicting
        # it is an error the user resolves deliberately -- see
        # _check_python_matches.
        if not recreate:
            self._check_python_matches(ctx, _venv_python_version(venv_dir), version, f"the venv at {venv_dir}")
        ensured[venv_dir] = version

        self._create_venv(ctx, uv, venv_dir, version, recreate)

    @staticmethod
    def _venvs_ensured_this_run(ctx):
        """The ``{venv_dir: python version}`` map of venvs this run has already decided about."""
        ensured = getattr(ctx, "_uv_venvs_ensured_this_run", None)
        if ensured is None:
            ensured = ctx._uv_venvs_ensured_this_run = {}
        return ensured

    def _stored_checksum(self, venv_dir):
        """This stage's requirements checksum from its last completed install, or None if there is none.

        None (no file at all) rather than "": a stage whose install has no
        checksummable *files* (e.g. only literal 'install-args:')
        legitimately stores an empty checksum, and must
        still count as "seen before" instead of recreating its venv on
        every single run.
        """
        checksum_path = venv_dir / f"{self.stage}-checksums.txt"
        if not checksum_path.is_file():
            return None
        return checksum_path.read_text()

    def _needs_recreate(self, ctx, venv_dir, checksum_files, command_outputs):
        """Whether the venv has to be rebuilt from scratch: --force, drifted requirements, or a dead base."""
        if ctx.force:
            return True

        previous = self._stored_checksum(venv_dir)
        if previous is None:
            return True  # first run (or never completed): be safe
        if previous != self._requirements_checksum(ctx, checksum_files, command_outputs):
            info("uv: requirement checksums changed; recreating venv")
            return True

        # A venv whose base interpreter has gone (a distro upgrade moved
        # python3, a uv-managed interpreter pruned) is broken rather than
        # reusable, and there is no configured value it could contradict --
        # the one place recreating without being asked is right.
        if _venv_base_interpreter_missing(venv_dir):
            info(f"uv[{self.stage}]: {venv_dir}'s base interpreter is gone; recreating venv")
            return True
        return False

    @staticmethod
    def _venv_still_needed(ctx, venv_dir, recreate):
        """Whether `uv venv` still has to run after a (possible) removal.

        Under --dry-run the removal only *reported* itself, so an existing
        venv is still there -- ask whether it would have survived, not
        whether it is still on disk, or the `uv venv` that a real run would
        follow the removal with goes missing from the preview.
        """
        if ctx.dry_run and recreate:
            return True
        return not venv_dir.exists()

    def _create_venv(self, ctx, uv, venv_dir, version, recreate):
        """Remove the venv if it is being recreated, then create it unless one is (or would be) there."""
        if recreate and venv_dir.exists():
            info(f"uv: removing {venv_dir}")
            ctx.rmtree(venv_dir)
        # no '-p' without a configured 'python:': uv's own discovery
        # (UV_PYTHON, a .python-version file, then the system) decides,
        # rather than denver picking a version nobody wrote down.
        version_args = ["-p", version] if version else []
        if self._venv_still_needed(ctx, venv_dir, recreate):
            ctx.run([uv, "venv", *version_args, str(venv_dir)])

    def _check_python_matches(self, ctx, actual, wanted, where):
        """Die when a configured 'python:' contradicts the interpreter a venv already has.

        Silent whenever there is nothing to compare: no ``python:`` at all
        (uv decides), no readable version for the venv, or a ``python:`` that
        isn't a plain release number (uv also accepts ``cpython@3.12``, a
        path, ...) and so can't be compared without re-implementing uv's own
        resolution. ``--force`` is exempt: it recreates the venv, which *is*
        the resolution this would otherwise ask for.
        """
        if ctx.force or not wanted or not actual or not _is_release_number(wanted):
            return
        if _release_matches(wanted, actual):
            return
        die(
            f"uv[{self.stage}]: {where} is Python {actual}, but 'python: {wanted}' is configured. "
            f"Re-run with --force to recreate it at {wanted}, or give this stage its own 'venv:' "
            f"to keep both interpreters (one venv holds exactly one interpreter)."
        )

    def _activate(self, ctx, venv_dir):
        """Activate the venv purely via env vars (equivalent to `activate`)."""
        ctx.set("VIRTUAL_ENV", venv_dir)
        ctx.prepend_path(venv_dir / "bin")
        ctx.env.pop("PYTHONHOME", None)

    def _index_args(self, ctx, cfg):
        """The --no-index arg every uv command that resolves packages gets.

        Shared by `uv pip install` and `uv sync` so both see the same
        offline/online policy -- an offline (no-index) env must stay offline
        whichever of them does the resolving.
        """
        args = []
        no_index = cfg["no-index"]
        if no_index == "auto":
            no_index = ctx.in_container
        if no_index:
            info("uv: using --no-index (offline install)")
            args += ["--no-index"]
        return args

    def _project_dir(self, lockfile, key):
        """The uv project 'lockfile:' belongs to: the directory holding it (and its pyproject.toml).

        Checked here, right before the uv command that needs it runs, rather
        than centrally in resolve_defaults: an earlier stage of this run may
        legitimately be what fills this directory in.
        """
        project = Path(lockfile).parent
        if not (project / "pyproject.toml").is_file():
            die(f"uv: no pyproject.toml beside '{key}:' ({lockfile}) -- a uv.lock belongs to its project")
        return project

    def _sync(self, ctx, uv, cfg, lockfile):
        """Install 'lockfile:''s lockfile into this stage's venv with `uv sync` (a no-op when unset)."""
        if not lockfile:
            return
        project = self._project_dir(lockfile, "lockfile")
        if not Path(lockfile).is_file():
            if not ctx.dry_run:
                die(
                    f"uv: 'lockfile:' file not found: {lockfile} -- "
                    f"create it first (e.g. a 'custom' stage running `uv lock`)"
                )
            info(f"uv: 'lockfile:' file {lockfile} does not exist (yet)")
        # --active:  sync into the venv this stage just activated, not the
        #            project's own .venv;
        # --frozen:  install the lockfile exactly as it is, never silently
        #            re-resolving (and rewriting) it here;
        # --inexact: leave whatever else lives in the venv (this stage's own
        #            'requirements:', or another stage sharing this venv)
        #            alone, instead of pruning everything the lockfile
        #            doesn't mention.
        args = ["--project", str(project), "--active", "--frozen", "--inexact"]
        ctx.run([uv, "sync", *args, *self._index_args(ctx, cfg)])

    def _install(self, ctx, uv, cfg, venv_dir, requirements, overrides, install_args):
        """Run `uv pip install` with every -r/--override/--no-index/install-args flag the config implies.

        Under 'amend:' (default on), the args actually run are every
        arg any *previous* run of this stage ever resolved *against this same
        venv*, with only this run's new ones appended -- see _merge_install_args.
        """
        # build this run's own uv pip install args, in the order uv expects:
        # overrides, then --no-index, then -r's, then every 'install-args:'
        # entry last.
        args = []
        for override in overrides:
            args += ["--override", str(override)]
        args += self._index_args(ctx, cfg)
        for req in requirements:
            args += ["-r", str(req)]
        args += install_args

        args = self._merge_install_args(ctx, venv_dir, args, amend=cfg["amend"])

        ctx.run([uv, "pip", "install", *args])

    def _install_args_path(self, ctx, venv_dir):
        """Where this stage's accumulated install args (see 'amend:') are stored, for ``venv_dir``.

        Inside ctx.logs_dir, not the venv itself, so it survives a
        checksum-triggered venv recreation (_ensure_venv) instead of being
        wiped along with it. Named after ``venv_dir``'s own leaf too (not
        just the stage id), so pointing this stage at a *different* venv
        (a changed 'venv:') starts that venv off with no prior args to
        amend, rather than joining onto a previous venv's history.
        """
        return ctx.logs_dir / f"{self.stage}-{Path(venv_dir).name}-install-args.json"

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

    def _merge_install_args(self, ctx, venv_dir, args, *, amend):
        """Union this run's args onto every arg a previous run of this stage ever resolved against ``venv_dir`` (if 'amend:').

        Comparing/deduplicating whole (flag, value) units (see _group_args)
        -- not raw tokens -- so e.g. two different '--override' entries both
        survive instead of the second losing its '--override' prefix. Stored
        units come first (unchanged order), only genuinely new units are
        appended; the merged (or, with 'amend:' off explicitly, just this run's own)
        units are written back so the *next* run sees them too. Scoped to
        ``venv_dir`` (see _install_args_path), so a stage retargeted at a
        different venv never joins onto the previous venv's args.
        """
        fresh_units = self._group_args(args)
        path = self._install_args_path(ctx, venv_dir)

        merged_units = fresh_units
        if amend:
            merged_units = self._appended(self._stored_install_units(path), fresh_units)

        ctx.mkdir(path.parent)
        ctx.write_text(path, json.dumps([list(u) for u in merged_units]))
        return [token for unit in merged_units for token in unit]

    @staticmethod
    def _stored_install_units(path):
        """The (flag, value) units a previous run of this stage stored at ``path`` -- empty if it never ran."""
        if not path.is_file():
            return []
        return [tuple(u) for u in json.loads(path.read_text())]

    @staticmethod
    def _appended(stored_units, fresh_units):
        """``stored_units`` in their original order, with only the genuinely new ``fresh_units`` appended."""
        merged = list(stored_units)
        seen = set(stored_units)
        for unit in fresh_units:
            if unit not in seen:
                merged.append(unit)
                seen.add(unit)
        return merged

    def _apply_patches(self, ctx, cfg):
        """Run the literal 'patches-apply:' command, if one is configured (already resolved -- see resolve_defaults)."""
        patches_apply = cfg.get("patches-apply")
        if not patches_apply:
            return
        ctx.run(patches_apply)

    def _store_checksums(self, ctx, venv_dir, checksum_files, command_outputs):
        """Record this stage's requirements checksum, so a later run can detect drift and reinstall.

        Namespaced by stage id (see 'venv:' sharing, module docstring), so a
        co-located stage sharing the same venv_dir never reads or clobbers
        another stage's own checksum file.
        """
        checksum_path = venv_dir / f"{self.stage}-checksums.txt"
        ctx.write_text(checksum_path, self._requirements_checksum(ctx, checksum_files, command_outputs))

    def _freeze(self, ctx, cfg, uv):
        """Write the venv's fully-resolved `uv pip freeze` output to 'freeze-to:', if configured."""
        freeze_to = cfg.get("freeze-to")
        if not freeze_to:
            return
        target = ctx.resolve_path(freeze_to)
        frozen = ctx.run([uv, "pip", "freeze"], capture=True, echo=False).stdout
        header = (
            "# This file is auto-generated by the uv provider!\n"
            "# To update versions: edit the source requirements and re-run with --force.\n"
        )
        ctx.mkdir(target.parent)
        ctx.write_text(target, header + frozen)
