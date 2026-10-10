"""Generate the deterministic complex async-consistency demonstration model."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "examples" / "async-write-sync-read-complex-facts.json"


def endpoint(endpoint_id: str, service: str, role: str, system: str, name: str, line: int) -> dict[str, object]:
    return {
        "id": endpoint_id,
        "service": service,
        "role": role,
        "system": system,
        "topic": name,
        "path": f"src/{service}/{service.title().replace('-', '')}Flow.java",
        "start_line": line,
        "end_line": line,
        "snippet": f"{role} {name}",
    }


def step(order: int, kind: str, name: str, path: str, line: int, **metadata: str) -> dict[str, object]:
    return {
        "order": order,
        "kind": kind,
        "name": name,
        "path": path,
        "start_line": line,
        "end_line": line,
        **metadata,
    }


def main() -> None:
    services = ["orders", "inventory", "payments", "shipping", "notifications"]
    topics = [
        "orders.created", "orders.updated", "inventory.reserved", "inventory.released",
        "payments.authorized", "payments.failed", "shipping.requested", "shipping.created",
        "notifications.send", "orders.reconciled",
    ]
    nodes: list[dict[str, object]] = [
        {"id": name, "kind": "service", "name": name}
        for name in services
    ] + [
        {"id": topic, "kind": "topic", "name": topic, "technology": "kafka"}
        for topic in topics
    ] + [
        {"id": f"db:{collection}", "kind": "collection", "name": collection, "technology": "postgresql"}
        for collection in ["orders", "inventory", "payments", "shipments", "notifications"]
    ]
    edges: list[dict[str, object]] = []
    edge_number = 1

    def add_edge(source: str, target: str, kind: str, relation: str) -> None:
        nonlocal edge_number
        edges.append({
            "id": f"edge-{edge_number}", "source": source, "target": target,
            "kind": kind, "relation": relation, "confidence": "high",
        })
        edge_number += 1

    http_calls = [
        ("orders", "inventory", "/inventory/reserve"),
        ("orders", "payments", "/payments/authorize"),
        ("orders", "shipping", "/shipping/quote"),
        ("orders", "notifications", "/notifications/preview"),
        ("inventory", "orders", "/orders/reservation"),
        ("payments", "orders", "/orders/payment-status"),
        ("shipping", "inventory", "/inventory/availability"),
        ("shipping", "notifications", "/notifications/shipping"),
        ("notifications", "orders", "/orders/notification-status"),
        ("inventory", "shipping", "/shipping/release"),
    ]
    for source, target, route in http_calls:
        add_edge(source, target, "http", route)
    kafka_routes = [
        ("orders", "orders.created"), ("orders", "orders.updated"),
        ("inventory", "inventory.reserved"), ("inventory", "inventory.released"),
        ("payments", "payments.authorized"), ("payments", "payments.failed"),
        ("shipping", "shipping.requested"), ("shipping", "shipping.created"),
        ("notifications", "notifications.send"), ("orders", "orders.reconciled"),
    ]
    for source, topic in kafka_routes:
        add_edge(source, topic, "event", "publishes")
        consumers = {
            "orders.created": "inventory", "orders.updated": "inventory",
            "inventory.reserved": "payments", "inventory.released": "shipping",
            "payments.authorized": "shipping", "payments.failed": "notifications",
            "shipping.requested": "shipping", "shipping.created": "notifications",
            "notifications.send": "notifications", "orders.reconciled": "orders",
        }
        add_edge(topic, consumers[topic], "event", "consumes")
    for service, collection in zip(services, ["orders", "inventory", "payments", "shipments", "notifications"], strict=True):
        add_edge(service, f"db:{collection}", "data", "writes")
        add_edge(service, f"db:{collection}", "data", "reads")

    endpoints: list[dict[str, object]] = []
    flows: list[dict[str, object]] = []
    scenarios = [
        ("create-order", "orders", "orders.created", "inventory", "orders", "potential_risk", "ack_only"),
        ("reserve-stock", "inventory", "inventory.reserved", "payments", "inventory", "potential_risk", "fixed_delay"),
        ("authorize-payment", "payments", "payments.authorized", "shipping", "payments", "guarantee_identified", "visible"),
        ("request-shipping", "shipping", "shipping.requested", "shipping", "shipments", "potential_risk", "ack_only"),
        ("notify-payment-failure", "notifications", "notifications.send", "notifications", "notifications", "insufficient_evidence", "unknown"),
        ("reconcile-order", "orders", "orders.reconciled", "orders", "orders", "potential_risk", "old_write_confirmation"),
        ("release-stock", "inventory", "inventory.released", "shipping", "inventory", "potential_risk", "ack_only"),
        ("shipping-created", "shipping", "shipping.created", "notifications", "shipments", "potential_risk", "ack_only"),
    ]
    for number, (slug, service, topic, consumer, resource, expected, completion) in enumerate(scenarios, start=1):
        base = number * 20
        flow_id = f"complex-{slug}"
        entry_id = f"ep-{slug}-entry"
        publish_id = f"ep-{slug}-publish"
        consume_id = f"ep-{slug}-consume"
        call_id = f"ep-{slug}-http"
        endpoints.extend([
            endpoint(entry_id, service, "serve", "rest", f"POST /{slug}", base),
            endpoint(publish_id, service, "produce", "kafka", topic, base + 4),
            endpoint(consume_id, consumer, "consume", "kafka", topic, base + 9),
            endpoint(call_id, service, "call", "rest", f"/{consumer}/status", base + 14),
        ])
        flow_path = f"src/{service}/{service.title().replace('-', '')}Flow.java"
        causal = f"causal-{slug}"
        steps = [
            step(1, "http_entry", f"POST /{slug}", flow_path, base, endpoint_id=entry_id, branch="sync"),
            step(2, "message_publish", topic, flow_path, base + 4, endpoint_id=publish_id, branch="async", resource=resource, key="request.id", causal_id=causal, expected_version=f"v{number}"),
            step(3, "message_entry", topic, f"src/{consumer}/{consumer.title().replace('-', '')}Flow.java", base + 9, endpoint_id=consume_id, branch="async", causal_id=causal),
            step(4, "data_write", resource, f"src/{consumer}/{consumer.title().replace('-', '')}Repository.java", base + 11, branch="async", resource=resource, key="request.id", causal_id=causal, expected_version=f"v{number}", completion=completion),
            step(5, "http_call", f"/{consumer}/status", flow_path, base + 14, endpoint_id=call_id, branch="sync"),
            step(6, "data_read", resource, flow_path, base + 16, branch="sync", resource=resource, key="request.id", causal_id=(causal if expected != "insufficient_evidence" else "unresolved-causal-link"), expected_version=f"v{number}", query_shape="point"),
        ]
        flows.append({
            "id": flow_id, "module": service, "method": f"{service.title().replace('-', '')}Flow.{slug}",
            "path": flow_path, "start_line": base, "end_line": base + 18,
            "status": "potential", "confidence": "high" if expected != "insufficient_evidence" else "low",
            "reason": f"Synthetic {slug} flow with HTTP and Kafka continuations.",
            "steps": steps, "expected_diagnostics": [expected] if expected != "guarantee_identified" else [expected],
        })

    manifest = {
        "format": "systemlens-ai-graph-v1",
        "generated_by": {"namespace": "async-consistency-complex", "pass": "001"},
        "mode": "partial",
        "description": "Synthetic complex model: 5 services, 20 HTTP/Kafka interactions and consistency diagnostics.",
        "nodes": nodes,
        "edges": edges,
        "endpoints": endpoints,
        "flows": flows,
    }
    DESTINATION.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {DESTINATION} ({len(services)} services, {len(http_calls) + 2 * len(kafka_routes)} network routes, {len(flows)} flows)")


if __name__ == "__main__":
    main()
