"""download provider: brings prebuilt release archives into the environment.

Configured from denver.toml -> a stage declaring ``provider: download``,
with one ``[[<stage>.packages]]`` entry per archive. Each entry is
downloaded once into a persistent ``downloads/`` folder, checksum-verified,
unpacked, and exposed through ``env-prepend:``/``env-append:``.

This is what the ``custom`` provider's "download, checksum, unpack, PATH"
shell script (see doc/providers/custom.md) looks like once it is a provider:
the same four jobs, but idempotent, checksum-verified and ``--fast``-aware
without every project writing that logic again.

Full key reference, worked examples and design notes: ``doc/providers/download.md``.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from urllib.request import urlopen

from .base import Provider, fill_unset
from .context import banner, die, info, interpolate, warn

# where the archives themselves live: one folder per env, under
# ctx.env_workdir. Deliberately *not* under the unpack root -- an archive is
# expensive to fetch and cheap to keep, so nothing in this provider ever
# deletes it (--force included, see the module's doc page).
DOWNLOADS_DIRNAME = "downloads"

# the default unpack root, ctx.env_workdir/<this>/<package name>. Cheap to
# rebuild from the archive next to it, so this one *is* wiped and re-made
# whenever the package it holds no longer matches the config.
UNPACK_DIRNAME = "download"

# written last, into the finished unpack dir: both the "this package is
# complete" marker and the record of what it was unpacked from. A tree
# whose stamp doesn't match the current config is stale (a bumped url, a
# re-downloaded archive, a changed unpack-cmd) and gets rebuilt.
STAMP_NAME = ".denver-download"

# separator for one 'env-prepend:'/'env-append:' value's entries, when the
# package doesn't set 'env-sep:' itself.
DEFAULT_ENV_SEP = ":"

# the config key naming each supported checksum, and the hashlib algorithm
# it pins. Both are optional; giving both checks both.
CHECKSUM_KEYS = (("sha256sum", "sha256"), ("md5sum", "md5"))

# read in chunks rather than all at once: these are release archives, and a
# multi-GB toolchain must not have to fit in memory to be verified.
_HASH_CHUNK_SIZE = 1024 * 1024


def file_digest(path, algorithm):
    """The hex digest of ``path`` under ``algorithm`` ('sha256'/'md5')."""
    digest = hashlib.new(algorithm)
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def url_filename(url):
    """The file name a 'url:' ends in -- the default 'outfile:'."""
    name = PurePosixPath(urlparse(url).path).name
    if not name:
        die(f"download: cannot derive a file name from 'url: {url}' -- name the target with 'outfile:'")
    return name


def checksum_mismatch(expected, path, key, algorithm):
    """How ``path`` fails the ``expected`` checksum, as a message -- or None if it matches (or none is pinned)."""
    if not expected:
        return None
    actual = file_digest(path, algorithm)
    return None if actual == expected else f"{key} mismatch (expected {expected}, got {actual})"


def extend_var(ctx, key, value, sep, *, prepend):
    """Put ``value`` in front of (or behind) whatever ctx.env[key] already holds, joined by ``sep``."""
    current = ctx.env.get(key, "")
    parts = [value, current] if prepend else [current, value]
    ctx.set(key, sep.join(part for part in parts if part))


def restore_exec_bits(archive, dest):
    """Re-apply the executable bits a zip recorded, which extracting it drops.

    ``shutil.unpack_archive`` uses ``zipfile``, and zipfile deliberately
    ignores the unix permissions a zip carries in its external attributes --
    so a release like ninja-linux.zip, whose entire payload is one
    executable, extracts as a non-executable file and every later stage
    fails with "permission denied". tarfile has no such problem (it restores
    modes itself), hence zip-only.
    """
    if not zipfile.is_zipfile(archive):
        return
    with zipfile.ZipFile(archive) as zf:
        for entry in zf.infolist():
            # the unix st_mode zip keeps in the high half of external_attr
            # (0 for a zip written on a system without unix permissions --
            # then there is nothing to restore and the entry is skipped)
            mode = entry.external_attr >> 16
            target = Path(dest) / entry.filename
            if mode & 0o111 and target.is_file():
                target.chmod(target.stat().st_mode | 0o111)


def package_text(entry, key, default=""):
    """One optional string key of a 'packages:' entry -- ``default`` when it is unset or empty."""
    return entry.get(key) or default


def package_env_map(entry, key):
    """One 'env-prepend:'/'env-append:' mapping of a 'packages:' entry -- {} when unset."""
    return dict(entry.get(key) or {})


def absolute_entries(value, base, sep):
    """One 'env-prepend:'/'env-append:' value, with every relative entry made absolute against ``base``.

    A value is a ``sep``-joined list of paths *inside* the unpacked package
    ("." for its root, "bin" for its bin/), because that is the only thing a
    package can talk about without knowing where denver put it. Absolute
    entries are left exactly as written, so an author can still point at
    something outside the package.
    """
    entries = [entry for entry in str(value).split(sep) if entry]
    return sep.join(entry if Path(entry).is_absolute() else str(Path(base) / entry) for entry in entries)


class DownloadProvider(Provider):
    """Downloads, verifies and unpacks prebuilt release archives -- see doc/providers/download.md for denver.toml keys."""

    name = "download"
    KEYS = ("packages",)

    #: every key one '[[<stage>.packages]]' entry understands
    PACKAGE_KEYS = (
        "name",
        "description",
        "url",
        "outfile",
        "sha256sum",
        "md5sum",
        "unpack-dir",
        "unpack-cmd",
        "env-sep",
        "env-prepend",
        "env-append",
    )

    #: package keys that must be given, and must be a non-empty string
    REQUIRED_PACKAGE_KEYS = ("name", "url")

    #: package keys that are optional, but must be a string when given
    OPTIONAL_STRING_KEYS = (
        "description",
        "outfile",
        "sha256sum",
        "md5sum",
        "unpack-dir",
        "unpack-cmd",
        "env-sep",
    )

    # ---- config defaults ------------------------------------------------- #
    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):  # noqa: ARG003  # shared (ctx, cfg, config) signature
        """Resolve every 'packages:' entry -- outfile, unpack-dir, checksums, env maps.

        Both paths come out absolute here, so --show-config shows exactly
        which file gets written where, and setup() never has to work out a
        location of its own.
        """
        packages = cfg.get("packages") or []
        if not isinstance(packages, list):
            die(f"download: 'packages:' must be a list of entries, got {packages!r}")
        resolved = dict(cfg)
        resolved["packages"] = [cls._resolve_package(ctx, entry, index=i) for i, entry in enumerate(packages)]
        cls._check_unique_names(resolved["packages"])
        return fill_unset(resolved, cls.KEYS)

    @classmethod
    def _resolve_package(cls, ctx, entry, *, index):
        """One '[[<stage>.packages]]' entry, validated and completed with every default."""
        cls._validate_package(entry, index)
        name = entry["name"]
        # interpolated here rather than left to config_section(): the
        # default 'outfile:' is derived from the url, so a url written as
        # '${TOOLS_MIRROR}/ninja.zip' has to be a real url by then.
        url = interpolate(entry["url"], ctx.variables)
        return {
            "name": name,
            "description": package_text(entry, "description"),
            "url": url,
            "outfile": str(cls._archive_path(ctx, package_text(entry, "outfile"), url)),
            "sha256sum": package_text(entry, "sha256sum").strip().lower(),
            "md5sum": package_text(entry, "md5sum").strip().lower(),
            "unpack-dir": str(cls._unpack_path(ctx, package_text(entry, "unpack-dir"), name)),
            "unpack-cmd": package_text(entry, "unpack-cmd"),
            "env-sep": package_text(entry, "env-sep", DEFAULT_ENV_SEP),
            "env-prepend": package_env_map(entry, "env-prepend"),
            "env-append": package_env_map(entry, "env-append"),
        }

    @staticmethod
    def _archive_path(ctx, outfile, url):
        """Where the archive is stored: 'outfile:' (defaulting to the url's own file name) under the downloads dir.

        Absolute anywhere else if the author writes an absolute 'outfile:' --
        the downloads dir is the default location, not a jail.
        """
        path = Path(interpolate(outfile or url_filename(url), ctx.variables)).expanduser()
        return path if path.is_absolute() else ctx.env_workdir / DOWNLOADS_DIRNAME / path

    @staticmethod
    def _unpack_path(ctx, unpack_dir, name):
        """Where the archive is unpacked: 'unpack-dir:', else <env workdir>/download/<name>."""
        if not unpack_dir:
            return ctx.env_workdir / UNPACK_DIRNAME / name
        return ctx.resolve_path(unpack_dir)

    @staticmethod
    def _check_unique_names(packages):
        """Die on two packages sharing a 'name:' -- they would share one default unpack dir and overwrite each other."""
        seen = set()
        for pkg in packages:
            if pkg["name"] in seen:
                die(f"download: duplicate package name '{pkg['name']}' -- each package needs its own name")
            seen.add(pkg["name"])

    # ---- config validation ------------------------------------------------ #
    @classmethod
    def _validate_package(cls, entry, index):
        """Die unless a 'packages:' entry is a mapping this provider fully understands."""
        where = f"download: packages[{index}]"
        if not isinstance(entry, dict):
            die(f"{where}: each entry must be a mapping (got {entry!r})")
        unknown = sorted(set(entry) - set(cls.PACKAGE_KEYS))
        if unknown:
            die(f"{where}: unknown key(s) {', '.join(unknown)} -- known: {', '.join(cls.PACKAGE_KEYS)}.")
        cls._validate_required_strings(entry, where)
        cls._validate_optional_strings(entry, where)
        for key in ("env-prepend", "env-append"):
            cls._validate_env_map(entry.get(key), f"{where}: '{key}:'")

    @classmethod
    def _validate_required_strings(cls, entry, where):
        """Die unless every required key is there, as a non-empty string."""
        for key in cls.REQUIRED_PACKAGE_KEYS:
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                die(f"{where}: '{key}:' is required and must be a non-empty string (got {value!r})")

    @classmethod
    def _validate_optional_strings(cls, entry, where):
        """Die unless every optional string-valued key that *is* given is a string."""
        for key in cls.OPTIONAL_STRING_KEYS:
            value = entry.get(key)
            if value is not None and not isinstance(value, str):
                die(f"{where}: '{key}:' must be a string (got {value!r})")

    @staticmethod
    def _validate_env_map(mapping, where):
        """Die unless an 'env-prepend:'/'env-append:' section is a flat {name = "value"} mapping."""
        if mapping is None:
            return
        if not isinstance(mapping, dict):
            die(f"{where} must be a mapping of environment variable to value (got {mapping!r})")
        for key, value in mapping.items():
            if not isinstance(value, str):
                die(f"{where} '{key}:' must be a string (got {value!r})")

    # ---- lifecycle --------------------------------------------------------- #
    def setup(self, ctx):
        """Provision every package, then fold each one's env entries into ctx.env."""
        cfg = self.config_section(ctx)
        packages = cfg.get("packages") or []
        if not packages:
            die(f"download[{self.stage}]: needs at least one '[[{self.stage}.packages]]' entry")
        for pkg in packages:
            self._provision(ctx, pkg)
            # always, --fast and --dry-run included: this is the activation
            # half, and a command rendered without it would be missing every
            # tool this stage provides (see custom's 'source:' for the same
            # split between building and activating).
            self._apply_env(ctx, pkg)

    def _provision(self, ctx, pkg):
        """Make sure one package's archive is downloaded, verified and unpacked."""
        if ctx.fast:
            self._check_fast(ctx, pkg)
            return
        archive = Path(pkg["outfile"])
        self._ensure_archive(ctx, pkg, archive)
        self._ensure_unpacked(ctx, pkg, archive)

    def _check_fast(self, ctx, pkg):
        """Under --fast: skip download and unpack entirely, and die if there is nothing to activate yet."""
        banner(ctx, self.stage, f"{pkg['name']}: download (skipped by --fast)")
        banner(ctx, self.stage, f"{pkg['name']}: unpack (skipped by --fast)")
        stamp = Path(pkg["unpack-dir"]) / STAMP_NAME
        if not stamp.is_file():
            die(
                f"download[{self.stage}]: --fast needs '{pkg['name']}' already unpacked at "
                f"{pkg['unpack-dir']} -- run once without --fast first"
            )
        banner(ctx, self.stage, f"{pkg['name']}: activate")

    # ---- download ----------------------------------------------------------- #
    def _ensure_archive(self, ctx, pkg, archive):
        """Download the archive unless a matching one is already there, and verify whatever ends up on disk."""
        banner(ctx, self.stage, f"{pkg['name']}: download")
        ctx.mkdir(archive.parent)
        if archive.is_file():
            mismatch = self._checksum_mismatch(pkg, archive)
            if not mismatch:
                info(f"download[{self.stage}]: {pkg['name']}: already downloaded: {archive}")
                return
            # a truncated or tampered-with archive must never survive as
            # "already downloaded" -- drop it and fetch it again.
            warn(f"download[{self.stage}]: {pkg['name']}: {mismatch} -- re-downloading")
            ctx.unlink(archive)
        self._download(ctx, pkg, archive)
        if ctx.dry_run:
            return  # nothing was fetched, so there is nothing to verify
        mismatch = self._checksum_mismatch(pkg, archive)
        if mismatch:
            ctx.unlink(archive)
            die(f"download[{self.stage}]: {pkg['name']}: {mismatch} -- {pkg['url']}")

    @staticmethod
    def _checksum_mismatch(pkg, archive):
        """The first configured checksum ``archive`` fails, as a message -- or None if it passes them all."""
        for key, algorithm in CHECKSUM_KEYS:
            message = checksum_mismatch(pkg[key], archive, key, algorithm)
            if message:
                return message
        return None

    def _download(self, ctx, pkg, archive):
        """Fetch 'url:' to ``archive``, via a .part file so an interrupted transfer is never mistaken for a complete one."""
        url = pkg["url"]
        if urlparse(url).scheme not in ("http", "https"):
            die(f"download[{self.stage}]: {pkg['name']}: 'url:' must be http(s), got {url!r}")
        if ctx.dry_run:
            ctx.dry_note("~", f"download {url} -> {archive}")
            return
        info(f"download[{self.stage}]: {pkg['name']}: fetching {url}")
        part = archive.with_name(archive.name + ".part")
        try:
            # scheme validated above -- file:/ and other local-file schemes are rejected
            with urlopen(url) as response, part.open("wb") as fh:  # nosec B310
                shutil.copyfileobj(response, fh)
        except OSError as exc:  # URLError and every socket/filesystem failure below it
            part.unlink(missing_ok=True)
            die(f"download[{self.stage}]: {pkg['name']}: cannot fetch {url}: {exc}")
        part.replace(archive)

    # ---- unpack -------------------------------------------------------------- #
    def _ensure_unpacked(self, ctx, pkg, archive):
        """Unpack the archive unless the existing tree was already built from exactly this package."""
        banner(ctx, self.stage, f"{pkg['name']}: unpack")
        dest = Path(pkg["unpack-dir"])
        stamp = self._stamp_text(pkg)
        if (dest / STAMP_NAME).is_file() and (dest / STAMP_NAME).read_text() == stamp:
            info(f"download[{self.stage}]: {pkg['name']}: already unpacked: {dest}")
            return
        if ctx.dry_run:
            ctx.dry_note("~", f"unpack {archive} -> {dest}")
            return
        info(f"download[{self.stage}]: {pkg['name']}: unpacking to {dest}")
        self._unpack(ctx, pkg, archive, dest)
        # written last, so a tree only counts as complete once it is
        # (see STAMP_NAME)
        (dest / STAMP_NAME).write_text(stamp)

    @staticmethod
    def _stamp_text(pkg):
        """What the unpacked tree records about its origin -- change any of it and the tree is rebuilt."""
        return "\n".join(f"{key}: {pkg[key]}" for key in ("url", "outfile", "sha256sum", "md5sum", "unpack-cmd"))

    def _unpack(self, ctx, pkg, archive, dest):
        """Extract into a staging dir next to ``dest`` and move it into place only once it is complete.

        A half-extracted tree at ``dest`` would otherwise be stamped on the
        next run's terms or, worse, used as-is -- so nothing is ever
        extracted into the final location directly.
        """
        ctx.rmtree(dest)
        ctx.mkdir(dest.parent)
        staging = Path(tempfile.mkdtemp(prefix=f".{dest.name}.", dir=dest.parent))
        try:
            self._extract(ctx, pkg, archive, staging)
            staging.replace(dest)
        finally:
            # a no-op once the move succeeded; the cleanup that matters is
            # the failure path, including die()'s SystemExit
            shutil.rmtree(staging, ignore_errors=True)

    def _extract(self, ctx, pkg, archive, staging):
        """Unpack ``archive`` into ``staging``: 'unpack-cmd:' if given, else python's own format handling."""
        if pkg["unpack-cmd"]:
            self._run_unpack_cmd(ctx, pkg, archive, staging)
            return
        try:
            shutil.unpack_archive(archive, staging)
        except shutil.ReadError:
            # not an archive python recognises: a bare binary release (a
            # single executable, an AppImage). The download *is* the payload,
            # so it becomes the package's one file -- executable, since that
            # is the only thing such a release is ever for.
            target = staging / archive.name
            shutil.copyfile(archive, target)
            target.chmod(0o755)
            return
        restore_exec_bits(archive, staging)

    def _run_unpack_cmd(self, ctx, pkg, archive, staging):
        """Run 'unpack-cmd:' via bash -c, with the staging dir as cwd and the archive named in the environment."""
        info(f"download[{self.stage}]: {pkg['name']}: unpack cmd: {pkg['unpack-cmd']}")
        ctx.run(
            ["bash", "-c", pkg["unpack-cmd"]],
            cwd=staging,
            extra_env={
                "DENVER_DOWNLOAD_NAME": pkg["name"],
                "DENVER_DOWNLOAD_ARCHIVE": str(archive),
                # the staging dir, not the final one: what the command
                # writes here is what gets moved into 'unpack-dir:'
                "DENVER_DOWNLOAD_DIR": str(staging),
            },
        )

    # ---- activation ------------------------------------------------------------ #
    def _apply_env(self, ctx, pkg):
        """Fold this package's 'env-prepend:'/'env-append:' entries into ctx.env."""
        dest = Path(pkg["unpack-dir"])
        sep = pkg["env-sep"]
        self._extend_env(ctx, pkg["env-prepend"], dest, sep, prepend=True)
        self._extend_env(ctx, pkg["env-append"], dest, sep, prepend=False)

    @staticmethod
    def _extend_env(ctx, mapping, dest, sep, *, prepend):
        """Put every entry of one env map in front of (or behind) whatever that variable already holds."""
        for key, value in mapping.items():
            extend_var(ctx, key, absolute_entries(value, dest, sep), sep, prepend=prepend)
