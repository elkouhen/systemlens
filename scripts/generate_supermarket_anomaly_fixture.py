"""Generate one call graph that demonstrates three data-anomaly classes."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "examples" / "supermarket-three-anomalies.json"


def endpoint(
    endpoint_id: str,
    service: str,
    role: str,
    system: str,
    topic: str,
    line: int,
) -> dict[str, object]:
    return {
        "id": endpoint_id,
        "service": service,
        "role": role,
        "system": system,
        "topic": topic,
        "path": f"{service}/src/{service.replace('-', '').title()}Flow.java",
        "start_line": line,
        "end_line": line,
        "snippet": f"{role} {topic}",
    }


def step(
    order: int,
    kind: str,
    name: str,
    service: str,
    line: int,
    **facts: object,
) -> dict[str, object]:
    return {
        "order": order,
        "kind": kind,
        "name": name,
        "path": f"{service}/src/{service.replace('-', '').title()}Flow.java",
        "start_line": line,
        "end_line": line,
        **facts,
    }


def flow(
    flow_id: str,
    service: str,
    method: str,
    start: int,
    steps: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "id": flow_id,
        "module": service,
        "method": method,
        "path": f"{service}/src/{service.replace('-', '').title()}Flow.java",
        "start_line": start,
        "end_line": start + 30,
        "status": "potential",
        "confidence": "high",
        "reason": "Synthetic, explicitly evidenced anomaly demonstration.",
        "steps": steps,
    }


def main() -> None:
    services = ["order-service", "inventory-service"]
    topic = "supermarket.order.placed"
    resource = "shared:inventory:products"
    key = "payload.productId"
    order_resource = "order-service:orders"
    product_identity = "postgresql:inventory:products"
    product_key_identity = "product:42"
    order_identity = "postgresql:orders:statuses"
    order_key_identity = "order:42"
    endpoints = [
        endpoint("orders-http", services[0], "serve", "rest", "POST /api/orders", 10),
        endpoint("orders-publish", services[0], "produce", "kafka", topic, 14),
        endpoint("inventory-consume", services[1], "consume", "kafka", topic, 20),
        endpoint("inventory-publish", services[1], "produce", "kafka", "inventory.stock-reserved", 28),
    ]
    flows = [
        flow("orders-root", services[0], "OrderFlow.place", 10, [
            step(1, "http_entry", "POST /api/orders", services[0], 10, endpoint_id="orders-http"),
            step(2, "data_read", "Product", services[0], 12, resource=resource, key=key,
                 resource_identity=product_identity, key_identity=product_key_identity,
                 action="read", branch="sync", transaction_id="tx-orders",
                 concurrency="independent_handlers", phase="observe", fields=["stockQuantity"]),
            step(3, "data_write", "Product", services[0], 13, resource=resource, key=key,
                 resource_identity=product_identity, key_identity=product_key_identity,
                 action="update", branch="sync", transaction_id="tx-orders",
                 concurrency="independent_handlers", phase="apply", fields=["stockQuantity"],
                 value_dependencies=["orders-root:2"]),
            step(4, "data_write", "OrderStatus", services[0], 14, resource=order_resource,
                 resource_identity=order_identity, key="request.orderId",
                 key_identity=order_key_identity, action="update", branch="async",
                 causal_id="order-42", request_before_reads=["orders-root:5"],
                 completion="ack_only"),
            step(5, "data_read", "OrderStatus", services[0], 15, resource=order_resource,
                 resource_identity=order_identity, key="request.orderId",
                 key_identity=order_key_identity, action="read", branch="sync",
                 causal_id="order-42"),
            step(6, "message_publish", topic, services[0], 16, endpoint_id="orders-publish"),
        ]),
        flow("inventory-handler", services[1], "InventoryFlow.reserve", 20, [
            step(1, "message_entry", topic, services[1], 20, endpoint_id="inventory-consume"),
            step(2, "data_read", "Product", services[1], 22, resource=resource, key=key,
                 resource_identity=product_identity, key_identity=product_key_identity,
                 action="read", branch="sync", causal_id="order-42", transaction_id="tx-inventory",
                 concurrency="independent_handlers", phase="observe", fields=["stockQuantity"],
                 freshness_required=True),
            step(3, "data_write", "Product", services[1], 24, resource=resource, key=key,
                 resource_identity=product_identity, key_identity=product_key_identity,
                 action="update", branch="sync", causal_id="order-42", transaction_id="tx-inventory",
                 concurrency="independent_handlers", phase="apply", fields=["stockQuantity"],
                 value_dependencies=["inventory-handler:2"], completion="unknown"),
            step(4, "message_publish", "inventory.stock-reserved", services[1], 28,
                 endpoint_id="inventory-publish"),
        ]),
    ]
    nodes = [
        {"id": service, "kind": "service", "name": service}
        for service in services
    ] + [
        {"id": name, "kind": "topic", "name": name, "technology": "kafka"}
        for name in (topic, "inventory.stock-reserved")
    ] + [
        {"id": "products", "kind": "collection", "name": resource, "technology": "postgresql"},
        {"id": "orders-db", "kind": "collection", "name": order_resource, "technology": "postgresql"},
    ]
    edges = [
        {"id": "order-publishes", "source": services[0], "target": topic,
         "kind": "event", "relation": "publishes", "channel": topic, "confidence": "high"},
        {"id": "inventory-consumes", "source": topic, "target": services[1],
         "kind": "event", "relation": "consumes", "channel": topic, "confidence": "high"},
        {"id": "inventory-publishes", "source": services[1], "target": "inventory.stock-reserved",
         "kind": "event", "relation": "publishes", "confidence": "high"},
    ]
    manifest = {
        "format": "systemlens-ai-graph-v1",
        "generated_by": {"agent": "SystemLens", "model": "synthetic-fixture", "pass": "001",
                         "namespace": "supermarket-three-anomalies"},
        "mode": "partial",
        "description": (
            "One order call graph with an async read-after-write risk, a freshness "
            "anomaly candidate, and a lost-update candidate. Uses synthetic shared-database facts."
        ),
        "nodes": nodes,
        "edges": edges,
        "endpoints": endpoints,
        "flows": flows,
    }
    DESTINATION.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {DESTINATION}")


if __name__ == "__main__":
    main()
