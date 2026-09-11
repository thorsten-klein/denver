"""nix provider: brings a nix flake's devShell into the environment.

Configured from denver.toml -> a stage declaring ``provider: nix``, with a
``flake:`` of its own. The devShell's environment is fetched once with ``nix
print-dev-env``, cached, and then *sourced into this process's own
environment* -- denver never wraps the final command in ``nix develop
--command``, so every later stage (and the command itself) simply runs with
the devShell's PATH, compilers and variables already applied.

That is the same trick a hand-written ``devshell.py`` plays: one process
throughout, no nested shell, and the (slow) evaluation paid for only when
the flake's own inputs actually changed -- see ``_cache_key``, which keys the
cached environment on the content of every git-tracked file of the flake's
copy root, exactly what a ``path:`` flake can see in the nix store.

This provider has no env-prepend:/env-append: keys of its own: the devShell
already exports everything it provides, and the generic per-stage
'env:'/'env-prepend:'/'env-append:' keys every stage gets (see
GENERIC_STAGE_KEYS in denver.py) cover anything an env wants to add on top.

Full key reference, worked examples and design notes: ``doc/providers/nix.md``.
"""

from __future__ import annotations

import hashlib
import itertools
import os
import platform
import re
import shlex
from pathlib import Path

from .base import Provider, fill_unset
from .context import banner, die, info, sha256_of_files, warn

# 'nix print-dev-env' and flake refs are still guarded behind nix's own
# experimental-feature gates on every released nix; passed per invocation
# (rather than requiring a nix.conf edit on every machine) so an env works
# on a stock install. An empty 'experimental-features:' drops the flag.
DEFAULT_EXPERIMENTAL_FEATURES = "nix-command flakes"

# where the cached 'nix print-dev-env' output for this env goes, under
# ctx.env_workdir. Keyed per stage *and* per input fingerprint (see
# _cache_key), so switching a flake back and forth re-uses both.
CACHE_DIRNAME = "nix-devshell"

# a flake's own files -- the fallback fingerprint when the copy root is not
# a git checkout (see _fingerprint_files).
FLAKE_FILES = ("flake.nix", "flake.lock")

# every key of this provider's section that must be a plain string, and
# every key that must be a boolean -- see NixProvider._validate.
STRING_KEYS = ("exe", "flake", "root", "shell", "experimental-features", "expected-version")
BOOL_KEYS = ("impure", "cache", "keep-path", "shell-hook")

# the last line 'nix print-dev-env' emits on a current nix: the devShell's
# own shellHook, run exactly as `nix develop` would run it. Dropped from the
# cached script when 'shell-hook:' says so -- there is no flag on nix's side
# that would leave it out. Matched loosely enough to cover the spellings
# older/newer releases use ('$shellHook', '${shellHook-}', '${shellHook:-}').
_SHELL_HOOK_LINE_RE = re.compile(r'^\s*eval\s+"\$\{?shellHook(?::?-)?\}?"\s*$')

# characters a dotted version number is made of -- see parsed_version, which
# scans `nix --version`'s output for one without a regular expression: two
# adjacent unbounded quantifiers ('\d+\.\d+') backtrack quadratically over a
# long run of digits that never reaches a dot, and a plain scan cannot.
_VERSION_CHARS = "0123456789."

# a flake *reference* rather than a path: 'github:owner/repo',
# 'git+https://...', 'path:/abs/dir', 'flake:nixpkgs'. Anything else is a
# filesystem path, resolved like every other denver.toml path.
_FLAKE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def parsed_version(text):
    """The first dotted version number in ``text`` (e.g. '2.35.1' out of 'nix (Nix) 2.35.1'), or None.

    A token counts when its leading run of digits-and-dots has at least two
    dot-separated components, all of them digits -- so '(Nix)' and 'nix' are
    passed over, while a suffixed '2.35.1-rc1' still yields '2.35.1'.
    """
    for token in text.split():
        candidate = "".join(itertools.takewhile(lambda c: c in _VERSION_CHARS, token))
        parts = candidate.split(".")
        if len(parts) >= 2 and all(part.isdigit() for part in parts):
            return candidate
    return None


def install_instructions(version):
    """The two nix install commands, for a machine that has none.

    Two different commands because they are genuinely different installs,
    not a flag on the same one: a multi-user (daemon-managed) install needs
    its own build users/group and a running nix-daemon, which only the
    ``--daemon`` form sets up.
    """
    release = f"https://releases.nixos.org/nix/nix-{version}/install" if version else "https://nixos.org/nix/install"
    return f"  curl -L {release} | sh                    # single-user\n  sh <(curl -L {release}) --daemon  # multi-user (daemon)"


class NixProvider(Provider):
    """Sources a nix flake's devShell into the environment -- see doc/providers/nix.md for denver.toml keys."""

    name = "nix"

    #: every key this provider's stage section understands
    KEYS = (
        "exe",
        "flake",
        "root",
        "shell",
        "args",
        "impure",
        "experimental-features",
        "expected-version",
        "cache",
        "keep-path",
        "shell-hook",
    )

    # ---- config defaults -------------------------------------------------- #
    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):  # noqa: ARG003  # shared (ctx, cfg, config) signature
        """Resolve flake/root/exe/args/impure/cache/... -- see doc/providers/nix.md."""
        cls._validate(cfg)
        resolved = dict(cfg)
        # bare name, not a resolved path -- existence is checked in setup(),
        # same as uv's and docker's own 'exe:' defaults.
        resolved["exe"] = cfg.get("exe") or "nix"
        resolved["flake"] = cls._resolved_flake(ctx, cfg.get("flake"))
        resolved["root"] = str(ctx.resolve_path(cfg["root"])) if cfg.get("root") else None
        resolved["shell"] = cfg.get("shell") or None
        resolved["args"] = [str(arg) for arg in (cfg.get("args") or [])]
        resolved["impure"] = bool(cfg.get("impure", False))
        features = cfg.get("experimental-features")
        resolved["experimental-features"] = DEFAULT_EXPERIMENTAL_FEATURES if features is None else features
        resolved["expected-version"] = cfg.get("expected-version") or None
        resolved["cache"] = bool(cfg.get("cache", True))
        resolved["keep-path"] = bool(cfg.get("keep-path", True))
        resolved["shell-hook"] = bool(cfg.get("shell-hook", True))
        return fill_unset(resolved, cls.KEYS)

    @staticmethod
    def _resolved_flake(ctx, flake):
        """'flake:' as an absolute directory, or verbatim when it is a flake *reference* (``github:...``).

        Defaults to the env dir itself: the common case is a ``flake.nix``
        living right next to the denver.yml that names this stage.
        """
        value = flake or "."
        if _FLAKE_REF_RE.match(value):
            return value
        return str(ctx.resolve_path(value))

    # ---- config validation ------------------------------------------------ #
    @classmethod
    def _validate(cls, cfg):
        """Die unless this stage's own keys are well-typed.

        Unknown keys are not this method's job: denver.py rejects any stage
        key that is neither one of GENERIC_STAGE_KEYS nor one of cls.KEYS
        before resolve_defaults() ever runs.
        """
        cls._validate_typed(cfg, STRING_KEYS, str, "a string")
        cls._validate_typed(cfg, BOOL_KEYS, bool, "a boolean")
        cls._validate_args(cfg.get("args"))

    @staticmethod
    def _validate_typed(cfg, keys, expected, described):
        """Die unless every one of ``keys`` that is set at all holds an ``expected`` value."""
        for key in keys:
            value = cfg.get(key)
            if value is not None and not isinstance(value, expected):
                die(f"nix: '{key}:' must be {described} (got {value!r})")

    @staticmethod
    def _validate_args(args):
        """Die unless 'args:', if given, is a list of strings."""
        if args is None:
            return
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            die(f"nix: 'args:' must be a list of strings (got {args!r})")

    # ---- the flake reference nix is actually given ------------------------ #
    @staticmethod
    def _is_path(flake):
        """True when 'flake:' is a filesystem path rather than a flake reference."""
        return not _FLAKE_REF_RE.match(flake)

    @classmethod
    def _copy_root(cls, cfg):
        """The directory nix copies into the store (``root:``, else the flake's own dir), or None for a flake reference."""
        if cfg["root"]:
            return Path(cfg["root"])
        return Path(cfg["flake"]) if cls._is_path(cfg["flake"]) else None

    def _flake_ref(self, cfg):
        """The flake reference handed to nix -- ``path:<root>?dir=<rel>`` when a 'root:' is configured.

        Rooting at 'root:' rather than at the flake's own directory is what
        lets a flake declare a *relative* input pointing outside itself (a
        sibling flake stacked on top of this one): nix copies only the
        reference's own root into the store, and an input below that root
        would otherwise fail evaluation with "access to absolute path ...
        forbidden in pure evaluation mode". ``?dir=`` then points nix at the
        flake.nix inside that copy.
        """
        flake = cfg["flake"]
        if not self._is_path(flake):
            return flake
        root = cfg["root"]
        if not root:
            return f"path:{flake}"
        rel = os.path.relpath(flake, root)
        if rel.startswith(".."):
            die(f"nix[{self.stage}]: 'root:' ({root}) must contain 'flake:' ({flake})")
        return f"path:{root}" if rel == "." else f"path:{root}?dir={Path(rel).as_posix()}"

    def _installable(self, cfg):
        """The installable ``nix print-dev-env`` is given: the flake ref, plus ``#<shell>`` when 'shell:' names one."""
        ref = self._flake_ref(cfg)
        return f"{ref}#{cfg['shell']}" if cfg["shell"] else ref

    # ---- cache ------------------------------------------------------------ #
    def _cache_file(self, ctx, cfg):
        """Where this stage's ``nix print-dev-env`` output is cached, for exactly these inputs.

        The stage id is free-form text out of the denver.yml and it *names*
        this file, so one containing a path separator (or '..') would put
        denver's own writes somewhere outside its state directory. Checked
        here, at the single place that turns config into a path, rather than
        trusted at the two places that then read and rewrite it.
        """
        cache_dir = ctx.env_workdir / CACHE_DIRNAME
        path = cache_dir / f"{self.stage}-{self._cache_key(ctx, cfg)}.sh"
        if path.resolve().parent != cache_dir.resolve():
            die(f"nix[{self.stage}]: stage id must not contain a path separator -- it names this stage's cache file")
        return path

    def _cache_key(self, ctx, cfg):
        """A digest of everything that decides what ``nix print-dev-env`` would print.

        The installable, the extra args, ``--impure`` and 'shell-hook:'
        (each changes the script that ends up cached), the machine's
        architecture (a cached x86_64
        environment is meaningless on aarch64 -- and the two legitimately
        share one checkout over a network mount), and the *content* of every
        file the flake can see (see _fingerprint_files). Content rather than
        mtimes, because that is precisely what nix itself keys on: a flake's
        evaluation only changes when a file it copies into the store does.
        """
        blob = "\n".join([
            f"installable: {self._installable(cfg)}",
            f"args: {shlex.join(cfg['args'])}",
            f"impure: {cfg['impure']}",
            f"shell-hook: {cfg['shell-hook']}",
            f"machine: {platform.machine()}",
            self._inputs_fingerprint(ctx, cfg),
        ])
        return hashlib.sha256(blob.encode()).hexdigest()

    def _inputs_fingerprint(self, ctx, cfg):
        """A checksum block over the flake's own files, or the bare reference when the flake isn't local.

        A remote reference (``github:owner/repo/<rev>``) has no local files
        to hash: a pinned one never changes, and a *moving* one is refreshed
        with ``--force`` (or ``cache: false``) -- see doc/providers/nix.md.
        """
        root = self._copy_root(cfg)
        if root is None:
            return f"ref: {cfg['flake']}"
        return sha256_of_files(self._fingerprint_files(ctx, root), base=root)

    @staticmethod
    def _fingerprint_files(ctx, root):
        """Every git-tracked file under ``root``, or just its flake.nix/flake.lock when it is not a checkout.

        Git-tracked, because that is exactly the set a ``path:`` flake can
        see: nix copies a git working tree's *tracked* files into the store
        and ignores the rest, so hashing more would invalidate the cache on
        an untracked build artifact nix never looked at.
        """
        result = ctx.run(["git", "-C", str(root), "ls-files", "-z"], capture=True, echo=False, check=False)
        names = [name for name in result.stdout.split("\0") if name]
        if result.returncode != 0 or not names:
            return [root / name for name in FLAKE_FILES]
        return [root / name for name in names]

    # ---- lifecycle -------------------------------------------------------- #
    def setup(self, ctx):
        """Fetch (or reuse) the devShell environment and source it into ctx.env."""
        cfg = self.config_section(ctx)
        cache_file = self._cache_file(ctx, cfg)
        if ctx.fast:
            self._setup_fast(ctx, cfg, cache_file)
            return
        banner(ctx, self.stage, "devshell")
        exe = self._require_nix(ctx, cfg)
        self._ensure_devshell_env(ctx, cfg, exe, cache_file)
        self._activate(ctx, cfg, cache_file)

    def _setup_fast(self, ctx, cfg, cache_file):
        """Handle '--fast': source the cached environment verbatim, without asking nix anything at all."""
        banner(ctx, self.stage, "devshell (skipped by --fast)")
        if not cache_file.is_file():
            die(
                f"nix[{self.stage}]: --fast needs a cached devshell environment at {cache_file} -- run once without --fast first"
            )
        self._activate(ctx, cfg, cache_file)

    def _require_nix(self, ctx, cfg):
        """Return the nix executable to use, dying (with install instructions) when there is none."""
        exe = cfg["exe"]
        # dry_fallback: under --dry-run a nix installed by an earlier stage
        # legitimately isn't there yet, and the bare name still renders every
        # command below it (see Context.which).
        found = ctx.which(exe, dry_fallback=True)
        if not found:
            die(
                f"nix[{self.stage}]: needs '{exe}' on PATH -- nothing this stage does works without it. "
                f"Install it with:\n{install_instructions(cfg['expected-version'])}"
            )
        self._check_version(ctx, cfg, found)
        return found

    def _check_version(self, ctx, cfg, exe):
        """Warn when the installed nix isn't exactly 'expected-version:' -- a no-op when none is configured.

        A warning, not an error: a different version is survivable (unlike a
        missing nix, which is fatal above), and an env that pins one is
        saying which version it was tested against, not refusing every other.
        """
        expected = cfg["expected-version"]
        if not expected:
            return
        out = ctx.run([exe, "--version"], capture=True, echo=False, check=False).stdout
        version = parsed_version(out)
        if version != expected:
            warn(
                f"nix[{self.stage}]: nix {version or 'unknown'}, expected {expected}. "
                f"Install it with:\n{install_instructions(expected)}"
            )

    def _ensure_devshell_env(self, ctx, cfg, exe, cache_file):
        """Write this stage's cached ``nix print-dev-env`` output, unless a usable one is already there."""
        if cache_file.is_file() and cfg["cache"] and not ctx.force:
            info(f"nix[{self.stage}]: reusing cached devshell environment {cache_file} (refresh with --force)")
            return
        cmd = self._print_dev_env_cmd(ctx, cfg, exe)
        if ctx.dry_run:
            self._report_dry_run(ctx, cmd, cache_file)
            return
        ctx.mkdir(cache_file.parent)
        tmp = cache_file.with_suffix(".sh.tmp")
        # stdout redirected by the shell rather than captured, so nix's own
        # progress/build log stays on the terminal while a cold evaluation
        # (minutes, on a flake whose inputs aren't in any binary cache)
        # runs -- ctx.run's capture=True would swallow stderr along with it.
        ctx.run(["bash", "-c", f"{shlex.join(str(c) for c in cmd)} > {shlex.quote(str(tmp))}"])
        if not cfg["shell-hook"]:
            self._strip_shell_hook(ctx, tmp)
        # rename rather than write in place: an interrupted (or failed) nix
        # must not leave a half-written environment behind that every later
        # run would then happily source.
        Path(tmp).replace(cache_file)

    @staticmethod
    def _strip_shell_hook(ctx, path):
        """Drop nix's own trailing ``eval "${shellHook:-}"`` from a freshly-written devshell environment ('shell-hook: false').

        Removed from the *cached script*, not undone afterwards: by the time
        the environment has been sourced the hook has already run, and
        whatever it did (printed a banner, waited on a tty, written a file)
        cannot be taken back.

        Rewritten through ctx.write_text rather than Path.write_text, like
        every other file a provider writes: that is the helper that knows
        about --dry-run, and the path it is given has already been confined
        to this env's own cache directory (see _cache_file).
        """
        lines = Path(path).read_text().splitlines(keepends=True)
        ctx.write_text(path, "".join(line for line in lines if not _SHELL_HOOK_LINE_RE.match(line)))

    def _report_dry_run(self, ctx, cmd, cache_file):
        """Report, under --dry-run, that the devShell is not evaluated and what that costs the preview."""
        ctx.dry_note("+", shlex.join(str(c) for c in cmd))
        ctx.dry_note(
            "!",
            f"{self.stage}: {cache_file} is not built in a preview -- "
            f"every command below runs without the devShell's own PATH and variables",
        )

    def _print_dev_env_cmd(self, ctx, cfg, exe):
        """The full ``nix print-dev-env`` argv for this stage, including denver's own -q/-v translation."""
        cmd = [exe]
        if cfg["experimental-features"]:
            cmd += ["--extra-experimental-features", cfg["experimental-features"]]
        if ctx.quiet:
            cmd.append("--quiet")
        if ctx.verbose:
            cmd += ["--print-build-logs", "--log-format", "bar-with-logs"]
        cmd.append("print-dev-env")
        if cfg["impure"]:
            cmd.append("--impure")
        return [*cmd, *cfg["args"], self._installable(cfg)]

    # ---- activation ------------------------------------------------------- #
    def _activate(self, ctx, cfg, cache_file):
        """Source the cached environment (shellHook and all) into ctx.env."""
        if not cache_file.is_file():
            return  # --dry-run only: nothing was evaluated, and _report_dry_run already said so
        banner(ctx, self.stage, "activate")
        previous_path = ctx.env.get("PATH", "")
        ctx.source(cache_file)
        if cfg["keep-path"]:
            self._keep_dropped_path(ctx, previous_path)

    @staticmethod
    def _keep_dropped_path(ctx, previous_path):
        """Re-append PATH entries the devShell's own PATH replaced ('keep-path:', on by default).

        ``print-dev-env`` exports the devShell's PATH outright, which would
        otherwise discard whatever earlier stages put there (a uv venv's
        bin/, a downloaded toolchain). Appended, not prepended: the
        devShell's own tools are the point of this stage and must still win.
        """
        current = ctx.env.get("PATH", "").split(os.pathsep)
        for entry in previous_path.split(os.pathsep):
            if entry and entry not in current:
                ctx.append_path_var("PATH", entry)
                current.append(entry)
