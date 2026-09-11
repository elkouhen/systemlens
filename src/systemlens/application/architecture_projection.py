"""Application projection shared by CLI and web architecture exports."""

from dataclasses import dataclass

from systemlens.architecture_inventory import ArchitectureInventory, is_deployable_service
from systemlens.graph import GraphEdge, graph_edges_from_relations
from systemlens.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule


@dataclass(frozen=True)
class ArchitectureGraphProjection:
    services_by_name: dict[str, list[MessageEndpoint]]
    edges: list[GraphEdge]
    collections_by_service: dict[str, list[str]]
    modules_by_service: dict[str, DiscoveredModule]


def is_exportable_microservice(name: str) -> bool:
    """Exclude test fixtures and unresolved build-property service names."""
    normalized = name.casefold()
    return "test" not in normalized and not (
        name.startswith("${") and name.endswith("}")
    )


def project_architecture_graph(
    inventory: ArchitectureInventory,
    *,
    include_module_details: bool,
) -> ArchitectureGraphProjection:
    """Select the consistent deployable topology consumed by export adapters."""
    services = {
        name: endpoints
        for name, endpoints in inventory.endpoints_by_service.items()
        if is_exportable_microservice(name)
        and is_deployable_service(name, inventory.modules_by_service)
    }
    edges = [
        edge
        for edge in graph_edges_from_relations(inventory.relations, services)
        if is_exportable_microservice(edge.from_service)
        and is_exportable_microservice(edge.to_service)
    ]
    modules = (
        {
            name: module
            for name, module in inventory.modules_by_service.items()
            if name in services
        }
        if include_module_details
        else {}
    )
    collections = {
        service: list(module.mongo_collections)
        for service, module in modules.items()
        if module.mongo_collections
    }
    return ArchitectureGraphProjection(services, edges, collections, modules)
