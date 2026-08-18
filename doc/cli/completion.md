# Shell completion

`denver complete` prints a completion script for the installed `denver`
command, for bash, zsh or fish. Wire it up once in your shell rc file:

```bash
# bash / zsh (~/.bashrc or ~/.zshrc)
eval "$(denver complete)"
```

```fish
# fish (~/.config/fish/config.fish)
denver complete | source
```

With no shell named, `denver complete` auto-detects it from the parent
process invoking it — which is exactly what's doing the invoking above, so
in practice you never have to pass one. Only spell it out explicitly
(`denver complete zsh`) if that guess is ever wrong, e.g. under tmux, `su`,
or some other login-shell chain that puts an unrelated process in between.

## Works no matter how you invoke it

The printed script wires completion to every command word you could
plausibly type, not just an installed, on-PATH `denver`: the literal word
`denver`, however this `denver complete` was actually invoked (e.g. a
checkout's `./src/denver.py`), and the absolute path to that same
script/executable — deduplicated, so an already-installed `denver` prints
just the one line you'd expect. Run it from a checkout and you get all
three for free, no alias needed:

```console
$ ./src/denver.py complete bash
# denver bash completion -- wire up with: eval "$(denver complete)"
_denver_complete() { ... }
complete -F _denver_complete -o default -o bashdefault denver ./src/denver.py /home/you/denver/src/denver.py
```

Each registered name shares the same function, and that function re-invokes
whichever one you actually typed (bash's `${COMP_WORDS[0]}`, zsh's
`${words[1]}`, fish's `$tokens[1]`) rather than a hardcoded `denver` — so
`./src/denver.py <TAB>` and `/home/you/denver/src/denver.py <TAB>` both work
correctly after sourcing the exact same script `denver <TAB>` uses. This
only helps when the script itself is the word typed at the prompt (its
shebang makes `./src/denver.py` directly executable) — `python3
src/denver.py <TAB>` still can't be completed this way, since bash keys
completion off the first word (`python3`), not the script path after it.

A bash/zsh `alias denver=/path/to/denver.py` also works: the printed
script resolves the typed word through `BASH_ALIASES`/`$aliases` before
re-invoking it, since alias expansion itself happens at parse time, on the
literal token, never on a variable holding it.

## What gets completed

Every `<TAB>` shells out to a hidden `denver __complete` subcommand that
inspects the real, current state to answer, so completion always tracks
whatever's true right now rather than whatever was true when you sourced
the script — the only part of the printed script that varies by *how* you
invoked `denver complete` is the registration line above; the completion
logic itself never has to be regenerated:

- `run` and `complete`, the two subcommands themselves
- `<env>` — directories and `denver.toml`/`denver.*.toml` files on disk
- denver's own `run` flags (`--fast`, `--until`, `--skip`, `--scripts`, ...)
- `--until`/`--skip`'s values — the stage ids the given `<env>` actually
  declares
- `--scripts`' value — the names that env's `scripts:` sections actually
  define
- an env's own `args:` — whatever extra flags its `denver.toml` declares (see
  "Environment-specific arguments" in
  [Configuration](../configuration/denver-toml.md)), completed the same way
  denver's own flags are

That last point is why there's nothing to regenerate: add a stage, rename a
`scripts:` entry, declare a new `args:` flag, and completion picks it up on
the next `<TAB>` — the script you sourced once never has to change.

## Inside a docker-relocated shell, automatically

A `docker` stage that lands you in a bare interactive `bash`/`zsh`/`fish`
(via `command:`, the docker stage's own `default-cmd:`, or the plain
`$SHELL`/`bash` fallback) gets this wired up for you, with no setup of your
own: denver routes the shell through `denver complete <shell>` before
handing over control. The container's own image, unlike your host, is never
somewhere you already have completion configured — so denver configures it
for that one shell, every time. See "`default-cmd`" in
[docker](../providers/docker.md) for the exact conditions.

```{note}
**Next:** [Environment variables](environment-variables.md) — the two
variables denver itself reads, and where an environment's state lives on
disk.
```
