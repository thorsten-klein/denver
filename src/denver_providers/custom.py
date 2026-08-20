"""custom provider: run an arbitrary shell command as a pipeline stage, or wrap the final command through scripts of its own.

Configured from denver.toml -> a stage declaring ``provider: custom``, via
``cmd:``/``source:``/``launcher:`` (at least one required).

Full key reference, worked examples and design notes: ``doc/providers/custom.md``.
"""

import shlex

from .base import Provider
from .context import banner, die, info


class CustomProvider(Provider):
    """Runs 'cmd:'/'source:' and/or wraps the final command via 'launcher:' -- see doc/providers/custom.md."""

    name = "custom"
    KEYS = ("cmd", "source", "launcher")

    @property
    def kind(self):  # pyright: ignore[reportIncompatibleVariableOverride] -- read-only override of Provider's plain str attribute, see below
        """'wrapper' if this stage's own section sets a (non-empty) 'launcher:', else 'setup'.

        A property, not set once in __init__: make_stage() (see
        denver_providers/__init__.py) only overwrites self.stage with the
        real stage id *after* construction -- self.stage is still 'custom'
        (Provider.__init__'s default, from self.name) while __init__ itself
        runs. Caching the answer there would silently misclassify every
        custom stage not literally named 'custom' as 'setup' even with
        'launcher:' configured, since self.config.get('custom') is empty for
        any other stage id. Computed fresh here instead, once self.stage is
        whatever make_stage() actually set it to. Nothing assigns
        ``.kind`` externally (grep confirms), so the narrower read-only
        surface a property has, versus Provider's plain attribute, costs
        nothing in practice.
        """
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
        """Run 'cmd:' via bash -c -- or, under --fast, report it as skipped instead.

        Banners with the key's own name ('cmd'), not a generic 'run' -- a
        stage combining 'cmd:'/'source:'/'launcher:' would otherwise show
        the same label for every one of them, hiding which is actually
        happening (see _source_script and wrap() for the other two).
        """
        if not cmd:
            return
        # logged stripped, not raw: a TOML triple-quoted 'cmd:' (the common
        # style for anything longer than one line) carries its own trailing
        # newline before the closing '''; embedding that raw would print a
        # stray blank line after this one instead of ending it cleanly. bash
        # itself doesn't care about the surrounding whitespace either way,
        # so only the logged text is stripped -- the executed cmd is not.
        logged_cmd = cmd.strip()
        if ctx.fast:
            banner(ctx, self.stage, "cmd (skipped by --fast)")
            info(f"custom[{self.stage}]: --fast skips '{logged_cmd}'")
            return
        banner(ctx, self.stage, "cmd")
        info(f"custom[{self.stage}]: run cmd: {logged_cmd}")
        ctx.run(["bash", "-c", cmd])

    def _source_script(self, ctx, source):
        """Source 'source:' into ctx.env -- always, --fast and --dry-run included (see doc/providers/custom.md)."""
        if not source:
            return
        path = ctx.resolve_path(source)
        if not path.is_file():
            die(f"custom[{self.stage}]: 'source' script not found: {path}")
        banner(ctx, self.stage, "source")
        info(f"custom[{self.stage}]: source {path}")
        ctx.source(path)

    def setup(self, ctx):
        """Run 'cmd:' via bash -c (unless --fast) and/or source 'source:' (always)."""
        cfg = self.config_section(ctx)
        cmd = cfg.get("cmd")
        source = cfg.get("source")
        launcher = cfg.get("launcher")
        self._validate_cfg(cmd, source, launcher)

        self._run_cmd(ctx, cmd)
        self._source_script(ctx, source)

    def wrap(self, ctx, cmd):
        """Prepend every 'launcher:' entry's shell-split tokens, in order, to cmd."""
        prefix = [token for entry in self.config_section(ctx).get("launcher") or [] for token in shlex.split(entry)]
        if prefix:
            info(f"custom[{self.stage}]: run launcher: {shlex.join(prefix)}")
        return [*prefix, *cmd]
