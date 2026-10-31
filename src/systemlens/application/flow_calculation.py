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
    materialize_call_graph_flows,
    reconcile_code_flows,
)
from systemlens.domain.data_operation_projection import data_operations_from_modules
from systemlens.domain.module_inventory import module_identity
from systemlens.indexing.integration_methods import (
    SCHEDULER_TRIGGER_PREFIX,
    scheduled_trigger_endpoints,
)
from systemlens.scanner import local_spring_application_names
from systemlens.storage.sqlite import Store


def calculate_persisted_flows(store: Store, module: str | None = None) -> int:
    """Reconstruct all persisted call-graph flows with source and AI topology.

    This function deliberately does not index source files or run a
    call-graph engine. It reads the endpoint, integration-method, call-edge,
    module and enrichment snapshots already stored in SQLite, rebuilds the
    derived flow
    set in memory, and persists only that derived result. When ``module`` is
    provided, only input methods owned by that module are reconstructed; the
    global persisted method graph remains available for helper calls.
    """
    endpoints = store.all_endpoints()
    modules = store.all_modules()
    # Persisted operation facts drive data steps; an index created before the
    # table existed is projected from its module inventory until reindexed.
    operations = store.all_data_operations() or data_operations_from_modules(
        store.repo_root, modules,
    )
    persisted_flows = store.all_code_flows()
    current_endpoint_ids = {endpoint.id for endpoint in endpoints}
    methods = store.all_integration_methods()
    # Scheduler triggers are transient: re-read only the Java files of the
    # persisted methods that reference one, to recover their schedules.
    scheduler_paths = sorted({
        method.path for method in methods
        if any(item.startswith(SCHEDULER_TRIGGER_PREFIX) for item in method.input_endpoint_ids)
    })
    flow_endpoints = [
        *endpoints,
        *scheduled_trigger_endpoints(store.repo_root, scheduler_paths, endpoints, modules),
    ] if scheduler_paths else endpoints
    persisted_call_edges = store.all_call_edges()
    persisted_codeql_methods = store.all_call_graph_methods()
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
        materialize_call_graph_flows(
            methods,
            flow_endpoints,
            [],
            module=module,
            modules=modules,
            persisted_edges=persisted_call_edges,
            persisted_methods=persisted_codeql_methods,
            operations=operations,
        )
        if methods
        else []
    )
    retained_persisted_flows = [
        flow
        for flow in persisted_flows
        if flow.module != module
        and all(
            step.endpoint_id is not None
            and step.endpoint_id in current_endpoint_ids
            for step in flow.steps
            if step.endpoint_id is not None
        )
        and any(step.endpoint_id is not None for step in flow.steps)
    ]
    flows = _ensure_unique_code_flow_ids(_deduplicate_code_flows(
        [*retained_persisted_flows, *reconstructed_flows], endpoints,
    ))
    reconciled = reconcile_code_flows(
        flows,
        endpoints,
        [*source_edges, *fact_edges],
    )
    store.replace_code_flows(_ensure_unique_code_flow_ids(reconciled))
    return len(reconciled)
