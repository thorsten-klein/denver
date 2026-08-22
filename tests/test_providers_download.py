"""Tests for providers.download.DownloadProvider."""

from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

import denver_providers.download as download_provider
from denver_providers.download import DownloadProvider

URL = "https://example.invalid/tools/tool-1.0.zip"


# ---- helpers ---------------------------------------------------------------#
def make_zip(files=None, mode=0o755):
    """A zip holding {name: bytes}, every entry carrying ``mode`` as its unix permissions."""
    files = files or {"tool": b"#!/bin/sh\necho tool\n"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, payload in files.items():
            entry = zipfile.ZipInfo(name)
            entry.external_attr = mode << 16
            zf.writestr(entry, payload)
    return buf.getvalue()


def make_tar_gz(files=None):
    """A .tar.gz holding {name: bytes}."""
    files = files or {"bin/tool": b"#!/bin/sh\necho tool\n"}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, payload in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def md5(payload):
    return hashlib.md5(payload).hexdigest()


def config_for(packages, stage="download"):
    return {stage: {"provider": "download", "packages": packages}}


def run_download(config, ctx, stage="download"):
    """Resolve this stage's defaults the way denver.py does, then run its setup()."""
    provider = DownloadProvider(config)
    provider.stage = stage
    config[stage] = DownloadProvider.resolve_defaults(ctx, config.get(stage) or {}, config)
    provider.setup(ctx)
    return provider


def resolved_package(ctx, entry, stage="download"):
    """The single resolved package of a one-package stage."""
    return DownloadProvider.resolve_defaults(ctx, {"packages": [entry]}, {})["packages"][0]


@pytest.fixture
def fake_opener(monkeypatch):
    """Capture the Request the authenticated path builds, and serve canned bytes for it."""
    opened = []

    class _Opener:
        def __init__(self, payload, handlers):
            self._payload = payload
            self.handlers = handlers

        def open(self, request):
            opened.append(request)
            if isinstance(self._payload, Exception):
                raise self._payload
            return io.BytesIO(self._payload)

    def _build_opener(*handlers):
        return _Opener(_build_opener.payload, handlers)

    _build_opener.payload = make_zip()
    _build_opener.opened = opened
    monkeypatch.setattr(download_provider, "build_opener", _build_opener)
    return _build_opener


@pytest.fixture
def fake_urlopen(monkeypatch):
    """Serve canned bytes (or raise) instead of really fetching a url."""
    payloads = {}
    calls = []

    def _urlopen(url, *args, **kwargs):
        calls.append(url)
        payload = payloads.get(url, payloads.get("*"))
        if isinstance(payload, Exception):
            raise payload
        return io.BytesIO(payload)

    monkeypatch.setattr(download_provider, "urlopen", _urlopen)
    _urlopen.payloads = payloads
    _urlopen.calls = calls
    return _urlopen


# ---- config defaults --------------------------------------------------------#
def test_defaults_fill_every_key(make_context):
    ctx = make_context()
    pkg = resolved_package(ctx, {"name": "tool", "url": URL})
    assert pkg["outfile"] == str(ctx.env_workdir / "downloads" / "tool-1.0.zip")
    assert pkg["unpack-dir"] == str(ctx.env_workdir / "download" / "tool")
    assert pkg["env-sep"] == ":"
    assert pkg == {
        **pkg,
        "description": "",
        "sha256sum": "",
        "md5sum": "",
        "unpack-cmd": "",
        "env-prepend": {},
        "env-append": {},
    }


def test_explicit_outfile_lands_in_the_downloads_dir(make_context):
    ctx = make_context()
    pkg = resolved_package(ctx, {"name": "tool", "url": URL, "outfile": "pinned.zip"})
    assert pkg["outfile"] == str(ctx.env_workdir / "downloads" / "pinned.zip")


def test_absolute_outfile_is_kept_as_written(make_context, tmp_path):
    ctx = make_context()
    target = tmp_path / "elsewhere" / "tool.zip"
    pkg = resolved_package(ctx, {"name": "tool", "url": URL, "outfile": str(target)})
    assert pkg["outfile"] == str(target)


def test_explicit_unpack_dir_resolves_against_the_env_dir(make_context):
    ctx = make_context()
    pkg = resolved_package(ctx, {"name": "tool", "url": URL, "unpack-dir": "tools/tool"})
    assert pkg["unpack-dir"] == str(ctx.env_dir / "tools" / "tool")


def test_checksums_are_normalised(make_context):
    ctx = make_context()
    pkg = resolved_package(ctx, {"name": "tool", "url": URL, "sha256sum": "  ABCDEF  ", "md5sum": "FF00"})
    assert pkg["sha256sum"] == "abcdef"
    assert pkg["md5sum"] == "ff00"


def test_url_is_interpolated_before_the_file_name_is_derived(make_context):
    ctx = make_context()
    ctx.env["TOOLS_MIRROR"] = "https://mirror.invalid/x"
    pkg = resolved_package(ctx, {"name": "tool", "url": "${TOOLS_MIRROR}/tool-2.0.tar.gz"})
    assert pkg["url"] == "https://mirror.invalid/x/tool-2.0.tar.gz"
    assert pkg["outfile"].endswith("/downloads/tool-2.0.tar.gz")


# ---- config validation ------------------------------------------------------#
def test_packages_not_a_list_dies(make_context):
    ctx = make_context()
    with pytest.raises(SystemExit):
        DownloadProvider.resolve_defaults(ctx, {"packages": "tool"}, {})


def test_package_entry_not_a_mapping_dies(make_context):
    ctx = make_context()
    with pytest.raises(SystemExit):
        resolved_package(ctx, "tool")


def test_unknown_package_key_dies(make_context):
    ctx = make_context()
    with pytest.raises(SystemExit):
        resolved_package(ctx, {"name": "tool", "url": URL, "unpackdir": "x"})


@pytest.mark.parametrize("entry", [{"url": URL}, {"name": "  ", "url": URL}, {"name": "tool"}, {"name": 7, "url": URL}])
def test_missing_or_blank_required_key_dies(make_context, entry):
    ctx = make_context()
    with pytest.raises(SystemExit):
        resolved_package(ctx, entry)


def test_optional_key_of_the_wrong_type_dies(make_context):
    ctx = make_context()
    with pytest.raises(SystemExit):
        resolved_package(ctx, {"name": "tool", "url": URL, "unpack-cmd": ["tar", "-xf"]})


def test_env_map_not_a_mapping_dies(make_context):
    ctx = make_context()
    with pytest.raises(SystemExit):
        resolved_package(ctx, {"name": "tool", "url": URL, "env-prepend": ["PATH=."]})


def test_env_map_value_not_a_string_dies(make_context):
    ctx = make_context()
    with pytest.raises(SystemExit):
        resolved_package(ctx, {"name": "tool", "url": URL, "env-append": {"PATH": 1}})


def test_duplicate_package_names_die(make_context):
    ctx = make_context()
    with pytest.raises(SystemExit):
        DownloadProvider.resolve_defaults(
            ctx, {"packages": [{"name": "tool", "url": URL}, {"name": "tool", "url": URL}]}, {}
        )


def test_url_without_a_file_name_dies(make_context):
    ctx = make_context()
    with pytest.raises(SystemExit):
        resolved_package(ctx, {"name": "tool", "url": "https://example.invalid/"})


def test_stage_without_packages_dies(make_context):
    config = config_for([])
    ctx = make_context(config=config)
    with pytest.raises(SystemExit):
        run_download(config, ctx)


# ---- the happy path ---------------------------------------------------------#
def test_downloads_unpacks_and_puts_the_package_on_path(make_context, fake_urlopen):
    payload = make_zip()
    fake_urlopen.payloads[URL] = payload
    config = config_for([{"name": "tool", "url": URL, "sha256sum": sha256(payload), "env-prepend": {"PATH": "."}}])
    ctx = make_context(config=config)

    run_download(config, ctx)

    archive = ctx.env_workdir / "downloads" / "tool-1.0.zip"
    unpacked = ctx.env_workdir / "download" / "tool"
    assert archive.read_bytes() == payload
    assert (unpacked / "tool").is_file()
    assert (unpacked / download_provider.STAMP_NAME).is_file()
    assert ctx.env["PATH"].startswith(f"{unpacked}:")


def test_zip_executable_bits_survive_unpacking(make_context, fake_urlopen):
    fake_urlopen.payloads["*"] = make_zip()
    config = config_for([{"name": "tool", "url": URL}])
    ctx = make_context(config=config)

    run_download(config, ctx)

    assert (ctx.env_workdir / "download" / "tool" / "tool").stat().st_mode & 0o111


def test_zip_without_unix_permissions_is_left_alone(make_context, fake_urlopen):
    fake_urlopen.payloads["*"] = make_zip(mode=0)
    config = config_for([{"name": "tool", "url": URL}])
    ctx = make_context(config=config)

    run_download(config, ctx)

    assert not (ctx.env_workdir / "download" / "tool" / "tool").stat().st_mode & 0o111


def test_tar_gz_is_unpacked_too(make_context, fake_urlopen):
    url = "https://example.invalid/tool-1.0.tar.gz"
    fake_urlopen.payloads[url] = make_tar_gz()
    config = config_for([{"name": "tool", "url": url, "env-prepend": {"PATH": "bin"}}])
    ctx = make_context(config=config)

    run_download(config, ctx)

    unpacked = ctx.env_workdir / "download" / "tool"
    assert (unpacked / "bin" / "tool").is_file()
    assert ctx.env["PATH"].startswith(f"{unpacked / 'bin'}:")


def test_a_bare_binary_becomes_the_package_itself(make_context, fake_urlopen):
    url = "https://example.invalid/tool.AppImage"
    fake_urlopen.payloads[url] = b"not an archive"
    config = config_for([{"name": "tool", "url": url}])
    ctx = make_context(config=config)

    run_download(config, ctx)

    binary = ctx.env_workdir / "download" / "tool" / "tool.AppImage"
    assert binary.read_bytes() == b"not an archive"
    assert binary.stat().st_mode & 0o111


# ---- idempotence ------------------------------------------------------------#
def test_second_run_downloads_and_unpacks_nothing(make_context, fake_urlopen):
    payload = make_zip()
    fake_urlopen.payloads[URL] = payload
    config = config_for([{"name": "tool", "url": URL, "sha256sum": sha256(payload)}])
    ctx = make_context(config=config)
    run_download(config, ctx)
    stamped = (ctx.env_workdir / "download" / "tool" / download_provider.STAMP_NAME).stat().st_mtime_ns

    run_download(config, ctx)

    assert fake_urlopen.calls == [URL]
    assert (ctx.env_workdir / "download" / "tool" / download_provider.STAMP_NAME).stat().st_mtime_ns == stamped


def test_force_changes_nothing(make_context, fake_urlopen):
    payload = make_zip()
    fake_urlopen.payloads[URL] = payload
    config = config_for([{"name": "tool", "url": URL}])
    ctx = make_context(config=config)
    run_download(config, ctx)

    forced = make_context(config=config, force=True)
    run_download(config, forced)

    assert fake_urlopen.calls == [URL]


def test_a_bumped_url_re_unpacks_into_the_same_dir(make_context, fake_urlopen):
    fake_urlopen.payloads["*"] = make_zip({"old": b"old"})
    config = config_for([{"name": "tool", "url": URL}])
    ctx = make_context(config=config)
    run_download(config, ctx)

    fake_urlopen.payloads["*"] = make_zip({"new": b"new"})
    config = config_for([{"name": "tool", "url": "https://example.invalid/tools/tool-2.0.zip"}])
    run_download(config, make_context(config=config))

    unpacked = ctx.env_workdir / "download" / "tool"
    assert (unpacked / "new").is_file()
    assert not (unpacked / "old").exists()


def test_an_unfinished_unpack_dir_is_rebuilt(make_context, fake_urlopen):
    fake_urlopen.payloads["*"] = make_zip()
    config = config_for([{"name": "tool", "url": URL}])
    ctx = make_context(config=config)
    unpacked = ctx.env_workdir / "download" / "tool"
    unpacked.mkdir(parents=True)
    (unpacked / "leftover").write_text("half-unpacked")

    run_download(config, ctx)

    assert (unpacked / "tool").is_file()
    assert not (unpacked / "leftover").exists()


# ---- checksums --------------------------------------------------------------#
def test_a_corrupted_archive_is_re_downloaded(make_context, fake_urlopen):
    payload = make_zip()
    fake_urlopen.payloads[URL] = payload
    config = config_for([{"name": "tool", "url": URL, "sha256sum": sha256(payload)}])
    ctx = make_context(config=config)
    archive = ctx.env_workdir / "downloads" / "tool-1.0.zip"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"corrupted")

    run_download(config, ctx)

    assert archive.read_bytes() == payload
    assert fake_urlopen.calls == [URL]


def test_a_download_failing_its_checksum_dies_and_leaves_nothing(make_context, fake_urlopen):
    fake_urlopen.payloads[URL] = make_zip()
    config = config_for([{"name": "tool", "url": URL, "sha256sum": "0" * 64}])
    ctx = make_context(config=config)

    with pytest.raises(SystemExit):
        run_download(config, ctx)

    assert not (ctx.env_workdir / "downloads" / "tool-1.0.zip").exists()


def test_md5sum_is_checked_too(make_context, fake_urlopen):
    payload = make_zip()
    fake_urlopen.payloads[URL] = payload
    config = config_for([{"name": "tool", "url": URL, "md5sum": md5(payload)}])
    ctx = make_context(config=config)

    run_download(config, ctx)

    assert (ctx.env_workdir / "download" / "tool" / "tool").is_file()


# ---- download failures ------------------------------------------------------#
def test_a_failed_transfer_dies_and_leaves_no_part_file(make_context, fake_urlopen):
    fake_urlopen.payloads[URL] = OSError("connection reset")
    config = config_for([{"name": "tool", "url": URL}])
    ctx = make_context(config=config)

    with pytest.raises(SystemExit):
        run_download(config, ctx)

    assert not (ctx.env_workdir / "downloads" / "tool-1.0.zip.part").exists()


def test_a_non_http_url_dies(make_context, fake_urlopen):
    config = config_for([{"name": "tool", "url": "file:///etc/passwd.zip"}])
    ctx = make_context(config=config)

    with pytest.raises(SystemExit):
        run_download(config, ctx)

    assert fake_urlopen.calls == []


def test_a_failing_unpack_leaves_no_unpack_dir_behind(make_context, fake_urlopen, run_recorder):
    fake_urlopen.payloads["*"] = make_zip()
    config = config_for([{"name": "tool", "url": URL, "unpack-cmd": "exit 3"}])
    ctx = make_context(config=config)

    # the unpack command's own failure, surfaced by ctx.run
    with pytest.raises(subprocess.CalledProcessError):
        run_download(config, ctx)

    unpack_root = ctx.env_workdir / "download"
    assert not (unpack_root / "tool").exists()
    assert list(unpack_root.iterdir()) == []


# ---- unpack-cmd -------------------------------------------------------------#
def test_unpack_cmd_replaces_the_built_in_unpacking(make_context, fake_urlopen, run_recorder):
    fake_urlopen.payloads["*"] = make_zip()
    config = config_for([
        {
            "name": "tool",
            "url": URL,
            # writes into the staging dir (its cwd), from the two variables
            # the provider exports
            "unpack-cmd": 'cp "$DENVER_DOWNLOAD_ARCHIVE" "$DENVER_DOWNLOAD_DIR/$DENVER_DOWNLOAD_NAME.copy"',
        }
    ])
    ctx = make_context(config=config)

    run_download(config, ctx)

    unpacked = ctx.env_workdir / "download" / "tool"
    assert (unpacked / "tool.copy").is_file()
    assert not (unpacked / "tool").exists()  # the zip was never extracted


# ---- environment ------------------------------------------------------------#
def test_env_prepend_and_append_use_env_sep(make_context, fake_urlopen):
    fake_urlopen.payloads["*"] = make_zip()
    config = config_for([
        {
            "name": "tool",
            "url": URL,
            "env-sep": ";",
            "env-prepend": {"TOOLPATH": "bin;libexec"},
            "env-append": {"TOOLPATH": "share"},
        }
    ])
    ctx = make_context(config=config)
    ctx.env["TOOLPATH"] = "existing"

    run_download(config, ctx)

    unpacked = ctx.env_workdir / "download" / "tool"
    assert ctx.env["TOOLPATH"] == f"{unpacked / 'bin'};{unpacked / 'libexec'};existing;{unpacked / 'share'}"


def test_absolute_env_entries_are_left_alone(make_context, fake_urlopen):
    fake_urlopen.payloads["*"] = make_zip()
    config = config_for([{"name": "tool", "url": URL, "env-prepend": {"TOOLPATH": "/opt/vendor/bin"}}])
    ctx = make_context(config=config)

    run_download(config, ctx)

    assert ctx.env["TOOLPATH"] == "/opt/vendor/bin"


def test_an_unset_variable_gets_only_the_packages_own_entries(make_context, fake_urlopen):
    fake_urlopen.payloads["*"] = make_zip()
    config = config_for([{"name": "tool", "url": URL, "env-append": {"TOOL_HOME": "."}}])
    ctx = make_context(config=config)

    run_download(config, ctx)

    assert ctx.env["TOOL_HOME"] == str(ctx.env_workdir / "download" / "tool")


# ---- --fast / --dry-run -----------------------------------------------------#
def test_fast_without_an_unpacked_package_dies(make_context, fake_urlopen):
    config = config_for([{"name": "tool", "url": URL}])
    ctx = make_context(config=config, fast=True)

    with pytest.raises(SystemExit):
        run_download(config, ctx)

    assert fake_urlopen.calls == []


def test_fast_only_activates_what_is_already_unpacked(make_context, fake_urlopen):
    fake_urlopen.payloads["*"] = make_zip()
    config = config_for([{"name": "tool", "url": URL, "env-prepend": {"PATH": "."}}])
    run_download(config, make_context(config=config))

    fast = make_context(config=config, fast=True)
    run_download(config, fast)

    assert fake_urlopen.calls == [URL]
    assert fast.env["PATH"].startswith(f"{fast.env_workdir / 'download' / 'tool'}:")


def test_dry_run_fetches_nothing_but_still_applies_the_environment(make_context, fake_urlopen):
    config = config_for([{"name": "tool", "url": URL, "env-prepend": {"PATH": "."}}])
    ctx = make_context(config=config, dry_run=True)

    run_download(config, ctx)

    assert fake_urlopen.calls == []
    assert not (ctx.env_workdir / "downloads").exists()
    assert ctx.env["PATH"].startswith(f"{ctx.env_workdir / 'download' / 'tool'}:")


# ---- banners ---------------------------------------------------------------#
def test_banners_name_the_package_and_the_step(make_context, fake_urlopen, capsys):
    fake_urlopen.payloads["*"] = make_zip()
    config = config_for([{"name": "tool", "url": URL}])
    ctx = make_context(config=config, verbose=True)

    run_download(config, ctx)

    err = capsys.readouterr().err
    assert "tool: download" in err
    assert "tool: unpack" in err


def test_fast_banners_say_what_it_skipped(make_context, fake_urlopen, capsys):
    fake_urlopen.payloads["*"] = make_zip()
    config = config_for([{"name": "tool", "url": URL}])
    run_download(config, make_context(config=config))

    run_download(config, make_context(config=config, fast=True, verbose=True))

    err = capsys.readouterr().err
    assert "tool: download (skipped by --fast)" in err
    assert "tool: activate" in err


# ---- '[[download-auth]]' ----------------------------------------------------#
def auth_config(packages, auth, stage="download"):
    """A config whose download stage is served by the given top-level '[[download-auth]]' entries."""
    return {**config_for(packages, stage=stage), "download-auth": auth}


def sent_headers(fake_opener):
    """The headers of the single request the authenticated path made."""
    return dict(fake_opener.opened[0].headers)


def test_username_and_password_are_sent_as_basic_auth(make_context, fake_opener):
    config = auth_config(
        [{"name": "tool", "url": URL}],
        [{"host": "example.invalid", "username": "${ART_USER}", "password": "${ART_TOKEN}"}],
    )
    ctx = make_context(config=config, env={"ART_USER": "ci", "ART_TOKEN": "s3cret"})

    run_download(config, ctx)

    assert sent_headers(fake_opener)["Authorization"] == download_provider.basic_auth("ci", "s3cret")
    assert fake_opener.opened[0].full_url == URL


def test_headers_are_sent_verbatim(make_context, fake_opener):
    config = auth_config(
        [{"name": "tool", "url": URL}],
        [
            {
                "host": "example.invalid",
                "headers": {"Authorization": "Bearer ${GH_TOKEN}", "Accept": "application/octet-stream"},
            }
        ],
    )
    ctx = make_context(config=config, env={"GH_TOKEN": "ghp_x"})

    run_download(config, ctx)

    headers = sent_headers(fake_opener)
    assert headers["Authorization"] == "Bearer ghp_x"
    assert headers["Accept"] == "application/octet-stream"


def test_explicit_headers_win_over_username_and_password(make_context, fake_opener):
    config = auth_config(
        [{"name": "tool", "url": URL}],
        [{"host": "example.invalid", "username": "ci", "password": "pw", "headers": {"Authorization": "Bearer t"}}],
    )
    ctx = make_context(config=config)

    run_download(config, ctx)

    assert sent_headers(fake_opener)["Authorization"] == "Bearer t"


def test_a_url_on_another_host_is_fetched_unauthenticated(make_context, fake_urlopen, fake_opener):
    fake_urlopen.payloads["*"] = make_zip()
    config = auth_config(
        [{"name": "tool", "url": URL}],
        [{"host": "artifactory.invalid", "username": "ci", "password": "pw"}],
    )
    ctx = make_context(config=config)

    run_download(config, ctx)

    assert fake_urlopen.calls == [URL]
    assert fake_opener.opened == []


def test_the_first_matching_entry_wins(make_context, fake_opener):
    config = auth_config(
        [{"name": "tool", "url": URL}],
        [
            {"host": "example.invalid", "headers": {"Authorization": "Bearer first"}},
            {"host": "example.invalid", "headers": {"Authorization": "Bearer second"}},
        ],
    )
    ctx = make_context(config=config)

    run_download(config, ctx)

    assert sent_headers(fake_opener)["Authorization"] == "Bearer first"


def test_a_host_with_a_port_only_matches_that_port(make_context, fake_urlopen, fake_opener):
    fake_urlopen.payloads["*"] = make_zip()
    auth = [{"host": "example.invalid:8443", "headers": {"Authorization": "Bearer port"}}]

    # the same host on its default port is *not* what this entry names
    default_port = auth_config([{"name": "tool", "url": URL}], auth)
    run_download(default_port, make_context(config=default_port))
    assert fake_opener.opened == []

    on_8443 = auth_config([{"name": "tool", "url": "https://example.invalid:8443/tools/tool-2.0.zip"}], auth)
    run_download(on_8443, make_context(config=on_8443))
    assert sent_headers(fake_opener)["Authorization"] == "Bearer port"


def test_credentials_are_never_baked_into_the_resolved_config(make_context, fake_opener):
    auth = [{"host": "example.invalid", "username": "${ART_USER}", "password": "${ART_TOKEN}"}]
    config = auth_config([{"name": "tool", "url": URL}], auth)
    ctx = make_context(config=config, env={"ART_USER": "ci", "ART_TOKEN": "s3cret"})

    run_download(config, ctx)

    # what --show-config would print: the '${...}' the file was written with
    assert config["download-auth"] == auth


def test_an_unset_credential_variable_dies(make_context, fake_opener):
    config = auth_config(
        [{"name": "tool", "url": URL}],
        [{"host": "example.invalid", "username": "ci", "password": "${ART_TOKEN_NOT_SET}"}],
    )
    ctx = make_context(config=config)

    with pytest.raises(SystemExit):
        run_download(config, ctx)


def test_a_401_names_the_auth_section(make_context, fake_urlopen, caplog):
    fake_urlopen.payloads[URL] = HTTPError(URL, 401, "Unauthorized", {}, None)
    config = config_for([{"name": "tool", "url": URL}])
    ctx = make_context(config=config)

    with pytest.raises(SystemExit):
        run_download(config, ctx)

    assert "download-auth" in caplog.text


def test_a_401_on_a_configured_host_says_it_was_rejected(make_context, fake_opener, caplog):
    fake_opener.payload = HTTPError(URL, 401, "Unauthorized", {}, None)
    config = auth_config(
        [{"name": "tool", "url": URL}],
        [{"host": "example.invalid", "username": "ci", "password": "pw"}],
    )
    ctx = make_context(config=config)

    with pytest.raises(SystemExit):
        run_download(config, ctx)

    assert "was sent and rejected" in caplog.text


# ---- '[[download-auth]]' validation -----------------------------------------#
@pytest.mark.parametrize(
    "auth",
    [
        "artifactory.invalid",  # not a list
        [["host"]],  # not a mapping
        [{"host": "h", "user": "ci", "password": "pw"}],  # unknown key
        [{"username": "ci", "password": "pw"}],  # no host
        [{"host": "  ", "username": "ci", "password": "pw"}],
        [{"host": "h", "username": "ci"}],  # password missing
        [{"host": "h", "password": "pw"}],  # username missing
        [{"host": "h", "username": 7, "password": "pw"}],
        [{"host": "h"}],  # sends nothing
        [{"host": "h", "headers": ["Authorization: Bearer t"]}],
        [{"host": "h", "headers": {"Authorization": 7}}],
    ],
)
def test_a_broken_auth_entry_dies_while_the_config_resolves(make_context, auth):
    ctx = make_context()
    with pytest.raises(SystemExit):
        DownloadProvider.resolve_defaults(ctx, {"packages": [{"name": "tool", "url": URL}]}, {"download-auth": auth})


def test_auth_headers_reach_the_request_only_through_the_opener(make_context, fake_urlopen):
    """A stage with no matching entry keeps the plain urlopen path (nothing to strip, nothing installed)."""
    fake_urlopen.payloads["*"] = make_zip()
    config = config_for([{"name": "tool", "url": URL}])
    ctx = make_context(config=config)

    run_download(config, ctx)

    assert fake_urlopen.calls == [URL]


# ---- redirects ---------------------------------------------------------------#
def redirected(handler, from_url, to_url, headers):
    """The Request ``handler`` would follow a 302 from ``from_url`` to ``to_url`` with."""
    return handler.redirect_request(Request(from_url, headers=headers), None, 302, "Found", {}, to_url)


def test_credentials_do_not_follow_a_redirect_to_another_host():
    handler = download_provider.AuthStrippingRedirectHandler({"Authorization"})

    new = redirected(
        handler, "https://example.invalid/a.zip", "https://cdn.other.invalid/a.zip", {"Authorization": "Bearer t"}
    )

    assert "Authorization" not in new.headers


def test_credentials_follow_a_redirect_within_the_same_host():
    handler = download_provider.AuthStrippingRedirectHandler({"Authorization"})

    new = redirected(
        handler, "https://example.invalid/a.zip", "https://example.invalid/b.zip", {"Authorization": "Bearer t"}
    )

    assert new.headers["Authorization"] == "Bearer t"


def test_credentials_do_not_follow_a_downgrade_to_http():
    handler = download_provider.AuthStrippingRedirectHandler({"Authorization"})

    new = redirected(
        handler, "https://example.invalid/a.zip", "http://example.invalid/a.zip", {"Authorization": "Bearer t"}
    )

    assert "Authorization" not in new.headers


def test_a_non_credential_header_survives_a_cross_host_redirect():
    handler = download_provider.AuthStrippingRedirectHandler({"Authorization"})

    new = redirected(
        handler,
        "https://example.invalid/a.zip",
        "https://cdn.other.invalid/a.zip",
        {"Authorization": "Bearer t", "Accept": "application/octet-stream"},
    )

    assert new.headers["Accept"] == "application/octet-stream"


# ---- module-level helpers ---------------------------------------------------#
def test_restore_exec_bits_ignores_a_non_zip(tmp_path):
    archive = tmp_path / "tool.tar"
    archive.write_bytes(b"not a zip")
    # nothing to do, and nothing to fail on: the dest isn't even looked at
    download_provider.restore_exec_bits(archive, tmp_path / "missing")


def test_absolute_entries_drops_empty_entries():
    assert download_provider.absolute_entries("::bin:", Path("/pkg"), ":") == "/pkg/bin"
