"""Materialize conservative same-method flows during indexing."""

from collections import defaultdict, deque
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from tree_sitter import Node

from systemlens.discovery.java import parser as java_parser
from systemlens.domain.code_flows import CodeFlow, CodeFlowStep, compute_code_flow_id
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.domain.graph import GraphEdge
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule, MongoMethod, module_identity
from systemlens.indexing.codeql import CodeQLCall, CodeQLReachability
from systemlens.indexing.java_symbols import JavaSymbols


CODE_FLOW_SIGNATURE = "code-flow-v19-trigger-rooted-fanout"
_TRIGGER_ROLES = {("rest", "serve"), ("kafka", "consume")}
_EFFECT_ROLES = {("rest", "call"), ("kafka", "produce")}
_MONGO_WRITE_OPERATIONS = frozenset({
    "bulkOps", "findAndModify", "findAndReplace", "insert", "remove", "save",
    "updateFirst", "updateMulti", "upsert",
})


def _deduplicate_code_flows(flows: list[CodeFlow]) -> list[CodeFlow]:
    """Keep one representative for each evidenced endpoint-to-endpoint flow.

    Call-graph enumeration can expose several implementation/dispatch routes
    for the same integration pair.  Those routes are useful while debugging
    the join, but are not distinct application flows.  Prefer a non-cycle,
    higher-confidence, shorter route and retain deterministic ordering.
    """
    confidence_rank = {"high": 0, "medium": 1, "low": 2}

    def route_signature(flow: CodeFlow) -> tuple[tuple[object, ...], ...]:
        """Return the evidence-bearing shape of one candidate route.

        AST and CodeQL can emit the same route independently.  They must be
        collapsed into one represented alternative, while genuinely different
        intermediate routes must remain countable.
        """
        return tuple(
            (
                step.kind,
                step.name,
                step.path,
                step.start_line,
                step.end_line,
                step.endpoint_id,
                step.operation,
            )
            for step in flow.steps
        )

    grouped: dict[tuple[str, str, str], list[CodeFlow]] = {}
    for flow in flows:
        endpoint_steps = [step for step in flow.steps if step.endpoint_id]
        if not endpoint_steps:
            key = (flow.id, "", flow.status)
        else:
            # A cycle has the same endpoint as both ends. Keep it separate from
            # an ordinary entry-to-output flow, but only once per entry.
            key = (
                endpoint_steps[0].endpoint_id or flow.id,
                endpoint_steps[-1].endpoint_id or flow.id,
                flow.status,
            )
        grouped.setdefault(key, []).append(flow)
    selected = []
    for _, parallel in sorted(grouped.items()):
        representative = min(
            parallel,
            key=lambda flow: (
                confidence_rank.get(flow.confidence, 99),
                len(flow.steps),
                flow.id,
            ),
        )
        selected.append(replace(
            representative,
            alternative_count=len({route_signature(flow) for flow in parallel}),
        ))
    return sorted(
        selected,
        key=lambda flow: (flow.module, flow.path, flow.start_line, flow.id),
    )


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


def _scheduled_trigger_step(
    method_node: Node, source: bytes, path: str
) -> CodeFlowStep | None:
    """Return a source-evidenced cron trigger for a scheduled Java method."""
    for annotation in java_parser.annotations_of(method_node):
        if java_parser.annotation_name(annotation, source) != "Scheduled":
            continue
        cron = java_parser.string_value(
            java_parser.annotation_argument(annotation, source, "cron"), source
        )
        return CodeFlowStep(
            order=1,
            kind="cron_entry",
            name=cron or "@Scheduled",
            path=path,
            start_line=method_node.start_point.row + 1,
            end_line=method_node.start_point.row + 1,
        )
    return None


def _matching_fanout_consumers(
    producer: MessageEndpoint,
    endpoints: list[MessageEndpoint],
) -> list[MessageEndpoint]:
    """Return consumers compatible with one concrete Kafka publication.

    A shared concrete topic establishes the integration. Missing payload types
    lower the confidence of the resulting flow but do not block it. Two known
    and different types remain incompatible because the evidence conflicts.
    """
    if producer.system != "kafka" or producer.role != "produce" or producer.topic_dynamic:
        return []
    candidates = [
        endpoint for endpoint in endpoints
        if endpoint.system == "kafka"
        and endpoint.role == "consume"
        and endpoint.topic == producer.topic
        and endpoint.id != producer.id
        and (
            producer.message_type is None
            or endpoint.message_type is None
            or endpoint.message_type == producer.message_type
        )
    ]
    return sorted(candidates, key=lambda endpoint: (endpoint.module or "", endpoint.id))


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
            scheduled_trigger = _scheduled_trigger_step(method_node, source, path)
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
            if scheduled_trigger is not None and endpoint_effects:
                producer = endpoint_effects[0]
                assert producer.module is not None
                qualified_method = (
                    f"{producer.qualified_name}.{method_name}"
                    if producer.qualified_name
                    else method_name
                )
                scheduled_effects: list[tuple[int, str, MessageEndpoint]] = sorted(
                    (
                        (endpoint.start_line, endpoint.id, endpoint)
                        for endpoint in endpoint_effects
                    ),
                    key=lambda item: (item[0], item[1]),
                )
                steps = [scheduled_trigger]
                for order, (_line, _key, effect) in enumerate(scheduled_effects, start=2):
                    steps.append(_endpoint_step(effect, order))
                flows.append(CodeFlow(
                    id=compute_code_flow_id(
                        producer.module, path, qualified_method,
                        "cron_entry", scheduled_trigger.name,
                    ),
                    module=producer.module,
                    method=qualified_method,
                    path=path,
                    start_line=start_line,
                    end_line=end_line,
                    status="potential",
                    confidence="medium",
                    reason=(
                        "A scheduled Java method publishes an external effect; "
                        "the scheduler is the flow trigger."
                    ),
                    steps=tuple(steps),
                ))
            for producer in endpoint_effects:
                # A publication is an effect of an input-triggered flow, not
                # an independent trigger. Only a method explicitly scheduled
                # by a cron expression can create a source flow here.
                if scheduled_trigger is None:
                    continue
                consumers = _matching_fanout_consumers(producer, endpoints)
                if not consumers:
                    continue
                assert producer.module is not None
                qualified_method = (
                    f"{producer.qualified_name}.{method_name}"
                    if producer.qualified_name
                    else method_name
                )
                # A CodeFlow remains one auditable endpoint path for
                # compatibility. The exported call graph expands this
                # representative path with every matching consumer branch.
                for consumer in consumers:
                    trigger_kind = (
                        "cron_entry"
                        if scheduled_trigger is not None
                        else "message_publish"
                    )
                    trigger_name = (
                        scheduled_trigger.name
                        if scheduled_trigger is not None
                        else producer.topic
                    )
                    prefix = [scheduled_trigger] if scheduled_trigger is not None else []
                    steps = [
                        *prefix,
                        _endpoint_step(producer, len(prefix) + 1),
                        _endpoint_step(consumer, len(prefix) + 2),
                    ]
                    flows.append(CodeFlow(
                        id=compute_code_flow_id(
                            producer.module,
                            path,
                            qualified_method,
                            trigger_kind,
                            f"{trigger_name}|fanout|{consumer.id}",
                        ),
                        module=producer.module,
                        method=qualified_method,
                        path=path,
                        start_line=start_line,
                        end_line=end_line,
                        status="potential",
                        confidence="medium",
                        reason=(
                            "A typed Kafka publication fans out to a matching "
                            "consumer; the call graph retains every proven branch."
                        ),
                        steps=tuple(steps),
                    ))
    return sorted(flows, key=lambda flow: (flow.module, flow.path, flow.start_line, flow.id))


def materialize_codeql_code_flows(
    methods: list[IntegrationMethod], endpoints: list[MessageEndpoint], calls: list[CodeQLCall],
    *, repo_root: Path | None = None, max_hops: int = 12,
    stats: dict[str, int] | None = None,
    reachability: Sequence[CodeQLReachability] = (),
    source_paths: Sequence[str] = (),
) -> list[CodeFlow]:
    """Join AST method facts through resolved CodeQL calls.

    Source-located CodeQL pairs are authoritative. Missing or imperfect
    virtual-dispatch edges may be completed by the transient source symbol
    index, but every synthetic edge remains possible/low-confidence.
    """
    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints}
    def normalized_method_name(name: str) -> str:
        """Align analyzer method names with Java source declarations."""
        # Analyzer names may include ``package.Type.method:return(args)``.
        # The AST inventory deliberately stores the stable Java declaration name
        # only. Removing the CPG signature is safe because the following lookup
        # still requires an exact source location or one unique declaration.
        normalized = name.replace("$", ".").split(":", 1)[0]
        # CodeQL normally omits parameters, while some versions
        # expose ``Type.method(arg, ...)``.  The AST projection intentionally
        # keeps only the stable owner-and-method part, so discard a terminal
        # signature before doing the source-backed join.
        open_parenthesis = normalized.find("(")
        if open_parenthesis >= 0:
            normalized = normalized[:open_parenthesis]
        return normalized

    def normalized_path(path: str) -> str:
        """Normalize harmless extractor spelling differences in source paths."""
        return path.replace("\\", "/").removeprefix("./")

    by_locator = {
        (normalized_method_name(item.qualified_method), normalized_path(item.path), item.start_line): item
        for item in methods
    }
    methods_by_path: dict[str, list[IntegrationMethod]] = defaultdict(list)
    for item in methods:
        methods_by_path[normalized_path(item.path)].append(item)
    def locate(
        name: str, path: str, line: int, *, allow_signature_fallback: bool = False,
    ) -> tuple[IntegrationMethod, bool] | None:
        """Resolve a method and report whether its source path was joined.

        A signature fallback is accepted only when the extractor explicitly
        lacks a source path and exactly one indexed method has that qualified
        name. It remains low-confidence evidence, never an exact CodeQL fact.
        """
        normalized_name = normalized_method_name(name)
        path = normalized_path(path)
        exact = by_locator.get((normalized_name, path, line))
        if exact is not None:
            return exact, False
        same_file = [
            item for item in methods
            if normalized_method_name(item.qualified_method) == normalized_name
            and normalized_path(item.path) == path
            and item.start_line <= line <= item.end_line
        ]
        if same_file:
            return min(same_file, key=lambda item: item.end_line - item.start_line), False
        if allow_signature_fallback:
            candidates = [item for item in methods if normalized_method_name(item.qualified_method) == normalized_name]
            if len(candidates) == 1:
                return candidates[0], True
        return None

    def locate_caller(call: CodeQLCall) -> IntegrationMethod | None:
        """Locate a caller, including a lambda's enclosing Java method.

        CodeQL represents a Java lambda as a synthetic anonymous ``apply``
        method. Its qualified name has no direct AST counterpart, but its call
        site remains within the lexical method declaration SystemLens stored.
        Prefer the narrowest containing declaration to avoid attributing a
        local/anonymous-class method to an outer method when both are present.
        """
        resolved = locate(call.caller, call.caller_path, call.caller_line)
        direct = resolved[0] if resolved is not None else None
        if direct is not None:
            return direct
        # The only name-less caller recovery supported is CodeQL's explicit
        # synthetic anonymous/lambda callable. Ordinary unknown names must not
        # be attributed to whichever method happens to contain the line.
        if not call.caller.startswith("<anonymous"):
            return None
        enclosing = [
            item for item in methods_by_path.get(normalized_path(call.caller_path), [])
            if item.start_line <= call.call_line <= item.end_line
        ]
        return min(enclosing, key=lambda item: item.end_line - item.start_line) if enclosing else None

    adjacency: dict[str, list[tuple[IntegrationMethod, CodeQLCall, bool]]] = defaultdict(list)
    symbols = JavaSymbols(repo_root, methods, source_paths) if repo_root is not None else None
    bridges: dict[str, IntegrationMethod | None] = {}
    resolved_sites: set[tuple[str, int]] = set()
    seen_edges: set[tuple[str, str, int, str, bool]] = set()
    synthetic_calls: set[CodeQLCall] = set()

    def add_edge(
        caller: IntegrationMethod, callee: IntegrationMethod, call: CodeQLCall,
        inferred: bool = False,
    ) -> None:
        key = (caller.id, callee.id, call.call_line, call.dispatch_confidence, inferred)
        if key not in seen_edges:
            seen_edges.add(key)
            adjacency[caller.id].append((callee, call, inferred))

    for call in calls:
        caller = locate_caller(call)
        resolved_callee = locate(
            call.callee, call.callee_path, call.callee_line,
            allow_signature_fallback=True,
        )
        if caller is None or resolved_callee is None:
            continue
        callee, signature_join = resolved_callee
        add_edge(caller, callee, call, signature_join)
        if symbols is not None:
            info = symbols.methods.get(callee.id)
            if info is not None and info.concrete:
                resolved_sites.add((caller.id, call.call_line))
            if callee.id not in bridges:
                bridges[callee.id] = symbols.bridge(callee)
            candidate = bridges[callee.id]
            if candidate is not None:
                synthetic = replace(
                    call, callee=candidate.qualified_method, callee_path=candidate.path,
                    callee_line=candidate.start_line, dispatch_confidence="possible",
                )
                synthetic_calls.add(synthetic)
                add_edge(caller, candidate, synthetic, True)

    if symbols is not None:
        for call in symbols.fallback_calls(resolved_sites):
            synthetic_calls.add(call)
            caller = locate_caller(call)
            resolved_target = locate(call.callee, call.callee_path, call.callee_line)
            if caller is not None and resolved_target is not None:
                add_edge(caller, resolved_target[0], call, True)

    flows: list[CodeFlow] = []
    # Contract-only HTTP inputs (for example generated OpenAPI interfaces)
    # have no source endpoint path for the AST materializer. If the same
    # indexed method also owns an output endpoint, the method itself is still
    # sufficient evidence for a direct input-to-output flow.
    for entry in methods:
        for trigger_id in entry.input_endpoint_ids:
            trigger = endpoint_by_id.get(trigger_id)
            if trigger is None:
                continue
            for output_id in entry.output_endpoint_ids:
                output = endpoint_by_id.get(output_id)
                if output is None:
                    continue
                flows.append(CodeFlow(
                    id=compute_code_flow_id(
                        entry.module, entry.path, entry.qualified_method,
                        _endpoint_step(trigger, 1).kind,
                        f"{trigger.topic}|{output.id}",
                    ),
                    module=entry.module,
                    method=entry.qualified_method,
                    path=entry.path,
                    start_line=entry.start_line,
                    end_line=entry.end_line,
                    status="potential",
                    confidence="medium",
                    reason=(
                        "The indexed entry and external effect belong to the same "
                        "Java method."
                    ),
                    steps=(_endpoint_step(trigger, 1), _endpoint_step(output, 2)),
                ))
    explored = 0

    def dispatch_matches_entry(
        entry: IntegrationMethod,
        current: IntegrationMethod,
        target: IntegrationMethod,
    ) -> bool:
        """Keep generic Kafka template dispatch on the receiving consumer.

        CodeQL can report every concrete override of the generic
        ``AbstractKafkaMessageProcessor.processMessage`` call. The runtime
        receiver is the concrete consumer that owns the indexed entry method;
        crossing to another consumer creates impossible POC flows and false
        cycles.
        """
        if not current.qualified_method.endswith(
            "AbstractKafkaMessageProcessor.consumeMessage"
        ) or not target.qualified_method.endswith(".processMessage"):
            return True
        entry_owner = entry.qualified_method.rsplit(".", 1)[0]
        target_owner = target.qualified_method.rsplit(".", 1)[0]
        return entry_owner == target_owner

    for entry in methods:
        if not entry.input_endpoint_ids:
            continue
        for trigger_id in entry.input_endpoint_ids:
            trigger = endpoint_by_id.get(trigger_id)
            if trigger is None:
                continue
            queue = deque[tuple[IntegrationMethod, list[tuple[IntegrationMethod, CodeQLCall, bool]]]]([(entry, [])])
            visited: set[tuple[str, bool]] = {(entry.id, False)}
            while queue:
                current, route = queue.popleft()
                if len(route) >= max_hops:
                    continue
                for target, call, signature_join in adjacency.get(current.id, []):
                    if not dispatch_matches_entry(entry, current, target):
                        continue
                    explored += 1
                    next_route = [*route, (target, call, signature_join)]
                    if target.id == entry.id or any(previous.id == target.id for previous, _edge, _signature in route):
                        steps = [_endpoint_step(trigger, 1)]
                        for order, (hop, edge, _signature_join) in enumerate(next_route, start=2):
                            steps.append(CodeFlowStep(
                                order=order, kind="method_call", name=hop.qualified_method,
                                path=edge.caller_path, start_line=edge.call_line, end_line=edge.call_line,
                            ))
                        flows.append(CodeFlow(
                            id=compute_code_flow_id(
                                entry.module, entry.path, entry.qualified_method,
                                _endpoint_step(trigger, 1).kind,
                                f"{trigger.topic}|cycle|" + ".".join(
                                    hop.id for hop, _edge, _signature_join in next_route
                                ),
                            ),
                            module=entry.module, method=entry.qualified_method,
                            path=entry.path, start_line=entry.start_line, end_line=entry.end_line,
                            status="cycle",
                            confidence="low" if any(
                                edge.dispatch_confidence == "possible" or signature
                                for _hop, edge, signature in next_route
                            ) else "medium",
                            reason=(
                                "CodeQL found a cyclic call path from this indexed entry method."
                            ),
                            steps=tuple(steps),
                        ))
                        continue
                    if target.output_endpoint_ids:
                        steps = [_endpoint_step(trigger, 1)]
                        for order, (hop, edge, _signature_join) in enumerate(next_route, start=2):
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
                                _endpoint_step(trigger, 1).kind,
                                f"{trigger.topic}|{output.id}|" + ".".join(
                                    hop.id for hop, _edge, _signature_join in next_route
                                ),
                            )
                            has_possible_dispatch = any(
                                edge.dispatch_confidence == "possible"
                                for _hop, edge, _signature_join in next_route
                            )
                            has_signature_join = any(
                                signature_join for _hop, _edge, signature_join in next_route
                            )
                            flows.append(CodeFlow(
                                id=flow_id, module=entry.module, method=entry.qualified_method,
                                path=entry.path, start_line=entry.start_line, end_line=entry.end_line,
                                status="potential",
                                confidence="low" if has_possible_dispatch or has_signature_join else "medium",
                            reason=(
                                    "A source-declared Java dispatch fallback connects an indexed entry to an output through qualified types and compatible method signatures."
                                    if any(edge in synthetic_calls for _hop, edge, _signature in next_route)
                                    else
                                    "A unique indexed method signature joined a module-local CodeQL call."
                                    if has_signature_join
                                    else
                                    "CodeQL found a possible virtual-dispatch path from an indexed entry method to an indexed output method."
                                    if has_possible_dispatch
                                    else "CodeQL resolved a static call path from an indexed entry method to an indexed output method."
                                ),
                                steps=tuple([*steps, _endpoint_step(output, len(steps) + 1)]),
                            ))
                    possible = any(edge.dispatch_confidence == "possible" or inferred
                                   for _method, edge, inferred in next_route)
                    state = (target.id, possible)
                    if state not in visited:
                        visited.add(state)
                        queue.append((target, next_route))
    if stats is not None:
        stats.update({
            "calls": len(calls), "joined_calls": sum(len(targets) for targets in adjacency.values()),
            "explored_paths": explored,
        })
    if reachability:
        # One predecessor tree per source/confidence, not a BFS per pair.
        tree_cache: dict[
            tuple[str, str], dict[str, tuple[str, IntegrationMethod, CodeQLCall, bool]]
        ] = {}
        max_cached_trees = 8
        direct_flows: dict[tuple[str, str], CodeFlow] = {}

        def representative_route(
            source: IntegrationMethod, target: IntegrationMethod, confidence: str
        ) -> list[tuple[IntegrationMethod, CodeQLCall, bool]]:
            key = (source.id, confidence)
            if key not in tree_cache:
                if len(tree_cache) >= max_cached_trees:
                    tree_cache.pop(next(iter(tree_cache)))
                predecessors: dict[str, tuple[str, IntegrationMethod, CodeQLCall, bool]] = {}
                queue = deque([source.id])
                visited = {source.id}
                while queue:
                    current_id = queue.popleft()
                    for candidate, call, inferred in adjacency.get(current_id, []):
                        # A direct CodeQL witness must not contain synthetic
                        # AST edges or a weaker dispatch than the proven pair.
                        if inferred or (confidence == "medium" and call.dispatch_confidence != "exact"):
                            continue
                        if candidate.id in visited:
                            continue
                        visited.add(candidate.id)
                        predecessors[candidate.id] = (current_id, candidate, call, inferred)
                        queue.append(candidate.id)
                tree_cache[key] = predecessors
            predecessors = tree_cache[key]
            route: list[tuple[IntegrationMethod, CodeQLCall, bool]] = []
            current_id = target.id
            while current_id != source.id:
                previous = predecessors.get(current_id)
                if previous is None:
                    return []
                current_id, candidate, call, inferred = previous
                route.append((candidate, call, inferred))
            route.reverse()
            return route

        for relation in sorted(reachability, key=lambda item: (
            item.source_path, item.source_line, item.source, item.confidence,
            item.target_path, item.target_line, item.target,
        )):
            source_location = locate(relation.source, relation.source_path, relation.source_line)
            target_location = locate(relation.target, relation.target_path, relation.target_line)
            if source_location is None or target_location is None:
                continue
            source_method = source_location[0]
            target_method = target_location[0]
            for input_id in source_method.input_endpoint_ids:
                trigger = endpoint_by_id.get(input_id)
                if trigger is None:
                    continue
                for output_id in target_method.output_endpoint_ids:
                    output = endpoint_by_id.get(output_id)
                    if output is None:
                        continue
                    previous_flow = direct_flows.get((input_id, output_id))
                    if previous_flow is not None and previous_flow.confidence == "medium":
                        continue
                    route = representative_route(source_method, target_method, relation.confidence)
                    steps = [_endpoint_step(trigger, 1)]
                    for order, (hop, edge, _signature_join) in enumerate(route, start=2):
                        steps.append(CodeFlowStep(
                            order=order, kind="method_call", name=hop.qualified_method,
                            path=edge.caller_path, start_line=edge.call_line, end_line=edge.call_line,
                        ))
                    flow_id = compute_code_flow_id(
                        source_method.module, source_method.path, source_method.qualified_method,
                        _endpoint_step(trigger, 1).kind,
                        f"{trigger.topic}|{output.id}|direct-codeql",
                    )
                    direct_flows[(input_id, output_id)] = CodeFlow(
                        id=flow_id, module=source_method.module, method=source_method.qualified_method,
                        path=source_method.path, start_line=source_method.start_line,
                        end_line=source_method.end_line,
                        status="potential", confidence=relation.confidence,
                        reason=(
                            "CodeQL directly proved reverse reachability from the indexed output "
                            "method to the indexed input method."
                            if not route else
                            "CodeQL proved reverse reachability and the indexed call graph "
                            "provided a representative intermediate route."
                        ),
                        steps=tuple([*steps, _endpoint_step(output, len(steps) + 1)]),
                    )
        flows = [
            flow for flow in flows
            if flow.status == "cycle" or
            (flow.steps[0].endpoint_id, flow.steps[-1].endpoint_id) not in direct_flows
        ]
        flows.extend(direct_flows.values())
    return _deduplicate_code_flows(flows)


def reconcile_code_flows(
    flows: list[CodeFlow],
    endpoints: list[MessageEndpoint],
    topology_edges: list[GraphEdge],
) -> list[CodeFlow]:
    """Attach a persisted, endpoint-identity-based topology status to flows.

    A flow is complete when every endpoint step still exists and every
    cross-service effect/continuation is represented by a topology edge. A
    same-service input-to-output flow is complete without an inter-service
    edge. Missing, dynamic, ambiguous, or otherwise unmatched endpoint
    evidence is retained as a partial flow rather than being discarded.
    """
    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints}
    outgoing = defaultdict(list)
    incoming = defaultdict(list)
    for edge in topology_edges:
        outgoing[edge.from_endpoint.id].append(edge)
        if edge.to_endpoint is not None:
            incoming[edge.to_endpoint.id].append(edge)

    reconciled: list[CodeFlow] = []
    for flow in flows:
        endpoint_steps = [step for step in flow.steps if step.endpoint_id]
        status = "complete"
        if any(step.endpoint_id not in endpoint_by_id for step in endpoint_steps):
            status = "partial"
        else:
            for index, step in enumerate(endpoint_steps):
                endpoint_id = step.endpoint_id
                if endpoint_id is None:
                    status = "partial"
                    break
                endpoint = endpoint_by_id[endpoint_id]
                previous_endpoint = None
                if index > 0:
                    previous_id = endpoint_steps[index - 1].endpoint_id
                    if previous_id is not None:
                        previous_endpoint = endpoint_by_id[previous_id]
                if endpoint.role in {"call", "produce"}:
                    if endpoint.module == flow.module:
                        continue
                    matching_outgoing = outgoing.get(endpoint.id, [])
                    next_endpoint = None
                    if index + 1 < len(endpoint_steps):
                        next_id = endpoint_steps[index + 1].endpoint_id
                        if next_id is not None:
                            next_endpoint = endpoint_by_id[next_id]
                    if next_endpoint is not None:
                        matching_outgoing = [
                            edge for edge in matching_outgoing
                            if edge.to_endpoint is not None
                            and edge.to_endpoint.id == next_endpoint.id
                        ]
                    if len(matching_outgoing) != 1:
                        status = "partial"
                        break
                if endpoint.role in {"serve", "consume"} and step is not endpoint_steps[0]:
                    matching_incoming = incoming.get(endpoint.id, [])
                    if previous_endpoint is not None:
                        matching_incoming = [
                            edge for edge in matching_incoming
                            if edge.from_endpoint.id == previous_endpoint.id
                        ]
                    if len(matching_incoming) != 1:
                        status = "partial"
                        break
        reconciled.append(replace(flow, reconciliation=status))
    return reconciled


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
    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints}
    consumers: dict[str, list[CodeFlow]] = defaultdict(list)
    for flow in flows:
        entry = (
            endpoint_by_id.get(flow.steps[0].endpoint_id)
            if flow.steps and flow.steps[0].endpoint_id
            else None
        )
        if (
            flow.steps
            and flow.steps[0].kind == "message_entry"
            and flow.steps[0].endpoint_id in concrete_kafka_endpoints
            and entry is not None
        ):
            consumers[flow.steps[0].name].append(flow)
    continuations: list[CodeFlow] = []
    # ``seen_consumers`` is intentionally carried independently from the
    # rendered steps: a cycle can revisit a topic without ever revisiting the
    # exact source location of a message entry.
    queue: list[tuple[CodeFlow, tuple[str, ...], int]] = [
        (flow, (flow.id,), 0) for flow in flows
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
            if publish.name is None:
                continue
            publish_endpoint = endpoint_by_id.get(publish.endpoint_id)
            if publish_endpoint is None:
                continue
            for consumer in consumers.get(publish.name, []):
                consumer_endpoint_id = consumer.steps[0].endpoint_id
                if consumer_endpoint_id is None:
                    continue
                consumer_endpoint = endpoint_by_id.get(consumer_endpoint_id)
                if consumer_endpoint is None:
                    continue
                if (
                    publish_endpoint.message_type is not None
                    and consumer_endpoint.message_type is not None
                    and publish_endpoint.message_type != consumer_endpoint.message_type
                ):
                    continue
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
    return _deduplicate_code_flows([*flows, *continuations])
