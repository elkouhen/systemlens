"""Materialize conservative same-method flows during indexing."""

import hashlib
import time
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Sequence

from tree_sitter import Node

from systemlens.discovery.java import parser as java_parser
from systemlens.domain.code_flows import (
    CodeFlow,
    CodeFlowStep,
    CodeQLCallGraphEdge,
    compute_code_flow_id,
)
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


CodeQLEdge = tuple[IntegrationMethod, CodeQLCall, bool]


@dataclass(frozen=True)
class CodeQLCallGraph:
    """The source-backed internal call graph used for flow reconstruction."""

    adjacency: dict[str, list[CodeQLEdge]]
    synthetic_calls: set[CodeQLCall]
    call_count: int
    locate: Callable[[str, str, int], tuple[IntegrationMethod, bool] | None]

    @property
    def joined_calls(self) -> int:
        return sum(len(targets) for targets in self.adjacency.values())

    def edges(self) -> tuple[CodeQLCallGraphEdge, ...]:
        """Return deterministic persisted edges for the internal call graph."""
        return tuple(sorted(
            (
                CodeQLCallGraphEdge(
                    caller_id=caller_id,
                    callee_id=callee.id,
                    path=call.caller_path.replace("\\", "/").removeprefix("./"),
                    line=call.call_line,
                    dispatch_confidence=call.dispatch_confidence,
                    inferred=inferred,
                )
                for caller_id, targets in self.adjacency.items()
                for callee, call, inferred in targets
            ),
            key=lambda edge: (
                edge.caller_id,
                edge.callee_id,
                edge.path,
                edge.line,
                edge.dispatch_confidence,
                edge.inferred,
            ),
        ))

    def connected_components(
        self, method_ids: Sequence[str] = ()
    ) -> tuple[frozenset[str], ...]:
        """Return weak components of the source-backed call graph.

        Components are only a grouping operation.  They do not turn a call
        edge into a reverse edge and do not create a route between methods.
        Direction is preserved by the traversal that materializes flows.
        """
        neighbours: dict[str, set[str]] = {method_id: set() for method_id in method_ids}
        for caller_id, targets in self.adjacency.items():
            neighbours.setdefault(caller_id, set())
            for target, _call, _inferred in targets:
                neighbours[caller_id].add(target.id)
                neighbours[target.id].add(caller_id)

        components: list[frozenset[str]] = []
        unseen = set(neighbours)
        while unseen:
            seed = min(unseen)
            component: set[str] = set()
            queue = [seed]
            unseen.remove(seed)
            while queue:
                current = queue.pop()
                component.add(current)
                for neighbour in sorted(neighbours[current]):
                    if neighbour in unseen:
                        unseen.remove(neighbour)
                        queue.append(neighbour)
            components.append(frozenset(component))
        return tuple(sorted(components, key=lambda component: min(component)))


def codeql_join_methods_signature(methods: Sequence[IntegrationMethod]) -> str:
    """Fingerprint the ordered input-method list used by resumable joins."""
    input_methods = [method for method in methods if method.input_endpoint_ids]
    payload = repr([
        (
            method.id,
            method.module,
            method.path,
            method.start_line,
            method.end_line,
            method.qualified_method,
            tuple(sorted(method.input_endpoint_ids)),
            tuple(sorted(method.output_endpoint_ids)),
        )
        for method in input_methods
    ]).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _deduplicate_code_flows(flows: list[CodeFlow]) -> list[CodeFlow]:
    """Keep one representative for each evidenced endpoint-to-endpoint flow.

    Method-call enumeration can expose several implementation/dispatch routes
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
    return sorted(flows, key=lambda flow: (flow.module, flow.path, flow.start_line, flow.id))


def _build_codeql_call_graph(
    methods: list[IntegrationMethod],
    calls: list[CodeQLCall],
    *,
    repo_root: Path | None,
    source_paths: Sequence[str],
    progress: Callable[[str], None] | None,
) -> CodeQLCallGraph:
    """Resolve CodeQL rows into the internal graph consumed by reconstruction."""
    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    def normalized_method_name(name: str) -> str:
        """Align analyzer method names with Java source declarations."""
        normalized = name.replace("$", ".").split(":", 1)[0]
        open_parenthesis = normalized.find("(")
        if open_parenthesis >= 0:
            normalized = normalized[:open_parenthesis]
        return normalized

    def normalized_path(path: str) -> str:
        """Normalize harmless extractor spelling differences in source paths."""
        return path.replace("\\", "/").removeprefix("./")

    by_locator: dict[tuple[str, str, int], list[IntegrationMethod]] = defaultdict(list)
    for item in methods:
        by_locator[
            (normalized_method_name(item.qualified_method), normalized_path(item.path), item.start_line)
        ].append(item)
    methods_by_path: dict[str, list[IntegrationMethod]] = defaultdict(list)
    methods_by_name: dict[str, list[IntegrationMethod]] = defaultdict(list)
    for item in methods:
        methods_by_path[normalized_path(item.path)].append(item)
        methods_by_name[normalized_method_name(item.qualified_method)].append(item)

    def locate(
        name: str, path: str, line: int, *, allow_signature_fallback: bool = False,
    ) -> tuple[IntegrationMethod, bool] | None:
        """Resolve one CodeQL method to an indexed method."""
        normalized_name = normalized_method_name(name)
        path = normalized_path(path)
        exact = by_locator.get((normalized_name, path, line), [])
        if len(exact) == 1:
            return exact[0], False
        if len(exact) > 1:
            return None
        same_file = [
            item for item in methods_by_path.get(path, [])
            if normalized_method_name(item.qualified_method) == normalized_name
            and item.start_line <= line <= item.end_line
        ]
        if len(same_file) == 1:
            return same_file[0], False
        if len(same_file) > 1:
            return None
        if allow_signature_fallback:
            candidates = methods_by_name.get(normalized_name, [])
            if len(candidates) == 1:
                return candidates[0], True
        return None

    def locate_caller(call: CodeQLCall) -> IntegrationMethod | None:
        resolved = locate(call.caller, call.caller_path, call.caller_line)
        if resolved is not None:
            return resolved[0]
        if not call.caller.startswith("<anonymous"):
            return None
        enclosing = [
            item for item in methods_by_path.get(normalized_path(call.caller_path), [])
            if item.start_line <= call.call_line <= item.end_line
        ]
        return min(enclosing, key=lambda item: item.end_line - item.start_line) if enclosing else None

    adjacency: dict[str, list[CodeQLEdge]] = defaultdict(list)
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

    started_at = time.monotonic()
    last_report_at = started_at
    for call_number, call in enumerate(calls, start=1):
        now = time.monotonic()
        if now - last_report_at >= 5.0:
            report(
                f"→ CodeQL : rattachement des appels en cours · "
                f"{call_number - 1}/{len(calls)} appel(s) analysé(s) · "
                f"{sum(len(targets) for targets in adjacency.values())} "
                f"rattachement(s) · {now - started_at:.0f} s."
            )
            last_report_at = now
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
            caller = locate_caller(call)
            resolved_target = locate(call.callee, call.callee_path, call.callee_line)
            if caller is not None and resolved_target is not None:
                add_edge(caller, resolved_target[0], call, True)

    return CodeQLCallGraph(
        adjacency=adjacency,
        synthetic_calls=synthetic_calls,
        call_count=len(calls),
        locate=lambda name, path, line: locate(name, path, line),
    )


def materialize_codeql_code_flows(
    methods: list[IntegrationMethod], endpoints: list[MessageEndpoint], calls: list[CodeQLCall],
    *, repo_root: Path | None = None, max_hops: int = 12,
    stats: dict[str, int] | None = None,
    reachability: Sequence[CodeQLReachability] = (),
    source_paths: Sequence[str] = (),
    progress: Callable[[str], None] | None = None,
    join_checkpoint: Callable[[list[CodeFlow], int, int], None] | None = None,
    resume_from_entry: int = 0,
    initial_flows: Sequence[CodeFlow] = (),
    call_graph_sink: Callable[[CodeQLCallGraph], None] | None = None,
    codeql_edge_confidence: str = "possible",
) -> list[CodeFlow]:
    """Reconstruct endpoint flows from the internal CodeQL call graph.

    The call graph is built first from source-located CodeQL rows. This phase
    then traverses its edges from indexed inputs to indexed outputs. Missing or
    imperfect virtual-dispatch edges may be completed by the transient source
    symbol index, but every synthetic edge remains possible/low-confidence.
    """
    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints}
    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    flow_groups: dict[
        tuple[str, str, str], tuple[CodeFlow, set[bytes]]
    ] = {}

    def record_flow(flow: CodeFlow) -> None:
        """Keep one representative while counting distinct route alternatives."""
        endpoint_steps = [step for step in flow.steps if step.endpoint_id]
        key = (
            (endpoint_steps[0].endpoint_id or flow.id) if endpoint_steps else flow.id,
            (endpoint_steps[-1].endpoint_id or "") if endpoint_steps else "",
            flow.status,
        )
        route_signature = tuple(
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
        route_digest = hashlib.blake2b(
            repr(route_signature).encode("utf-8"), digest_size=16
        ).digest()
        current = flow_groups.get(key)
        if current is None:
            flow_groups[key] = (replace(flow, alternative_count=1), {route_digest})
            return
        representative, signatures = current
        if route_digest in signatures:
            return
        signatures.add(route_digest)
        candidate = min(
            (representative, flow),
            key=lambda item: (
                confidence_rank.get(item.confidence, 99),
                len(item.steps),
                item.id,
            ),
        )
        flow_groups[key] = (replace(candidate, alternative_count=len(signatures)), signatures)

    def current_flows() -> list[CodeFlow]:
        return [representative for representative, _signatures in flow_groups.values()]

    def replace_flows(values: list[CodeFlow]) -> None:
        flow_groups.clear()
        for flow in values:
            record_flow(flow)

    for flow in initial_flows:
        record_flow(flow)

    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    report(
        f"→ CodeQL : jointure de {len(calls)} appel(s) avec "
        f"{len(methods)} méthode(s) Java..."
    )
    call_join_started_at = time.monotonic()
    call_graph = _build_codeql_call_graph(
        methods,
        calls,
        repo_root=repo_root,
        source_paths=source_paths,
        progress=progress,
    )
    if call_graph_sink is not None:
        call_graph_sink(call_graph)
    adjacency = call_graph.adjacency
    synthetic_calls = call_graph.synthetic_calls
    call_count = call_graph.call_count
    locate = call_graph.locate
    if codeql_edge_confidence not in {"exact", "possible"}:
        raise ValueError(
            f"Invalid CodeQL edge confidence {codeql_edge_confidence!r}; "
            "expected 'exact' or 'possible'."
        )
    if codeql_edge_confidence == "exact":
        adjacency = {
            caller_id: [
                (target, call, inferred)
                for target, call, inferred in targets
                if not inferred and call.dispatch_confidence == "exact"
            ]
            for caller_id, targets in adjacency.items()
        }
    joined_calls = sum(len(targets) for targets in adjacency.values())
    report(
        f"→ CodeQL : graphe d'appels interne construit · {joined_calls} "
        f"arête(s) rattachée(s) à {len(adjacency)} méthode(s) appelante(s)."
    )
    components = call_graph.connected_components(tuple(method.id for method in methods))
    component_by_method = {
        method_id: component
        for component in components
        for method_id in component
    }
    report(
        f"→ CodeQL : {len(components)} composante(s) connexe(s) dans le "
        "graphe d'appels interne."
    )
    report("→ CodeQL : reconstruction des parcours input → output...")

    # Contract-only HTTP inputs (for example generated OpenAPI interfaces)
    # have no source endpoint path for the AST materializer. If the same
    # indexed method also owns an output endpoint, the method itself is still
    # sufficient evidence for a direct input-to-output flow.
    input_methods = [entry for entry in methods if entry.input_endpoint_ids]
    if resume_from_entry < 0 or resume_from_entry > len(input_methods):
        raise ValueError(
            f"Invalid CodeQL join resume offset {resume_from_entry}; "
            f"expected 0..{len(input_methods)}."
        )
    explored_entries = resume_from_entry
    last_reported_explored = 0
    last_exploration_report_at = time.monotonic()
    for entry in input_methods[resume_from_entry:]:
        for trigger_id in entry.input_endpoint_ids:
            trigger = endpoint_by_id.get(trigger_id)
            if trigger is None:
                continue
            for output_id in entry.output_endpoint_ids:
                output = endpoint_by_id.get(output_id)
                if output is None:
                    continue
                record_flow(CodeFlow(
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
        component = component_by_method.get(entry.id, frozenset({entry.id}))
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
                    if target.id not in component:
                        continue
                    if not dispatch_matches_entry(entry, current, target):
                        continue
                    explored += 1
                    now = time.monotonic()
                    if now - last_exploration_report_at >= 5.0:
                        report(
                            f"→ CodeQL : jointure en cours · méthode IN "
                            f"{explored_entries + 1}/{len(input_methods)} · "
                            f"{explored} transition(s) explorée(s) · "
                            f"{len(current_flows())} flux... "
                            f"({now - call_join_started_at:.0f} s)"
                        )
                        last_exploration_report_at = now
                    next_route = [*route, (target, call, signature_join)]
                    if target.id == entry.id or any(previous.id == target.id for previous, _edge, _signature in route):
                        steps = [_endpoint_step(trigger, 1)]
                        for order, (hop, edge, _signature_join) in enumerate(next_route, start=2):
                            steps.append(CodeFlowStep(
                                order=order, kind="method_call", name=hop.qualified_method,
                                path=edge.caller_path, start_line=edge.call_line, end_line=edge.call_line,
                            ))
                        record_flow(CodeFlow(
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
                            record_flow(CodeFlow(
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
        explored_entries += 1
        now = time.monotonic()
        if (
            explored_entries == len(input_methods)
            or explored_entries % 25 == 0
            or explored - last_reported_explored >= 5_000
            or now - last_exploration_report_at >= 5.0
        ):
            report(
                f"→ CodeQL : jointure en cours · méthode IN "
                f"{explored_entries}/{len(input_methods)} · "
                f"{explored} transition(s) explorée(s) · {len(current_flows())} flux... "
                f"({now - call_join_started_at:.0f} s)"
            )
            if join_checkpoint is not None:
                join_checkpoint(current_flows(), explored_entries, len(input_methods))
            last_reported_explored = explored
            last_exploration_report_at = now
    if stats is not None:
        stats.update({
            "calls": call_count, "joined_calls": sum(len(targets) for targets in adjacency.values()),
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
                            "CodeQL proved reverse reachability and the indexed method-call graph "
                            "provided a representative intermediate route."
                        ),
                        steps=tuple([*steps, _endpoint_step(output, len(steps) + 1)]),
                    )
        retained_flows = [
            flow for flow in current_flows()
            if flow.status == "cycle" or
            (flow.steps[0].endpoint_id, flow.steps[-1].endpoint_id) not in direct_flows
        ]
        replace_flows([*retained_flows, *direct_flows.values()])
    report(
        f"→ CodeQL : jointure terminée · {explored} transition(s), "
        f"{len(reachability)} reachability(s), {len(current_flows())} flux dédupliqué(s)."
    )
    return _deduplicate_code_flows(current_flows())


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
