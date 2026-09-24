"""Assemble the standalone interactive HTML graph export."""

from __future__ import annotations

import json
from base64 import b64encode
from collections import Counter
from pathlib import Path
from typing import cast

import networkx as nx

from systemlens.domain.graph import GraphEdge
from systemlens.domain.code_flows import CodeFlow, CodeQLCallGraphEdge, IntegrationMethod
from systemlens.domain.models import (
    ArchitectureRelation,
    ExtractionDiagnostic,
    Finding,
    GraphFact,
    MessageEndpoint,
)
from systemlens.domain.module_inventory import DiscoveredModule, ModuleDependency
from systemlens.render.graph_view_model import build_graph_view_model
from systemlens.render._graph_view_helpers import _vscode_file_uri


_ASSET_ROOT = Path(__file__).parent / "assets"
_GRAPH_HTML_TEMPLATE = (_ASSET_ROOT / "graph.html").read_text(encoding="utf-8")
_GRAPH_STYLE_MODULES = tuple(sorted((_ASSET_ROOT / "graph").glob("*.css")))
_GRAPH_CSS = "".join(
    path.read_text(encoding="utf-8") for path in _GRAPH_STYLE_MODULES
)
_GRAPH_JS_MODULES = tuple(sorted((_ASSET_ROOT / "graph").glob("*.js")))
_GRAPH_JS = "\n".join(path.read_text(encoding="utf-8") for path in _GRAPH_JS_MODULES)
_LAYER_GEOMETRY_JS = (_ASSET_ROOT / "layer_geometry.js").read_text(encoding="utf-8")
_ASYNCAPI_WEB_COMPONENT_JS = (
    _ASSET_ROOT / "vendor" / "asyncapi-web-component-3.1.8.js"
).read_text(encoding="utf-8")
_ASYNCAPI_WEB_COMPONENT_CSS = (
    _ASSET_ROOT / "vendor" / "asyncapi-react-component-3.1.8.min.css"
).read_text(encoding="utf-8")
_ASYNCAPI_WEB_COMPONENT_CSS_IMPORT_PATH = (
    "data:text/css;base64,"
    + b64encode(_ASYNCAPI_WEB_COMPONENT_CSS.encode("utf-8")).decode("ascii")
)


def _networkx_call_graph(
    flow: CodeFlow,
    endpoints_by_service: dict[str, list[MessageEndpoint]],
    edges: list[GraphEdge],
) -> dict[str, object]:
    """Build the proven inter-service interaction graph for one persisted flow."""
    endpoint_by_id = {
        endpoint.id: endpoint
        for service_endpoints in endpoints_by_service.values()
        for endpoint in service_endpoints
    }
    service_by_endpoint = {
        endpoint.id: service
        for service, service_endpoints in endpoints_by_service.items()
        for endpoint in service_endpoints
    }
    graph = nx.MultiDiGraph()
    endpoint_steps = [
        step.endpoint_id for step in flow.steps
        if step.endpoint_id in endpoint_by_id
    ]
    flow_endpoint_pairs = set(zip(endpoint_steps, endpoint_steps[1:]))
    for endpoint_id in endpoint_steps:
        graph.add_node(service_by_endpoint[endpoint_id])

    def add_relation(source_id: str, target_id: str) -> None:
        source_service = service_by_endpoint.get(source_id)
        target_service = service_by_endpoint.get(target_id)
        if not source_service or not target_service or source_service == target_service:
            return
        candidates = [
            edge for edge in edges
            if edge.from_endpoint.id == source_id
            and edge.to_endpoint is not None
            and edge.to_endpoint.id == target_id
        ]
        for edge in candidates:
            label = edge.from_endpoint.topic
            key = (edge.kind, label)
            candidate = {
                "kind": edge.kind,
                "label": label,
                "endpoint_ids": [source_id, target_id],
            }
            if graph.has_edge(source_service, target_service, key=key):
                current = graph[source_service][target_service][key]
                current_pair = tuple(current.get("endpoint_ids", []))
                candidate_rank = (
                    (source_id, target_id) not in flow_endpoint_pairs,
                    (source_id, target_id),
                )
                current_rank = (
                    current_pair not in flow_endpoint_pairs,
                    current_pair,
                )
                if candidate_rank < current_rank:
                    graph[source_service][target_service][key].update(candidate)
            else:
                graph.add_edge(source_service, target_service, key=key, **candidate)

    for source_id, target_id in zip(endpoint_steps, endpoint_steps[1:]):
        add_relation(source_id, target_id)
    # A producer can have several proven consumers. Keep all of those
    # branches in the exported interaction graph even though the persisted flow
    # remains one representative endpoint path for compatibility.
    for source_id in endpoint_steps:
        source_endpoint = endpoint_by_id.get(source_id)
        if source_endpoint is None or (source_endpoint.system, source_endpoint.role) != ("kafka", "produce"):
            continue
        for edge in edges:
            if edge.from_endpoint.id == source_id and edge.to_endpoint is not None:
                add_relation(source_id, edge.to_endpoint.id)
    if endpoint_steps:
        first_id = endpoint_steps[0]
        first_endpoint = endpoint_by_id[first_id]
        incoming_by_source = {
            edge.from_endpoint.id: edge
            for edge in edges
            if edge.to_endpoint is not None and edge.to_endpoint.id == first_id
        }
        incoming = list(incoming_by_source.values())
        # An upstream service is part of the rooted call flow only for a
        # message entry with one proven producer. HTTP entries are already
        # roots, and a Kafka fan-in must not manufacture a root by selecting
        # one of several producers.
        if (
            (first_endpoint.system, first_endpoint.role) == ("kafka", "consume")
            and len(incoming) == 1
        ):
            add_relation(incoming[0].from_endpoint.id, first_id)
        last_id = endpoint_steps[-1]
        for edge in edges:
            if edge.from_endpoint.id == last_id and edge.to_endpoint is not None:
                add_relation(last_id, edge.to_endpoint.id)

    # The call-flow view is intentionally an arborescence, even when the
    # persisted architecture contains fan-in or cycles.  Pick one stable root
    # and keep the first reachable parent for every service.  This preserves
    # all proven branches from that root, while preventing a second root or a
    # back-edge from turning the exported view into a disconnected graph/DAG.
    first_service = service_by_endpoint.get(endpoint_steps[0]) if endpoint_steps else None
    roots = sorted(node for node in graph if graph.in_degree(node) == 0)
    upstream_roots = [
        candidate for candidate in roots
        if first_service is not None and nx.has_path(graph, candidate, first_service)
    ]
    if len(upstream_roots) == 1:
        root = upstream_roots[0]
    elif first_service is not None:
        root = first_service
    else:
        root = sorted(graph.nodes)[0] if graph.nodes else None

    tree = nx.MultiDiGraph()
    if root is not None:
        tree.add_node(root)
        visited = {root}
        pending = [root]
        while pending:
            source = pending.pop(0)
            for target in sorted(graph.successors(source)):
                if target in visited:
                    continue
                visited.add(target)
                pending.append(target)
                tree.add_node(target)
                for key, data in sorted(graph[source][target].items(), key=lambda item: str(item[0])):
                    tree.add_edge(source, target, key=key, **data)

    component_order = list(nx.topological_sort(tree))
    return {
        "nodes": sorted(tree.nodes),
        "node_order": component_order,
        "edges": [
            {
                "source": source,
                "target": target,
                "kind": str(data.get("kind", "")),
                "label": str(data.get("label", "")),
                "endpoint_ids": list(data.get("endpoint_ids", [])),
            }
            for source, target, _key, data in sorted(
                tree.edges(keys=True, data=True),
                key=lambda item: (item[0], item[1], str(item[2])),
            )
        ],
    }


def _distinct_export_flows(
    flows: list[CodeFlow],
    endpoints_by_service: dict[str, list[MessageEndpoint]],
    edges: list[GraphEdge],
) -> list[tuple[CodeFlow, dict[str, object], int]]:
    """Collapse flows that render to the same inter-service interaction graph.

    Indexing keeps distinct evidence and diagnostics. The HTML flow picker,
    however, should not present the same graph repeatedly just because CodeQL
    found several equivalent dispatch routes.
    """
    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    status_rank = {"complete": 0, "potential": 1, "cycle": 2}
    grouped: dict[
        tuple[tuple[str, ...], tuple[tuple[str, str, str, str], ...]],
        list[tuple[CodeFlow, dict[str, object]]],
    ] = {}
    for flow in flows:
        call_graph = _networkx_call_graph(flow, endpoints_by_service, edges)
        graph_nodes = cast(list[str], call_graph["nodes"])
        graph_edges = cast(list[dict[str, str]], call_graph["edges"])
        signature: tuple[tuple[str, ...], tuple[tuple[str, str, str, str], ...]] = (
            tuple(graph_nodes),
            tuple(
                (
                    edge["source"], edge["target"], edge["kind"], edge["label"]
                )
                for edge in graph_edges
            ),
        )
        grouped.setdefault(signature, []).append((flow, call_graph))

    distinct: list[tuple[CodeFlow, dict[str, object], int]] = []
    for parallel in grouped.values():
        flow, call_graph = min(
            parallel,
            key=lambda item: (
                status_rank.get(item[0].status, 99),
                confidence_rank.get(item[0].confidence, 99),
                len(item[0].steps),
                item[0].module,
                item[0].path,
                item[0].start_line,
                item[0].id,
            ),
        )
        distinct.append((flow, call_graph, len(parallel)))

    # A Kafka publication and the consumer fragment that starts at the same
    # topic are two indexing fragments of one rooted flow.  They can have
    # different edge sets because one fragment discovers the downstream branch
    # and the other discovers the upstream producer.  Merge only when the
    # rendered root, topic and known message type agree; this is a keyed union,
    # not a Cartesian product of producer and consumer routes.
    endpoint_by_id = {
        endpoint.id: endpoint
        for service_endpoints in endpoints_by_service.values()
        for endpoint in service_endpoints
    }

    def fragment_key(
        item: tuple[CodeFlow, dict[str, object], int],
    ) -> tuple[str, str, str | None, bool] | None:
        flow, call_graph, _count = item
        node_order = cast(list[str], call_graph["node_order"])
        if not node_order:
            return None
        kafka_steps = [
            endpoint_by_id[step.endpoint_id]
            for step in flow.steps
            if step.endpoint_id in endpoint_by_id
            and endpoint_by_id[step.endpoint_id].system == "kafka"
        ]
        if not kafka_steps:
            return None
        has_entry = any(endpoint.role == "consume" for endpoint in kafka_steps)
        has_publication = any(endpoint.role == "produce" for endpoint in kafka_steps)
        if not has_entry and not has_publication:
            return None
        boundary = next(
            (endpoint for endpoint in kafka_steps if endpoint.role == "consume"),
            kafka_steps[0],
        )
        topic = boundary.topic
        message_type = boundary.message_type
        return node_order[0], topic, message_type, has_entry

    def merge_call_graphs(
        items: list[tuple[CodeFlow, dict[str, object], int]],
    ) -> dict[str, object]:
        root = cast(list[str], items[0][1]["node_order"])[0]
        graph = nx.MultiDiGraph()
        for _flow, call_graph, _count in items:
            graph.add_nodes_from(cast(list[str], call_graph["nodes"]))
            for edge in cast(list[dict[str, object]], call_graph["edges"]):
                graph.add_edge(
                    edge["source"], edge["target"],
                    kind=edge["kind"], label=edge["label"],
                    endpoint_ids=list(cast(list[object], edge.get("endpoint_ids", []))),
                )
        tree = nx.MultiDiGraph()
        tree.add_node(root)
        visited = {root}
        pending = [root]
        while pending:
            source = pending.pop(0)
            for target in sorted(graph.successors(source)):
                if target in visited:
                    continue
                visited.add(target)
                pending.append(target)
                tree.add_node(target)
                for key, data in sorted(
                    graph[source][target].items(), key=lambda item: str(item[0])
                ):
                    tree.add_edge(source, target, key=key, **data)
        return {
            "nodes": sorted(tree.nodes),
            "node_order": list(nx.topological_sort(tree)),
            "edges": [
                {
                    "source": source,
                    "target": target,
                    "kind": str(data.get("kind", "")),
                    "label": str(data.get("label", "")),
                    "endpoint_ids": list(cast(list[object], data.get("endpoint_ids", []))),
                }
                for source, target, _key, data in sorted(
                    tree.edges(keys=True, data=True),
                    key=lambda item: (item[0], item[1], str(item[2])),
                )
            ],
        }

    fusion_groups: dict[tuple[str, str, str | None], list[int]] = {}
    fusion_roles: dict[tuple[str, str, str | None], set[bool]] = {}
    for index, item in enumerate(distinct):
        key = fragment_key(item)
        if key is not None:
            fusion_groups.setdefault(key[:3], []).append(index)
            fusion_roles.setdefault(key[:3], set()).add(key[3])
    fused: set[int] = set()
    fused_distinct: list[tuple[CodeFlow, dict[str, object], int]] = []
    for group_key, indexes in fusion_groups.items():
        if len(indexes) < 2 or fusion_roles[group_key] != {False, True}:
            continue
        candidates = [distinct[index] for index in indexes]
        root = cast(list[str], candidates[0][1]["node_order"])[0]
        flow, _graph, count = min(
            candidates,
            key=lambda item: (
                item[0].module != root,
                not any(step.kind == "cron_entry" for step in item[0].steps),
                status_rank.get(item[0].status, 99),
                confidence_rank.get(item[0].confidence, 99),
                -len(item[0].steps),
                item[0].id,
            ),
        )
        fused_distinct.append((flow, merge_call_graphs(candidates), sum(item[2] for item in candidates)))
        fused.update(indexes)
    fused_distinct.extend(item for index, item in enumerate(distinct) if index not in fused)
    return sorted(
        fused_distinct,
        key=lambda item: (item[0].module, item[0].path, item[0].start_line, item[0].id),
    )

def render_graph_html(
    endpoints_by_service: dict[str, list[MessageEndpoint]],
    edges: list[GraphEdge],
    collections_by_service: dict[str, list[str]] | None = None,
    modules_by_service: dict[str, DiscoveredModule] | None = None,
    indexing_warnings: list[str] | None = None,
    build_modules: list[DiscoveredModule] | None = None,
    module_dependencies: list[ModuleDependency] | None = None,
    source_roots: list[Path] | None = None,
    findings_by_service: dict[str, list[Finding]] | None = None,
    root_path: Path | None = None,
    request_reply_strategy1: bool = False,
    diagnostics: list[ExtractionDiagnostic] | None = None,
    kafka_dto_definitions: list[dict[str, object]] | None = None,
    openapi_contracts: list[dict[str, object]] | None = None,
    asyncapi_contracts: list[dict[str, object]] | None = None,
    graph_facts: list[GraphFact] | None = None,
    strategy1: bool = False,
    architecture_relations: list[ArchitectureRelation] | None = None,
    code_flows: list[CodeFlow] | None = None,
    integration_methods: list[IntegrationMethod] | None = None,
    codeql_call_edges: list[CodeQLCallGraphEdge] | None = None,
    progress_notice: str | None = None,
) -> str:
    """Render a graph view model as one self-contained HTML document."""
    view_model = build_graph_view_model(
        endpoints_by_service=endpoints_by_service,
        edges=edges,
        collections_by_service=collections_by_service,
        modules_by_service=modules_by_service,
        indexing_warnings=indexing_warnings,
        build_modules=build_modules,
        module_dependencies=module_dependencies,
        source_roots=source_roots,
        findings_by_service=findings_by_service,
        root_path=root_path,
        request_reply_strategy1=request_reply_strategy1,
        diagnostics=diagnostics,
        kafka_dto_definitions=kafka_dto_definitions,
        openapi_contracts=openapi_contracts,
        asyncapi_contracts=asyncapi_contracts,
        graph_facts=graph_facts,
        strategy1=strategy1,
        architecture_relations=architecture_relations,
        integration_methods=integration_methods,
        code_flows=code_flows,
        codeql_call_edges=codeql_call_edges,
    )
    port_labels = {
        str(port["endpoint_id"]): str(port["label"])
        for node in cast(list[dict[str, object]], view_model["nodes"])
        for port in cast(list[dict[str, object]], node.get("ports", []))
        if "endpoint_id" in port and "label" in port
    }
    def flow_vscode_uri(flow: CodeFlow) -> str | None:
        if root_path is not None:
            return _vscode_file_uri(root_path / flow.path, root_path, source_roots, flow.start_line)
        if source_roots:
            return _vscode_file_uri(source_roots[0] / flow.path, root_path, source_roots, flow.start_line)
        return None

    serialized_code_flows = [
        {
            "id": flow.id,
            "module": flow.module,
            "method": flow.method,
            "path": flow.path,
            "start_line": flow.start_line,
            "end_line": flow.end_line,
            "status": flow.status,
            "confidence": flow.confidence,
            "reconciliation": flow.reconciliation,
            "alternative_count": flow.alternative_count,
            "equivalent_count": equivalent_count,
            "reason": flow.reason,
            "vscode_uri": flow_vscode_uri(flow),
            "call_graph": call_graph,
            "steps": [
                {
                    "order": step.order,
                    "kind": step.kind,
                    "name": step.name,
                    "path": step.path,
                    "start_line": step.start_line,
                    "end_line": step.end_line,
                    "endpoint_id": step.endpoint_id,
                    "port_label": port_labels.get(step.endpoint_id) if step.endpoint_id else None,
                    "operation": step.operation,
                }
                for step in flow.steps
            ],
        }
        for flow, call_graph, equivalent_count in _distinct_export_flows(
            list(code_flows or []), endpoints_by_service, edges
        )
    ]
    view_model["code_flows"] = serialized_code_flows
    view_model["progress_notice"] = progress_notice
    flow_counts = Counter(flow["module"] for flow in serialized_code_flows)
    nodes = cast(list[dict[str, object]], view_model["nodes"])
    for node in nodes:
        name = node.get("name")
        if node.get("kind") == "microservice" and isinstance(name, str):
            node["internal_flow_count"] = flow_counts[name]
    graph_data = json.dumps(
        view_model,
        ensure_ascii=False,
    ).replace("</", "<\\/")
    asyncapi_assets = ""
    if asyncapi_contracts:
        asyncapi_assets = (
            "<script>window.systemlensAsyncApiCssImportPath="
            f"{json.dumps(_ASYNCAPI_WEB_COMPONENT_CSS_IMPORT_PATH)};</script>"
            f"<script>{_ASYNCAPI_WEB_COMPONENT_JS}</script>"
        )
    return (
        _GRAPH_HTML_TEMPLATE.replace("__GRAPH_CSS__", _GRAPH_CSS)
        .replace("__GRAPH_JS__", _GRAPH_JS)
        .replace("__GRAPH_DATA__", graph_data)
        .replace("__LAYER_GEOMETRY__", _LAYER_GEOMETRY_JS)
        .replace("__ASYNCAPI_WEB_COMPONENT__", asyncapi_assets)
    )
