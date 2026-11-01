"""Local CodeQL call-graph engine for Java integration flows.

The package is loaded lazily by the engine registry; no core module imports
it. Submodules own one concern each:

- ``queries``: QL text and query builders.
- ``database``: executable lookup, database lifecycle, query execution.
- ``extraction``: decoding of query results into engine-neutral facts.
- ``engine``: the ``CallGraphEngine`` implementation and registry entry.
- ``errors``: engine errors mapped onto the engine seam.
"""

from systemlens.callgraph.model import (
    MethodCall,
    MethodReachability,
    PayloadTypeEvidence,
    SourceMethod,
)
from systemlens.engines.codeql import database, engine, errors, extraction, queries
from systemlens.engines.codeql.database import (
    _prepare_source_only_root,
    _run_with_progress,
    automatic_codeql_database,
    codeql_executable,
)
from systemlens.engines.codeql.engine import CodeQLEngine, create_engine
from systemlens.engines.codeql.errors import CodeQLError, CodeQLTimeout
from systemlens.engines.codeql.extraction import (
    extract_codeql_calls,
    extract_codeql_kafka_message_types,
    extract_codeql_methods,
    extract_codeql_reachability,
)
from systemlens.engines.codeql.queries import (
    _QUERY,
    CODEQL_JAVA_PACK_VERSION,
    _calls_query,
    _methods_query,
    _reachability_query,
)

__all__ = [
    "CODEQL_JAVA_PACK_VERSION",
    "CodeQLEngine",
    "CodeQLError",
    "CodeQLTimeout",
    "MethodCall",
    "MethodReachability",
    "PayloadTypeEvidence",
    "SourceMethod",
    "_QUERY",
    "_calls_query",
    "_methods_query",
    "_prepare_source_only_root",
    "_reachability_query",
    "_run_with_progress",
    "automatic_codeql_database",
    "codeql_executable",
    "create_engine",
    "database",
    "engine",
    "errors",
    "extract_codeql_calls",
    "extract_codeql_kafka_message_types",
    "extract_codeql_methods",
    "extract_codeql_reachability",
    "extraction",
    "queries",
]
