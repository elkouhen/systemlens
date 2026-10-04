"""Persisted, source-evidenced potential execution flows."""

import hashlib
from dataclasses import dataclass, replace


def compute_code_flow_id(
    module: str,
    path: str,
    method: str,
    trigger_kind: str,
    trigger_name: str,
) -> str:
    """Build an identity that survives source-line movement."""
    coordinate = f"{module}|{path}|{method}|{trigger_kind}|{trigger_name}"
    digest = hashlib.sha256(coordinate.encode()).hexdigest()
    return digest[:16]


@dataclass(frozen=True)
class CodeFlowStep:
    order: int
    kind: str
    name: str
    path: str
    start_line: int
    end_line: int
    endpoint_id: str | None = None
    operation: str | None = None


@dataclass(frozen=True)
class CodeFlow:
    id: str
    module: str
    method: str
    path: str
    start_line: int
    end_line: int
    status: str
    confidence: str
    reason: str
    steps: tuple[CodeFlowStep, ...]
    reconciliation: str = "unknown"
    alternative_count: int = 1


def ensure_unique_code_flow_ids(flows: list[CodeFlow]) -> list[CodeFlow]:
    """Disambiguate flow IDs while preserving unique historical IDs."""
    used_ids: set[str] = set()
    unique: list[CodeFlow] = []
    for flow in flows:
        if flow.id not in used_ids:
            used_ids.add(flow.id)
            unique.append(flow)
            continue
        discriminator = repr((
            flow.module,
            flow.path,
            flow.method,
            flow.status,
            tuple(
                (step.kind, step.name, step.path, step.endpoint_id, step.operation)
                for step in flow.steps
            ),
        )).encode("utf-8")
        digest = hashlib.sha256(discriminator).hexdigest()[:8]
        candidate = f"{flow.id}-{digest}"
        collision = 2
        while candidate in used_ids:
            candidate = f"{flow.id}-{digest}-{collision}"
            collision += 1
        used_ids.add(candidate)
        unique.append(replace(flow, id=candidate))
    return unique


@dataclass(frozen=True)
class IntegrationMethod:
    """A Java method that receives or emits an indexed integration event.

    Endpoint facts remain the source of truth for the integration itself. This
    projection makes their method-level role explicit for interprocedural
    analysis without inferring a class-level relationship.
    """

    id: str
    module: str
    qualified_method: str
    path: str
    start_line: int
    end_line: int
    input_endpoint_ids: tuple[str, ...]
    output_endpoint_ids: tuple[str, ...]


@dataclass(frozen=True)
class CodeQLCallGraphEdge:
    """One source-backed directed edge in the persisted Java call graph."""

    caller_id: str
    callee_id: str
    path: str
    line: int
    dispatch_confidence: str
    inferred: bool = False


@dataclass(frozen=True)
class PersistedCodeQLMethod:
    """One source-backed method in the persisted CodeQL projection."""

    id: str
    module: str
    qualified_method: str
    path: str
    start_line: int
    end_line: int
