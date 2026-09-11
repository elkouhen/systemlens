from pathlib import Path

from systemlens.architecture_inventory import AnalysisProfile, ArchitectureInventory
from systemlens.application.architecture_projection import project_architecture_graph
from systemlens.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule


def _module(name: str, *, starts_application: bool, collections: tuple[str, ...] = ()) -> DiscoveredModule:
    return DiscoveredModule(
        name=name,
        path=Path("/workspace") / name,
        build_system="maven",
        version=None,
        kind="library",
        starts_application=starts_application,
        configuration_example="",
        mongo_collections=collections,
        identity=name,
    )


def _endpoint(module: str) -> MessageEndpoint:
    return MessageEndpoint(
        id=module,
        role="consume",
        system="kafka",
        topic="orders",
        topic_dynamic=False,
        source="code",
        framework="spring-kafka",
        path=f"{module}/Listener.java",
        start_line=1,
        end_line=1,
        snippet="",
        module=module,
    )


def test_graph_projection_keeps_only_deployable_exportable_services() -> None:
    service = _module("orders", starts_application=True, collections=("orders",))
    library = _module("orders-domain", starts_application=False, collections=("internal",))
    endpoints = {
        "orders": [_endpoint("orders")],
        "orders-domain": [_endpoint("orders-domain")],
        "test-fixture": [_endpoint("test-fixture")],
    }
    inventory = ArchitectureInventory(
        endpoints_by_service=endpoints,
        endpoints_by_module=endpoints,
        findings_by_service={},
        endpoints=[endpoint for values in endpoints.values() for endpoint in values],
        findings=[],
        modules=[service, library],
        modules_by_service={"orders": service, "orders-domain": library},
        module_dependencies=[],
        relations=[],
        diagnostics=[],
        warnings=[],
        source_roots=[],
        profile=AnalysisProfile(),
    )

    projection = project_architecture_graph(inventory, include_module_details=True)

    assert list(projection.services_by_name) == ["orders"]
    assert projection.modules_by_service == {"orders": service}
    assert projection.collections_by_service == {"orders": ["orders"]}


def test_graph_projection_can_hide_module_details_without_hiding_services() -> None:
    service = _module("orders", starts_application=True, collections=("orders",))
    endpoints = {"orders": [_endpoint("orders")]}
    inventory = ArchitectureInventory(
        endpoints_by_service=endpoints,
        endpoints_by_module=endpoints,
        findings_by_service={},
        endpoints=endpoints["orders"],
        findings=[],
        modules=[service],
        modules_by_service={"orders": service},
        module_dependencies=[],
        relations=[],
        diagnostics=[],
        warnings=[],
        source_roots=[],
        profile=AnalysisProfile(),
    )

    projection = project_architecture_graph(inventory, include_module_details=False)

    assert list(projection.services_by_name) == ["orders"]
    assert projection.modules_by_service == {}
    assert projection.collections_by_service == {}
