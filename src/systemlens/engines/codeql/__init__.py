"""Local CodeQL call-graph engine for Java integration flows.

The registry reads ``declaration`` and loads ``engine`` lazily; no core
module imports this package's adapter. Submodules own one concern each:

- ``declaration``: name, capabilities and options published to the registry.
- ``queries``: QL text and query builders.
- ``database``: executable lookup, database lifecycle, query execution.
- ``extraction``: decoding of query results into engine-neutral facts.
- ``engine``: the ``CallGraphEngine`` implementation and registry entry.
- ``errors``: engine errors mapped onto the engine seam.

The public names below resolve on first access, so importing the package
for its declaration does not load the adapter.
"""

import importlib
from typing import Any

_EXPORTS = {
    "CODEQL_JAVA_PACK_VERSION": "queries",
    "CodeQLEngine": "engine",
    "CodeQLError": "errors",
    "CodeQLTimeout": "errors",
    "MethodCall": None,
    "MethodReachability": None,
    "PayloadTypeEvidence": None,
    "SourceMethod": None,
    "_QUERY": "queries",
    "_calls_query": "queries",
    "_methods_query": "queries",
    "_prepare_source_only_root": "database",
    "_reachability_query": "queries",
    "_run_with_progress": "database",
    "automatic_codeql_database": "database",
    "codeql_executable": "database",
    "create_engine": "engine",
    "extract_codeql_calls": "extraction",
    "extract_codeql_kafka_message_types": "extraction",
    "extract_codeql_methods": "extraction",
    "extract_codeql_reachability": "extraction",
}
_SUBMODULES = ("database", "declaration", "engine", "errors", "extraction", "queries")

__all__ = sorted([*_EXPORTS, *_SUBMODULES])


def __getattr__(name: str) -> Any:
    if name in _SUBMODULES:
        return importlib.import_module(f"{__name__}.{name}")
    if name in _EXPORTS:
        owner = _EXPORTS[name]
        module = importlib.import_module(
            "systemlens.callgraph.model" if owner is None else f"{__name__}.{owner}"
        )
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
