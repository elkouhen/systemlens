"""GraphML export of the indexed inter-service interaction graph."""

from __future__ import annotations

from collections import defaultdict
import hashlib
from xml.sax.saxutils import escape

from systemlens.domain.graph import GraphEdge
from systemlens.domain.models import MessageEndpoint


def _class_name(qualified_name: str | None) -> str:
    if not qualified_name:
        return "?"
    return qualified_name.rsplit(".", 1)[-1]


def _text(value: object) -> str:
    return escape(str(value))


def _node_id(service: str) -> str:
    return f"n-{hashlib.sha256(service.encode()).hexdigest()[:16]}"


def _components(nodes: set[str], edges: list[GraphEdge]) -> dict[str, tuple[str, int]]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        adjacency[edge.from_service].add(edge.to_service)
        adjacency[edge.to_service].add(edge.from_service)
    result: dict[str, tuple[str, int]] = {}
    for component_number, root in enumerate(sorted(nodes), 1):
        if root in result:
            continue
        pending = [root]
        members: list[str] = []
        while pending:
            node = pending.pop()
            if node in result or node in members:
                continue
            members.append(node)
            pending.extend(sorted(adjacency[node] - set(members), reverse=True))
        component_id = f"component-{component_number}"
        for member in members:
            result[member] = (component_id, len(members))
    return result


def _data(key: str, value: object) -> str:
    return f'<data key="{key}">{_text(value)}</data>'


def _edge_resource(endpoint: MessageEndpoint) -> str:
    return endpoint.topic


def render_graphml(services: list[str], edges: list[GraphEdge]) -> str:
    """Render only inter-service edges and annotate weak components."""
    nodes = set(services)
    nodes.update(edge.from_service for edge in edges)
    nodes.update(edge.to_service for edge in edges)
    components = _components(nodes, edges)
    node_xml = "".join(
        f'<node id="{_node_id(service)}" label="{_text(service)}">'
        f"{_data('n-component_id', components[service][0])}"
        f"{_data('n-component_size', components[service][1])}</node>"
        for service in sorted(nodes)
    )
    edge_xml: list[str] = []
    for index, edge in enumerate(edges, 1):
        source = edge.from_endpoint
        target = edge.to_endpoint
        edge_xml.append(
            f'<edge id="e-{index}" source="{_node_id(edge.from_service)}" '
            f'target="{_node_id(edge.to_service)}">'
            f"{_data('e-kind', edge.kind)}"
            f"{_data('e-source_resource', _edge_resource(source))}"
            f"{_data('e-target_resource', _edge_resource(target) if target else '')}"
            f"{_data('e-source_class', _class_name(source.qualified_name))}"
            f"{_data('e-target_class', _class_name(target.qualified_name) if target else '?')}"
            f"{_data('e-source_role', source.role)}"
            f"{_data('e-target_role', target.role if target else '?')}"
            f"{_data('e-dynamic_topic', str(source.topic_dynamic).lower())}</edge>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://graphml.graphdrawing.org/xmlns '
        'http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd">'
        '<key id="n-component_id" for="node" attr.name="component_id" attr.type="string"/>'
        '<key id="n-component_size" for="node" attr.name="component_size" attr.type="int"/>'
        '<key id="e-kind" for="edge" attr.name="kind" attr.type="string"/>'
        '<key id="e-source_resource" for="edge" attr.name="source_resource" attr.type="string"/>'
        '<key id="e-target_resource" for="edge" attr.name="target_resource" attr.type="string"/>'
        '<key id="e-source_class" for="edge" attr.name="source_class" attr.type="string"/>'
        '<key id="e-target_class" for="edge" attr.name="target_class" attr.type="string"/>'
        '<key id="e-source_role" for="edge" attr.name="source_role" attr.type="string"/>'
        '<key id="e-target_role" for="edge" attr.name="target_role" attr.type="string"/>'
        '<key id="e-dynamic_topic" for="edge" attr.name="dynamic_topic" attr.type="boolean"/>'
        '<graph id="interservice-flows" edgedefault="directed">'
        f"{node_xml}{''.join(edge_xml)}</graph></graphml>"
    )
