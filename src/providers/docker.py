"""docker provider: run the command inside a docker compose service.

This is a *wrapper* provider -- it does not build the local environment, it
relocates the final command into a container.

Configured from denver.yml -> ``docker:``:

    docker:
      exe: docker
      default-cmd: fish                # fallback interactive command once
                                        # relocated into the container (read by
                                        # denver.py's default_command(), not
                                        # this provider -- 'command:' still wins
                                        # if set)
      image: "myproject:dev"           # canonical local tag -- must match the
                                        # compose file's own service `image:`.
      registries:                      # ordered list of registries to check
                                        # (each as `<url>/<image>`, via `docker
                                        # manifest inspect` -- never pulled here)
                                        # before falling back to a build -- local
                                        # cache first, then each entry in turn;
                                        # first hit wins and $DENVER_DOCKER_IMAGE
                                        # points at it, so `docker compose run`
                                        # pulls it lazily, on demand
      - url: registry.internal.example:5000  # 'username'/'password' are optional --
        username: myusername           # when both are set, 'docker login'
        password: ${DOCKER_PASSWORD}   # runs automatically (password via
                                        # stdin, never argv) right before the
                                        # manifest check against this entry;
                                        # both fields go through denver's normal
                                        # ${VAR} interpolation, so a literal and
                                        # an env-sourced secret look the same.
      - url: registry.example.com      # no username/password -> no login,
                                        # assumed already-authenticated/public
      compose:
        file: docker-compose.yml       # REQUIRED: a single path, or a list for
                                        # multiple `-f` overlays (base + override)
        service: dev
        build: true
        args: []                       # extra `docker compose` args
      run-args: ["--rm"]               # extra `docker compose run` args
      env-scripts:                     # script(s) run before build/run, e.g. to
      - create-env.sh                  #   write a compose .env file themselves

denver has no notion of a compose env-file -- it just runs env-scripts and
lets docker compose/the scripts sort out their own file naming and lookup.

Every default above (exe, compose.service/build, run-args) is computed
once, centrally, by ``DockerProvider.resolve_defaults`` -- not in setup().
By the time this provider's setup() runs, its config section already has
every default filled in (see ``denver.resolve_provider_defaults``), so
nothing here ever falls back to a conventional value itself.

``compose.file`` has no default: denver never guesses that a
``docker-compose.yml`` next to the ``denver.yml`` is the one meant. An env
must name its compose file(s) explicitly.

Whenever ``image:`` is set (``registries:`` or not), setup() first checks
the local image cache; a hit skips the build entirely. ``registries`` is
empty by default -- nothing changes for an env that doesn't set it. When
set, and the image is missing locally, setup() checks each entry in turn
via ``docker manifest inspect`` -- never a real ``docker pull`` -- before
ever considering a build; if none of them have it and ``compose.build:
false``, that's a hard error rather than a silent no-op. Each entry's
``url:`` is required; ``username:``/``password:`` are optional but, if
either is set, both must be -- when present, ``docker login`` runs
automatically (credentials via stdin, never argv) right before the
manifest check against that entry; an entry with neither is assumed
already-authenticated or public. The actual pull, if any, happens lazily
later, when ``docker compose run`` itself needs the image.

``--force`` rebuilds even a locally-cached image -- but a registry that
already has it still wins over a forced local rebuild, so the local hit
no longer short-circuits the registries search in that case; a rebuild
only actually happens if none of them have it either.

denver's --fast is not consulted anywhere in this provider's setup() --
``compose.build:`` is read exactly as configured, so a real `docker compose
build` still runs under --fast if the image wasn't found locally or on a
registry. There is no "skip the rebuild, just activate" fast path here the
way uv/conan/zephyr have one.

A build (``compose.build: true``, the default) never actually runs without
``image:`` set, regardless of --fast/--force: with no tag to check next
time, it would just rebuild on every single run. An env that wants
denver's own build-once behavior must set ``image:``; one that doesn't
gets ``compose.build:`` fully ignored -- ``docker compose run`` still
builds it itself if the compose file's own ``build:`` section is set and
the image doesn't exist, denver just never calls ``compose build`` for it.

Whatever ``image:`` resolves to (empty string if unset) is exported as
``$DENVER_DOCKER_IMAGE`` before env-scripts/build/run -- so the compose file
can say ``image: "${DENVER_DOCKER_IMAGE}"`` instead of hard-coding the same
tag a second time.

``docker`` with the Compose plugin (every command here is a ``docker
compose ...`` one, v2 -- not the standalone ``docker-compose`` script) must
already be available, with a reachable daemon, on the machine this stage
runs on -- denver never installs it.

Full key reference, worked examples and design notes: ``doc/providers/docker.md``.
"""

import os
from pathlib import Path

from .base import Provider, fill_unset
from .context import banner, die


class DockerProvider(Provider):
    """Relocates the final command into a docker compose service -- see module docstring for denver.yml keys."""

    name = "docker"
    kind = "wrapper"
    # 'default-cmd' isn't resolved here -- it's read by denver.py's
    # default_command() ('command:' still wins if set) -- but it's a real
    # docker: key, so it's still listed for --show-config's sake.
    KEYS = ("exe", "default-cmd", "image", "registries", "compose", "run-args", "env-scripts")

    def __init__(self, config):
        """Init the bits setup() stashes for wrap() as None, so wrap() can tell "not run yet" from a real value."""
        super().__init__(config)
        self._exe = None
        self._compose_files = None
        self._compose_args = None
        self._service = None
        self._run_args = None

    @classmethod
    def resolve_defaults(cls, ctx, cfg, config):  # noqa: ARG003 -- shared (ctx, cfg, config) signature
        """Resolve exe/compose.file/compose.service/compose.build/run-args defaults -- see module docstring."""
        resolved = dict(cfg)
        resolved["exe"] = cfg.get("exe") or "docker"
        compose = dict(cfg.get("compose") or {})
        if not compose.get("file"):
            die("docker: 'compose.file:' is required -- denver never guesses a docker-compose.yml")
        compose["service"] = compose.get("service") or "dev"
        compose["build"] = compose.get("build", True)
        resolved["compose"] = fill_unset(compose, ["args"])
        resolved["run-args"] = cfg.get("run-args") or ["--rm"]
        return fill_unset(resolved, cls.KEYS)

    def setup(self, ctx):
        """Validate the compose files/exe, run env-scripts, and build the image if not found locally/remotely."""
        if ctx.in_docker:
            die("docker provider: already running inside a container")

        # seed convenience variables BEFORE the config section is interpolated,
        # so ${UID}/${GID} in denver.yml resolve correctly.
        ctx.setdefault("UID", str(os.getuid()))
        ctx.setdefault("GID", str(os.getgid()))

        cfg = self.config_section(ctx)

        exe = cfg["exe"]
        if not ctx.which(exe):
            die(f"docker provider needs '{exe}' on PATH")

        compose = cfg["compose"]
        files = compose["file"]
        files = [files] if isinstance(files, str) else files
        compose_files = [ctx.resolve_path(f) for f in files]
        for f in compose_files:
            if not f.is_file():
                die(f"docker: compose file not found: {f}")

        image = cfg.get("image")
        registries = cfg.get("registries") or []
        if not image:
            # nothing to search a registry *for* without a canonical tag --
            # silently treat 'registries:' as unset rather than erroring.
            registries = []
        for i, registry in enumerate(registries):
            if not registry.get("url"):
                die(f"docker: registries[{i}] needs a 'url:'")
            if bool(registry.get("username")) != bool(registry.get("password")):
                die(f"docker: registries[{i}] ('{registry['url']}') needs both 'username:' and 'password:', or neither")

        # bannered first so its own visible work (a registry login's echo,
        # an env-script's echo/output) never prints ahead of any banner,
        # the way create-env.sh's output used to.
        banner(ctx, self.stage, "prepare")

        # Decide which ref $DENVER_DOCKER_IMAGE should be. Local is checked
        # whenever 'image:' is set, regardless of whether 'registries:' is
        # configured. Remote is checked whenever local missed, OR --force is
        # set -- --force means "rebuild even a locally-cached image", but a
        # registry that already has it still wins over a forced local
        # rebuild, so that case must still look.
        found_locally = bool(image) and self._image_present_locally(ctx, exe, image)
        remote_ref = None
        if registries and (not found_locally or ctx.force):
            remote_ref = self._use_remote_image(ctx, exe, image, registries)

        # export $DENVER_DOCKER_IMAGE -- the registry ref if one was found,
        # else the bare local tag (empty string if 'image:' is unset) -- so
        # the compose file, and any env-scripts run below, reference the
        # exact same ref denver resolved instead of hard-coding it a second
        # time, e.g. `image: "${DENVER_DOCKER_IMAGE}"`. Every
        # docker-compose/env-script subprocess inherits ctx.env, so setting
        # it here, once, centrally, is enough.
        ctx.set("DENVER_DOCKER_IMAGE", remote_ref or image)

        self._run_env_scripts(ctx, cfg)

        # stash resolved bits for wrap()
        self._exe = exe
        self._compose_files = compose_files
        self._compose_args = [str(a) for a in (compose.get("args") or [])]
        self._service = compose["service"]
        self._run_args = [str(a) for a in cfg["run-args"]]

        # --fast has no special case here: 'compose.build:' is read exactly
        # as configured, so a real 'docker compose build' still runs if
        # nothing was found locally/on a registry, --fast or not. The
        # 'found locally'/'found on a registry' banners above always take
        # priority, though. A build never runs without 'image:' either --
        # there'd be nothing to check for next run, so it'd rebuild every
        # single time regardless of --fast/found_locally.
        if remote_ref:
            banner(ctx, self.stage, f"found '{remote_ref}' on a configured registry, will pull on run")
        elif found_locally and not ctx.force:
            banner(ctx, self.stage, f"image '{image}' found locally, skip build")
        elif image and compose["build"]:
            ctx.run(
                [exe, "compose", *self._compose_file_args(), *self._compose_args, "build", self._service],
                step="build",
            )
        elif registries:
            urls = [r["url"] for r in registries]
            die(
                f"docker: image '{image}' not found locally or on any of the configured "
                f"registries ({', '.join(urls)}), and compose.build is false"
            )
        elif not compose["build"]:
            banner(ctx, self.stage, "build (skipped: compose.build=false)")
        else:
            banner(ctx, self.stage, "build (skipped: 'image:' is not set)")

    def wrap(self, ctx, cmd):
        """Turn ``cmd`` into ``docker compose run <run-args> <service> <cmd...>``, bannered after setup()'s own banner."""
        if self._exe is None:
            die(f"docker provider: wrap() called before setup() for stage '{self.stage}' -- this is a denver bug")
        banner(ctx, self.stage, "run")
        return [
            self._exe,
            "compose",
            *self._compose_file_args(),
            *self._compose_args,
            "run",
            *self._run_args,
            # land in the directory denver was invoked from (bind-mounted at
            # the same absolute path in the container), not the image's WORKDIR
            "--workdir",
            str(Path.cwd()),
            self._service,
            *[str(c) for c in cmd],
        ]

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
        for f in self._compose_files:
            args += ["-f", str(f)]
        return args

    # ------------------------------------------------------------------ #
    def _run_env_scripts(self, ctx, cfg):
        """Run each 'env-scripts:' entry (host-side, before build/run) -- e.g. to write a compose .env file."""
        entry = cfg.get("env-scripts")
        scripts = [entry] if isinstance(entry, str) else (entry or [])
        for script in scripts:
            script_path = ctx.resolve_path(script)
            if not script_path.is_file():
                die(f"docker: env script not found: {script_path}")
            ctx.run([str(script_path)])
