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
    flows: list[CodeFlow],
    endpoints_by_service: dict[str, list[MessageEndpoint]],
    edges: list[GraphEdge],
) -> dict[str, object]:
    """Build the complete port graph evidenced by all persisted flows.

    Every flow supplies endpoint and module seeds. Every topology arc that
    touches those seeds is retained, including fan-in, fan-out and cycles. The
    graph is not reduced to a rooted tree because that would discard proven
    modules and port arcs.
    """
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
    seen_relation_keys: set[tuple[str, str, str, str | None]] = set()
    endpoint_steps = [
        step.endpoint_id for flow in flows for step in flow.steps
        if step.endpoint_id in endpoint_by_id
    ]
    flow_endpoint_ids = set(endpoint_steps)

    def add_relation(edge: GraphEdge) -> None:
        source_id = edge.from_endpoint.id
        target_id = edge.to_endpoint.id if edge.to_endpoint is not None else None
        source_service = service_by_endpoint.get(source_id)
        target_service = service_by_endpoint.get(target_id) if target_id else None
        if not source_service or not target_service or source_service == target_service:
            return
        label = edge.from_endpoint.topic
        key = (edge.kind, label, source_id, target_id)
        if key in seen_relation_keys:
            return
        seen_relation_keys.add(key)
        graph.add_edge(
            source_service,
            target_service,
            key=key,
            kind=edge.kind,
            label=label,
            endpoint_ids=[source_id, target_id],
        )

    # Include every service owning a flow endpoint, including a module that
    # has no matching topology arc in the current snapshot.
    for endpoint_id in endpoint_steps:
        graph.add_node(service_by_endpoint[endpoint_id])
    for flow in flows:
        if flow.module:
            graph.add_node(flow.module)

    # A topology arc is relevant when either port is part of the persisted
    # flow. This retains all proven incoming and outgoing branches around the
    # flow instead of selecting only consecutive endpoint pairs.
    for edge in edges:
        source_id = edge.from_endpoint.id
        target_id = edge.to_endpoint.id if edge.to_endpoint is not None else None
        source_role = (edge.from_endpoint.system, edge.from_endpoint.role)
        target_role = (
            (edge.to_endpoint.system, edge.to_endpoint.role)
            if edge.to_endpoint is not None else None
        )
        if (
            (source_id in flow_endpoint_ids or target_id in flow_endpoint_ids)
            and source_role in {("rest", "call"), ("kafka", "produce")}
            and target_role in {("rest", "serve"), ("kafka", "consume")}
        ):
            add_relation(edge)

    triggers: dict[str, list[dict[str, object]]] = {}
    for flow in flows:
        if flow.steps:
            trigger = flow.steps[0]
        else:
            continue
        trigger_service = (
            service_by_endpoint.get(trigger.endpoint_id)
            if trigger.endpoint_id else flow.module
        )
        if trigger_service:
            triggers.setdefault(trigger_service, []).append({
                "flow_id": flow.id,
                "kind": trigger.kind,
                "name": trigger.name,
                "endpoint_id": trigger.endpoint_id,
            })

    if nx.is_directed_acyclic_graph(graph):
        component_order = list(nx.lexicographical_topological_sort(graph))
    else:
        component_order = sorted(graph.nodes)
    return {
        "nodes": sorted(graph.nodes),
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
                graph.edges(keys=True, data=True),
                key=lambda item: (item[0], item[1], str(item[2])),
            )
        ],
        "triggers": triggers,
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
        call_graph = _networkx_call_graph([flow], endpoints_by_service, edges)
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
        seen_edges: set[tuple[object, ...]] = set()
        for _flow, call_graph, _count in items:
            graph.add_nodes_from(cast(list[str], call_graph["nodes"]))
            for edge in cast(list[dict[str, object]], call_graph["edges"]):
                endpoint_ids = tuple(cast(list[object], edge.get("endpoint_ids", [])))
                edge_key = (
                    edge["source"], edge["target"], edge["kind"], edge["label"],
                    endpoint_ids,
                )
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                graph.add_edge(
                    edge["source"], edge["target"],
                    kind=edge["kind"], label=edge["label"],
                    endpoint_ids=list(endpoint_ids),
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
    view_model["all_flows_call_graph"] = _networkx_call_graph(
        list(code_flows or []), endpoints_by_service, edges
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
