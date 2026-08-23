"""Sphinx configuration for denver's documentation.

Builds the existing hand-written doc/*.md tree (the same files GitHub
renders directly) into a static site via MyST -- no reST, no content moved.
`doc/README.md` is excluded from the build (see exclude_patterns below): it
plays the role GitHub gives it, a folder-browsing landing page, while
`index.md` plays that role for the built site instead.

Run `uv run poe docs` to build -- it delegates to `examples/doc-env` (a
denver env whose own 'build-docs' stage invokes sphinx-build from a venv its
'docs-tools' stage manages), rather than calling sphinx-build directly, so
this project's own docs are built the same way any denver env builds
anything. That stage builds this same source tree twice, once per builder:
'html' (output goes to doc/_build/html, gitignored) and 'markdown' (via the
sphinx_markdown_builder extension below), the latter copied into
doc/_build/html/markdown/ so a single publish step carries both -- a plain,
one-file-per-page Markdown mirror alongside the rendered site, meant for AI
tools/LLMs to fetch directly. `.github/workflows/docs.yml` runs the same
build and publishes the combined result to the gh-pages branch.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

project = "denver"
copyright = "denver contributors"
author = "denver contributors"

try:
    # matches the installed distribution's version (setuptools-scm, derived
    # from git tags -- see pyproject.toml's [tool.setuptools_scm]). Falls
    # back quietly so a docs-only checkout without the package installed, or
    # a shallow/tagless one, still builds.
    release = version("denver-tool")
except PackageNotFoundError:
    release = "0.0.0"
version = release

# -- General configuration --------------------------------------------------

extensions = [
    "myst_parser",
    "sphinx_copybutton",
    # provides the 'markdown' builder -- see this file's docstring.
    "sphinx_markdown_builder",
]

# doc/*.md is CommonMark/GFM (as GitHub renders it) -- these extensions cover
# the GFM features actually used across doc/ (fenced code, tables,
# blockquotes) plus a couple of MyST conveniences.
myst_enable_extensions = [
    "colon_fence",
    "deflist",
]
# several pages cross-link as "configuration.md#some-heading"; depth 3
# covers every heading level those anchors target.
myst_heading_anchors = 3

source_suffix = {
    ".md": "markdown",
}

# the toctree hub is index.md, not README.md -- see this file's docstring.
# 'bak' is the pre-restructure doc tree (see this file's docstring on the
# Sphinx restructure): gitignored, kept locally for reference, never a
# source the built site should read.
exclude_patterns = ["README.md", "bak", "_build", "Thumbs.db", ".DS_Store"]

root_doc = "index"

# every doc/*.md link that used to point outside this tree ('../README.md',
# 'providers/' as a bare directory, '../examples/...') has been rewritten to
# an absolute GitHub URL instead (see the 'docs' poe task and
# .github/workflows/docs.yml: both build with -W, so a warning fails the
# build -- nothing here is suppressed).
nitpicky = True

# -- Options for HTML output -------------------------------------------------

html_theme = "sphinx_rtd_theme"
html_title = "denver"

# shown at the top of the left sidebar, above the search box. Relative to
# this file (confdir), reaching out to the same logo the CLI prints and the
# READMEs embed -- Sphinx copies it into _static/ at build time, so the
# published site carries its own copy rather than hotlinking GitHub.
html_logo = "../src/denver_assets/logo.svg"

# browser tab icon -- the logo's staircase mark alone, square-cropped.
# Modern browsers render SVG favicons directly; Sphinx copies it into
# _static/ the same way it does html_logo above.
html_favicon = "../src/denver_assets/favicon.svg"

# doc/_static/custom.css overrides two of sphinx_rtd_theme's own defaults
# that don't suit a docs-only site: a fixed 800px content column (leaving
# most of a normal monitor empty) and `white-space: nowrap` on every inline
# code span, which stops a table cell containing one from wrapping at all --
# see that file's own comments for both. It also boxes plain blockquotes and,
# together with custom.js, turns GitHub's '> [!NOTE]'-style blockquote
# alerts into the same colored, labeled boxes GitHub itself renders them as.
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_js_files = ["custom.js"]

html_theme_options = {
    "collapse_navigation": False,
    # sphinx_rtd_theme's sidebar ignores each {toctree}'s own :maxdepth: and
    # rebuilds the nav itself at this depth instead, pulling every H2/H3 in
    # every page in as its own sidebar entry. 3 keeps that in-page nav (it's
    # genuinely useful on the bigger reference pages) -- a page-local "further
    # reading" list that shouldn't be a nav entry at all (a repo-browsing
    # affordance, not a subsection) is written as a {note}/{admonition}
    # instead of a heading, so it never becomes a section to begin with.
    "navigation_depth": 3,
}

# "Edit on GitHub" flyout -- sphinx_rtd_theme reads these from html_context
# rather than a theme option (that's a leftover from readthedocs.org itself,
# which templates it in for hosted builds; a self-built site has to supply it).
html_context = {
    "display_github": True,
    "github_user": "thorsten-klein",
    "github_repo": "denver",
    "github_version": "develop",
    "conf_py_path": "/doc/",
}
