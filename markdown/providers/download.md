# download provider

A `download` stage brings prebuilt release archives into the environment:
fetch, checksum, unpack, put on `PATH`. It is what the “download a release
tarball by hand” shell script (see the worked example in
[`custom`](custom.md)) looks like once it is a provider — the same four
jobs, but idempotent, checksum-verified and `--fast`-aware without every
project writing that logic again.

```toml
[ninja-setup]
provider = "download"

[[ninja-setup.packages]]
name = "ninja"
url = "https://github.com/ninja-build/ninja/releases/download/v1.13.2/ninja-linux.zip"
sha256sum = "5749cbc4e668273514150a80e387a957f933c6ed3f5f11e03fb30955e2bbead6"
env-prepend = { PATH = "${DENVER_UNPACK_DIR}:" }
```

(`provider:`/`description:`/`disabled:`/`depends-on:`/`scripts:`/`env:`/`env-prepend:`/`env-append:` are generic keys every stage has —
see “Generic stage keys” in [Configuration](../configuration/denver-toml.md). Everything below is specific to `download`.)

## Key reference

The stage has exactly one key of its own:

- **`packages`** — a list of packages, one `[[<stage>.packages]]` table per
  archive. They are processed in the order they are written, each one fully
  (download, unpack, environment) before the next.

### Package keys

- **`name`** (**required**) — this package’s id: it names the default
  unpack dir and prefixes every log line about it. Unique within the stage.
- **`url`** (**required**) — where to fetch the archive from. Must be
  `http(s)`; `${...}` interpolation works (e.g. an internal mirror in
  `[env]`).
- **`outfile`** — the file name to store the archive under, inside the
  downloads folder (default: the file name the `url` ends in). An absolute
  value puts the archive wherever it names instead.
- **`sha256sum`** / **`md5sum`** — the expected checksum of the downloaded
  file. Both optional, both checked when given. Without either, whatever
  the url serves at any given time is trusted — which is exactly what
  pinning a version in the url alone does *not* protect you from.
- **`unpack-dir`** — where the archive is unpacked (default:
  `<env workdir>/download/<name>`). Relative values resolve like every
  other denver path — against the env dir, then imported base envs.
- **`unpack-cmd`** — a shell command that unpacks the archive itself,
  replacing python’s own archive handling. See “Unpacking” below.
- **`env-prepend`** / **`env-append`** — what the unpacked package
  contributes to the environment, as `{ VAR = "value" }`. A value is used
  exactly as written, once `${...}`-interpolated — the same rule the
  generic per-stage `env-prepend:`/`env-append:` keys follow (see “Generic
  stage keys” in [Configuration](../configuration/denver-toml.md)) — plus
  one interpolation variable these don’t have: `${DENVER_UNPACK_DIR}`, this
  package’s own `unpack-dir:` (above). `env-prepend:` glues the result
  directly in front of whatever the variable already holds, `env-append:`
  directly behind it — no separator inserted, so a value that needs one
  (almost always true for a `:`-joined variable like `PATH`) has to carry
  it itself: a trailing `:` for `env-prepend:` (`"${DENVER_UNPACK_DIR}/bin:"`),
  a leading `:` for `env-append:` (`":${DENVER_UNPACK_DIR}/share"`).
- **`description`** — free text about this package, for whoever reads the
  config. denver never acts on it; it only shows up in `--show-config`.

## Authenticated downloads

A url behind a login is served by the top-level `[[download-auth]]` entries —
credentials **per host**, not per package:

```toml
[[download-auth]]
host = "artifactory.example.com"
username = "${ARTIFACTORY_USER}"
password = "${ARTIFACTORY_TOKEN}"

[[download-auth]]
host = "api.github.com"
headers = { Authorization = "Bearer ${GH_TOKEN}", Accept = "application/octet-stream" }
```

They are a top-level key, not a stage key, because a token belongs to a
*server*: one entry covers every package of every `download` stage that
fetches from that host, and a base env can declare the company mirror’s
credentials once for every env importing it.

- **`host`** (**required**) — the host these credentials belong to, matched
  case-insensitively against the url’s own host. Write it as `host:port`
  (e.g. `nexus.example.com:8443`) to match only that port; without a port it
  matches the host on its default port. The first entry matching a url wins,
  and nothing later is merged into it.
- **`username`** / **`password`** — sent as an HTTP Basic `Authorization`
  header. Optional, but if either is set both must be — a half-configured
  login is an error, not a bare request (the same rule `docker:`’s
  `registries:` follows).
- **`headers`** — headers to send verbatim, as `{ Name = "value" }`. This is
  what bearer tokens and vendor-specific schemes need (`Authorization = "Bearer ..."`, `PRIVATE-TOKEN`, `X-JFrog-Art-Api`, …). An `Authorization`
  written here wins over one built from `username:`/`password:`.

An entry that would send nothing at all (no `username:`, no `headers:`) is an
error rather than a silently unauthenticated download.

**Write the secret as `${VAR}`, not as a literal.** Credential values are
interpolated per fetch, at the moment the request is built — never while the
config is resolved — so `--show-config` prints the `${ARTIFACTORY_TOKEN}` the
file was written with and never the token it stands for. A `${VAR}` that is
unset in the environment is a hard error, not an empty password sent to the
server.

**Credentials never follow a redirect off their host.** Release downloads
redirect constantly (a github/gitlab/artifactory url answers `302` and hands
the transfer to a CDN or a pre-signed S3 url), and urllib would otherwise
replay the `Authorization` header written for one host at whatever host it
is pointed at next. denver drops every configured auth header as soon as a
redirect changes scheme, host or port. A `401`/`403` says which
`[[download-auth]]` entry was missing, or that the one it found was
rejected.

## Where things go

```default
<env dir>/.denver/<config stem>/     # the env's workdir (DENVER_ENV_WORKDIR)
├── downloads/                       # the archives -- never deleted by denver
│   └── ninja-linux.zip
└── download/                        # the unpacked packages
    └── ninja/                       # <- unpack-dir: (default) -- env-prepend: above points at it explicitly
        ├── ninja
        └── .denver-download         # what this tree was unpacked from
```

The two tiers are deliberately different in kind. An **archive is expensive
to fetch and cheap to keep**, so nothing in this provider ever deletes one —
`--force` included. An **unpacked tree is the opposite**, so it is rebuilt
from the archive next to it whenever it no longer matches the config.

That is what the `.denver-download` stamp is for: it records the `url`,
`outfile`, checksums and `unpack-cmd` the tree was built from, and it is
written *last*, once unpacking succeeded. A tree whose stamp doesn’t match
the current config (a bumped `url`, a re-downloaded archive, a changed
`unpack-cmd`) is stale and gets rebuilt; a tree with no stamp at all was
never finished and is rebuilt too.

## What runs, and what is skipped

Per package, in order:

1. **Download** — skipped when the archive is already there *and* passes
   its configured checksums. An archive that fails them is deleted and
   fetched again; one that fails them again right after being fetched is a
   hard error, and the bad file is not left behind. The transfer itself
   goes to a `.part` file that is renamed into place only once complete, so
   an interrupted run never leaves a truncated archive that the next run
   would accept.
2. **Unpack** — skipped when the stamp above says this exact package is
   already unpacked there. Otherwise the tree is removed and rebuilt: the
   archive is extracted into a staging dir *next to* `unpack-dir:` and
   moved into place only once extraction succeeded, so a failure can never
   leave a half-unpacked tree behind.
3. **Environment** — `env-prepend:`/`env-append:` are folded into the
   environment. Never skipped: this is the activation half, the thing later
   stages and the final command actually depend on.

## Unpacking

Without `unpack-cmd:`, the archive is unpacked by python itself
(`shutil.unpack_archive`), which picks the format from the file name —
`.zip`, `.tar`, `.tar.gz`/`.tgz`, `.tar.bz2`, `.tar.xz`.

Two things happen on top of that:

- **Executable bits from a zip are restored.** Python’s `zipfile`
  deliberately ignores the unix permissions a zip records, so a release
  like `ninja-linux.zip` — whose whole payload is one executable — would
  otherwise extract as a non-executable file, and the next stage would fail
  with “permission denied”. (tar archives need nothing here; `tarfile`
  restores modes itself.)
- **A download that isn’t an archive at all** — a bare binary, an
  AppImage — becomes the package’s single file, made executable, since that
  is the only thing such a release is ever for. No `unpack-cmd:` needed for
  that case.

`unpack-cmd:` takes over from all of it when the archive needs something
python doesn’t do (a self-extracting installer, a nested archive, an
installer that needs flags). It runs via `bash -c`, with the **staging dir
as its working directory** — whatever it leaves there is what gets moved
into `unpack-dir:`. Three variables name what it is working on:

| variable                  | value                                        |
|---------------------------|----------------------------------------------|
| `DENVER_DOWNLOAD_NAME`    | the package’s `name:`                        |
| `DENVER_DOWNLOAD_ARCHIVE` | absolute path of the downloaded file         |
| `DENVER_DOWNLOAD_DIR`     | absolute path of the staging dir (= the cwd) |
```toml
[[toolchain-setup.packages]]
name = "toolchain"
url = "https://example.invalid/toolchain-1.2.3.tar.gz"
sha256sum = "..."
# strip the archive's own top-level directory, so the paths below are correct
unpack-cmd = 'tar -xzf "$DENVER_DOWNLOAD_ARCHIVE" --strip-components=1'
env-prepend = { PATH = "${DENVER_UNPACK_DIR}/bin:", LD_LIBRARY_PATH = "${DENVER_UNPACK_DIR}/lib:" }
```

## Design notes

- **Why a provider and not a `custom: cmd:` script.** The shell version of
  this (see [`custom`](custom.md)’s worked example) is ~30 lines that every
  project rewrites, and the parts that are easy to get wrong are the ones
  that only bite later: recognising an already-installed release, never
  accepting a truncated download, never leaving a half-unpacked tree that
  the *next* run then treats as finished. Here that logic exists once.
- **Downloads are persistent state, not cache.** They live in the env’s own
  workdir (`DENVER_ENV_WORKDIR`, itself overridable) — next to `.logs/`,
  deleted with the env and with nothing else. They are *not* in the shared
  `DENVER_CACHE_DIR`, which denver only ever points other tools at, never
  writes itself.
- **Checksums are checked on the file, not on the transfer.** An existing
  archive is verified on every run, not only right after it is fetched — a
  file corrupted on disk months later is caught the same way a bad download
  is. That costs one hash of a local file per start; `--fast` skips it
  along with everything else.
- **`env-prepend:` before `env-append:`.** A package’s own `PATH` entry
  almost always wants to be *prepended*: a package exists to provide a
  specific version of a tool, and appending it would let whatever the OS
  happens to ship win instead. `env-append:` is there for the cases where
  the opposite is true (a fallback `MANPATH`, a low-priority
  `CMAKE_PREFIX_PATH`).
- **Credentials are configured per host, once.** Per-package credentials
  would mean the same token repeated in every entry fetching from the same
  server — and repeated again in the next stage, and in the next env that
  imports this one. `[[download-auth]]` is matched against the url a package
  already has, so adding a package behind the same login needs no auth
  config at all. See “Authenticated downloads” above.
- **`--fast`** skips the download and the unpack entirely and only applies
  `env-prepend:`/`env-append:`; it dies with a clear message if a package
  has never been unpacked — run once without `--fast` first.
- **`--force`** does nothing here, deliberately. This provider has no
  expensive computation to redo and no checksum-based skip to bypass: an
  archive that matches its checksum *is* the right archive, and an unpacked
  tree whose stamp matches *is* the right tree. To re-provision a package,
  delete its `unpack-dir:` (or, to re-fetch, its archive).
- **`--dry-run`** reports the download and the unpack instead of performing
  them, and leaves whatever is already on disk untouched. The environment
  is still applied, for the same reason `custom:`’s `source:` still runs:
  every later stage’s commands are rendered against it.
