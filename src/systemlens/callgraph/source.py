"""The tree-sitter source engine: no external analyzer, no build.

It reports no call by itself. Flow construction always runs the shared
source symbol pass (``JavaSymbols``), which resolves calls from declared
receiver types, imports, inheritance, and unique interface implementations.
With this engine that pass is the whole call graph.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from systemlens.callgraph.engine import EngineRequest
from systemlens.callgraph.model import (
    MethodCall,
    MethodReachability,
    PayloadTypeEvidence,
    SourceMethod,
)
from systemlens.domain.code_flows import IntegrationMethod


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
    name = "source"
    label = "Source (tree-sitter)"
    supports_module_refresh = False
    resolves_types = False

    def available(self) -> bool:
        return True

    @contextmanager
    def open(self, request: EngineRequest) -> Iterator[_SourceSession]:
        yield _SourceSession()
