"""The default toolset.

Importing this module builds a :class:`ToolRegistry` populated with a small,
genuinely useful set of tools. Add your own by writing a function and decorating
it with ``@registry.tool`` here (or in your own module).
"""

from __future__ import annotations

from .base import Tool, ToolRegistry

# One shared registry the CLI uses by default.
registry = ToolRegistry()

# Registering happens as a side effect of importing these modules.
from . import files as _files  # noqa: E402,F401
from . import web as _web  # noqa: E402,F401
from . import python as _python  # noqa: E402,F401

__all__ = ["Tool", "ToolRegistry", "registry"]
