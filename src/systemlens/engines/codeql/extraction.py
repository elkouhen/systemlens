"""Decode CodeQL query results into engine-neutral call-graph facts."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

from systemlens.callgraph.model import (
    MethodCall,
    MethodReachability,
    PayloadTypeEvidence,
    SourceMethod,
)
from systemlens.domain.code_flows import IntegrationMethod
import systemlens.engines.codeql.database as database
from systemlens.engines.codeql.errors import CodeQLError
from systemlens.engines.codeql.queries import (
    _KAFKA_MESSAGE_TYPES_QUERY,
    _calls_query,
    _methods_query,
    _reachability_query,
)


def extract_codeql_calls(
    database_path: Path,
    executable: str | None = None,
    timeout_seconds: int = 600,
    path_prefix: str = "",
    caller_prefix: str = "",
    threads: int = 1,
    ram_mb: int | None = None,
    verbosity: str | None = None,
    progress: Callable[[str], None] | None = None,
    deadline: float | None = None,
) -> list[MethodCall]:
    """Return statically resolved Java method calls from one CodeQL database.

    A database created for a build module reports paths relative to that
    module. ``path_prefix`` maps those paths back to the indexed repository
    root so that calls from several module databases can be joined together.
    ``caller_prefix`` restricts a global database query to callers below one
    repository-relative project prefix, while retaining callees from other
    projects for cross-project joins. A deadline reached during the query
    returns no call: CodeQL writes its result only when the query completes.
    """
    rows = database.run_query(
        database_path, "calls", _calls_query(caller_prefix),
        executable=executable, timeout_seconds=timeout_seconds, threads=threads,
        ram_mb=ram_mb, verbosity=verbosity, progress=progress, deadline=deadline,
        label="call graph",
    )
    return _parse_codeql_calls(rows, path_prefix)


def extract_codeql_methods(
    database_path: Path,
    executable: str | None = None,
    timeout_seconds: int = 600,
    path_prefix: str = "",
    source_prefix: str = "",
    threads: int = 1,
    ram_mb: int | None = None,
    verbosity: str | None = None,
    progress: Callable[[str], None] | None = None,
    deadline: float | None = None,
) -> list[SourceMethod]:
    """Return source-backed Java methods queried directly from CodeQL."""
    rows = database.run_query(
        database_path, "methods", _methods_query(source_prefix),
        executable=executable, timeout_seconds=timeout_seconds, threads=threads,
        ram_mb=ram_mb, verbosity=verbosity, progress=progress, deadline=deadline,
        label="method query",
    )
    normalized_prefix = path_prefix.strip("/")
    methods: list[SourceMethod] = []
    for row in rows:
        try:
            path = row["path"]
            if normalized_prefix and path:
                path = f"{normalized_prefix}/{path.lstrip('/')}"
            methods.append(SourceMethod(
                qualified_method=row["qualified_method"],
                path=path,
                start_line=int(row["start_line"]),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise CodeQLError("CodeQL returned an unexpected method schema.") from exc
    return methods


def _parse_codeql_calls(rows: Iterable[Mapping[str, str]], path_prefix: str) -> list[MethodCall]:
    normalized_prefix = path_prefix.strip("/")

    def repository_path(path: str) -> str:
        if not normalized_prefix or not path:
            return path
        return f"{normalized_prefix}/{path.lstrip('/')}"

    calls: list[MethodCall] = []
    for row in rows:
        try:
            calls.append(MethodCall(
                caller=row["caller"], caller_path=repository_path(row["caller_path"]),
                caller_line=int(row["caller_line"]), callee=row["callee"],
                callee_path=repository_path(row["callee_path"]), callee_line=int(row["callee_line"]),
                call_line=int(row["call_line"]),
                # A result without the confidence column cannot claim exact
                # dispatch.
                dispatch_confidence=row.get("dispatch_confidence", "possible"),
                caller_parameter_types=_parameter_types(row.get("caller_parameter_types")),
                callee_parameter_types=_parameter_types(row.get("callee_parameter_types")),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise CodeQLError("CodeQL returned an unexpected call-graph CSV schema.") from exc
    return calls


def _parameter_types(value: str | None) -> tuple[str, ...] | None:
    """Decode the ``;``-separated parameter types; ``None`` when absent."""
    if value is None:
        return None
    return tuple(value.split(";")) if value else ()


def extract_codeql_kafka_message_types(
    database_path: Path,
    *,
    executable: str | None = None,
    timeout_seconds: int = 600,
    threads: int = 1,
    ram_mb: int | None = None,
    deadline: float | None = None,
) -> list[PayloadTypeEvidence]:
    """Return unique payload types for Strategy1 Kafka producer calls."""
    rows = database.run_query(
        database_path, "kafka_message_types", _KAFKA_MESSAGE_TYPES_QUERY,
        executable=executable, timeout_seconds=timeout_seconds, threads=threads,
        ram_mb=ram_mb, deadline=deadline, label="Kafka message-type query",
    )
    result: list[PayloadTypeEvidence] = []
    for row in rows:
        try:
            result.append(PayloadTypeEvidence(
                path=row["path"], line=int(row["line"]), message_type=row["message_type"],
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise CodeQLError("CodeQL returned an unexpected Kafka message-type CSV schema.") from exc
    return result


def extract_codeql_reachability(
    database_path: Path,
    methods: Sequence[IntegrationMethod],
    *,
    executable: str | None = None,
    timeout_seconds: int = 600,
    threads: int = 1,
    ram_mb: int | None = None,
    deadline: float | None = None,
) -> list[MethodReachability]:
    """Return unbounded CodeQL reachability between indexed input/output methods."""
    if not database_path.is_dir():
        raise CodeQLError(f"CodeQL database not found: {database_path}")
    if not any(method.input_endpoint_ids for method in methods):
        return []
    if not any(method.output_endpoint_ids for method in methods):
        return []
    rows = database.run_query(
        database_path, "reachability", _reachability_query(methods),
        executable=executable, timeout_seconds=timeout_seconds, threads=threads,
        ram_mb=ram_mb, deadline=deadline, label="reachability query",
    )
    reachability: list[MethodReachability] = []
    for row in rows:
        try:
            reachability.append(MethodReachability(
                source=row["source"], source_path=row["source_path"],
                source_line=int(row["source_line"]), target=row["target"],
                target_path=row["target_path"], target_line=int(row["target_line"]),
                confidence=row["confidence"],
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise CodeQLError("CodeQL returned an unexpected reachability schema.") from exc
    return reachability


