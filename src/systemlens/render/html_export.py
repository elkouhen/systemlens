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
    root_flow_ids: set[str] | None = None,
) -> dict[str, object]:
    """Build the port graph by following internal flow outputs.

    Candidate arcs are built from flow-associated OUT ports. Traversal starts
    at trigger flows and follows each OUT-to-IN arc to the flow triggered by
    its target IN port. This keeps fan-in, fan-out and cycles while avoiding
    unrelated flows.
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
    traversal_tree = nx.MultiDiGraph()
    seen_relation_keys: set[tuple[str, str, str, str | None]] = set()
    trigger_kinds = {"http_entry", "message_entry", "cron_entry"}
    flow_by_id = {flow.id: flow for flow in flows}
    output_endpoint_ids_by_flow: dict[str, set[str]] = {}
    output_order_by_endpoint: dict[str, tuple[int, int]] = {}
    for flow_index, flow in enumerate(flows):
        if not flow.module:
            continue
        for step in flow.steps:
            if not step.endpoint_id:
                continue
            endpoint = endpoint_by_id.get(step.endpoint_id)
            if endpoint and endpoint.role in {"call", "produce"}:
                output_endpoint_ids_by_flow.setdefault(flow.id, set()).add(endpoint.id)
                output_order_by_endpoint.setdefault(
                    endpoint.id, (flow_index, step.order)
                )

    def add_relation(edge: GraphEdge) -> tuple[str, str, str, str | None] | None:
        source_id = edge.from_endpoint.id
        target_id = edge.to_endpoint.id if edge.to_endpoint is not None else None
        source_service = service_by_endpoint.get(source_id)
        target_service = service_by_endpoint.get(target_id) if target_id else None
        if not source_service or not target_service or source_service == target_service:
            return None
        label = edge.from_endpoint.topic
        key = (edge.kind, label, source_id, target_id)
        if key in seen_relation_keys:
            return None
        seen_relation_keys.add(key)
        graph.add_edge(
            source_service,
            target_service,
            key=key,
            kind=edge.kind,
            label=label,
            endpoint_ids=[source_id, target_id],
        )
        return key

    edges_by_output: dict[str, list[GraphEdge]] = {}
    edge_keys_by_output: dict[str, set[tuple[str, str]]] = {}
    for edge in edges:
        source_role = (edge.from_endpoint.system, edge.from_endpoint.role)
        target_role = (
            (edge.to_endpoint.system, edge.to_endpoint.role)
            if edge.to_endpoint is not None else None
        )
        if (
            source_role in {("rest", "call"), ("kafka", "produce")}
            and target_role in {("rest", "serve"), ("kafka", "consume")}
        ):
            edges_by_output.setdefault(edge.from_endpoint.id, []).append(edge)
            edge_keys_by_output.setdefault(edge.from_endpoint.id, set()).add(
                (edge.kind, edge.to_endpoint.id if edge.to_endpoint else "")
            )

    # Keep the flow view aligned with `flows show`: an OUT endpoint can reach
    # every opposite-role IN endpoint with the same protocol and topic/route.
    # The architecture graph may omit such an endpoint pair when its broader
    # topology relation was not materialized, but the persisted flow evidence
    # still makes the pair observable in the flow tree.
    for source_id, source in endpoint_by_id.items():
        source_service = service_by_endpoint.get(source_id)
        synthetic_target_role = {"call": "serve", "produce": "consume"}.get(source.role)
        if not source_service or synthetic_target_role is None:
            continue
        for target_id, target in endpoint_by_id.items():
            target_service = service_by_endpoint.get(target_id)
            if (
                target.role != synthetic_target_role
                or target.system != source.system
                or target.topic != source.topic
                or not target_service
                or target_service == source_service
            ):
                continue
            kind = "rest" if source.system == "rest" else "kafka"
            edge_key = (kind, target_id)
            if edge_key in edge_keys_by_output.setdefault(source_id, set()):
                continue
            edge_keys_by_output[source_id].add(edge_key)
            edges_by_output.setdefault(source_id, []).append(
                GraphEdge(kind, source_service, target_service, source, target)
            )

    trigger_flow_ids_by_endpoint: dict[str, list[str]] = {}
    trigger_flow_ids: set[str] = set()
    for flow in flows:
        if not flow.steps or flow.steps[0].kind not in trigger_kinds:
            continue
        trigger_flow_ids.add(flow.id)
        endpoint_id = flow.steps[0].endpoint_id
        if endpoint_id:
            trigger_flow_ids_by_endpoint.setdefault(endpoint_id, []).append(flow.id)

    incoming_flow_ids = {
        target_flow_id
        for output_endpoint_ids in output_endpoint_ids_by_flow.values()
        for endpoint_id in output_endpoint_ids
        for edge in edges_by_output.get(endpoint_id, [])
        for target_flow_id in trigger_flow_ids_by_endpoint.get(
            edge.to_endpoint.id if edge.to_endpoint is not None else "", []
        )
    }
    default_root_flow_ids = trigger_flow_ids - incoming_flow_ids
    frontier = sorted(
        root_flow_ids if root_flow_ids is not None else default_root_flow_ids
    )
    if not frontier:
        # A cycle without an external trigger has no natural flow root.
        frontier = sorted(root_flow_ids or trigger_flow_ids or flow_by_id)
    traversal_levels: list[list[str]] = []
    expanded_output_ids: set[str] = set()
    while frontier:
        traversal_levels.append([
            flow_by_id[flow_id].module
            for flow_id in frontier
            if flow_id in flow_by_id and flow_by_id[flow_id].module
        ])
        next_frontier: list[str] = []
        next_frontier_set: set[str] = set()
        for flow_id in frontier:
            current_flow = flow_by_id.get(flow_id)
            if current_flow is None or not current_flow.module:
                continue
            module = current_flow.module
            graph.add_node(module)
            for endpoint_id in sorted(
                output_endpoint_ids_by_flow.get(flow_id, set()),
                key=lambda endpoint_id: (output_order_by_endpoint[endpoint_id], endpoint_id),
            ):
                if endpoint_id in expanded_output_ids:
                    continue
                expanded_output_ids.add(endpoint_id)
                if service_by_endpoint.get(endpoint_id) != module:
                    continue
                for edge in edges_by_output.get(endpoint_id, []):
                    target_endpoint_id = (
                        edge.to_endpoint.id if edge.to_endpoint is not None else None
                    )
                    target_module = (
                        service_by_endpoint.get(target_endpoint_id)
                        if target_endpoint_id
                        else None
                    )
                    if not target_module or target_module == module:
                        continue
                    graph_edge_key = add_relation(edge)
                    if graph_edge_key is None:
                        continue
                    target_flow_ids = (
                        trigger_flow_ids_by_endpoint.get(target_id, [])
                        if target_id
                        else []
                    )
                    if not target_flow_ids and target_id and edge.to_endpoint is not None:
                        target_flow_ids = [
                            candidate.id
                            for candidate in flows
                            if candidate.module == target_module
                            and candidate.steps
                            and candidate.steps[0].endpoint_id
                            and endpoint_by_id.get(candidate.steps[0].endpoint_id)
                            and endpoint_by_id[candidate.steps[0].endpoint_id].topic
                            == edge.to_endpoint.topic
                        ]
                    if target_flow_ids:
                        traversal_tree.add_edge(
                            module,
                            target_module,
                            key=graph_edge_key,
                            graph_edge_key=graph_edge_key,
                        )
                    for target_flow_id in (
                        target_flow_ids
                    ):
                        if target_flow_id not in next_frontier_set:
                            next_frontier.append(target_flow_id)
                            next_frontier_set.add(target_flow_id)
        frontier = next_frontier

    # The tree contains the actual module arcs; derive its levels after flow
    # discovery so modules reached through several flow fragments are not lost
    # from the serialized traversal metadata.
    tree_frontier = sorted(
        node for node in traversal_tree if traversal_tree.in_degree(node) == 0
    )
    tree_levels: list[list[str]] = []
    while tree_frontier:
        tree_levels.append(tree_frontier)
        tree_frontier = sorted(
            target
            for source in tree_frontier
            for target in traversal_tree.successors(source)
            if all(
                predecessor in sum(tree_levels, [])
                for predecessor in traversal_tree.predecessors(target)
            )
        )
    if tree_levels:
        traversal_levels = tree_levels

    # The tree/forest is the source of truth for numbering. It is traversed
    # level by level, then its labels are copied to the corresponding graph
    # arcs. This keeps the final graph complete without making its topology
    # responsible for traversal order.
    next_edge_order = 1
    for level in traversal_levels:
        for module in level:
            for _source, _target, tree_key, tree_data in traversal_tree.out_edges(
                module, keys=True, data=True
            ):
                graph_edge_key = tree_data["graph_edge_key"]
                graph.edges[module, _target, graph_edge_key]["order"] = next_edge_order
                traversal_tree.edges[module, _target, tree_key]["order"] = next_edge_order
                next_edge_order += 1

    triggers: dict[str, list[dict[str, object]]] = {}
    for flow in flows:
        if not flow.steps or flow.steps[0].kind not in trigger_kinds:
            continue
        trigger = flow.steps[0]
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
        "traversal_levels": traversal_levels,
        "call_tree": {
            "levels": traversal_levels,
            "edges": [
                {
                    "source": source,
                    "target": target,
                    "order": int(data["order"]),
                }
                for source, target, _key, data in traversal_tree.edges(
                    keys=True, data=True
                )
            ],
        },
        "edges": [
            {
                "source": source,
                "target": target,
                "kind": str(data.get("kind", "")),
                "label": str(data.get("label", "")),
                "order": int(data.get("order", 0)),
                "endpoint_ids": list(data.get("endpoint_ids", [])),
            }
            for source, target, _key, data in sorted(
                graph.edges(keys=True, data=True),
                key=lambda item: int(item[3].get("order", 0)),
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
        call_graph = _networkx_call_graph(
            flows, endpoints_by_service, edges, root_flow_ids={flow.id}
        )
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
        next_edge_order = 1
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
                source = str(edge["source"])
                edge_order = next_edge_order
                next_edge_order += 1
                graph.add_edge(
                    edge["source"], edge["target"],
                    kind=edge["kind"], label=edge["label"],
                    order=edge_order,
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
                    "order": int(data.get("order", 0)),
                    "endpoint_ids": list(cast(list[object], data.get("endpoint_ids", []))),
                }
                for source, target, _key, data in sorted(
                    tree.edges(keys=True, data=True),
                    key=lambda item: int(item[3].get("order", 0)),
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


def _all_export_flows(
    flows: list[CodeFlow],
    endpoints_by_service: dict[str, list[MessageEndpoint]],
    edges: list[GraphEdge],
) -> list[tuple[CodeFlow, dict[str, object], int]]:
    """Build one HTML entry for every persisted flow.

    The CLI exposes persisted flows individually. The HTML picker follows the
    same contract; graph-arc deduplication remains inside each call graph.
    """
    return sorted(
        [
            (
                flow,
                _networkx_call_graph(
                    flows, endpoints_by_service, edges, root_flow_ids={flow.id}
                ),
                1,
            )
            for flow in flows
        ],
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

    call_graphs: dict[str, dict[str, object]] = {}
    graph_ids_by_json: dict[str, str] = {}
    export_flow_items: list[tuple[CodeFlow, dict[str, object], int, str]] = []
    for flow, call_graph, equivalent_count in _all_export_flows(
        list(code_flows or []), endpoints_by_service, edges
    ):
        graph_json = json.dumps(call_graph, sort_keys=True, separators=(",", ":"))
        call_graph_id = graph_ids_by_json.get(graph_json)
        if call_graph_id is None:
            call_graph_id = f"call-graph-{len(call_graphs) + 1}"
            graph_ids_by_json[graph_json] = call_graph_id
            call_graphs[call_graph_id] = call_graph
        export_flow_items.append((flow, call_graph, equivalent_count, call_graph_id))

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
            "call_graph_id": call_graph_id,
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
        for flow, _call_graph, equivalent_count, call_graph_id in export_flow_items
    ]
    view_model["code_flows"] = serialized_code_flows
    view_model["call_graphs"] = call_graphs
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
    ).replace("<", "\\u003c")
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
