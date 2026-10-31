"""The seam between SystemLens' flow construction and a call-graph engine.

The indexing core asks an engine for source-located calls and, optionally,
for direct reachability answers and Kafka payload types. It never imports an
engine implementation: engines are loaded by name through the registry.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from systemlens.callgraph.model import (
    MethodCall,
    MethodReachability,
    PayloadTypeEvidence,
    SourceMethod,
)
from systemlens.domain.code_flows import IntegrationMethod


@dataclass(frozen=True)
class EngineRequest:
    """What one index run asks of an engine."""

    repo_root: Path
    allowed_paths: Sequence[str]
    timeout_seconds: int
    deadline: float | None = None
    # Generated build sources are already present in the repository.
    include_generated_sources: bool = False
    progress: Callable[[str], None] | None = None
    verbose: bool = False


class CallGraphSession(Protocol):
    """One prepared analysis of a repository, valid inside ``open``."""

    def calls(self) -> list[MethodCall]: ...

    def methods(self, source_prefix: str = "") -> list[SourceMethod]: ...

    def reachability(self, methods: Sequence[IntegrationMethod]) -> list[MethodReachability]: ...

    def payload_types(self) -> list[PayloadTypeEvidence]: ...


class CallGraphEngine(Protocol):
    """A pluggable producer of source-located Java method calls.

    ``supports_module_refresh`` engines can recompute one module against a
    persisted global graph. ``resolves_types`` engines answer from a typed
    program model; others leave resolution to the shared source symbol pass.
    """

    name: str
    label: str
    supports_module_refresh: bool
    resolves_types: bool

    def available(self) -> bool: ...

    def open(self, request: EngineRequest) -> AbstractContextManager[CallGraphSession]: ...
