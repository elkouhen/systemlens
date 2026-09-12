"""Persisted, source-evidenced potential execution flows."""

import hashlib
from dataclasses import dataclass


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
