"""Compatibility import for the MCP delivery adapter."""

import sys

from systemlens.delivery import mcp as _implementation

sys.modules[__name__] = _implementation
