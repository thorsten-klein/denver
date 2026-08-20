"""Bundled docker-host setup scripts, run standalone as subprocesses.

See providers/docker.py's module docstring -- these are not imported as a
package at runtime. This __init__.py exists so denver's own test suite can
import them as providers.docker_scripts.*.
"""
