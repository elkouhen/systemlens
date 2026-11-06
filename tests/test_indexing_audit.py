from systemlens.application.architecture_inventory import AnalysisProfile, ArchitectureInventory
from systemlens.application.indexing_audit import audit_indexing
from systemlens.domain.code_flows import (
    CallGraphEdge,
    IntegrationMethod,
    PersistedCallGraphMethod,
)


def test_an_edge_to_a_persisted_engine_method_is_not_an_orphan() -> None:
    """Review D2: the audit checks edges against every persisted graph node.

    A CodeQL index persists helper methods that are not integration methods;
    ignoring them flagged 4754 edges as orphans on train-ticket.
    """
    entry = IntegrationMethod(
        "entry", "orders", "p.OrderController.create", "src/App.java", 1, 10, (), (),
    )
    helper = PersistedCallGraphMethod(
        "codeql:helper", "orders", "p.Order.getId", "src/Order.java", 3, 3,
    )
    inventory = ArchitectureInventory(
        endpoints_by_service={}, endpoints_by_module={}, findings_by_service={}, endpoints=[],
        findings=[], modules=[], modules_by_service={}, module_dependencies=[], relations=[],
        diagnostics=[], warnings=[], source_roots=[], profile=AnalysisProfile(),
        integration_methods=[entry],
        call_graph_methods=[helper],
        call_graph_edges=[
            CallGraphEdge("entry", "codeql:helper", "src/App.java", 5, "exact"),
            CallGraphEdge("entry", "codeql:gone", "src/App.java", 6, "exact"),
        ],
    )

    issues = audit_indexing(inventory)["issues"]

    assert isinstance(issues, list)
    orphans = [item["message"] for item in issues if item["rule"] == "orphan_call_graph_edge"]
    assert orphans == ["L'arête du graphe d'appel entry → codeql:gone référence une méthode absente."]
