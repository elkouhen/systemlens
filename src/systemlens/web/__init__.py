"""Compatibility import for the local web delivery adapter."""

import sys

from systemlens.delivery import web as _implementation

sys.modules[__name__] = _implementation
