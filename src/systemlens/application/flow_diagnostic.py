"""Reconcile external integration facts with persisted internal code flows."""

from collections import Counter, defaultdict
from typing import Iterable

from systemlens.application.architecture_inventory import ArchitectureInventory
from systemlens.domain.code_flows import CodeFlow, IntegrationMethod
from systemlens.domain.graph import build_graph, group_endpoints_by_module
from systemlens.domain.models import MessageEndpoint


_ENTRY_ROLES = {("rest", "serve"), ("kafka", "consume")}
_EFFECT_ROLES = {("rest", "call"), ("kafka", "produce")}


def _endpoint_index(endpoints: Iterable[MessageEndpoint]) -> dict[str, MessageEndpoint]:
    return {endpoint.id: endpoint for endpoint in endpoints}


def _flow_modules(flow: CodeFlow, endpoints: dict[str, MessageEndpoint]) -> set[str]:
    return {
        endpoint.module
        for step in flow.steps
        if step.endpoint_id and (endpoint := endpoints.get(step.endpoint_id)) and endpoint.module
    }


def _entry_status(
    endpoint: MessageEndpoint,
    methods: list[IntegrationMethod],
    flows: list[CodeFlow],
    endpoint_by_id: dict[str, MessageEndpoint],
    relation_targets: dict[str, set[str]],
) -> dict[str, object]:
    entry_methods = [method for method in methods if endpoint.id in method.input_endpoint_ids]
    entry_flows = [flow for flow in flows if flow.steps and flow.steps[0].endpoint_id == endpoint.id]
    output_ids = sorted({
        output_id
        for method in entry_methods
        for output_id in method.output_endpoint_ids
    })
    local_flows = [flow for flow in entry_flows if len(_flow_modules(flow, endpoint_by_id)) <= 1]
    global_flows = [flow for flow in entry_flows if len(_flow_modules(flow, endpoint_by_id)) > 1]
    effect_ids = sorted({
        step.endpoint_id
        for flow in entry_flows
        for step in flow.steps[1:]
        if step.endpoint_id
    })
    targets = sorted({target for effect_id in effect_ids for target in relation_targets.get(effect_id, ())})

    if not entry_methods:
        status = "entry_without_method_fact"
    elif not output_ids and not entry_flows:
        status = "entry_method_without_external_effect"
    elif output_ids and not entry_flows:
        status = "local_flow_materialization_gap"
    elif entry_flows and not global_flows and targets:
        status = "global_composition_gap"
    elif entry_flows and not global_flows:
        status = "external_relation_unresolved"
    else:
        status = "flow_found"

    return {
        "entry": endpoint.topic,
        "endpoint_id": endpoint.id,
        "service": endpoint.module,
        "system": endpoint.system,
        "role": endpoint.role,
        "source": {"path": endpoint.path, "line": endpoint.start_line},
        "method_facts": len(entry_methods),
        "method_outputs": [
            {
                "endpoint_id": output_id,
                "name": endpoint_by_id[output_id].topic,
                "service": endpoint_by_id[output_id].module,
            }
            for output_id in output_ids
            if output_id in endpoint_by_id
        ],
        "local_flows": len(local_flows),
        "global_flows": len(global_flows),
        "external_targets": targets,
        "status": status,
    }


def diagnose_flows(inventory: ArchitectureInventory) -> dict[str, object]:
    """Explain where persisted external-to-global flows disappear.

    This is deliberately a read-only reconciliation of the persisted snapshot.
    It does not infer missing calls and does not re-parse source files.
    """
    endpoint_by_id = _endpoint_index(inventory.endpoints)
    methods = inventory.integration_methods
    flows = inventory.code_flows
    graph_edges = build_graph(
        group_endpoints_by_module(inventory.endpoints),
        strategy1=inventory.strategy1,
    )
    relation_targets: dict[str, set[str]] = defaultdict(set)
    for edge in graph_edges:
        if edge.to_endpoint is not None:
            relation_targets[edge.from_endpoint.id].add(edge.to_service)

    # Keep the diagnostic more informative than the asserted topology.  A
    # concrete Kafka topic with a downstream consumer is enough to explain a
    # possible composition gap, but not enough to create a topology edge: the
    # graph still requires compatible, known payload types.
    consumers_by_topic: dict[str, list[MessageEndpoint]] = defaultdict(list)
    for endpoint in inventory.endpoints:
        if (
            endpoint.system == "kafka"
            and endpoint.role == "consume"
            and not endpoint.topic_dynamic
        ):
            consumers_by_topic[endpoint.topic].append(endpoint)
    for endpoint in inventory.endpoints:
        if (
            endpoint.system != "kafka"
            or endpoint.role != "produce"
            or endpoint.topic_dynamic
            or endpoint.module is None
        ):
            continue
        for consumer in consumers_by_topic.get(endpoint.topic, ()):
            if consumer.module is None or consumer.module == endpoint.module:
                continue
            if (
                endpoint.message_type is not None
                and consumer.message_type is not None
                and endpoint.message_type != consumer.message_type
            ):
                continue
            relation_targets[endpoint.id].add(consumer.module)

    entries = [
        endpoint for endpoint in inventory.endpoints
        if (endpoint.system, endpoint.role) in _ENTRY_ROLES
    ]
    entry_results = [
        _entry_status(endpoint, methods, flows, endpoint_by_id, relation_targets)
        for endpoint in sorted(entries, key=lambda item: (item.module or "", item.path, item.start_line, item.id))
    ]
    external_relations = [
        edge for edge in graph_edges
        if edge.from_service != edge.to_service
    ]
    status_counts = Counter(str(item["status"]) for item in entry_results)
    return {
        "kind": "flow_diagnostic",
        "version": 1,
        "profile": {
            "topic_strategy": inventory.profile.topic_strategy,
            "persisted_snapshot_only": True,
        },
        "summary": {
            "services": len({endpoint.module for endpoint in inventory.endpoints if endpoint.module}),
            "external_entries": len(entries),
            "external_effects": sum(
                1 for endpoint in inventory.endpoints
                if (endpoint.system, endpoint.role) in _EFFECT_ROLES
            ),
            "integration_methods": len(methods),
            "local_flows": sum(
                1 for flow in flows if len(_flow_modules(flow, endpoint_by_id)) <= 1
            ),
            "global_flows": sum(
                1 for flow in flows if len(_flow_modules(flow, endpoint_by_id)) > 1
            ),
            "external_relations": len(external_relations),
            "entries_by_status": dict(sorted(status_counts.items())),
        },
        "entries": entry_results,
        "external_relations": [
            {
                "kind": edge.kind,
                "from_service": edge.from_service,
                "to_service": edge.to_service,
                "from_endpoint": edge.from_endpoint.id,
                "to_endpoint": edge.to_endpoint.id if edge.to_endpoint else None,
                "name": edge.from_endpoint.topic,
            }
            for edge in external_relations
        ],
        "warnings": [*inventory.warnings],
        "diagnostics": [
            {"path": item.path, "category": item.category, "severity": item.severity, "detail": item.detail}
            for item in inventory.diagnostics
        ],
    }


def render_flow_diagnostic_text(result: dict[str, object]) -> str:
    summary = result["summary"]
    assert isinstance(summary, dict)
    lines = [
        "Diagnostic des flux",
        f"Entrées externes : {summary['external_entries']}",
        f"Méthodes d'intégration : {summary['integration_methods']}",
        f"Flux locaux : {summary['local_flows']}",
        f"Flux globaux : {summary['global_flows']}",
        f"Relations externes : {summary['external_relations']}",
        "",
    ]
    entries = result["entries"]
    assert isinstance(entries, list)
    for item in entries:
        assert isinstance(item, dict)
        lines.append(
            f"- [{item['status']}] {item['service'] or '<unknown>'} "
            f"{item['entry']} : local={item['local_flows']} global={item['global_flows']}"
        )
    return "\n".join(lines)
