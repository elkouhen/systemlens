"""Strategy1 repository convention pack.

This package is the only boundary through which the portable core invokes
Strategy1 rules. It intentionally contains no persistence or CLI policy:
those remain generic SystemLens responsibilities. Submodules are deliberately
not imported here: convention extraction depends on scanner primitives, while
the scanner package also exposes compatibility façades.
"""
