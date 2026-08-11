"""Shared runtime context for denver providers.

The Context object is the single place that holds everything a provider
needs: computed denver built-in paths, the merged denver.yml config, and the
mutable environment (``env``) that providers build up and that the final
command is launched with.

Genericity principle: no provider hard-codes project-specific paths or
values. Everything specific comes from denver.yml, where values may
reference denver built-ins and each other through ``${VAR}`` interpolation.
"""

import errno
import fcntl
import hashlib
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

# Every line a --dry-run emits starts with this, so the whole preview can be
# grepped/filtered out of a terminal session in one go. The marker that
# follows it ('+', '?', '~', '.', '!') says which kind of line it is -- see
# dry_run_legend(), which states the same key to the user once per run.
DRY_PREFIX = "[dry-run]"

# One denver run per env at a time -- see Context.acquire_lock.
LOCK_FILE_NAME = ".lock"

# Lock files this *process* already holds, path -> fd. flock associates a lock
# with the open file description rather than the process, so a second open() of
# the same path blocks against the first even from within one process. The
# hazard being guarded against is another *process*, so a path already held
# here is simply kept: re-locking it would deadlock against ourselves.
_HELD_LOCKS = {}


def _boot_id():
    """This boot's identifier, or "?" where the kernel does not publish one.

    Stamped into the lock file so a leftover from before a reboot is
    recognisable: pids are reused freely across boots, so "pid 1234 holds it"
    is otherwise unfalsifiable.
    """
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:  # pragma: no cover - Linux-only file
        return "?"


def _lock_holder(path):
    """Describe whoever holds the lock, for a message -- best effort."""
    try:
        stamp = Path(path).read_text().split()
    except OSError:  # pragma: no cover - the file exists; we just locked it
        stamp = []
    return " ".join(stamp) or "unknown"


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stderr)
logger = logging.getLogger("denver")


# --------------------------------------------------------------------------- #
# Small logging helpers, backed by the stdlib logging module
# --------------------------------------------------------------------------- #
def info(message):
    """Log ``message`` at info level (suppressed under --quiet)."""
    logger.info(message)


def warn(message):
    """Log ``message`` at warning level: something is off, but not off enough to stop.

    Suppressed under --quiet exactly like info() (see set_quiet) -- only
    die()'s errors survive that. So a warning is for a run that is expected
    to keep working, just not the way the config says it should; anything
    the user must see even under -q belongs in die() instead.
    """
    logger.warning(message)


# 0 = normal, 1 = quiet (-q: banners still shown), 2+ = silent (-qq: nothing
# denver-emitted shown at all). See set_quiet()/banner().
_quiet_level = 0


def set_quiet(level):
    """Silence denver-emitted messages across every provider module (by level), or restore normal logging.

    Level 1 (-q) covers info/'+ cmd' echoes and the stdout/stderr of
    subprocesses run via Context.run -- they all funnel through this one
    "denver" logger and Context.run, so a single threshold here is enough --
    but leaves banner() (which stage/sub-step is currently running) visible,
    so it's still possible to tell where denver is without full output.
    Level 2+ (-qq) additionally silences banner() itself, matching every
    prior version of --quiet (full silence). logger.error (die) is never
    silenced at any level: a failure must still be reported.
    """
    global _quiet_level
    _quiet_level = level
    logger.setLevel(logging.ERROR if level >= 1 else logging.INFO)


def banner(ctx, stage, message):
    """Print a colored progress marker to stderr (e.g. '[1/7] conan - install'), unless -qq.

    ``[ctx.stage_index/ctx.stage_count]`` is the stage's position in the
    overall pipeline (set by denver.py as it runs each stage in order).
    Prefixed with the stage id, not just the message -- an env can declare
    several stages of the same provider type (e.g. two 'uv' stages), and
    the message alone (e.g. 'install') can't tell those apart; the stage id
    always can. There's no sub-step numbering: each call is just the next
    line in that stage's own progress trail, in whatever order the provider
    actually does the work -- nothing to precompute or keep in sync between
    a real run and its --fast/--force variants. Normally (no -q) a boxed
    3-line frame; under -q it collapses to a single '-- TEXT' line (still
    visible, just less vertical space -- see set_quiet). -qq silences it
    entirely.
    """
    # printed directly (not via logger.info) so it isn't prefixed with
    # "INFO: " -- it's a visual progress marker, not a log line.
    if _quiet_level >= 2:
        return
    text = f"[{ctx.stage_index}/{ctx.stage_count}] {stage} - {message}"
    if _quiet_level == 1:
        print(f"\033[93m-- {text}\033[39m", file=sys.stderr)
    else:
        line = "-" * (len(text) + 4)
        print(f"\033[93m{line}\n| {text} |\n{line}\033[39m", file=sys.stderr)


def stage_banner(ctx, stage, provider_name):
    """Print a single colored '[i/n] stage '<id>' (<provider>)' line to stderr, unless -qq.

    Emitted centrally by denver.py right before a stage's setup() runs, so a
    stage always announces itself even when its provider dies before
    reaching a banner() of its own -- which several do, since they check for
    their tool first (see e.g. ZephyrProvider.setup). Without this, such a
    failure prints an error with no indication of which stage produced it,
    and the stage id is exactly what the user needs to pass to --skip next.

    One line rather than banner()'s boxed frame: this marks *entering* a
    stage, while the boxes below it are the sub-steps that stage actually
    performs. Naming the provider too, because a stage id is only a label --
    an env may run the same provider twice under different ids.
    """
    if _quiet_level >= 2:
        return
    text = f"[{ctx.stage_index}/{ctx.stage_count}] stage '{stage}' ({provider_name})"
    print(f"\033[93m-- {text}\033[39m", file=sys.stderr)


def skip_banner(ctx, stage, reason):
    """Print a single colored '[i/n] stage '<id>' <reason>' line to stderr, unless -qq.

    For a stage skipped entirely (--until/--skip), not one that ran but had a
    sub-step skipped (that's still banner(), e.g. 'install (skipped by
    --fast)'). Always one line, never banner()'s boxed frame at -q's default
    level -- a whole-stage skip did no work, so a full box would overstate
    it. Hidden at -qq, same as banner().
    """
    if _quiet_level >= 2:
        return
    text = f"[{ctx.stage_index}/{ctx.stage_count}] stage '{stage}' {reason}"
    print(f"\033[93m-- {text}\033[39m", file=sys.stderr)


def dry_run_legend():
    """Print the one-off ``[dry-run]`` marker legend to stderr, before the first stage runs.

    The markers mean genuinely different things -- skipped, really run,
    skipped filesystem write (see Context.run) -- and a preview that doesn't
    say which is which invites reading a '?' line as "would run", i.e. as
    the opposite of what it reports. So the key is stated once, up front,
    rather than left to the documentation.
    """
    print(
        f"\033[93m{DRY_PREFIX} no command below is executed for its effect. Legend:\n"
        f"{DRY_PREFIX}   +  command that would run\n"
        f"{DRY_PREFIX}   ?  read-only query, really run (its output decides what follows)\n"
        f"{DRY_PREFIX}   ~  file/directory write that would happen\n"
        f"{DRY_PREFIX}   .  script sourced into the environment, really done\n"
        f"{DRY_PREFIX}   !  note about what this preview cannot show\033[39m",
        file=sys.stderr,
    )


def die(message) -> NoReturn:
    """Log ``message`` as an error and exit the process with status 1."""
    logger.error(message)
    sys.exit(1)


# --------------------------------------------------------------------------- #
# Parent-directory search helpers
# --------------------------------------------------------------------------- #
def find_in_parents(start, name):
    """Yield every ancestor of ``start`` (inclusive) that contains ``name``."""
    current = Path(start).resolve()
    while True:
        if (current / name).exists():
            yield current
        if current.parent == current:
            break
        current = current.parent


def find_outermost_in_parents(start, name):
    """Return the highest (closest-to-root) ancestor of ``start`` that contains ``name``, or None."""
    matches = list(find_in_parents(start, name))
    return matches[-1] if matches else None


_VAR_RE = re.compile(r"\$\{([A-Za-z_]\w*)(?::-([^}]*))?\}")


def interpolate(value, variables):
    """Expand ``${VAR}`` / ``${VAR:-default}`` in strings, lists and dicts."""
    if isinstance(value, str):

        def repl(match):
            name, default = match.group(1), match.group(2)
            if name in variables and variables[name] is not None:
                return str(variables[name])
            return default if default is not None else ""

        return _VAR_RE.sub(repl, value)
    if isinstance(value, list):
        return [interpolate(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: interpolate(v, variables) for k, v in value.items()}
    return value


def _drop_bundled_library_path(env):
    """Undo a frozen build's LD_LIBRARY_PATH in the environment handed to child processes.

    A one-file PyInstaller build (see scripts/create-python-exe.sh) unpacks
    the libraries it bundles -- liblzma, libssl, libz, ... -- into a temporary
    directory and runs with ``LD_LIBRARY_PATH`` pointing there, so its own
    interpreter finds them. Every child process inherits that, and denver's
    entire job is starting child processes: the *system's* programs, linked
    against the *system's* libraries, which then load denver's instead. Where
    the bundled copy is older (it is built on an old distro precisely so the
    executable runs everywhere), they simply fail::

        xz: /tmp/_MEIxxxxxx/liblzma.so.5: version `XZ_5.4' not found (required by xz)

    PyInstaller preserves any pre-existing value as ``LD_LIBRARY_PATH_ORIG``,
    so that one is restored when present and the variable dropped entirely
    when it is not -- the state the user's own shell was in either way.

    Undoing it here, on the environment rather than on this process, is what
    makes it safe: the dynamic loader read the variable once at startup, so
    denver's own already-resolved libraries are unaffected, while everything
    downstream of ctx (run(), source(), exec() and the final command) gets a
    clean environment from the single place it is seeded.
    """
    if not getattr(sys, "frozen", False):
        return
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is None:
        env.pop("LD_LIBRARY_PATH", None)
    else:
        env["LD_LIBRARY_PATH"] = original


class Context:
    """Everything a provider needs to do its job."""

    def __init__(
        self, denver_dir, env_dir, config, import_dirs=None, quiet=0, fast=False, force=False, ci=False, dry_run=False
    ):
        """Compute every built-in path/flag a provider might need, and seed ``self.env`` with them.

        ``quiet`` is a level (0 normal, 1 = -q, 2+ = -qq -- see
        set_quiet()/banner()), not a bool, though a bare ``True``/``False``
        still works (``bool`` is an ``int`` subclass: ``True`` behaves as
        level 1, ``False`` as level 0). ``force``/``ci``/``dry_run`` are
        plain flags (denver's own ``--force``/``--ci``/``--dry-run``, see
        denver.py) -- unlike every other provider-facing toggle, these are
        never read back out of a real environment variable;
        ``ctx.force``/``ctx.ci``/``ctx.dry_run`` reflect exactly what was
        passed in here, nothing else.
        """
        self.denver_dir = Path(denver_dir).resolve()
        # where denver's own code lives (this file's directory, containing
        # denver.py and providers/) -- unlike denver_dir (per-run state; may
        # be an arbitrary DENVER_STATE_DIR when installed), this is always
        # exactly where the bundled conan_scripts/docker_scripts live, in
        # both a checkout and an installed package.
        self.denver_pkg_dir = Path(__file__).resolve().parent.parent
        self.env_dir = Path(env_dir).resolve()
        self.config = config or {}
        self.import_dirs = [Path(d).resolve() for d in (import_dirs or [])]
        self.quiet = quiet
        self.fast = fast
        self.force = force
        self.ci = ci
        self.dry_run = dry_run
        # names ctx.which() already had to invent a dry-run stand-in for, so
        # the warning explaining that is printed once per tool instead of
        # once per lookup (every stage re-resolves its own defaults).
        self._dry_missing_tools = set()
        # holds this env's run lock open for the process's lifetime (acquire_lock)
        self._lock_fd = None
        # this stage's position in the overall pipeline, for banner()'s
        # '[i/n]' -- 1/1 by default so a provider driven directly (e.g. in
        # tests) without going through denver.py's run_stages() still gets a
        # sensible progress line; run_stages() overwrites these per stage.
        self.stage_index = 1
        self.stage_count = 1
        # the current stage's own id (e.g. "docker", "conan") -- set by
        # denver.py right before calling a provider's setup()/wrap(), so
        # ctx.run(..., step="...")'s auto-banner knows which stage it's
        # for without every call site having to pass self.stage itself.
        self.stage_id = None
        # each stage's config section exactly as the denver.yml (after
        # stacking/overrides) spelled it, before any provider default was
        # filled in -- kept by denver.resolve_provider_defaults so a stage's
        # defaults can be resolved *again*, from scratch, right before it
        # runs. Re-resolving the already-resolved section can't do that: a
        # resolver reads its own output back as if the author had written
        # it, so a PATH lookup that resolved to the host's copy upfront
        # would survive an earlier stage installing the real one. Empty
        # (never populated) when a provider is driven directly, e.g. in tests.
        self.raw_sections = {}
        set_quiet(quiet)

        self.env_name = self.env_dir.name
        self.in_docker = Path("/.dockerenv").exists()

        # denver-owned working area for this env (caches, venv, logs, ...)
        self.env_workdir = self.denver_dir / ".envs" / self.env_name
        self.logs_dir = self.env_workdir / ".logs"

        # venv lives under the workdir; host and in-docker venvs are kept apart.
        # venv_dir is the default; venv_dir_for(name) gives a per-stage venv so
        # an env can have several uv stages with distinct venvs.
        self.venv_dir = self.venv_dir_for(None)

        # the mutable environment the final command inherits
        self.env = dict(os.environ)
        _drop_bundled_library_path(self.env)
        self._init_builtins()

    # ---- built-in variables --------------------------------------------- #
    def _init_builtins(self):
        """Seed env with denver built-ins (also usable in ${...} interpolation)."""
        builtins = {
            "DENVER_SRC_DIR": str(self.denver_pkg_dir),
            "DENVER_ENV_DIR": str(self.env_dir),
            "DENVER_ENV_NAME": self.env_name,
            "DENVER_ENV_WORKDIR": str(self.env_workdir),
            "SHELL_PROMPT_PREFIX": self.prompt_prefix,
        }
        # denver-owned identifiers always reflect the current run, even over a
        # stale value of the same name already in the real environment
        self.env.update(builtins)
        self._prefix_prompt()

    @property
    def prompt_prefix(self):
        """The marker text saying a shell is running inside this env: ``'(<env>) '``.

        Exported as denver's own ``SHELL_PROMPT_PREFIX``; nothing reads that
        on its own, it carries the text for a shell's config to apply.
        """
        return f"({self.env_name}) "

    @property
    def prompt(self):
        """Zsh's PROMPT for this env: ``'(<env>) %m%#'`` (short host, then % or # for root)."""
        return f"{self.prompt_prefix}%m%#"

    @property
    def prompt_command(self):
        """The snippet denver appends to PROMPT_COMMAND, re-applying ``prompt_prefix`` to PS1.

        PROMPT_COMMAND is bash's own standard pre-prompt hook, not something
        denver owns -- denver only adds to whatever is already in it. That
        hook is what makes the marker survive at all: bash runs it before
        drawing *every* prompt, i.e. after the rc files that would otherwise
        have discarded an inherited PS1 (see _prefix_prompt).

        The ``case`` guard is not decoration: without it this would re-prefix
        on every single prompt, growing PS1 to '(env) (env) (env) ...' line
        after line. ``case`` rather than a bash-only conditional so a
        POSIX-ish shell sourcing it doesn't choke.
        """
        prefix = self.prompt_prefix
        return f'case "$PS1" in "{prefix}"*) ;; *) export PS1="{prefix}$PS1";; esac'

    def _prefix_prompt(self):
        """Mark the shell denver execs with ``prompt_prefix``, via each shell's own prompt variable.

        None of these belong to denver -- it writes the variables the shells
        themselves define: PS1/PROMPT_COMMAND for bash, PROMPT for zsh, and
        SHELL_PROMPT_PREFIX, which fish reads natively from 4.8.0 on.

        PS1 is deliberately *not* set: an interactive bash re-reads its own
        rc files after denver execs it and assigns PS1 outright, so anything
        denver put there is discarded before the user ever sees it.
        PROMPT_COMMAND is bash's answer to exactly that -- it runs after
        those rc files, before every prompt -- so that is where the marker
        goes instead.

        Prefixing is idempotent: a wrapper provider re-invokes denver inside
        the container (see denver.py's reinvoke_command) with this env
        already applied, and the inner run must not stack a second copy.
        """
        snippet = self.prompt_command
        # a trailing ';' on the inherited value would make '<existing>; <snippet>'
        # a bash syntax error ('cmd; ; case ...'), so it's normalised away
        existing = self.env.get("PROMPT_COMMAND", "").strip().rstrip(";").strip()
        if snippet not in existing:
            self.env["PROMPT_COMMAND"] = f"{existing}; {snippet}" if existing else snippet

        # zsh's own prompt variable. PROMPT and PS1 are the *same* parameter
        # in zsh, so an inherited PS1 (denver sets none of its own, see
        # above) would win over this one if it happened to come later in
        # environ -- re-inserting PROMPT last (pop + assign) makes this
        # zsh-syntax value the one zsh keeps, whatever it inherited.
        self.env.pop("PROMPT", None)
        self.env["PROMPT"] = self.prompt

    @property
    def variables(self):
        """The dict used for ${...} interpolation: current env wins."""
        return self.env

    def venv_dir_for(self, name):
        """Path of a (named) venv; host and in-docker venvs are kept apart."""
        leaf = ".venv" if not name else f".venv-{name}"
        venv = self.env_workdir / leaf
        return venv if self.in_docker else Path(str(venv) + ".host")

    # ---- serialising concurrent runs ------------------------------------ #
    def acquire_lock(self, *, wait=True):
        """Take this env's exclusive run lock, so two runs cannot corrupt each other's state.

        Every piece of an env's state is shared between concurrent runs, and
        several steps rebuild rather than update: the conan provider wipes its
        whole install tree before installing, and the uv provider removes and
        recreates a venv whose requirements changed -- both potentially while
        another run is sourcing or using exactly that. There is no useful way
        to merge two such runs, so they are serialised.

        Deliberately *not* released explicitly: the lock fd is closed by
        ``os.execvpe`` (Python creates file descriptors non-inheritable --
        PEP 446), so the lock lasts exactly as long as denver is mutating
        state and drops the moment it hands over to the user's command. A
        long-lived devshell therefore never holds it, and a wrapper
        relocation cannot deadlock against itself: the outer process ceases
        to exist at exec, before the inner one asks.

        Skipped entirely under --dry-run, which mutates nothing.
        """
        if self.dry_run:
            return
        path = self.env_workdir / LOCK_FILE_NAME
        if str(path) in _HELD_LOCKS:
            return  # already ours -- see _HELD_LOCKS
        self.mkdir(path.parent)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        if not self._flock(fd, path, wait=wait):
            os.close(fd)
            return
        # stamped for diagnosis only -- the lock itself is the flock, never
        # this content. The boot id makes a stale file from before a reboot
        # recognisable as such, since pids are reused freely across one.
        os.ftruncate(fd, 0)
        os.write(fd, f"pid={os.getpid()}\nboot={_boot_id()}\n".encode())
        # kept on the instance purely to hold the descriptor open for this
        # process's lifetime; nothing ever reads it back.
        self._lock_fd = fd
        _HELD_LOCKS[str(path)] = fd

    def _flock(self, fd, path, *, wait):
        """Take the flock on ``fd``, reporting a wait; False if locking is unavailable here."""
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                # ENOLCK/EOPNOTSUPP/EINVAL: some NFS and overlay mounts do not
                # implement flock at all. Saying so beats pretending the run
                # is serialised when nothing is enforcing it.
                warn(f"denver: cannot lock {path} ({exc.strerror or exc}) -- concurrent runs are not serialised")
                return False
        if not wait:
            die(f"denver: another denver run holds this env ({_lock_holder(path)}); --no-wait was given")
        info(f"denver: waiting for another denver run on this env ({_lock_holder(path)})")
        fcntl.flock(fd, fcntl.LOCK_EX)
        return True

    # ---- config access -------------------------------------------------- #
    def section(self, name):
        """Return a provider's config section (interpolated), or {}."""
        raw = self.config.get(name) or {}
        return interpolate(raw, self.variables)

    def resolve_path(self, value, *, base=None):
        """Resolve a possibly-relative config path.

        Relative paths are resolved against ``base`` (default: the env dir).
        If not found under the env dir, imported (base) env dirs are tried, so
        an env can inherit files like conan/base_classes from its base.

        A value that isn't a path at all (a list where a provider expects one
        string, say) is a config mistake, and gets denver's own message
        rather than a raw TypeError out of pathlib -- see "Fail loud" in
        doc/philosophy.md. This is the last line of defence: a provider that
        knows which key it is reading should say so itself first (see
        ConanProvider.resolve_defaults' 'base-classes:' check).
        """
        value = interpolate(value, self.variables)
        if not isinstance(value, (str, os.PathLike)):
            die(f"expected a path in denver.yml, got a {type(value).__name__}: {value!r}")
        p = Path(value).expanduser()
        if p.is_absolute():
            return p
        base = Path(base) if base else self.env_dir
        primary = base / p
        if primary.exists():
            return primary.resolve()
        for d in self.import_dirs:
            candidate = d / p
            if candidate.exists():
                return candidate.resolve()
        # default to env-dir-relative even if missing (caller may create it)
        return (self.env_dir / p).resolve()

    # ---- env manipulation ----------------------------------------------- #
    def set(self, key, value):
        """Set ``key`` in ctx.env to ``str(value)`` (or "" for None)."""
        self.env[key] = "" if value is None else str(value)

    def setdefault(self, key, value):
        """Set ``key`` only if it isn't already set (or is set to an empty string) in ctx.env."""
        if not self.env.get(key):
            self.set(key, value)

    def prepend_path(self, directory):
        """Prepend ``directory`` onto ctx.env['PATH'] (e.g. to put a venv's bin/ first)."""
        directory = str(directory)
        current = self.env.get("PATH", "")
        if current:
            self.env["PATH"] = f"{directory}{os.pathsep}{current}"
        else:
            self.env["PATH"] = directory

    def append_path_var(self, key, value, sep=os.pathsep):
        """Append ``value`` onto ctx.env[key], joined by ``sep`` if it's already set."""
        current = self.env.get(key, "")
        self.env[key] = f"{current}{sep}{value}" if current else str(value)

    def apply_env_map(self, mapping):
        """Apply an interpolated {name: value} mapping into the environment."""
        for key, value in interpolate(mapping or {}, self.variables).items():
            self.set(key, value)

    # ---- dry-run reporting ---------------------------------------------- #
    def dry_note(self, marker, message):
        """Print one ``[dry-run] <marker> <message>`` line to stderr (see Context.run for the markers).

        Always printed, even under --quiet: in a dry run these lines *are*
        the output -- silencing them would leave the run with nothing to
        show at all.
        """
        print(f"{DRY_PREFIX} {marker} {message}", file=sys.stderr)

    # ---- process helpers ------------------------------------------------ #
    def run(self, cmd, *, cwd=None, check=True, echo=True, capture=False, extra_env=None, input=None, step=None):
        """Run a subprocess using the context environment.

        Under --quiet, the '+ cmd' echo is skipped and -- unless the caller
        already asked for capture=True, in which case nothing was ever
        printed anyway -- the subprocess's own stdout/stderr are discarded
        rather than inherited, so only the final launched command (which
        goes through Context.exec, not Context.run) is ever visible.

        Under --dry-run (``ctx.dry_run``) a command that exists for its
        *effect* is printed as ``[dry-run] + cmd`` and not run at all,
        standing in as an immediately-successful, output-less call. A
        ``capture=True`` call is different: it exists for its *output*, which
        some provider is about to branch on (is the image cached? which conan
        home? what does `west list` say?), so a dry run would have nothing to
        decide with and would stop reflecting what a real run does. Those are
        genuinely executed -- they are the reads, not the writes -- and
        reported as ``[dry-run] ? cmd`` so the preview still says so.
        A missing executable is not fatal in that case either: half the point
        of a dry run is previewing an env whose tools an earlier (skipped)
        stage would have installed, so it degrades to the same empty,
        non-zero result rather than raising.

        ``input``, if given, is fed to the subprocess's stdin (e.g. a secret
        piped into ``docker login --password-stdin`` without it ever
        appearing in argv or a log line) -- same name/meaning as
        subprocess.run's own ``input`` kwarg, just forwarded through.

        ``step``, if given, prints this command's own progress banner (via
        ``banner()``, using ``ctx.stage_id``) immediately before the '+ cmd'
        echo -- the two can never drift apart or print out of order, unlike
        a separate ``banner(...)`` call a caller has to remember to place
        before this one. Only worth using for a step that *is* essentially
        one subprocess call; a step spanning several calls (or none, e.g. a
        pure info message) still calls ``banner()`` directly instead.
        """
        if step is not None:
            banner(self, self.stage_id, step)
        env = dict(self.env)
        if extra_env:
            env.update({k: str(v) for k, v in extra_env.items()})
        printable = " ".join(str(c) for c in cmd)
        if self.dry_run:
            return self._dry_run_command(cmd, printable, cwd=cwd, env=env, capture=capture, input=input)
        if echo and not self.quiet:
            print(f"+ {printable}", file=sys.stderr)
        run_kwargs = {}
        if capture:
            run_kwargs["capture_output"] = True
        elif self.quiet:
            run_kwargs["stdout"] = subprocess.DEVNULL
            run_kwargs["stderr"] = subprocess.DEVNULL
        if input is not None:
            run_kwargs["input"] = input
        try:
            return subprocess.run(
                [str(c) for c in cmd],
                cwd=str(cwd) if cwd else None,
                env=env,
                check=check,
                text=True,
                **run_kwargs,
            )
        except OSError as exc:
            # The command could not be started at all -- a configured 'exe:'
            # naming a file that isn't there, a script without the execute
            # bit, an unreadable cwd. That is a denver.yml problem, but
            # Popen raises before check= ever applies, so main()'s
            # CalledProcessError handler never sees it and the user gets a
            # traceback whose frames name subprocess.py rather than the key
            # at fault. Reported here instead, naming the stage and command.
            stage = f"stage '{self.stage_id}': " if self.stage_id else ""
            die(f"{stage}cannot run {printable}: {exc.strerror or exc}")

    def _dry_run_command(self, cmd, printable, *, cwd, env, capture, input):
        """Report ``cmd`` under --dry-run: print-and-skip it, or really run it if it's a query (see Context.run)."""
        if not capture:
            self.dry_note("+", printable)
            return subprocess.CompletedProcess([str(c) for c in cmd], 0, "", "")
        self.dry_note("?", printable)
        try:
            result = subprocess.run(
                [str(c) for c in cmd],
                cwd=str(cwd) if cwd else None,
                env=env,
                check=False,  # a dry run reports, it never aborts on a query
                text=True,
                capture_output=True,
                input=input,
            )
        except OSError as exc:
            # e.g. the tool an earlier stage would have installed isn't
            # there: report it and let the caller see an ordinary failure.
            self.dry_note("?", f"{cmd[0]}: not available ({exc.strerror or exc}) -- assuming it would fail")
            return subprocess.CompletedProcess([str(c) for c in cmd], 127, "", "")
        if result.returncode != 0:
            # a query that failed answered nothing, so whatever the caller
            # derives from it below (which args to pass, whether a step is
            # needed) is a guess. Said out loud: a real run would have had
            # an answer here, and silence would let the preview look
            # authoritative where it is least so.
            self.dry_note("?", f"{cmd[0]}: exited {result.returncode} -- what follows may not match a real run")
        return result

    def which(self, name, *, dry_fallback=False):
        """Find an executable, honouring the context PATH (e.g. the venv).

        ``dry_fallback`` marks a tool an *earlier stage* is expected to
        install (west, conan, uv, docker): under --dry-run that stage only
        printed its commands, so the tool legitimately isn't there yet, and
        returning None would abort the preview at exactly the stage the user
        wanted to see. The bare name is returned instead -- enough to render
        every command below it -- with a one-off warning saying so.
        """
        found = shutil.which(name, path=self.env.get("PATH"))
        if found is None and self.dry_run and dry_fallback:
            if name not in self._dry_missing_tools:
                self._dry_missing_tools.add(name)
                warn(f"dry-run: '{name}' is not on PATH -- showing commands as if it were")
            return name
        return found

    # ---- filesystem helpers (--dry-run aware) ---------------------------- #
    #
    # Providers write and delete real files outside of any subprocess
    # (checksum stamps, generated listings, a wiped venv/conan tree). Those
    # go through these helpers rather than pathlib/shutil directly, so
    # --dry-run has one place to intercept them: printing that a subprocess
    # was skipped while still deleting the venv it would have rebuilt would
    # be worse than not offering a dry run at all.
    def mkdir(self, path, *, parents=True, exist_ok=True):
        """Create ``path`` as a directory (no-op, reported, under --dry-run)."""
        if self.dry_run:
            if not Path(path).is_dir():
                self.dry_note("~", f"mkdir {path}")
            return
        Path(path).mkdir(parents=parents, exist_ok=exist_ok)

    def write_text(self, path, text):
        """Write ``text`` to ``path``, replacing it (no-op, reported, under --dry-run)."""
        if self.dry_run:
            self.dry_note("~", f"write {path}")
            return
        Path(path).write_text(text)

    def append_text(self, path, text):
        """Append ``text`` to ``path`` (no-op, reported, under --dry-run)."""
        if self.dry_run:
            self.dry_note("~", f"append to {path}")
            return
        with Path(path).open("a") as fh:
            fh.write(text)

    def touch(self, path):
        """Create ``path`` as an empty file if missing (no-op, reported, under --dry-run)."""
        if self.dry_run:
            self.dry_note("~", f"touch {path}")
            return
        Path(path).touch()

    def unlink(self, path, *, missing_ok=False):
        """Delete the file ``path`` (no-op, reported, under --dry-run)."""
        if self.dry_run:
            self.dry_note("~", f"rm {path}")
            return
        Path(path).unlink(missing_ok=missing_ok)

    def rmtree(self, path):
        """Delete the directory tree ``path``, tolerating a missing one (no-op, reported, under --dry-run)."""
        if self.dry_run:
            self.dry_note("~", f"rm -r {path}")
            return
        shutil.rmtree(path, ignore_errors=True)

    def source(self, *scripts):
        """Source bash script(s) and fold the resulting exports into env.

        This is how denver 'activates' things that are only expressible as
        bash (venv activate, conan's conanbuildenv.sh, hook scripts).

        Still done under --dry-run, and reported as ``[dry-run] . script``:
        sourcing is how denver *computes* the environment, and a command
        rendered without it would show empty ``${...}`` values and a PATH
        missing every tool an earlier stage put there -- i.e. not the
        commands a real run would use. A script that doesn't exist yet
        (a venv's activate, conan's conanbuildenv.sh) is skipped in a dry
        run exactly as it is in a real one, silently.
        """
        scripts = [str(s) for s in scripts if s and Path(s).exists()]
        if not scripts:
            return
        if self.dry_run:
            for script in scripts:
                self.dry_note(".", script)
        sentinel = "__DENVER_ENV_SENTINEL__"
        source_cmds = " && ".join(f". {shlex.quote(s)}" for s in scripts)
        script = f'{source_cmds}; printf "%s\\0" "{sentinel}"; env -0'
        result = subprocess.run(
            ["bash", "-c", script],
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            die(f"failed to source {scripts}: {result.stderr.strip()}")
        _, _, env_blob = result.stdout.partition(f"{sentinel}\0")
        for entry in env_blob.split("\0"):
            # skip the trailing '' after the last \0-terminated entry (and
            # any malformed entry with no '=') -- always exercised (every
            # source() call hits the trailing ''), but coverage.py under
            # Python 3.9 doesn't reliably trace a 'continue' as a loop's
            # last statement, so it's excluded rather than restructured.
            if not entry or "=" not in entry:
                continue  # pragma: no cover
            key, _, value = entry.partition("=")
            self.env[key] = value

    def exec(self, cmd):
        """Replace the current process with ``cmd`` using the context env.

        Under --dry-run the command is reported and this returns normally
        instead -- every caller treats exec() as the last thing it does, so
        returning simply ends the run the way the real one ends the process.
        """
        cmd = [str(c) for c in cmd]
        # a resolved command is always denver's own doing (default_command()/
        # resolve_command(), a wrapper's wrap(), or a script's own argv) --
        # never raw external input -- but a malformed 'command:'/script entry
        # (e.g. an empty string) must not reach os.execvpe() as a bare,
        # confusing OSError: validated here so the failure names the actual
        # problem instead.
        if not cmd or not cmd[0]:
            die(f"exec: empty or invalid command: {cmd!r}")
        if any("\0" in arg for arg in cmd):
            die(f"exec: command arguments must not contain NUL bytes: {cmd!r}")
        if self.dry_run:
            sys.stdout.flush()
            self.dry_note("+", f"exec: {' '.join(cmd)}")
            return
        # Flush stdout *before* logging "exec:" (and again right before the
        # actual os.execvpe() below): print() output (e.g. the startup logo,
        # a stage's "finished in Xs" line) isn't always line-buffered --
        # piped output block-buffers -- while logging's stderr handler
        # flushes on every write, so without this the "exec:" line (and the
        # replaced command's own output right after it) could appear
        # on-screen *before* stdout content that was actually printed
        # earlier, and os.execvpe() never runs Python's normal
        # flush-on-exit, so unflushed content would otherwise be lost
        # outright, not just reordered.
        sys.stdout.flush()
        info(f"exec: {' '.join(cmd)}")
        sys.stderr.flush()
        try:
            os.execvpe(cmd[0], cmd, self.env)
        except OSError as exc:
            die(f"failed to exec {cmd[0]}: {exc}")


def fingerprint_label(path, base=None):
    """How ``path`` is named inside a fingerprint: relative to ``base`` where possible.

    A fingerprint exists to answer "did the inputs change since last run",
    so it must not also change when the same inputs sit at a different
    absolute path -- which is the normal state of affairs with two checkouts
    of one project, a renamed directory, or a git worktree. Naming a file
    relative to the env dir keeps the answer about content and layout, not
    about where the tree happens to live.

    Anything not reachable relatively (a different drive, or no ``base`` at
    all) keeps its absolute path: that is still stable for the run it
    describes, and it is better to over-invalidate than to conflate two
    genuinely different files.
    """
    path = Path(path)
    if base is None:
        return str(path)
    try:
        return str(Path(os.path.relpath(path, base)))
    except ValueError:  # pragma: no cover - Windows-only (paths on different drives)
        return str(path)


def sha256_of_files(paths, base=None):
    """Stable checksum block for a set of files (missing files are tolerated).

    ``base`` makes the block independent of where the tree lives -- see
    fingerprint_label.
    """
    lines = []
    for path in paths:
        p = Path(path)
        digest = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "0" * 64
        lines.append(f"{digest}  {fingerprint_label(p, base)}")
    return "\n".join(lines)
