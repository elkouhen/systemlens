"""Compatibility facade for module facts now owned by :mod:`systemlens.domain`."""

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
]
