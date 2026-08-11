"""Tests for providers.docker.DockerProvider."""

import sys
from pathlib import Path

import pytest

from providers.docker import DockerProvider


def run_docker(config, ctx, stage="docker"):
    """Resolve ``config[stage]``'s defaults exactly like denver.py's real
    pipeline would (see DockerProvider.resolve_defaults), then run the docker
    stage's setup() against it and return (ctx, provider)."""
    config[stage] = DockerProvider.resolve_defaults(ctx, config.get(stage) or {}, config)
    n = DockerProvider(config)
    n.stage = stage
    ctx.stage_id = stage  # denver.py sets this before setup()/wrap(); mirrored here for ctx.run(step=...)
    n.setup(ctx)
    return ctx, n


def docker_cfg(compose=None, **rest):
    """A minimal *explicit* docker section.

    every test that expects setup() to get as far as compose has to name it.
    """
    section = {"compose": {"file": "docker-compose.yml", **(compose or {})}}
    section.update(rest)
    return section


def write_compose(ctx, name="docker-compose.yml"):
    p = ctx.env_dir / name
    p.write_text("services:\n  dev: {}\n")
    return p


# ---- guard clauses -------------------------------------------------------------#
def test_already_in_docker_dies(make_context):
    config = {"docker": docker_cfg()}
    ctx = make_context(config=config, in_container=True)
    with pytest.raises(SystemExit):
        run_docker(config, ctx)


def test_exe_missing_dies(make_context, which):
    which["docker"] = None
    config = {"docker": docker_cfg()}
    ctx = make_context(config=config)
    with pytest.raises(SystemExit):
        run_docker(config, ctx)


def test_compose_file_missing_dies(make_context, which):
    config = {"docker": docker_cfg()}
    ctx = make_context(config=config)
    with pytest.raises(SystemExit):
        run_docker(config, ctx)


def test_compose_file_unconfigured_dies(make_context, which):
    # 'compose.file:' has no conventional default: even with a
    # docker-compose.yml sitting right next to the denver.yml, an env that
    # doesn't name it is a config error rather than a lucky guess.
    config = {"docker": {}}
    ctx = make_context(config=config)
    write_compose(ctx)
    with pytest.raises(SystemExit):
        run_docker(config, ctx)


# ---- exe / build -----------------------------------------------------------------#
def test_exe_explicit(make_context, run_recorder, which):
    which["docker"] = None
    config = {"docker": docker_cfg(exe="/opt/docker", image="myapp:dev")}
    ctx = make_context(config=config)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()
    run_docker(config, ctx)
    assert any("/opt/docker compose" in c and "build" in c for c in run_recorder.commands())


def test_build_default_true(make_context, run_recorder, which):
    config = {"docker": docker_cfg(image="myapp:dev")}
    ctx = make_context(config=config)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()
    run_docker(config, ctx)
    assert any("build dev" in c for c in run_recorder.commands())


def test_build_never_runs_without_image(make_context, run_recorder, which, capsys):
    # 'compose.build: true' (the default) is a no-op without 'image:' set --
    # there'd be nothing to check next run, so it would rebuild every time;
    # 'docker compose run' itself is left to build on demand instead.
    config = {"docker": docker_cfg()}
    ctx = make_context(config=config, quiet=1)
    write_compose(ctx)
    run_docker(config, ctx)
    assert not any("build dev" in c for c in run_recorder.commands())
    assert "build (skipped: 'image:' is not set)" in capsys.readouterr().err


def test_build_false_skips(make_context, run_recorder, which, capsys):
    config = {"docker": docker_cfg(compose={"build": False})}
    ctx = make_context(config=config, quiet=1)
    write_compose(ctx)
    run_docker(config, ctx)
    assert not any("build dev" in c for c in run_recorder.commands())
    assert "build (skipped: compose.build=false)" in capsys.readouterr().err


def test_fast_has_no_effect_on_build_decision(make_context, run_recorder, which):
    # --fast is not threaded through setup() at all here (unlike
    # uv/conan/zephyr): 'compose.build:' is read exactly as configured, so
    # a real build still runs under --fast, same as without it.
    config = {"docker": docker_cfg(image="myapp:dev")}
    ctx = make_context(config=config, fast=True)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()
    ctx, provider = run_docker(config, ctx)
    assert any("build dev" in c for c in run_recorder.commands())
    # relocation into the (freshly built) container still works as usual
    assert provider.wrap(ctx, ["echo", "hi"])[-2:] == ["echo", "hi"]


def test_compose_args_and_service(make_context, run_recorder, which):
    config = {"docker": docker_cfg(image="myapp:dev", compose={"service": "custom", "args": ["--project-name", "x"]})}
    ctx = make_context(config=config)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()
    run_docker(config, ctx)
    build_argv = next(a for a in run_recorder.argvs() if "build" in a)
    assert build_argv[build_argv.index("--project-name") + 1] == "x"
    assert build_argv[-2:] == ["build", "custom"]


def test_compose_file_list_produces_multiple_dash_f(make_context, run_recorder, which):
    config = {
        "docker": {"image": "myapp:dev", "compose": {"file": ["docker-compose.yml", "docker-compose.override.yml"]}}
    }
    ctx = make_context(config=config)
    write_compose(ctx)
    write_compose(ctx, name="docker-compose.override.yml")
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()
    ctx, n = run_docker(config, ctx)
    build_argv = next(a for a in run_recorder.argvs() if "build" in a)
    assert build_argv.count("-f") == 2
    first = build_argv.index("-f")
    assert build_argv[first + 1] == str(ctx.env_dir / "docker-compose.yml")
    second = build_argv.index("-f", first + 1)
    assert build_argv[second + 1] == str(ctx.env_dir / "docker-compose.override.yml")

    cmd = n.wrap(ctx, ["fish"])
    assert cmd.count("-f") == 2


def test_compose_file_list_missing_file_dies(make_context, which):
    config = {"docker": {"compose": {"file": ["docker-compose.yml", "missing.yml"]}}}
    ctx = make_context(config=config)
    write_compose(ctx)
    with pytest.raises(SystemExit):
        run_docker(config, ctx)


# ---- env-scripts -----------------------------------------------------------------------#
def test_env_script_missing_dies(make_context, run_recorder, which):
    config = {"docker": docker_cfg(**{"env-scripts": ["nope.sh"]})}
    ctx = make_context(config=config)
    write_compose(ctx)
    with pytest.raises(SystemExit):
        run_docker(config, ctx)


@pytest.mark.parametrize("env_scripts", [["gen.sh"], "gen.sh"], ids=["list-form", "single-string"])
def test_env_script_runs(make_context, run_recorder, which, env_scripts):
    config = {"docker": docker_cfg(**{"env-scripts": env_scripts})}
    ctx = make_context(config=config)
    write_compose(ctx)
    script = ctx.env_dir / "gen.sh"
    script.write_text("#!/bin/bash\n")
    script.chmod(0o755)

    run_docker(config, ctx)
    assert any(str(script.resolve()) in c for c in run_recorder.commands())


def test_prepare_banner_shown_before_env_script_output(make_context, run_recorder, which, capsys):
    # the 'prepare' banner must print before an env-script's own '+ cmd'
    # echo/output, not after -- env-scripts used to run unbannered, so
    # their output appeared ahead of the first banner (e.g. create-env.sh).
    config = {"docker": docker_cfg(**{"env-scripts": ["gen.sh"]})}
    ctx = make_context(config=config)
    write_compose(ctx)
    script = ctx.env_dir / "gen.sh"
    script.write_text("#!/bin/bash\n")
    script.chmod(0o755)

    run_docker(config, ctx)

    err = capsys.readouterr().err
    banner_pos = err.index("prepare")
    script_pos = err.index(str(script.resolve()))
    assert banner_pos < script_pos


def test_no_env_scripts_no_op(make_context, run_recorder, which):
    config = {"docker": docker_cfg(image="myapp:dev")}
    ctx = make_context(config=config)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()
    run_docker(config, ctx)  # must not raise despite no env-scripts configured
    # only the image-inspect probe and the build command ran -- nothing env-script-related
    assert len(run_recorder.commands()) == 2
    assert "build" in run_recorder.commands()[-1]


# ---- image / registries ------------------------------------------------------------------#
def test_registries_without_image_is_silently_ignored(make_context, run_recorder, which):
    # without 'image:', 'registries:' is ignored -- and so is the build,
    # since 'compose.build: true' also needs 'image:' to mean anything here.
    config = {"docker": docker_cfg(**{"registries": [{"url": "registry1.example.com"}]})}
    ctx = make_context(config=config)
    write_compose(ctx)

    run_docker(config, ctx)  # must not raise

    assert not any("manifest inspect" in c for c in run_recorder.commands())
    assert not any("build dev" in c for c in run_recorder.commands())


def test_registries_entry_without_url_dies(make_context, run_recorder, which):
    config = {"docker": docker_cfg(image="myapp:dev", **{"registries": [{"username": "u", "password": "p"}]})}
    ctx = make_context(config=config)
    write_compose(ctx)
    with pytest.raises(SystemExit):
        run_docker(config, ctx)


@pytest.mark.parametrize("creds", [{"username": "u"}, {"password": "p"}], ids=["no-password", "no-username"])
def test_registries_entry_incomplete_credentials_dies(make_context, run_recorder, which, creds):
    config = {"docker": docker_cfg(image="myapp:dev", **{"registries": [{"url": "registry1.example.com", **creds}]})}
    ctx = make_context(config=config)
    write_compose(ctx)
    with pytest.raises(SystemExit):
        run_docker(config, ctx)


def test_image_found_locally_skips_build(make_context, run_recorder, which, capsys):
    config = {"docker": docker_cfg(image="myapp:dev", **{"registries": [{"url": "registry1.example.com"}]})}
    ctx = make_context(config=config)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 0})()

    run_docker(config, ctx)

    assert not any("manifest inspect" in c for c in run_recorder.commands())
    assert not any("build dev" in c for c in run_recorder.commands())
    assert ctx.env["DENVER_DOCKER_IMAGE"] == "myapp:dev"  # unchanged: the local tag itself
    assert "found locally, skip build" in capsys.readouterr().err


def test_image_found_locally_skips_build_without_registries(make_context, run_recorder, which):
    # the local check runs whenever 'image:' is set, whether or not
    # 'registries:' is configured at all -- previously it required
    # 'registries:' to be non-empty, which meant a plain 'image:'-only env
    # rebuilt unconditionally every run even with nothing new to build.
    config = {"docker": docker_cfg(image="myapp:dev")}
    ctx = make_context(config=config)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 0})()

    run_docker(config, ctx)

    assert not any("build dev" in c for c in run_recorder.commands())
    assert ctx.env["DENVER_DOCKER_IMAGE"] == "myapp:dev"


def test_uses_first_registry_that_has_it(make_context, run_recorder, which):
    config = {
        "docker": docker_cfg(
            image="myapp:dev",
            **{
                "registries": [
                    {"url": "registry1.example.com"},
                    {"url": "registry2.example.com"},
                    {"url": "registry3.example.com"},
                ]
            },
        )
    }
    ctx = make_context(config=config)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()
    run_recorder.responses["manifest inspect registry1.example.com/myapp:dev"] = lambda cmd: type(
        "R", (), {"returncode": 1}
    )()
    run_recorder.responses["manifest inspect registry2.example.com/myapp:dev"] = lambda cmd: type(
        "R", (), {"returncode": 0}
    )()

    run_docker(config, ctx)

    commands = run_recorder.commands()
    assert any("manifest inspect registry1.example.com/myapp:dev" in c for c in commands)
    assert any("manifest inspect registry2.example.com/myapp:dev" in c for c in commands)
    assert not any("registry3.example.com" in c for c in commands)  # first hit wins, no further entry tried
    assert not any(" pull " in c for c in commands)  # setup() never runs a real pull
    assert not any("build dev" in c for c in commands)
    # $DENVER_DOCKER_IMAGE now points at the hit's own ref -- 'docker compose
    # run' pulls it lazily later, denver itself never does
    assert ctx.env["DENVER_DOCKER_IMAGE"] == "registry2.example.com/myapp:dev"


def test_all_registries_miss_falls_back_to_build(make_context, run_recorder, which):
    config = {
        "docker": docker_cfg(
            image="myapp:dev",
            **{"registries": [{"url": "registry1.example.com"}, {"url": "registry2.example.com"}]},
        )
    }
    ctx = make_context(config=config)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()
    run_recorder.responses["manifest inspect"] = lambda cmd: type("R", (), {"returncode": 1})()

    run_docker(config, ctx)

    assert any("build dev" in c for c in run_recorder.commands())
    assert ctx.env["DENVER_DOCKER_IMAGE"] == "myapp:dev"  # falls back to the local canonical tag


def test_all_registries_miss_and_build_false_dies(make_context, run_recorder, which):
    config = {
        "docker": docker_cfg(
            compose={"build": False}, image="myapp:dev", **{"registries": [{"url": "registry1.example.com"}]}
        )
    }
    ctx = make_context(config=config)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()
    run_recorder.responses["manifest inspect"] = lambda cmd: type("R", (), {"returncode": 1})()

    with pytest.raises(SystemExit):
        run_docker(config, ctx)


def test_fast_still_resolves_registries_but_never_builds(make_context, run_recorder, which):
    # --fast skips the real `docker compose build` invocation, but the
    # local/registries lookup is a cheap read-only check, not a rebuild --
    # it still has to run so $DENVER_DOCKER_IMAGE ends up correct.
    config = {"docker": docker_cfg(image="myapp:dev", **{"registries": [{"url": "registry1.example.com"}]})}
    ctx = make_context(config=config, fast=True)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()
    run_recorder.responses["manifest inspect registry1.example.com/myapp:dev"] = lambda cmd: type(
        "R", (), {"returncode": 0}
    )()

    run_docker(config, ctx)

    assert any("image inspect" in c for c in run_recorder.commands())
    assert any("manifest inspect registry1.example.com/myapp:dev" in c for c in run_recorder.commands())
    assert not any("build dev" in c for c in run_recorder.commands())
    assert ctx.env["DENVER_DOCKER_IMAGE"] == "registry1.example.com/myapp:dev"


def test_fast_still_builds_when_registries_configured_and_nothing_found(make_context, run_recorder, which):
    # --fast has no special case: nothing found locally or on any
    # registry, and compose.build defaults true, so a real build still
    # runs -- exactly like a non-fast run would.
    config = {"docker": docker_cfg(image="myapp:dev", **{"registries": [{"url": "registry1.example.com"}]})}
    ctx = make_context(config=config, fast=True)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()
    run_recorder.responses["manifest inspect"] = lambda cmd: type("R", (), {"returncode": 1})()

    run_docker(config, ctx)

    assert any("build dev" in c for c in run_recorder.commands())


def test_fast_still_dies_when_registries_configured_nothing_found_and_build_false(make_context, run_recorder, which):
    config = {
        "docker": docker_cfg(
            compose={"build": False}, image="myapp:dev", **{"registries": [{"url": "registry1.example.com"}]}
        )
    }
    ctx = make_context(config=config, fast=True)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()
    run_recorder.responses["manifest inspect"] = lambda cmd: type("R", (), {"returncode": 1})()

    with pytest.raises(SystemExit):
        run_docker(config, ctx)


# ---- registries: username/password (automated login) --------------------------------------#
def test_registry_login_runs_before_manifest_check(make_context, run_recorder, which):
    config = {
        "docker": docker_cfg(
            image="myapp:dev",
            **{"registries": [{"url": "registry1.example.com", "username": "myuser", "password": "mysecret"}]},
        )
    }
    ctx = make_context(config=config)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()
    run_recorder.responses["manifest inspect"] = lambda cmd: type("R", (), {"returncode": 0})()

    run_docker(config, ctx)

    commands = run_recorder.commands()
    login_idx = next(i for i, c in enumerate(commands) if "login registry1.example.com" in c)
    manifest_idx = next(i for i, c in enumerate(commands) if "manifest inspect registry1.example.com/myapp:dev" in c)
    assert login_idx < manifest_idx  # login happens before the check it's needed for
    assert "-u myuser" in commands[login_idx]
    assert "--password-stdin" in commands[login_idx]
    # the secret itself never appears in argv/echoed command text -- only via stdin
    assert not any("mysecret" in c for c in commands)
    login_call = run_recorder.calls[login_idx]
    assert login_call.kwargs["input"] == "mysecret"


def test_registry_login_password_from_env_var(make_context, run_recorder, which):
    config = {
        "docker": docker_cfg(
            image="myapp:dev",
            **{
                "registries": [
                    {"url": "registry1.example.com", "username": "myuser", "password": "${DOCKER_PASSWORD_SDD}"}
                ]
            },
        )
    }
    ctx = make_context(config=config, env={"DOCKER_PASSWORD_SDD": "from-env-secret"})
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()

    run_docker(config, ctx)

    login_call = next(c for c in run_recorder.calls if "login" in " ".join(str(p) for p in c.cmd))
    assert login_call.kwargs["input"] == "from-env-secret"


def test_registry_login_failure_dies_without_checking_manifest(make_context, run_recorder, which):
    config = {
        "docker": docker_cfg(
            image="myapp:dev",
            **{"registries": [{"url": "registry1.example.com", "username": "myuser", "password": "mysecret"}]},
        )
    }
    ctx = make_context(config=config)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()
    run_recorder.responses["login registry1.example.com"] = lambda cmd: type("R", (), {"returncode": 1})()

    with pytest.raises(SystemExit):
        run_docker(config, ctx)

    assert not any("manifest inspect" in c for c in run_recorder.commands())


def test_registry_login_skipped_when_not_configured(make_context, run_recorder, which):
    config = {"docker": docker_cfg(image="myapp:dev", **{"registries": [{"url": "registry1.example.com"}]})}
    ctx = make_context(config=config)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()
    run_recorder.responses["manifest inspect registry1.example.com/myapp:dev"] = lambda cmd: type(
        "R", (), {"returncode": 0}
    )()

    run_docker(config, ctx)

    assert not any("login" in c for c in run_recorder.commands())


# ---- --force ----------------------------------------------------------------------------#
def test_force_rebuilds_local_hit_when_no_registries(make_context, run_recorder, which):
    config = {"docker": docker_cfg(image="myapp:dev")}
    ctx = make_context(config=config, force=True)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 0})()

    run_docker(config, ctx)

    assert any("build dev" in c for c in run_recorder.commands())


def test_force_still_prefers_remote_hit_over_local_rebuild(make_context, run_recorder, which):
    config = {"docker": docker_cfg(image="myapp:dev", **{"registries": [{"url": "registry1.example.com"}]})}
    ctx = make_context(config=config, force=True)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 0})()
    run_recorder.responses["manifest inspect registry1.example.com/myapp:dev"] = lambda cmd: type(
        "R", (), {"returncode": 0}
    )()

    run_docker(config, ctx)

    # --force still looks remotely even though the image is already local,
    # since a registry hit should win over a forced local rebuild
    assert any("manifest inspect registry1.example.com/myapp:dev" in c for c in run_recorder.commands())
    assert not any("build dev" in c for c in run_recorder.commands())
    assert ctx.env["DENVER_DOCKER_IMAGE"] == "registry1.example.com/myapp:dev"


def test_force_rebuilds_when_remote_also_misses(make_context, run_recorder, which):
    config = {"docker": docker_cfg(image="myapp:dev", **{"registries": [{"url": "registry1.example.com"}]})}
    ctx = make_context(config=config, force=True)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 0})()
    run_recorder.responses["manifest inspect registry1.example.com/myapp:dev"] = lambda cmd: type(
        "R", (), {"returncode": 1}
    )()

    run_docker(config, ctx)

    assert any("manifest inspect registry1.example.com/myapp:dev" in c for c in run_recorder.commands())
    assert any("build dev" in c for c in run_recorder.commands())
    assert ctx.env["DENVER_DOCKER_IMAGE"] == "myapp:dev"  # remote missed too -- falls back to the local tag


def test_not_forced_skips_remote_check_on_local_hit(make_context, run_recorder, which):
    config = {"docker": docker_cfg(image="myapp:dev", **{"registries": [{"url": "registry1.example.com"}]})}
    ctx = make_context(config=config, force=False)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 0})()

    run_docker(config, ctx)

    assert not any("manifest inspect" in c for c in run_recorder.commands())


def test_denver_docker_image_exported_for_build(make_context, run_recorder, which):
    config = {"docker": docker_cfg(image="myapp:dev")}
    ctx = make_context(config=config)
    write_compose(ctx)
    run_recorder.responses["image inspect"] = lambda cmd: type("R", (), {"returncode": 1})()  # not present locally

    run_docker(config, ctx)

    build_call = next(c for c in run_recorder.calls if "build" in " ".join(str(p) for p in c.cmd))
    assert build_call.kwargs["env"]["DENVER_DOCKER_IMAGE"] == "myapp:dev"


def test_denver_docker_image_empty_when_unset(make_context, run_recorder, which):
    # no build runs without 'image:', so there's no build subprocess call to
    # inspect the env of -- check ctx.env directly instead.
    config = {"docker": docker_cfg()}
    ctx = make_context(config=config)
    write_compose(ctx)

    run_docker(config, ctx)

    assert ctx.env["DENVER_DOCKER_IMAGE"] == ""


def test_denver_docker_image_visible_to_env_scripts(make_context, run_recorder, which):
    config = {"docker": docker_cfg(image="myapp:dev", **{"env-scripts": ["gen.sh"]})}
    ctx = make_context(config=config)
    write_compose(ctx)
    script = ctx.env_dir / "gen.sh"
    script.write_text("#!/bin/bash\n")
    script.chmod(0o755)

    run_docker(config, ctx)

    script_call = next(c for c in run_recorder.calls if "gen.sh" in " ".join(str(p) for p in c.cmd))
    assert script_call.kwargs["env"]["DENVER_DOCKER_IMAGE"] == "myapp:dev"


# ---- wrap() -----------------------------------------------------------------------------#
def test_wrap_before_setup_dies(make_context):
    n = DockerProvider({})
    ctx = make_context()
    with pytest.raises(SystemExit):
        n.wrap(ctx, ["fish"])


def test_wrap_builds_run_command(make_context, run_recorder, which):
    config = {"docker": docker_cfg(compose={"service": "dev"}, **{"run-args": ["--rm", "-it"]})}
    ctx = make_context(config=config)
    write_compose(ctx)
    ctx, n = run_docker(config, ctx)
    cmd = n.wrap(ctx, ["fish", "-l"])
    assert cmd[0] == "docker"
    assert "run" in cmd
    assert "--rm" in cmd
    assert "-it" in cmd
    assert cmd[-2:] == ["fish", "-l"]
    assert cmd[cmd.index("--workdir") + 1] == str(Path.cwd())


def test_wrap_tells_the_inner_denver_it_is_in_a_container(make_context, run_recorder, which):
    # a container's environment comes from the image and the compose file,
    # not from this process, so it has to be handed across explicitly --
    # otherwise the inner denver infers it from a runtime marker file that
    # only docker is guaranteed to write.
    config = {"docker": docker_cfg()}
    ctx = make_context(config=config)
    write_compose(ctx)
    ctx, n = run_docker(config, ctx)
    cmd = n.wrap(ctx, ["fish"])
    assert cmd[cmd.index("-e") + 1] == "DENVER_IN_CONTAINER=1"


def test_wrap_forwards_the_relocating_stage_ids(make_context, run_recorder, which):
    config = {"docker": docker_cfg()}
    ctx = make_context(config=config)
    write_compose(ctx)
    ctx, n = run_docker(config, ctx)
    ctx.set("DENVER_RELOCATED", "docker")
    assert "DENVER_RELOCATED=docker" in n.wrap(ctx, ["fish"])


def test_wrap_shows_run_banner(make_context, run_recorder, which, capsys):
    config = {"docker": docker_cfg()}
    ctx = make_context(config=config, quiet=1)
    write_compose(ctx)
    ctx, n = run_docker(config, ctx)
    capsys.readouterr()  # discard setup()'s own banner

    n.wrap(ctx, ["fish"])

    assert "run" in capsys.readouterr().err


def test_wrap_workdir_is_invocation_cwd_not_image_default(make_context, run_recorder, which, monkeypatch, tmp_path):
    config = {"docker": docker_cfg()}
    ctx = make_context(config=config)
    write_compose(ctx)
    ctx, n = run_docker(config, ctx)
    monkeypatch.chdir(tmp_path)
    cmd = n.wrap(ctx, ["echo"])
    assert cmd[cmd.index("--workdir") + 1] == str(tmp_path)


# ---- relocation mounts ----------------------------------------------------------------------#
def test_wrap_does_not_mount_denver_when_it_runs_from_the_workspace(make_context, run_recorder, which, monkeypatch):
    # the invocation dir is bind-mounted at the same absolute path already
    # (that is what --workdir relies on), so a checkout or an editable install
    # is reachable inside the container without any help
    config = {"docker": docker_cfg()}
    ctx = make_context(config=config)
    write_compose(ctx)
    ctx, n = run_docker(config, ctx)
    monkeypatch.chdir(ctx.denver_pkg_dir.parent)

    assert "-v" not in n.wrap(ctx, ["echo"])

    monkeypatch.chdir(ctx.denver_pkg_dir)
    assert "-v" not in n.wrap(ctx, ["echo"])


def test_wrap_mounts_an_installed_denver_at_its_own_path(make_context, run_recorder, which, monkeypatch, tmp_path):
    # a wheel's site-packages is nowhere near the workspace, so the inner
    # denver would have nothing to re-invoke
    config = {"docker": docker_cfg()}
    ctx = make_context(config=config)
    write_compose(ctx)
    ctx, n = run_docker(config, ctx)
    site_packages = tmp_path / "venv" / "site-packages"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(ctx, "denver_pkg_dir", site_packages)
    monkeypatch.chdir(workspace)

    cmd = n.wrap(ctx, ["echo"])

    assert cmd[cmd.index("-v") + 1] == f"{site_packages}:{site_packages}:ro"


def test_wrap_mounts_the_frozen_executable_itself(make_context, run_recorder, which, monkeypatch, tmp_path):
    config = {"docker": docker_cfg()}
    ctx = make_context(config=config)
    write_compose(ctx)
    ctx, n = run_docker(config, ctx)
    exe = tmp_path / "usr" / "local" / "bin" / "denver"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.chdir(ctx.env_dir)

    cmd = n.wrap(ctx, ["echo"])

    # the executable, not its package dir: a one-file build has no importable
    # tree on disk to mount (see denver.py's reinvoke_command)
    assert cmd[cmd.index("-v") + 1] == f"{exe.resolve()}:{exe.resolve()}:ro"
    # ...and it lands before the service name, as a `docker compose run` flag
    assert cmd.index("-v") < cmd.index("dev")


# ---- UID/GID seeding ------------------------------------------------------------------------#
def test_uid_gid_defaults_not_overridden(make_context, run_recorder, which):
    config = {"docker": docker_cfg(**{"env-scripts": ["${UID}-gen.sh"]})}
    ctx = make_context(config=config, env={"UID": "9999"})
    write_compose(ctx)
    script = ctx.env_dir / "9999-gen.sh"
    script.write_text("#!/bin/bash\n")
    script.chmod(0o755)
    run_docker(config, ctx)
    # UID was already set in the environment, so setdefault must not clobber it
    # before the "docker:" section is interpolated
    assert any(str(script.resolve()) in c for c in run_recorder.commands())
