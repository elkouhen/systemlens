"""Stable domain facts shared across SystemLens layers."""

from systemlens.domain.code_flows import CodeFlow, CodeFlowStep
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
    "CodeFlow",
    "CodeFlowStep",
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
