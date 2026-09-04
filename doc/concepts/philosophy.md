# Philosophy

These are the principles that shape every provider and every design
decision in denver — the *why* behind what [Configuration](../configuration/denver-toml.md)
describes as *what*. None of them are abstract for their own sake; each one
exists because its absence caused a real, specific kind of pain somewhere
else.

## Genericity

denver itself holds no per-provider knowledge — it only calls whichever
provider type a stage's `provider:` key names. A provider is a generic,
reusable engine; every project-specific detail comes from `denver.yml`,
never from the provider's own code.

The test this principle has to pass: could this provider be dropped into a
completely unrelated project's `denver.yml`, with zero code changes, and
just work off that project's own config? If a provider ever needs an `if
project == "x"` branch, genericity has already failed.

## Explicit over implicit

denver never guesses that a file exists because it happens to sit in a
conventional place. There's no "if there's a `docker-compose.yml` next to
the `denver.yml`, use it," no "if there's a `conan/recipes` directory,
scan it." Every path an env needs — a compose file, a recipe dir, a
skip-on-success/skip-on-failure script — is named explicitly, and a venv patch step is
run only if `patches-apply:` names the exact command for it.

What this buys: an env's `denver.yml` is a complete, honest description of
what it does. Reading it (or running `--show-config-full`, which — unlike
the minimal-by-default `--show-config` — shows every key a stage's own
provider understands, unset ones included) tells you everything; nothing is
hidden in directory layout that you'd only discover by tripping over it.
The cost is a few extra lines in the config for anything that
*does* follow a convention — a trade denver makes deliberately, on the
theory that a wrong guess is far more expensive than an explicit line.

## Central default resolution

Every default a provider might fall back to — a PATH lookup, a
conventional value, anything — is computed once, centrally, before any
stage's real work begins. A provider's own build/setup step never guesses a
default itself; it only ever reads what's already been resolved.

What this buys: `--show-config` and a real run can never drift apart. If a
value looks wrong in `--show-config`, it is exactly as wrong in the real
run — there is no separate "what the code actually decides at run time" to
go check. Most tools with a "show me the effective config" mode can't make
this promise, because their config resolution is scattered across the
codepath that actually runs things; denver's can, because resolution and
execution are deliberately kept as two separate steps.

## Fail loud on the unexpected

An unrecognised top-level key, an unrecognised key in a stage's own
section, a `--until`/`--skip` naming a stage id that isn't declared — all
of these are hard errors, immediately, not silently ignored.

The alternative — quietly doing nothing with a key nobody recognises — is
how a typo'd or orphaned config key survives for months unnoticed. If a key
doesn't do anything, denver says so instead of pretending everything is
fine.

## Fast by default, never at the cost of correctness

Fingerprinting exists so that re-running an unchanged environment is cheap.
But speed is never allowed to compromise the one guarantee that actually
matters: what `--show-config` shows is what runs. When there's tension
between "skip this because it's probably unchanged" and "always resolve
correctly," correctness wins, and `--force` exists as the deliberate,
explicit escape hatch for the rare case a fingerprint gets it wrong.


## Reproducibility as a first-class goal

The same `denver.yml`, run on a fresh clone and on a six-month-old working
copy, should produce the same environment — not one shaped by whatever
history happens to be sitting on that particular machine.

> [!NOTE]
> For full reproducibility you need to upload the docker container and the
> conan packages to some artifactory, so that denver downloads them prebuilt each
> instead of re-building it again.
