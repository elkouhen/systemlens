"""Materialize conservative same-method flows during indexing."""

from collections import defaultdict
from pathlib import Path

from systemlens.discovery.java import parser as java_parser
from systemlens.domain.code_flows import CodeFlow, CodeFlowStep, compute_code_flow_id
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule, MongoMethod, module_identity
from systemlens.indexing.codeql import CodeQLCall


CODE_FLOW_SIGNATURE = "code-flow-v9-bounded-codeql-kafka-continuations"
_TRIGGER_ROLES = {("rest", "serve"), ("kafka", "consume")}
_EFFECT_ROLES = {("rest", "call"), ("kafka", "produce")}
_MONGO_WRITE_OPERATIONS = frozenset({
    "bulkOps", "findAndModify", "findAndReplace", "insert", "remove", "save",
    "updateFirst", "updateMulti", "upsert",
})


def _endpoint_step(endpoint: MessageEndpoint, order: int) -> CodeFlowStep:
    kind = {
        ("rest", "serve"): "http_entry",
        ("kafka", "consume"): "message_entry",
        ("rest", "call"): "http_call",
        ("kafka", "produce"): "message_publish",
    }[(endpoint.system, endpoint.role)]
    return CodeFlowStep(
        order=order,
        kind=kind,
        name=endpoint.topic,
        path=endpoint.path,
        start_line=endpoint.start_line,
        end_line=endpoint.end_line,
        endpoint_id=endpoint.id,
    )


def _mongo_step(method: MongoMethod, path: str, order: int) -> CodeFlowStep:
    operation = "write" if method.operation in _MONGO_WRITE_OPERATIONS else "read"
    return CodeFlowStep(
        order=order,
        kind=f"data_{operation}",
        name=method.collection or "<dynamic>",
        path=path,
        start_line=method.line,
        end_line=method.evidence.end_line if method.evidence else method.line,
        operation=method.operation,
    )


def _repository_path(repo_root: Path, module: DiscoveredModule, path: str) -> str:
    module_root = module.path.resolve().relative_to(repo_root.resolve())
    return (module_root / path).as_posix()


def materialize_code_flows(
    repo_root: Path,
    endpoints: list[MessageEndpoint],
    modules: list[DiscoveredModule],
) -> list[CodeFlow]:
    """Return potential flows whose trigger and effects share one Java method.

    Same-method containment proves a useful local relationship, but not that a
    conditional effect executes for every invocation. Flows therefore remain
    explicitly potential with medium confidence.
    """
    endpoints_by_path: dict[str, list[MessageEndpoint]] = defaultdict(list)
    for endpoint in endpoints:
        if (
            endpoint.source == "code"
            and endpoint.path.endswith(".java")
            and endpoint.module
            and (endpoint.system, endpoint.role) in _TRIGGER_ROLES | _EFFECT_ROLES
        ):
            endpoints_by_path[endpoint.path].append(endpoint)

    mongo_by_path_and_method: dict[tuple[str, str, str], list[MongoMethod]] = defaultdict(list)
    for module in modules:
        identity = module_identity(module)
        for method in module.mongo_methods:
            if method.owner_method:
                path = _repository_path(repo_root, module, method.path)
                mongo_by_path_and_method[(identity, path, method.owner_method)].append(method)

    flows: list[CodeFlow] = []
    for path, path_endpoints in sorted(endpoints_by_path.items()):
        parsed = java_parser.parse_java(str(repo_root.resolve()), path)
        if parsed is None:
            continue
        source, root = parsed
        method_nodes = [
            node for node in java_parser.walk(root)
            if node.type == "method_declaration"
        ]
        endpoint_owners: dict[str, object] = {}
        for endpoint in path_endpoints:
            candidates = [
                node for node in method_nodes
                if node.start_point.row + 1 <= endpoint.start_line <= node.end_point.row + 1
            ]
            if candidates:
                endpoint_owners[endpoint.id] = min(
                    candidates, key=lambda node: node.end_byte - node.start_byte
                )
        for method_node in method_nodes:
            if method_node.type != "method_declaration":
                continue
            method_name = java_parser.declaration_name(method_node, source)
            if method_name is None:
                continue
            start_line = method_node.start_point.row + 1
            end_line = method_node.end_point.row + 1
            local_endpoints = [
                endpoint
                for endpoint in path_endpoints
                if endpoint_owners.get(endpoint.id) == method_node
            ]
            triggers = [
                endpoint
                for endpoint in local_endpoints
                if (endpoint.system, endpoint.role) in _TRIGGER_ROLES
            ]
            endpoint_effects = [
                endpoint
                for endpoint in local_endpoints
                if (endpoint.system, endpoint.role) in _EFFECT_ROLES
            ]
            for trigger in triggers:
                assert trigger.module is not None
                effects: list[tuple[int, str, MessageEndpoint | MongoMethod]] = [
                    (endpoint.start_line, endpoint.id, endpoint)
                    for endpoint in endpoint_effects
                    if endpoint.id != trigger.id
                ]
                effects.extend(
                    (mongo.line, f"mongo:{mongo.operation}:{mongo.collection or ''}", mongo)
                    for mongo in mongo_by_path_and_method.get(
                        (trigger.module, path, method_name), ()
                    )
                )
                if not effects:
                    continue
                ordered_effects = sorted(effects, key=lambda item: (item[0], item[1]))
                steps = [_endpoint_step(trigger, 1)]
                for order, (_line, _key, effect) in enumerate(ordered_effects, start=2):
                    steps.append(
                        _endpoint_step(effect, order)
                        if isinstance(effect, MessageEndpoint)
                        else _mongo_step(effect, path, order)
                    )
                qualified_method = (
                    f"{trigger.qualified_name}.{method_name}"
                    if trigger.qualified_name
                    else method_name
                )
                trigger_kind = _endpoint_step(trigger, 1).kind
                flows.append(CodeFlow(
                    id=compute_code_flow_id(
                        trigger.module,
                        path,
                        qualified_method,
                        trigger_kind,
                        trigger.topic,
                    ),
                    module=trigger.module,
                    method=qualified_method,
                    path=path,
                    start_line=start_line,
                    end_line=end_line,
                    status="potential",
                    confidence="medium",
                    reason=(
                        "The entry point and external effects occur in the same Java "
                        "method."
                    ),
                    steps=tuple(steps),
                ))
    return sorted(flows, key=lambda flow: (flow.module, flow.path, flow.start_line, flow.id))


def materialize_codeql_code_flows(
    methods: list[IntegrationMethod], endpoints: list[MessageEndpoint], calls: list[CodeQLCall],
    *, max_hops: int = 12, max_paths: int = 10_000, stats: dict[str, int] | None = None,
) -> list[CodeFlow]:
    """Join AST method facts through resolved CodeQL calls.

    A flow is emitted only for one unambiguous, bounded call path from an AST
    input method to an AST output method. This deliberately excludes dispatch
    targets CodeQL cannot resolve and paths with a cycle.
    """
    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints}
    def normalized_method_name(name: str) -> str:
        """Align CodeQL's ``Outer$Inner`` names with Java source names."""
        return name.replace("$", ".")

    by_locator = {
        (normalized_method_name(item.qualified_method), item.path, item.start_line): item
        for item in methods
    }
    def locate(name: str, path: str, line: int) -> IntegrationMethod | None:
        normalized_name = normalized_method_name(name)
        exact = by_locator.get((normalized_name, path, line))
        if exact is not None:
            return exact
        candidates = [
            item for item in methods
            if normalized_method_name(item.qualified_method) == normalized_name
            and item.path == path
        ]
        containing = [
            item for item in candidates if item.start_line <= line <= item.end_line
        ]
        if containing:
            return min(containing, key=lambda item: item.end_line - item.start_line)
        return candidates[0] if len(candidates) == 1 else None

    def locate_caller(call: CodeQLCall) -> IntegrationMethod | None:
        """Locate a caller, including a lambda's enclosing Java method.

        CodeQL represents a Java lambda as a synthetic anonymous ``apply``
        method. Its qualified name has no direct AST counterpart, but its call
        site remains within the lexical method declaration SystemLens stored.
        Prefer the narrowest containing declaration to avoid attributing a
        local/anonymous-class method to an outer method when both are present.
        """
        direct = locate(call.caller, call.caller_path, call.caller_line)
        if direct is not None:
            return direct
        enclosing = [
            item for item in methods
            if item.path == call.caller_path
            and item.start_line <= call.call_line <= item.end_line
        ]
        return min(enclosing, key=lambda item: item.end_line - item.start_line) if enclosing else None

    adjacency: dict[str, list[tuple[IntegrationMethod, CodeQLCall]]] = defaultdict(list)
    for call in calls:
        caller = locate_caller(call)
        callee = locate(call.callee, call.callee_path, call.callee_line)
        if caller is not None and callee is not None:
            adjacency[caller.id].append((callee, call))

    flows: list[CodeFlow] = []
    explored = 0
    truncated = 0
    for entry in methods:
        if not entry.input_endpoint_ids:
            continue
        for trigger_id in entry.input_endpoint_ids:
            trigger = endpoint_by_id.get(trigger_id)
            if trigger is None:
                continue
            queue: list[tuple[IntegrationMethod, list[tuple[IntegrationMethod, CodeQLCall]]]] = [(entry, [])]
            while queue:
                current, route = queue.pop(0)
                if len(route) >= max_hops:
                    continue
                for target, call in adjacency.get(current.id, []):
                    if explored >= max_paths:
                        truncated += 1
                        queue.clear()
                        break
                    explored += 1
                    next_route = [*route, (target, call)]
                    if target.id == entry.id or any(previous.id == target.id for previous, _ in route):
                        steps = [_endpoint_step(trigger, 1)]
                        for order, (hop, edge) in enumerate(next_route, start=2):
                            steps.append(CodeFlowStep(
                                order=order, kind="method_call", name=hop.qualified_method,
                                path=edge.caller_path, start_line=edge.call_line, end_line=edge.call_line,
                            ))
                        flows.append(CodeFlow(
                            id=compute_code_flow_id(
                                entry.module, entry.path, entry.qualified_method,
                                _endpoint_step(trigger, 1).kind,
                                f"{trigger.topic}|cycle|" + ".".join(hop.id for hop, _ in next_route),
                            ),
                            module=entry.module, method=entry.qualified_method,
                            path=entry.path, start_line=entry.start_line, end_line=entry.end_line,
                            status="cycle",
                            confidence="low" if any(edge.dispatch_confidence == "possible" for _hop, edge in next_route) else "medium",
                            reason="CodeQL found a cyclic call path from this indexed entry method.",
                            steps=tuple(steps),
                        ))
                        continue
                    if target.output_endpoint_ids:
                        steps = [_endpoint_step(trigger, 1)]
                        for order, (hop, edge) in enumerate(next_route, start=2):
                            steps.append(CodeFlowStep(
                                order=order, kind="method_call", name=hop.qualified_method,
                                path=edge.caller_path, start_line=edge.call_line, end_line=edge.call_line,
                            ))
                        for output_id in target.output_endpoint_ids:
                            output = endpoint_by_id.get(output_id)
                            if output is None:
                                continue
                            flow_id = compute_code_flow_id(
                                entry.module, entry.path, entry.qualified_method,
                                _endpoint_step(trigger, 1).kind, f"{trigger.topic}|{output.id}|" + ".".join(hop.id for hop, _ in next_route),
                            )
                            has_possible_dispatch = any(
                                edge.dispatch_confidence == "possible"
                                for _hop, edge in next_route
                            )
                            flows.append(CodeFlow(
                                id=flow_id, module=entry.module, method=entry.qualified_method,
                                path=entry.path, start_line=entry.start_line, end_line=entry.end_line,
                                status="potential",
                                confidence="low" if has_possible_dispatch else "medium",
                                reason=(
                                    "CodeQL found a possible virtual-dispatch path from an indexed entry method to an indexed output method."
                                    if has_possible_dispatch
                                    else "CodeQL resolved a static call path from an indexed entry method to an indexed output method."
                                ),
                                steps=tuple([*steps, _endpoint_step(output, len(steps) + 1)]),
                            ))
                    queue.append((target, next_route))
    if stats is not None:
        stats.update({
            "calls": len(calls), "joined_calls": sum(len(targets) for targets in adjacency.values()),
            "explored_paths": explored, "truncated_paths": truncated,
        })
    unique = {flow.id: flow for flow in flows}
    return sorted(unique.values(), key=lambda flow: (flow.module, flow.path, flow.start_line, flow.id))


def materialize_kafka_flow_continuations(
    flows: list[CodeFlow], endpoints: list[MessageEndpoint]
) -> list[CodeFlow]:
    """Add bounded, source-evidenced Kafka producer-to-consumer continuations.

    Only the original, persisted trigger flows can be consumers.  Composed
    flows are then placed back on the work queue so a later publication can be
    followed as well, without treating an arbitrary intermediate step as a
    new entry point.  The hop cap prevents cyclic topics from producing an
    unbounded number of candidates.
    """
    max_hops = 4
    concrete_kafka_endpoints = {
        endpoint.id for endpoint in endpoints
        if endpoint.system == "kafka" and not endpoint.topic_dynamic
    }
    consumers: dict[str, list[CodeFlow]] = defaultdict(list)
    for flow in flows:
        if (
            flow.steps
            and flow.steps[0].kind == "message_entry"
            and flow.steps[0].endpoint_id in concrete_kafka_endpoints
        ):
            consumers[flow.steps[0].name].append(flow)
    continuations: list[CodeFlow] = []
    # ``seen_consumers`` is intentionally carried independently from the
    # rendered steps: a cycle can revisit a topic without ever revisiting the
    # exact source location of a message entry.
    queue: list[tuple[CodeFlow, tuple[str, ...], int]] = [
        (flow, (), 0) for flow in flows
    ]
    while queue:
        flow, seen_consumers, hop_count = queue.pop(0)
        if hop_count >= max_hops:
            continue
        for publish_index, publish in enumerate(flow.steps):
            if publish.kind != "message_publish":
                continue
            if publish.endpoint_id not in concrete_kafka_endpoints:
                continue
            # A composed line must not silently discard effects that occur
            # after publication in the producer method.
            if any(step.kind in {"http_call", "message_publish", "data_read", "data_write"}
                   for step in flow.steps[publish_index + 1:]):
                continue
            for consumer in consumers.get(publish.name, []):
                if consumer.id == flow.id or consumer.id in seen_consumers:
                    cycle_steps = [*flow.steps[:publish_index + 1], consumer.steps[0]]
                    steps = tuple(
                        CodeFlowStep(**{**step.__dict__, "order": order})
                        for order, step in enumerate(cycle_steps, start=1)
                    )
                    continuations.append(CodeFlow(
                        id=compute_code_flow_id(
                            flow.module, flow.path, flow.method, steps[0].kind,
                            f"{steps[0].name}|kafka-cycle|{consumer.id}",
                        ),
                        module=flow.module, method=flow.method, path=flow.path,
                        start_line=flow.start_line, end_line=flow.end_line,
                        status="cycle", confidence="low" if "low" in {flow.confidence, consumer.confidence} else "medium",
                        reason="A concrete Kafka publication returns to an already traversed message entry.",
                        steps=steps,
                    ))
                    continue
                combined_steps = [*flow.steps[:publish_index + 1], *consumer.steps]
                steps = tuple(
                    CodeFlowStep(**{**step.__dict__, "order": order})
                    for order, step in enumerate(combined_steps, start=1)
                )
                continuation = CodeFlow(
                    id=compute_code_flow_id(
                        flow.module, flow.path, flow.method, steps[0].kind,
                        f"{steps[0].name}|kafka|{'|'.join((*seen_consumers, consumer.id))}",
                    ),
                    module=flow.module, method=flow.method, path=flow.path,
                    start_line=flow.start_line, end_line=flow.end_line,
                    status="potential",
                    confidence="low" if "low" in {flow.confidence, consumer.confidence} else "medium",
                    reason=(
                        "A concrete Kafka publication matches the indexed message entry "
                        "of a downstream potential flow."
                    ),
                    steps=steps,
                )
                continuations.append(continuation)
                queue.append((
                    continuation,
                    (*seen_consumers, consumer.id),
                    hop_count + 1,
                ))
    unique = {flow.id: flow for flow in [*flows, *continuations]}
    return sorted(unique.values(), key=lambda flow: (flow.module, flow.path, flow.start_line, flow.id))
