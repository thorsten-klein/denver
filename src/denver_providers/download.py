"""download provider: brings prebuilt release archives into the environment.

Configured from denver.toml -> a stage declaring ``provider: download``,
with one ``[[<stage>.packages]]`` entry per archive. Each entry is
downloaded once into a persistent ``downloads/`` folder, checksum-verified,
unpacked, and exposed through ``env-prepend:``/``env-append:``.

This is what the ``custom`` provider's "download, checksum, unpack, PATH"
shell script (see doc/providers/custom.md) looks like once it is a provider:
the same four jobs, but idempotent, checksum-verified and ``--fast``-aware
without every project writing that logic again.

A url behind a login is served by the top-level ``[[download-auth]]``
entries -- credentials per *host*, read by every download stage of the
config (see AUTH_SECTION below).

Full key reference, worked examples and design notes: ``doc/providers/download.md``.
"""

from __future__ import annotations

import base64
import hashlib
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

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

# the config key naming each supported checksum, and the hashlib algorithm
# it pins. Both are optional; giving both checks both.
CHECKSUM_KEYS = (("sha256sum", "sha256"), ("md5sum", "md5"))

# the top-level denver.toml key holding the credentials this provider
# sends. Deliberately *not* a stage key: a token belongs to a server, not to
# one stage's package list, so every stage and every package fetching from
# that server is covered by the one entry, written once at the top of the
# config.
AUTH_SECTION = "download-auth"

# every key one '[[download-auth]]' entry understands
AUTH_KEYS = ("host", "username", "password", "headers")

# the response codes whose "cannot fetch" message is really about
# credentials, and gets told so
AUTH_FAILURE_CODES = (401, 403)

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


def extend_var(ctx, key, value, *, prepend):
    """Put ``value`` directly in front of (or behind) whatever ctx.env[key] already holds -- no separator inserted.

    A thin, package-oriented alias for ``ctx.extend_env_var`` (the same
    engine the generic per-stage 'env-prepend:'/'env-append:' keys use --
    see GENERIC_STAGE_KEYS in denver.py): kept here so every call site below
    reads ``extend_var(ctx, ...)`` rather than switching between a free
    function and a method mid-module.
    """
    ctx.extend_env_var(key, value, prepend=prepend)


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


# ---- authenticated downloads ------------------------------------------------ #
def auth_entries(config):
    """Every '[[download-auth]]' entry of the whole denver.toml, validated -- [] when the config declares none."""
    entries = config.get(AUTH_SECTION)
    if entries is None:
        return []
    if not isinstance(entries, list):
        die(f"'{AUTH_SECTION}:' must be a list of entries, got {entries!r}")
    for index, entry in enumerate(entries):
        _validate_auth_entry(entry, f"{AUTH_SECTION}[{index}]")
    return entries


def _validate_auth_entry(entry, where):
    """Die unless one '[[download-auth]]' entry is a mapping this provider fully understands."""
    if not isinstance(entry, dict):
        die(f"{where}: each entry must be a mapping (got {entry!r})")
    unknown = sorted(set(entry) - set(AUTH_KEYS))
    if unknown:
        die(f"{where}: unknown key(s) {', '.join(unknown)} -- known: {', '.join(AUTH_KEYS)}.")
    host = entry.get("host")
    if not isinstance(host, str) or not host.strip():
        die(f"{where}: 'host:' is required and must be a non-empty string (got {host!r})")
    _validate_auth_credentials(entry, where)
    _validate_auth_headers(entry.get("headers"), where)


def _validate_auth_credentials(entry, where):
    """Die unless the entry has both 'username:' and 'password:' (or neither), and something to send at all."""
    for key in ("username", "password"):
        _validate_auth_string(entry.get(key), f"{where}: '{key}:'")
    # one without the other is a mistake, never a half-configured login --
    # the docker provider's 'registries:' rejects the same shape
    if bool(entry.get("username")) != bool(entry.get("password")):
        die(f"{where} ('{entry['host']}'): needs both 'username:' and 'password:', or neither")
    _validate_auth_sends_something(entry, where)


def _validate_auth_string(value, where):
    """Die unless one optional string-valued auth key is a string."""
    if value is not None and not isinstance(value, str):
        die(f"{where} must be a string (got {value!r})")


def _validate_auth_sends_something(entry, where):
    """Die on an entry that carries no credentials at all.

    It would look configured and change nothing: the download still goes
    out bare, and still comes back 401.
    """
    if not entry.get("username") and not entry.get("headers"):
        die(f"{where} ('{entry['host']}'): needs 'username:'/'password:' or 'headers:' -- it sends nothing as written")


def _validate_auth_headers(headers, where):
    """Die unless an entry's 'headers:' is a flat {name = "value"} mapping."""
    if headers is None:
        return
    if not isinstance(headers, dict):
        die(f"{where}: 'headers:' must be a mapping of header name to value (got {headers!r})")
    for name, value in headers.items():
        if not isinstance(value, str):
            die(f"{where}: 'headers:' '{name}:' must be a string (got {value!r})")


def auth_headers_for(config, url, variables):
    """The request headers '[[download-auth]]' adds for ``url`` -- {} when no entry names its host.

    The first matching entry wins and nothing later is merged into it: two
    entries for one host are a config mistake rather than a merge, and
    first-wins keeps a base env's entry the one in force once a derived env
    appends its own (list keys stack across layers, see "Layering" in the
    configuration doc).

    Credential values are interpolated *here*, per fetch, and never in
    resolve_defaults(): what --show-config prints is then the
    '${ARTIFACTORY_TOKEN}' the file was written with, never the token it
    stands for.
    """
    parsed = urlparse(url)
    for entry in auth_entries(config):
        if _host_matches(entry["host"], parsed):
            return _entry_headers(entry, variables)
    return {}


def _host_matches(configured, parsed):
    """Whether one entry's 'host:' names the host ``parsed`` points at.

    Compared case-insensitively against the bare host name -- or against
    'host:port' when the entry itself names a port, so one server reachable
    on two ports can carry different credentials per port.
    """
    wanted = configured.strip().lower()
    host = (parsed.hostname or "").lower()
    if ":" in wanted:
        return wanted == f"{host}:{parsed.port}"
    return wanted == host


def _entry_headers(entry, variables):
    """One matching entry as the headers to send: 'username:'/'password:' as Basic auth, plus its own 'headers:'."""
    where = f"{AUTH_SECTION} ('{entry['host']}')"
    headers = {}
    if entry.get("username"):
        username = auth_value(f"{where} 'username:'", entry["username"], variables)
        password = auth_value(f"{where} 'password:'", entry["password"], variables)
        headers["Authorization"] = basic_auth(username, password)
    # applied last, so an explicit 'Authorization' in 'headers:' (a bearer
    # token, say) is what goes out if an entry writes both
    for name, raw in (entry.get("headers") or {}).items():
        headers[name] = auth_value(f"{where} 'headers:' '{name}:'", raw, variables)
    return headers


def auth_value(where, raw, variables):
    """One credential value, interpolated -- dying if its '${...}' resolved to nothing.

    An unset variable interpolates to the empty string (see "Variable
    interpolation" in the configuration doc), and an empty password would
    have denver send credentials it does not have, for the server to answer
    with a 401 that says nothing about why.
    """
    value = interpolate(raw, variables)
    if raw and not value:
        die(f"{where}: '{raw}' resolves to nothing -- the variable it reads is unset in this environment")
    return value


def basic_auth(username, password):
    """``username``/``password`` as one HTTP Basic 'Authorization' header value."""
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode("ascii")


def auth_hint(exc, url, headers):
    """The tail a 401/403 adds to "cannot fetch", about the credentials it wanted -- '' for any other failure."""
    if not isinstance(exc, HTTPError) or exc.code not in AUTH_FAILURE_CODES:
        return ""
    host = urlparse(url).hostname or url
    if headers:
        return f" -- the '[[{AUTH_SECTION}]]' entry for '{host}' was sent and rejected"
    return f' -- this url needs credentials: add a \'[[{AUTH_SECTION}]]\' entry with host = "{host}"'


class AuthStrippingRedirectHandler(HTTPRedirectHandler):
    """urllib's redirect handling, minus the credentials, once a redirect leaves the host they were written for.

    Release downloads redirect constantly: a github/gitlab/artifactory url
    answers 302 and hands the actual transfer to a CDN or a pre-signed S3
    url. urllib copies every header of the original request onto the
    redirected one, so without this the 'Authorization' header configured
    for artifactory.example.com would be replayed verbatim at whatever
    third-party host it points at. A scheme change (https -> http) counts as
    leaving too -- the same header must not go out unencrypted.
    """

    def __init__(self, header_names):
        """Remember which header names carry credentials: exactly the ones this env configured."""
        super().__init__()
        self._header_names = {name.lower() for name in header_names}

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """The redirected request urllib would make, with every configured auth header dropped off-host."""
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None or _same_origin(req.full_url, newurl):
            return redirected
        # Request stores header names capitalised ('X-jfrog-art-api'), so the
        # match has to be case-insensitive on both sides. Collected before
        # deleting anything: the loop below mutates the dict it matched over.
        configured = [name for name in redirected.headers if name.lower() in self._header_names]
        for name in configured:
            del redirected.headers[name]
        return redirected


def _same_origin(url, other):
    """Whether two urls share scheme, host and port -- i.e. whether credentials may follow the redirect."""
    first, second = urlparse(url), urlparse(other)
    return (first.scheme, first.hostname, first.port) == (second.scheme, second.hostname, second.port)


def open_url(url, headers):
    """Open ``url``, sending ``headers`` if there are any.

    With no credentials configured this is urllib's plain urlopen, exactly
    as before: an opener that drops auth headers across a redirect has
    nothing to drop when none were sent in the first place.
    """
    if not headers:
        # scheme validated by the caller -- file:/ and other local-file schemes are rejected
        return urlopen(url)  # nosec B310
    # scheme validated by the caller, same as the plain path above
    request = Request(url, headers=headers)  # nosec B310
    return build_opener(AuthStrippingRedirectHandler(headers)).open(request)


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
    )

    # ---- config defaults ------------------------------------------------- #
    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):
        """Resolve every 'packages:' entry -- outfile, unpack-dir, checksums, env maps.

        Both paths come out absolute here, so --show-config shows exactly
        which file gets written where, and setup() never has to work out a
        location of its own.
        """
        packages = cfg.get("packages") or []
        if not isinstance(packages, list):
            die(f"download: 'packages:' must be a list of entries, got {packages!r}")
        # the credentials are a top-level section, but this is their only
        # reader -- validating them here means a typo'd entry dies while the
        # config is resolved (--show-config included), not mid-download.
        auth_entries(config)
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
        raw_packages = {p["name"]: p for p in (self.config.get(self.section_name) or {}).get("packages") or []}
        for pkg in packages:
            self._provision(ctx, pkg)
            # always, --fast and --dry-run included: this is the activation
            # half, and a command rendered without it would be missing every
            # tool this stage provides (see custom's 'source:' for the same
            # split between building and activating).
            self._apply_env(ctx, pkg, raw_packages[pkg["name"]])

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
        """Fetch 'url:' to ``archive``, with whatever '[[download-auth]]' configured for its host.

        The transfer goes to a .part file, renamed into place only once it
        is complete, so an interrupted run never leaves a truncated archive
        the next run would accept as downloaded.
        """
        url = pkg["url"]
        if urlparse(url).scheme not in ("http", "https"):
            die(f"download[{self.stage}]: {pkg['name']}: 'url:' must be http(s), got {url!r}")
        if ctx.dry_run:
            ctx.dry_note("~", f"download {url} -> {archive}")
            return
        info(f"download[{self.stage}]: {pkg['name']}: fetching {url}")
        headers = auth_headers_for(ctx.config, url, ctx.variables)
        if headers:
            info(
                f"download[{self.stage}]: {pkg['name']}: with the '{AUTH_SECTION}' credentials for {urlparse(url).hostname}"
            )
        part = archive.with_name(archive.name + ".part")
        try:
            with open_url(url, headers) as response, part.open("wb") as fh:
                shutil.copyfileobj(response, fh)
        except OSError as exc:  # URLError (HTTPError included) and every socket/filesystem failure below it
            part.unlink(missing_ok=True)
            die(f"download[{self.stage}]: {pkg['name']}: cannot fetch {url}: {exc}{auth_hint(exc, url, headers)}")
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
    def _apply_env(self, ctx, pkg, raw_pkg):
        """Fold this package's 'env-prepend:'/'env-append:' entries into ctx.env.

        Interpolated here, against ``raw_pkg`` (this package's own entry out
        of the *resolved-but-not-yet-interpolated* config, see setup()) --
        not against ``pkg``, which config_section() already interpolated
        once, stage-wide, before any package's own 'unpack-dir:' was in
        scope. That's what lets '${DENVER_UNPACK_DIR}' below resolve to
        *this* package's own unpack dir rather than every package in the
        stage sharing whatever one happened to be current first.
        """
        variables = {**ctx.variables, "DENVER_UNPACK_DIR": pkg["unpack-dir"]}
        self._extend_env(ctx, raw_pkg.get("env-prepend") or {}, variables, prepend=True)
        self._extend_env(ctx, raw_pkg.get("env-append") or {}, variables, prepend=False)

    @staticmethod
    def _extend_env(ctx, mapping, variables, *, prepend):
        """Put every entry of one env map in front of (or behind) whatever that variable already holds.

        A value is used exactly as written, once '${...}' interpolated --
        the same rule the generic per-stage 'env-prepend:'/'env-append:'
        keys follow (see GENERIC_STAGE_KEYS in denver.py) -- plus this
        package's own 'unpack-dir:', as '${DENVER_UNPACK_DIR}' (its default
        is '<env workdir>/download/<name>', see doc/providers/download.md).
        """
        for key, value in mapping.items():
            extend_var(ctx, key, interpolate(value, variables), prepend=prepend)
