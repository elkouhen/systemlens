"""Compatibility import for the static-file delivery adapter."""

import sys

from systemlens.delivery import simpleweb as _implementation

sys.modules[__name__] = _implementation
