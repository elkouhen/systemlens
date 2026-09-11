"""Compatibility import for build-module discovery."""

import sys

from systemlens.discovery.build import modules as _implementation

sys.modules[__name__] = _implementation
