"""Audit persisted indexing quality without reparsing the source tree."""

from collections import Counter, defaultdict
from typing import Callable

from systemlens.application.architecture_inventory import ArchitectureInventory
from systemlens.domain.code_flows import CodeFlow, IntegrationMethod
from systemlens.domain.graph import build_graph, group_endpoints_by_module, resolve_rest_target_service
from systemlens.domain.models import MessageEndpoint


_ENTRY_ROLES = {("rest", "serve"), ("kafka", "consume")}
_EFFECT_ROLES = {("rest", "call"), ("kafka", "produce")}
_RULES = (
    "extraction_diagnostic", "inventory_warning", "endpoint_without_module",
    "dynamic_kafka_topic", "unknown_kafka_message_type", "kafka_type_mismatch",
    "kafka_producer_without_consumer", "kafka_consumer_without_producer",
    "ambiguous_http_target", "unmatched_http_call", "entry_without_method_fact",
    "entry_without_external_effect", "output_without_entry", "flow_missing_endpoint",
    "partial_flow", "cycle_flow", "low_confidence_flow", "local_flow_external_gap",
    "orphan_call_graph_edge", "uncertain_call_graph_edge",
)


def _source(endpoint: MessageEndpoint) -> dict[str, object]:
    return {
        "path": endpoint.path,
        "start_line": endpoint.start_line,
        "end_line": endpoint.end_line,
        "snippet": endpoint.snippet,
    }


def _issue(
    rule: str,
    severity: str,
    message: str,
    *,
    source: dict[str, object] | None = None,
    service: str | None = None,
) -> dict[str, object]:
    return {
        "rule": rule,
        "severity": severity,
        "message": message,
        "service": service,
        "source": source,
    }


def audit_indexing(inventory: ArchitectureInventory) -> dict[str, object]:
    """Return varied, evidence-backed quality findings for one index snapshot."""
    endpoints = inventory.endpoints
    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints}
    methods = inventory.integration_methods
    flows = inventory.code_flows
    edges = build_graph(
        group_endpoints_by_module(endpoints), strategy1=inventory.strategy1
    )
    service_names = sorted({endpoint.module for endpoint in endpoints if endpoint.module})
    issues: list[dict[str, object]] = []

    def add_many(rule: str, check: Callable[[], list[dict[str, object]]]) -> None:
        issues.extend(check())

    add_many("extraction_diagnostic", lambda: [
        _issue(
            "extraction_diagnostic", item.severity,
            f"{item.extractor}: {item.detail}",
            source={"path": item.path, "start_line": None, "end_line": None},
        )
        for item in inventory.diagnostics
    ])
    add_many("inventory_warning", lambda: [
        _issue("inventory_warning", "warning", warning)
        for warning in dict.fromkeys(inventory.warnings)
    ])

    def endpoint_rule(rule: str, predicate: Callable[[MessageEndpoint], bool], severity: str, message: Callable[[MessageEndpoint], str]) -> list[dict[str, object]]:
        return [
            _issue(rule, severity, message(endpoint), source=_source(endpoint), service=endpoint.module)
            for endpoint in endpoints if predicate(endpoint)
        ]

    add_many("endpoint_without_module", lambda: endpoint_rule(
        "endpoint_without_module", lambda endpoint: endpoint.module is None, "warning",
        lambda endpoint: f"{endpoint.topic!r} n'est rattaché à aucun module.",
    ))
    add_many("dynamic_kafka_topic", lambda: endpoint_rule(
        "dynamic_kafka_topic", lambda endpoint: endpoint.system == "kafka" and endpoint.topic_dynamic, "warning",
        lambda endpoint: f"Le topic Kafka {endpoint.topic!r} est dynamique.",
    ))
    add_many("unknown_kafka_message_type", lambda: endpoint_rule(
        "unknown_kafka_message_type", lambda endpoint: endpoint.system == "kafka" and not endpoint.message_type, "info",
        lambda endpoint: f"Le type du message Kafka {endpoint.topic!r} est inconnu.",
    ))

    producers: dict[str, list[MessageEndpoint]] = defaultdict(list)
    consumers: dict[str, list[MessageEndpoint]] = defaultdict(list)
    for endpoint in endpoints:
        if endpoint.system != "kafka" or endpoint.topic_dynamic:
            continue
        if endpoint.role == "produce":
            producers[endpoint.topic].append(endpoint)
        elif endpoint.role == "consume":
            consumers[endpoint.topic].append(endpoint)

    add_many("kafka_type_mismatch", lambda: [
        _issue(
            "kafka_type_mismatch", "warning",
            f"Le topic Kafka {topic!r} a des types incompatibles.",
            source=_source(producer), service=producer.module,
        )
        for topic, topic_producers in producers.items()
        for producer in topic_producers
        for consumer in consumers.get(topic, [])
        if producer.message_type and consumer.message_type
        and producer.message_type != consumer.message_type
    ])
    add_many("kafka_producer_without_consumer", lambda: [
        _issue(
            "kafka_producer_without_consumer", "warning",
            f"Aucun consommateur indexé pour le topic Kafka {producer.topic!r}.",
            source=_source(producer), service=producer.module,
        )
        for topic, topic_producers in producers.items()
        if not consumers.get(topic)
        for producer in topic_producers
    ])
    add_many("kafka_consumer_without_producer", lambda: [
        _issue(
            "kafka_consumer_without_producer", "info",
            f"Aucun producteur indexé pour le topic Kafka {consumer.topic!r}.",
            source=_source(consumer), service=consumer.module,
        )
        for topic, topic_consumers in consumers.items()
        if not producers.get(topic)
        for consumer in topic_consumers
    ])

    matched_http = {edge.from_endpoint.id for edge in edges if edge.kind == "rest"}
    add_many("ambiguous_http_target", lambda: [
        _issue(
            "ambiguous_http_target", "warning",
            f"La cible HTTP explicite {resolution.hint!r} correspond à plusieurs services.",
            source=_source(endpoint), service=endpoint.module,
        )
        for endpoint in endpoints
        if endpoint.system == "rest" and endpoint.role == "call"
        and endpoint.id not in matched_http
        for resolution in [resolve_rest_target_service(endpoint, service_names)]
        if resolution.status == "ambiguous"
    ])
    add_many("unmatched_http_call", lambda: endpoint_rule(
        "unmatched_http_call",
        lambda endpoint: endpoint.system == "rest" and endpoint.role == "call" and endpoint.id not in matched_http
        and resolve_rest_target_service(endpoint, service_names).status != "ambiguous",
        "warning",
        lambda endpoint: f"Aucun fournisseur indexé pour l'appel HTTP {endpoint.topic!r}.",
    ))

    methods_by_input: dict[str, list[IntegrationMethod]] = defaultdict(list)
    methods_by_output: dict[str, list[IntegrationMethod]] = defaultdict(list)
    for method in methods:
        for endpoint_id in method.input_endpoint_ids:
            methods_by_input[endpoint_id].append(method)
        for endpoint_id in method.output_endpoint_ids:
            methods_by_output[endpoint_id].append(method)
    flows_by_input: dict[str, list[CodeFlow]] = defaultdict(list)
    for flow in flows:
        if flow.steps and flow.steps[0].endpoint_id:
            flows_by_input[flow.steps[0].endpoint_id].append(flow)

    add_many("entry_without_method_fact", lambda: endpoint_rule(
        "entry_without_method_fact",
        lambda endpoint: (endpoint.system, endpoint.role) in _ENTRY_ROLES and not methods_by_input[endpoint.id],
        "warning",
        lambda endpoint: f"Aucune méthode d'intégration ne porte l'entrée {endpoint.topic!r}.",
    ))
    add_many("entry_without_external_effect", lambda: endpoint_rule(
        "entry_without_external_effect",
        lambda endpoint: (endpoint.system, endpoint.role) in _ENTRY_ROLES
        and bool(methods_by_input[endpoint.id])
        and not any(method.output_endpoint_ids for method in methods_by_input[endpoint.id])
        and not flows_by_input[endpoint.id],
        "info",
        lambda endpoint: f"L'entrée {endpoint.topic!r} n'a aucun effet externe indexé.",
    ))
    add_many("output_without_entry", lambda: endpoint_rule(
        "output_without_entry",
        lambda endpoint: (endpoint.system, endpoint.role) in _EFFECT_ROLES and not any(
            flow for flow in flows if any(step.endpoint_id == endpoint.id for step in flow.steps)
        ),
        "info",
        lambda endpoint: f"La sortie {endpoint.topic!r} n'est atteinte par aucun flux indexé.",
    ))

    add_many("flow_missing_endpoint", lambda: [
        _issue(
            "flow_missing_endpoint", "warning",
            f"Le flux {flow.id} référence l'endpoint absent {step.endpoint_id!r}.",
            service=flow.module,
            source={"path": flow.path, "start_line": flow.start_line, "end_line": flow.end_line},
        )
        for flow in flows
        for step in flow.steps
        if step.endpoint_id and step.endpoint_id not in endpoint_by_id
    ])
    add_many("partial_flow", lambda: [
        _issue(
            "partial_flow", "warning",
            f"Le flux {flow.id} est marqué comme {flow.reconciliation}.",
            service=flow.module,
            source={"path": flow.path, "start_line": flow.start_line, "end_line": flow.end_line},
        )
        for flow in flows if flow.reconciliation == "partial"
    ])
    add_many("cycle_flow", lambda: [
        _issue(
            "cycle_flow", "warning", f"Le flux {flow.id} contient un cycle d'appels.",
            service=flow.module,
            source={"path": flow.path, "start_line": flow.start_line, "end_line": flow.end_line},
        )
        for flow in flows if flow.status == "cycle"
    ])
    add_many("low_confidence_flow", lambda: [
        _issue(
            "low_confidence_flow", "info", f"Le flux {flow.id} repose sur une résolution faible.",
            service=flow.module,
            source={"path": flow.path, "start_line": flow.start_line, "end_line": flow.end_line},
        )
        for flow in flows if flow.confidence == "low"
    ])
    add_many("local_flow_external_gap", lambda: [
        _issue(
            "local_flow_external_gap", "warning",
            f"L'entrée {entry.topic!r} a un flux local mais aucun flux inter-service.",
            service=entry.module, source=_source(entry),
        )
        for entry in endpoints
        if (entry.system, entry.role) in _ENTRY_ROLES
        and flows_by_input[entry.id]
        and not any(len({endpoint_by_id[step.endpoint_id].module for step in flow.steps if step.endpoint_id and step.endpoint_id in endpoint_by_id}) > 1 for flow in flows_by_input[entry.id])
        and any(edge.from_endpoint.id in {step.endpoint_id for flow in flows_by_input[entry.id] for step in flow.steps if step.endpoint_id} for edge in edges)
    ])

    method_ids = {method.id for method in methods}
    add_many("orphan_call_graph_edge", lambda: [
        _issue(
            "orphan_call_graph_edge", "warning",
            f"L'arête CodeQL {edge.caller_id} → {edge.callee_id} référence une méthode absente.",
        )
        for edge in inventory.codeql_call_edges
        if edge.caller_id not in method_ids or edge.callee_id not in method_ids
    ])
    add_many("uncertain_call_graph_edge", lambda: [
        _issue(
            "uncertain_call_graph_edge", "info",
            f"L'arête CodeQL {edge.caller_id} → {edge.callee_id} "
            f"utilise une résolution {'inférée' if edge.inferred else 'possible'}.",
            source={"path": edge.path, "start_line": edge.line, "end_line": edge.line},
        )
        for edge in inventory.codeql_call_edges
        if edge.dispatch_confidence == "possible" or edge.inferred
    ])

    rule_count = len(_RULES)
    counts = Counter(str(item["rule"]) for item in issues)
    by_rule = {rule: counts.get(rule, 0) for rule in _RULES}
    severity_rank = {"warning": 0, "info": 1}
    issues.sort(key=lambda item: (
        severity_rank.get(str(item["severity"]), 99), str(item["rule"]), str(item["message"])
    ))
    return {
        "kind": "indexing_audit",
        "version": 1,
        "rule_count": rule_count,
        "finding_count": len(issues),
        "by_rule": by_rule,
        "by_severity": dict(sorted(Counter(str(item["severity"]) for item in issues).items())),
        "issues": issues,
    }
