"""Shared ConanFile base for denver's third-party toolchain recipes.

A recipe under conan/recipes/<tool>/<version>/conanfile.py subclasses this,
sets metadata + `exports_sources` (and optionally `kind`/`no_copy_source`/
`build_script_folder`/`apply_patches_basepath`), and gets export/download,
unpack, patch, cmake-or-autotools build, and .pc-file packaging for free.
Conan calls package_id/requirements/build_requirements/export/export_sources/
source/build/package/package_info itself -- those names are Conan's API
contract and can't be renamed; everything else here is this base class's own
decomposition.
"""

from pathlib import Path
from types import SimpleNamespace

from conan import ConanFile
from conan import conan_version as conan_version_full
from conan.tools.cmake import CMake, CMakeToolchain
from conan.tools.env import VirtualBuildEnv
from conan.tools.files import check_md5, download, load, patch, save
from conan.tools.gnu import Autotools, AutotoolsToolchain
from conan.tools.system import package_manager

CONAN_MAJOR = 1 if str(conan_version_full).startswith("1.") else 2

_ARCHIVE_EXTRACT_CMD = {
    (".tar.gz",): "tar -xzf {name}",
    (".tgz",): "tar -xzf {name}",
    (".tar.xz",): "tar -xf {name}",
    (".tar.bz2",): "tar -xf {name}",
    (".zip",): "unzip {name}",
}


class DenverConanFileError(Exception):
    """Raised for recipe configuration/build errors in this base class."""


class DenverConanFile(ConanFile):
    build_policy = "missing"
    default_user = "denver"
    default_channel = "unofficial"
    system_tools_requires = ()

    def package_id(self):
        self.info.requires.recipe_revision_mode()
        if CONAN_MAJOR == 1:
            # conan 1's package_id doesn't otherwise see this conf value
            self.info.settings.find_package_prefer_config = self.conf.get(
                "tools.cmake.cmaketoolchain:find_package_prefer_config", check_type=bool
            )

    def system_requirements(self):
        self.global_system_requirements = True
        # update=True only kicks in when 'check' found something missing *and*
        # the mode is 'install' -- i.e. right before the apt-get install that
        # would otherwise fail on a machine whose package lists are stale or
        # were never fetched (a fresh CI runner or container).
        package_manager.Apt(self).install(self.system_tools_requires, check=True, update=True)

    def requirements(self):
        self._add_requires(self.requires, "requires", "requires_latest")

    def build_requirements(self):
        self._add_requires(self.tool_requires, "tool_requires", "tool_requires_latest")

    def _add_requires(self, adder, *conan_data_keys):
        for key in conan_data_keys:
            for ref in self.conan_data.get(key, []):
                adder(ref)

    # -- export: bundle each declared source next to the recipe -----------

    def export(self):
        self.output.info(f"exporting recipe file '{__file__}'")
        self.run(f"cp -r {__file__} {self.export_folder}/")

    def export_sources(self):
        if not isinstance(self.exports_sources, (list, tuple)):
            raise DenverConanFileError("exports_sources must be a list or tuple")
        self._check_no_stale_conandata_sources()
        for source_name in self.exports_sources:
            self._export_one_source(source_name)

    def _check_no_stale_conandata_sources(self):
        """Fail if conandata.yml pins a source this recipe doesn't declare in exports_sources.

        get_rrev.py only adds/updates the entries it is told about via
        exports_sources and never deletes any, so a source dropped from a
        recipe would otherwise linger in conandata.yml forever -- pinned,
        unused, and quietly feeding a stale md5 into the recipe revision.
        The recipe is the authority on what it exports, so the check belongs
        here rather than in the RREV script.
        """
        stale = sorted(set(self.conan_data.get("sources") or {}) - set(self.exports_sources))
        if stale:
            raise DenverConanFileError(
                f"conandata.yml lists sources which are not in exports_sources: {', '.join(stale)}. "
                "Remove them from conandata.yml or add them to exports_sources."
            )

    def _export_one_source(self, source_name):
        spec = self._source_spec(source_name)
        path = Path(source_name)

        if not spec.md5:
            raise DenverConanFileError(f"'{source_name}': conandata.yml is missing 'md5'")

        if path.exists():
            check_md5(self, source_name, spec.md5)
            return

        if not spec.url and not spec.custom:
            raise DenverConanFileError(f"'{source_name}': conandata.yml needs 'url' (or 'custom') to fetch it")

        self.output.info(f"fetching '{source_name}' to export alongside the recipe")
        self.conf = self.conf_info  # hotfix conan bug
        if spec.custom:
            self.run(spec.custom)
        else:
            download(self, spec.url, filename=source_name, verify=False, md5=spec.md5)

        exported = Path(self.export_sources_folder) / source_name
        if not exported.exists():
            self.run(f"cp --parents {source_name} {self.export_sources_folder}/")

    def _source_spec(self, source_name):
        entry = (self.conan_data.get("sources") or {}).get(source_name) or {}
        return SimpleNamespace(url=entry.get("url"), md5=entry.get("md5"), custom=entry.get("custom"))

    # -- source: unpack, normalize the folder name, patch -----------------

    def source(self):
        self._unpack_sources()
        self._normalize_source_folder()
        self._apply_source_patches()

    def _unpack_sources(self):
        sources = self.conan_data.get("sources")
        if not sources:
            return
        for source_name in self.exports_sources:
            entry = sources.get(source_name, {})
            if "md5" in entry:
                check_md5(self, source_name, entry["md5"])
            self._extract_if_archive(source_name)

    def _extract_if_archive(self, source_name):
        for suffixes, cmd in _ARCHIVE_EXTRACT_CMD.items():
            if source_name.endswith(suffixes):
                self.output.info(f"unpacking '{source_name}'")
                self.run(cmd.format(name=source_name))
                self.run(f"rm -rf {source_name}")
                return
        self.output.info(f"'{source_name}' is not an archive, leaving as-is")

    def _normalize_source_folder(self):
        # a single-directory extraction (the common case) is renamed to
        # self.name so downstream build_script_folder lookups don't need to
        # know the archive's internal top-level folder name
        subfolders = [p for p in Path().iterdir() if p.is_dir()]
        if len(subfolders) == 1 and not Path(self.name).exists():
            self.output.info(f"renaming '{subfolders[0]}' -> '{self.name}'")
            self.run(f"mv */ {self.name}")

    def _apply_source_patches(self, base_path=None):
        base_path = base_path or getattr(self, "apply_patches_basepath", None) or self.name
        for source_name in self.exports_sources:
            if source_name.endswith(".patch"):
                self.output.info(f"applying '{source_name}'")
                patch(self, base_path=base_path, patch_file=source_name)

    # -- shared build/kind detection ---------------------------------------

    def get_infos(self):
        rel_script_folder = getattr(self, "build_script_folder", None) or self.name
        root = self.source_folder if getattr(self, "no_copy_source", None) else self.build_folder
        script_folder_abs = Path(root) / rel_script_folder

        kind = getattr(self, "kind", None) or self._detect_kind(script_folder_abs)

        return SimpleNamespace(build_script_folder=str(script_folder_abs), kind=kind)

    def _detect_kind(self, script_folder_abs):
        if (script_folder_abs / "CMakeLists.txt").exists():
            self.output.info("detected a cmake project")
            return "cmake"
        if (script_folder_abs / "configure").exists():
            self.output.info("detected an autotools project")
            return "autotools"
        return None

    def generate(self):
        toolchain = self.get_generator()
        if toolchain:
            toolchain.generate()
        return toolchain

    def get_buildenv(self):
        return VirtualBuildEnv(self)

    def get_generator(self, kind=None):
        buildenv = self.get_buildenv()
        buildenv.generate()

        kind = kind or self.get_infos().kind
        if kind == "cmake":
            toolchain = CMakeToolchain(self)
        elif kind == "autotools":
            toolchain = self._autotools_toolchain(buildenv)
        else:
            toolchain = None

        self.output.info(f"get_generator: kind='{kind}' -> {type(toolchain)}")
        return toolchain

    def _autotools_toolchain(self, buildenv):
        toolchain = AutotoolsToolchain(self)
        extra_flags = buildenv.vars().get("CONFIGURE_FLAGS")
        if not extra_flags:
            return toolchain
        # CONFIGURE_FLAGS may re-set an arg AutotoolsToolchain already
        # defaults -- clear those keys first so appending doesn't duplicate
        overridden = {flag.split("=")[0]: None for flag in extra_flags.split() if "=" in flag}
        toolchain.update_configure_args(overridden)
        toolchain.configure_args += extra_flags.split()
        return toolchain

    def build(self):
        self.system_requirements()
        kind = self.get_infos().kind
        if kind == "cmake":
            return self._build_cmake()
        if kind == "autotools":
            return self._build_autotools()
        self.output.warning(f"no build step for project kind '{kind}'")
        return None

    def _build_cmake(self):
        self.run("set -x; which cmake")
        build_type = self.settings.get_safe("build_type", "Release")
        cmake = CMake(self)
        cmake.configure(build_script_folder=self.get_infos().build_script_folder)
        cmake.build(build_type=build_type)
        cmake.install(build_type=build_type)
        return cmake

    def _build_autotools(self):
        autotools = Autotools(self)
        autotools.configure(build_script_folder=self.get_infos().build_script_folder)
        autotools.make()
        autotools.install()
        return autotools

    # -- package: pkg-config prefix rewriting + sanity checks --------------

    def _pkgconfig_dir(self):
        return Path(self.package_folder) / "lib" / "pkgconfig"

    def fix_pc_files(self):
        pkgconfig_dir = self._pkgconfig_dir()
        if not pkgconfig_dir.exists():
            return
        for pc_file in pkgconfig_dir.iterdir():
            self.run(f"sed -i 's@{self.package_folder}/@${{prefix}}/@g' {pc_file}")

    def package(self):
        self.output.info("rewriting absolute prefixes in any .pc files ...")
        self.fix_pc_files()

        self.output.info("checking the package output isn't empty ...")
        contents = [p for p in Path(self.package_folder).iterdir() if not p.name.startswith("conan")]
        if not contents:
            raise DenverConanFileError(f"package folder '{self.package_folder}' is empty")

        self._warn_if_not_relocatable()

    def _warn_if_not_relocatable(self):
        self.output.info("checking whether the package still embeds its build path ...")
        try:
            self.run(f"cd {self.package_folder} && ! grep -rnw . -e '{self.package_folder}' --exclude='conan*'")
        except Exception:  # noqa: BLE001 - a failing check here must not fail the build
            self.output.warning("package contains path-specific files, may not be relocatable")

    def package_info(self):
        self.cpp_info.builddirs.append(self.package_folder)
        self.cpp_info.frameworkdirs.append(self.package_folder)

        pkgconfig_dir = self._pkgconfig_dir()
        if not pkgconfig_dir.exists():
            return
        self.buildenv_info.append_path("PKG_CONFIG_PATH", str(pkgconfig_dir))
        for pc_file in pkgconfig_dir.iterdir():
            self._rewrite_pc_prefix(pc_file, self.package_folder)

    def _rewrite_pc_prefix(self, pc_file, new_prefix):
        lines = load(self, pc_file).splitlines()
        rewritten = [f"prefix={new_prefix}" if line.startswith("prefix=") else line for line in lines]
        save(self, pc_file, "\n".join(rewritten))
