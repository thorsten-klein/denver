<!--
PRs go to the `develop` branch. doc/development.md has the long version of
this; the template is only the short one.
-->

## What this changes

<!-- What is different for a user after this PR. -->

## Why

<!--
The problem it solves. If it fixes an open issue, write "Fixes #123" here
and the issue closes when this is merged.
-->

## How you tested it

<!--
More than "CI is green": the command you ran, the env you ran it on, or the
test that fails without your change.
-->

## Checklist

- [ ] `uv run poe all` passes on my machine (lint, format, mypy, tests, 100% coverage).
- [ ] There are tests for the new behavior, not only tests that happen to run it.
- [ ] If a resolved config changed, I regenerated the files in `tests/golden/`
      instead of editing them by hand.
- [ ] I updated the docs if a setting, a default, or a provider changed
      (`doc/`, `README.md`).
- [ ] I updated or added an example under `examples/` if a working
      `denver.yml` now looks different.
- [ ] My commit messages say *why*, not only *what*.

## Does this break anything?

<!--
Delete this part if nothing applies. Otherwise: what people with an existing
denver.yml/denver.toml, lockfile, or cached env have to do now -- and whether
denver tells them, or just behaves differently.
-->

- [ ] Existing `denver.yml`/`denver.toml` files behave differently after this.
- [ ] A command line flag changed (its name, its default, or what it prints).
