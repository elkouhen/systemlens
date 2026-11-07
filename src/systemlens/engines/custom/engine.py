"""The tree-sitter source engine: no external analyzer, no build.

It reports no call by itself. Flow construction always runs the shared
source symbol pass (``JavaSymbols``), which resolves calls from declared
receiver types, imports, inheritance, and unique interface implementations.
With this engine that pass is the whole call graph.

Its share of Strategy1 is empty: the scanner's AST rule (ADR-76) already
types every Kafka payload the sources declare, and this engine has no data
flow to add evidence beyond it.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from systemlens.callgraph.engine import EngineRequest, EngineSettings
from systemlens.callgraph.model import (
    MethodCall,
    MethodReachability,
    PayloadTypeEvidence,
    SourceMethod,
)
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.engines.custom.declaration import DECLARATION


class _SourceSession:
    def calls(self) -> list[MethodCall]:
        return []

    def methods(self, source_prefix: str = "") -> list[SourceMethod]:
        return []

    def reachability(self, methods: Sequence[IntegrationMethod]) -> list[MethodReachability]:
        return []

    def payload_types(self) -> list[PayloadTypeEvidence]:
        return []


class SourceEngine:
    name = DECLARATION.name
    label = DECLARATION.label
    supports_module_refresh = DECLARATION.supports_module_refresh
    resolves_types = DECLARATION.resolves_types

    def available(self) -> bool:
        return True

    def input_fingerprint(self) -> str:
        return ""

    @contextmanager
    def open(self, request: EngineRequest) -> Iterator[_SourceSession]:
        yield _SourceSession()


def create_engine(settings: EngineSettings) -> SourceEngine:
    """Registry entry point; the source engine has no setting."""
    return SourceEngine()
