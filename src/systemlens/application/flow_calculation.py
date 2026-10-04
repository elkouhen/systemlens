"""Reconstruct persisted code flows from independent stored evidence."""

from systemlens.conventions.strategy1.graph import STRATEGY1_REST_TARGET_POLICY
from systemlens.domain.graph import (
    DEFAULT_REST_TARGET_POLICY,
    build_graph,
    graph_edges_from_facts,
    group_endpoints_by_module,
)
from systemlens.indexing.code_flows import (
    _deduplicate_code_flows,
    _ensure_unique_code_flow_ids,
    materialize_codeql_code_flows,
    reconcile_code_flows,
)
from systemlens.domain.module_inventory import module_identity
from systemlens.scanner import local_spring_application_names
from systemlens.storage.sqlite import Store


def calculate_persisted_flows(store: Store, module: str | None = None) -> int:
    """Reconstruct all persisted call-graph flows with source and AI topology.

    This function deliberately does not index source files or invoke CodeQL.
    It reads the endpoint, integration-method, CodeQL-edge, module and
    enrichment snapshots already stored in SQLite, rebuilds the derived flow
    set in memory, and persists only that derived result. When ``module`` is
    provided, only input methods owned by that module are reconstructed; the
    global persisted method graph remains available for helper calls.
    """
    endpoints = store.all_endpoints()
    modules = store.all_modules()
    persisted_flows = store.all_code_flows()
    methods = store.all_integration_methods()
    persisted_call_edges = store.all_codeql_call_edges()
    persisted_codeql_methods = store.all_codeql_methods()
    facts = store.all_graph_facts()
    endpoints_by_service = group_endpoints_by_module(endpoints)
    strategy = store.get_meta("topic_strategy") or "default"
    service_aliases = {
        module_identity(module): local_spring_application_names(module.path, None)
        for module in modules
    }
    source_edges = build_graph(
        endpoints_by_service,
        rest_policy=(
            STRATEGY1_REST_TARGET_POLICY
            if strategy == "strategy1"
            else DEFAULT_REST_TARGET_POLICY
        ),
        service_aliases=service_aliases,
    )
    fact_edges = graph_edges_from_facts(facts, endpoints_by_service)
    reconstructed_flows = (
        materialize_codeql_code_flows(
            methods,
            endpoints,
            [],
            module=module,
            persisted_edges=persisted_call_edges,
            persisted_methods=persisted_codeql_methods,
        )
        if methods
        else []
    )
    flows = _ensure_unique_code_flow_ids(_deduplicate_code_flows(
        [*persisted_flows, *reconstructed_flows], endpoints,
    ))
    reconciled = reconcile_code_flows(
        flows,
        endpoints,
        [*source_edges, *fact_edges],
    )
    store.replace_code_flows(_ensure_unique_code_flow_ids(reconciled))
    return len(reconciled)
