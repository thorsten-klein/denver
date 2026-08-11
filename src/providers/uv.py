"""uv provider: a generic Python virtualenv managed with uv.

Everything is configured from denver.yml -> ``uv:``:

    uv:
      python: "3.12.3"            # interpreter version for the venv (optional;
                                   # unset => no '-p', uv's own discovery decides)
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
      lock:                       # uv project lockfiles (optional)
        create: py/uv.lock        # `uv lock` writes/updates this lockfile
        sync: py/uv.lock          # `uv sync` installs this lockfile
      no-index: false             # true|false|auto (default false; 'auto' =>
                                   # true inside docker, false on the host)
      link-mode: copy             # uv link mode
      venv-patcher:                # applies venv patches (optional); when set,
        exe: venv-patcher          # executable (default: venv-patcher on PATH)
        patches: uv/venv-patcher/patches.yml   # 'patches:' is required
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

``lock:`` is the uv-project (pyproject.toml) side of the same venv, and is
independent of the requirements above -- either, both or neither may be
set. ``create:`` runs ``uv lock`` for the project owning that lockfile
(the directory the lockfile sits in, which must hold its pyproject.toml),
i.e. it *writes* the file, like 'freeze-to:' does. ``sync:`` runs ``uv
sync`` for the project owning that lockfile, installing it into the venv
this stage just activated (``--active``) without re-resolving it
(``--frozen``) and without pruning packages the lockfile doesn't mention
(``--inexact``, so a shared venv's other stages survive). With both set,
``create:`` runs first, so a single stage can relock and then install what
it locked. Only ``sync:``'s lockfile counts as an input for the checksum
that recreates the venv; ``create:``'s is an output.

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

One venv holds exactly one interpreter, and the one it already has wins: an
existing venv is reused rather than silently rebuilt because ``python:``
changed (that would discard everything installed into it too), and a
``python:`` contradicting it is an error naming both ways out -- ``--force``
to recreate it, or a ``venv:`` of this stage's own to keep both. The same
rule covers several stages sharing a venv, so a later stage disagreeing is
reported rather than silently ignored. A venv whose base interpreter has
disappeared is the one case denver recreates unasked: it is broken rather
than reusable. See ``doc/providers/uv.md``.

Several uv stages may share one venv (via an unset/identical 'venv:') --
e.g. so `west`'s own extension commands (imported into the *same* running
`west` process) can see packages a later stage installs on top of what an
earlier one built. Only the first such stage (in 'stages:' order) to touch a
given venv this run ever decides whether to recreate it; every later stage
sharing it only ever installs on top, never wipes what the first one built.

Every default above (python's version, uv/venv-patcher's executable,
no-index's auto-resolution, ...) is computed once, centrally, by
``UvProvider.resolve_defaults`` -- not in setup(). By the time this
provider's setup() runs, its config section already has every default
filled in (see ``denver.resolve_provider_defaults``), so nothing here ever
falls back to a PATH lookup itself.

``skip-if`` and ``venv-patcher`` are never guessed from the env's directory
layout: denver does not go looking for a ``uv/skip-if.sh``/``.py`` or a
``uv/venv-patcher/patches.yml`` that happens to exist. With no ``skip-if:``
there is no skip check, and the venv patcher runs only when a
``venv-patcher:`` section names its ``patches:`` file explicitly (an
unreadable or missing one is an error, not a silent no-op).

``uv`` itself must already be installed wherever this stage runs -- denver
never installs it: the host for a plain run, or the image when a ``docker``
stage relocated the pipeline first.

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

# the only keys a 'lock:' section understands (see module docstring); unlike a
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
    """A generic Python virtualenv managed with uv -- see module docstring for denver.yml keys."""

    name = "uv"
    KEYS = (
        "python",
        "uv",
        "no-index",
        "link-mode",
        "skip-if",
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

    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):  # noqa: ARG003  # shared (ctx, cfg, config) signature
        """Resolve python/uv/no-index/skip-if/venv-patcher defaults -- see module docstring."""
        resolved = dict(cfg)
        # No default: denver does not pick an interpreter nobody wrote down.
        # Unset means uv's own discovery decides (see _ensure_venv), and
        # --show-config shows it as null rather than as a version that looks
        # like configuration.
        resolved["python"] = str(cfg["python"]) if cfg.get("python") else None
        # dry_fallback: under --dry-run an unavailable uv still renders every
        # command below it, instead of aborting the preview (see Context.which).
        resolved["uv"] = cfg.get("uv") or ctx.which("uv", dry_fallback=True)
        no_index = cfg.get("no-index", False)
        resolved["no-index"] = ctx.in_container if no_index == "auto" else bool(no_index)
        resolved["link-mode"] = cfg.get("link-mode", "copy")
        resolved["append-mode"] = cfg.get("append-mode", False)

        # resolved centrally (like every other path in this section) so
        # --show-config shows the real script; whether it actually *exists*
        # is checked at run time, right before we'd run it (_skip_if_satisfied).
        resolved["skip-if"] = [str(ctx.resolve_path(s)) for s in cfg.get("skip-if") or []]

        lock_cfg = cls._resolve_lock_defaults(ctx, cfg)
        if lock_cfg is not None:
            resolved["lock"] = lock_cfg

        vp_cfg = cls._resolve_venv_patcher_defaults(ctx, cfg)
        if vp_cfg is not None:
            resolved["venv-patcher"] = vp_cfg

        return fill_unset(resolved, cls.KEYS)

    @classmethod
    def _resolve_lock_defaults(cls, ctx, cfg):
        """Validate and resolve 'lock:'s paths, if set -- None otherwise (see module docstring)."""
        if not cfg.get("lock"):
            return None
        lock_cfg = dict(cfg["lock"])
        unknown = sorted(set(lock_cfg) - set(_LOCK_KEYS))
        if unknown:
            die(f"uv: unknown 'lock:' key(s) {', '.join(unknown)} -- known: {', '.join(_LOCK_KEYS)}")
        for key in _LOCK_KEYS:
            value = lock_cfg.get(key)
            if not value:
                lock_cfg[key] = None
                continue
            path = ctx.resolve_path(value)
            # uv only ever reads/writes '<project>/uv.lock', so a path
            # naming anything else could never be the file uv acts on --
            # say so here rather than silently acting on a different one.
            if path.name != "uv.lock":
                die(f"uv: 'lock: {key}:' must name a 'uv.lock' file, got: {path}")
            lock_cfg[key] = str(path)
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

    def setup(self, ctx):
        """Create/activate the venv, install requirements (unless --fast), and apply venv patches."""
        cfg = self.config_section(ctx)
        # each uv stage may target its own venv (config 'venv:'); default venv
        # otherwise. This is what lets an env have several uv stages.
        venv_dir = ctx.venv_dir_for(cfg.get("venv"))

        if ctx.fast:
            self._setup_fast(ctx, venv_dir)
            return

        banner(ctx, self.stage, "install")

        python_version = cfg["python"]

        requirements = [ctx.resolve_path(r) for r in (cfg.get("requirements") or [])]
        overrides = [ctx.resolve_path(o) for o in (cfg.get("overrides") or [])]
        install_args, command_outputs = self._resolve_install_args(ctx, cfg)
        lock_cfg = cfg.get("lock") or {}
        lock_create, lock_sync = lock_cfg.get("create"), lock_cfg.get("sync")

        if not requirements and not install_args and not lock_create and not lock_sync:
            info(f"uv[{self.stage}]: no requirements configured; only creating the venv")

        uv = cfg.get("uv")
        if not uv:
            die(f"uv[{self.stage}]: needs 'uv' on PATH (see https://docs.astral.sh/uv/)")

        # 'lock: sync:'s lockfile is an install *input*, like a requirements
        # file, so drift in it recreates the venv the same way; 'lock:
        # create:'s is an output this run writes itself (like 'freeze-to:'),
        # and would otherwise invalidate its own checksum every time.
        checksum_files = requirements + overrides + ([Path(lock_sync)] if lock_sync else [])

        self._ensure_python(ctx, uv, python_version)
        self._ensure_venv(ctx, uv, venv_dir, python_version, checksum_files, command_outputs)
        self._activate(ctx, venv_dir)

        if requirements or install_args or lock_create or lock_sync:
            self._install_and_patch(
                ctx,
                uv,
                cfg,
                venv_dir,
                requirements,
                overrides,
                install_args,
                lock_create,
                lock_sync,
                checksum_files,
                command_outputs,
            )

    # ------------------------------------------------------------------ #
    def _setup_fast(self, ctx, venv_dir):
        """Handle '--fast': activate the existing venv verbatim, without touching requirements at all."""
        banner(ctx, self.stage, "install (skipped by --fast)")
        if not venv_dir.is_dir():
            die(f"uv[{self.stage}]: --fast needs an existing venv at {venv_dir} -- run once without --fast first")
        banner(ctx, self.stage, "activate")
        self._activate(ctx, venv_dir)

    def _install_and_patch(
        self,
        ctx,
        uv,
        cfg,
        venv_dir,
        requirements,
        overrides,
        install_args,
        lock_create,
        lock_sync,
        checksum_files,
        command_outputs,
    ):
        """Install requirements/lockfile (unless skip-if says otherwise), apply venv patches, record checksums."""
        skip_if = cfg["skip-if"]
        if not ctx.force and skip_if and self._skip_if_satisfied(ctx, skip_if):
            info("uv: skip-if scripts all exited 0; skipping install")
        else:
            self._lock(ctx, uv, cfg, lock_create)
            self._sync(ctx, uv, cfg, lock_sync)
            if requirements or install_args:
                self._install(ctx, uv, cfg, requirements, overrides, install_args)
        self._apply_patches(ctx, cfg)
        self._store_checksums(ctx, venv_dir, checksum_files, command_outputs)
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
            if entry.startswith("$(") and entry.endswith(")"):
                output = ctx.run(["bash", "-c", entry[2:-1]], capture=True, echo=False).stdout
                command_outputs.append(output)
                args += output.split()
            else:
                args.append(entry)
        return args, command_outputs

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
            # in a container the interpreter is fixed (baked into the image); we can't
            # install a different one, so just assert it matches what uv.python
            # asks for, then let uv find (not install) it.
            result = ctx.run(["python3", "--version"], capture=True, echo=False)
            installed = result.stdout.split()
            # no output at all only happens when that query couldn't run --
            # under --dry-run, where a failed query is reported, not fatal
            # (see Context.run); there is simply no version to compare then.
            if installed and installed[-1] != version:
                die(f"docker provides Python {installed[-1]}, but uv.python={version}")
            ctx.run([uv, "python", "find", version])
        else:
            # on the host, uv is free to download/install the requested version itself.
            ctx.run([uv, "python", "install", version])

    def _ensure_venv(self, ctx, uv, venv_dir, version, checksum_files, command_outputs):
        """Create the venv if missing, or recreate it if forced or its requirements changed since last run.

        Several uv stages may share one venv_dir (see module docstring) --
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
        ensured = getattr(ctx, "_uv_venvs_ensured_this_run", None)
        if ensured is None:
            ensured = ctx._uv_venvs_ensured_this_run = {}
        if venv_dir in ensured:
            # a later stage sharing this venv: it is whatever the first stage
            # left, so its own 'python:' is checked against that decision
            # rather than against the filesystem (under --dry-run nothing was
            # actually created, and the venv on disk may be the old one).
            self._check_python_matches(ctx, ensured[venv_dir], version, f"the venv this run built at {venv_dir}")
            return

        recreate = ctx.force
        checksum_path = venv_dir / f"{self.stage}-checksums.txt"
        # None (no file at all) rather than "": a stage whose install has no
        # checksummable *files* (e.g. only 'lock: create:', or only literal
        # 'install-args:') legitimately stores an empty checksum, and must
        # still count as "seen before" instead of recreating its venv on
        # every single run.
        previous = checksum_path.read_text() if checksum_path.is_file() else None
        current = self._requirements_checksum(ctx, checksum_files, command_outputs)

        if previous is None:
            recreate = True  # first run (or never completed): be safe
        elif previous != current:
            info("uv: requirement checksums changed; recreating venv")
            recreate = True

        # A venv whose base interpreter has gone (a distro upgrade moved
        # python3, a uv-managed interpreter pruned) is broken rather than
        # reusable, and there is no configured value it could contradict --
        # the one place recreating without being asked is right.
        if not recreate and _venv_base_interpreter_missing(venv_dir):
            info(f"uv[{self.stage}]: {venv_dir}'s base interpreter is gone; recreating venv")
            recreate = True

        # An existing venv's interpreter is authoritative: it is never
        # silently rebuilt just because 'python:' changed, because that would
        # also silently discard everything installed into it. Contradicting
        # it is an error the user resolves deliberately -- see
        # _check_python_matches.
        if not recreate:
            self._check_python_matches(ctx, _venv_python_version(venv_dir), version, f"the venv at {venv_dir}")
        ensured[venv_dir] = version

        if recreate and venv_dir.exists():
            info(f"uv: removing {venv_dir}")
            ctx.rmtree(venv_dir)

        # under --dry-run the removal above only *reported* itself, so an
        # existing venv is still there -- ask whether it would have survived,
        # not whether it is still on disk, or the `uv venv` that a real run
        # would follow the removal with would go missing from the preview.
        if not venv_dir.exists() or (ctx.dry_run and recreate):
            # no '-p' without a configured 'python:': uv's own discovery
            # (UV_PYTHON, a .python-version file, then the system) decides,
            # rather than denver picking a version nobody wrote down.
            version_args = ["-p", version] if version else []
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
        if cfg["no-index"]:
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

        merged_units = list(fresh_units)
        if append_mode:
            stored_units = [tuple(u) for u in json.loads(path.read_text())] if path.is_file() else []
            seen = set(stored_units)
            merged_units = list(stored_units)
            for unit in fresh_units:
                if unit not in seen:
                    merged_units.append(unit)
                    seen.add(unit)

        ctx.mkdir(path.parent)
        ctx.write_text(path, json.dumps([list(u) for u in merged_units]))
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
                die(f"uv: skip-if script not found: {path}")
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
