"""Instrumented tool layer.

Importing this package registers every tool handler with the dispatcher.
Public surface:

    from agent_range.tools import execute, ToolCall

    result = execute(ToolCall(tool="read_file", args={"path": "repo/README.md"}))
"""
from __future__ import annotations

from .base import ToolCall, ToolResult, execute, registered_tools

# Import side-effects register handlers.
from . import fs as _fs  # noqa: F401
from . import shell as _shell  # noqa: F401
from . import db as _db  # noqa: F401
from . import cloud as _cloud  # noqa: F401
from . import git as _git  # noqa: F401

__all__ = ["ToolCall", "ToolResult", "execute", "registered_tools"]
