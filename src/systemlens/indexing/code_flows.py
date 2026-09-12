"""Materialize conservative same-method flows during indexing."""

from collections import defaultdict
from pathlib import Path

from systemlens.discovery.java import parser as java_parser
from systemlens.domain.code_flows import CodeFlow, CodeFlowStep, compute_code_flow_id
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule, MongoMethod, module_identity
from systemlens.indexing.codeql import CodeQLCall


CODE_FLOW_SIGNATURE = "code-flow-v2-ast-methods-codeql"
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
        for method_node in java_parser.walk(root):
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
                if start_line <= endpoint.start_line <= end_line
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
    methods: list[IntegrationMethod], endpoints: list[MessageEndpoint], calls: list[CodeQLCall]
) -> list[CodeFlow]:
    """Join AST method facts through resolved CodeQL calls.

    A flow is emitted only for one unambiguous, bounded call path from an AST
    input method to an AST output method. This deliberately excludes dispatch
    targets CodeQL cannot resolve and paths with a cycle.
    """
    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints}
    by_locator = {(item.qualified_method, item.path, item.start_line): item for item in methods}
    def locate(name: str, path: str, line: int) -> IntegrationMethod | None:
        exact = by_locator.get((name, path, line))
        if exact is not None:
            return exact
        candidates = [item for item in methods if item.qualified_method == name and item.path.endswith(path)]
        return candidates[0] if len(candidates) == 1 else None

    adjacency: dict[str, list[tuple[IntegrationMethod, CodeQLCall]]] = defaultdict(list)
    for call in calls:
        caller = locate(call.caller, call.caller_path, call.caller_line)
        callee = locate(call.callee, call.callee_path, call.callee_line)
        if caller is not None and callee is not None:
            adjacency[caller.id].append((callee, call))

    flows: list[CodeFlow] = []
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
                if len(route) >= 12:
                    continue
                for target, call in adjacency.get(current.id, []):
                    next_route = [*route, (target, call)]
                    if target.id == entry.id or any(previous.id == target.id for previous, _ in route):
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
                            flows.append(CodeFlow(
                                id=flow_id, module=entry.module, method=entry.qualified_method,
                                path=entry.path, start_line=entry.start_line, end_line=entry.end_line,
                                status="potential", confidence="medium",
                                reason="CodeQL resolved a static call path from an indexed entry method to an indexed output method.",
                                steps=tuple([*steps, _endpoint_step(output, len(steps) + 1)]),
                            ))
                    queue.append((target, next_route))
    unique = {flow.id: flow for flow in flows}
    return sorted(unique.values(), key=lambda flow: (flow.module, flow.path, flow.start_line, flow.id))
