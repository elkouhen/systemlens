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
        "method": flow.method,
        "trigger": {"kind": flow.steps[0].kind, "name": flow.steps[0].name},
        "input_topic": input_topics[0] if input_topics else None,
        "output_topics": output_topics,
        "effects": len(flow.steps) - 1,
        "status": flow.status,
        "confidence": flow.confidence,
        "reconciliation": flow.reconciliation,
        "alternative_count": flow.alternative_count,
    }


def list_code_flows(
    flows: list[CodeFlow],
    endpoints: list[MessageEndpoint] | None = None,
    *,
    publishes_to_topic: bool = False,
) -> list[dict[str, object]]:
    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints or []}
    items = [code_flow_summary(flow, endpoint_by_id) for flow in flows]
    if publishes_to_topic:
        items = [item for item in items if item["output_topics"]]
    return items


def show_code_flow(flows: list[CodeFlow], flow_id: str) -> dict[str, object] | None:
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
    return asdict(matches[0])


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
        f"Potential code flow {item['id']}",
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
    return "\n".join(lines)
