"""Shared runtime context for denver providers.

The Context object is the single place that holds everything a provider
needs: computed denver built-in paths, the merged denver.yml config, and the
mutable environment (``env``) that providers build up and that the final
command is launched with.

Genericity principle: no provider hard-codes project-specific paths or
values. Everything specific comes from denver.yml, where values may
reference denver built-ins and each other through ``${VAR}`` interpolation.
"""

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

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stderr)
logger = logging.getLogger("denver")


# --------------------------------------------------------------------------- #
# Small logging helpers, backed by the stdlib logging module
# --------------------------------------------------------------------------- #
def info(message):
    """Log ``message`` at info level (suppressed under --quiet)."""
    logger.info(message)


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
    several stages of the same provider type (e.g. two 'pip' stages), and
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


_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


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


class Context:
    """Everything a provider needs to do its job."""

    def __init__(self, denver_dir, env_dir, config, import_dirs=None, quiet=0, fast=False, force=False, ci=False):
        """Compute every built-in path/flag a provider might need, and seed ``self.env`` with them.

        ``quiet`` is a level (0 normal, 1 = -q, 2+ = -qq -- see
        set_quiet()/banner()), not a bool, though a bare ``True``/``False``
        still works (``bool`` is an ``int`` subclass: ``True`` behaves as
        level 1, ``False`` as level 0). ``force``/``ci`` are plain flags
        (denver's own ``--force``/``--ci``, see denver.py) -- unlike every
        other provider-facing toggle, these are never read back out of a
        real environment variable; ``ctx.force``/``ctx.ci`` reflect exactly
        what was passed in here, nothing else.
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
        set_quiet(quiet)

        self.env_name = self.env_dir.name
        self.in_docker = Path("/.dockerenv").exists()

        # denver-owned working area for this env (caches, venv, logs, ...)
        self.env_workdir = self.denver_dir / ".envs" / self.env_name
        self.logs_dir = self.env_workdir / ".logs"

        # venv lives under the workdir; host and in-docker venvs are kept apart.
        # venv_dir is the default; venv_dir_for(name) gives a per-stage venv so
        # an env can have several pip stages with distinct venvs.
        self.venv_dir = self.venv_dir_for(None)

        # the mutable environment the final command inherits
        self.env = dict(os.environ)
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

    # ---- process helpers ------------------------------------------------ #
    def run(self, cmd, *, cwd=None, check=True, echo=True, capture=False, extra_env=None, input=None, step=None):
        """Run a subprocess using the context environment.

        Under --quiet, the '+ cmd' echo is skipped and -- unless the caller
        already asked for capture=True, in which case nothing was ever
        printed anyway -- the subprocess's own stdout/stderr are discarded
        rather than inherited, so only the final launched command (which
        goes through Context.exec, not Context.run) is ever visible.

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
        return subprocess.run(
            [str(c) for c in cmd],
            cwd=str(cwd) if cwd else None,
            env=env,
            check=check,
            text=True,
            **run_kwargs,
        )

    def which(self, name):
        """Find an executable, honouring the context PATH (e.g. the venv)."""
        return shutil.which(name, path=self.env.get("PATH"))

    def source(self, *scripts):
        """Source bash script(s) and fold the resulting exports into env.

        This is how denver 'activates' things that are only expressible as
        bash (venv activate, conan's conanbuildenv.sh, hook scripts).
        """
        scripts = [str(s) for s in scripts if s and Path(s).exists()]
        if not scripts:
            return
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
        """Replace the current process with ``cmd`` using the context env."""
        cmd = [str(c) for c in cmd]
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


def sha256_of_files(paths):
    """Stable checksum block for a set of files (missing files are tolerated)."""
    lines = []
    for path in paths:
        p = Path(path)
        digest = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "0" * 64
        lines.append(f"{digest}  {p}")
    return "\n".join(lines)
