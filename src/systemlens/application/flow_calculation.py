"""Reconstruct persisted code flows from independent stored evidence."""

from systemlens.conventions.strategy1.graph import STRATEGY1_REST_TARGET_POLICY
from systemlens.domain.graph import (
    DEFAULT_REST_TARGET_POLICY,
    build_graph,
    graph_edges_from_facts,
    group_endpoints_by_module,
)
from systemlens.indexing.code_flows import reconcile_code_flows
from systemlens.domain.module_inventory import module_identity
from systemlens.scanner import local_spring_application_names
from systemlens.storage.sqlite import Store


def calculate_persisted_flows(store: Store) -> int:
    """Reconcile stored flow candidates with source and AI topology evidence.

    This function deliberately does not index source files or invoke CodeQL.
    It reads the endpoint, flow, module and enrichment snapshots already stored
    in SQLite and persists only the derived flow reconciliation result.
    """
    endpoints = store.all_endpoints()
    modules = store.all_modules()
    flows = store.all_code_flows()
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
    reconciled = reconcile_code_flows(
        flows,
        endpoints,
        [*source_edges, *fact_edges],
    )
    store.replace_code_flows(reconciled)
    return len(reconciled)
