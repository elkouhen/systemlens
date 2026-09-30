from xml.etree import ElementTree

from systemlens.domain.graph import GraphEdge
from systemlens.domain.models import MessageEndpoint
from systemlens.render.graphml_export import render_graphml


def _endpoint(role: str, topic: str, qualified_name: str) -> MessageEndpoint:
    return MessageEndpoint(
        id=f"{role}-{topic}", role=role, system="kafka", topic=topic,
        topic_dynamic=False, source="code", framework="spring", path="App.java",
        start_line=1, end_line=1, snippet="evidence", qualified_name=qualified_name,
    )


def test_graphml_exports_interservice_edges_and_weak_components() -> None:
    edge = GraphEdge(
        "kafka", "orders", "billing", _endpoint("produce", "orders.created", "x.Orders"),
        _endpoint("consume", "orders.created", "x.Billing"),
    )

    document = ElementTree.fromstring(render_graphml(["orders", "billing", "isolated"], [edge]))
    namespace = {"g": "http://graphml.graphdrawing.org/xmlns"}
    nodes = document.findall("g:graph/g:node", namespace)
    edges = document.findall("g:graph/g:edge", namespace)
    values = {
        node.attrib["label"]: {
            item.attrib["key"]: item.text
            for item in node.findall("g:data", namespace)
        }
        for node in nodes
    }
    assert len(nodes) == 3
    assert len(edges) == 1
    assert values["orders"]["n-component_id"] == values["billing"]["n-component_id"]
    assert values["orders"]["n-component_size"] == "2"
    assert values["isolated"]["n-component_size"] == "1"
    edge_values = {
        item.attrib["key"]: item.text
        for item in edges[0].findall("g:data", namespace)
    }
    assert edge_values["e-kind"] == "kafka"
    assert edge_values["e-source_class"] == "Orders"
    assert edge_values["e-target_class"] == "Billing"
