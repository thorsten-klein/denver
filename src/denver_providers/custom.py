"""custom provider: run an arbitrary shell command as a pipeline stage, or wrap the final command through scripts of its own.

Configured from denver.yml -> a stage declaring ``provider: custom``::

    my-stage:
      provider: custom
      cmd: "echo hello"
      source: my-stage.sh        # optional, see below
      launcher:                  # optional, see below
      - myscript.sh --
      - otherscript.sh --

``cmd`` is run via ``bash -c`` in an isolated subprocess (``ctx.run()``), so
ordinary shell syntax (pipes, ``&&``, quoting, ``$VAR`` expansion against
``ctx.env``) works the same way it would on a command line -- but anything it
exports dies with that subprocess, denver never sees it.

``source`` is different: it names a script *sourced* (``ctx.source()``, not
run) right after ``cmd``, so its exports are folded into ``ctx.env`` and
persist into every later stage and the final command -- the way to make a
``custom`` stage hand env vars forward, scoped to this one stage's section
rather than the global ``hooks:`` mechanism.

``launcher`` makes this stage a wrapper, the same way ``docker`` is one:
instead of (only) doing its own work, it prepends its own script(s) ahead of
whatever command would otherwise run. Each entry is split shell-style
(``shlex.split()``, so quoted args stay together) into its own tokens, and
every entry's tokens land in order, ahead of the actual command -- so::

    launcher:
    - myscript.sh --
    - otherscript.sh --

turns a resolved command of ``<cmd>`` into ``myscript.sh -- otherscript.sh --
<cmd>``. ``cmd:``/``source:`` (if also given) still run as usual during this
stage's own setup -- ``launcher:`` only changes what happens to the *final*
command. At least one of ``cmd``/``source``/``launcher`` must be given.

An arbitrary command has no "already built, just activate" state denver can
reason about, so under --fast ``cmd`` is skipped. ``source`` still runs under
--fast: it's what later stages'/the final command's env depends on, not a
build step, so skipping it would break the very propagation it exists for.
``launcher`` is likewise never skipped under --fast -- relocating the command
isn't a build step either.

Under --dry-run ``cmd`` is printed rather than run, while ``source`` is still
sourced -- for the same reason it survives --fast: its exports are what every
later stage's commands are rendered against.

Full key reference, worked examples and design notes: ``doc/providers/custom.md``.
"""

import shlex

from .base import Provider
from .context import banner, die, info


class CustomProvider(Provider):
    """Runs 'cmd:'/'source:' and/or wraps the final command via 'launcher:' -- see module docstring."""

    name = "custom"
    KEYS = ("cmd", "source", "launcher")

    @property
    def kind(self):
        """'wrapper' if this stage's own section sets a (non-empty) 'launcher:', else the default 'setup'."""
        return "wrapper" if (self.config.get(self.stage) or {}).get("launcher") else "setup"

    def _validate_str(self, value, key):
        """Die unless ``key``'s value is unset or a non-empty string."""
        if value is None:
            return
        if not isinstance(value, str) or not value.strip():
            die(f"custom[{self.stage}]: '{key}' must be a non-empty string")

    def _validate_launcher(self, launcher):
        """Die unless 'launcher:' is unset or a list of non-empty strings."""
        if launcher is None:
            return
        if not isinstance(launcher, list) or not all(isinstance(w, str) and w.strip() for w in launcher):
            die(f"custom[{self.stage}]: 'launcher' must be a list of non-empty strings")

    def _validate_cfg(self, cmd, source, launcher):
        """Die unless cmd/source/launcher are well-typed and at least one is given."""
        self._validate_str(cmd, "cmd")
        self._validate_str(source, "source")
        self._validate_launcher(launcher)
        if not cmd and not source and not launcher:
            die(f"custom[{self.stage}]: at least one of 'cmd'/'source'/'launcher' must be given")

    def _run_cmd(self, ctx, cmd):
        """Run 'cmd:' via bash -c -- or, under --fast, report it as skipped instead."""
        if not cmd:
            return
        if ctx.fast:
            info(f"custom[{self.stage}]: --fast skips '{cmd}'")
            return
        ctx.run(["bash", "-c", cmd])

    def _source_script(self, ctx, source):
        """Source 'source:' into ctx.env -- always, --fast and --dry-run included (see module docstring)."""
        if not source:
            return
        path = ctx.resolve_path(source)
        if not path.is_file():
            die(f"custom[{self.stage}]: 'source' script not found: {path}")
        info(f"custom[{self.stage}]: source {path}")
        ctx.source(path)

    def setup(self, ctx):
        """Run 'cmd:' via bash -c (unless --fast) and/or source 'source:' (always)."""
        cfg = self.config_section(ctx)
        cmd = cfg.get("cmd")
        source = cfg.get("source")
        launcher = cfg.get("launcher")
        self._validate_cfg(cmd, source, launcher)

        banner(ctx, self.stage, "run")
        self._run_cmd(ctx, cmd)
        self._source_script(ctx, source)

    def wrap(self, ctx, cmd):
        """Prepend every 'launcher:' entry's shell-split tokens, in order, to cmd."""
        prefix = [token for entry in self.config_section(ctx).get("launcher") or [] for token in shlex.split(entry)]
        return [*prefix, *cmd]
