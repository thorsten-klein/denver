"""``DenverError`` and ``die()`` -- the one place both denver.py and denver_providers/ raise/catch a fatal error.

A leaf module on purpose, with no dependents in either direction: denver.py
avoids importing denver_providers eagerly (denver_providers/__init__.py
imports every provider module -- conan, docker, uv, zephyr, ... -- which
--help/--version don't need), and denver_providers avoids importing denver.py
at all, to stay a self-contained package. Both need the exact same
exception type (so main() can catch it regardless of which side raised it)
and the exact same die(), so both live here instead of being defined twice.
"""

from __future__ import annotations

import logging
from typing import NoReturn

logger = logging.getLogger("denver")


class DenverError(Exception):
    """A user-facing failure, reported via die() -- caught once, in denver.py's main(), and turned into exit 1.

    Never meant to be caught anywhere else: a provider (or any other caller)
    that wants to say something *more specific* about a failure should
    catch whatever it can act on (OSError, CalledProcessError, ...) and
    call die() itself with that context, rather than catch a DenverError
    that's already been logged.
    """


def die(message) -> NoReturn:
    """Log ``message`` as an error and raise DenverError -- main() turns that into exit 1."""
    logger.error(message)
    raise DenverError(message)
