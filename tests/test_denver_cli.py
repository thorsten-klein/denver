"""Tests for denver.py's main() CLI dispatch."""

import stat
import types

import pytest

import denver


def _raise(error):
    """A subprocess.run stand-in that raises ``error`` instead of running anything."""

    def run(*args, **kwargs):
        raise error

    return run


def test_main_no_args_prints_help(capsys):
    assert denver.main([]) == 0
    assert "denver -- Development Environment Launcher" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["--help", "-h"], ids=["long", "short"])
def test_main_help_flag(capsys, flag):
    assert denver.main([flag]) == 0
    assert "usage:" in capsys.readouterr().out


def test_print_logo_prints_asset_contents(capsys):
    denver.print_logo()
    assert capsys.readouterr().err.strip() == denver.LOGO_PATH.read_text().strip()


def test_print_logo_noop_when_asset_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(denver, "LOGO_PATH", tmp_path / "no-such-logo.txt")
    denver.print_logo()
    assert capsys.readouterr().err == ""


def test_main_no_args_shows_logo_banner(capsys):
    denver.main([])
    err = capsys.readouterr().err
    assert err.startswith(denver.LOGO_PATH.read_text().splitlines()[0])


def test_main_version_flag(capsys):
    assert denver.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("denver ")


def test_package_version_from_checkout_tags(monkeypatch):
    # running out of a checkout (the plain script, or an editable install):
    # the tags win over whatever install-time metadata might also exist.
    monkeypatch.setattr(denver, "scm_version", lambda: "1.2.3-4-gabc1234")
    monkeypatch.setattr(denver.importlib.metadata, "version", lambda name: "0.0.1")
    assert denver.package_version() == "1.2.3-4-gabc1234"


def test_package_version_installed(monkeypatch):
    monkeypatch.setattr(denver, "scm_version", lambda: None)
    monkeypatch.setattr(denver.importlib.metadata, "version", lambda name: "1.2.3")
    assert denver.package_version() == "1.2.3"


def test_package_version_not_installed(monkeypatch):
    def raise_not_found(name):
        raise denver.importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(denver, "scm_version", lambda: None)
    monkeypatch.setattr(denver.importlib.metadata, "version", raise_not_found)
    assert denver.package_version() is None


def test_main_version_flag_without_any_version_source(monkeypatch, capsys):
    monkeypatch.setattr(denver, "package_version", lambda: None)
    assert denver.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"denver {denver.UNKNOWN_VERSION}"


def test_main_license_flag(capsys):
    assert denver.main(["--license"]) == 0
    assert "Apache License" in capsys.readouterr().out


def test_license_text_from_checkout(monkeypatch, tmp_path):
    # running out of a checkout (the plain script, or an editable install):
    # the checkout's own LICENSE file wins over installed metadata.
    (tmp_path / "LICENSE").write_text("checkout license\n")

    def _unexpected(name):
        raise AssertionError("installed metadata should not be consulted when a checkout LICENSE exists")

    monkeypatch.setattr(denver, "checkout_root", lambda: tmp_path)
    monkeypatch.setattr(denver.importlib.metadata, "distribution", _unexpected)
    assert denver.license_text() == "checkout license\n"


def test_license_text_installed(monkeypatch):
    monkeypatch.setattr(denver, "checkout_root", lambda: None)
    monkeypatch.setattr(
        denver.importlib.metadata,
        "distribution",
        lambda name: types.SimpleNamespace(read_text=lambda path: "installed license\n"),
    )
    assert denver.license_text() == "installed license\n"


def test_license_text_not_installed(monkeypatch):
    def raise_not_found(name):
        raise denver.importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(denver, "checkout_root", lambda: None)
    monkeypatch.setattr(denver.importlib.metadata, "distribution", raise_not_found)
    assert denver.license_text() is None


def test_main_license_flag_without_any_license_source(monkeypatch):
    monkeypatch.setattr(denver, "license_text", lambda: None)
    with pytest.raises(SystemExit):
        denver.main(["--license"])


def test_scm_version_outside_a_checkout(monkeypatch):
    monkeypatch.setattr(denver, "checkout_root", lambda: None)
    assert denver.scm_version() is None


def _describing(output):
    """Stub subprocess.run so scm_version() sees ``output`` from `git describe`."""
    return lambda *a, **kw: types.SimpleNamespace(returncode=0, stdout=output)


@pytest.mark.parametrize(
    ("described", "expected"),
    [
        # tags have caught up with DEV_VERSION (or passed it): git describe
        # is authoritative and is passed straight through.
        ("9.9.9-2-gabc1234\n", "9.9.9-2-gabc1234"),
        # sitting exactly on a tag: this tree really *is* that release, even
        # an older one than DEV_VERSION -- never re-based.
        ("1.0.3\n", "1.0.3"),
        ("9.9.9\n", "9.9.9"),
        # a tag matching the '*.*.*' glob that isn't a version at all: left
        # alone rather than guessed at.
        ("not.a.version-2-gabc1234\n", "not.a.version-2-gabc1234"),
        # the normal in-development state: tags still name the previous
        # release, so the commit suffix is carried onto DEV_VERSION.
        ("1.0.3-2-gabc1234\n", "8.8.8-2-gabc1234"),
    ],
    ids=["tags-caught-up", "on-an-older-tag", "on-a-newer-tag", "unparseable-tag", "tags-behind"],
)
def test_scm_version_reads_git_describe(monkeypatch, tmp_path, described, expected):
    monkeypatch.setattr(denver, "checkout_root", lambda: tmp_path)
    monkeypatch.setattr(denver, "DEV_VERSION", "8.8.8")
    monkeypatch.setattr(denver.subprocess, "run", _describing(described))
    assert denver.scm_version() == expected


def test_scm_version_without_a_dev_version(monkeypatch, tmp_path):
    """DEV_VERSION = None switches the re-basing off: git describe is reported verbatim."""
    monkeypatch.setattr(denver, "checkout_root", lambda: tmp_path)
    monkeypatch.setattr(denver, "DEV_VERSION", None)
    monkeypatch.setattr(denver.subprocess, "run", _describing("1.0.3-2-gabc1234\n"))
    assert denver.scm_version() == "1.0.3-2-gabc1234"


@pytest.mark.parametrize(
    "outcome",
    [
        lambda *a, **kw: types.SimpleNamespace(returncode=128, stdout=""),  # no tags (e.g. shallow clone)
        lambda *a, **kw: types.SimpleNamespace(returncode=0, stdout="\n"),
        _raise(FileNotFoundError("git")),  # no git binary at all
    ],
    ids=["no-tags", "empty-output", "no-git"],
)
def test_scm_version_falls_back_to_none(monkeypatch, tmp_path, outcome):
    monkeypatch.setattr(denver, "checkout_root", lambda: tmp_path)
    monkeypatch.setattr(denver.subprocess, "run", outcome)
    assert denver.scm_version() is None


def test_main_no_env_given_dies():
    # a flag with no env positional and no help/list/version -- e.g. a typo'd
    # invocation missing the env argument entirely -- must die with a clear
    # message rather than proceeding with env=None.
    with pytest.raises(SystemExit):
        denver.main(["--fast"])


def test_main_denver_version_error_wins_over_unknown_key(tmp_path, monkeypatch, caplog):
    # a file written for a newer denver may well also use a key this denver
    # doesn't know yet -- the version requirement is the message that
    # actually explains that, so it must be the one reported.
    monkeypatch.setattr(denver, "package_version", lambda: "1.0.3")
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text('denver-version: ">=99.0"\nfrom-the-future: true\nstages: [uv]\n')
    with pytest.raises(SystemExit):
        denver.main([str(env_dir), "--show-config"])
    assert ">=99.0" in caplog.text
    assert "unknown top-level key" not in caplog.text


def test_main_dies_without_stages(tmp_path):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("command: fish\n")
    with pytest.raises(SystemExit):
        denver.main([str(env_dir)])


def test_main_reports_a_failing_command_instead_of_a_traceback(tmp_path, monkeypatch, caplog):
    # a provider's subprocess failing is an ordinary outcome, not a denver
    # bug: the user must get the command and its exit status, not a stack of
    # frames whose only content is the last one.
    import denver_providers as providers
    from denver_providers.base import Provider

    class Fake(Provider):
        name = "failer"
        kind = "setup"

        def setup(self, ctx):
            ctx.run(["false-ish", "--flag"])

    monkeypatch.setitem(providers.PROVIDERS, "failer", Fake)
    monkeypatch.setattr(
        denver.subprocess,
        "run",
        _raise(denver.subprocess.CalledProcessError(3, ["false-ish", "--flag"])),
    )
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [failer]\nfailer:\n  provider: failer\n")

    with pytest.raises(SystemExit) as exc:
        denver.main([str(env_dir), "--", "echo", "hi"])

    assert exc.value.code == 1
    assert "command failed (exit 3): false-ish --flag" in caplog.text
    assert "Traceback" not in caplog.text


def test_command_failure_message_appends_captured_output():
    # a capture=True call printed nothing, so the exception is holding the
    # only explanation there is -- both streams, str or bytes.
    error = denver.subprocess.CalledProcessError(1, ["conan", "config", "home"])
    error.stdout = "  \n"  # whitespace only: nothing to report
    error.stderr = b"ERROR: Invalid setting\n"
    message = denver._command_failure_message(error)
    assert message.splitlines() == [
        "command failed (exit 1): conan config home",
        "ERROR: Invalid setting",
    ]


def test_command_failure_message_handles_a_string_command():
    # shell=True calls (e.g. the custom provider's 'cmd:') pass a str, not a list
    error = denver.subprocess.CalledProcessError(2, "exit 2")
    assert denver._command_failure_message(error) == "command failed (exit 2): exit 2"


def test_main_dispatches_to_providers(tmp_path, monkeypatch, exec_recorder, capsys):
    import denver_providers as providers
    from denver_providers.base import Provider

    class Fake(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            ctx.set("RAN", "1")

    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", Fake)

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [fakesetup]\nfakesetup:\n  provider: fakesetup\n")
    denver.main([str(env_dir), "--", "echo", "hi"])
    # logo prints last, right before the command is invoked -- after the
    # stage-finished summary, not before it (see run_stages). Both are
    # denver's own noise, on stderr -- stdout stays reserved for the
    # launched command's real output.
    err = capsys.readouterr().err
    assert err.rstrip("\n").endswith(denver.LOGO_PATH.read_text().rstrip("\n"))
    assert err.index("stage 'fakesetup'") < err.index(denver.LOGO_PATH.read_text().splitlines()[0])
    assert exec_recorder["env"]["RAN"] == "1"
    assert exec_recorder["args"] == ["echo", "hi"]


def test_main_forwarded_command_without_separator_dies(tmp_path):
    # a command to run must be introduced with '--'; without it, denver dies
    # immediately (before resolving the env or loading any config) rather
    # than silently treating it as (part of) the command.
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n")
    with pytest.raises(SystemExit):
        denver.main([str(env_dir), "echo", "hi"])


def test_main_unrecognised_flag_dies_immediately_even_in_show_config_mode(tmp_path):
    # flag parsing stops at the first unrecognised token and forwards
    # everything from there on -- so a mistyped flag (here '--exclude',
    # which isn't a real denver flag) must still be caught, even though
    # --show-config itself never looks at 'forwarded'.
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n")
    with pytest.raises(SystemExit):
        denver.main([str(env_dir), "--show-config", "--exclude=docker", "--help"])


def test_main_skip_flag_disables_wrapper(tmp_path, monkeypatch, exec_recorder):
    import denver_providers as providers
    from denver_providers.base import Provider

    class FakeWrap(Provider):
        name = "fakewrap"
        kind = "wrapper"

        def setup(self, ctx):
            ctx.set("WRAP", "1")

        def wrap(self, ctx, cmd):
            return ["WRAPPED", *cmd]

    class FakeSetup(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            ctx.set("SETUP", "1")

    monkeypatch.setitem(providers.PROVIDERS, "fakewrap", FakeWrap)
    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", FakeSetup)

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text(
        "stages: [fakewrap, fakesetup]\nfakewrap:\n  provider: fakewrap\nfakesetup:\n  provider: fakesetup\n"
    )
    denver.main([str(env_dir), "--skip", "fakewrap", "--", "echo", "hi"])
    # --skip <wrapper stage> excludes it from 'stages:', so it's never
    # active -- its setup did not run, the remaining (setup) stage ran
    # directly on the host, and the command was executed directly (not
    # wrapped).
    assert "WRAP" not in exec_recorder["env"]
    assert exec_recorder["env"]["SETUP"] == "1"
    assert exec_recorder["args"] == ["echo", "hi"]


def test_main_quiet_flag_consumed(tmp_path, monkeypatch, capsys, exec_recorder):
    import denver_providers as providers
    from denver_providers.base import Provider

    class Fake(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            ctx.set("RAN", "1")

    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", Fake)

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [fakesetup]\nfakesetup:\n  provider: fakesetup\n")
    denver.main([str(env_dir), "-q", "--", "echo", "hi"])
    # the command still runs normally...
    assert exec_recorder["args"] == ["echo", "hi"]
    err = capsys.readouterr().err
    # ...the logo is suppressed, but the "stage finished" summary stays
    # visible under a single -q (only -qq silences that too)
    assert "finished in" in err
    assert "▄" not in err  # the block-art wordmark logo itself is still gone
    # a later non-quiet run resets the shared logger/flag for other tests
    import denver_providers.context as ctxmod

    ctxmod.set_quiet(False)


def test_main_fast_flag_consumed(tmp_path, monkeypatch, exec_recorder):
    import denver_providers as providers
    from denver_providers.base import Provider

    class Fake(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            ctx.set("SAW_FAST", "1" if ctx.fast else "0")

    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", Fake)

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [fakesetup]\nfakesetup:\n  provider: fakesetup\n")
    denver.main([str(env_dir), "--fast", "--", "echo", "hi"])
    assert exec_recorder["env"]["SAW_FAST"] == "1"
    assert exec_recorder["args"] == ["echo", "hi"]


def test_main_force_flag_consumed(tmp_path, monkeypatch, exec_recorder):
    import denver_providers as providers
    from denver_providers.base import Provider

    class Fake(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            ctx.set("SAW_FORCE", "1" if ctx.force else "0")

    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", Fake)

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [fakesetup]\nfakesetup:\n  provider: fakesetup\n")
    denver.main([str(env_dir), "--force", "--", "echo", "hi"])
    assert exec_recorder["env"]["SAW_FORCE"] == "1"


def test_main_no_force_flag_ctx_force_false(tmp_path, monkeypatch, exec_recorder):
    import denver_providers as providers
    from denver_providers.base import Provider

    class Fake(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            ctx.set("SAW_FORCE", "1" if ctx.force else "0")

    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", Fake)

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [fakesetup]\nfakesetup:\n  provider: fakesetup\n")
    denver.main([str(env_dir), "--", "echo", "hi"])
    assert exec_recorder["env"]["SAW_FORCE"] == "0"


def test_main_ci_flag_consumed(tmp_path, monkeypatch, exec_recorder):
    import denver_providers as providers
    from denver_providers.base import Provider

    class Fake(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            ctx.set("SAW_CI", "1" if ctx.ci else "0")

    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", Fake)

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [fakesetup]\nfakesetup:\n  provider: fakesetup\n")
    denver.main([str(env_dir), "--ci", "--", "echo", "hi"])
    assert exec_recorder["env"]["SAW_CI"] == "1"


def test_main_real_ci_env_var_does_not_leak_into_ctx_ci(tmp_path, monkeypatch, exec_recorder):
    # ctx.ci must only ever reflect --ci; a real CI=true/ON in the actual
    # process environment (e.g. set by a CI runner itself) must not leak in.
    import denver_providers as providers
    from denver_providers.base import Provider

    class Fake(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            ctx.set("SAW_CI", "1" if ctx.ci else "0")

    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", Fake)
    monkeypatch.setenv("CI", "true")

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [fakesetup]\nfakesetup:\n  provider: fakesetup\n")
    denver.main([str(env_dir), "--", "echo", "hi"])
    assert exec_recorder["env"]["SAW_CI"] == "0"


def test_main_single_q_keeps_banner_visible(tmp_path, monkeypatch, capsys, exec_recorder):
    import denver_providers as providers
    from denver_providers.base import Provider
    from denver_providers.context import banner

    class Fake(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            banner(ctx, self.stage, "visible")

    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", Fake)

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [fakesetup]\nfakesetup:\n  provider: fakesetup\n")
    denver.main([str(env_dir), "-q", "--", "echo", "hi"])
    assert "visible" in capsys.readouterr().err
    # a later non-quiet run resets the shared logger/flag for other tests
    import denver_providers.context as ctxmod

    ctxmod.set_quiet(0)


def test_main_double_q_hides_banner_too(tmp_path, monkeypatch, capsys, exec_recorder):
    import denver_providers as providers
    from denver_providers.base import Provider
    from denver_providers.context import banner

    class Fake(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            banner(ctx, self.stage, "hidden")

    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", Fake)

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [fakesetup]\nfakesetup:\n  provider: fakesetup\n")
    denver.main([str(env_dir), "-qq", "--", "echo", "hi"])
    assert capsys.readouterr().err == ""
    # a later non-quiet run resets the shared logger/flag for other tests
    import denver_providers.context as ctxmod

    ctxmod.set_quiet(0)


@pytest.mark.parametrize("name", ["setup", "login"])
def test_main_run_flag_runs_named_scripts_and_exits(tmp_path, run_recorder, which, exec_recorder, name):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "prep.sh").write_text("#!/bin/bash\n")
    (env_dir / "denver.yml").write_text(f"stages: [uv]\nuv:\n  provider: uv\n  scripts:\n    {name}: [prep.sh]\n")
    assert denver.main([str(env_dir), "--run", name]) == 0
    assert str((env_dir / "prep.sh").resolve()) in run_recorder.commands()[-1]
    # --run never builds/enters the environment
    assert exec_recorder == {}


def test_main_no_config_direction_dies(tmp_path):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    script = env_dir / "devshell.sh"
    script.write_text("#!/bin/bash\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    # a script next to the env is not enough: denver.yml must declare it
    with pytest.raises(SystemExit):
        denver.main([str(env_dir)])


def test_main_show_config_flag(tmp_path, capsys, which):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n  python: '3.12.3'\n")

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("import: [../base]\nuv:\n  requirements: [r.txt]\n")

    assert denver.main([str(env_dir), "--show-config"]) == 0
    out = capsys.readouterr().out
    assert "import" not in out  # the directive itself is dropped, not the data
    printed = denver.yaml.safe_load(out)
    assert printed["stages"] == ["uv"]
    assert printed["uv"]["python"] == "3.12.3"
    assert printed["uv"]["requirements"] == ["r.txt"]
    # provider defaults (PATH lookups, filesystem conventions, static
    # fallbacks) are baked in too -- not just what denver.yml itself sets.
    assert printed["uv"]["uv"] == "/usr/bin/uv"
    assert printed["uv"]["skip-if"] == []
    assert printed["uv"]["link-mode"] == "copy"


def test_main_show_config_key_order(tmp_path, capsys, which):
    # version: first, then the rest of the generic (non-stage) keys
    # alphabetically, then stages:, then each stage's own section in
    # pipeline order -- not a single alphabetical sweep over every key.
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text(
        "version: 1.0\n"
        "stages: [zephyr, uv]\n"
        "command: fish\n"
        "zephyr:\n  provider: custom\n  cmd: echo z\n"
        "uv:\n  provider: custom\n  cmd: echo p\n"
    )

    assert denver.main([str(env_dir), "--show-config"]) == 0
    out = capsys.readouterr().out
    top_level_keys = [line.split(":", 1)[0] for line in out.splitlines() if line and not line.startswith((" ", "-"))]
    assert top_level_keys == ["version", "command", "hooks", "stages", "zephyr", "uv"]


def test_main_show_config_lists_scripts_for_every_stage(tmp_path, capsys, which):
    """'scripts:' is generic (denver.py-level), not provider-specific -- it
    must show up (as null when unset) for every stage, including a provider
    type (custom) with no PROVIDER_DEFAULT_RESOLVERS entry."""
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text(
        "stages: [uv, my-stage]\n"
        "uv:\n  provider: uv\n  scripts:\n    setup: [prep.sh]\n"
        "my-stage:\n  provider: custom\n  cmd: echo hi\n"
    )

    assert denver.main([str(env_dir), "--show-config"]) == 0
    printed = denver.yaml.safe_load(capsys.readouterr().out)
    assert printed["uv"]["scripts"] == {"setup": ["prep.sh"]}
    assert printed["my-stage"]["scripts"] is None


def test_main_show_config_lists_disabled_for_every_stage(tmp_path, capsys, which):
    """'disabled:' is generic too -- defaults to false, shown for every stage."""
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text(
        "stages: [uv, my-stage]\nuv:\n  provider: uv\n  disabled: true\nmy-stage:\n  provider: custom\n  cmd: echo hi\n"
    )

    assert denver.main([str(env_dir), "--show-config"]) == 0
    printed = denver.yaml.safe_load(capsys.readouterr().out)
    assert printed["uv"]["disabled"] is True
    assert printed["my-stage"]["disabled"] is False


def test_main_show_config_disabled_not_a_bool_dies(tmp_path, which):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n  disabled: yes-please\n")
    with pytest.raises(SystemExit):
        denver.main([str(env_dir), "--show-config"])


def test_main_show_config_lists_description_for_every_stage(tmp_path, capsys, which):
    """'description:' is generic too -- a list of strings, null when unset, shown for every stage."""
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text(
        "stages: [uv, my-stage]\n"
        "uv:\n  provider: uv\n  description: ['installs the venv']\n"
        "my-stage:\n  provider: custom\n  cmd: echo hi\n"
    )

    assert denver.main([str(env_dir), "--show-config"]) == 0
    printed = denver.yaml.safe_load(capsys.readouterr().out)
    assert printed["uv"]["description"] == ["installs the venv"]
    assert printed["my-stage"]["description"] is None


def test_main_show_config_description_not_a_list_of_strings_dies(tmp_path, which):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n  description: not-a-list\n")
    with pytest.raises(SystemExit):
        denver.main([str(env_dir), "--show-config"])


def test_main_show_config_resolves_hooks(tmp_path, capsys, which):
    """--show-config must reflect the effective hooks: a list-valued entry, a
    single-string entry, and a name with nothing configured (shown as null).

    An unconfigured hooks/post-uv.sh is written to disk too, to pin down
    that it is *not* discovered."""
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "hooks").mkdir()
    (env_dir / "hooks" / "env.sh").write_text("#!/bin/bash\n")
    (env_dir / "hooks" / "post-uv.sh").write_text("#!/bin/bash\n")
    (env_dir / "pre-uv.sh").write_text("#!/bin/bash\n")
    (env_dir / "denver.yml").write_text(
        "stages: [uv]\nuv:\n  provider: uv\nhooks:\n  env:\n  - hooks/env.sh\n  pre-uv: pre-uv.sh\n"
    )

    assert denver.main([str(env_dir), "--show-config"]) == 0
    printed = denver.yaml.safe_load(capsys.readouterr().out)
    hooks = printed["hooks"]
    assert hooks["env"] == [str((env_dir / "hooks" / "env.sh").resolve())]
    assert hooks["pre-uv"] == [str((env_dir / "pre-uv.sh").resolve())]
    assert hooks["post-uv"] is None
    assert hooks["pre-cmd"] is None


def test_main_show_config_expands_section_stacking(tmp_path, capsys):
    src_env = tmp_path / "src"
    src_env.mkdir()
    (src_env / "denver.yml").write_text(
        "docker:\n  exe: docker\n  default-cmd: dev\n  compose:\n    file: docker-compose.yml\n"
    )

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text(
        "stages: [docker]\ndocker:\n  import: [../src]\n  provider: docker\n  default-cmd: '!override'\n"
    )
    (env_dir / "docker-compose.yml").write_text("services: {}\n")

    assert denver.main([str(env_dir), "--show-config"]) == 0
    printed = denver.yaml.safe_load(capsys.readouterr().out)
    docker = printed["docker"]
    assert docker["exe"] == "docker"
    assert docker["default-cmd"] == "override"  # section-stacking merge result
    # provider defaults baked in too
    assert docker["compose"] == {"file": "docker-compose.yml", "service": "dev", "build": True, "args": None}
    assert docker["run-args"] == ["--rm"]
    assert docker["env-scripts"] is None


def test_main_show_config_skip_drops_stage_and_section(tmp_path, capsys, which):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text(
        "stages: [docker, uv]\n"
        "docker:\n  provider: docker\n  compose:\n    file: docker-compose.yml\n"
        "uv:\n  provider: uv\n"
    )
    (env_dir / "docker-compose.yml").write_text("services: {}\n")

    assert denver.main([str(env_dir), "--show-config", "--skip", "docker"]) == 0
    printed = denver.yaml.safe_load(capsys.readouterr().out)
    assert printed["stages"] == ["uv"]
    assert "docker" not in printed
    assert "pre-docker" not in printed["hooks"]


def test_main_show_config_does_not_start_environment(tmp_path, monkeypatch, exec_recorder):
    import denver_providers as providers
    from denver_providers.base import Provider

    class Fake(Provider):
        name = "fakesetup"
        kind = "setup"

        def setup(self, ctx):
            ctx.set("RAN", "1")

    monkeypatch.setitem(providers.PROVIDERS, "fakesetup", Fake)

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [fakesetup]\nfakesetup:\n  provider: fakesetup\n")
    denver.main([str(env_dir), "--show-config"])
    assert exec_recorder == {}


def test_main_uses_sys_argv_when_no_argv_given(monkeypatch, capsys):
    monkeypatch.setattr(denver.sys, "argv", ["denver", "--help"])
    assert denver.main() == 0
    assert "usage:" in capsys.readouterr().out


def test_main_config_flag_combines_with_show_config(tmp_path, capsys, which):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n  python: '3.9'\n")

    assert denver.main([str(env_dir), "-c", "uv.python=3.12.3", "--show-config"]) == 0
    printed = denver.yaml.safe_load(capsys.readouterr().out)
    assert printed["uv"]["python"] == "3.12.3"


def test_main_config_flag_repeatable_last_wins(tmp_path, capsys, which):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n")

    denver.main([str(env_dir), "-c", "uv.python=3.9", "--config", "uv.python=3.12.3", "--show-config"])
    printed = denver.yaml.safe_load(capsys.readouterr().out)
    assert printed["uv"]["python"] == "3.12.3"


def test_main_config_flag_creates_new_section(tmp_path, capsys, which):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n")

    denver.main([str(env_dir), "-c", "env.FOO=bar", "--show-config"])
    printed = denver.yaml.safe_load(capsys.readouterr().out)
    assert printed["env"] == {"FOO": "bar"}


def test_main_config_file_flag_overlays_denver_yml(tmp_path, capsys, which):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n  python: '3.9'\n")
    overlay = tmp_path / "overlay.yml"
    overlay.write_text("uv:\n  python: '!3.12.3'\n")

    denver.main([str(env_dir), "-cf", str(overlay), "--show-config"])
    printed = denver.yaml.safe_load(capsys.readouterr().out)
    assert printed["uv"]["python"] == "3.12.3"


def test_main_config_file_flag_multiple_applied_in_order(tmp_path, capsys, which):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n")
    first = tmp_path / "first.yml"
    first.write_text("uv:\n  requirements: [a]\n")
    second = tmp_path / "second.yml"
    second.write_text("uv:\n  python: '3.11'\n")

    denver.main([str(env_dir), "--config-file", str(first), "--config-file", str(second), "--show-config"])
    printed = denver.yaml.safe_load(capsys.readouterr().out)
    assert printed["uv"]["requirements"] == ["a"]
    assert printed["uv"]["python"] == "3.11"


def test_main_config_override_wins_over_config_file(tmp_path, capsys, which):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n")
    overlay = tmp_path / "overlay.yml"
    overlay.write_text("uv:\n  python: '3.9'\n")

    denver.main([str(env_dir), "-cf", str(overlay), "-c", "uv.python=3.12.3", "--show-config"])
    printed = denver.yaml.safe_load(capsys.readouterr().out)
    assert printed["uv"]["python"] == "3.12.3"


def test_main_config_flag_missing_argument_dies(tmp_path):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n")
    with pytest.raises(SystemExit):
        denver.main([str(env_dir), "-c"])


def test_main_until_flag_truncates_stages(tmp_path, monkeypatch, exec_recorder):
    import denver_providers as providers
    from denver_providers.base import Provider

    class FakeA(Provider):
        name = "fakea"
        kind = "setup"

        def setup(self, ctx):
            ctx.set("RAN_A", "1")

    class FakeB(Provider):
        name = "fakeb"
        kind = "setup"

        def setup(self, ctx):
            ctx.set("RAN_B", "1")

    monkeypatch.setitem(providers.PROVIDERS, "fakea", FakeA)
    monkeypatch.setitem(providers.PROVIDERS, "fakeb", FakeB)

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text(
        "stages: [fakea, fakeb]\nfakea:\n  provider: fakea\nfakeb:\n  provider: fakeb\n"
    )
    denver.main([str(env_dir), "--until", "fakea", "--", "echo", "hi"])
    # --until keeps every stage up to and including the named one, and drops
    # only what comes after it.
    assert exec_recorder["env"]["RAN_A"] == "1"
    assert "RAN_B" not in exec_recorder["env"]


def test_main_skip_flag_excludes_stage(tmp_path, monkeypatch, exec_recorder):
    import denver_providers as providers
    from denver_providers.base import Provider

    class FakeA(Provider):
        name = "fakea"
        kind = "setup"

        def setup(self, ctx):
            ctx.set("RAN_A", "1")

    class FakeB(Provider):
        name = "fakeb"
        kind = "setup"

        def setup(self, ctx):
            ctx.set("RAN_B", "1")

    monkeypatch.setitem(providers.PROVIDERS, "fakea", FakeA)
    monkeypatch.setitem(providers.PROVIDERS, "fakeb", FakeB)

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text(
        "stages: [fakea, fakeb]\nfakea:\n  provider: fakea\nfakeb:\n  provider: fakeb\n"
    )
    denver.main([str(env_dir), "--skip", "fakea", "--", "echo", "hi"])
    assert "RAN_A" not in exec_recorder["env"]
    assert exec_recorder["env"]["RAN_B"] == "1"


def test_main_skip_flag_accepts_equals_syntax(tmp_path, monkeypatch, exec_recorder):
    import denver_providers as providers
    from denver_providers.base import Provider

    class FakeA(Provider):
        name = "fakea"
        kind = "setup"

        def setup(self, ctx):
            ctx.set("RAN_A", "1")

    class FakeB(Provider):
        name = "fakeb"
        kind = "setup"

        def setup(self, ctx):
            ctx.set("RAN_B", "1")

    monkeypatch.setitem(providers.PROVIDERS, "fakea", FakeA)
    monkeypatch.setitem(providers.PROVIDERS, "fakeb", FakeB)

    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text(
        "stages: [fakea, fakeb]\nfakea:\n  provider: fakea\nfakeb:\n  provider: fakeb\n"
    )
    # '--flag=value' must work exactly like '--flag value'
    denver.main([str(env_dir), "--skip=fakea", "--", "echo", "hi"])
    assert "RAN_A" not in exec_recorder["env"]
    assert exec_recorder["env"]["RAN_B"] == "1"
    assert exec_recorder["args"] == ["echo", "hi"]


def test_main_config_flag_equals_syntax_splits_on_first_equals_only(tmp_path, capsys, which):
    # '-c KEY.PATH=VALUE' has its own '='; '--config=KEY.PATH=VALUE' must
    # split only on the *first* '=', leaving KEY.PATH=VALUE intact.
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n")
    assert denver.main([str(env_dir), "--config=uv.python=3.12.3", "--show-config"]) == 0
    printed = denver.yaml.safe_load(capsys.readouterr().out)
    assert printed["uv"]["python"] == "3.12.3"


def test_main_boolean_flag_with_equals_dies(tmp_path):
    # a flag that never takes a value (e.g. --show-config) must reject
    # '--show-config=foo' instead of silently ignoring the '=foo' part.
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n")
    with pytest.raises(SystemExit):
        denver.main([str(env_dir), "--show-config=foo"])


def test_main_until_flag_unknown_stage_dies(tmp_path):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n")
    with pytest.raises(SystemExit):
        denver.main([str(env_dir), "--until", "typo-stage"])


def test_main_run_without_a_name_lists_them(tmp_path, capsys):
    # --run's names are open-ended, and 'scripts:' stacks across the whole
    # import chain -- so reading one file does not answer "which names?"
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text(
        "stages: [a, b]\n"
        "a:\n  provider: custom\n  cmd: x\n  scripts:\n    setup: [one.sh, two.sh]\n"
        "b:\n  provider: custom\n  cmd: x\n  scripts:\n    setup: [three.sh]\n    login: [l.sh]\n"
    )
    assert denver.main([str(env_dir), "--run"]) == 0
    err = capsys.readouterr().err
    assert "available --run names" in err
    assert "setup" in err
    assert "a (2 scripts)" in err
    assert "b (1 script)" in err
    assert "login" in err


def test_main_run_without_a_name_says_when_there_are_none(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [a]\na:\n  provider: custom\n  cmd: x\n")
    assert denver.main([str(env_dir), "--run"]) == 0
    assert "defines no 'scripts:' entries" in capsys.readouterr().err


def test_main_run_listing_does_not_resolve_provider_defaults(tmp_path, capsys):
    # a listing must not fail over an unrelated missing path: full resolution
    # runs every provider's existence checks, which have nothing to do with
    # which scripts an env declares.
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text(
        "stages: [d]\nd:\n  provider: docker\n  compose:\n    file: no-such-compose.yml\n"
        "  scripts:\n    login: [l.sh]\n"
    )
    assert denver.main([str(env_dir), "--run"]) == 0
    assert "login" in capsys.readouterr().err


def test_main_fast_and_force_together_are_rejected(tmp_path, capsys):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n")
    with pytest.raises(SystemExit) as exc:
        denver.main([str(env_dir), "--fast", "--force"])
    # argparse's own error, not a die(): exit 2, and it names both flags so
    # the message says which pair conflicts.
    assert exc.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--fast", "--force"])
def test_main_fast_or_force_alone_still_parses(flag):
    args = denver.build_arg_parser().parse_args(["some-env", flag])
    assert getattr(args, flag.lstrip("-")) is True


def test_main_unknown_stage_section_key_dies(tmp_path):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("stages: [uv]\nuv:\n  provider: uv\n  pythonn: '3.12.3'\n")
    with pytest.raises(SystemExit):
        denver.main([str(env_dir), "--show-config"])


def test_main_unsupported_config_version_dies(tmp_path):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("version: 2.0\nstages: [uv]\nuv:\n  provider: uv\n")
    with pytest.raises(SystemExit):
        denver.main([str(env_dir), "--show-config"])


def test_main_matching_config_version_ok(tmp_path, capsys, which):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("version: 1.0\nstages: [uv]\nuv:\n  provider: uv\n")
    assert denver.main([str(env_dir), "--show-config"]) == 0


def test_main_runnable_false_dies_when_run_directly(tmp_path):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("runnable: false\nstages: [uv]\nuv:\n  provider: uv\n")
    with pytest.raises(SystemExit):
        denver.main([str(env_dir)])


def test_main_runnable_false_still_allows_show_config(tmp_path, capsys, which):
    env_dir = tmp_path / "e"
    env_dir.mkdir()
    (env_dir / "denver.yml").write_text("runnable: false\nstages: [uv]\nuv:\n  provider: uv\n")
    assert denver.main([str(env_dir), "--show-config"]) == 0
