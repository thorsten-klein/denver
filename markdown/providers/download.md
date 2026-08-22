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
env-prepend = { PATH = "." }
```

(`provider:`/`description:`/`disabled:`/`scripts:` are generic keys every stage has —
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
- **`env-sep`** — the separator used to split every `env-prepend:` /
  `env-append:` *value* into entries, and to join the result onto the
  variable’s current value (default: `:`).
- **`env-prepend`** / **`env-append`** — what the unpacked package
  contributes to the environment, as `{ VAR = "value" }`. Each value is a
  list of paths *inside* the package, joined by `env-sep:` — `"."` is the
  package root, `"bin"` its `bin/`, `"bin:libexec"` both. Every relative
  entry is made absolute against `unpack-dir:` before it lands in the
  variable; absolute entries are left as written. `env-prepend:` puts them
  in front of whatever the variable already holds, `env-append:` behind it.
- **`description`** — free text about this package, for whoever reads the
  config. denver never acts on it; it only shows up in `--show-config`.

## Where things go

```default
<env dir>/.denver/<config stem>/     # the env's workdir (DENVER_ENV_WORKDIR)
├── downloads/                       # the archives -- never deleted by denver
│   └── ninja-linux.zip
└── download/                        # the unpacked packages
    └── ninja/                       # <- unpack-dir:, what env-prepend: points into
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
# strip the archive's own top-level directory, so 'bin' below is really its bin/
unpack-cmd = 'tar -xzf "$DENVER_DOWNLOAD_ARCHIVE" --strip-components=1'
env-prepend = { PATH = "bin", LD_LIBRARY_PATH = "lib" }
```

## Design notes

- **Why a provider and not a `custom: cmd:` script.** The shell version of
  this (see [`custom`](custom.md)’s worked example) is ~30 lines that every
  project rewrites, and the parts that are easy to get wrong are the ones
  that only bite later: recognising an already-installed release, never
  accepting a truncated download, never leaving a half-unpacked tree that
  the *next* run then treats as finished. Here that logic exists once.
- **Downloads are persistent state, not cache.** They live in the env’s own
  workdir (`DENVER_ENV_WORKDIR`, honouring a `DENVER_STATE_DIR` override) —
  next to `.logs/`, deleted with the env and with nothing else. They are
  *not* in the shared `DENVER_CACHE_DIR`, which denver only ever points
  other tools at, never writes itself.
- **Checksums are checked on the file, not on the transfer.** An existing
  archive is verified on every run, not only right after it is fetched — a
  file corrupted on disk months later is caught the same way a bad download
  is. That costs one hash of a local file per start; `--fast` skips it
  along with everything else.
- **`env-prepend:` before `env-append:`.** `PATH = "."` almost always wants
  to be *prepended*: a package exists to provide a specific version of a
  tool, and appending it would let whatever the OS happens to ship win
  instead. `env-append:` is there for the cases where the opposite is true
  (a fallback `MANPATH`, a low-priority `CMAKE_PREFIX_PATH`).
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
