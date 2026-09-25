"""Read models for persisted potential code flows."""

from dataclasses import asdict

from systemlens.domain.code_flows import CodeFlow
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
    flow: CodeFlow, endpoints: dict[str, MessageEndpoint] | None = None
) -> dict[str, object]:
    endpoint_by_id = endpoints or {}
    input_endpoint, output_endpoint = _endpoint_summary(flow, endpoint_by_id)
    target_modules = _target_modules(flow, list(endpoint_by_id.values()))
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


def _endpoints_compatible(
    source: MessageEndpoint, target: MessageEndpoint
) -> bool:
    return source.system == target.system and source.topic == target.topic


def _target_modules(
    flow: CodeFlow, endpoints: list[MessageEndpoint]
) -> list[str]:
    output_endpoints = {
        step.endpoint_id
        for step in flow.steps
        if step.endpoint_id and step.kind in {"http_call", "message_publish"}
    }
    outputs = [endpoint for endpoint in endpoints if endpoint.id in output_endpoints]
    target_roles = {"call": "serve", "produce": "consume"}
    modules = {
        endpoint.module
        for output in outputs
        for endpoint in endpoints
        if endpoint.role == target_roles.get(output.role)
        and _endpoints_compatible(output, endpoint)
        and endpoint.module
        and endpoint.module != flow.module
    }
    return sorted(modules)


def list_code_flows(
    flows: list[CodeFlow],
    endpoints: list[MessageEndpoint] | None = None,
    *,
    publishes_to_topic: bool = False,
) -> list[dict[str, object]]:
    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints or []}
    root_ids = _flow_root_ids(flows, endpoint_by_id)
    items = [code_flow_summary(flow, endpoint_by_id) for flow in flows]
    for flow, item in zip(flows, items, strict=True):
        item["root"] = flow.id in root_ids
    if publishes_to_topic:
        items = [item for item in items if item["output_topics"]]
    return items


def _flow_children(
    flow: CodeFlow,
    flows: list[CodeFlow],
    endpoints: dict[str, MessageEndpoint],
) -> list[CodeFlow]:
    output_ids = {
        step.endpoint_id
        for step in flow.steps
        if step.endpoint_id and step.kind in {"http_call", "message_publish"}
    }
    output_endpoints = [
        endpoints[endpoint_id] for endpoint_id in output_ids if endpoint_id in endpoints
    ]
    children: list[CodeFlow] = []
    for candidate in flows:
        if candidate.id == flow.id or not candidate.steps:
            continue
        first_step = candidate.steps[0]
        if first_step.kind not in {"http_entry", "message_entry"} or not first_step.endpoint_id:
            continue
        input_endpoint = endpoints.get(first_step.endpoint_id)
        if input_endpoint is None:
            continue
        if any(
            _endpoints_compatible(output, input_endpoint)
            and output.role in {"produce", "call"}
            for output in output_endpoints
        ):
            children.append(candidate)
    return sorted(
        children,
        key=lambda item: (item.module, item.path, item.start_line, item.id),
    )


def _flow_root_ids(
    flows: list[CodeFlow], endpoints: dict[str, MessageEndpoint]
) -> set[str]:
    incoming = {
        child.id
        for flow in flows
        for child in _flow_children(flow, flows, endpoints)
    }
    return {flow.id for flow in flows if flow.id not in incoming}


def _flow_tree(
    flow: CodeFlow,
    flows: list[CodeFlow],
    endpoints: dict[str, MessageEndpoint],
    root_ids: set[str],
    visited: set[str] | None = None,
) -> dict[str, object]:
    visited = set() if visited is None else visited
    visited.add(flow.id)
    children = []
    for child in _flow_children(flow, flows, endpoints):
        if child.id in visited:
            continue
        children.append(_flow_tree(child, flows, endpoints, root_ids, visited))
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
    root_ids = _flow_root_ids(flows, endpoint_by_id)
    item["root"] = matches[0].id in root_ids
    item["tree"] = _flow_tree(matches[0], flows, endpoint_by_id, root_ids)
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
