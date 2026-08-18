"""uv provider: a generic Python virtualenv managed with uv.

Creates/activates the venv, installs requirements, applies venv patches.
Configured from denver.yml -> ``uv:``.

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
_VALUE_FLAGS = ("-r", "--override", "--find-links")

# the only keys a 'lock:' section understands (see doc/providers/uv.md); unlike a
# stage's top-level keys (checked centrally against KEYS by denver.py) a typo
# in here would otherwise be silently ignored.
_LOCK_KEYS = ("create", "sync")


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
    """A generic Python virtualenv managed with uv -- see doc/providers/uv.md for denver.yml keys."""

    name = "uv"
    KEYS = (
        "python",
        "uv",
        "no-index",
        "link-mode",
        "skip-if-0",
        "skip-if-1",
        "venv-patcher",
        "requirements",
        "lock",
        "install-args",
        "overrides",
        "find-links",
        "venv",
        "freeze-to",
        "append-mode",
    )

    @staticmethod
    def _resolved_no_index(cfg):
        """'no-index:' as configured (bool, or the literal 'auto') -- resolved lazily in '_index_args'."""
        no_index = cfg.get("no-index", False)
        return no_index if no_index == "auto" else bool(no_index)

    @staticmethod
    def _resolved_skip_if(ctx, cfg, key):
        """One 'skip-if-N:' list of scripts, resolved to absolute paths (empty when unset)."""
        return [str(ctx.resolve_path(s)) for s in cfg.get(key) or []]

    @classmethod
    def _resolve_optional_sections(cls, ctx, cfg, resolved):
        """Fill 'lock:'/'venv-patcher:' into ``resolved`` only when the env configured them at all."""
        lock_cfg = cls._resolve_lock_defaults(ctx, cfg)
        if lock_cfg is not None:
            resolved["lock"] = lock_cfg

        vp_cfg = cls._resolve_venv_patcher_defaults(ctx, cfg)
        if vp_cfg is not None:
            resolved["venv-patcher"] = vp_cfg

    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):  # noqa: ARG003  # shared (ctx, cfg, config) signature
        """Resolve python/uv/no-index/skip-if-0/skip-if-1/venv-patcher defaults -- see doc/providers/uv.md."""
        resolved = dict(cfg)
        # No default: denver does not pick an interpreter nobody wrote down.
        # Unset means uv's own discovery decides (see _ensure_venv), and
        # --show-config shows it as null rather than as a version that looks
        # like configuration.
        resolved["python"] = str(cfg["python"]) if cfg.get("python") else None
        # dry_fallback: under --dry-run an unavailable uv still renders every
        # command below it, instead of aborting the preview (see Context.which).
        resolved["uv"] = cfg.get("uv") or ctx.which("uv", dry_fallback=True)
        resolved["no-index"] = cls._resolved_no_index(cfg)
        resolved["link-mode"] = cfg.get("link-mode", "copy")
        resolved["append-mode"] = cfg.get("append-mode", False)

        # resolved centrally (like every other path in this section) so
        # --show-config shows the real script; whether it actually *exists*
        # is checked at run time, right before we'd run it (_skip_if_satisfied).
        resolved["skip-if-0"] = cls._resolved_skip_if(ctx, cfg, "skip-if-0")
        resolved["skip-if-1"] = cls._resolved_skip_if(ctx, cfg, "skip-if-1")

        cls._resolve_optional_sections(ctx, cfg, resolved)

        return fill_unset(resolved, cls.KEYS)

    @staticmethod
    def _resolved_lock_path(ctx, key, value):
        """One 'lock:' entry resolved to an absolute uv.lock path -- None when that key is unset."""
        if not value:
            return None
        path = ctx.resolve_path(value)
        # uv only ever reads/writes '<project>/uv.lock', so a path
        # naming anything else could never be the file uv acts on --
        # say so here rather than silently acting on a different one.
        if path.name != "uv.lock":
            die(f"uv: 'lock: {key}:' must name a 'uv.lock' file, got: {path}")
        return str(path)

    @classmethod
    def _resolve_lock_defaults(cls, ctx, cfg):
        """Validate and resolve 'lock:'s paths, if set -- None otherwise (see doc/providers/uv.md)."""
        if not cfg.get("lock"):
            return None
        lock_cfg = dict(cfg["lock"])
        unknown = sorted(set(lock_cfg) - set(_LOCK_KEYS))
        if unknown:
            die(f"uv: unknown 'lock:' key(s) {', '.join(unknown)} -- known: {', '.join(_LOCK_KEYS)}")
        for key in _LOCK_KEYS:
            lock_cfg[key] = cls._resolved_lock_path(ctx, key, lock_cfg.get(key))
        return lock_cfg

    @classmethod
    def _resolve_venv_patcher_defaults(cls, ctx, cfg):
        """Validate and resolve 'venv-patcher:'s patches file/exe, if set -- None otherwise."""
        if not cfg.get("venv-patcher"):
            return None
        vp_cfg = dict(cfg["venv-patcher"])
        patches = vp_cfg.get("patches")
        if not patches:
            die("uv: 'venv-patcher:' needs an explicit 'patches:' file")
        patches_path = ctx.resolve_path(patches)
        if not patches_path.is_file():
            die(f"uv: venv-patcher patches file not found: {patches_path}")
        vp_cfg["patches"] = str(patches_path)
        vp_cfg["exe"] = vp_cfg.get("exe") or ctx.which("venv-patcher")
        return vp_cfg

    @staticmethod
    def _resolved_paths(ctx, cfg, key):
        """``cfg[key]``'s entries resolved to paths (an empty list when the key is unset)."""
        return [ctx.resolve_path(p) for p in (cfg.get(key) or [])]

    def _install_inputs(self, ctx, cfg):
        """Everything this stage installs from, as one dict: requirements/overrides/install-args/lock/checksums."""
        requirements = self._resolved_paths(ctx, cfg, "requirements")
        overrides = self._resolved_paths(ctx, cfg, "overrides")
        install_args, command_outputs = self._resolve_install_args(ctx, cfg)
        lock_cfg = cfg.get("lock") or {}
        lock_sync = lock_cfg.get("sync")
        return {
            "requirements": requirements,
            "overrides": overrides,
            "install_args": install_args,
            "command_outputs": command_outputs,
            "lock_create": lock_cfg.get("create"),
            "lock_sync": lock_sync,
            # 'lock: sync:'s lockfile is an install *input*, like a
            # requirements file, so drift in it recreates the venv the same
            # way; 'lock: create:'s is an output this run writes itself (like
            # 'freeze-to:'), and would otherwise invalidate its own checksum
            # every time.
            "checksum_files": requirements + overrides + ([Path(lock_sync)] if lock_sync else []),
        }

    @staticmethod
    def _installs_anything(inputs):
        """Whether this stage installs anything at all, or only creates the venv."""
        return bool(inputs["requirements"] or inputs["install_args"] or inputs["lock_create"] or inputs["lock_sync"])

    def setup(self, ctx):
        """Create/activate the venv, install requirements (unless --fast), and apply venv patches."""
        cfg = self.config_section(ctx)
        # each uv stage may target its own venv (config 'venv:'); default venv
        # otherwise. This is what lets an env have several uv stages.
        venv_dir = ctx.venv_dir_for(cfg.get("venv"))

        if ctx.fast:
            self._setup_fast(ctx, venv_dir)
            return

        if self._should_skip_before_venv(ctx, cfg, venv_dir):
            return

        banner(ctx, self.stage, "install")

        python_version = cfg["python"]
        inputs = self._install_inputs(ctx, cfg)
        installs_anything = self._installs_anything(inputs)
        if not installs_anything:
            info(f"uv[{self.stage}]: no requirements configured; only creating the venv")

        uv = cfg.get("uv")
        if not uv:
            die(f"uv[{self.stage}]: needs 'uv' on PATH (see https://docs.astral.sh/uv/)")

        self._ensure_python(ctx, uv, python_version)
        self._ensure_venv(ctx, uv, venv_dir, python_version, inputs["checksum_files"], inputs["command_outputs"])
        self._activate(ctx, venv_dir)

        if installs_anything:
            self._install_and_patch(ctx, uv, cfg, venv_dir, inputs)

    # ------------------------------------------------------------------ #
    def _setup_fast(self, ctx, venv_dir):
        """Handle '--fast': activate the existing venv verbatim, without touching requirements at all."""
        banner(ctx, self.stage, "install (skipped by --fast)")
        if not venv_dir.is_dir():
            die(f"uv[{self.stage}]: --fast needs an existing venv at {venv_dir} -- run once without --fast first")
        banner(ctx, self.stage, "activate")
        self._activate(ctx, venv_dir)

    def _install_everything(self, ctx, uv, cfg, inputs):
        """Run the lock/sync/`uv pip install` commands this stage's own inputs call for."""
        self._lock(ctx, uv, cfg, inputs["lock_create"])
        self._sync(ctx, uv, cfg, inputs["lock_sync"])
        if inputs["requirements"] or inputs["install_args"]:
            self._install(ctx, uv, cfg, inputs["requirements"], inputs["overrides"], inputs["install_args"])

    def _should_skip_before_venv(self, ctx, cfg, venv_dir):
        """True if there's no venv yet and skip-if says this stage is pointless right now.

        (e.g. already inside the container a standalone system venv is for):
        skip the stage outright, same as 'disabled: true', rather than
        creating and *activating* an empty venv nobody will fill in. Once a
        venv exists, skip-if instead only ever skips the install substep
        below, in _install_and_patch -- an already-populated venv still gets
        activated, since later stages depend on that.
        """
        return not venv_dir.is_dir() and not ctx.force and self._skip_if_stage(ctx, cfg)

    def _skip_if_stage(self, ctx, cfg):
        """True if skip-if-0/skip-if-1 says this stage (not just the install) should be a no-op.

        Only consulted from setup() before a venv exists (see setup()) --
        the same scripts/config as _install_and_patch's own check, just
        asked one step earlier, before _ensure_venv/_activate ever run.
        """
        skip_if_0 = cfg["skip-if-0"]
        skip_if_1 = cfg["skip-if-1"]
        if skip_if_0 and self._skip_if_satisfied(ctx, "skip-if-0", skip_if_0, 0):
            info(f"uv[{self.stage}]: no venv yet and skip-if-0 satisfied; skipping stage entirely")
            return True
        if skip_if_1 and self._skip_if_satisfied(ctx, "skip-if-1", skip_if_1, 1):
            info(f"uv[{self.stage}]: no venv yet and skip-if-1 satisfied; skipping stage entirely")
            return True
        return False

    def _install_and_patch(self, ctx, uv, cfg, venv_dir, inputs):
        """Install requirements/lockfile (unless skip-if-0/skip-if-1 says otherwise), apply venv patches, record checksums."""
        skip_if_0 = cfg["skip-if-0"]
        skip_if_1 = cfg["skip-if-1"]
        if not ctx.force and skip_if_0 and self._skip_if_satisfied(ctx, "skip-if-0", skip_if_0, 0):
            info("uv: skip-if-0 scripts all exited 0; skipping install")
        elif not ctx.force and skip_if_1 and self._skip_if_satisfied(ctx, "skip-if-1", skip_if_1, 1):
            info("uv: skip-if-1 scripts all exited 1; skipping install")
        else:
            self._install_everything(ctx, uv, cfg, inputs)
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
        checksummable *files* (e.g. only 'lock: create:', or only literal
        'install-args:') legitimately stores an empty checksum, and must
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
        """The --find-links/--no-index args every uv command that resolves packages gets.

        Shared by `uv pip install`, `uv lock` and `uv sync` so all three see
        the same wheel sources -- an offline (no-index) env must stay offline
        whichever of them does the resolving.
        """
        args = []
        for link in cfg.get("find-links") or []:
            args += ["--find-links", str(ctx.resolve_path(link))]
        no_index = cfg["no-index"]
        if no_index == "auto":
            no_index = ctx.in_container
        if no_index:
            info("uv: using --no-index (offline install)")
            args += ["--no-index"]
        return args

    def _project_dir(self, lockfile, key):
        """The uv project a 'lock:' lockfile belongs to: the directory holding it (and its pyproject.toml).

        Checked here, right before the uv command that needs it runs, rather
        than centrally in resolve_defaults: 'lock: create:'s own directory
        may legitimately only be filled in by an earlier stage of this run.
        """
        project = Path(lockfile).parent
        if not (project / "pyproject.toml").is_file():
            die(f"uv: no pyproject.toml beside 'lock: {key}:' ({lockfile}) -- a uv.lock belongs to its project")
        return project

    def _lock(self, ctx, uv, cfg, lock_create):
        """Write/update 'lock: create:'s lockfile with `uv lock` (a no-op when unset)."""
        if not lock_create:
            return
        project = self._project_dir(lock_create, "create")
        ctx.run([uv, "lock", "--project", str(project), *self._index_args(ctx, cfg)])

    def _sync(self, ctx, uv, cfg, lock_sync):
        """Install 'lock: sync:'s lockfile into this stage's venv with `uv sync` (a no-op when unset)."""
        if not lock_sync:
            return
        project = self._project_dir(lock_sync, "sync")
        if not Path(lock_sync).is_file():
            # under --dry-run a 'lock: create:' above only printed its `uv
            # lock`, so its output legitimately isn't there -- show the sync
            # that would follow it rather than stopping the preview here.
            if not ctx.dry_run:
                die(f"uv: 'lock: sync:' file not found: {lock_sync} -- set 'lock: create:' to generate it first")
            info(f"uv: 'lock: sync:' file {lock_sync} does not exist (yet)")
        # --active:  sync into the venv this stage just activated, not the
        #            project's own .venv;
        # --frozen:  install the lockfile exactly as it is, never silently
        #            re-resolving (and rewriting) it here -- that is what
        #            'lock: create:' is for;
        # --inexact: leave whatever else lives in the venv (this stage's own
        #            'requirements:', or another stage sharing this venv)
        #            alone, instead of pruning everything the lockfile
        #            doesn't mention.
        args = ["--project", str(project), "--active", "--frozen", "--inexact"]
        ctx.run([uv, "sync", *args, *self._index_args(ctx, cfg)], extra_env={"UV_LINK_MODE": cfg["link-mode"]})

    def _install(self, ctx, uv, cfg, requirements, overrides, install_args):
        """Run `uv pip install` with every -r/--override/--find-links/--no-index/install-args flag the config implies.

        Under 'append-mode:' (default on), the args actually run are every
        arg any *previous* run of this stage ever resolved, with only this
        run's new ones appended -- see _merge_install_args.
        """
        # build this run's own uv pip install args, in the order uv expects:
        # overrides, then find-links, then --no-index, then -r's, then every
        # 'install-args:' entry last.
        args = []
        for override in overrides:
            args += ["--override", str(override)]
        args += self._index_args(ctx, cfg)
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

        merged_units = fresh_units
        if append_mode:
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

    def _skip_if_satisfied(self, ctx, label, scripts, expected_code):
        """True if every ``label`` script exits with ``expected_code`` (install is skipped).

        Unlike the *value* of 'skip-if-0:'/'skip-if-1:' (resolved centrally),
        actually running each script is a real side effect and must stay
        here, at install time -- checking whether a script's missing (a
        config error, not something the central resolver should silently
        paper over) belongs right before we'd run it too.
        """
        for script in scripts:
            path = ctx.resolve_path(script)
            if not path.is_file():
                die(f"uv: {label} script not found: {path}")
            result = ctx.run([path], check=False, capture=False, query=True, echo=False)
            if result.returncode != expected_code:
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
            info("uv: venv-patcher not installed; skipping venv patches")
            return
        ctx.run([patcher, "apply", "-f", patches])

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
