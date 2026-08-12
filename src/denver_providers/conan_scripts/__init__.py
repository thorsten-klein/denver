"""Bundled scripts run standalone (as a subprocess, in the target env's own venv) by conan.py.

See recipes.py's docstring. This __init__.py exists so denver's own test
suite can import them as providers.conan_scripts.*; it is not required for
(and not relied on by) the real runtime invocation.
"""
