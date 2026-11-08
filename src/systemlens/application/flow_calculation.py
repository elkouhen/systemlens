"""Reconstruct persisted code flows from independent stored evidence."""

from dataclasses import dataclass

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
    combine_code_flows,
    materialize_call_graph_flows,
    materialize_code_flows,
    reconcile_code_flows,
)
from systemlens.domain.code_flows import TRANSIENT_ENTRY_KINDS, IntegrationMethod
from systemlens.domain.data_operation_projection import data_operations_from_modules
from systemlens.domain.data_operations import DataOperation
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule, module_identity
from systemlens.indexing.integration_methods import (
    TRANSIENT_TRIGGER_PREFIXES,
    transient_trigger_endpoints,
)
from systemlens.infrastructure.config import Config, config_path, load_config
from systemlens.scanner import local_spring_application_names
from systemlens.storage.sqlite import Store


@dataclass(frozen=True)
class PersistedFlowInputs:
    """The stored facts every flow reconstruction starts from.

    Indexing, ``flows calculate`` and ``analyze compare-engines`` must join
    the same methods, endpoints, modules and data operations, or their flow
    counts diverge (review D1): one loader keeps the two commands aligned.
    """

    endpoints: list[MessageEndpoint]
    flow_endpoints: list[MessageEndpoint]
    methods: list[IntegrationMethod]
    modules: list[DiscoveredModule]
    operations: list[DataOperation]


def load_persisted_flow_inputs(store: Store) -> PersistedFlowInputs:
    """Read the flow-construction inputs of a persisted index.

    ``flow_endpoints`` adds the transient scheduler triggers, recovered by
    re-reading only the Java files of persisted methods that reference one
    (schedules, startup code and event listeners, PDR-16).
    """
    endpoints = store.all_endpoints()
    modules = store.all_modules()
    # Persisted operation facts drive data steps; an index created before the
    # table existed is projected from its module inventory until reindexed.
    operations = store.all_data_operations() or data_operations_from_modules(
        store.repo_root, modules,
    )
    methods = store.all_integration_methods()
    trigger_paths = sorted({
        method.path for method in methods
        if any(item.startswith(TRANSIENT_TRIGGER_PREFIXES) for item in method.input_endpoint_ids)
    })
    flow_endpoints = [
        *endpoints,
        *transient_trigger_endpoints(store.repo_root, trigger_paths, endpoints, modules),
    ] if trigger_paths else endpoints
    return PersistedFlowInputs(
        endpoints=endpoints, flow_endpoints=flow_endpoints, methods=methods,
        modules=modules, operations=operations,
    )


def calculate_persisted_flows(
    store: Store, module: str | None = None, config: Config | None = None,
) -> int:
    """Rebuild the persisted flow set as indexing builds it.

    This function does not extract endpoints or run a call-graph engine. It
    reads the endpoint, integration-method, call-edge, module, data-operation
    and enrichment snapshots stored in SQLite and rebuilds the flows with the
    same passes and merge as indexing: the same-method pass, the join over the
    persisted call graph with the configured hop bound and edge confidence,
    key tracing and the ``after_commit`` phase. Those passes read the Java
    sources under the repository root, as indexing does (review F12).

    When ``module`` is provided, only flows owned by that module are rebuilt,
    same-method flows included; current flows of other modules are kept. The
    global persisted method graph remains available for helper calls.
    """
    if config is None:
        # An index built before ``init`` wrote a configuration used the
        # defaults; so does its rebuild.
        config = (
            load_config(store.repo_root)
            if config_path(store.repo_root).is_file() else Config()
        )
    repo_root = store.repo_root
    inputs = load_persisted_flow_inputs(store)
    endpoints, modules, methods = inputs.endpoints, inputs.modules, inputs.methods
    current_endpoint_ids = {endpoint.id for endpoint in endpoints}
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
    same_method_flows = [
        flow for flow in materialize_code_flows(
            repo_root, endpoints, modules, inputs.operations,
        )
        if module is None or flow.module == module
    ]
    call_graph_flows = (
        materialize_call_graph_flows(
            methods,
            inputs.flow_endpoints,
            [],
            repo_root=repo_root,
            module=module,
            modules=modules,
            persisted_edges=store.all_call_edges(),
            persisted_methods=store.all_call_graph_methods(),
            operations=inputs.operations,
            source_paths=sorted(store.get_file_hashes()),
            max_hops=config.engine_max_hops,
            edge_confidence=config.engine_edge_confidence,
            module_dependencies=store.all_module_dependencies(),
        )
        if methods
        else []
    )
    # A module rebuild keeps the current flows of the other modules. A
    # scheduled method whose only indexed effect is a data access has no
    # endpoint step: its transient trigger anchors it, so it stays eligible.
    retained_persisted_flows = [
        flow
        for flow in store.all_code_flows()
        if module is not None
        and flow.module != module
        and all(
            step.endpoint_id in current_endpoint_ids
            for step in flow.steps
            if step.endpoint_id is not None
        )
        and (
            any(step.endpoint_id is not None for step in flow.steps)
            or (bool(flow.steps) and flow.steps[0].kind in TRANSIENT_ENTRY_KINDS)
        )
    ]
    flows = _ensure_unique_code_flow_ids(_deduplicate_code_flows(
        [
            *retained_persisted_flows,
            *combine_code_flows(same_method_flows, call_graph_flows, endpoints),
        ],
        endpoints,
    ))
    reconciled = reconcile_code_flows(
        flows,
        endpoints,
        [*source_edges, *fact_edges],
    )
    store.replace_code_flows(_ensure_unique_code_flow_ids(reconciled))
    return len(reconciled)
