"""Domain types shared by build discovery, persistence, and projections.

This module deliberately has no dependency on Java parsing or build-system
discovery.  Consumers that only exchange module inventory facts should import
from here instead of depending on :mod:`systemlens.modules`.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from systemlens.domain.runtime import KubernetesWorkload


@dataclass(frozen=True)
class DiscoveredModule:
    name: str
    path: Path
    build_system: str  # maven | gradle
    version: str | None
    kind: str  # library | aggregator
    starts_application: bool
    configuration_example: str
    application_entrypoint: "SourceEvidence | None" = None
    mongo_collections: tuple[str, ...] = ()
    mongo_methods: tuple["MongoMethod", ...] = ()
    mongo_persistence_classes: tuple["MongoPersistenceClass", ...] = ()
    openapi_files: tuple[str, ...] = ()
    kafka_methods: tuple["KafkaMethod", ...] = ()
    blocking_points: tuple["BlockingPoint", ...] = ()
    rest_controllers: tuple[str, ...] = ()
    openapi_generated_clients: tuple[str, ...] = ()
    kubernetes_workloads: tuple[KubernetesWorkload, ...] = ()
    # Stable key carried by endpoints and relations. It equals ``name`` unless
    # another build module in the same index uses the same artifact name.
    identity: str = ""


def module_identity(module: DiscoveredModule) -> str:
    """Return the collision-safe key while keeping ``name`` as display alias."""
    return module.identity or module.name


@dataclass(frozen=True, order=True)
class ModuleDependency:
    """A build dependency between two modules in the indexed workspace."""

    source: str
    target: str


@dataclass(frozen=True)
class MongoMethod:
    operation: str
    receiver: str
    path: str
    line: int
    collection: str | None = None
    evidence: "SourceEvidence | None" = None
    owner_method: str | None = None


@dataclass(frozen=True, order=True)
class MongoField:
    name: str
    type: str
    references: tuple[str, ...] = ()


@dataclass(frozen=True, order=True)
class MongoPersistenceClass:
    collection: str
    name: str
    qualified_name: str
    path: str
    line: int
    fields: tuple[MongoField, ...] = ()
    root: bool = True


@dataclass(frozen=True)
class KafkaMethod:
    role: str  # send | receive
    mechanism: str
    method: str
    path: str
    line: int
    topic: str | None = None
    evidence: "SourceEvidence | None" = None


@dataclass(frozen=True)
class BlockingPoint:
    mechanism: str
    method: str
    path: str
    line: int
    detail: str
    evidence: "SourceEvidence | None" = None


@dataclass(frozen=True)
class SourceEvidence:
    start_line: int
    end_line: int
    snippet: str
    source_hash: str


class JavaArchitectureExtension(Protocol):
    """Extension that contributes facts from production Java sources."""

    name: str

    def extract(self, files: list[tuple[str, bytes]]) -> tuple[KafkaMethod, ...]: ...
