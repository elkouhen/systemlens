"""Read models for persisted potential code flows."""

from dataclasses import asdict

from systemlens.domain.code_flows import CodeFlow


def code_flow_summary(flow: CodeFlow) -> dict[str, object]:
    input_topics = [
        step.name for step in flow.steps if step.kind == "message_entry"
    ]
    output_topics = [
        step.name for step in flow.steps if step.kind == "message_publish"
    ]
    return {
        "id": flow.id,
        "module": flow.module,
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
    flows: list[CodeFlow], *, publishes_to_topic: bool = False
) -> list[dict[str, object]]:
    items = [code_flow_summary(flow) for flow in flows]
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
