"""Assemble the standalone interactive HTML graph export."""

from __future__ import annotations

import json
from base64 import b64encode
from collections import Counter
from pathlib import Path
from typing import cast

import networkx as nx

from systemlens.domain.graph import GraphEdge
from systemlens.domain.code_flows import CodeFlow, IntegrationMethod
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
    """Build the proven service call graph for one persisted code flow."""
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
    if endpoint_steps:
        first_id = endpoint_steps[0]
        for edge in edges:
            if edge.to_endpoint is not None and edge.to_endpoint.id == first_id:
                add_relation(edge.from_endpoint.id, first_id)
        last_id = endpoint_steps[-1]
        for edge in edges:
            if edge.from_endpoint.id == last_id and edge.to_endpoint is not None:
                add_relation(last_id, edge.to_endpoint.id)

    condensation = nx.condensation(graph)
    component_order: list[str] = []
    for component_id in nx.topological_sort(condensation):
        component_order.extend(sorted(condensation.nodes[component_id]["members"]))
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
    }

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
            "reason": flow.reason,
            "vscode_uri": flow_vscode_uri(flow),
            "call_graph": _networkx_call_graph(flow, endpoints_by_service, edges),
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
        for flow in (code_flows or [])
    ]
    view_model["code_flows"] = serialized_code_flows
    view_model["progress_notice"] = progress_notice
    flow_counts = Counter(flow.module for flow in (code_flows or []))
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
