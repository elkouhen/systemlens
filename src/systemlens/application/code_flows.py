"""Read models for persisted potential code flows."""

from collections import defaultdict
from dataclasses import asdict

from systemlens.domain.code_flows import CodeFlow
from systemlens.domain.graph import GraphEdge
from systemlens.domain.models import MessageEndpoint


def _endpoint_summary(
    flow: CodeFlow, endpoints: dict[str, MessageEndpoint]
) -> tuple[MessageEndpoint | None, MessageEndpoint | None]:
    endpoint_steps = [step for step in flow.steps if step.endpoint_id]
    if not endpoint_steps:
        return None, None
    first = endpoints.get(endpoint_steps[0].endpoint_id or "")
    last = endpoints.get(endpoint_steps[-1].endpoint_id or "")
    return first, last


def code_flow_summary(
    flow: CodeFlow,
    endpoints: dict[str, MessageEndpoint] | None = None,
    targets_by_key: dict[tuple[str, str, str], set[str]] | None = None,
) -> dict[str, object]:
    endpoint_by_id = endpoints or {}
    input_endpoint, output_endpoint = _endpoint_summary(flow, endpoint_by_id)
    target_modules = _target_modules(flow, endpoint_by_id, targets_by_key)
    input_topics = [
        step.name for step in flow.steps if step.kind == "message_entry"
    ]
    output_topics = [
        step.name for step in flow.steps if step.kind == "message_publish"
    ]
    return {
        "id": flow.id,
        "module": flow.module,
        "input_flow": input_endpoint.topic if input_endpoint else None,
        "input_java_type": input_endpoint.message_type if input_endpoint else None,
        "output_flow": output_endpoint.topic if output_endpoint else None,
        "output_java_type": output_endpoint.message_type if output_endpoint else None,
        "target_modules": target_modules,
        "method": flow.method,
        "root": _is_root_flow(flow),
        "trigger": {"kind": flow.steps[0].kind, "name": flow.steps[0].name},
        "input_topic": input_topics[0] if input_topics else None,
        "output_topics": output_topics,
        "effects": len(flow.steps) - 1,
        "status": flow.status,
        "confidence": flow.confidence,
        "reconciliation": flow.reconciliation,
        "alternative_count": flow.alternative_count,
    }


def _is_root_flow(flow: CodeFlow) -> bool:
    """Return whether a flow starts at an external trigger.

    Kafka consumer flows are continuation flows in the persisted flow model;
    HTTP and scheduled entries are external triggers. Keeping this derived
    avoids changing the SQLite schema while making the distinction explicit in
    read-only exports.
    """
    return bool(flow.steps) and flow.steps[0].kind != "message_entry"


def _target_modules(
    flow: CodeFlow,
    endpoints: dict[str, MessageEndpoint],
    targets_by_key: dict[tuple[str, str, str], set[str]] | None = None,
) -> list[str]:
    output_endpoints = {
        step.endpoint_id
        for step in flow.steps
        if step.endpoint_id and step.kind in {"http_call", "message_publish"}
    }
    outputs = [endpoint for endpoint_id in output_endpoints if (endpoint := endpoints.get(endpoint_id))]
    target_roles = {"call": "serve", "produce": "consume"}
    if targets_by_key is None:
        targets_by_key = _target_modules_index(endpoints)
    modules = {
        module
        for output in outputs
        for module in targets_by_key.get(
            (output.system, output.topic, target_roles[output.role]), ()
        )
        if module != flow.module
    }
    return sorted(modules)


def _target_modules_index(
    endpoints: dict[str, MessageEndpoint],
) -> dict[tuple[str, str, str], set[str]]:
    index: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for endpoint in endpoints.values():
        if endpoint.module and endpoint.role in {"serve", "consume"}:
            index[(endpoint.system, endpoint.topic, endpoint.role)].add(endpoint.module)
    return index


def list_code_flows(
    flows: list[CodeFlow],
    endpoints: list[MessageEndpoint] | None = None,
    *,
    publishes_to_topic: bool = False,
) -> list[dict[str, object]]:
    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints or []}
    flow_index = _build_flow_index(flows, endpoint_by_id)
    root_ids = _flow_root_ids(flows, endpoint_by_id, flow_index)
    targets_by_key = _target_modules_index(endpoint_by_id)
    items = []
    for flow in flows:
        item = code_flow_summary(flow, endpoint_by_id, targets_by_key)
        items.append(item)
    for flow, item in zip(flows, items, strict=True):
        item["root"] = flow.id in root_ids
    if publishes_to_topic:
        items = [item for item in items if item["output_topics"]]
    return items


def internal_flow_stats(edges: list[GraphEdge]) -> dict[str, int]:
    """Count persisted cross-module HTTP and Kafka connections.

    An edge is counted only when both endpoint sides are indexed. Dynamic or
    targetless configured integrations remain outside these internal totals.
    """
    http = sum(
        1
        for edge in edges
        if edge.kind == "rest"
        and edge.to_endpoint is not None
        and edge.from_service != edge.to_service
    )
    kafka = sum(
        1
        for edge in edges
        if edge.kind == "kafka"
        and edge.to_endpoint is not None
        and edge.from_service != edge.to_service
    )
    return {
        "http_internal_connections": http,
        "kafka_internal_connections": kafka,
        "internal_connections": http + kafka,
    }


def render_internal_flow_stats_text(stats: dict[str, int]) -> str:
    return (
        f"HTTP internal connections: {stats['http_internal_connections']}\n"
        f"Kafka internal connections: {stats['kafka_internal_connections']}\n"
        f"Total internal connections: {stats['internal_connections']}"
    )


def _flow_children(
    flow: CodeFlow,
    flows: list[CodeFlow],
    endpoints: dict[str, MessageEndpoint],
    flow_index: dict[tuple[str, str], list[CodeFlow]] | None = None,
) -> list[CodeFlow]:
    output_ids = {
        step.endpoint_id
        for step in flow.steps
        if step.endpoint_id and step.kind in {"http_call", "message_publish"}
    }
    output_endpoints = [endpoints[endpoint_id] for endpoint_id in output_ids if endpoint_id in endpoints]
    if flow_index is None:
        flow_index = _build_flow_index(flows, endpoints)
    target_roles = {"call": "serve", "produce": "consume"}
    children_by_id: dict[str, CodeFlow] = {}
    for output in output_endpoints:
        for candidate in flow_index.get((output.system, output.topic), ()):
            if candidate.id == flow.id or not candidate.steps:
                continue
            first_endpoint = endpoints.get(candidate.steps[0].endpoint_id or "")
            if first_endpoint is not None and first_endpoint.role == target_roles[output.role]:
                children_by_id[candidate.id] = candidate
    children = list(children_by_id.values())
    return sorted(
        children,
        key=lambda item: (item.module, item.path, item.start_line, item.id),
    )


def _flow_root_ids(
    flows: list[CodeFlow],
    endpoints: dict[str, MessageEndpoint],
    flow_index: dict[tuple[str, str], list[CodeFlow]] | None = None,
) -> set[str]:
    incoming = {
        child.id
        for flow in flows
        for child in _flow_children(flow, flows, endpoints, flow_index)
    }
    return {flow.id for flow in flows if flow.id not in incoming}


def _build_flow_index(
    flows: list[CodeFlow], endpoints: dict[str, MessageEndpoint]
) -> dict[tuple[str, str], list[CodeFlow]]:
    index: dict[tuple[str, str], list[CodeFlow]] = defaultdict(list)
    for flow in flows:
        if not flow.steps or flow.steps[0].kind not in {"http_entry", "message_entry"}:
            continue
        endpoint = endpoints.get(flow.steps[0].endpoint_id or "")
        if endpoint is not None:
            index[(endpoint.system, endpoint.topic)].append(flow)
    return index


def _flow_tree(
    flow: CodeFlow,
    flows: list[CodeFlow],
    endpoints: dict[str, MessageEndpoint],
    root_ids: set[str],
    flow_index: dict[tuple[str, str], list[CodeFlow]] | None = None,
    visited: set[str] | None = None,
) -> dict[str, object]:
    visited = set() if visited is None else visited
    visited.add(flow.id)
    children = []
    for child in _flow_children(flow, flows, endpoints, flow_index):
        if child.id in visited:
            continue
        children.append(_flow_tree(child, flows, endpoints, root_ids, flow_index, visited))
    return {
        "id": flow.id,
        "module": flow.module,
        "method": flow.method,
        "root": flow.id in root_ids,
        "trigger": (
            {
                "kind": flow.steps[0].kind,
                "name": flow.steps[0].name,
            }
            if flow.steps
            else None
        ),
        "children": children,
    }


def show_code_flow(
    flows: list[CodeFlow],
    flow_id: str,
    endpoints: list[MessageEndpoint] | None = None,
) -> dict[str, object] | None:
    matches = [flow for flow in flows if flow.id == flow_id]
    if not matches:
        folded = flow_id.casefold()
        matches = [
            flow
            for flow in flows
            if folded in flow.method.casefold() or folded in flow.steps[0].name.casefold()
        ]
    if len(matches) != 1:
        return None
    item = asdict(matches[0])
    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints or []}
    flow_index = _build_flow_index(flows, endpoint_by_id)
    root_ids = _flow_root_ids(flows, endpoint_by_id, flow_index)
    item["root"] = matches[0].id in root_ids
    item["tree"] = _flow_tree(matches[0], flows, endpoint_by_id, root_ids, flow_index)
    return item


def render_code_flows_text(items: list[dict[str, object]]) -> str:
    if not items:
        return "No potential code flows indexed."
    lines: list[str] = []
    for item in items:
        trigger = item["trigger"]
        assert isinstance(trigger, dict)
        line = (
            f"{item['id']}  {item['module']}  "
            f"{trigger['kind']} {trigger['name']} -> {item['effects']} effect(s)"
        )
        output_topics = item["output_topics"]
        input_topic = item["input_topic"]
        assert isinstance(output_topics, list)
        if input_topic is not None or output_topics:
            line += f"  Kafka in={input_topic or '-'} out={','.join(output_topics) or '-'}"
        line += (
            f"  module={item['module']}"
            f" in={item['input_flow'] or '-'}"
            f" in_type={item['input_java_type'] or '-'}"
            f" out={item['output_flow'] or '-'}"
            f" out_type={item['output_java_type'] or '-'}"
        )
        lines.append(line)
    return "\n".join(lines)


def render_code_flow_text(item: dict[str, object]) -> str:
    steps = item["steps"]
    assert isinstance(steps, tuple | list)
    lines = [
        f"Potential code flow {item['id']}  "
        f"root={str(item.get('root', False)).lower()}",
        f"Method: {item['method']}",
        f"Confidence: {item['confidence']} — {item['reason']}",
        f"Topology reconciliation: {item['reconciliation']}",
    ]
    for step in steps:
        assert isinstance(step, dict)
        lines.append(
            f"  {step['order']}. {step['kind']} {step['name']} "
            f"({step['path']}:{step['start_line']})"
        )
    tree = item.get("tree")
    if isinstance(tree, dict):
        lines.append("Call tree:")
        _render_flow_tree_text(tree, lines)
    return "\n".join(lines)


def _render_flow_tree_text(
    tree: dict[str, object], lines: list[str], prefix: str = "", branch: str = ""
) -> None:
    trigger = tree.get("trigger")
    trigger_text = ""
    if isinstance(trigger, dict):
        trigger_text = f" [{trigger.get('kind')}: {trigger.get('name')}]"
    root_text = " root" if tree.get("root") else ""
    lines.append(
        f"{prefix}{branch}{tree['id']}  "
        f"{tree['module']}::{tree['method']}{root_text}{trigger_text}"
    )
    children = tree.get("children")
    if not isinstance(children, list):
        return
    child_prefix = prefix if not branch else prefix + ("    " if branch == "└── " else "│   ")
    for index, child in enumerate(children):
        if not isinstance(child, dict):
            continue
        child_branch = "└── " if index == len(children) - 1 else "├── "
        _render_flow_tree_text(child, lines, child_prefix, child_branch)
