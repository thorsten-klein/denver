"""docker provider: relocates the final command into a docker compose service.

A *wrapper* provider (see providers/base.py) -- it does not build the local
environment itself. Configured from denver.toml -> ``docker:``.

Full key reference, worked examples and design notes: ``doc/providers/docker.md``.
"""

import os
import sys
from pathlib import Path
from typing import cast

from .base import Provider, fill_unset
from .context import banner, die, die_on_unknown_keys, die_unless_paired


def _relocation_env(ctx):
    """``docker compose run -e`` flags telling the denver inside where it is.

    A container's environment comes from the image and the compose file, not
    from this process, so both facts have to be handed across the boundary
    explicitly:

    * ``DENVER_IN_CONTAINER`` -- so the inner denver never has to infer it
      from a runtime's marker file. ``/.dockerenv`` is docker's; podman
      writes something else, and other runtimes write nothing at all, which
      would leave the inner run believing it was on the host and trying to
      relocate a second time.
    * ``DENVER_RELOCATED`` -- which wrapper stage ids put it there (see
      denver.py's _mark_relocated), so a wrapper stage's absence inside is
      known to be denver's own doing rather than a user's ``--skip``.
    * every ``-e``/``--env NAME=VALUE`` the user gave this invocation
      (``ctx.cli_env_vars``) -- the re-invoked denver inside the container
      re-applies the same flags to its own ctx.env regardless (see
      denver.py's reinvoke_command), but the raw container environment
      itself comes from the image and the compose file, same as above, so
      anything in there that isn't the re-invoked denver (an entrypoint
      script, ``docker inspect``, ...) would otherwise never see them.
    """
    from .context import IN_CONTAINER_VAR, RELOCATED_VAR

    flags = ["-e", f"{IN_CONTAINER_VAR}=1"]
    relocated = ctx.env.get(RELOCATED_VAR)
    if relocated:
        flags += ["-e", f"{RELOCATED_VAR}={relocated}"]
    for name, value in ctx.cli_env_vars.items():
        flags += ["-e", f"{name}={value}"]
    return flags


def _relocation_mounts(ctx):
    """``docker compose run -v`` flags making the running denver reachable inside the container.

    denver re-invokes itself inside the container (see denver.py's
    reinvoke_command) by the very path it runs from on the host, which only
    resolves there if that path is part of the relocated environment.
    Anything under the invocation directory already is -- it is bind-mounted
    at the same absolute path, which is what wrap()'s ``--workdir`` relies on
    too -- so a checkout or an editable install needs nothing here and gets
    no mount, exactly as before.

    Installed anywhere else there is nothing to find: a frozen executable in
    /usr/local/bin, or a wheel's site-packages. That location is bind-mounted
    read-only at its own path, so the re-invocation runs precisely the denver
    that started it rather than whatever the image happens to have (or, as
    was the case, nothing at all).
    """
    source = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else ctx.denver_pkg_dir
    cwd = Path.cwd().resolve()
    if source == cwd or cwd in source.parents:
        return []
    return ["-v", f"{source}:{source}:ro"]


class DockerProvider(Provider):
    """Relocates the final command into a docker compose service -- see doc/providers/docker.md for denver.toml keys."""

    name = "docker"
    kind = "wrapper"
    KEYS = ("exe", "registries", "compose")
    # 'compose.default-cmd' isn't resolved here -- it's read by denver.py's
    # default_command() ('command:' still wins if set) -- but it's a real
    # 'compose:' key, so it's still listed for --show-config's sake.
    COMPOSE_KEYS = ("file", "service", "build", "default-cmd", "image", "run-args")

    def __init__(self, config):
        """Init the bits setup() stashes for wrap() as None, so wrap() can tell "not run yet" from a real value."""
        super().__init__(config)
        self._exe = None
        self._compose_files = None
        self._service = None
        self._run_args = None

    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):  # noqa: ARG003  # shared (ctx, cfg, config) signature
        """Resolve exe/compose.* defaults -- see doc/providers/docker.md."""
        resolved = dict(cfg)
        resolved["exe"] = cfg.get("exe") or "docker"
        resolved["compose"] = cls._resolve_compose(cfg.get("compose") or {})
        return fill_unset(resolved, cls.KEYS)

    @classmethod
    def _resolve_compose(cls, compose):
        """Resolve one 'compose:' subtable's own defaults, after checking it for unknown keys."""
        die_on_unknown_keys(compose, cls.COMPOSE_KEYS, "docker: 'compose:'")
        if not compose.get("file"):
            die("docker: 'compose.file:' is required -- denver never guesses a docker-compose.yml")
        resolved = dict(compose)
        resolved["service"] = compose.get("service") or "dev"
        resolved["build"] = compose.get("build", True)
        resolved["run-args"] = compose.get("run-args") or ["--rm"]
        return fill_unset(resolved, cls.COMPOSE_KEYS)

    def _require_exe(self, ctx, exe):
        """Die unless the configured docker executable is on PATH.

        dry_fallback: --dry-run never starts a container, so previewing an
        env's docker stage on a machine without docker is legitimate and
        shouldn't abort before the compose commands are shown.
        """
        if not ctx.which(exe, dry_fallback=True):
            die(f"docker[{self.stage}]: needs '{exe}' on PATH")

    def _require_compose(self, ctx, exe):
        """Die unless ``<exe> compose`` (the v2 plugin) actually works.

        Compose v2 ships as a plugin subcommand, not a standalone binary, so
        ``exe`` being on PATH (_require_exe) says nothing about whether it's
        installed -- a docker without the plugin (or a standalone v1
        docker-compose only) would otherwise fail deep inside the first
        'compose build'/'compose run' with a bare "docker: 'compose' is not
        a docker command" instead of naming the actual problem up front.

        Skipped entirely under --dry-run, for the same reason _require_exe's
        dry_fallback is: a dry run never actually starts docker compose, so
        previewing an env's docker stage on a machine without the plugin is
        legitimate too.
        """
        if ctx.dry_run:
            return
        result = ctx.run([exe, "compose", "version"], check=False, capture=True, echo=False)
        if result.returncode != 0:
            die(f"docker[{self.stage}]: needs the 'docker compose' (v2) plugin -- '{exe} compose version' failed")

    @staticmethod
    def _resolved_compose_files(ctx, compose):
        """Every 'compose.file:' entry resolved to a path that exists (a lone string is a one-entry list)."""
        files = compose["file"]
        files = [files] if isinstance(files, str) else files
        compose_files = [ctx.resolve_path(f) for f in files]
        for f in compose_files:
            if not f.is_file():
                die(f"docker: compose file not found: {f}")
        return compose_files

    def _resolved_registries(self, cfg, image):
        """'registries:' as configured and validated -- empty when there is no 'image:' to look for."""
        registries = cfg.get("registries") or []
        if not image:
            # nothing to search a registry *for* without a canonical tag --
            # silently treat 'registries:' as unset rather than erroring.
            registries = []
        self._validate_registries(registries)
        return registries

    def _resolve_image_ref(self, ctx, exe, image, registries):
        """Decide which ref $DENVER_DOCKER_IMAGE should be. Returns ``(remote_ref, found_locally)``.

        Local is checked whenever 'image:' is set, regardless of whether
        'registries:' is configured. Remote is checked whenever local missed,
        OR --force is set -- --force means "rebuild even a locally-cached
        image", but a registry that already has it still wins over a forced
        local rebuild, so that case must still look.
        """
        found_locally = bool(image) and self._image_present_locally(ctx, exe, image)
        remote_ref = None
        if registries and (not found_locally or ctx.force):
            remote_ref = self._use_remote_image(ctx, exe, image, registries)
        return remote_ref, found_locally

    def _stash_for_wrap(self, exe, compose_files, compose):
        """Hand the bits setup() resolved over to wrap(), which runs long after setup() returned."""
        self._exe = exe
        self._compose_files = compose_files
        self._service = compose["service"]
        self._run_args = [str(a) for a in compose["run-args"]]

    def setup(self, ctx):
        """Validate the compose files/exe, and build the image if not found locally/remotely."""
        if ctx.in_container:
            die(f"docker[{self.stage}]: already running inside a container")

        # seed convenience variables BEFORE the config section is interpolated,
        # so ${UID}/${GID} in denver.toml resolve correctly.
        ctx.setdefault("UID", str(os.getuid()))
        ctx.setdefault("GID", str(os.getgid()))

        cfg = self.config_section(ctx)

        exe = cfg["exe"]
        self._require_exe(ctx, exe)
        self._require_compose(ctx, exe)

        compose = cfg["compose"]
        compose_files = self._resolved_compose_files(ctx, compose)

        image = compose.get("image")
        registries = self._resolved_registries(cfg, image)

        # bannered first so its own visible work (a registry login's echo)
        # never prints ahead of any banner.
        banner(ctx, self.stage, "prepare")

        remote_ref, found_locally = self._resolve_image_ref(ctx, exe, image, registries)

        # export $DENVER_DOCKER_IMAGE -- the registry ref if one was found,
        # else the bare local tag (empty string if 'image:' is unset) -- so
        # the compose file references the exact same ref denver resolved
        # instead of hard-coding it a second time, e.g.
        # `image: "${DENVER_DOCKER_IMAGE}"`. Every docker-compose subprocess
        # inherits ctx.env, so setting it here, once, centrally, is enough.
        ctx.set("DENVER_DOCKER_IMAGE", remote_ref or image)

        self._stash_for_wrap(exe, compose_files, compose)

        self._build_or_skip(ctx, exe, image, compose, registries, remote_ref, found_locally)

    def wrap(self, ctx, cmd):
        """Turn ``cmd`` into ``docker compose run <run-args> <service> <cmd...>``, bannered after setup()'s own banner."""
        if self._exe is None:
            die(f"docker provider: wrap() called before setup() for stage '{self.stage}' -- this is a denver bug")
        banner(ctx, self.stage, "run")
        # setup() stashes _run_args alongside _exe (see __init__), so the
        # guard above already establishes it's a real list too.
        return [
            self._exe,
            "compose",
            *self._compose_file_args(),
            "run",
            *cast(list, self._run_args),
            *_relocation_env(ctx),
            *_relocation_mounts(ctx),
            # land in the directory denver was invoked from (bind-mounted at
            # the same absolute path in the container), not the image's WORKDIR
            "--workdir",
            str(Path.cwd()),
            self._service,
            *[str(c) for c in cmd],
        ]

    def _validate_registries(self, registries):
        """Die if any 'registries:' entry is missing 'url:', or has just one of username/password."""
        for i, registry in enumerate(registries):
            if not registry.get("url"):
                die(f"docker: registries[{i}] needs a 'url:'")
            die_unless_paired(registry, "username", "password", f"docker: registries[{i}] ('{registry['url']}')")

    def _build_or_skip(self, ctx, exe, image, compose, registries, remote_ref, found_locally):
        """Banner a registry/local hit, run 'compose build', die if nowhere found, or banner a skipped build.

        --fast has no special case here: 'compose.build:' is read exactly
        as configured, so a real 'docker compose build' still runs if
        nothing was found locally/on a registry, --fast or not. The
        'found locally'/'found on a registry' banners above always take
        priority, though. A build never runs without 'compose.image:' either --
        there'd be nothing to check for next run, so it'd rebuild every
        single time regardless of --fast/found_locally.
        """
        already_available = self._already_available_banner(ctx, image, remote_ref, found_locally)
        if already_available:
            banner(ctx, self.stage, already_available)
        elif image and compose["build"]:
            ctx.run(
                [exe, "compose", *self._compose_file_args(), "build", self._service],
                step="build",
            )
        else:
            self._explain_no_build(ctx, image, compose, registries)

    @staticmethod
    def _already_available_banner(ctx, image, remote_ref, found_locally):
        """The banner text for an image that needs no build, or None if it still has to come from somewhere."""
        if remote_ref:
            return f"found '{remote_ref}' on a configured registry, will pull on run"
        if found_locally and not ctx.force:
            return f"image '{image}' found locally, skip build"
        return None

    def _explain_no_build(self, ctx, image, compose, registries):
        """Nothing to build: die if a configured registry missed it, else banner why the build is skipped."""
        if registries:
            urls = [r["url"] for r in registries]
            die(
                f"docker: image '{image}' not found locally or on any of the configured "
                f"registries ({', '.join(urls)}), and compose.build is false"
            )
        if not compose["build"]:
            banner(ctx, self.stage, "build (skipped: compose.build=false)")
        else:
            banner(ctx, self.stage, "build (skipped: 'compose.image:' is not set)")

    def _image_present_locally(self, ctx, exe, image):
        """True if ``image`` already exists in the local docker image cache."""
        result = ctx.run([exe, "image", "inspect", image], check=False, capture=True, echo=False)
        return result.returncode == 0

    def _use_remote_image(self, ctx, exe, image, registries):
        """Check each ``registries`` entry in order (never pulling) for ``image``; return the first hit's ref.

        Logs in first if that entry has 'username:'/'password:' set -- a manifest check on a
        private registry needs auth too. Returns the full ``<url>/<image>`` ref on the first hit
        (and stops there), or None if none of them have it -- setup() falls back to a local build
        (or dies) in that case. Setting $DENVER_DOCKER_IMAGE and the progress banner both happen
        centrally in setup(), not here.
        """
        for registry in registries:
            url = registry["url"]
            if registry.get("username"):
                self._login_registry(ctx, exe, url, registry["username"], registry["password"])
            remote_ref = f"{url}/{image}"
            result = ctx.run([exe, "manifest", "inspect", remote_ref], check=False, capture=True, echo=False)
            if result.returncode == 0:
                return remote_ref
        return None

    def _login_registry(self, ctx, exe, url, username, password):
        """`docker login` into `url`, piping the password via stdin (never argv), before a manifest check."""
        result = ctx.run([exe, "login", url, "-u", username, "--password-stdin"], input=password, check=False)
        if result.returncode != 0:
            die(f"docker: login to '{url}' failed")

    def _compose_file_args(self):
        """Turn ``self._compose_files`` into repeated `-f <file>` flags, in order."""
        args = []
        for f in cast(list, self._compose_files):
            args += ["-f", str(f)]
        return args
