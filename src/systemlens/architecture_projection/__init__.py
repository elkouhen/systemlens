"""Compatibility facade for the shared architecture application projection."""

from systemlens.application.architecture_projection import (
    ArchitectureGraphProjection,
    is_exportable_microservice,
    project_architecture_graph,
)

__all__ = [
    "ArchitectureGraphProjection",
    "is_exportable_microservice",
    "project_architecture_graph",
]
