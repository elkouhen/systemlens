"""Assemble the standalone interactive HTML graph export."""

from __future__ import annotations

import json
import hashlib
import gzip
from base64 import b64encode
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast, Mapping

from systemlens.domain.graph import GraphEdge, graph_edges_from_facts
from systemlens.domain.code_flows import CodeFlow, CodeQLCallGraphEdge, IntegrationMethod
from systemlens.application.flow_consistency import diagnose_flow_consistency_snapshot
from systemlens.domain.models import (
    ArchitectureRelation,
    ExtractionDiagnostic,
    Finding,
    GraphFact,
    MessageEndpoint,
)
from systemlens.domain.module_inventory import DiscoveredModule, ModuleDependency
from systemlens.render.graph_view_model import build_graph_view_model
from systemlens.render.call_graph import (
    _all_export_flows,
    _index_call_graph_inputs,
    _networkx_call_graph,
    _reachable_flow_paths,
)
from systemlens.render._graph_view_helpers import _vscode_file_uri


_ASSET_ROOT = Path(__file__).parent / "assets"
_GRAPH_HTML_TEMPLATE = (_ASSET_ROOT / "graph.html").read_text(encoding="utf-8")
_GRAPH_STYLE_FILENAMES = (
    "00-graph.css",
    "10-widgets.css",
    "20-presentation.css",
    "30-charter.css",
    "40-ergonomics.css",
    "50-obsidian.css",
)
_GRAPH_STYLE_MODULES = tuple(_ASSET_ROOT / "graph" / name for name in _GRAPH_STYLE_FILENAMES)
_GRAPH_CSS = "".join(
    path.read_text(encoding="utf-8") for path in _GRAPH_STYLE_MODULES
)
_GRAPH_JS_FILENAMES = (
    "00-core.js",
    "05-code-flow-selection.js",
    "06-view-lifecycle.js",
    "07-navigation.js",
    "10-rebuild.js",
    "20-controls.js",
    "30-layouts.js",
    "40-details.js",
    "42-resource-catalogue.js",
    "50-paths.js",
    "55-code-flows.js",
    "60-bootstrap.js",
)
_GRAPH_JS_MODULES = tuple(_ASSET_ROOT / "graph" / name for name in _GRAPH_JS_FILENAMES)
_GRAPH_JS = "\n".join(path.read_text(encoding="utf-8") for path in _GRAPH_JS_MODULES)
_LAYER_GEOMETRY_JS = (_ASSET_ROOT / "layer_geometry.js").read_text(encoding="utf-8")
_ASYNCAPI_WEB_COMPONENT_JS = (
    _ASSET_ROOT / "vendor" / "asyncapi-web-component-3.1.8.js"
).read_text(encoding="utf-8")
_ASYNCAPI_WEB_COMPONENT_CSS = (
    _ASSET_ROOT / "vendor" / "asyncapi-react-component-3.1.8.min.css"
).read_text(encoding="utf-8")
_ASYNCAPI_COMPRESSED_ASSETS = b64encode(gzip.compress(
    json.dumps({"js": _ASYNCAPI_WEB_COMPONENT_JS, "css": _ASYNCAPI_WEB_COMPONENT_CSS},
               separators=(",", ":")).encode("utf-8"),
    mtime=0,
)).decode("ascii")
_ASYNCAPI_LOADER_JS = (
    _ASSET_ROOT / "asyncapi_loader.js"
).read_text(encoding="utf-8")


@dataclass(frozen=True)
class GraphExportInput:
    """Complete snapshot input shared by delivery adapters and the HTML renderer."""

    endpoints_by_service: dict[str, list[MessageEndpoint]]
    edges: list[GraphEdge]
    options: Mapping[str, Any] = field(default_factory=dict)


def render_graph_html_input(export_input: GraphExportInput) -> str:
    """Render one typed snapshot while retaining the legacy function surface."""
    return render_graph_html(
        export_input.endpoints_by_service,
        export_input.edges,
        **dict(export_input.options),
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
    flow_descriptions: dict[str, str] | None = None,
    progress_notice: str | None = None,
    call_graphs_only: bool = False,
    include_call_graph_annotations: bool = True,
) -> str:
    """Render a graph view model as one self-contained HTML document."""
    flow_annotation_facts = [
        fact for fact in graph_facts or []
        if include_call_graph_annotations
        and fact.fact_type == "node" and fact.kind == "flow_data_access"
    ]
    topology_facts = [fact for fact in graph_facts or [] if fact.kind != "flow_data_access"]
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
        graph_facts=topology_facts,
        strategy1=strategy1,
        architecture_relations=architecture_relations,
        integration_methods=integration_methods,
        code_flows=code_flows,
        codeql_call_edges=codeql_call_edges,
    )
    export_flows = list(code_flows or [])
    call_flows = [
        flow for flow in export_flows
        if not flow.steps
        or flow.steps[-1].kind not in {"data_read", "data_write"}
        or any(step.kind in {"http_call", "message_publish"} for step in flow.steps)
    ]
    flow_edges = [
        *edges,
        *graph_edges_from_facts(topology_facts, endpoints_by_service),
    ]
    call_graph_index = _index_call_graph_inputs(export_flows, endpoints_by_service, flow_edges)
    call_graph_index_for_display = _index_call_graph_inputs(
        call_flows, endpoints_by_service, flow_edges
    )
    all_flows_call_graph = _networkx_call_graph(
        call_flows, endpoints_by_service, flow_edges, index=call_graph_index_for_display,
        include_occurrences=False,
    )
    all_flows_tree = cast(dict[str, object], all_flows_call_graph["call_tree"])
    view_model["all_flows_call_graph"] = {
        **all_flows_call_graph,
        "call_tree": {
            key: value
            for key, value in all_flows_tree.items()
            if key not in {"root_flow_ids", "reachable_flow_ids", "transitions"}
        },
    }
    port_labels = {
        str(port["endpoint_id"]): str(port["label"])
        for node in cast(list[dict[str, object]], view_model["nodes"])
        for port in cast(list[dict[str, object]], node.get("ports", []))
        if "endpoint_id" in port and "label" in port
    }
    endpoint_by_id = {
        endpoint.id: endpoint
        for service_endpoints in endpoints_by_service.values()
        for endpoint in service_endpoints
    }
    def flow_vscode_uri(flow: CodeFlow) -> str | None:
        if root_path is not None:
            return _vscode_file_uri(root_path / flow.path, root_path, source_roots, flow.start_line)
        if source_roots:
            return _vscode_file_uri(source_roots[0] / flow.path, root_path, source_roots, flow.start_line)
        return None

    call_graphs: dict[str, dict[str, object]] = {}
    graph_ids_by_digest: dict[str, str] = {}
    export_flow_items: list[tuple[CodeFlow, int, str]] = []
    for flow, call_graph, equivalent_count in _all_export_flows(
        call_flows, endpoints_by_service, edges, index=call_graph_index_for_display
    ):
        call_tree = cast(dict[str, object], call_graph["call_tree"])
        graph_identity = {
            **call_graph,
            "call_tree": {
                key: value
                for key, value in call_tree.items()
                if key not in {
                    "root_flow_ids", "reachable_flow_ids", "transitions",
                    "root_occurrence_ids", "occurrences",
                }
            },
        }
        # The trigger catalogue is shared by every root graph in this export.
        # Hash only the root-specific shape; serializing the full catalogue for
        # each flow makes this loop quadratic when many triggers are indexed.
        graph_json = json.dumps(
            {key: value for key, value in graph_identity.items() if key != "triggers"},
            sort_keys=True, separators=(",", ":"),
        )
        graph_digest = hashlib.sha256(graph_json.encode("utf-8")).hexdigest()
        call_graph_id = graph_ids_by_digest.get(graph_digest)
        if call_graph_id is None:
            call_graph_id = f"call-graph-{len(call_graphs) + 1}"
            graph_ids_by_digest[graph_digest] = call_graph_id
            call_graphs[call_graph_id] = {
                **graph_identity,
                "call_trees_by_flow": {},
            }
            call_graphs[call_graph_id]["call_trees_by_flow"] = {}
        call_trees_by_flow = cast(
            dict[str, dict[str, object]],
            call_graphs[call_graph_id]["call_trees_by_flow"],
        )
        compact_tree = cast(dict[str, object], call_graph["call_tree"])
        call_trees_by_flow[flow.id] = {
            "root_flow_ids": [flow.id],
            "reachable_flow_ids": compact_tree["reachable_flow_ids"],
            "transitions": compact_tree["transitions"],
        }
        export_flow_items.append((flow, equivalent_count, call_graph_id))

    def serialize_code_flow(
        flow: CodeFlow, equivalent_count: int, call_graph_id: str | None,
    ) -> dict[str, object]:
        return {
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
            "call_graph_id": call_graph_id,
            "steps": [
                {
                    "order": step.order,
                    "kind": step.kind,
                    "name": (
                        endpoint_by_id[step.endpoint_id].topic_display
                        or endpoint_by_id[step.endpoint_id].topic
                        if step.endpoint_id in endpoint_by_id
                        else step.name
                    ),
                    "path": step.path,
                    "start_line": step.start_line,
                    "end_line": step.end_line,
                    "endpoint_id": step.endpoint_id,
                    "port_label": port_labels.get(step.endpoint_id) if step.endpoint_id else None,
                    "operation": step.operation,
                    "action": step.action,
                    "branch": step.branch,
                    "resource": step.resource,
                    "key": step.key,
                    "causal_id": step.causal_id,
                    "expected_version": step.expected_version,
                    "completion": step.completion,
                    "query_shape": step.query_shape,
                }
                for step in flow.steps
                if include_call_graph_annotations or step.kind not in {"data_read", "data_write"}
            ],
        }

    serialized_code_flows = [
        serialize_code_flow(flow, equivalent_count, call_graph_id)
        for flow, equivalent_count, call_graph_id in export_flow_items
    ]
    visible_flow_ids = {flow.id for flow, _, _ in export_flow_items}
    view_model["supporting_code_flows"] = [
        serialize_code_flow(flow, 1, None)
        for flow in call_flows if flow.id not in visible_flow_ids
    ]
    view_model["code_flows"] = serialized_code_flows
    exported_diagnostics: list[dict[str, object]] = []
    causal_reachability: dict[str, set[str]] = {}
    causal_paths: dict[str, dict[str, tuple[str, ...]]] = {}
    flow_by_id = {flow.id: flow for flow in export_flows}
    annotations_by_flow: dict[str, list[GraphFact]] = {}
    for fact in flow_annotation_facts:
        metadata = fact.metadata or {}
        flow_id = metadata.get("flow_id")
        if isinstance(flow_id, str) and flow_id in flow_by_id:
            annotations_by_flow.setdefault(flow_id, []).append(fact)
    reachable_access_flow_ids: set[str] = set()
    for root_flow, _, _ in (export_flow_items if include_call_graph_annotations else ()):
        paths = _reachable_flow_paths(call_graph_index, root_flow.id)
        causal_reachability[root_flow.id] = set(paths)
        causal_paths[root_flow.id] = paths
        reachable_access_flow_ids.update(paths)
    source_resources_by_module_and_name: dict[tuple[str, str], set[str]] = {}
    for flow in export_flows:
        for step in flow.steps:
            if step.kind not in {"data_read", "data_write"}:
                continue
            resource = step.resource or step.name
            simple_name = resource.rsplit(":", 1)[-1].rsplit(".", 1)[-1]
            source_resources_by_module_and_name.setdefault(
                (flow.module, simple_name), set()
            ).add(resource)
    data_accesses_by_flow: dict[str, list[dict[str, object]]] = {}
    first_call_by_access_flow: dict[str, list[object]] = {}
    if include_call_graph_annotations:
        for flow in export_flows:
            if flow.id not in reachable_access_flow_ids:
                continue
            accesses: dict[tuple[str, int, str, str, str], dict[str, object]] = {}
            for step in flow.steps:
                if step.kind not in {"data_read", "data_write"}:
                    continue
                site = (
                    step.path, step.start_line, step.kind,
                    step.operation or "", step.resource or step.name,
                )
                accesses.setdefault(site, {
                    "flow_id": flow.id,
                    "module": flow.module,
                    "kind": step.kind,
                    "action": step.action,
                    "resource": step.resource or step.name,
                    "path": step.path,
                    "line": step.start_line,
                    "confidence": flow.confidence,
                    "branch": step.branch,
                    "trigger_endpoint_id": flow.steps[0].endpoint_id if flow.steps else None,
                    "trigger_method": flow.method,
                    "trigger_kind": flow.steps[0].kind if flow.steps else None,
                    "trigger_name": flow.steps[0].name if flow.steps else None,
                })
            for fact in annotations_by_flow.get(flow.id, []):
                metadata = fact.metadata or {}
                action = str(metadata.get("action", ""))
                if action not in {"read", "insert", "update", "delete", "create_or_update", "read_update"}:
                    continue
                fact_name = fact.name or ""
                candidates = source_resources_by_module_and_name.get(
                    (flow.module, fact_name.rsplit(":", 1)[-1].rsplit(".", 1)[-1]), set()
                )
                candidate = next(iter(candidates)) if len(candidates) == 1 else None
                resource = (
                    candidate
                    if candidate and (
                        fact_name == candidate
                        or ":" not in fact_name and (
                            "." not in fact_name or candidate.rsplit(":", 1)[-1] == fact_name
                        )
                    ) else fact_name
                )
                site = (
                    fact.evidence_path or "", fact.evidence_line or 0,
                    "imported", action, resource,
                )
                accesses.setdefault(site, {
                    "flow_id": flow.id,
                    "module": flow.module,
                    "kind": "data_read" if action == "read" else "data_write",
                    "action": action,
                    "resource": resource,
                    "path": fact.evidence_path,
                    "line": fact.evidence_line,
                    "confidence": fact.confidence,
                    "status": fact.status,
                    "branch": metadata.get("branch"),
                    "origin": "imported",
                    "trigger_endpoint_id": flow.steps[0].endpoint_id if flow.steps else None,
                    "trigger_method": flow.method,
                    "trigger_kind": flow.steps[0].kind if flow.steps else None,
                    "trigger_name": flow.steps[0].name if flow.steps else None,
                })
            if accesses:
                data_accesses_by_flow[flow.id] = [accesses[site] for site in sorted(accesses)]
                first_call = next(
                    (step for step in flow.steps if step.kind == "method_call"), None
                )
                if first_call is not None:
                    first_call_by_access_flow[flow.id] = [
                        first_call.name, first_call.path, first_call.start_line,
                    ]
    view_model["flow_data_accesses"] = data_accesses_by_flow
    view_model["flow_data_access_first_calls"] = first_call_by_access_flow
    access_flow_ids_by_root: dict[str, list[str]] = {}
    for root_flow, _, _ in (export_flow_items if include_call_graph_annotations else ()):
        paths = causal_paths[root_flow.id]
        access_ids = sorted(paths.keys() & data_accesses_by_flow.keys())
        if access_ids:
            access_flow_ids_by_root[root_flow.id] = access_ids
    view_model["flow_data_access_flow_ids_by_root"] = access_flow_ids_by_root
    call_flow_ids = visible_flow_ids
    diagnostics_for_export = (
        diagnose_flow_consistency_snapshot(export_flows, causal_reachability, causal_paths)
        if include_call_graph_annotations else ()
    )
    for diagnostic in diagnostics_for_export:
        if diagnostic.flow_id not in call_flow_ids:
            continue
        item = diagnostic.as_dict()
        for side, flow_id, order in (
            ("write", diagnostic.write_flow_id, diagnostic.write_step),
            ("read", diagnostic.read_flow_id, diagnostic.read_step),
        ):
            evidence_flow = flow_by_id.get(flow_id)
            evidence_step = next((step for step in evidence_flow.steps if step.order == order), None) if evidence_flow else None
            if evidence_flow is not None and evidence_step is not None:
                item[f"{side}_site"] = {
                    "module": evidence_flow.module,
                    "trigger_endpoint_id": evidence_flow.steps[0].endpoint_id if evidence_flow.steps else None,
                    "path": evidence_step.path,
                    "line": evidence_step.start_line,
                    "action": evidence_step.action,
                }
        for side, endpoint_id in (
            ("write", diagnostic.write_endpoint_id),
            ("read", diagnostic.read_endpoint_id),
        ):
            endpoint = endpoint_by_id.get(endpoint_id) if endpoint_id else None
            if endpoint is not None:
                item[f"{side}_name"] = endpoint.topic_display or endpoint.topic
        item["async_endpoint_ids"] = [diagnostic.write_endpoint_id] if diagnostic.write_endpoint_id else []
        item["sync_endpoint_ids"] = [diagnostic.read_endpoint_id] if diagnostic.read_endpoint_id else []
        item["async_path_endpoint_ids"] = list(dict.fromkeys((
            *diagnostic.write_path_endpoint_ids,
            *((diagnostic.write_endpoint_id,) if diagnostic.write_endpoint_id else ()),
        )))
        item["sync_path_endpoint_ids"] = list(dict.fromkeys((
            *diagnostic.read_path_endpoint_ids,
            *((diagnostic.read_endpoint_id,) if diagnostic.read_endpoint_id else ()),
        )))
        if diagnostic.event_type != "async_write_sync_read":
            item["interaction_path_endpoint_ids"] = list(dict.fromkeys((
                *diagnostic.write_path_endpoint_ids,
                *((diagnostic.write_endpoint_id,) if diagnostic.write_endpoint_id else ()),
                *diagnostic.read_path_endpoint_ids,
                *((diagnostic.read_endpoint_id,) if diagnostic.read_endpoint_id else ()),
            )))
        for side, flow_id, dependency_order in (
            ("write", diagnostic.write_flow_id, diagnostic.write_dependency_step),
            ("read", diagnostic.read_flow_id, diagnostic.read_dependency_step),
        ):
            evidence_flow = flow_by_id.get(flow_id)
            evidence_step = next((
                step for step in evidence_flow.steps if step.order == dependency_order
            ), None) if evidence_flow is not None and dependency_order is not None else None
            if evidence_flow is not None and evidence_step is not None:
                item[f"{side}_dependency_site"] = {
                    "module": evidence_flow.module,
                    "trigger_endpoint_id": evidence_flow.steps[0].endpoint_id if evidence_flow.steps else None,
                    "path": evidence_step.path,
                    "line": evidence_step.start_line,
                    "action": evidence_step.action,
                }
        exported_diagnostics.append(item)
    view_model["flow_diagnostics"] = exported_diagnostics
    view_model["flow_descriptions"] = {
        str(flow_id): str(description)
        for flow_id, description in (flow_descriptions or {}).items()
        if description
    }
    view_model["call_graphs"] = call_graphs
    view_model["progress_notice"] = progress_notice
    flow_counts = Counter(flow["module"] for flow in serialized_code_flows)
    nodes = cast(list[dict[str, object]], view_model["nodes"])
    for node in nodes:
        name = node.get("name")
        if node.get("kind") == "microservice" and isinstance(name, str):
            node["internal_flow_count"] = flow_counts[name]
    if call_graphs_only:
        call_graph_edge_endpoint_ids = {
            str(endpoint_id)
            for graph in call_graphs.values()
            for edge in cast(list[dict[str, object]], graph.get("edges", []))
            for endpoint_id in cast(list[object], edge.get("endpoint_ids", []))
        }
        call_flow_endpoint_ids = {
            str(step.endpoint_id)
            for flow, _equivalent_count, _call_graph_id in export_flow_items
            for step in flow.steps
            if step.endpoint_id
        }
        relevant_endpoint_ids = call_graph_edge_endpoint_ids | call_flow_endpoint_ids
        service_names = {
            str(service)
            for graph in call_graphs.values()
            for service in cast(list[object], graph.get("node_order", []))
        }
        service_names.update(flow.module for flow, _count, _graph_id in export_flow_items)
        topic_names = {
            str(edge.get("label", ""))
            for graph in call_graphs.values()
            for edge in cast(list[dict[str, object]], graph.get("edges", []))
            if edge.get("kind") == "kafka"
        }
        compact_nodes: list[dict[str, object]] = []
        for node in nodes:
            kind = node.get("kind")
            name = str(node.get("name", ""))
            if kind == "microservice" and name in service_names:
                compact_node: dict[str, object] = {
                    key: node[key]
                    for key in ("id", "kind", "name", "color", "ports", "http_routes", "kafka_endpoints")
                    if key in node
                }
                compact_node["ports"] = [
                    port for port in cast(list[dict[str, object]], node.get("ports", []))
                    if str(port.get("endpoint_id", "")) in relevant_endpoint_ids
                ]
                compact_nodes.append(compact_node)
            elif kind in {"kafka_topic", "message_channel"} and name in topic_names:
                compact_nodes.append({
                    key: node[key] for key in ("id", "kind", "name", "color") if key in node
                })
        view_model["nodes"] = compact_nodes
        view_model["links"] = [
            link for link in cast(list[dict[str, object]], view_model["links"])
            if relevant_endpoint_ids.intersection(
                str(value) for value in cast(list[object], link.get("endpoint_ids", []))
            )
        ]
        view_model["port_links"] = [
            link for link in cast(list[dict[str, object]], view_model["port_links"])
            if relevant_endpoint_ids.intersection((
                str(link.get("input_endpoint_id", "")),
                str(link.get("output_endpoint_id", "")),
            ))
        ]
        view_model["internal_port_links"] = [
            link for link in cast(list[dict[str, object]], view_model["internal_port_links"])
            if relevant_endpoint_ids.intersection((
                str(link.get("input_endpoint_id", "")),
                str(link.get("output_endpoint_id", "")),
            ))
        ]
        for key in (
            "groups", "kafka_dtos", "project_dto_definitions",
            "mongo_persistence_classes", "resource_descriptions",
            "asyncapi_contracts", "indexing_issues",
        ):
            view_model[key] = []
        view_model["codeql_call_graph"] = {"nodes": [], "edges": []}
        view_model["call_graphs_only"] = True
    graph_data = json.dumps(
        view_model,
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    asyncapi_assets = ""
    if asyncapi_contracts and not call_graphs_only:
        asyncapi_assets = (
            '<script id="asyncapi-assets" type="application/octet-stream">'
            f"{_ASYNCAPI_COMPRESSED_ASSETS}</script>"
            f"<script>{_ASYNCAPI_LOADER_JS}</script>"
        )
    graph_css = _GRAPH_CSS
    if call_graphs_only:
        graph_css += """
          body.call-graphs-only .toolbar-mode-tabs,
          body.call-graphs-only #architecture-tabs-title,
          body.call-graphs-only .toolbar-tabs > button:not(#flows-tab),
          body.call-graphs-only .toolbar-panel:not(#flows-panel) { display: none !important; }
        """
    html = (
        _GRAPH_HTML_TEMPLATE.replace("__GRAPH_CSS__", graph_css)
        .replace("__GRAPH_JS__", _GRAPH_JS)
        .replace("__GRAPH_DATA__", graph_data)
        .replace("__LAYER_GEOMETRY__", _LAYER_GEOMETRY_JS)
        .replace("__ASYNCAPI_WEB_COMPONENT__", asyncapi_assets)
    )
    if call_graphs_only:
        html = html.replace("<body>", '<body class="call-graphs-only">')
    return html
