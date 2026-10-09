import json
import re
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from systemlens.domain.models import ArchitectureRelation, GraphFact, MessageEndpoint, compute_endpoint_id
from systemlens.domain.graph import GraphEdge
from systemlens.domain.code_flows import (
    CodeFlow,
    CodeFlowStep,
    CodeQLCallGraphEdge,
    IntegrationMethod,
)
from systemlens.discovery.kubernetes import KubernetesWorkload
from systemlens.domain.module_inventory import (
    DiscoveredModule,
    JpaEntity,
    JpaDto,
    JpaField,
    ModuleDependency,
    MongoField,
    MongoPersistenceClass,
    MongoMethod,
)
from systemlens.discovery.build.modules import discover_modules
from systemlens.render import _vscode_file_uri, render_graph_html
from systemlens.render.call_graph import _networkx_call_graph
from systemlens.render.graph_view_model import build_graph_view_model
from systemlens.storage.sqlite import Store


def _kafka_endpoint(
    role: str, message_type: str, path: str, qualified_name: str | None = None
) -> MessageEndpoint:
    return MessageEndpoint(
        id=compute_endpoint_id(role, "orders.created", path),
        role=role,
        system="kafka",
        topic="orders.created",
        topic_dynamic=False,
        source="code",
        framework="spring-kafka",
        path=path,
        start_line=1,
        end_line=1,
        snippet="",
        message_type=message_type,
        qualified_name=qualified_name,
    )


def _rest_endpoint(role: str, resource: str, path: str) -> MessageEndpoint:
    return MessageEndpoint(
        id=compute_endpoint_id(role, resource, path),
        role=role,
        system="rest",
        topic=resource,
        topic_dynamic=False,
        source="code",
        framework="spring-mvc",
        path=path,
        start_line=1,
        end_line=1,
        snippet="",
    )


def _html_graph_data(document: str) -> dict[str, object]:
    match = re.search(
        r'<script id="graph-data" type="application/json">(.*)</script>', document
    )
    assert match is not None
    return json.loads(match.group(1))


def _flow_call_graph(data: dict[str, object], flow: dict[str, object]) -> dict[str, object]:
    graphs = data.get("call_graphs", {})
    assert isinstance(graphs, dict)
    graph = graphs[flow["call_graph_id"]]
    assert isinstance(graph, dict)
    return graph


def test_export_includes_persisted_codeql_call_graph() -> None:
    caller = IntegrationMethod("a", "orders", "Orders.receive", "Orders.java", 1, 4, ("in",), ())
    callee = IntegrationMethod("b", "orders", "Orders.publish", "Orders.java", 5, 7, (), ("out",))
    data = _html_graph_data(render_graph_html(
        {"orders": []},
        [],
        integration_methods=[caller, callee],
        codeql_call_edges=[CodeQLCallGraphEdge("a", "b", "Orders.java", 3, "exact")],
    ))

    assert data["codeql_call_graph"] == {
        "nodes": [
            {
                "id": "a",
                "module": "orders",
                "method": "Orders.receive",
                "path": "Orders.java",
                "start_line": 1,
                "end_line": 4,
                "input_endpoint_ids": ["in"],
                "output_endpoint_ids": [],
            },
            {
                "id": "b",
                "module": "orders",
                "method": "Orders.publish",
                "path": "Orders.java",
                "start_line": 5,
                "end_line": 7,
                "input_endpoint_ids": [],
                "output_endpoint_ids": ["out"],
            },
        ],
        "edges": [{
            "caller_id": "a",
            "callee_id": "b",
            "path": "Orders.java",
            "line": 3,
            "dispatch_confidence": "exact",
            "inferred": False,
        }],
    }


def test_global_input_label_references_its_local_output() -> None:
    producer = replace(_kafka_endpoint("produce", "OrderCreated", "Orders.java"), id="orders-out")
    consumer = replace(_kafka_endpoint("consume", "OrderCreated", "Payments.java"), id="payments-in")
    next_producer = replace(_kafka_endpoint("produce", "PaymentCompleted", "Payments.java"), id="payments-out")
    next_consumer = replace(_kafka_endpoint("consume", "PaymentCompleted", "Inventory.java"), id="inventory-in")

    data = _html_graph_data(render_graph_html(
        {"orders": [producer], "payments": [consumer, next_producer], "inventory": [next_consumer]},
        [
            GraphEdge("kafka", "orders", "payments", producer, consumer),
            # Repeated evidence for the same endpoint pair must produce one
            # call-graph arc in the exported model.
            GraphEdge("kafka", "orders", "payments", producer, consumer),
            GraphEdge("kafka", "payments", "inventory", next_producer, next_consumer),
        ],
        code_flows=[CodeFlow(
            id="payment-flow", module="payments", method="PaymentHandler.handle",
            path="Payments.java", start_line=1, end_line=2,
            status="potential", confidence="medium", reason="test",
            steps=(
                CodeFlowStep(1, "message_entry", "orders.created", "Payments.java", 1, 1, consumer.id),
                CodeFlowStep(2, "message_publish", "payment.completed", "Payments.java", 2, 2, next_producer.id),
            ),
        )],
    ))
    flow_graph = _flow_call_graph(data, data["code_flows"][0])
    assert flow_graph["node_order"] == ["payments", "inventory"]
    assert [
        (edge["source"], edge["target"], edge["kind"])
        for edge in flow_graph["edges"]
    ] == [("payments", "inventory", "kafka")]

    ports_by_id = {
        port["endpoint_id"]
        : port["label"]
        for node in data["nodes"]
        for port in node.get("ports", [])
    }
    assert ports_by_id == {
        "orders-out": "O1",
        "payments-in": "I1 → O2",
        "payments-out": "O2",
        "inventory-in": "I2",
    }
    port_types_by_id = {
        port["endpoint_id"]: port["message_type"]
        for node in data["nodes"]
        for port in node.get("ports", [])
    }
    assert port_types_by_id == {
        "orders-out": "OrderCreated",
        "payments-in": "OrderCreated",
        "payments-out": "PaymentCompleted",
        "inventory-in": "PaymentCompleted",
    }
    local_outputs_by_id = {
        port["endpoint_id"]: port.get("local_output_labels", [])
        for node in data["nodes"]
        for port in node.get("ports", [])
    }
    assert local_outputs_by_id == {
        "orders-out": [],
        "payments-in": ["O2"],
        "payments-out": [],
        "inventory-in": [],
    }
    payment_input = next(
        port
        for node in data["nodes"]
        for port in node.get("ports", [])
        if port["endpoint_id"] == "payments-in"
    )
    assert payment_input["local_outputs"] == [{
        "endpoint_id": "payments-out",
        "label": "O2",
        "type": "Kafka publish",
        "name": "orders.created",
        "method": "<unknown>",
        "message_type": "PaymentCompleted",
    }]
    assert data["internal_port_links"] == [
        {"input_endpoint_id": "payments-in", "output_endpoint_id": "payments-out"},
    ]
    assert data["port_links"] == [
        {"source_endpoint_id": "orders-out", "target_endpoint_id": "payments-in", "kind": "kafka"},
        {"source_endpoint_id": "payments-out", "target_endpoint_id": "inventory-in", "kind": "kafka"},
    ]
    assert ("microservice:orders", "microservice:payments", ()) not in {
        (link["source"], link["target"], tuple(link.get("endpoint_ids", [])))
        for link in data["links"]
    }
    assert {
        (link["source"], link["target"], tuple(link.get("endpoint_ids", [])))
        for link in data["links"]
    } >= {
        ("microservice:orders", "kafka_topic:orders.created", ("orders-out",)),
        ("kafka_topic:orders.created", "microservice:payments", ("payments-in",)),
        ("microservice:payments", "kafka_topic:orders.created", ("payments-out",)),
        ("kafka_topic:orders.created", "microservice:inventory", ("inventory-in",)),
    }


def test_call_graph_retains_all_typed_kafka_fanout_branches() -> None:
    producer = replace(_kafka_endpoint("produce", "OrderCreated", "Publisher.java"), id="orders-out")
    inventory = replace(_kafka_endpoint("consume", "OrderCreated", "Inventory.java"), id="inventory-in")
    restock = replace(_kafka_endpoint("consume", "OrderCreated", "Restock.java"), id="restock-in")
    data = _html_graph_data(render_graph_html(
        {"orders": [producer], "inventory": [inventory], "restock": [restock]},
        [
            GraphEdge("kafka", "orders", "inventory", producer, inventory),
            GraphEdge("kafka", "orders", "restock", producer, restock),
        ],
        code_flows=[CodeFlow(
            id="fanout-flow", module="orders", method="ScheduledPublisher.publish",
            path="Publisher.java", start_line=1, end_line=2,
            status="potential", confidence="medium", reason="fanout",
            steps=(
                CodeFlowStep(1, "message_publish", "orders.created", "Publisher.java", 1, 1, producer.id),
                CodeFlowStep(2, "message_entry", "orders.created", "Inventory.java", 1, 1, inventory.id),
            ),
        )],
    ))
    flow_graph = _flow_call_graph(data, data["code_flows"][0])
    assert {
        (edge["source"], edge["target"])
        for edge in flow_graph["edges"]
    } == {("orders", "inventory"), ("orders", "restock")}


def test_export_keeps_equivalent_persisted_flows() -> None:
    producer = replace(_kafka_endpoint("produce", "OrderCreated", "Publisher.java"), id="orders-out")
    consumer = replace(_kafka_endpoint("consume", "OrderCreated", "Inventory.java"), id="inventory-in")
    steps = (
        CodeFlowStep(1, "message_publish", "orders.created", "Publisher.java", 1, 1, producer.id),
        CodeFlowStep(2, "message_entry", "orders.created", "Inventory.java", 1, 1, consumer.id),
    )
    first = CodeFlow(
        id="first-flow", module="orders", method="Publisher.publish",
        path="Publisher.java", start_line=1, end_line=2,
        status="potential", confidence="medium", reason="first", steps=steps,
    )
    duplicate = replace(first, id="duplicate-flow", reason="equivalent route")
    data = _html_graph_data(render_graph_html(
        {"orders": [producer], "inventory": [consumer]},
        [GraphEdge("kafka", "orders", "inventory", producer, consumer)],
        code_flows=[first, duplicate],
    ))
    assert len(data["code_flows"]) == 2
    assert {flow["id"] for flow in data["code_flows"]} == {
        "first-flow", "duplicate-flow"
    }


def test_export_fuses_kafka_producer_and_consumer_fragments() -> None:
    producer = replace(_kafka_endpoint("produce", "OrderCreated", "Publisher.java"), id="orders-out")
    consumer = replace(_kafka_endpoint("consume", "OrderCreated", "Inventory.java"), id="inventory-in")
    downstream = replace(
        _kafka_endpoint("produce", "StockDepleted", "Inventory.java"),
        id="stock-out",
        topic="stock.depleted",
    )
    restock = replace(
        _kafka_endpoint("consume", "StockDepleted", "Restock.java"),
        id="restock-in",
        topic="stock.depleted",
    )
    scheduled = CodeFlow(
        id="scheduled-flow", module="orders", method="Publisher.publish",
        path="Publisher.java", start_line=1, end_line=2,
        status="potential", confidence="medium", reason="scheduled",
        steps=(CodeFlowStep(1, "cron_entry", "cron", "Publisher.java", 1, 1),
               CodeFlowStep(2, "message_publish", "orders.created", "Publisher.java", 2, 2, producer.id)),
    )
    consumer_fragment = CodeFlow(
        id="consumer-flow", module="inventory", method="Consumer.consume",
        path="Inventory.java", start_line=1, end_line=5,
        status="potential", confidence="medium", reason="consumer",
        steps=(CodeFlowStep(1, "message_entry", "orders.created", "Inventory.java", 1, 1, consumer.id),
               CodeFlowStep(2, "message_publish", "stock.depleted", "Inventory.java", 5, 5, downstream.id)),
    )
    data = _html_graph_data(render_graph_html(
        {"orders": [producer], "inventory": [consumer, downstream], "restock": [restock]},
        [
            GraphEdge("kafka", "orders", "inventory", producer, consumer),
            GraphEdge("kafka", "inventory", "restock", downstream, restock),
        ],
        code_flows=[scheduled, consumer_fragment],
    ))

    assert len(data["code_flows"]) == 2
    assert {flow["module"] for flow in data["code_flows"]} == {"orders", "inventory"}
    flow = next(flow for flow in data["code_flows"] if flow["module"] == "orders")
    assert {
        (edge["source"], edge["target"])
            for edge in _flow_call_graph(data, flow)["edges"]
    } == {("orders", "inventory"), ("inventory", "restock")}


def test_export_fusion_deduplicates_identical_inter_service_edges() -> None:
    producer = replace(_kafka_endpoint("produce", "OrderCreated", "Publisher.java"), id="orders-out")
    consumer = replace(_kafka_endpoint("consume", "OrderCreated", "Inventory.java"), id="inventory-in")
    first_output = replace(
        _kafka_endpoint("produce", "StockDepleted", "Inventory.java"),
        id="stock-out",
        topic="stock.depleted",
    )
    second_output = replace(
        _kafka_endpoint("produce", "StockLow", "Inventory.java"),
        id="stock-low-out",
        topic="stock.low",
    )
    first_consumer = replace(
        _kafka_endpoint("consume", "StockDepleted", "Restock.java"),
        id="restock-in",
        topic="stock.depleted",
    )
    second_consumer = replace(
        _kafka_endpoint("consume", "StockLow", "Alerts.java"),
        id="alerts-in",
        topic="stock.low",
    )
    scheduled = CodeFlow(
        id="scheduled-flow", module="orders", method="Publisher.publish",
        path="Publisher.java", start_line=1, end_line=2,
        status="potential", confidence="medium", reason="scheduled",
        steps=(CodeFlowStep(1, "cron_entry", "cron", "Publisher.java", 1, 1),
               CodeFlowStep(2, "message_publish", "orders.created", "Publisher.java", 2, 2, producer.id)),
    )
    first_fragment = CodeFlow(
        id="first-consumer-flow", module="inventory", method="Consumer.consume",
        path="Inventory.java", start_line=1, end_line=5,
        status="potential", confidence="medium", reason="consumer",
        steps=(CodeFlowStep(1, "message_entry", "orders.created", "Inventory.java", 1, 1, consumer.id),
               CodeFlowStep(2, "message_publish", "stock.depleted", "Inventory.java", 5, 5, first_output.id)),
    )
    second_fragment = replace(
        first_fragment,
        id="second-consumer-flow",
        steps=(CodeFlowStep(1, "message_entry", "orders.created", "Inventory.java", 1, 1, consumer.id),
               CodeFlowStep(2, "message_publish", "stock.low", "Inventory.java", 6, 6, second_output.id)),
    )
    data = _html_graph_data(render_graph_html(
        {"orders": [producer], "inventory": [consumer, first_output, second_output],
         "restock": [first_consumer], "alerts": [second_consumer]},
        [
            GraphEdge("kafka", "orders", "inventory", producer, consumer),
            GraphEdge("kafka", "inventory", "restock", first_output, first_consumer),
            GraphEdge("kafka", "inventory", "alerts", second_output, second_consumer),
        ],
        code_flows=[scheduled, first_fragment, second_fragment],
    ))

    edges = data["all_flows_call_graph"]["edges"]
    assert {
        (edge["source"], edge["target"])
        for edge in edges
    } == {
        ("orders", "inventory"),
        ("inventory", "restock"),
        ("inventory", "alerts"),
    }


def test_call_graph_keeps_all_fan_in_and_cycle_arcs() -> None:
    first_producer = replace(_kafka_endpoint("produce", "OrderCreated", "Orders.java"), id="orders-out")
    second_producer = replace(_kafka_endpoint("produce", "OrderCreated", "LegacyOrders.java"), id="legacy-orders-out")
    consumer = replace(_kafka_endpoint("consume", "OrderCreated", "Payments.java"), id="payments-in")
    data = _html_graph_data(render_graph_html(
        {"orders": [first_producer], "legacy": [second_producer], "payments": [consumer]},
        [
            GraphEdge("kafka", "orders", "payments", first_producer, consumer),
            GraphEdge("kafka", "legacy", "payments", second_producer, consumer),
        ],
        code_flows=[CodeFlow(
            id="fan-in-flow", module="payments", method="PaymentHandler.handle",
            path="Payments.java", start_line=1, end_line=1,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "message_entry", "orders.created", "Payments.java", 1, 1, consumer.id),),
        ),
        CodeFlow(
            id="orders-flow", module="orders", method="Orders.publish",
            path="Orders.java", start_line=1, end_line=1,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "cron_entry", "cron", "Orders.java", 1, 1),
                   CodeFlowStep(2, "message_publish", "orders.created", "Orders.java", 2, 2, first_producer.id)),
        ),
        CodeFlow(
            id="legacy-flow", module="legacy", method="LegacyOrders.publish",
            path="LegacyOrders.java", start_line=1, end_line=1,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "cron_entry", "cron", "LegacyOrders.java", 1, 1),
                   CodeFlowStep(2, "message_publish", "orders.created", "LegacyOrders.java", 2, 2, second_producer.id)),
        )],
    ))
    flow_graph = data["all_flows_call_graph"]
    assert set(flow_graph["nodes"]) == {"orders", "legacy", "payments"}
    assert {
        (edge["source"], edge["target"])
        for edge in flow_graph["edges"]
    } == {("orders", "payments"), ("legacy", "payments")}
    assert flow_graph["triggers"] == {
        "payments": [{
            "flow_id": "fan-in-flow",
            "kind": "message_entry",
            "name": "orders.created",
            "endpoint_id": "payments-in",
        }],
        "orders": [{
            "flow_id": "orders-flow",
            "kind": "cron_entry",
            "name": "cron",
            "endpoint_id": None,
        }],
        "legacy": [{
            "flow_id": "legacy-flow",
            "kind": "cron_entry",
            "name": "cron",
            "endpoint_id": None,
        }],
    }

    cycle_a = replace(_kafka_endpoint("produce", "OrderCreated", "A.java"), id="a-out")
    cycle_b = replace(_kafka_endpoint("consume", "OrderCreated", "B.java"), id="b-in")
    cycle_data = _html_graph_data(render_graph_html(
        {"a": [cycle_a], "b": [cycle_b]},
        [
            GraphEdge("kafka", "a", "b", cycle_a, cycle_b),
            GraphEdge("kafka", "b", "a", cycle_b, cycle_a),
        ],
        code_flows=[CodeFlow(
            id="cycle-flow", module="a", method="A.publish",
            path="A.java", start_line=1, end_line=1,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "message_publish", "orders.created", "A.java", 1, 1, cycle_a.id),),
        )],
    ))
    cycle_graph = _flow_call_graph(cycle_data, cycle_data["code_flows"][0])
    assert set(cycle_graph["nodes"]) == {"a", "b"}
    assert {
        (edge["source"], edge["target"])
        for edge in cycle_graph["edges"]
    } == {("a", "b")}
def test_graph_keeps_unmatched_and_dynamic_kafka_evidence() -> None:
    producer = replace(_kafka_endpoint("produce", "OrderCreated", "Publisher.java"), id="orders-out")
    dynamic_consumer = replace(
        _kafka_endpoint("consume", "OrderCreated", "Consumer.java"),
        id="unknown-in",
        topic="<dynamic>",
        topic_dynamic=True,
        message_type=None,
    )

    data = _html_graph_data(render_graph_html(
        {"orders": [producer], "payments": [dynamic_consumer]}, []
    ))

    assert any(node["id"] == "kafka_topic:orders.created" for node in data["nodes"])
    dynamic = next(node for node in data["nodes"] if node.get("unresolved"))
    assert dynamic["kind"] == "kafka_topic"
    assert len(data["links"]) == 2
    assert all(link["unresolved"] for link in data["links"])
    assert any(link["message_type_status"] == "unknown" for link in data["links"])
    assert any("Aucun endpoint opposé" in link["message_type_warning"] for link in data["links"])


def test_graph_groups_identical_dynamic_topic_evidence_without_pairing_it() -> None:
    first = replace(
        _kafka_endpoint("produce", "OrderCreated", "Publisher.java"),
        id="dynamic-first",
        topic="<dynamic>",
        topic_dynamic=True,
    )
    second = replace(
        _kafka_endpoint("produce", "OrderCreated", "RetryPublisher.java"),
        id="dynamic-second",
        topic="<dynamic>",
        topic_dynamic=True,
    )

    data = _html_graph_data(render_graph_html({"orders": [first, second]}, []))

    dynamic_nodes = [node for node in data["nodes"] if node.get("unresolved")]
    assert len(dynamic_nodes) == 1
    assert dynamic_nodes[0]["name"] == "<dynamic>"
    assert dynamic_nodes[0]["label"] == "<dynamic>"
    assert "Topic dynamique" not in dynamic_nodes[0]["name"]
    assert dynamic_nodes[0]["endpoint_ids"] == ["dynamic-first", "dynamic-second"]
    assert len(data["links"]) == 2
    assert all(link["unresolved"] for link in data["links"])


def test_graph_merges_dynamic_topic_with_matching_static_topic_node() -> None:
    static = replace(
        _kafka_endpoint("produce", "OrderCreated", "Publisher.java"),
        topic="orders-created",
    )
    dynamic = replace(
        _kafka_endpoint("consume", "OrderCreated", "Consumer.java"),
        id="dynamic-consumer",
        topic="<dynamic>",
        topic_display="Orders_Created",
        topic_dynamic=True,
    )

    data = _html_graph_data(render_graph_html({"orders": [static], "payments": [dynamic]}, []))
    topic_nodes = [node for node in data["nodes"] if node["kind"] == "kafka_topic"]

    assert [node["id"] for node in topic_nodes] == ["kafka_topic:orders-created"]
    assert topic_nodes[0]["dynamic_endpoint_ids"] == ["dynamic-consumer"]
    dynamic_link = next(
        link for link in data["links"] if link.get("endpoint_ids") == ["dynamic-consumer"]
    )
    assert dynamic_link["source"] == "kafka_topic:orders-created"
    assert dynamic_link["target"] == "microservice:payments"


def test_graph_displays_source_topic_label_without_changing_topic_identity() -> None:
    producer = replace(
        _kafka_endpoint("produce", "OrderCreated", "Publisher.java"),
        topic="orders-created",
        topic_display="Orders_Created",
    )

    data = _html_graph_data(render_graph_html({"orders": [producer]}, []))

    topic = next(node for node in data["nodes"] if node["kind"] == "kafka_topic")
    assert topic["id"] == "kafka_topic:orders-created"
    assert topic["name"] == "Orders_Created"
    assert data["nodes"]


def test_microservice_widget_shows_only_internal_flows_and_marks_service() -> None:
    endpoint = _rest_endpoint("serve", "POST /orders", "OrderController.java")
    endpoint = replace(endpoint, id="receive-order", qualified_name="com.example.OrderController")
    document = render_graph_html(
        {"orders": [endpoint]}, [],
        code_flows=[CodeFlow(
            id="flow", module="orders", method="com.example.OrderController.placeOrder",
            path="OrderController.java", start_line=12, end_line=22,
            steps=(CodeFlowStep(
                order=1, kind="http_entry", name="POST /orders",
                path="OrderController.java", start_line=12, end_line=12,
                endpoint_id=endpoint.id,
            ),),
            status="potential", confidence="medium", reason="test",
        )],
        integration_methods=[IntegrationMethod(
            id="method", module="orders",
            qualified_method="com.example.OrderController.placeOrder",
            path="OrderController.java", start_line=12, end_line=22,
            input_endpoint_ids=(endpoint.id,), output_endpoint_ids=(),
        )],
    )

    data = _html_graph_data(document)
    node = next(item for item in data["nodes"] if item["id"] == "microservice:orders")
    assert node["ports"] == [{
        "label": "I1", "direction": "in", "type": "HTTP receive",
        "system": "rest", "role": "serve", "path": "OrderController.java", "line": 1,
        "method": "com.example.OrderController::placeOrder", "name": "POST /orders",
        "endpoint_id": "receive-order",
    }]
    assert data["code_flows"][0]["steps"][0]["port_label"] == "I1"
    assert node["internal_flow_count"] == 1
def test_graph_uses_persisted_mongodb_relation_evidence_when_available() -> None:
    document = render_graph_html(
        {"inventory": []}, [], {"inventory": ["orders"]},
        architecture_relations=[ArchitectureRelation(
            id="mongo-write", source_kind="microservice", source_name="inventory",
            relation="writes", target_kind="collection", target_name="orders",
            origin="code", confidence="high", path="src/InventoryPersistence.java", start_line=12,
        )],
    )

    links = _html_graph_data(document)["links"]

    assert {
        "source": "microservice:inventory", "target": "mongodb_collection:inventory:orders",
        "kind": "mongodb", "direction": "data_access", "label": "écrit",
        "confidence": "proved", "provenance": "code",
    } in links
    assert not any(link["provenance"] == "module inventory" for link in links)


def test_microservice_graph_exposes_software_layers_and_namespaces() -> None:
    module = DiscoveredModule(
        name="domain-orders",
        path=Path("/workspace/domain-orders"),
        build_system="maven",
        version=None,
        kind="library",
        starts_application=False,
        configuration_example="",
        kubernetes_workloads=(KubernetesWorkload(
            kind="Deployment",
            namespace="orders-prod",
            name="domain-orders",
            replicas=2,
            cpu_request_millicores=None,
            memory_request_bytes=None,
            cpu_limit_millicores=None,
            memory_limit_bytes=None,
        ),),
    )
    fact = GraphFact(
        id="fact-1",
        fact_type="node",
        kind="microservice",
        name="domain-orders",
        source_kind=None,
        source_name=None,
        target_kind=None,
        target_name=None,
        relation=None,
        origin="ai",
        confidence="medium",
        namespace="ai-boundaries",
    )

    document = render_graph_html(
        {"domain-orders": []}, [], modules_by_service={"domain-orders": module},
        graph_facts=[fact],
        strategy1=True,
    )
    graph_data = _html_graph_data(document)
    node = next(item for item in graph_data["nodes"] if item["name"] == "domain-orders")
    assert node["layer"] == "domain"
    assert node["runtime_namespaces"] == ["orders-prod"]
    assert node["fact_namespaces"] == ["ai-boundaries"]
    assert node["project_namespace"] == "workspace"
    assert "domain" in graph_data["software_layers"]
    assert graph_data["runtime_namespaces"] == ["orders-prod"]
    assert graph_data["fact_namespaces"] == ["ai-boundaries"]


def test_microservice_graph_exposes_jpa_entities_as_owned_nodes() -> None:
    module = DiscoveredModule(
        name="orders", path=Path("/workspace/orders"), build_system="maven",
        version=None, kind="application", starts_application=True,
        configuration_example="", jpa_entities=(JpaEntity(
            qualified_name="com.example.Order",
            path="src/main/java/com/example/Order.java", line=12,
            fields=(JpaField("id", "UUID"), JpaField("status", "String")),
        ),), jpa_dtos=(JpaDto(
            qualified_name="com.example.OrderDto",
            path="src/main/java/com/example/OrderDto.java", line=8,
            roles=("jpa_entity",), entities=("com.example.Order",),
        ),),
    )
    graph_data = _html_graph_data(render_graph_html(
        {"orders": []}, [], modules_by_service={"orders": module},
    ))
    entity = next(node for node in graph_data["nodes"] if node["kind"] == "jpa_entity")
    assert entity["name"] == "com.example.Order"
    assert entity["display_name"] == "Order"
    assert entity["owner"] == "orders"
    assert entity["technology"] == "JPA"
    assert entity["fields"] == [
        {"name": "id", "type": "UUID"},
        {"name": "status", "type": "String"},
    ]
    assert entity["jpa_dtos"] == [{
        "name": "com.example.OrderDto",
        "display_name": "OrderDto",
        "location": "src/main/java/com/example/OrderDto.java:8",
        "roles": ["jpa_entity"],
    }]
    resource = next(item for item in graph_data["resource_descriptions"] if item["id"] == entity["id"])
    assert resource["kind"] == "jpa_entity"
    assert resource["technology"] == "JPA"
    assert resource["attributes"] == entity["fields"]
    assert resource["navigation"]["inspect"] == entity["id"]
    rendered = render_graph_html(
        {"orders": []}, [], modules_by_service={"orders": module},
    )
    assert "openJpaEntityInspector" in rendered
    assert "Entité JPA · ${entity.display_name || entity.name}" in rendered
    assert 'appendActionList("Entités JPA déclarées"' in rendered
    assert 'appendActionList("Classe Java"' in rendered
    assert any(
        link["source"] == "microservice:orders"
        and link["target"] == entity["id"]
        and link["label"] == "maps"
        for link in graph_data["links"]
    )


def test_graph_fact_topic_reuses_the_canonical_kafka_topic_node() -> None:
    producer = replace(_kafka_endpoint("produce", "OrderCreated", "Orders.java"), id="orders-out")
    consumer = replace(_kafka_endpoint("consume", "OrderCreated", "Payments.java"), id="payments-in")
    fact = GraphFact(
        id="documented-topic",
        fact_type="node",
        kind="topic",
        name="orders.created",
        source_kind=None,
        source_name=None,
        target_kind=None,
        target_name=None,
        relation=None,
        origin="ai",
        confidence="high",
        namespace="ai-architecture",
    )

    graph_data = _html_graph_data(render_graph_html(
        {"orders": [producer], "payments": [consumer]},
        [GraphEdge("kafka", "orders", "payments", producer, consumer)],
        graph_facts=[fact],
    ))

    topic_nodes = [node for node in graph_data["nodes"] if node["name"] == "orders.created"]
    assert len(topic_nodes) == 1
    assert topic_nodes[0]["id"] == "kafka_topic:orders.created"
    assert topic_nodes[0]["kind"] == "kafka_topic"


def test_export_builds_one_call_graph_from_all_flows() -> None:
    producer = replace(_kafka_endpoint("produce", "OrderCreated", "Orders.java"), id="orders-out")
    first_consumer = replace(_kafka_endpoint("consume", "OrderCreated", "Payments.java"), id="payments-in")
    second_consumer = replace(_kafka_endpoint("consume", "OrderCreated", "Inventory.java"), id="inventory-in")
    flows = [
        CodeFlow(
            id="orders-flow", module="orders", method="Orders.publish",
            path="Orders.java", start_line=1, end_line=1,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "cron_entry", "cron", "Orders.java", 1, 1),
                   CodeFlowStep(2, "message_publish", "orders.created", "Orders.java", 2, 2, producer.id)),
        ),
        CodeFlow(
            id="payments-flow", module="payments", method="Payments.consume",
            path="Payments.java", start_line=1, end_line=1,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "message_entry", "orders.created", "Payments.java", 1, 1, first_consumer.id),),
        ),
        CodeFlow(
            id="inventory-flow", module="inventory", method="Inventory.consume",
            path="Inventory.java", start_line=1, end_line=1,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "message_entry", "orders.created", "Inventory.java", 1, 1, second_consumer.id),),
        ),
    ]
    data = _html_graph_data(render_graph_html(
        {"orders": [producer], "payments": [first_consumer], "inventory": [second_consumer]},
        [
            GraphEdge("kafka", "orders", "payments", producer, first_consumer),
            GraphEdge("kafka", "orders", "payments", producer, first_consumer),
            GraphEdge("kafka", "orders", "inventory", producer, second_consumer),
        ],
        code_flows=flows,
    ))

    graph = data["all_flows_call_graph"]
    assert set(graph["nodes"]) == {"orders", "payments", "inventory"}
    assert {
        (edge["source"], edge["target"])
        for edge in graph["edges"]
    } == {("orders", "payments"), ("orders", "inventory")}
    assert len(graph["edges"]) == 2
    assert graph["traversal_levels"] == [["orders"], ["inventory", "payments"]]
    assert graph["call_tree"]["edges"] == [
        {"source": "orders", "target": "inventory", "order": 1},
        {"source": "orders", "target": "payments", "order": 2},
    ]
    selected_flow = next(flow for flow in data["code_flows"] if flow["id"] == "orders-flow")
    selected_graph = _flow_call_graph(data, selected_flow)
    selected_tree = selected_graph["call_trees_by_flow"][selected_flow["id"]]
    assert selected_tree["root_flow_ids"] == [selected_flow["id"]]
    assert set(selected_tree["transitions"]) == {selected_flow["id"], "payments-flow", "inventory-flow"}
    assert [edge["order"] for edge in graph["edges"]] == [1, 2]
    for flow in data["code_flows"]:
        orders = [edge["order"] for edge in _flow_call_graph(data, flow)["edges"]]
        if orders:
            assert min(orders) == 1
    assert set(graph["triggers"]) == {"orders", "payments", "inventory"}

    indexed_graph = _networkx_call_graph(
        flows,
        {"orders": [producer], "payments": [first_consumer], "inventory": [second_consumer]},
        [
            GraphEdge("kafka", "orders", "payments", producer, first_consumer),
            GraphEdge("kafka", "orders", "payments", producer, first_consumer),
            GraphEdge("kafka", "orders", "inventory", producer, second_consumer),
        ],
        root_flow_ids={"orders-flow"},
        include_occurrences=False,
    )
    order_transitions = indexed_graph["call_tree"]["transitions"]["orders-flow"]
    assert len(order_transitions) == 2
    assert {
        (transition["edge"]["target"], transition["edge"]["label"])
        for transition in order_transitions
    } == {("payments", "orders.created"), ("inventory", "orders.created")}


def test_call_tree_follows_causal_flow_occurrences_and_unknown_leaves() -> None:
    orders_out = replace(_kafka_endpoint("produce", "OrderCreated", "Orders.java"), id="orders-out")
    payments_in = replace(_kafka_endpoint("consume", "OrderCreated", "Payments.java"), id="payments-in")
    payments_out = replace(_kafka_endpoint("produce", "PaymentSettled", "Payments.java"), id="payments-out")
    settlement_in = replace(_kafka_endpoint("consume", "PaymentSettled", "Settlement.java"), id="settlement-in")
    flows = [
        CodeFlow(
            id="orders-flow-one", module="orders", method="Orders.one",
            path="Orders.java", start_line=1, end_line=2,
            status="potential", confidence="medium", reason="test",
            steps=(
                CodeFlowStep(1, "cron_entry", "cron-one", "Orders.java", 1, 1),
                CodeFlowStep(2, "message_publish", "orders.created", "Orders.java", 2, 2, orders_out.id),
            ),
        ),
        CodeFlow(
            id="orders-flow-two", module="orders", method="Orders.two",
            path="Orders.java", start_line=4, end_line=5,
            status="potential", confidence="medium", reason="test",
            steps=(
                CodeFlowStep(1, "cron_entry", "cron-two", "Orders.java", 4, 4),
                CodeFlowStep(2, "message_publish", "orders.created", "Orders.java", 5, 5, orders_out.id),
            ),
        ),
        CodeFlow(
            id="payments-flow", module="payments", method="Payments.consume",
            path="Payments.java", start_line=1, end_line=2,
            status="potential", confidence="medium", reason="test",
            steps=(
                CodeFlowStep(1, "message_entry", "orders.created", "Payments.java", 1, 1, payments_in.id),
                CodeFlowStep(2, "message_publish", "payments.settled", "Payments.java", 2, 2, payments_out.id),
            ),
        ),
        CodeFlow(
            id="settlement-flow", module="settlement", method="Settlement.consume",
            path="Settlement.java", start_line=1, end_line=1,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "message_entry", "payments.settled", "Settlement.java", 1, 1, settlement_in.id),),
        ),
    ]
    endpoints = {
        "orders": [replace(orders_out, module=None)],
        "payments": [replace(payments_in, module=None), replace(payments_out, module=None)],
        "settlement": [replace(settlement_in, module=None)],
    }
    edges = [
        GraphEdge("kafka", "orders", "payments", orders_out, payments_in),
        GraphEdge("kafka", "payments", "settlement", payments_out, settlement_in),
    ]
    data = _html_graph_data(render_graph_html(endpoints, edges, code_flows=flows))
    first_flow = next(flow for flow in data["code_flows"] if flow["id"] == "orders-flow-one")
    second_flow = next(flow for flow in data["code_flows"] if flow["id"] == "orders-flow-two")
    first_graph = _networkx_call_graph(
        flows, endpoints, edges, root_flow_ids={first_flow["id"]}, include_occurrences=True,
    )
    second_graph = _networkx_call_graph(
        flows, endpoints, edges, root_flow_ids={second_flow["id"]}, include_occurrences=True,
    )
    first_tree = first_graph["call_tree"]
    second_tree = second_graph["call_tree"]
    first_root_id = first_tree["root_occurrence_ids"][0]
    second_root_id = second_tree["root_occurrence_ids"][0]
    first_root_occurrence = next(
        occurrence for occurrence in first_tree["occurrences"] if occurrence["id"] == first_root_id
    )
    second_root_occurrence = next(
        occurrence for occurrence in second_tree["occurrences"] if occurrence["id"] == second_root_id
    )
    assert first_root_occurrence["flow_id"] == "orders-flow-one"
    assert second_root_occurrence["flow_id"] == "orders-flow-two"
    first_by_id = {occurrence["id"]: occurrence for occurrence in first_tree["occurrences"]}
    first_root = first_by_id[first_tree["root_occurrence_ids"][0]]
    first_children = [first_by_id[child_id] for child_id in first_root["children"]]
    assert [child["name"] for child in first_children] == ["payments"]
    payments = first_children[0]
    settlement = first_by_id[payments["children"][0]]
    assert settlement["name"] == "settlement"
    assert settlement["cycle"] is False
    assert settlement["edge"]["source"] == "payments"
    assert settlement["edge"]["target"] == "settlement"

    unknown_data = _html_graph_data(render_graph_html(
        {"orders": endpoints["orders"], "payments": endpoints["payments"][:1]},
        edges[:1],
        code_flows=flows[:2],
    ))
    unknown_flow = unknown_data["code_flows"][0]
    unknown_tree = _networkx_call_graph(
        flows[:2],
        {"orders": endpoints["orders"], "payments": endpoints["payments"][:1]},
        edges[:1],
        root_flow_ids={unknown_flow["id"]},
        include_occurrences=True,
    )["call_tree"]
    unknown_by_id = {occurrence["id"]: occurrence for occurrence in unknown_tree["occurrences"]}
    unknown_root = unknown_by_id[unknown_tree["root_occurrence_ids"][0]]
    unknown_child = unknown_by_id[unknown_root["children"][0]]
    assert unknown_child["name"] == "payments"
    assert unknown_child["continuation_unknown"] is True
    assert unknown_child["cycle"] is False


def test_call_graph_follows_exact_target_endpoints_for_downstream_flows() -> None:
    orders_payments = replace(_kafka_endpoint("produce", "payments", "Orders.java"), id="orders-payments", topic="payments")
    orders_inventory = replace(_kafka_endpoint("produce", "inventory", "Orders.java"), id="orders-inventory", topic="inventory")
    payments_in = replace(_kafka_endpoint("consume", "payments", "Payments.java"), id="payments-in", topic="payments")
    inventory_in = replace(_kafka_endpoint("consume", "inventory", "Inventory.java"), id="inventory-in", topic="inventory")
    payments_out = replace(_kafka_endpoint("produce", "settled", "Payments.java"), id="payments-out", topic="settled")
    inventory_out = replace(_kafka_endpoint("produce", "indexed", "Inventory.java"), id="inventory-out", topic="indexed")
    settled_in = replace(_kafka_endpoint("consume", "settled", "Settlement.java"), id="settled-in", topic="settled")
    indexed_in = replace(_kafka_endpoint("consume", "indexed", "Index.java"), id="indexed-in", topic="indexed")
    flows = [
        CodeFlow(
            id="orders-flow", module="orders", method="Orders.publish",
            path="Orders.java", start_line=1, end_line=2,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "cron_entry", "cron", "Orders.java", 1, 1),
                   CodeFlowStep(2, "message_publish", "payments", "Orders.java", 2, 2, orders_payments.id),
                   CodeFlowStep(3, "message_publish", "inventory", "Orders.java", 3, 3, orders_inventory.id)),
        ),
        CodeFlow(
            id="payments-flow", module="payments", method="Payments.consume",
            path="Payments.java", start_line=1, end_line=2,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "message_entry", "payments", "Payments.java", 1, 1, payments_in.id),
                   CodeFlowStep(2, "message_publish", "settled", "Payments.java", 2, 2, payments_out.id)),
        ),
        CodeFlow(
            id="inventory-flow", module="inventory", method="Inventory.consume",
            path="Inventory.java", start_line=1, end_line=2,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "message_entry", "inventory", "Inventory.java", 1, 1, inventory_in.id),
                   CodeFlowStep(2, "message_publish", "indexed", "Inventory.java", 2, 2, inventory_out.id)),
        ),
        CodeFlow(
            id="settled-flow", module="settlement", method="Settlement.consume",
            path="Settlement.java", start_line=1, end_line=1,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "message_entry", "settled", "Settlement.java", 1, 1, settled_in.id),),
        ),
        CodeFlow(
            id="indexed-flow", module="index", method="Index.consume",
            path="Index.java", start_line=1, end_line=1,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "message_entry", "indexed", "Index.java", 1, 1, indexed_in.id),),
        ),
    ]
    endpoints = {
        "orders": [orders_payments, orders_inventory],
        "payments": [payments_in, payments_out],
        "inventory": [inventory_in, inventory_out],
        "settlement": [settled_in],
        "index": [indexed_in],
    }
    edges = [
        GraphEdge("kafka", "orders", "payments", orders_payments, payments_in),
        GraphEdge("kafka", "orders", "inventory", orders_inventory, inventory_in),
        GraphEdge("kafka", "payments", "settlement", payments_out, settled_in),
        GraphEdge("kafka", "inventory", "index", inventory_out, indexed_in),
    ]
    graph = _html_graph_data(render_graph_html(endpoints, edges, code_flows=flows))["all_flows_call_graph"]
    assert {
        (edge["source"], edge["target"])
        for edge in graph["edges"]
    } == {
        ("orders", "payments"), ("orders", "inventory"),
        ("payments", "settlement"), ("inventory", "index"),
    }
    reordered_graph = _html_graph_data(render_graph_html(
        dict(reversed(list(endpoints.items()))),
        list(reversed(edges)),
        code_flows=list(reversed(flows)),
    ))["all_flows_call_graph"]
    assert [
        (edge["source"], edge["target"], edge["order"])
        for edge in graph["edges"]
    ] == [
        (edge["source"], edge["target"], edge["order"])
        for edge in reordered_graph["edges"]
    ]


def test_call_graph_does_not_infer_same_topic_consumers_without_an_indexed_arc() -> None:
    producer = replace(_kafka_endpoint("produce", "OrderCreated", "Orders.java"), id="orders-out")
    indexed_consumer = replace(_kafka_endpoint("consume", "OrderCreated", "Payments.java"), id="payments-in")
    unrelated_consumer = replace(_kafka_endpoint("consume", "OrderCreated", "Inventory.java"), id="inventory-in")
    flows = [
        CodeFlow(
            id="orders-flow", module="orders", method="Orders.publish",
            path="Orders.java", start_line=1, end_line=2,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "cron_entry", "cron", "Orders.java", 1, 1),
                   CodeFlowStep(2, "message_publish", "orders.created", "Orders.java", 2, 2, producer.id)),
        ),
        CodeFlow(
            id="payments-flow", module="payments", method="Payments.consume",
            path="Payments.java", start_line=1, end_line=1,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "message_entry", "orders.created", "Payments.java", 1, 1, indexed_consumer.id),),
        ),
        CodeFlow(
            id="inventory-flow", module="inventory", method="Inventory.consume",
            path="Inventory.java", start_line=1, end_line=1,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "message_entry", "orders.created", "Inventory.java", 1, 1, unrelated_consumer.id),),
        ),
    ]
    graph = _html_graph_data(render_graph_html(
        {"orders": [producer], "payments": [indexed_consumer], "inventory": [unrelated_consumer]},
        [GraphEdge("kafka", "orders", "payments", producer, indexed_consumer)],
        code_flows=flows,
    ))["all_flows_call_graph"]
    assert ("orders", "payments") in {
        (edge["source"], edge["target"])
        for edge in graph["edges"]
    }
    assert ("orders", "inventory") not in {
        (edge["source"], edge["target"])
        for edge in graph["edges"]
    }


def test_call_graph_numbers_cycle_arcs_once_in_breadth_first_order() -> None:
    a_in = replace(_kafka_endpoint("consume", "events", "A.java"), id="a-in")
    a_out = replace(_kafka_endpoint("produce", "events", "A.java"), id="a-out")
    b_in = replace(_kafka_endpoint("consume", "events", "B.java"), id="b-in")
    b_out = replace(_kafka_endpoint("produce", "events", "B.java"), id="b-out")
    a_flow = CodeFlow(
        id="a-flow", module="a", method="A.consume", path="A.java",
        start_line=1, end_line=2, status="potential", confidence="medium", reason="test",
        steps=(CodeFlowStep(1, "message_entry", "events", "A.java", 1, 1, a_in.id),
               CodeFlowStep(2, "message_publish", "events", "A.java", 2, 2, a_out.id)),
    )
    b_flow = CodeFlow(
        id="b-flow", module="b", method="B.consume", path="B.java",
        start_line=1, end_line=2, status="potential", confidence="medium", reason="test",
        steps=(CodeFlowStep(1, "message_entry", "events", "B.java", 1, 1, b_in.id),
               CodeFlowStep(2, "message_publish", "events", "B.java", 2, 2, b_out.id)),
    )
    data = _html_graph_data(render_graph_html(
        {"a": [a_in, a_out], "b": [b_in, b_out]},
        [
            GraphEdge("kafka", "a", "b", a_out, b_in),
            GraphEdge("kafka", "b", "a", b_out, a_in),
        ],
        code_flows=[a_flow, b_flow],
    ))
    graph = _flow_call_graph(data, next(flow for flow in data["code_flows"] if flow["id"] == "a-flow"))
    assert [(edge["source"], edge["target"], edge["order"]) for edge in graph["edges"]] == [
        ("a", "b", 1), ("b", "a", 2),
    ]


def test_untriggered_flow_remains_a_root_when_module_has_another_triggered_flow() -> None:
    entry = replace(_kafka_endpoint("consume", "OrderCreated", "Orders.java"), id="orders-in")
    producer = replace(_kafka_endpoint("produce", "PaymentCreated", "Orders.java"), id="orders-out")
    target = replace(_kafka_endpoint("consume", "PaymentCreated", "Payments.java"), id="payments-in")
    data = _html_graph_data(render_graph_html(
        {"orders": [entry, producer], "payments": [target]},
        [GraphEdge("kafka", "orders", "payments", producer, target)],
        code_flows=[
            CodeFlow(
                id="triggered-orders-flow", module="orders", method="Orders.consume",
                path="Orders.java", start_line=1, end_line=1,
                status="potential", confidence="medium", reason="test",
                steps=(CodeFlowStep(1, "message_entry", "orders.created", "Orders.java", 1, 1, entry.id),
                       CodeFlowStep(2, "message_publish", "payment.created", "Orders.java", 2, 2, producer.id)),
            ),
            CodeFlow(
                id="internal-orders-flow", module="orders", method="Orders.publish",
                path="Orders.java", start_line=2, end_line=2,
                status="potential", confidence="medium", reason="test",
                steps=(CodeFlowStep(1, "message_publish", "payment.created", "Orders.java", 2, 2, producer.id),),
            ),
        ],
    ))
    assert {
        (edge["source"], edge["target"])
        for edge in data["all_flows_call_graph"]["edges"]
    } == {("orders", "payments")}


def test_call_graph_follows_only_arcs_outgoing_from_current_module() -> None:
    orders_output = replace(_kafka_endpoint("produce", "OrderCreated", "Orders.java"), id="orders-out")
    payments_input = replace(_kafka_endpoint("consume", "OrderCreated", "Payments.java"), id="payments-in")
    data = _html_graph_data(render_graph_html(
        {"orders": [orders_output], "payments": [payments_input]},
        [
            GraphEdge("kafka", "orders", "payments", orders_output, payments_input),
            # The endpoint direction is deliberately inconsistent with the
            # current module and must not create a reverse traversal.
            GraphEdge("kafka", "payments", "orders", payments_input, orders_output),
        ],
        code_flows=[CodeFlow(
            id="orders-flow", module="orders", method="Orders.publish",
            path="Orders.java", start_line=1, end_line=1,
            status="potential", confidence="medium", reason="test",
            steps=(CodeFlowStep(1, "message_publish", "orders.created", "Orders.java", 1, 1, orders_output.id),),
        )],
    ))
    assert [
        (edge["source"], edge["target"])
        for edge in data["all_flows_call_graph"]["edges"]
    ] == [("orders", "payments")]


def test_graph_html_uses_only_indexed_kafka_dto_facts(tmp_path: Path) -> None:
    source_root = tmp_path / "orders" / "src" / "main" / "java" / "com" / "example" / "events"
    source_root.mkdir(parents=True)
    (source_root / "OrderCreated.java").write_text(
        """package com.example.events;

import java.util.List;

public record OrderCreated(OrderDetails details, List<LineItem> lines) {}
class OrderDetails { Customer customer; }
record LineItem(String sku, Price price, PaymentStatus status) {}
record Customer(String id, Address address) {}
record Price(String currency) {}
record Address(String city) {}
enum PaymentStatus { AUTHORIZED, DECLINED }
""",
        encoding="utf-8",
    )
    module = DiscoveredModule(
        name="orders",
        path=tmp_path / "orders",
        build_system="maven",
        version=None,
        kind="library",
        starts_application=True,
        configuration_example="",
    )
    document = render_graph_html(
        {
            "producer": [_kafka_endpoint("produce", "com.example.events.OrderCreated", "OrderPublisher.java")],
            "consumer": [_kafka_endpoint("consume", "com.example.events.OrderCreated", "OrderConsumer.java")],
        },
        [],
        build_modules=[module],
    )

    graph_data = _html_graph_data(document)
    kafka_dtos = {dto["name"]: dto for dto in graph_data["kafka_dtos"]}
    assert kafka_dtos["OrderCreated"]["producers"] == ["producer"]
    assert kafka_dtos["OrderCreated"]["consumers"] == ["consumer"]
    assert kafka_dtos["OrderCreated"]["topics"] == ["orders.created"]
    assert kafka_dtos["OrderCreated"]["fields"] == []
    assert graph_data["project_dto_definitions"] == []

def test_graph_html_uses_persisted_kafka_dto_source_definitions(tmp_path: Path) -> None:
    module_root = tmp_path / "orders"
    module = DiscoveredModule(
        name="orders", path=module_root, build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    endpoint = _kafka_endpoint("produce", "com.example.OrderCreated", "Publisher.java")
    document = render_graph_html(
        {"orders": [endpoint]}, [], modules_by_service={"orders": module},
        build_modules=[module],
        kafka_dto_definitions=[{
            "id": "com.example.OrderCreated", "name": "OrderCreated",
            "qualified_name": "com.example.OrderCreated", "module": "orders",
            "source": "src/main/java/com/example/OrderCreated.java", "root": True,
            "fields": [{"name": "id", "type": "String"}],
            "producers": ["orders"], "consumers": [], "topics": ["orders.created"],
        }],
    )

    dto = _html_graph_data(document)["kafka_dtos"][0]
    assert dto["source"] == "src/main/java/com/example/OrderCreated.java"
    assert dto["fields"] == [{"name": "id", "type": "String"}]
    assert dto["vscode_uri"].endswith("/src/main/java/com/example/OrderCreated.java")


def test_graph_html_uses_persisted_openapi_specs() -> None:
    module = DiscoveredModule(
        name="orders", path=Path("/workspace/orders"), build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
        openapi_files=("src/main/resources/openapi.yaml",),
    )
    graph_data = _html_graph_data(render_graph_html(
        {"orders": []}, [], modules_by_service={"orders": module},
        openapi_contracts=[{
            "module": "orders", "path": "src/main/resources/openapi.yaml",
            "spec": {"openapi": "3.0.0", "paths": {}},
        }],
    ))

    assert graph_data["nodes"][0]["openapi_contracts"][0]["spec"] == {
        "openapi": "3.0.0", "paths": {}
    }


def test_graph_html_embeds_the_official_asyncapi_component_only_when_needed() -> None:
    module = DiscoveredModule(
        name="orders", path=Path("/workspace/orders"), build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    without_contract = render_graph_html({"orders": []}, [], modules_by_service={"orders": module})
    with_contract = render_graph_html(
        {"orders": []}, [], modules_by_service={"orders": module},
        asyncapi_contracts=[{
            "module": "orders", "path": "src/main/resources/orders.asyncapi.yaml",
            "spec": {"asyncapi": "3.0.0", "info": {"title": "Orders", "version": "1.0.0"}},
        }],
    )

    assert "asyncapi-web-component-3.1.8.js" not in without_contract
    assert '"asyncapi-component"' in with_contract
    assert "data:text/css;base64," in with_contract

def test_graph_html_deduplicates_repository_and_module_relative_openapi_paths() -> None:
    module = DiscoveredModule(
        name="products", path=Path("/workspace/products"), build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
        openapi_files=("src/main/resources/openapi/products.yaml",),
    )
    endpoint = MessageEndpoint(
        id=compute_endpoint_id("serve", "GET /products", "products/src/main/resources/openapi/products.yaml"),
        role="serve", system="rest", topic="GET /products", topic_dynamic=False,
        source="code", framework="openapi",
        path="products/src/main/resources/openapi/products.yaml", start_line=1, end_line=1,
        snippet="",
    )

    graph_data = _html_graph_data(render_graph_html(
        {"products": [endpoint]}, [], modules_by_service={"products": module},
        openapi_contracts=[{
            "module": "products", "path": "src/main/resources/openapi/products.yaml",
            "spec": {"openapi": "3.0.0", "paths": {"/products": {}}},
        }],
    ))

    contracts = graph_data["nodes"][0]["openapi_contracts"]
    assert [contract["path"] for contract in contracts] == ["src/main/resources/openapi/products.yaml"]
    assert contracts[0]["resources"] == ["GET /products"]
    assert contracts[0]["spec"] == {"openapi": "3.0.0", "paths": {"/products": {}}}


def test_graph_html_lists_a_shared_openapi_file_only_for_its_enclosing_module() -> None:
    workspace = DiscoveredModule(
        name="workspace", path=Path("/workspace"), build_system="maven", version=None,
        kind="aggregator", starts_application=False, configuration_example="",
        openapi_files=("swagger.yaml",),
    )
    orders = DiscoveredModule(
        name="orders", path=Path("/workspace/orders"), build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
        openapi_files=("../swagger.yaml",),
    )

    graph_data = _html_graph_data(render_graph_html(
        {"workspace": [], "orders": []}, [],
        modules_by_service={"workspace": workspace, "orders": orders},
        openapi_contracts=[{
            "module": "workspace", "path": "swagger.yaml",
            "spec": {"swagger": "2.0", "paths": {}},
        }],
    ))

    by_name = {node["name"]: node for node in graph_data["nodes"]}
    assert [contract["path"] for contract in by_name["workspace"]["openapi_contracts"]] == ["swagger.yaml"]
    assert by_name["orders"]["openapi_contracts"] == []


def test_graph_html_normalizes_openapi_evidence_for_a_deeply_nested_module() -> None:
    """A module more than one directory level below the repo root must not
    show its own contract twice under two different path strings.

    ``_module_openapi_contract_path`` used to strip only the module
    directory's last path segment, so a module such as
    ``services/orders-api`` (nested two levels deep) never had its
    repository-relative evidence normalized to the module-relative path
    used by ``module.openapi_files`` -- producing a duplicate entry and a
    missing parsed ``spec``.
    """
    module = DiscoveredModule(
        name="orders-api", path=Path("/workspace/services/orders-api"), build_system="maven",
        version=None, kind="application", starts_application=True, configuration_example="",
        openapi_files=("src/main/resources/openapi.yaml",),
    )
    endpoint = MessageEndpoint(
        id=compute_endpoint_id("serve", "GET /orders", "services/orders-api/src/main/resources/openapi.yaml"),
        role="serve", system="rest", topic="GET /orders", topic_dynamic=False,
        source="code", framework="openapi",
        path="services/orders-api/src/main/resources/openapi.yaml", start_line=1, end_line=1,
        snippet="",
    )

    graph_data = _html_graph_data(render_graph_html(
        {"orders-api": [endpoint]}, [], modules_by_service={"orders-api": module},
        openapi_contracts=[{
            "module": "orders-api", "path": "src/main/resources/openapi.yaml",
            "spec": {"openapi": "3.0.0", "paths": {"/orders": {}}},
        }],
    ))

    node = graph_data["nodes"][0]
    assert node["openapi_files"] == ["src/main/resources/openapi.yaml"]
    contracts = node["openapi_contracts"]
    assert [contract["path"] for contract in contracts] == ["src/main/resources/openapi.yaml"]
    assert contracts[0]["spec"] == {"openapi": "3.0.0", "paths": {"/orders": {}}}


def test_graph_html_resolves_strategy1_shared_contract_spec_and_owner() -> None:
    """A Strategy1 declaration can publish a contract that physically lives
    in a different (shared ``model-*``) module than the publishing service.

    Normalizing that evidence against the *publishing* module's own
    directory name produced a bogus path (the shared module's name nested
    under the publisher), which never matched the ``openapi_contracts``
    entry indexed under the *owning* module -- rendering the contract with
    no parsed spec ("contrat non detecte").
    """
    publisher = DiscoveredModule(
        name="microservice-order", path=Path("/workspace/microservice-order"), build_system="maven",
        version=None, kind="application", starts_application=True, configuration_example="",
    )
    shared_model = DiscoveredModule(
        name="model-order-api", path=Path("/workspace/model-order-api"), build_system="maven",
        version=None, kind="library", starts_application=False, configuration_example="",
        openapi_files=("src/main/resources/order.yaml",),
    )
    declaration_path = "microservice-order/src/main/resources/openapi/order.rest"
    endpoint = MessageEndpoint(
        id=compute_endpoint_id("serve", "GET /orders", declaration_path),
        role="serve", system="rest", topic="GET /orders", topic_dynamic=False,
        source="code", framework="openapi",
        path=declaration_path, start_line=1, end_line=1,
        snippet=(
            "Publication OpenAPI declaree par microservice-order/src/main/resources/openapi/order.rest\n"
            "systemlens-openapi-contract:model-order-api/src/main/resources/order.yaml\n"
        ),
    )

    graph_data = _html_graph_data(render_graph_html(
        {"microservice-order": [endpoint]},
        [],
        modules_by_service={"microservice-order": publisher},
        build_modules=[publisher, shared_model],
        openapi_contracts=[{
            "module": "model-order-api", "path": "src/main/resources/order.yaml",
            "spec": {"openapi": "3.0.0", "paths": {"/orders": {}}},
        }],
    ))

    node = graph_data["nodes"][0]
    assert node["openapi_files"] == ["src/main/resources/order.yaml"]
    contracts = node["openapi_contracts"]
    assert [contract["path"] for contract in contracts] == ["src/main/resources/order.yaml"]
    assert contracts[0]["spec"] == {"openapi": "3.0.0", "paths": {"/orders": {}}}


def test_graph_html_does_not_infer_dto_packages_from_live_sources(tmp_path: Path) -> None:
    source_root = tmp_path / "service" / "src" / "main" / "java"
    (source_root / "com" / "acme" / "one").mkdir(parents=True)
    (source_root / "com" / "acme" / "two").mkdir(parents=True)
    (source_root / "com" / "acme" / "publishers").mkdir(parents=True)
    (source_root / "com" / "acme" / "one" / "Event.java").write_text(
        "package com.acme.one; public record Event(String orderId) {}",
        encoding="utf-8",
    )
    (source_root / "com" / "acme" / "two" / "Event.java").write_text(
        "package com.acme.two; public record Event(String customerId) {}",
        encoding="utf-8",
    )
    (source_root / "com" / "acme" / "publishers" / "FirstPublisher.java").write_text(
        "package com.acme.publishers; import com.acme.one.Event; class FirstPublisher {}",
        encoding="utf-8",
    )
    (source_root / "com" / "acme" / "publishers" / "SecondPublisher.java").write_text(
        "package com.acme.publishers; import com.acme.two.Event; class SecondPublisher {}",
        encoding="utf-8",
    )
    module = DiscoveredModule(
        name="service",
        path=tmp_path / "service",
        build_system="maven",
        version=None,
        kind="application",
        starts_application=True,
        configuration_example="",
    )
    first = _kafka_endpoint("produce", "Event", "FirstPublisher.java", "com.acme.publishers.FirstPublisher")
    second = _kafka_endpoint("produce", "Event", "SecondPublisher.java", "com.acme.publishers.SecondPublisher")

    graph_data = _html_graph_data(render_graph_html({"one": [first], "two": [second]}, [], build_modules=[module]))
    definitions = {dto["id"]: dto for dto in graph_data["kafka_dtos"]}

    assert set(definitions) == {"Event"}
    assert definitions["Event"]["fields"] == []
    assert definitions["Event"]["producers"] == ["one", "two"]


def test_graph_html_links_an_indexing_issue_to_its_source_file() -> None:
    endpoint = replace(_kafka_endpoint("consume", "Event", "Listener.java"), topic_dynamic=True)

    graph_data = _html_graph_data(render_graph_html({"orders": [endpoint]}, []))
    issue = graph_data["indexing_issues"][0]

    assert issue["location"] == "Listener.java:1"
    assert issue["vscode_uri"].startswith("vscode://file/")


def test_graph_html_reports_an_ambiguous_explicit_http_target() -> None:
    call = replace(_rest_endpoint("call", "GET /orders", "Client.java"), snippet="http://orders")
    orders = _rest_endpoint("serve", "GET /orders", "OrdersController.java")
    alternate = replace(
        _rest_endpoint("serve", "GET /orders", "AlternateController.java"),
        id="alternate",
    )

    graph_data = _html_graph_data(
        render_graph_html(
            {"caller": [call], "orders": [orders], "ORDERS": [alternate]}, []
        )
    )

    issue = graph_data["indexing_issues"][0]
    assert issue["severity"] == "warning"
    assert issue["category"] == "Cible HTTP ambiguë"
    assert issue["message"] == (
        "caller : la cible explicite 'orders' correspond à plusieurs microservices."
    )
    assert issue["location"] == "Client.java:1"
    assert issue["vscode_uri"].endswith("/Client.java:1")


def test_graph_html_colours_topics_and_mongodb_collections_by_connectivity() -> None:
    producer = _kafka_endpoint("produce", "OrderCreated", "Publisher.java")
    consumer = _kafka_endpoint("consume", "OrderCreated", "Consumer.java")
    orders_module = DiscoveredModule(
        name="orders", path=Path("/workspace/orders"), build_system="maven",
        version=None, kind="application", starts_application=True,
        configuration_example="", mongo_collections=("orders",),
        mongo_persistence_classes=(MongoPersistenceClass(
            collection="orders", name="Order", qualified_name="com.example.Order",
            path="src/main/java/com/example/Order.java", line=7,
            fields=(MongoField("id", "String"),),
        ),),
    )

    graph_document = render_graph_html(
        {"orders": [producer], "payments": [consumer]},
        [GraphEdge("kafka", "orders", "payments", producer, consumer)],
        collections_by_service={"orders": ["orders"]},
        modules_by_service={"orders": orders_module},
    )
    graph_data = _html_graph_data(graph_document)
    nodes = {node["id"]: node for node in graph_data["nodes"]}

    service = nodes["microservice:orders"]
    topic = nodes["kafka_topic:orders.created"]
    collection = nodes["mongodb_collection:orders:orders"]
    assert service["build_system"] == "maven"
    assert service["vscode_uri"] == "vscode://file//workspace/orders"
    assert "Ouvrir le projet ${buildSystem} dans VS Code" in graph_document
    assert topic["complexity"] == {
        "score": 2,
        "level": "low",
        "relations": 2,
        "breakdown": {"http": 0, "kafka": 2, "mongodb": 0},
        "rank": 1,
        "population": 1,
        "tier_start": 1,
        "tier_end": 1,
    }
    assert topic["color"] == "#2563eb"
    assert topic["size"] == 50
    assert topic["label"] == "orders.created"
    assert collection["complexity"] == {
        "score": 1,
        "level": "low",
        "relations": 1,
        "breakdown": {"http": 0, "kafka": 0, "mongodb": 1},
        "rank": 1,
        "population": 1,
        "tier_start": 1,
        "tier_end": 1,
    }
    assert collection["color"] == "#2563eb"
    assert collection["label"] == "orders"
    assert collection["persistence_classes"][0]["qualified_name"] == "com.example.Order"
    assert graph_data["mongo_persistence_classes"][0]["fields"] == [
        {"name": "id", "type": "String", "references": []}
    ]


def test_graph_html_uses_root_path_for_module_directories(tmp_path: Path) -> None:
    module_root = tmp_path / "orders"
    module_root.mkdir()
    source = module_root / "Publisher.java"
    source.write_text("class Publisher {}", encoding="utf-8")
    module = DiscoveredModule(
        name="orders", path=module_root, build_system="gradle", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    endpoint = _kafka_endpoint("produce", "OrderCreated", "Publisher.java")
    document = render_graph_html(
        {"orders": [endpoint]}, [], modules_by_service={"orders": module},
        build_modules=[module], source_roots=[tmp_path], root_path=Path("/exported/repository"),
    )

    service = next(
        node for node in _html_graph_data(document)["nodes"]
        if node["id"] == "microservice:orders"
    )
    export_root = "vscode://file//exported/repository"
    assert service["vscode_uri"] == f"{export_root}/orders"
    assert service["kafka_endpoints"][0]["vscode_uri"] == (
        f"{export_root}/orders/Publisher.java:1"
    )


def test_vscode_uri_joins_root_path_with_indexed_relative_path(tmp_path: Path) -> None:
    source = tmp_path / "orders" / "src" / "Order.java"
    assert _vscode_file_uri(
        source, Path("/exported/repository"), [tmp_path], 14
    ) == "vscode://file//exported/repository/orders/src/Order.java:14"


def test_graph_html_resolves_mongo_class_from_dependent_persistence_module() -> None:
    application = DiscoveredModule(
        name="orders-app", path=Path("/workspace/orders-app"), build_system="maven",
        version=None, kind="application", starts_application=True,
        configuration_example="", mongo_collections=("orders",),
    )
    model = DiscoveredModule(
        name="orders-model", path=Path("/workspace/orders-model"), build_system="maven",
        version=None, kind="library", starts_application=False,
        configuration_example="", mongo_persistence_classes=(MongoPersistenceClass(
            collection="orders", name="Order", qualified_name="com.example.orders.Order",
            path="src/main/java/com/example/orders/Order.java", line=5,
        ),),
    )
    unrelated = DiscoveredModule(
        name="legacy-model", path=Path("/workspace/legacy-model"), build_system="maven",
        version=None, kind="library", starts_application=False,
        configuration_example="", mongo_persistence_classes=(MongoPersistenceClass(
            collection="orders", name="LegacyOrder", qualified_name="legacy.LegacyOrder",
            path="src/main/java/legacy/LegacyOrder.java", line=3,
        ),),
    )

    graph_data = _html_graph_data(render_graph_html(
        {"orders-app": []}, [],
        collections_by_service={"orders-app": ["orders"]},
        modules_by_service={"orders-app": application},
        build_modules=[application, model, unrelated],
        module_dependencies=[ModuleDependency("orders-app", "orders-model")],
    ))

    persistence_classes = graph_data["mongo_persistence_classes"]
    assert [item["qualified_name"] for item in persistence_classes] == [
        "com.example.orders.Order"
    ]
    assert persistence_classes[0]["module"] == "orders-model"
    collection = next(node for node in graph_data["nodes"] if node["kind"] == "mongodb_collection")
    assert collection["persistence_classes"] == persistence_classes


def test_graph_html_lists_document_class_from_persisted_maven_module(tmp_path: Path) -> None:
    module_root = tmp_path / "orders"
    (module_root / "pom.xml").parent.mkdir(parents=True, exist_ok=True)
    (module_root / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion>"
        "<groupId>com.example</groupId><artifactId>orders</artifactId>"
        "<version>1.0.0</version></project>"
    )
    source_root = module_root / "src" / "main" / "java" / "com" / "example"
    source_root.mkdir(parents=True)
    (source_root / "OrdersApplication.java").write_text(
        "package com.example; class OrdersApplication { public static void main(String[] args) { SpringApplication.run(OrdersApplication.class, args); } }"
    )
    (source_root / "Order.java").write_text(
        "package com.example; import org.springframework.data.mongodb.core.mapping.Document; "
        "@Document(collection = \"orders\") class Order { String id; Address address; } "
        "record Address(String city) {}"
    )

    with Store(tmp_path) as store:
        store.replace_modules(discover_modules(tmp_path))
        persisted_module = store.all_modules()[0]

    graph_data = _html_graph_data(render_graph_html(
        {"orders": []}, [],
        collections_by_service={"orders": ["orders"]},
        modules_by_service={"orders": persisted_module},
        build_modules=[persisted_module],
    ))

    definitions = {
        item["qualified_name"]: item
        for item in graph_data["mongo_persistence_classes"]
    }
    order = definitions["com.example.Order"]
    address = definitions["com.example.Address"]
    assert order["root"] is True
    assert address["root"] is False
    assert order["fields"] == [
        {"name": "id", "type": "String", "references": []},
        {
            "name": "address",
            "type": "Address",
            "references": [address["id"]],
        },
    ]
    assert address["fields"] == [
        {"name": "city", "type": "String", "references": []}
    ]
    collection = next(
        node for node in graph_data["nodes"]
        if node["kind"] == "mongodb_collection"
    )
    assert [item["qualified_name"] for item in collection["persistence_classes"]] == [
        "com.example.Order"
    ]


def test_graph_html_microservice_complexity_counts_distinct_direct_clients() -> None:
    producer = _kafka_endpoint("produce", "OrderCreated", "Publisher.java")
    consumer = _kafka_endpoint("consume", "OrderCreated", "Consumer.java")
    first_call = _rest_endpoint("call", "GET /payments", "PaymentClient.java")
    first_serve = _rest_endpoint("serve", "GET /payments", "PaymentController.java")
    second_call = _rest_endpoint("call", "POST /payments", "PaymentClient.java")
    second_serve = _rest_endpoint("serve", "POST /payments", "PaymentController.java")

    graph_data = _html_graph_data(render_graph_html(
        {"orders": [producer, first_call, second_call], "payments": [consumer, first_serve, second_serve]},
        [
            GraphEdge("kafka", "orders", "payments", producer, consumer),
            GraphEdge("rest", "orders", "payments", first_call, first_serve),
            GraphEdge("rest", "orders", "payments", second_call, second_serve),
        ],
        collections_by_service={"orders": ["orders"]},
    ))
    nodes = {node["id"]: node for node in graph_data["nodes"]}

    # orders -> payments is one HTTP client relation despite two called routes.
    assert nodes["microservice:orders"]["complexity"]["score"] == 3
    assert nodes["microservice:orders"]["label"] == "orders"
    assert nodes["microservice:orders"]["complexity"]["breakdown"] == {
        "http": 1, "kafka": 1, "mongodb": 1
    }
    assert nodes["microservice:payments"]["complexity"]["score"] == 2
    assert nodes["microservice:payments"]["complexity"]["breakdown"] == {
        "http": 1, "kafka": 1, "mongodb": 0
    }


def test_architecture_graph_aggregates_http_routes_and_keeps_service_route_lists() -> None:
    first_call = _rest_endpoint("call", "GET /orders", "Client.java")
    first_serve = _rest_endpoint("serve", "GET /orders", "Controller.java")
    second_call = _rest_endpoint("call", "POST /orders", "Client.java")
    second_serve = _rest_endpoint("serve", "POST /orders", "Controller.java")

    graph_data = _html_graph_data(render_graph_html(
        {"caller": [first_call, second_call], "orders": [first_serve, second_serve]},
        [
            GraphEdge("rest", "caller", "orders", first_call, first_serve),
            GraphEdge("rest", "caller", "orders", second_call, second_serve),
        ],
    ))

    http_links = [link for link in graph_data["links"] if link["kind"] == "rest"]
    assert len(http_links) == 1
    assert http_links[0]["label"] == "HTTP"
    assert set(http_links[0]["endpoint_ids"]) == {first_call.id, second_call.id}
    nodes = {node["name"]: node for node in graph_data["nodes"]}
    assert [(item["service"], item["route"]) for item in nodes["caller"]["http_calls"]] == [
        ("orders", "GET /orders"), ("orders", "POST /orders")
    ]
    assert [(item["service"], item["route"]) for item in nodes["orders"]["http_callers"]] == [
        ("caller", "GET /orders"), ("caller", "POST /orders")
    ]
    assert [(item["role"], item["route"]) for item in nodes["orders"]["http_routes"]] == [
        ("serve", "GET /orders"), ("serve", "POST /orders")
    ]
    assert [(item["role"], item["route"]) for item in nodes["caller"]["http_routes"]] == [
        ("call", "GET /orders"), ("call", "POST /orders")
    ]
    assert "Routes exposées" in render_graph_html(
        {"caller": [first_call, second_call], "orders": [first_serve, second_serve]},
        [
            GraphEdge("rest", "caller", "orders", first_call, first_serve),
            GraphEdge("rest", "caller", "orders", second_call, second_serve),
        ],
    )


def test_graph_view_model_ignores_complexity_links_without_a_projected_node() -> None:
    with patch(
        "systemlens.render.graph_view_model._visual_graph_edges",
        return_value=[(
            "microservice", "orders", "microservice", "model-client",
            "GET /models", "rest",
        )],
    ):
        graph_data = build_graph_view_model({"orders": []}, [])

    nodes = {node["id"]: node for node in graph_data["nodes"]}
    assert nodes["microservice:orders"]["complexity"]["score"] == 0


def test_graph_html_keeps_kafka_topic_in_producer_namespace_cluster() -> None:
    producer = _kafka_endpoint("produce", "OrderCreated", "Publisher.java")
    consumer = _kafka_endpoint("consume", "OrderCreated", "Consumer.java")
    parent = Path("/workspace/PORTAIL")
    producer_module = DiscoveredModule(
        name="orders", path=parent / "orders", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    consumer_module = DiscoveredModule(
        name="payments", path=parent / "payments", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    graph_data = _html_graph_data(render_graph_html(
        {"orders": [producer], "payments": [consumer]},
        [GraphEdge("kafka", "orders", "payments", producer, consumer)],
        modules_by_service={"orders": producer_module, "payments": consumer_module},
        build_modules=[producer_module, consumer_module],
    ))
    topic = next(node for node in graph_data["nodes"] if node["kind"] == "kafka_topic")
    producer_node = next(node for node in graph_data["nodes"] if node["name"] == "orders")
    assert producer_node["project_namespace"] == "PORTAIL"
    assert topic["architecture_namespace"] == producer_node["project_namespace"]
    assert all(topic["id"] not in group["children"] for group in graph_data["groups"])


def test_graph_html_uses_canonical_cluster_path_for_services_and_topics() -> None:
    producer = _kafka_endpoint("produce", "OrderCreated", "Publisher.java")
    consumer = _kafka_endpoint("consume", "OrderCreated", "Consumer.java")
    producer_module = DiscoveredModule(
        name="orders", path=Path("/workspace/cluster1/cluster2/orders"),
        build_system="maven", version=None, kind="application", starts_application=True,
        configuration_example="",
    )
    consumer_module = DiscoveredModule(
        name="payments", path=Path("/workspace/cluster1/cluster3/payments"),
        build_system="maven", version=None, kind="application", starts_application=True,
        configuration_example="",
    )
    graph_data = _html_graph_data(render_graph_html(
        {"orders": [producer], "payments": [consumer]},
        [GraphEdge("kafka", "orders", "payments", producer, consumer)],
        modules_by_service={"orders": producer_module, "payments": consumer_module},
        build_modules=[producer_module, consumer_module], root_path=Path("/workspace"),
    ))
    nodes = {node["name"]: node for node in graph_data["nodes"]}
    topic = next(node for node in graph_data["nodes"] if node["kind"] == "kafka_topic")
    assert nodes["orders"]["cluster_path"] == "cluster1/cluster2"
    assert nodes["payments"]["cluster_path"] == "cluster1/cluster3"
    assert topic["architecture_namespace_path"] == "cluster1/cluster2"


def test_mongodb_resource_owner_uses_lowest_writing_service_layer() -> None:
    writer_domain = DiscoveredModule(
        name="domain-orders", path=Path("/workspace/DOMAIN/domain-orders"),
        build_system="maven", version=None, kind="application", starts_application=True,
        configuration_example="", mongo_collections=("orders",),
        mongo_methods=(MongoMethod("save", "mongoTemplate", "Store.java", 1, "orders"),),
    )
    writer_persistence = DiscoveredModule(
        name="orders-repository", path=Path("/workspace/PERSISTENCE/orders-repository"),
        build_system="maven", version=None, kind="application", starts_application=True,
        configuration_example="", mongo_collections=("orders",),
        mongo_methods=(MongoMethod("save", "mongoTemplate", "Store.java", 1, "orders"),),
    )
    graph_data = _html_graph_data(render_graph_html(
        {"domain-orders": [], "orders-repository": []}, [],
        collections_by_service={"domain-orders": ["orders"], "orders-repository": ["orders"]},
        modules_by_service={
            "domain-orders": writer_domain, "orders-repository": writer_persistence,
        },
        build_modules=[writer_domain, writer_persistence],
        root_path=Path("/workspace"), strategy1=True,
    ))
    collection_nodes = [node for node in graph_data["nodes"] if node["kind"] == "mongodb_collection"]
    assert collection_nodes
    assert {node["owner_service"] for node in collection_nodes} == {"orders-repository"}
