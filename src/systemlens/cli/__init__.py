"""Compatibility import for the CLI delivery adapter."""

import sys

from systemlens.delivery import cli as _implementation

sys.modules[__name__] = _implementation
