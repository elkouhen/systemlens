"""Joern as a call-graph engine for Java (ADR-85).

The registry reads ``declaration``; ``engine`` is imported only when the
engine is built. Submodules own one concern each:

- ``declaration``: name, capabilities and options published to the registry.
- ``extraction``: decoding of the exported JSON into engine-neutral facts.
- ``engine``: CPG creation, caching, the export script run and the adapter.
- ``export.sc``: the Joern script that exports methods and calls as JSON.
"""
