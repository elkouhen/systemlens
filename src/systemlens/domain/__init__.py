"""Stable domain facts shared across SystemLens layers."""

from systemlens.domain.module_inventory import (
    BlockingPoint,
    DiscoveredModule,
    JavaArchitectureExtension,
    KafkaMethod,
    ModuleDependency,
    MongoField,
    MongoMethod,
    MongoPersistenceClass,
    SourceEvidence,
    module_identity,
)
from systemlens.domain.runtime import KubernetesWorkload

__all__ = [
    "BlockingPoint",
    "DiscoveredModule",
    "JavaArchitectureExtension",
    "KafkaMethod",
    "ModuleDependency",
    "MongoField",
    "MongoMethod",
    "MongoPersistenceClass",
    "SourceEvidence",
    "module_identity",
    "KubernetesWorkload",
]
