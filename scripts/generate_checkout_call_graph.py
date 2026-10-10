"""Build one synthetic checkout call graph with twenty-one interactions."""

import json
from pathlib import Path

from systemlens.domain.models import compute_endpoint_id


def build_manifest() -> dict:
    services = ["gateway", "checkout", "customer", "catalog", "pricing",
                "payment", "reservation", "inventory", "domain-inventory",
                "shipping", "notification"]
    # Parent interaction identifies the exact handler occurrence, not just its service.
    calls = [
        (0, "gateway", "checkout", "http", "POST /checkout"),
        (1, "checkout", "customer", "http", "GET /customer/profile"),
        (1, "checkout", "catalog", "http", "POST /catalog/validate"),
        (3, "catalog", "pricing", "http", "POST /pricing/quote"),
        (4, "pricing", "customer", "http", "GET /customer/discount"),
        (1, "checkout", "payment", "http", "POST /payment/authorize"),
        (6, "payment", "customer", "http", "GET /customer/billing"),
        (1, "checkout", "reservation", "event", "stock.reserve.commands"),
        (8, "reservation", "catalog", "http", "GET /catalog/sku"),
        (8, "reservation", "inventory", "event", "inventory.adjust.commands"),
        (10, "inventory", "domain-inventory", "event", "inventory.write.commands"),
        (10, "inventory", "shipping", "event", "inventory.reserved.events"),
        (12, "shipping", "customer", "http", "GET /customer/address"),
        (12, "shipping", "notification", "event", "shipping.prepared.events"),
        (1, "checkout", "shipping", "http", "POST /shipping/preview"),
        # Shipping consumes the event emitted by inventory, then calls the
        # domain service synchronously for the read.
        (12, "shipping", "domain-inventory", "http", "GET /inventory/availability"),
        (15, "shipping", "pricing", "http", "POST /pricing/shipping"),
        (15, "shipping", "customer", "http", "GET /customer/delivery-preferences"),
        (1, "checkout", "notification", "event", "checkout.accepted.events"),
        (19, "notification", "customer", "http", "GET /customer/contact"),
        (6, "payment", "notification", "event", "payment.authorized.events"),
    ]
    manifest = {
        "format": "systemlens-ai-graph-v1",
        "generated_by": {"namespace": "synthetic-checkout-race", "pass": "001"},
        "mode": "partial",
        "description": "Synthetic scenario, not extracted source evidence. One checkout root, eleven services, twenty-one interactions.",
        "nodes": [{"id": s, "name": s, "kind": "service"} for s in services],
        "edges": [], "endpoints": [], "flows": [],
        "scenario": {
            "root_flow_id": "checkout-root", "interaction_count": 21,
            "async_write_path": [1, 8, 10, 11], "sync_read_path": [1, 8, 10, 12, 16],
            "publication_before_read": [8, 10, 11, 12, 16],
            "resource": "inventory-primary.stock", "key_expression": "checkout.sku",
            "expected_operation": "checkout.reservationOperationId",
            "problem": "Inventory delegates the write to domain-inventory through Kafka, while inventory also emits an event that leads shipping to read domain-inventory synchronously over HTTP before the async write may be visible.",
            "ordering": "Sibling continuations branch from their parent handler. Step order is a display order, not a global completion order.",
            "possible_schedules": ["publish, read old stock, consume, commit", "publish, consume, commit, read updated stock"],
        },
    }
    flows = {}
    entry_endpoint_ids = {}

    def make_step(endpoint, order, kind):
        return {"order": order, "kind": kind, "name": endpoint["topic"],
                "path": endpoint["path"], "start_line": endpoint["start_line"],
                "end_line": endpoint["end_line"], "endpoint_id": endpoint["id"]}

    def make_flow(number, service, entry):
        return {"id": "checkout-root" if number == 0 else f"handler-{number:02}",
                "module": service, "method": f"{service}.handle{number}",
                "path": entry["path"], "start_line": 1, "end_line": 100,
                "status": "potential", "confidence": "medium",
                "reason": "Synthetic handler occurrence in the single checkout scenario.",
                "steps": [make_step(entry, 1, "message_entry" if entry["system"] == "kafka" else "http_entry")]}

    entry = {"id": "checkout-http-root", "service": "gateway", "role": "serve",
             "system": "rest", "topic": "POST /shop/checkout",
             "path": "synthetic/gateway/entry.java", "start_line": 1, "end_line": 1}
    manifest["endpoints"].append(entry)
    flows[0] = make_flow(0, "gateway", entry)
    interaction_data = []
    for number, (parent, source, target, kind, channel) in enumerate(calls, 1):
        path = f"synthetic/interactions/call-{number:02}.java"
        manifest["edges"].append({"id": f"call-{number:02}", "source": source,
            "target": target, "kind": kind, "channel": channel, "label": channel,
            "confidence": "medium", "evidence": [{"path": path, "start_line": 1}],
            "metadata": {"synthetic": True, "parent_interaction": parent}})
        pair = []
        for service, role in [(source, "produce" if kind == "event" else "call"),
                              (target, "consume" if kind == "event" else "serve")]:
            ep = {"id": compute_endpoint_id(role, channel, path, 1), "service": service,
                  "role": role, "system": "kafka" if kind == "event" else "rest",
                  "topic": channel, "path": path, "start_line": 1, "end_line": 1}
            manifest["endpoints"].append(ep)
            pair.append(ep)
        interaction_data.append((number, parent, kind, pair))
        flows[number] = make_flow(number, target, pair[1])
        entry_endpoint_ids[number] = pair[1]["id"]

    for _number, parent, kind, pair in interaction_data:
        flows[parent]["steps"].append(make_step(
            pair[0], len(flows[parent]["steps"]) + 1,
            "message_publish" if kind == "event" else "http_call",
        ))

    # Access evidence stays on the handlers that perform it. Detection joins
    # those handlers through the persisted call-tree reachability index.
    for number, access, branch in [(11, "write", "async"), (16, "read", "sync")]:
        witness = {"order": len(flows[0]["steps"]) + 1, "kind": f"data_{access}",
                   "name": "inventory-primary.stock", "resource": "inventory-primary.stock",
                   "path": f"synthetic/inventory/handler-{number}.java", "start_line": 20,
                   "end_line": 20, "branch": branch, "key": "checkout.sku",
                   "endpoint_id": entry_endpoint_ids[number],
                   "causal_id": "checkout.requestId", "expected_version": "checkout.reservationOperationId",
                   "query_shape": "point", "completion": "ack_only" if access == "write" else None}
        flows[number]["steps"].append({**witness, "order": len(flows[number]["steps"]) + 1})
    manifest["flows"] = list(flows.values())
    return manifest


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "examples/checkout-single-call-graph.json"
    target.write_text(json.dumps(build_manifest(), indent=2) + "\n", encoding="utf-8")
    print(target)
