"""Read models for persisted potential code flows."""

from collections import defaultdict, deque
from dataclasses import asdict
import re

from systemlens.domain.code_flows import CodeFlow, CodeQLCallGraphEdge, IntegrationMethod
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
    module: str | None = None,
) -> list[dict[str, object]]:
    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints or []}
    flow_index = _build_flow_index(flows, endpoint_by_id)
    root_ids = _flow_root_ids(flows, endpoint_by_id, flow_index)
    targets_by_key = _target_modules_index(endpoint_by_id)
    items = []
    for flow in flows:
        if module is not None and not _is_internal_module_flow(
            flow, endpoint_by_id, module
        ):
            continue
        item = code_flow_summary(flow, endpoint_by_id, targets_by_key)
        items.append(item)
    selected_flows = [
        flow for flow in flows
        if module is None or _is_internal_module_flow(flow, endpoint_by_id, module)
    ]
    for flow, item in zip(selected_flows, items, strict=True):
        item["root"] = flow.id in root_ids
    if publishes_to_topic:
        items = [item for item in items if item["output_topics"]]
    return items


def _is_internal_module_flow(
    flow: CodeFlow,
    endpoints: dict[str, MessageEndpoint],
    module: str,
) -> bool:
    """Return whether a persisted flow stays within one indexed module.

    A flow is owned by its entry method's module. Endpoint steps without a
    module are not treated as evidence of a cross-module path; an explicitly
    indexed endpoint in another module is required to exclude the flow.
    """
    if flow.module != module:
        return False
    endpoint_steps = [
        step for step in flow.steps
        if step.endpoint_id and step.endpoint_id in endpoints
    ]
    if not endpoint_steps:
        return False
    first_endpoint = endpoints[endpoint_steps[0].endpoint_id or ""]
    last_endpoint = endpoints[endpoint_steps[-1].endpoint_id or ""]
    if first_endpoint.role not in {"serve", "consume"}:
        return False
    if last_endpoint.role not in {"call", "produce"}:
        return False
    involved_modules = {
        endpoint.module
        for step in flow.steps
        if step.endpoint_id
        and (endpoint := endpoints.get(step.endpoint_id)) is not None
        and endpoint.module is not None
    }
    return involved_modules <= {module}


def _flow_construction(
    flow: CodeFlow, endpoints: dict[str, MessageEndpoint]
) -> dict[str, object]:
    """Explain one persisted flow using only its stored source evidence."""
    steps = []
    for step in flow.steps:
        endpoint = endpoints.get(step.endpoint_id or "") if step.endpoint_id else None
        port = (
            "IN" if endpoint is not None and endpoint.role in {"serve", "consume"}
            else "OUT" if endpoint is not None and endpoint.role in {"call", "produce"}
            else "CALL" if step.kind == "method_call"
            else "STEP"
        )
        steps.append({
            "order": step.order,
            "port": port,
            "kind": step.kind,
            "name": step.name,
            "path": step.path,
            "start_line": step.start_line,
            "end_line": step.end_line,
            "endpoint_id": step.endpoint_id,
            "endpoint_module": endpoint.module if endpoint is not None else None,
        })
    method_calls = [step for step in flow.steps if step.kind == "method_call"]
    endpoint_steps = [step for step in steps if step["port"] in {"IN", "OUT"}]
    input_name = str(endpoint_steps[0]["name"]) if endpoint_steps else "IN"
    output_name = str(endpoint_steps[-1]["name"]) if endpoint_steps else "OUT"
    output_endpoint = (
        endpoints.get(str(endpoint_steps[-1]["endpoint_id"]))
        if endpoint_steps and endpoint_steps[-1]["endpoint_id"] is not None
        else None
    )
    external_call = _invocation_name(output_endpoint.snippet) if output_endpoint else None
    call_chain = [f"IN {input_name}", *[step.name for step in method_calls]]
    if external_call is not None:
        call_chain.append(external_call)
    call_chain.append(f"OUT {output_name}")
    return {
        "found": True,
        "flow_id": flow.id,
        "method": flow.method,
        "source": {"path": flow.path, "start_line": flow.start_line, "end_line": flow.end_line},
        "steps": steps,
        "construction": " -> ".join(str(item["port"]) for item in steps),
        "chain": " -> ".join(call_chain),
        "chain_type": "interprocedural" if method_calls else "direct_external_call",
        "external_call": (
            {
                "name": external_call,
                "path": output_endpoint.path,
                "line": output_endpoint.start_line,
            }
            if external_call is not None and output_endpoint is not None
            else None
        ),
        "method_call_count": len(method_calls),
        "confidence": flow.confidence,
        "reason": flow.reason,
    }


def internal_flow_debug(
    flows: list[CodeFlow], endpoints: list[MessageEndpoint], module: str,
    integration_methods: list[IntegrationMethod] | None = None,
    codeql_call_edges: list[CodeQLCallGraphEdge] | None = None,
) -> dict[str, object]:
    """Describe found or missing internal flows for one module."""
    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints}
    graph_mode = integration_methods is not None and codeql_call_edges is not None
    if graph_mode:
        assert integration_methods is not None and codeql_call_edges is not None
        selected = _call_graph_flow_examples(
            endpoints, module, integration_methods, codeql_call_edges
        )
        selected_keys: set[tuple[object, object]] = set()
        for item in selected:
            input_item = item.get("input")
            output_item = item.get("output")
            if isinstance(input_item, dict) and isinstance(output_item, dict):
                selected_keys.add((input_item.get("id"), output_item.get("id")))
        for flow in flows:
            if not _is_internal_module_flow(flow, endpoint_by_id, module):
                continue
            if not any(step.kind == "method_call" for step in flow.steps):
                continue
            endpoint_steps = [step for step in flow.steps if step.endpoint_id]
            if len(endpoint_steps) < 2:
                continue
            key = (endpoint_steps[0].endpoint_id, endpoint_steps[-1].endpoint_id)
            if key in selected_keys:
                continue
            selected.append(_flow_construction(flow, endpoint_by_id))
            selected_keys.add(key)
    else:
        selected = [
            _flow_construction(flow, endpoint_by_id)
            for flow in flows
            if _is_internal_module_flow(flow, endpoint_by_id, module)
        ]
    module_flows = [flow for flow in flows if flow.module == module]
    selected_ids = {
        item["flow_id"] for item in selected if isinstance(item, dict) and "flow_id" in item
    }
    excluded = (
        []
        if graph_mode
        else [flow for flow in module_flows if flow.id not in selected_ids]
    )
    module_endpoints = [endpoint for endpoint in endpoints if endpoint.module == module]
    inputs = sorted(
        f"{endpoint.system}:{endpoint.topic}"
        for endpoint in module_endpoints
        if endpoint.role in {"serve", "consume"}
    )
    outputs = sorted(
        f"{endpoint.system}:{endpoint.topic}"
        for endpoint in module_endpoints
        if endpoint.role in {"call", "produce"}
    )
    return {
        "module": module,
        "found": bool(selected),
        "flow_count": len(selected),
        "flows": selected,
        "excluded_flows": [
            {
                "flow_id": flow.id,
                "method": flow.method,
                "reason": "Le parcours ne commence pas par un port IN et ne se termine pas par un port OUT.",
                "construction": " -> ".join(step.kind for step in flow.steps),
            }
            for flow in sorted(excluded, key=lambda item: (item.path, item.start_line, item.id))
        ],
        "indexed_inputs": inputs,
        "indexed_outputs": outputs,
        "not_found_reason": (
            None
            if selected
            else (
                "Aucun chemin interprocédural CodeQL ne relie une entrée indexée "
                "à un effet indexé dans ce module."
                if graph_mode
                else "Aucun chemin source-backed ne relie une entrée indexée à un effet indexé dans ce module."
            )
        ),
    }


def _call_graph_flow_examples(
    endpoints: list[MessageEndpoint],
    module: str,
    methods: list[IntegrationMethod],
    edges: list[CodeQLCallGraphEdge],
) -> list[dict[str, object]]:
    """Find one Java call-graph path for every internal IN-to-OUT pair."""
    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints}
    methods_by_id = {method.id: method for method in methods}
    module_methods = {
        method.id for method in methods if method.module == module
    }
    inputs = [
        (method, endpoint)
        for method in methods
        if method.id in module_methods
        for endpoint_id in method.input_endpoint_ids
        if (endpoint := endpoint_by_id.get(endpoint_id)) is not None
    ]
    outputs = [
        (method, endpoint)
        for method in methods
        if method.id in module_methods
        for endpoint_id in method.output_endpoint_ids
        if (endpoint := endpoint_by_id.get(endpoint_id)) is not None
    ]
    adjacency: dict[str, list[CodeQLCallGraphEdge]] = defaultdict(list)
    for edge in edges:
        if edge.caller_id in methods_by_id and edge.callee_id in methods_by_id:
            adjacency[edge.caller_id].append(edge)
    examples: list[dict[str, object]] = []
    for input_method, input_endpoint in sorted(inputs, key=lambda item: item[1].id):
        for output_method, output_endpoint in sorted(outputs, key=lambda item: item[1].id):
            method_path = _shortest_method_path(
                input_method.id,
                output_method.id,
                adjacency,
                methods_by_id,
                input_method.qualified_method,
            )
            if method_path is None:
                continue
            method_ids, path_edges = method_path
            path_methods = [methods_by_id[method_id] for method_id in method_ids]
            external_call = _invocation_name(output_endpoint.snippet)
            chain_parts = [
                f"IN {input_endpoint.topic}",
                *[method.qualified_method for method in path_methods],
            ]
            if external_call is not None:
                chain_parts.append(external_call)
            chain_parts.append(f"OUT {output_endpoint.topic}")
            examples.append({
                "found": True,
                "flow_id": f"{input_endpoint.id}->{output_endpoint.id}",
                "input": _port_evidence(input_endpoint),
                "output": _port_evidence(output_endpoint),
                "chain_type": "interprocedural" if path_edges else "direct_external_call",
                "method_call_count": len(path_edges),
                "chain": " -> ".join(chain_parts),
                "external_call": (
                    {
                        "name": external_call,
                        "path": output_endpoint.path,
                        "line": output_endpoint.start_line,
                    }
                    if external_call is not None
                    else None
                ),
                "methods": [
                    {
                        "id": method.id,
                        "qualified_method": method.qualified_method,
                        "path": method.path,
                        "start_line": method.start_line,
                        "end_line": method.end_line,
                    }
                    for method in path_methods
                ],
                "edges": [
                    {
                        "caller_id": edge.caller_id,
                        "callee_id": edge.callee_id,
                        "path": edge.path,
                        "line": edge.line,
                        "confidence": edge.dispatch_confidence,
                        "inferred": edge.inferred,
                    }
                    for edge in path_edges
                ],
            })
    return examples


def _invocation_name(snippet: str) -> str | None:
    """Extract a receiver-qualified invocation from endpoint source evidence."""
    matches = re.findall(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)\s*\(", snippet)
    preferred = [
        match for match in matches
        if match.rsplit(".", 1)[-1] in {
            "send", "publish", "postForObject", "getForObject", "exchange",
            "sendMessage", "convertAndSend",
        }
    ]
    return (preferred or matches)[-1] if matches else None


def _shortest_method_path(
    start: str,
    target: str,
    adjacency: dict[str, list[CodeQLCallGraphEdge]],
    methods: dict[str, IntegrationMethod],
    entry_method: str,
) -> tuple[list[str], list[CodeQLCallGraphEdge]] | None:
    queue: deque[tuple[str, list[str], list[CodeQLCallGraphEdge]]] = deque([
        (start, [start], [])
    ])
    visited = {start}
    while queue:
        current, method_path, edge_path = queue.popleft()
        if current == target:
            return method_path, edge_path
        for edge in sorted(
            adjacency.get(current, []),
            key=lambda item: (item.path, item.line, item.callee_id),
        ):
            caller = methods[edge.caller_id].qualified_method
            callee = methods[edge.callee_id].qualified_method
            if (
                caller.endswith("AbstractKafkaMessageProcessor.consumeMessage")
                and callee.endswith(".processMessage")
                and entry_method.rsplit(".", 1)[0] != callee.rsplit(".", 1)[0]
            ):
                continue
            if edge.callee_id in visited:
                continue
            visited.add(edge.callee_id)
            queue.append((
                edge.callee_id,
                [*method_path, edge.callee_id],
                [*edge_path, edge],
            ))
    return None


def _port_evidence(endpoint: MessageEndpoint) -> dict[str, object]:
    return {
        "id": endpoint.id,
        "role": endpoint.role,
        "system": endpoint.system,
        "topic": endpoint.topic,
        "path": endpoint.path,
        "line": endpoint.start_line,
        "module": endpoint.module,
    }


def render_internal_flow_debug_text(debug: dict[str, object]) -> str:
    """Render the internal-flow construction diagnostic for a human."""
    module = debug["module"]
    flows = debug["flows"]
    assert isinstance(flows, list)
    lines = [
        f"Flux internes du module {module}",
        f"Résultat : {'trouvé' if debug['found'] else 'non trouvé'} ({debug['flow_count']})",
    ]
    for item in flows:
        assert isinstance(item, dict)
        lines.append(f"Exemple {item['flow_id']}")
        lines.append(
            f"  Type : {'chaîne interprocédurale' if item['chain_type'] == 'interprocedural' else 'appel sortant direct depuis la méthode IN'}"
        )
        lines.append(f"  Chaîne : {item['chain']}")
        if "methods" in item:
            methods = item["methods"]
            assert isinstance(methods, list)
            for method in methods:
                assert isinstance(method, dict)
                lines.append(
                    f"    Méthode : {method['qualified_method']} "
                    f"({method['path']}:{method['start_line']})"
                )
            edges = item["edges"]
            assert isinstance(edges, list)
            for edge in edges:
                assert isinstance(edge, dict)
                lines.append(
                    f"    Appel : {edge['caller_id']} -> {edge['callee_id']} "
                    f"({edge['path']}:{edge['line']})"
                )
            external_call = item.get("external_call")
            if isinstance(external_call, dict):
                lines.append(
                    f"    Appel externe : {external_call['name']} "
                    f"({external_call['path']}:{external_call['line']})"
                )
        else:
            source = item["source"]
            assert isinstance(source, dict)
            lines.append(f"  Source : {source['path']}:{source['start_line']}")
            steps = item["steps"]
            assert isinstance(steps, list)
            for step in steps:
                assert isinstance(step, dict)
                lines.append(
                    f"    {step['order']}. {step['port']} {step['kind']} {step['name']} "
                    f"({step['path']}:{step['start_line']})"
                )
    excluded = debug["excluded_flows"]
    assert isinstance(excluded, list)
    if excluded:
        lines.append("Flux exclus du compteur IN -> OUT :")
        for item in excluded:
            assert isinstance(item, dict)
            lines.append(
                f"  {item['flow_id']}  {item['method']}  "
                f"({item['construction']}) : {item['reason']}"
            )
    if not flows:
        inputs = debug["indexed_inputs"]
        outputs = debug["indexed_outputs"]
        assert isinstance(inputs, list) and isinstance(outputs, list)
        lines.extend([
            f"  Entrées indexées : {', '.join(inputs) or 'aucune'}",
            f"  Sorties indexées : {', '.join(outputs) or 'aucune'}",
            f"  Cause : {debug['not_found_reason']}",
        ])
    return "\n".join(lines)


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


def internal_flow_stats_by_module(
    flows: list[CodeFlow], endpoints: list[MessageEndpoint]
) -> dict[str, object]:
    """Count source-backed IN-to-OUT flows for every indexed module."""
    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints}
    module_names = sorted({
        endpoint.module for endpoint in endpoints if endpoint.module is not None
    } | {
        flow.module for flow in flows
    })
    counts = {
        module: {
            "internal_flows": sum(
                _is_internal_module_flow(flow, endpoint_by_id, module)
                for flow in flows
            ),
            "inputs": sum(
                endpoint.module == module and endpoint.role in {"serve", "consume"}
                for endpoint in endpoints
            ),
            "outputs": sum(
                endpoint.module == module and endpoint.role in {"call", "produce"}
                for endpoint in endpoints
            ),
        }
        for module in module_names
    }
    return {
        "modules": counts,
        "total_internal_flows": sum(
            int(item["internal_flows"]) for item in counts.values()
        ),
    }


def render_internal_flow_stats_by_module_text(stats: dict[str, object]) -> str:
    modules = stats["modules"]
    assert isinstance(modules, dict)
    lines = [
        "Flux internes par module",
    ]
    for module, values in modules.items():
        assert isinstance(values, dict)
        lines.append(
            f"{module} : {values['internal_flows']} flux interne(s) "
            f"({values['inputs']} IN, {values['outputs']} OUT)"
        )
    lines.append(f"Total : {stats['total_internal_flows']} flux interne(s)")
    return "\n".join(lines)


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
