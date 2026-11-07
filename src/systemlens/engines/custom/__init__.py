"""The in-house call-graph engine: tree-sitter, no external analyzer (ADR-85).

The registry reads ``declaration``; ``engine`` is imported only when the
engine is built. Its name stays ``source``, the configuration value.

- ``declaration``: name, capabilities and description published to the
  registry.
- ``engine``: the adapter, whose session reports no call by itself.

The symbol pass that resolves calls from source (``indexing/java_symbols.py``)
and the join (``indexing/code_flows.py``) stay in the core: they run for
every engine, and this engine only leaves them the whole call graph.
"""
