"""Build browser-facing call-graph projections from indexed facts."""

from __future__ import annotations

from typing import cast

import networkx as nx

from systemlens.domain.code_flows import CodeFlow
from systemlens.domain.graph import GraphEdge
from systemlens.domain.models import MessageEndpoint


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
    service_order = {
        service: index for index, service in enumerate(sorted(endpoints_by_service))
    }
    graph = nx.MultiDiGraph()
    traversal_tree = nx.MultiDiGraph()
    seen_relation_keys: set[tuple[str, str, str, str | None]] = set()
    relation_discovery_order = 0
    trigger_kinds = {"http_entry", "message_entry", "cron_entry"}
    ordered_flows = sorted(
        flows,
        key=lambda flow: (flow.module, flow.path, flow.start_line, flow.id),
    )
    flow_by_id = {flow.id: flow for flow in ordered_flows}
    output_endpoint_ids_by_flow: dict[str, set[str]] = {}
    output_order_by_endpoint: dict[str, tuple[int, int]] = {}
    for flow_index, flow in enumerate(ordered_flows):
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
        nonlocal relation_discovery_order
        source_id = edge.from_endpoint.id
        target_id = edge.to_endpoint.id if edge.to_endpoint is not None else None
        source_service = service_by_endpoint.get(source_id)
        target_service = service_by_endpoint.get(target_id) if target_id else None
        if not source_service or not target_service or source_service == target_service:
            return None
        label = edge.from_endpoint.topic_display or edge.from_endpoint.topic
        key = (edge.kind, edge.from_endpoint.topic, source_id, target_id)
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
            discovery_order=relation_discovery_order,
        )
        relation_discovery_order += 1
        return key

    edges_by_output: dict[str, list[GraphEdge]] = {}
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

    def edge_sort_key(edge: GraphEdge) -> tuple[int, str, str, str, str]:
        target = edge.to_endpoint
        target_service = service_by_endpoint.get(target.id) if target is not None else None
        return (
            service_order.get(target_service or "", len(service_order)),
            edge.from_endpoint.topic,
            edge.kind,
            target.id if target is not None else "",
            edge.from_endpoint.id,
        )

    for output_id in edges_by_output:
        edges_by_output[output_id].sort(key=edge_sort_key)

    trigger_flow_ids_by_endpoint: dict[str, list[str]] = {}
    trigger_flow_ids: set[str] = set()
    for flow in ordered_flows:
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
    visited_flow_ids: set[str] = set()
    queued_flow_ids = set(frontier)
    root_modules: list[str] = []
    for flow_id in frontier:
        current_flow = flow_by_id.get(flow_id)
        if current_flow and current_flow.module and current_flow.module not in root_modules:
            root_modules.append(current_flow.module)
    traversal_tree.add_nodes_from(root_modules)
    tree_parent_modules: set[str] = set()
    while frontier:
        current_frontier = [
            flow_id for flow_id in frontier
            if flow_id in flow_by_id and flow_id not in visited_flow_ids
        ]
        if not current_frontier:
            break
        visited_flow_ids.update(current_frontier)
        next_frontier: list[str] = []
        next_frontier_set: set[str] = set()
        for flow_id in current_frontier:
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
                    target_flow_ids = trigger_flow_ids_by_endpoint.get(
                        target_endpoint_id or "", []
                    )
                    if target_flow_ids and (
                        target_module not in traversal_tree
                        and target_module not in tree_parent_modules
                        and target_module not in root_modules
                    ):
                        traversal_tree.add_node(module)
                        traversal_tree.add_node(target_module)
                        traversal_tree.add_edge(
                            module,
                            target_module,
                            key=graph_edge_key,
                            graph_edge_key=graph_edge_key,
                        )
                        tree_parent_modules.add(target_module)
                    for target_flow_id in target_flow_ids:
                        if (
                            target_flow_id not in visited_flow_ids
                            and target_flow_id not in queued_flow_ids
                            and target_flow_id not in next_frontier_set
                        ):
                            next_frontier.append(target_flow_id)
                            next_frontier_set.add(target_flow_id)
                            queued_flow_ids.add(target_flow_id)
        frontier = next_frontier

    # The tree is a spanning forest used for readable levels. The complete
    # graph remains the source of truth for arc numbering, so cycle and
    # cross-level arcs are retained and numbered as well.
    tree_roots = sorted(
        node for node in traversal_tree if traversal_tree.in_degree(node) == 0
    )
    tree_levels: list[list[str]] = []
    seen_tree_nodes: set[str] = set()
    tree_frontier = tree_roots
    while tree_frontier:
        level = [node for node in tree_frontier if node not in seen_tree_nodes]
        if not level:
            break
        tree_levels.append(level)
        seen_tree_nodes.update(level)
        tree_frontier = sorted({
            target
            for source in level
            for target in traversal_tree.successors(source)
            if target not in seen_tree_nodes
        })

    graph_roots = [module for module in root_modules if module in graph]
    graph_roots.extend(sorted(node for node in graph if node not in graph_roots))
    graph_levels: list[list[str]] = []
    seen_graph_nodes: set[str] = set()
    graph_frontier = graph_roots[:len(root_modules)]
    while graph_frontier:
        level = [node for node in graph_frontier if node not in seen_graph_nodes]
        if not level:
            break
        graph_levels.append(level)
        seen_graph_nodes.update(level)
        graph_frontier = sorted({
            target
            for source in level
            for target in graph.successors(source)
            if target not in seen_graph_nodes
        })
    graph_levels.extend([[node] for node in sorted(graph) if node not in seen_graph_nodes])
    traversal_levels = graph_levels

    # Number every complete-graph arc by source BFS level. Sorting by the
    # stable discovery key makes labels independent of input edge order.
    next_edge_order = 1
    for level in graph_levels:
        for module in level:
            outgoing = sorted(
                graph.out_edges(module, keys=True, data=True),
                key=lambda item: (
                    item[3].get("discovery_order", 0),
                    str(item[2]),
                ),
            )
            for _source, _target, graph_edge_key, graph_data in outgoing:
                graph_data["order"] = next_edge_order
                if traversal_tree.has_edge(module, _target, key=graph_edge_key):
                    traversal_tree.edges[module, _target, graph_edge_key]["order"] = next_edge_order
                next_edge_order += 1

    triggers: dict[str, list[dict[str, object]]] = {}
    for flow in ordered_flows:
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
            "levels": tree_levels or [[module] for module in root_modules if module in graph],
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
                edge_order = next_edge_order
                next_edge_order += 1
                graph.add_edge(
                    edge["source"], edge["target"],
                    kind=edge["kind"], label=edge["label"],
                    order=edge_order,
                    endpoint_ids=list(endpoint_ids),
                )
        reachable = {root} | nx.descendants(graph, root)
        graph = graph.subgraph(reachable).copy()
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
                first_edge = next(iter(sorted(
                    graph[source][target].items(), key=lambda item: str(item[0])
                )))
                key, data = first_edge
                tree.add_edge(source, target, key=key, **data)
        node_order = (
            list(nx.lexicographical_topological_sort(graph))
            if nx.is_directed_acyclic_graph(graph)
            else sorted(graph.nodes)
        )
        return {
            "nodes": sorted(graph.nodes),
            "node_order": node_order,
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
                    graph.edges(keys=True, data=True),
                    key=lambda item: int(item[3].get("order", 0)),
                )
            ],
            "traversal_levels": [
                list(level)
                for level in nx.bfs_layers(graph, root)
            ],
            "call_tree": {
                "levels": [list(level) for level in nx.bfs_layers(tree, root)],
                "edges": [
                    {
                        "source": source,
                        "target": target,
                        "order": int(data.get("order", 0)),
                    }
                    for source, target, _key, data in sorted(
                        tree.edges(keys=True, data=True),
                        key=lambda item: int(item[3].get("order", 0)),
                    )
                ],
            },
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
