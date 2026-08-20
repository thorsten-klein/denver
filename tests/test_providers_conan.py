"""Tests for providers.conan.ConanProvider."""

import pytest

from denver_providers.conan import ConanProvider


def run_conan(config, ctx, stage="conan"):
    """Resolve ``config[stage]``'s defaults exactly like denver.py's real
    pipeline would (see ConanProvider.resolve_defaults), then run the conan
    stage's setup() against it and return ctx."""
    config[stage] = ConanProvider.resolve_defaults(ctx, config.get(stage) or {}, config)
    n = ConanProvider(config)
    n.stage = stage
    ctx.stage_id = stage  # denver.py sets this before setup(); mirrored here for ctx.run(step=...)
    n.setup(ctx)
    return ctx


def _ensure_default_conanfile(ctx, config, recipe=None):
    """Create *and explicitly configure* a conan/conanfile.py conanfile unless the test names its own.

    a test that wants `conan install` to run has to name a conanfile.
    ``conanfile:`` and ``recipes:`` are independent (see conan.py's module
    docstring) -- ``recipe``, if given, is appended as its own 'recipes:'
    entry (dirs:/catalog:/export-tool:), not tied to the conanfile at all.
    """
    conan_cfg = config.setdefault("conan", {})
    if not conan_cfg.get("conanfile"):
        (ctx.env_dir / "conan").mkdir(parents=True, exist_ok=True)
        conanfile = ctx.env_dir / "conan" / "conanfile.py"
        if not conanfile.exists():
            conanfile.write_text("x\n")
        conan_cfg["conanfile"] = "conan/conanfile.py"
    if recipe is not None:
        conan_cfg.setdefault("recipes", []).append(recipe)


def _flag_values(argv, flag):
    """Every value directly following ``flag`` in ``argv``, in order."""
    return [argv[i + 1] for i, word in enumerate(argv) if word == flag]


def _argv_with(run_recorder, word):
    """The first recorded argv containing ``word``."""
    return next(a for a in run_recorder.argvs() if word in a)


def default_profile_ok(run_recorder, home="/home/dev/.conan2"):
    """conan config home + a pre-existing default profile: no 'profile detect'."""
    run_recorder.responses["config home"] = lambda cmd: type("R", (), {"stdout": f"{home}\n", "returncode": 0})()


# ---- --fast --------------------------------------------------------------- #
def test_fast_sources_existing_buildenv_without_building(make_context, run_recorder, which):
    config = {"conan": {}}
    ctx = make_context(config=config, fast=True)
    _ensure_default_conanfile(ctx, config)
    buildenv = ctx.env_workdir / ".conan" / "conanbuildenv.sh"
    buildenv.parent.mkdir(parents=True)
    buildenv.write_text("export FROM_CONAN=yes\n")

    run_conan(config, ctx)

    assert ctx.env["FROM_CONAN"] == "yes"
    # the only subprocess call is source()'s own 'bash -c' (needed to fold
    # the buildenv's exports into ctx.env) -- no `conan` binary ever runs
    assert len(run_recorder.calls) == 1
    assert run_recorder.calls[0].cmd[:2] == ["bash", "-c"]


def test_fast_still_shows_progress_banner(make_context, run_recorder, which, capsys):
    # --fast activates instead of exporting/installing, but under --verbose
    # the '[i/n]' progress banners for that must still show, not silently vanish.
    config = {"conan": {}}
    ctx = make_context(config=config, fast=True, verbose=True)
    _ensure_default_conanfile(ctx, config)
    buildenv = ctx.env_workdir / ".conan" / "conanbuildenv.sh"
    buildenv.parent.mkdir(parents=True)
    buildenv.write_text("export FROM_CONAN=yes\n")

    run_conan(config, ctx)

    err = capsys.readouterr().err
    assert "conan" in err
    assert "install" in err
    assert "activate" in err  # the real work --fast does, not skipped


def test_fast_dies_when_buildenv_missing(make_context, which):
    config = {"conan": {}}
    ctx = make_context(config=config, fast=True)
    _ensure_default_conanfile(ctx, config)
    with pytest.raises(SystemExit):
        run_conan(config, ctx)


# ---- exe resolution ----------------------------------------------------------#
def test_conan_missing_dies(make_context, which):
    which["conan"] = None
    config = {"conan": {}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    with pytest.raises(SystemExit):
        run_conan(config, ctx)


def test_conan_explicit_exe(make_context, run_recorder, which):
    which["conan"] = None
    default_profile_ok(run_recorder)
    config = {"conan": {"exe": "/opt/conan"}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    assert any("/opt/conan config home" in c for c in run_recorder.commands())


def test_conan_outside_active_venv_warns(make_context, run_recorder, which, caplog, tmp_path):
    # a venv left without a conan script (e.g. an interrupted install a later
    # run still considers satisfied) makes exe's PATH lookup fall back to a
    # host conan -- unpinning the env silently unless this says so
    caplog.set_level("INFO")
    which["conan"] = "/usr/bin/conan"
    default_profile_ok(run_recorder)
    config = {"conan": {}}
    ctx = make_context(config=config)
    ctx.env["VIRTUAL_ENV"] = str(tmp_path / "venv")
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    assert "outside the active venv" in caplog.text
    assert "--force" in caplog.text  # how to fix it, not just that it happened


def test_conan_from_active_venv_does_not_warn(make_context, run_recorder, which, caplog, tmp_path):
    caplog.set_level("INFO")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "conan").write_text("x")
    which["conan"] = str(venv / "bin" / "conan")
    default_profile_ok(run_recorder)
    config = {"conan": {}}
    ctx = make_context(config=config)
    ctx.env["VIRTUAL_ENV"] = str(venv)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    assert "outside the active venv" not in caplog.text


# ---- profile detection --------------------------------------------------------#
def test_config_home_failure_dies_with_conans_own_message(make_context, run_recorder, which, caplog):
    # `conan config home` is captured, so conan's stderr is the only thing
    # that explains the failure -- it must reach the user, not the result object
    caplog.set_level("INFO")
    run_recorder.responses["config home"] = lambda cmd: type(
        "R", (), {"stdout": "", "returncode": 1, "stderr": "ERROR: Invalid configuration: unknown conf 'tools.bogus'\n"}
    )()
    config = {"conan": {}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)

    with pytest.raises(SystemExit):
        run_conan(config, ctx)

    assert "unknown conf 'tools.bogus'" in caplog.text
    assert "config home` failed (exit 1)" in caplog.text
    assert "CONAN_HOME=<unset" in caplog.text  # says which home it couldn't use


def test_config_home_failure_names_a_configured_conan_home(make_context, run_recorder, which, caplog, tmp_path):
    caplog.set_level("INFO")
    run_recorder.responses["config home"] = lambda cmd: type("R", (), {"stdout": "", "returncode": 1, "stderr": ""})()
    config = {"conan": {}}
    ctx = make_context(config=config)
    ctx.env["CONAN_HOME"] = str(tmp_path / "conanhome")
    _ensure_default_conanfile(ctx, config)

    with pytest.raises(SystemExit):
        run_conan(config, ctx)

    assert f"CONAN_HOME={tmp_path / 'conanhome'}" in caplog.text


def test_profile_detect_runs_when_missing(make_context, run_recorder, which):
    run_recorder.responses["config home"] = lambda cmd: type("R", (), {"stdout": "/no/such/home\n", "returncode": 0})()
    config = {"conan": {}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    assert any("profile detect" in c for c in run_recorder.commands())


def test_profile_detect_skipped_when_present(make_context, run_recorder, which, tmp_path):
    home = tmp_path / "conanhome"
    (home / "profiles").mkdir(parents=True)
    (home / "profiles" / "default").write_text("x")
    run_recorder.responses["config home"] = lambda cmd: type("R", (), {"stdout": f"{home}\n", "returncode": 0})()
    config = {"conan": {}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    assert not any("profile detect" in c for c in run_recorder.commands())


# ---- base classes --------------------------------------------------------------#
def test_base_classes_configured(make_context, run_recorder, which, tmp_path):
    default_profile_ok(run_recorder)
    config = {"conan": {"base-classes": ["custom/bc"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "custom" / "bc").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    # no assertion needed beyond "did not crash"; exercised via prepare/export below


def test_base_classes_default(make_context, run_recorder, which):
    default_profile_ok(run_recorder)
    config = {"conan": {}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    assert config["conan"]["base-classes"] == []


def test_base_classes_string_dies(make_context, which):
    # 'base-classes:' is a list like a 'recipes:' entry's 'dirs:': a bare
    # string would silently iterate its characters, and wouldn't append
    # across 'import:' layers the way the list form does.
    config = {"conan": {"base-classes": "conan/base_classes"}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conan" / "base_classes").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config)
    with pytest.raises(SystemExit):
        run_conan(config, ctx)


def test_base_classes_missing_dir_dies(make_context, which, tmp_path):
    # like a 'recipes:' entry's dirs: an explicitly listed dir that isn't
    # there is a config error, not something to silently drop.
    config = {"conan": {"base-classes": [str(tmp_path / "does-not-exist")]}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    with pytest.raises(SystemExit):
        run_conan(config, ctx)


# ---- recipes: entry dirs resolution ----------------------------------------------#
def test_recipe_dirs_configured_explicit(make_context, run_recorder, which, tmp_path):
    default_profile_ok(run_recorder)
    config = {"conan": {}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conanA").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config, {"dirs": ["conanA"]})

    run_conan(config, ctx)
    argvs = run_recorder.argvs()
    export_argv = next(a for a in argvs if "--export" in a)
    assert export_argv[export_argv.index("--recipes-dir") + 1] == str(ctx.env_dir / "conanA")
    # --prepare is remotes-only; it takes no recipe dir of its own
    prepare_argv = next(a for a in argvs if "--prepare" in a)
    assert "--recipes-dir" not in prepare_argv
    # no 'catalog:' -> the catalog stays in memory, nothing is written
    assert "--export-catalog" not in export_argv
    assert "--catalog-yml" not in export_argv


def test_unit_recipe_dirs_all_passed_to_one_invocation(make_context, run_recorder, which, tmp_path):
    # a recipes entry's dirs form ONE catalog, so they go to a single
    # --export run -- not one run per dir.
    default_profile_ok(run_recorder)
    base_dir = tmp_path / "base"
    (base_dir / "conan" / "recipes").mkdir(parents=True)

    config = {"conan": {}}
    ctx = make_context(config=config, import_dirs=[base_dir])
    (ctx.env_dir / "conan" / "recipes").mkdir(parents=True)
    _ensure_default_conanfile(
        ctx,
        config,
        {"dirs": [str(base_dir / "conan" / "recipes"), "conan/recipes"]},
    )

    run_conan(config, ctx)
    export_argvs = [a for a in run_recorder.argvs() if "--export" in a]
    assert len(export_argvs) == 1
    passed = [a for i, a in enumerate(export_argvs[0]) if export_argvs[0][i - 1] == "--recipes-dir"]
    assert passed == [str(base_dir / "conan" / "recipes"), str(ctx.env_dir / "conan" / "recipes")]


def test_units_are_exported_in_order(make_context, run_recorder, which):
    # each 'recipes:' entry gets its own --export run, in config order --
    # independent of 'conanfile:', which names what to install.
    default_profile_ok(run_recorder)
    config = {
        "conan": {
            "conanfile": "a/conanfile.py",
            "recipes": [{"dirs": ["a/recipes"]}, {"dirs": ["b/recipes"]}],
        }
    }
    ctx = make_context(config=config)
    for name in ("a", "b"):
        (ctx.env_dir / name / "recipes").mkdir(parents=True)
        (ctx.env_dir / name / "conanfile.py").write_text("x\n")

    run_conan(config, ctx)
    export_argvs = [a for a in run_recorder.argvs() if "--export" in a]
    assert [a[a.index("--recipes-dir") + 1] for a in export_argvs] == [
        str(ctx.env_dir / "a" / "recipes"),
        str(ctx.env_dir / "b" / "recipes"),
    ]


# ---- recipes: entry catalog: -------------------------------------------------------#
def test_catalog_written_where_the_unit_names_it(make_context, run_recorder, which):
    default_profile_ok(run_recorder)
    config = {"conan": {}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conanA").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config, {"dirs": ["conanA"], "catalog": "conan/catalog.yml"})

    run_conan(config, ctx)
    export_argv = next(a for a in run_recorder.argvs() if "--export" in a)
    assert export_argv[export_argv.index("--export-catalog") + 1] == str(ctx.env_dir / "conan" / "catalog.yml")


def test_catalog_without_recipe_dirs_dies(make_context, which):
    # a catalog with nothing to build it from is a config mistake, not an
    # empty file to write.
    config = {"conan": {}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config, {"catalog": "conan/catalog.yml"})
    with pytest.raises(SystemExit):
        run_conan(config, ctx)


def test_catalog_list_dies(make_context, which):
    # 'catalog:' is a single output path, not a list -- one 'recipes:' entry
    # builds one catalog, so a list has no meaning to fall back on.
    config = {"conan": {}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conanA").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config, {"dirs": ["conanA"], "catalog": ["conan/catalog.yml"]})
    with pytest.raises(SystemExit):
        run_conan(config, ctx)


def test_unit_recipes_exporter_missing_dies(make_context, which):
    config = {"conan": {}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conanA").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config, {"dirs": ["conanA"], "export-tool": "no-such-exporter.py"})
    with pytest.raises(SystemExit):
        run_conan(config, ctx)


def test_unit_recipes_exporter_overrides_the_default(make_context, run_recorder, which):
    default_profile_ok(run_recorder)
    config = {"conan": {}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conanA").mkdir(parents=True)
    own = ctx.env_dir / "my-exporter.py"
    own.write_text("x\n")
    _ensure_default_conanfile(ctx, config, {"dirs": ["conanA"], "export-tool": "my-exporter.py"})

    run_conan(config, ctx)
    export_argv = next(a for a in run_recorder.argvs() if "--export" in a)
    assert str(own) in export_argv
    # --prepare still uses the env-wide exporter
    prepare_argv = next(a for a in run_recorder.argvs() if "--prepare" in a)
    assert str(own) not in prepare_argv


@pytest.mark.parametrize(
    "user_channel_cfg, expected_user, expected_channel",
    [({}, "denver", "snapshot"), ({"user": "acme", "channel": "stable"}, "acme", "stable")],
    ids=["default", "configured"],
)
def test_export_user_channel(make_context, run_recorder, which, user_channel_cfg, expected_user, expected_channel):
    default_profile_ok(run_recorder)
    config = {"conan": {**user_channel_cfg}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conanA").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config, {"dirs": ["conanA"]})

    run_conan(config, ctx)
    export_argv = next(a for a in run_recorder.argvs() if "--export" in a)
    assert export_argv[export_argv.index("--user") + 1] == expected_user
    assert export_argv[export_argv.index("--channel") + 1] == expected_channel


def test_recipe_dirs_not_guessed_from_directory_layout(make_context, run_recorder, which, tmp_path):
    # a conan/recipes dir in an imported base AND in the env itself: neither
    # is exported, because no 'recipes:' entry's 'dirs:' names them. Nothing is
    # discovered off the directory layout any more.
    default_profile_ok(run_recorder)
    base_dir = tmp_path / "base"
    (base_dir / "conan" / "recipes").mkdir(parents=True)

    config = {"conan": {}}
    ctx = make_context(config=config, import_dirs=[base_dir])
    (ctx.env_dir / "conan" / "recipes").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config)

    run_conan(config, ctx)
    commands = run_recorder.commands()
    assert [c for c in commands if "--export" in c] == []


def test_recipe_dir_missing_dies(make_context, run_recorder, which, tmp_path):
    # an explicitly listed recipe dir that isn't there is a config error --
    # silently filtering it out would let a stale entry go unnoticed.
    default_profile_ok(run_recorder)
    config = {"conan": {}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config, {"dirs": [str(tmp_path / "does-not-exist")]})

    with pytest.raises(SystemExit):
        run_conan(config, ctx)


def test_no_recipe_dirs_skips_export_body(make_context, run_recorder, which):
    default_profile_ok(run_recorder)
    config = {"conan": {}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    assert not any("--export" in c for c in run_recorder.commands())


def test_no_recipe_dirs_no_remotes_skips_prepare_entirely_when_keep_remotes_on(make_context, run_recorder, which):
    # with keep-remotes: true, --prepare's only other job (recipe-dir
    # catalog prep) is also absent, so the whole step is skipped -- see
    # test_no_recipe_dirs_skips_export_body for the (now default) opposite
    # case, where --prepare still runs just to reconcile remotes.
    default_profile_ok(run_recorder)
    config = {"conan": {"keep-remotes": True}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    assert not any("--prepare" in c for c in run_recorder.commands())
    assert not any("--export" in c for c in run_recorder.commands())


# ---- conanfile resolution --------------------------------------------------------#
def test_conanfile_list_dies(make_context, which):
    # 'conanfile:' is a single path -- a project only ever has one dependency
    # graph, so a list is rejected rather than silently accepted and iterated.
    config = {"conan": {"conanfile": ["conan/conanfile.py"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conan").mkdir(parents=True)
    (ctx.env_dir / "conan" / "conanfile.py").write_text("x\n")
    with pytest.raises(SystemExit):
        run_conan(config, ctx)


def test_conanfile_not_a_string_dies(make_context, which):
    # the old per-unit mapping shape ({path:, recipe-dirs:, ...}) is no
    # longer accepted here now that 'recipes:' is a separate, decoupled list.
    config = {"conan": {"conanfile": {"path": "conan/conanfile.py"}}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conan").mkdir(parents=True)
    (ctx.env_dir / "conan" / "conanfile.py").write_text("x\n")
    with pytest.raises(SystemExit):
        run_conan(config, ctx)


def test_recipes_entry_with_unknown_key_dies(make_context, which):
    # a typo'd 'recipes:' entry key would otherwise be silently ignored
    config = {"conan": {"recipes": [{"dir": ["conanA"]}]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conanA").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config)
    with pytest.raises(SystemExit):
        run_conan(config, ctx)


def test_recipes_entry_not_a_mapping_dies(make_context, which):
    # each 'recipes:' entry must be its own {dirs:, catalog:, export-tool:}
    # mapping -- a bare string/list entry has no keys to read dirs: from.
    config = {"conan": {"recipes": ["conanA"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conanA").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config)
    with pytest.raises(SystemExit):
        run_conan(config, ctx)


def test_conanfile_explicit_path(make_context, run_recorder, which):
    default_profile_ok(run_recorder)
    config = {"conan": {"conanfile": "conan/conanfile.py"}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conan").mkdir(parents=True)
    (ctx.env_dir / "conan" / "conanfile.py").write_text("x\n")
    run_conan(config, ctx)
    assert any("conan install" in c for c in run_recorder.commands())


def test_conanfile_not_guessed_from_directory_layout(make_context, run_recorder, which):
    # conan/conanfile.py exists but isn't configured: nothing is installed,
    # rather than it being picked up by convention.
    default_profile_ok(run_recorder)
    config = {"conan": {}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conan").mkdir(parents=True)
    (ctx.env_dir / "conan" / "conanfile.py").write_text("x\n")
    run_conan(config, ctx)
    assert not any("conan install" in c for c in run_recorder.commands())


def test_conanfile_missing_dies(make_context, run_recorder, which):
    # an explicitly named conanfile that isn't there: the central resolver
    # (ConanProvider.resolve_defaults) dies before the provider ever runs.
    default_profile_ok(run_recorder)
    config = {"conan": {"conanfile": "conan/conanfile.py"}}
    ctx = make_context(config=config)
    with pytest.raises(SystemExit):
        run_conan(config, ctx)


# ---- stage toggles --------------------------------------------------------------#
# ---- install details -------------------------------------------------------------#
def test_install_no_auth_via_config(make_context, run_recorder, which):
    default_profile_ok(run_recorder)
    config = {"conan": {"authentication": False}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    assert any("--no-remote" in c for c in run_recorder.commands())


def test_install_build_default_missing(make_context, run_recorder, which):
    default_profile_ok(run_recorder)
    config = {"conan": {}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    assert any("--build=missing" in c for c in run_recorder.commands())


def test_install_build_list(make_context, run_recorder, which):
    default_profile_ok(run_recorder)
    config = {"conan": {"build": ["missing", "openssl/*"]}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    install_cmd = next(c for c in run_recorder.commands() if "conan install" in c)
    assert "--build=missing" in install_cmd
    assert "--build=openssl/*" in install_cmd


@pytest.mark.parametrize(
    "profiles, expect_present, expect_absent",
    [
        ({}, [], ["-pr:h", "-pr:b"]),
        ({"host": ["linux-x86_64"], "build": ["default"]}, ["-pr:h=linux-x86_64", "-pr:b=default"], []),
        ({"host": ["a", "b"]}, ["-pr:h=a", "-pr:h=b"], []),
    ],
    ids=["default-empty", "host-and-build", "multiple-values-each-get-own-flag"],
)
def test_install_profiles(make_context, run_recorder, which, profiles, expect_present, expect_absent):
    default_profile_ok(run_recorder)
    config = {"conan": {"profiles": profiles}} if profiles else {"conan": {}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    install_cmd = next(c for c in run_recorder.commands() if "conan install" in c)
    for flag in expect_present:
        assert flag in install_cmd
    for flag in expect_absent:
        assert flag not in install_cmd


def test_deployers_bare_string_dies(make_context, which):
    # a config key says what it is: a bare deployer path can't be told apart from
    # a one-entry list by accident, so it is rejected rather than silently
    # wrapped.
    config = {"conan": {"deployers": "deploy.py"}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    with pytest.raises(SystemExit):
        run_conan(config, ctx)


def test_deployers_missing_script_dies(make_context, which):
    config = {"conan": {"deployers": ["no-such-deployer.py"]}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    with pytest.raises(SystemExit):
        run_conan(config, ctx)


def test_no_conanfile_skips_conan_install(make_context, run_recorder, which, capsys):
    # 'conanfile:' is the only toggle -- unset means "export/pin recipes
    # without installing anything", no separate 'install:' flag to check.
    default_profile_ok(run_recorder)
    config = {"conan": {}}
    ctx = make_context(config=config, verbose=True)
    run_conan(config, ctx)
    assert not any("conan install" in c for c in run_recorder.commands())
    assert "install (skipped: no conanfile configured)" in capsys.readouterr().err


def test_install_extra_args(make_context, run_recorder, which):
    default_profile_ok(run_recorder)
    config = {"conan": {"install-args": ["--update"]}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    assert any("--update" in c for c in run_recorder.commands())


def test_install_writes_buildenv_straight_into_the_install_tree(make_context, run_recorder, which):
    # a single conanfile has nothing to aggregate: conan writes
    # conanbuildenv.sh straight into --output-folder, which must be exactly
    # where _activate_buildenv looks for it -- no per-conanfile subdir.
    default_profile_ok(run_recorder)
    config = {"conan": {"conanfile": "conan/conanfile.py"}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conan").mkdir(parents=True)
    (ctx.env_dir / "conan" / "conanfile.py").write_text("x\n")
    run_conan(config, ctx)

    install_cmd = next(
        a for a in run_recorder.argvs() if "install" in a and str(ctx.env_dir / "conan" / "conanfile.py") in a
    )
    install_root = ctx.env_workdir / ".conan"
    assert f"--output-folder={install_root}" in install_cmd
    assert f"--deployer-folder={install_root / 'symlinks'}" in install_cmd


def test_install_success_activates_the_buildenv_it_wrote(make_context, run_recorder, which):
    # simulate what the real 'conan install' does: write conanbuildenv.sh
    # into --output-folder as a side effect -- _activate_buildenv must then
    # source it into ctx.env.
    default_profile_ok(run_recorder)
    config = {"conan": {"conanfile": "conan/conanfile.py"}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conan").mkdir(parents=True)
    (ctx.env_dir / "conan" / "conanfile.py").write_text("x\n")

    def write_buildenv(cmd):
        buildenv = ctx.env_workdir / ".conan" / "conanbuildenv.sh"
        buildenv.write_text("export FROM_CONAN_INSTALL=yes\n")
        return run_recorder.default

    run_recorder.responses["conan install"] = write_buildenv

    run_conan(config, ctx)

    assert ctx.env["FROM_CONAN_INSTALL"] == "yes"


# ---- config (conan config install) -------------------------------------------#
def test_no_config_configured_no_install_command(make_context, run_recorder, which):
    default_profile_ok(run_recorder)
    config = {"conan": {}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    assert not any("config install" in c for c in run_recorder.commands())


def test_config_single_dir_installed(make_context, run_recorder, which, tmp_path):
    default_profile_ok(run_recorder)
    config = {"conan": {"config": ["conan-config"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conan-config").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config)

    run_conan(config, ctx)
    argv = next(a for a in run_recorder.argvs() if a[1:3] == ["config", "install"])
    assert argv[-1] == str(ctx.env_dir / "conan-config")


def test_config_multiple_dirs_installed_in_order(make_context, run_recorder, which):
    default_profile_ok(run_recorder)
    config = {"conan": {"config": ["conf-a", "conf-b"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conf-a").mkdir(parents=True)
    (ctx.env_dir / "conf-b").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config)

    run_conan(config, ctx)
    install_argvs = [a for a in run_recorder.argvs() if a[1:3] == ["config", "install"]]
    assert len(install_argvs) == 2
    assert install_argvs[0][-1] == str(ctx.env_dir / "conf-a")
    assert install_argvs[1][-1] == str(ctx.env_dir / "conf-b")


def test_config_installed_before_profile_detect(make_context, run_recorder, which):
    run_recorder.responses["config home"] = lambda cmd: type("R", (), {"stdout": "/no/such/home\n", "returncode": 0})()
    config = {"conan": {"config": ["conan-config"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conan-config").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config)

    run_conan(config, ctx)
    commands = run_recorder.commands()
    install_index = next(i for i, c in enumerate(commands) if "config install" in c)
    detect_index = next(i for i, c in enumerate(commands) if "profile detect" in c)
    assert install_index < detect_index


def test_config_banner_shown_before_config_install_output(make_context, run_recorder, which, capsys):
    # the 'config' progress banner must print before 'conan config
    # install's own '+ cmd' echo/output, not after -- config install used
    # to run unbannered, so its output appeared ahead of the first banner.
    default_profile_ok(run_recorder)
    config = {"conan": {"config": ["conan-config"]}}
    ctx = make_context(config=config, verbose=True)
    (ctx.env_dir / "conan-config").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config)

    run_conan(config, ctx)

    err = capsys.readouterr().err
    banner_pos = err.index("config")
    install_pos = err.index("conan config install")
    assert banner_pos < install_pos


def test_config_dir_missing_dies(make_context, which):
    config = {"conan": {"config": ["nope"]}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    with pytest.raises(SystemExit):
        run_conan(config, ctx)


def test_config_entry_not_a_directory_dies(make_context, which):
    config = {"conan": {"config": ["conan/conanfile.py"]}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    with pytest.raises(SystemExit):
        run_conan(config, ctx)


# ---- remotes ---------------------------------------------------------------- #
def test_no_remotes_configured_still_reconciles_by_default(make_context, run_recorder, which):
    # keep-remotes defaults off: 'remotes:' is exhaustive even when empty,
    # so --remotes-json/--cleanup-remotes are passed regardless, disabling
    # every remote already present (e.g. one an earlier, different env's run
    # left behind) -- see test_no_remotes_configured_no_remotes_json_when_keep_remotes_on
    # for the opt-out.
    default_profile_ok(run_recorder)
    config = {"conan": {}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    # the install step rebuilds env_workdir/.conan/ from scratch afterwards
    # (it's also where remotes.json lives, see test_remotes_configured_written_and_passed),
    # so only the command-line wiring is checked here, not the file's
    # survival past the whole pipeline.
    prepare_cmds = [c for c in run_recorder.commands() if "--prepare" in c]
    assert len(prepare_cmds) == 1
    assert "--remotes-json" in prepare_cmds[0]
    assert "--cleanup-remotes" in prepare_cmds[0]


def test_no_remotes_configured_no_remotes_json_when_keep_remotes_on(make_context, run_recorder, which):
    default_profile_ok(run_recorder)
    config = {"conan": {"keep-remotes": True}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    assert not any("--remotes-json" in c for c in run_recorder.commands())
    assert not any("--cleanup-remotes" in c for c in run_recorder.commands())
    assert not (ctx.env_workdir / ".conan" / "remotes.json").exists()


def test_cleanup_remotes_skipped_when_config_dir_present(make_context, run_recorder, which):
    # a 'config:' dir's own `conan config install` may itself have installed
    # a remotes.json (denver never opens/interprets it) -- cleanup-remotes
    # defaulting on must not reconcile an empty 'remotes:' to "exhaustive"
    # in that case, or it would silently disable every remote config
    # install just enabled (e.g. the bug this test guards against).
    default_profile_ok(run_recorder)
    config = {"conan": {"config": ["conan-config"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conan-config").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    assert not any("--remotes-json" in c for c in run_recorder.commands())
    assert not any("--cleanup-remotes" in c for c in run_recorder.commands())


def test_cleanup_remotes_still_applies_with_config_dir_when_remotes_also_configured(make_context, run_recorder, which):
    # an explicit 'remotes:' still reconciles/cleans up normally even
    # alongside a 'config:' dir -- the skip above only kicks in when
    # 'remotes:' is left unset/empty (nothing of denver's own to reconcile).
    default_profile_ok(run_recorder)
    config = {
        "conan": {
            "config": ["conan-config"],
            "remotes": {"conancenter": {"url": "https://example.invalid/conan"}},
        }
    }
    ctx = make_context(config=config)
    (ctx.env_dir / "conan-config").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    prepare_cmds = [c for c in run_recorder.commands() if "--prepare" in c]
    assert len(prepare_cmds) == 1
    assert "--remotes-json" in prepare_cmds[0]
    assert "--cleanup-remotes" in prepare_cmds[0]


def test_remotes_configured_written_and_passed(make_context, run_recorder, which):
    import json

    default_profile_ok(run_recorder)
    remotes = {"conancenter": {"url": "https://example.invalid/conan", "enabled": True}}
    config = {"conan": {"remotes": remotes}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)

    # the install step rebuilds env_workdir/.conan/ from scratch (it's also
    # where remotes.json lives), so capture the file's content as it's
    # written rather than after run_conan() returns.
    written = {}

    def capture(cmd):
        written["remotes"] = json.loads((ctx.env_workdir / ".conan" / "remotes.json").read_text())
        return run_recorder.default

    run_recorder.responses["--remotes-json"] = capture

    run_conan(config, ctx)

    assert written["remotes"] == remotes
    prepare_argvs = [a for a in run_recorder.argvs() if "--prepare" in a]
    assert len(prepare_argvs) == 1
    prepare_argv = prepare_argvs[0]
    assert prepare_argv[prepare_argv.index("--remotes-json") + 1] == str(ctx.env_workdir / ".conan" / "remotes.json")


def test_ctx_force_passes_force_to_prepare(make_context, run_recorder, which):
    default_profile_ok(run_recorder)
    config = {"conan": {"remotes": {"conancenter": {"url": "https://example.invalid"}}}}
    ctx = make_context(config=config, force=True)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    prepare_cmds = [c for c in run_recorder.commands() if "--prepare" in c]
    assert len(prepare_cmds) == 1
    assert "--force" in prepare_cmds[0]


def test_no_force_omits_force_flag(make_context, run_recorder, which):
    default_profile_ok(run_recorder)
    config = {"conan": {"remotes": {"conancenter": {"url": "https://example.invalid"}}}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    prepare_cmds = [c for c in run_recorder.commands() if "--prepare" in c]
    assert len(prepare_cmds) == 1
    assert "--force" not in prepare_cmds[0]


def test_remotes_configured_without_recipe_dirs_still_prepares(make_context, run_recorder, which):
    # 'remotes:' alone (no recipes: entry) must still trigger a --prepare run --
    # remote management doesn't depend on any recipes being configured.
    default_profile_ok(run_recorder)
    config = {"conan": {"remotes": {"conancenter": {"url": "https://example.invalid"}}}}
    ctx = make_context(config=config)
    _ensure_default_conanfile(ctx, config)
    run_conan(config, ctx)
    prepare_cmds = [c for c in run_recorder.commands() if "--prepare" in c]
    assert len(prepare_cmds) == 1
    assert "--recipes-dir" not in prepare_cmds[0]


def test_base_classes_passed_to_prepare_and_export(make_context, run_recorder, which):
    # 'base-classes:' is optional; when set it is handed to the catalog tool
    # for both the shared --prepare and every --export.
    default_profile_ok(run_recorder)
    config = {"conan": {"base-classes": ["conan/base_classes"]}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conanA").mkdir(parents=True)
    (ctx.env_dir / "conan" / "base_classes").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config, {"dirs": ["conanA"]})

    run_conan(config, ctx)
    argvs = run_recorder.argvs()
    expected = str(ctx.env_dir / "conan" / "base_classes")
    prepare_argv = next(a for a in argvs if "--prepare" in a)
    assert prepare_argv[prepare_argv.index("--base-classes-dir") + 1] == expected
    export_argv = next(a for a in argvs if "--export" in a)
    assert export_argv[export_argv.index("--base-classes-dir") + 1] == expected


def test_multiple_base_classes_passed_in_order(make_context, run_recorder, which):
    # every listed dir becomes its own --base-classes-dir flag, in list order,
    # on both --prepare and every --export.
    default_profile_ok(run_recorder)
    config = {"conan": {"base-classes": ["bc-own", "bc-shared"]}}
    ctx = make_context(config=config)
    for name in ("conanA", "bc-own", "bc-shared"):
        (ctx.env_dir / name).mkdir(parents=True)
    _ensure_default_conanfile(ctx, config, {"dirs": ["conanA"]})

    run_conan(config, ctx)
    expected = [str(ctx.env_dir / "bc-own"), str(ctx.env_dir / "bc-shared")]
    assert _flag_values(_argv_with(run_recorder, "--prepare"), "--base-classes-dir") == expected
    assert _flag_values(_argv_with(run_recorder, "--export"), "--base-classes-dir") == expected


def test_base_classes_omitted_when_unset(make_context, run_recorder, which):
    default_profile_ok(run_recorder)
    config = {"conan": {}}
    ctx = make_context(config=config)
    (ctx.env_dir / "conanA").mkdir(parents=True)
    _ensure_default_conanfile(ctx, config, {"dirs": ["conanA"]})

    run_conan(config, ctx)
    assert not any("--base-classes-dir" in c for c in run_recorder.commands())
