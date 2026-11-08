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
    DATA_STEP_KINDS,
    entry_dispatch_allowed,
    inherited_method_anchor,
    CodeFlow,
    CodeFlowStep,
    CallGraphEdge,
    compute_code_flow_id,
    ensure_unique_code_flow_ids,
    PersistedCallGraphMethod,
)
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.domain.data_operation_projection import data_operations_from_modules
from systemlens.domain.data_operations import DataOperation, key_identity_base
from systemlens.domain.graph import GraphEdge
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule, ModuleDependency, module_identity
from systemlens.callgraph.model import MethodCall, MethodReachability
from systemlens.indexing.integration_methods import (
    SCHEDULER_TRIGGER,
    is_initializer,
    path_module_resolver,
    scheduled_trigger_label,
)
from systemlens.indexing.java_symbols import JavaSymbols
from systemlens.indexing.java_value_origins import trace_scalar_key_identity
from systemlens.storage.persisted_names import (
    CALL_GRAPH_METHOD_ID_PREFIX,
    DIRECT_REACHABILITY_FLOW_MARKER,
)


CODE_FLOW_SIGNATURE = "code-flow-v56-inherited-routes-and-triggers"
_TRIGGER_ROLES = {("rest", "serve"), ("kafka", "consume")}
_EFFECT_ROLES = {("rest", "call"), ("kafka", "produce")}


CallEdgeEntry = tuple[IntegrationMethod, MethodCall, bool]


def _anchor_steps(flow: CodeFlow) -> list[CodeFlowStep]:
    """Steps that identify a flow's ends: endpoints and scheduler triggers.

    A scheduler trigger is transient and carries no endpoint ID, so its
    source location is its identity. Without it, two schedulers reaching
    the same publication would share one deduplication key.
    """
    return [step for step in flow.steps if step.endpoint_id or step.kind == "cron_entry"]


def _step_anchor(step: CodeFlowStep) -> str:
    return step.endpoint_id or f"{step.kind}:{step.path}:{step.start_line}"


@dataclass(frozen=True)
class CallGraph:
    """The source-backed internal call graph used for flow reconstruction."""

    adjacency: dict[str, list[CallEdgeEntry]]
    synthetic_calls: set[MethodCall]
    call_count: int
    locate: Callable[[str, str, int], tuple[IntegrationMethod, bool] | None]
    methods: tuple[PersistedCallGraphMethod, ...] = ()
    symbols: JavaSymbols | None = None
    # ``(caller ID, call line, callee ID)`` of the edges whose call runs on
    # an object other than the caller's ``this`` (ADR-43 amendment, BL-47).
    other_receiver_sites: frozenset[tuple[str, int, str]] = frozenset()

    def on_this(self, caller_id: str, call: MethodCall, callee_id: str) -> bool:
        """Whether a call may run on the caller's own instance."""
        return (caller_id, call.call_line, callee_id) not in self.other_receiver_sites

    @property
    def joined_calls(self) -> int:
        return sum(len(targets) for targets in self.adjacency.values())

    def edges(self) -> tuple[CallGraphEdge, ...]:
        """Return deterministic persisted edges for the internal call graph."""
        return tuple(sorted(
            (
                CallGraphEdge(
                    caller_id=caller_id,
                    callee_id=callee.id,
                    path=call.caller_path.replace("\\", "/").removeprefix("./"),
                    line=call.call_line,
                    dispatch_confidence=call.dispatch_confidence,
                    inferred=inferred,
                    other_receiver=not self.on_this(caller_id, call, callee.id),
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
                neighbours.setdefault(target.id, set())
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


def call_graph_join_methods_signature(methods: Sequence[IntegrationMethod]) -> str:
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


def _deduplicate_code_flows(
    flows: list[CodeFlow], endpoints: Sequence[MessageEndpoint] = ()
) -> list[CodeFlow]:
    """Keep one representative for each evidenced endpoint-to-endpoint flow.

    Method-call enumeration can expose several implementation/dispatch routes
    for the same integration pair. Those routes are useful while debugging
    the join, but are not distinct application flows. Prefer a route with
    method-call evidence, then confidence and deterministic length.
    """
    confidence_rank = {"high": 0, "medium": 1, "low": 2}

    def route_signature(flow: CodeFlow) -> tuple[tuple[object, ...], ...]:
        """Return the evidence-bearing shape of one candidate route.

        AST and an engine can emit the same route independently.  They must be
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

    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints}

    def flow_identity(flow: CodeFlow) -> tuple[object, ...]:
        endpoint_steps = _anchor_steps(flow)
        data_step = (
            flow.steps[-1] if flow.steps and flow.steps[-1].kind in DATA_STEP_KINDS else None
        )
        input_endpoint = (
            endpoint_by_id.get(endpoint_steps[0].endpoint_id or "")
            if endpoint_steps
            else None
        )
        output_endpoint = (
            endpoint_by_id.get(endpoint_steps[-1].endpoint_id or "")
            if endpoint_steps
            else None
        )
        return (
            flow.module,
            _step_anchor(endpoint_steps[0]) if endpoint_steps else None,
            input_endpoint.topic if input_endpoint else (
                endpoint_steps[0].name if endpoint_steps else None
            ),
            input_endpoint.message_type if input_endpoint else None,
            _step_anchor(endpoint_steps[-1]) if endpoint_steps else None,
            output_endpoint.topic if output_endpoint else (
                endpoint_steps[-1].name if endpoint_steps else None
            ),
            output_endpoint.message_type if output_endpoint else None,
            (data_step.path, data_step.start_line, data_step.operation)
            if data_step is not None else None,
            flow.status,
        )

    grouped: dict[tuple[object, ...], list[CodeFlow]] = {}
    for flow in flows:
        if not _anchor_steps(flow):
            key = flow_identity(flow) + (flow.id,)
        else:
            key = flow_identity(flow)
        grouped.setdefault(key, []).append(flow)
    selected = []
    for _, parallel in sorted(grouped.items(), key=lambda item: repr(item[0])):
        representative = min(
            parallel,
            key=lambda flow: (
                0 if any(step.kind == "method_call" for step in flow.steps) else 1,
                confidence_rank.get(flow.confidence, 99),
                -sum(step.kind == "method_call" for step in flow.steps),
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


_ensure_unique_code_flow_ids = ensure_unique_code_flow_ids


def _effect_anchor(step: CodeFlowStep) -> tuple[object, ...]:
    if step.endpoint_id is not None:
        return ("endpoint", step.endpoint_id)
    if step.operation_id is not None:
        return ("operation", step.operation_id)
    return (step.kind, step.path, step.start_line, step.operation)


def without_same_method_duplicates(
    source_flows: Sequence[CodeFlow], call_graph_flows: Sequence[CodeFlow],
) -> list[CodeFlow]:
    """Drop the join's same-method flows that a same-method flow already holds.

    The same-method pass puts every effect of one method in one flow, while
    the call-graph join emits one direct flow per effect. A direct join flow
    whose trigger and effects all appear in a same-method flow of that
    trigger is the same evidence: keeping it would give each engine and
    ``flows calculate`` a different flow set (review F4). A direct flow the
    same-method pass lacks, such as a contract-only HTTP input, is kept.
    """
    covered = {
        (_step_anchor(flow.steps[0]), _effect_anchor(step))
        for flow in source_flows if flow.steps
        for step in flow.steps[1:]
    }
    return [
        flow for flow in call_graph_flows
        if not flow.steps
        or any(step.kind == "method_call" for step in flow.steps)
        or not all(
            (_step_anchor(flow.steps[0]), _effect_anchor(step)) in covered
            for step in flow.steps[1:]
        )
    ]


def combine_code_flows(
    source_flows: Sequence[CodeFlow],
    call_graph_flows: Sequence[CodeFlow],
    endpoints: Sequence[MessageEndpoint],
) -> list[CodeFlow]:
    """Merge same-method and call-graph flows into the one persisted flow set.

    Indexing with any engine and ``flows calculate`` call this one merge, so
    the same inputs give the same flows and identifiers.
    """
    return _ensure_unique_code_flow_ids(_deduplicate_code_flows(
        [*source_flows, *without_same_method_duplicates(source_flows, call_graph_flows)],
        endpoints,
    ))


def _endpoint_step(endpoint: MessageEndpoint, order: int) -> CodeFlowStep:
    if (endpoint.system, endpoint.role) == SCHEDULER_TRIGGER:
        # Scheduler triggers are transient, so the step names no endpoint.
        return CodeFlowStep(
            order=order, kind="cron_entry", name=endpoint.topic, path=endpoint.path,
            start_line=endpoint.start_line, end_line=endpoint.end_line,
        )
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


def _data_access_branch(trigger_kind: str, *, write: bool) -> str | None:
    """Classify only async consumer writes and reads on synchronous HTTP paths."""
    if write and trigger_kind == "message_entry":
        return "async"
    if not write and trigger_kind == "http_entry":
        return "sync"
    return None


def _operation_step(operation: DataOperation, order: int, trigger_kind: str) -> CodeFlowStep:
    """Project one persisted operation fact onto a flow step.

    The broad ``data_read``/``data_write`` kind serves existing consumers; a
    ``read_update`` operation is a write step that keeps its read facet in
    ``action``. An unknown action yields ``data_access`` so no read or write
    relation is asserted.
    """
    if operation.action == "unknown":
        kind = "data_access"
    elif operation.writes:
        kind = "data_write"
    else:
        kind = "data_read"
    if operation.technology == "mongodb":
        name = operation.resource or "<dynamic>"
        resource = operation.resource
    elif operation.resource is not None:
        name = operation.resource.rsplit(".", 1)[-1]
        resource = operation.resource_identity
    else:
        name = "<unresolved>"
        resource = None
    return CodeFlowStep(
        order=order, kind=kind, name=name, path=operation.path,
        start_line=operation.line, end_line=operation.end_line,
        operation=operation.operation, action=operation.action,
        key=operation.key_expression, resource=resource,
        resource_identity=operation.resource_identity,
        key_identity=operation.key_identity,
        branch=(
            None if kind == "data_access"
            else _data_access_branch(trigger_kind, write=kind == "data_write")
        ),
        transaction_id=operation.transaction_id,
        isolation=operation.isolation,
        guard=operation.guard,
        guard_scope=operation.guard_scope,
        value_dependencies=operation.value_dependencies,
        operation_id=operation.id,
    )


def _operation_sort_key(operation: DataOperation) -> tuple[int, str]:
    return operation.line, f"{operation.technology}:{operation.operation}:{operation.resource or ''}"


def _scheduled_trigger_step(
    method_node: Node, source: bytes, path: str
) -> CodeFlowStep | None:
    """Return a source-evidenced cron trigger for a scheduled Java method."""
    for annotation in java_parser.annotations_of(method_node):
        if java_parser.annotation_name(annotation, source) != "Scheduled":
            continue
        return CodeFlowStep(
            order=1,
            kind="cron_entry",
            name=scheduled_trigger_label(annotation, source),
            path=path,
            start_line=method_node.start_point.row + 1,
            end_line=method_node.start_point.row + 1,
        )
    return None


def _after_commit_lines(repo_root: Path, path: str) -> list[tuple[int, int]]:
    """Line ranges of methods annotated ``@TransactionalEventListener``."""
    parsed = java_parser.parse_java(str(repo_root.resolve()), path)
    if parsed is None:
        return []
    source, root = parsed
    ranges: list[tuple[int, int]] = []
    for node in java_parser.walk(root):
        if node.type != "method_declaration":
            continue
        if any(
            java_parser.annotation_name(annotation, source) == "TransactionalEventListener"
            for annotation in java_parser.annotations_of(node)
        ):
            ranges.append((node.start_point.row + 1, node.end_point.row + 1))
    return ranges


def mark_after_commit_publications(
    repo_root: Path | None, flows: list[CodeFlow],
) -> list[CodeFlow]:
    """Phase a publication ``after_commit`` when its method is a transactional listener.

    The annotation is scope evidence for the dual-write review aid only; it
    neither confirms a transaction nor an outbox. Without a repository root
    (a rebuild without sources) steps are returned unchanged.
    """
    if repo_root is None:
        return flows
    cache: dict[str, list[tuple[int, int]]] = {}
    result: list[CodeFlow] = []
    for flow in flows:
        steps = list(flow.steps)
        changed = False
        for index, step in enumerate(steps):
            if step.kind != "message_publish" or step.phase is not None:
                continue
            if step.path not in cache:
                cache[step.path] = _after_commit_lines(repo_root, step.path)
            if any(start <= step.start_line <= end for start, end in cache[step.path]):
                steps[index] = replace(step, phase="after_commit")
                changed = True
        result.append(replace(flow, steps=tuple(steps)) if changed else flow)
    return result


def materialize_code_flows(
    repo_root: Path,
    endpoints: list[MessageEndpoint],
    modules: list[DiscoveredModule],
    operations: Sequence[DataOperation] | None = None,
) -> list[CodeFlow]:
    """Return potential flows whose trigger and effects share one Java method.

    Same-method containment proves a useful local relationship, but not that a
    conditional effect executes for every invocation. Flows therefore remain
    explicitly potential with medium confidence. ``operations`` are the
    persisted data facts; without them the module inventory of an older
    index is projected instead.
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

    if operations is None:
        operations = data_operations_from_modules(repo_root, modules)
    operations_by_path_and_method: dict[tuple[str, str, str], list[DataOperation]] = defaultdict(list)
    operation_modules_by_path_and_method: dict[tuple[str, str], set[str]] = defaultdict(set)
    for operation in operations:
        operations_by_path_and_method[
            (operation.module, operation.path, operation.owner_method)
        ].append(operation)
        operation_modules_by_path_and_method[(operation.path, operation.owner_method)].add(
            operation.module
        )

    flows: list[CodeFlow] = []

    indexed_paths = set(endpoints_by_path)
    indexed_paths.update(path for _module, path, _method in operations_by_path_and_method)
    for path in sorted(indexed_paths):
        path_endpoints = endpoints_by_path.get(path, [])
        parsed = java_parser.parse_java(str(repo_root.resolve()), path)
        if parsed is None:
            continue
        source, root = parsed
        method_nodes = [
            node for node in java_parser.walk(root)
            if node.type == "method_declaration" and not java_parser.is_anonymous_member(node)
        ]
        endpoints_by_owner: dict[int, list[MessageEndpoint]] = defaultdict(list)
        for endpoint in path_endpoints:
            candidates = [
                node for node in method_nodes
                if node.start_point.row + 1 <= endpoint.start_line <= node.end_point.row + 1
            ]
            if candidates:
                owner = min(
                    candidates, key=lambda node: node.end_byte - node.start_byte
                )
                endpoints_by_owner[id(owner)].append(endpoint)
        for method_node in method_nodes:
            if method_node.type != "method_declaration":
                continue
            method_name = java_parser.declaration_name(method_node, source)
            if method_name is None:
                continue
            start_line = method_node.start_point.row + 1
            end_line = method_node.end_point.row + 1
            local_endpoints = endpoints_by_owner.get(id(method_node), [])
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
            has_kafka_join = (
                sum(endpoint.system == "kafka" for endpoint in triggers) > 1
                and any(
                    node.type == "method_invocation"
                    and java_parser.invocation_parts(node, source)[1] == "join"
                    for node in java_parser.walk(method_node)
                )
            )
            scheduled_trigger = _scheduled_trigger_step(method_node, source, path)
            for trigger in triggers:
                assert trigger.module is not None
                effects: list[tuple[int, str, MessageEndpoint | DataOperation]] = [
                    (endpoint.start_line, endpoint.id, endpoint)
                    for endpoint in endpoint_effects
                    if endpoint.id != trigger.id
                ]
                effects.extend(
                    (*_operation_sort_key(operation), operation)
                    for operation in operations_by_path_and_method.get(
                        (trigger.module, path, method_name), ()
                    )
                    if start_line <= operation.line <= end_line
                )
                if not effects:
                    continue
                ordered_effects = sorted(effects, key=lambda item: (item[0], item[1]))
                trigger_kind = _endpoint_step(trigger, 1).kind
                steps = [_endpoint_step(trigger, 1)]
                for order, (_line, _key, effect) in enumerate(ordered_effects, start=2):
                    steps.append(
                        _endpoint_step(effect, order)
                        if isinstance(effect, MessageEndpoint)
                        else _operation_step(effect, order, trigger_kind)
                    )
                qualified_method = (
                    f"{trigger.qualified_name}.{method_name}"
                    if trigger.qualified_name
                    else method_name
                )
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
                        "This method joins multiple indexed Kafka inputs before "
                        "the publication; the path remains conditional and potential."
                        if has_kafka_join and trigger.system == "kafka" else
                        "The entry point and external effects occur in the same Java "
                        "method."
                    ),
                    steps=tuple(steps),
                ))
            if scheduled_trigger is not None:
                operation_modules = operation_modules_by_path_and_method.get(
                    (path, method_name), set()
                )
                producer = endpoint_effects[0] if endpoint_effects else None
                owner_module = producer.module if producer else (
                    next(iter(operation_modules)) if len(operation_modules) == 1 else None
                )
                if owner_module is None:
                    continue
                qualified_method = (
                    f"{producer.qualified_name}.{method_name}"
                    if producer is not None and producer.qualified_name
                    else method_name
                )
                scheduled_effects: list[tuple[int, str, MessageEndpoint | DataOperation]] = [
                    (endpoint.start_line, endpoint.id, endpoint)
                    for endpoint in endpoint_effects
                ]
                scheduled_effects.extend(
                    (*_operation_sort_key(operation), operation)
                    for operation in operations_by_path_and_method.get(
                        (owner_module, path, method_name), ()
                    )
                    if start_line <= operation.line <= end_line
                )
                if not scheduled_effects:
                    continue
                scheduled_effects.sort(key=lambda item: (item[0], item[1]))
                steps = [scheduled_trigger]
                for order, (_line, _key, effect) in enumerate(scheduled_effects, start=2):
                    steps.append(
                        _endpoint_step(effect, order)
                        if isinstance(effect, MessageEndpoint)
                        else _operation_step(effect, order, scheduled_trigger.kind)
                    )
                flows.append(CodeFlow(
                    id=compute_code_flow_id(
                        owner_module, path, qualified_method,
                        "cron_entry", scheduled_trigger.name,
                    ),
                    module=owner_module,
                    method=qualified_method,
                    path=path,
                    start_line=start_line,
                    end_line=end_line,
                    status="potential",
                    confidence="medium",
                    reason=(
                        "A scheduled Java method has source-backed effects; "
                        "the scheduler is the flow trigger."
                    ),
                    steps=tuple(steps),
                ))
    return _ensure_unique_code_flow_ids(sorted(
        mark_after_commit_publications(repo_root, flows),
        key=lambda flow: (flow.module, flow.path, flow.start_line, flow.id),
    ))


def _build_call_graph(
    methods: list[IntegrationMethod],
    calls: list[MethodCall],
    *,
    repo_root: Path | None,
    source_paths: Sequence[str],
    progress: Callable[[str], None] | None,
    persisted_edges: Sequence[CallGraphEdge] | None = None,
    persisted_methods: Sequence[PersistedCallGraphMethod] = (),
    closed_world: bool = False,
    modules: Sequence[DiscoveredModule] = (),
    module_dependencies: Sequence[ModuleDependency] = (),
) -> CallGraph:
    """Resolve engine call rows into the internal graph consumed by reconstruction."""
    def prefer_caller_module(
        candidates: Sequence[IntegrationMethod], caller: IntegrationMethod,
    ) -> tuple[IntegrationMethod, ...]:
        local = tuple(candidate for candidate in candidates if candidate.module == caller.module)
        return local or tuple(candidates)

    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    def normalized_method_name(name: str) -> str:
        """Align analyzer method names with Java source declarations."""
        normalized = name.replace("$", ".").split(":", 1)[0]
        # An engine may use ``$`` as the qualified-name marker for a type in the
        # unnamed package.  Replacing it with the nested-type separator would
        # otherwise leave a leading ``..`` and prevent the source join.
        normalized = normalized.lstrip(".")
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
    methods_by_path_and_name: dict[tuple[str, str], list[IntegrationMethod]] = defaultdict(list)
    methods_by_name: dict[str, list[IntegrationMethod]] = defaultdict(list)
    module_by_path: dict[str, str] = {}
    for item in methods:
        path = normalized_path(item.path)
        name = normalized_method_name(item.qualified_method)
        methods_by_path[path].append(item)
        methods_by_path_and_name[(path, name)].append(item)
        methods_by_name[name].append(item)
        module_by_path.setdefault(path, item.module)

    module_of_path = (
        path_module_resolver(repo_root, modules)
        if repo_root is not None and modules else (lambda _path: None)
    )

    def placeholder(name: str, path: str, line: int) -> IntegrationMethod:
        """Represent a non-port engine method only inside the transient graph.

        Its module is the discovered build module owning the path, the same
        identity indexed methods carry, so application boundaries apply to
        it. A path outside every discovered module has no module.
        """
        normalized_path = normalized_path_value(path)
        module = module_by_path.get(normalized_path) or module_of_path(normalized_path) or ""
        method_id = CALL_GRAPH_METHOD_ID_PREFIX + hashlib.sha256(
            f"{module}|{normalized_path}|{line}|{normalized_method_name(name)}".encode()
        ).hexdigest()[:24]
        return IntegrationMethod(
            id=method_id,
            module=module,
            qualified_method=normalized_method_name(name),
            path=normalized_path,
            start_line=line,
            end_line=line,
            input_endpoint_ids=(),
            output_endpoint_ids=(),
        )

    def normalized_path_value(path: str) -> str:
        return path.replace("\\", "/").removeprefix("./")

    transient_methods: dict[tuple[str, str, int], IntegrationMethod] = {}
    known_source_paths = {
        normalized_path_value(path) for path in source_paths
    } | {normalized_path_value(method.path) for method in methods}
    for call in calls:
        for name, path, line in (
            (call.caller, call.caller_path, call.caller_line),
            (call.callee, call.callee_path, call.callee_line),
        ):
            if (
                name.startswith("<anonymous")
                or not path
                or line <= 0
                or normalized_path_value(path) not in known_source_paths
            ):
                continue
            key = (normalized_method_name(name), normalized_path_value(path), line)
            if is_initializer(key[0]):
                # The index holds every written constructor and every
                # initializer that makes a call (ADR-75); an engine's other
                # ones, such as a default constructor, would be dead ends.
                continue
            has_enclosing_indexed_method = any(
                item.start_line <= line <= item.end_line
                for item in methods_by_path_and_name.get((key[1], key[0]), ())
            )
            if not by_locator.get(key) and not has_enclosing_indexed_method:
                transient_methods.setdefault(key, placeholder(name, path, line))
    for method in transient_methods.values():
        by_locator[
            (normalized_method_name(method.qualified_method), normalized_path_value(method.path), method.start_line)
        ].append(method)
        methods_by_path[normalized_path_value(method.path)].append(method)
        methods_by_name[normalized_method_name(method.qualified_method)].append(method)

    graph_methods_by_id = {
        method.id: method for method in [*methods, *transient_methods.values()]
    }

    def method_facts(values: dict[str, IntegrationMethod]) -> tuple[PersistedCallGraphMethod, ...]:
        return tuple(
            PersistedCallGraphMethod(
                id=method.id,
                module=method.module,
                qualified_method=method.qualified_method,
                path=method.path,
                start_line=method.start_line,
                end_line=method.end_line,
            )
            for method in sorted(values.values(), key=lambda item: item.id)
        )

    def simple_type(written: str) -> str:
        """Reduce a written or engine type name to its simple erased name."""
        depth = 0
        kept = []
        for character in written:
            if character == "<":
                depth += 1
            elif character == ">":
                depth = max(0, depth - 1)
            elif depth == 0:
                kept.append(character)
        reduced = "".join(kept).replace("...", "[]").replace(" ", "")
        return reduced.rsplit(".", 1)[-1]

    def same_signature(
        method: IntegrationMethod, parameter_types: tuple[str, ...],
    ) -> bool:
        """Whether a method may be the engine's declaration of these types.

        An unknown signature on either side, or an unnamed engine type,
        matches: only a provable difference separates two overloads.
        """
        if method.parameter_types is None:
            return True
        if len(method.parameter_types) != len(parameter_types):
            return False
        return all(
            not simple_type(engine) or simple_type(engine) == simple_type(written)
            for written, engine in zip(method.parameter_types, parameter_types)
        )

    def overload(
        candidates: Sequence[IntegrationMethod], parameter_types: tuple[str, ...] | None,
    ) -> list[IntegrationMethod]:
        """Keep the same-line overloads whose signature the engine names."""
        if len(candidates) <= 1 or parameter_types is None:
            return list(candidates)
        return [item for item in candidates if same_signature(item, parameter_types)]

    def locate(
        name: str, path: str, line: int, *, allow_signature_fallback: bool = False,
        parameter_types: tuple[str, ...] | None = None,
    ) -> tuple[IntegrationMethod, bool] | None:
        """Resolve one engine method to an indexed method.

        Same-name overloads declared on one line share the locator; the
        engine's ``parameter_types``, when known, choose between them.
        """
        normalized_name = normalized_method_name(name)
        path = normalized_path(path)
        exact = overload(by_locator.get((normalized_name, path, line), []), parameter_types)
        if len(exact) == 1:
            return exact[0], False
        if len(exact) > 1:
            return None
        same_file = overload([
            item for item in methods_by_path_and_name.get((path, normalized_name), ())
            if item.start_line <= line <= item.end_line
        ], parameter_types)
        if len(same_file) == 1:
            return same_file[0], False
        if len(same_file) > 1:
            return None
        if allow_signature_fallback:
            candidates = methods_by_name.get(normalized_name, [])
            if len(candidates) == 1:
                return candidates[0], True
        return None

    def locate_caller(call: MethodCall) -> IntegrationMethod | None:
        resolved = locate(
            call.caller, call.caller_path, call.caller_line,
            parameter_types=call.caller_parameter_types,
        )
        if resolved is not None:
            return resolved[0]
        if not call.caller.startswith("<anonymous"):
            return None
        enclosing = [
            item for item in methods_by_path.get(normalized_path(call.caller_path), [])
            if item.start_line <= call.call_line <= item.end_line
        ]
        return min(enclosing, key=lambda item: item.end_line - item.start_line) if enclosing else None

    adjacency: dict[str, list[CallEdgeEntry]] = defaultdict(list)
    symbols = (
        JavaSymbols(repo_root, methods, source_paths, module_dependencies=module_dependencies)
        if repo_root is not None else None
    )
    implementation_targets: dict[tuple[str, str], tuple[IntegrationMethod, ...]] = {}
    resolved_sites: set[tuple[str, int, str]] = set()
    # Sites whose concrete target the engine resolved exactly: neither its
    # interface witness nor the symbol fallback adds another target there.
    exact_resolved_sites: set[tuple[str, int, str]] = set()
    seen_edges: set[tuple[str, str, int, str, bool]] = set()
    synthetic_calls: set[MethodCall] = set()
    # Witness expansions, dropped at the end when a declared edge already
    # reaches the same callee at the same site (review A4).
    witness_edges: set[tuple[str, str, int]] = set()

    def add_edge(
        caller: IntegrationMethod, callee: IntegrationMethod, call: MethodCall,
        inferred: bool = False,
    ) -> None:
        key = (caller.id, callee.id, call.call_line, call.dispatch_confidence, inferred)
        if key not in seen_edges:
            seen_edges.add(key)
            adjacency[caller.id].append((callee, call, inferred))

    dependencies_by_module: dict[str, set[str]] = defaultdict(set)
    for dependency in module_dependencies:
        dependencies_by_module[dependency.source].add(dependency.target)
    visible_by_module: dict[str, frozenset[str]] = {}

    def module_path(module: str) -> frozenset[str]:
        """``module`` and its transitive build dependencies."""
        modules = visible_by_module.get(module)
        if modules is None:
            seen = {module}
            pending = [module]
            while pending:
                for target in dependencies_by_module.get(pending.pop(), ()):
                    if target not in seen:
                        seen.add(target)
                        pending.append(target)
            modules = visible_by_module[module] = frozenset(seen)
        return modules

    def visible(caller: IntegrationMethod, callee: IntegrationMethod) -> bool:
        """Whether a typed engine's edge may join two modules (ADR-71).

        A typed engine resolves over every projected type, without the
        module dependencies. The callee's module must be on the caller's
        module path, or depend on the caller's module: a module built on a
        library can implement the library's interfaces, as an adapter
        implements a port. Two modules unrelated by the build graph are
        never joined. A method without a module is not judged, and neither
        is any edge when the index knows no module dependency at all:
        without a build graph, the engine's typed evidence stands.
        """
        if (
            not dependencies_by_module or not caller.module or not callee.module
            or caller.module == callee.module
        ):
            return True
        return callee.module in module_path(caller.module) or caller.module in module_path(callee.module)

    def dispatch_expansion(
        caller: IntegrationMethod, callee: IntegrationMethod, exact_targets: set[str],
    ) -> tuple[IntegrationMethod, ...]:
        """The implementations a call on a declared contract dispatches to.

        Those of the caller's module come first; a typed engine's dispatch
        keeps to the modules the caller can see (ADR-71). When the site
        already reaches one of them exactly, the expansion opens no new
        route and keeps only that target (ADR-70). The fresh join and the
        rebuild from persisted edges share this rule (BL-51).
        """
        assert symbols is not None
        target_key = (callee.id, caller.module)
        if target_key not in implementation_targets:
            info = symbols.methods.get(callee.id)
            implementation_targets[target_key] = prefer_caller_module(
                tuple(candidate.method for candidate in symbols.implementations(info))
                if info is not None and not info.concrete else (),
                caller,
            )
        candidates = tuple(
            candidate for candidate in implementation_targets[target_key]
            if visible(caller, candidate)
        )
        if any(candidate.id in exact_targets for candidate in candidates):
            candidates = tuple(candidate for candidate in candidates if candidate.id in exact_targets)
        return candidates

    if persisted_edges is not None:
        methods_by_id = {method.id: method for method in methods}
        for persisted in persisted_methods:
            if persisted.id in methods_by_id:
                continue
            methods_by_id[persisted.id] = IntegrationMethod(
                id=persisted.id,
                module=persisted.module,
                qualified_method=persisted.qualified_method,
                path=persisted.path,
                start_line=persisted.start_line,
                end_line=persisted.end_line,
                input_endpoint_ids=(),
                output_endpoint_ids=(),
            )
        # Declared exact targets per site: as in a fresh join, the witness
        # beside an exact target dispatches only there and opens no route
        # (ADR-70, BL-29).
        persisted_exact: dict[tuple[str, int], set[str]] = defaultdict(set)
        for edge in persisted_edges:
            if edge.dispatch_confidence == "exact" and not edge.inferred:
                persisted_exact[(edge.caller_id, edge.line)].add(edge.callee_id)
        persisted_other_receiver: set[tuple[str, int, str]] = set()
        for edge in persisted_edges:
            caller = methods_by_id.get(edge.caller_id)
            callee = methods_by_id.get(edge.callee_id)
            if caller is None or callee is None:
                continue
            if edge.other_receiver:
                persisted_other_receiver.add((caller.id, edge.line, callee.id))
            add_edge(
                caller,
                callee,
                MethodCall(
                    caller=caller.qualified_method,
                    caller_path=caller.path,
                    caller_line=caller.start_line,
                    callee=callee.qualified_method,
                    callee_path=callee.path,
                    callee_line=callee.start_line,
                    call_line=edge.line,
                    dispatch_confidence=edge.dispatch_confidence,
                ),
                edge.inferred,
            )
            if symbols is not None and not edge.inferred:
                callee_info = symbols.methods.get(callee.id)
                if callee_info is not None and not callee_info.concrete:
                    exact_targets = persisted_exact.get((caller.id, edge.line), set())
                    for candidate in dispatch_expansion(caller, callee, exact_targets):
                        if candidate.id in exact_targets:
                            # The exact edge already reaches the site's target.
                            continue
                        synthetic = MethodCall(
                            caller=caller.qualified_method,
                            caller_path=caller.path,
                            caller_line=caller.start_line,
                            callee=candidate.qualified_method,
                            callee_path=candidate.path,
                            callee_line=candidate.start_line,
                            call_line=edge.line,
                            dispatch_confidence="possible",
                        )
                        synthetic_calls.add(synthetic)
                        add_edge(caller, candidate, synthetic, True)
                        if edge.other_receiver:
                            # The expansion keeps the site's receiver.
                            persisted_other_receiver.add((caller.id, edge.line, candidate.id))
        return CallGraph(
            adjacency=adjacency,
            synthetic_calls=synthetic_calls,
            call_count=len(persisted_edges),
            locate=lambda name, path, line: locate(name, path, line),
            methods=tuple(persisted_methods),
            symbols=symbols,
            other_receiver_sites=frozenset(persisted_other_receiver),
        )

    started_at = time.monotonic()
    last_report_at = started_at
    located_calls: list[tuple[MethodCall, IntegrationMethod, IntegrationMethod, bool]] = []
    # Exact targets per call site. When the engine resolved the site's exact
    # implementation, its interface witness dispatches only there (ADR-70).
    exact_site_targets: dict[tuple[str, str, int], set[str]] = {}
    constant_body_calls: list[tuple[IntegrationMethod, IntegrationMethod, MethodCall]] = []
    for call in calls:
        caller = locate_caller(call)
        resolved_callee = locate(
            call.callee, call.callee_path, call.callee_line,
            allow_signature_fallback=True,
            parameter_types=call.callee_parameter_types,
        )
        if caller is None or resolved_callee is None:
            continue
        if not visible(caller, resolved_callee[0]):
            continue
        if call.dispatch_confidence == "exact" and symbols is not None:
            overrides = [
                override for override in symbols.constant_body_overrides(resolved_callee[0])
                if visible(caller, override)
            ]
            if overrides:
                # An enum constant body the engine does not model can
                # receive the call: no body is the only one (H9, ADR-81).
                call = replace(call, dispatch_confidence="possible")
                constant_body_calls.extend(
                    (caller, override, replace(
                        call, callee=override.qualified_method, callee_path=override.path,
                        callee_line=override.start_line,
                    ))
                    for override in overrides
                )
        located_calls.append((call, caller, *resolved_callee))
        if call.dispatch_confidence == "exact" and not resolved_callee[1]:
            exact_site_targets.setdefault(
                (caller.id, call.caller_path, call.call_line), set()
            ).add(resolved_callee[0].id)
    for call_number, (call, caller, callee, signature_join) in enumerate(located_calls, start=1):
        now = time.monotonic()
        if now - last_report_at >= 5.0:
            report(
                f"→ Graphe d'appel : rattachement des appels en cours · "
                f"{call_number - 1}/{len(located_calls)} appel(s) analysé(s) · "
                f"{sum(len(targets) for targets in adjacency.values())} "
                f"rattachement(s) · {now - started_at:.0f} s."
            )
            last_report_at = now
        add_edge(caller, callee, call, signature_join)
        if symbols is not None:
            info = symbols.methods.get(callee.id)
            if info is not None and info.concrete:
                resolved_sites.add((caller.id, call.call_line, callee.id))
                if call.dispatch_confidence == "exact" and not signature_join:
                    exact_resolved_sites.add((caller.id, call.call_line, callee.id))
            elif info is not None and not signature_join:
                # The engine already names this declared contract (a Spring
                # Data query method): the symbol pass must not add it again
                # as a second, inferred edge (review D4).
                resolved_sites.add((caller.id, call.call_line, callee.id))
            # The witness opens no new route beside a resolved target.
            candidates = dispatch_expansion(caller, callee, exact_site_targets.get(
                (caller.id, call.caller_path, call.call_line), set()
            ))
            for candidate in candidates:
                synthetic = replace(
                    call, callee=candidate.qualified_method, callee_path=candidate.path,
                    callee_line=candidate.start_line, dispatch_confidence="possible",
                )
                synthetic_calls.add(synthetic)
                add_edge(caller, candidate, synthetic, True)
                witness_edges.add((caller.id, candidate.id, call.call_line))

    for caller, override, synthetic in constant_body_calls:
        synthetic_calls.add(synthetic)
        add_edge(caller, override, synthetic, True)

    if symbols is not None:
        for call in symbols.fallback_calls(
            resolved_sites, closed_world=closed_world, exact_sites=exact_resolved_sites,
        ):
            caller = locate_caller(call)
            resolved_target = locate(call.callee, call.callee_path, call.callee_line)
            if caller is None or resolved_target is None:
                continue
            target = resolved_target[0]
            if not closed_world and not visible(caller, target):
                # Beside a typed engine, the gap filler keeps the engine's
                # module scope (ADR-71); as the whole call graph, the source
                # pass applies ADR-71 itself.
                continue
            synthetic_calls.add(call)
            exact_call = symbols.interface_witnesses.get(call)
            implementation = (
                locate(exact_call.callee, exact_call.callee_path, exact_call.callee_line)
                if exact_call is not None else None
            )
            if exact_call is not None and implementation is not None:
                # ADR-70 witness: the interface method the call names is a
                # declared target, like a typed engine's row, and the join adds the
                # possible dispatch to the site's exact implementation, the
                # shape the engine-call join builds above. No other
                # implementation is added: the witness opens no new route.
                add_edge(caller, target, call)
                synthetic = replace(exact_call, dispatch_confidence="possible")
                synthetic_calls.add(synthetic)
                add_edge(caller, implementation[0], synthetic, True)
                witness_edges.add((caller.id, implementation[0].id, call.call_line))
                continue
            # An exact source call names its target from the declared
            # receiver type, and so does a call to a declaration without a
            # body, such as a Spring Data query method: the receiver type
            # declares it (review D4). Only candidate dispatch to a concrete
            # body is an inference.
            target_info = symbols.methods.get(target.id)
            declared_contract = target_info is not None and not target_info.concrete
            add_edge(
                caller, target, call,
                call.dispatch_confidence != "exact" and not declared_contract,
            )

    # The witness hop is the declared edge to the interface method. Its
    # expansion to an implementation the site already reaches through a
    # declared edge adds no route, only a second search state (review A4).
    declared_edges = {
        (caller_id, callee.id, call.call_line)
        for caller_id, entries in adjacency.items()
        for callee, call, inferred in entries
        if not inferred
    }
    for caller_id, entries in adjacency.items():
        adjacency[caller_id] = [
            (callee, call, inferred) for callee, call, inferred in entries
            if not (
                inferred
                and (caller_id, callee.id, call.call_line) in witness_edges
                and (caller_id, callee.id, call.call_line) in declared_edges
            )
        ]
    return CallGraph(
        adjacency=adjacency,
        synthetic_calls=synthetic_calls,
        call_count=len(calls),
        locate=lambda name, path, line: locate(name, path, line),
        methods=method_facts(graph_methods_by_id),
        symbols=symbols,
        other_receiver_sites=(
            other_receiver_sites(symbols, adjacency) if symbols is not None else frozenset()
        ),
    )


def other_receiver_sites(
    symbols: JavaSymbols, adjacency: dict[str, list[CallEdgeEntry]],
) -> frozenset[tuple[str, int, str]]:
    """The edges whose call runs on an object other than the caller's ``this``.

    A call site without receiver, or on ``this`` or ``super``, stays on
    ``this`` when its target belongs to the caller's class hierarchy; a
    call without receiver to an outer class or a static import does not.
    An edge whose caller or call site the source symbols do not know keeps
    an unknown receiver, which is not recorded: the entry-subtype
    restriction stays on for it (ADR-43 amendment, BL-47).
    """
    def owners(owner: str, module: str) -> set[str]:
        return {name for name, _module in symbols.ancestors(owner, module)}

    sites_by_caller: dict[str, dict[tuple[int, str], bool]] = {}
    other: set[tuple[str, int, str]] = set()
    for caller_id, entries in adjacency.items():
        caller_info = symbols.methods.get(caller_id)
        if caller_info is None:
            continue
        sites = sites_by_caller.get(caller_id)
        if sites is None:
            sites = sites_by_caller[caller_id] = symbols.receiver_sites(caller_id)
        for callee, call, _inferred in entries:
            name = callee.qualified_method.rsplit(".", 1)[-1]
            on_this = sites.get((call.call_line, name))
            if on_this is None:
                continue
            if on_this:
                callee_info = symbols.methods.get(callee.id)
                callee_owner = (
                    callee_info.owner if callee_info is not None
                    else callee.qualified_method.rsplit(".", 1)[0]
                )
                related = (
                    callee_owner in owners(caller_info.owner, caller_info.module)
                    or callee_info is not None
                    and caller_info.owner in owners(callee_info.owner, callee_info.module)
                )
                if related:
                    continue
            other.add((caller_id, call.call_line, callee.id))
    return frozenset(other)


def materialize_call_graph_flows(
    methods: list[IntegrationMethod], endpoints: list[MessageEndpoint], calls: list[MethodCall],
    *, repo_root: Path | None = None, max_hops: int = 12,
    module: str | None = None,
    stats: dict[str, int] | None = None,
    reachability: Sequence[MethodReachability] = (),
    source_paths: Sequence[str] = (),
    progress: Callable[[str], None] | None = None,
    call_chain_progress: Callable[[str], None] | None = None,
    call_chain_module: str | None = None,
    join_checkpoint: Callable[[list[CodeFlow], int, int], None] | None = None,
    resume_from_entry: int = 0,
    initial_flows: Sequence[CodeFlow] = (),
    call_graph_sink: Callable[[CallGraph], None] | None = None,
    edge_confidence: str = "possible",
    persisted_edges: Sequence[CallGraphEdge] | None = None,
    persisted_methods: Sequence[PersistedCallGraphMethod] = (),
    modules: Sequence[DiscoveredModule] = (),
    closed_world: bool = False,
    operations: Sequence[DataOperation] | None = None,
    module_dependencies: Sequence[ModuleDependency] = (),
) -> list[CodeFlow]:
    """Reconstruct endpoint flows from the internal method-call graph.

    The call graph is built first from source-located engine rows. This phase
    then traverses its edges from indexed inputs to indexed outputs. Missing or
    imperfect virtual-dispatch edges may be completed by the transient source
    symbol index, but every synthetic edge remains possible/low-confidence.
    ``closed_world`` marks the symbol pass as the whole call graph (an engine
    that resolves no types): a unique source implementation is then exact.
    ``operations`` are the persisted data facts; ``None`` projects the module
    inventory of an older index (``repo_root`` is then required).
    ``module_dependencies`` scope a closed-world dispatch on an external
    interface to the caller's module and its transitive dependencies
    (ADR-71); without them each module sees only itself.
    """
    endpoint_by_id = {endpoint.id: endpoint for endpoint in endpoints}
    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    flow_groups: dict[
        tuple[str, str, str], tuple[CodeFlow, set[bytes]]
    ] = {}

    def record_flow(flow: CodeFlow) -> None:
        """Keep one representative while counting distinct route alternatives."""
        endpoint_steps = _anchor_steps(flow)
        data_step = (
            flow.steps[-1] if flow.steps and flow.steps[-1].kind in DATA_STEP_KINDS else None
        )
        key = (
            _step_anchor(endpoint_steps[0]) if endpoint_steps else flow.id,
            _step_anchor(endpoint_steps[-1]) if len(endpoint_steps) > 1 else
            f"data:{data_step.path}:{data_step.start_line}:{data_step.operation}"
            if data_step is not None else "",
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
                0 if any(step.kind == "method_call" for step in item.steps) else 1,
                confidence_rank.get(item.confidence, 99),
                -sum(step.kind == "method_call" for step in item.steps),
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

    def report_live_chain(
        entry: IntegrationMethod,
        trigger: MessageEndpoint,
        route: list[tuple[IntegrationMethod, MethodCall, bool]],
        *,
        output: MessageEndpoint | None = None,
        stop_reason: str | None = None,
        explored: bool = False,
    ) -> None:
        if call_chain_progress is None:
            return
        if call_chain_module is not None and entry.module != call_chain_module:
            return
        parts = [f"IN {trigger.topic}", entry.qualified_method]
        parts.extend(hop.qualified_method for hop, _call, _inferred in route)
        if output is not None:
            parts.append(f"OUT {output.topic}")
            prefix = "Chaîne construite en direct"
        elif explored:
            prefix = "Chaîne explorée en direct"
        else:
            parts.append(f"[{stop_reason or 'aucun résultat'}]")
            prefix = "Chaîne arrêtée en direct"
        call_chain_progress(f"  ↳ {prefix} : {' -> '.join(parts)}")

    report(
        f"→ Graphe d'appel : jointure de {len(calls)} appel(s) avec "
        f"{len(methods)} méthode(s) Java..."
    )
    call_join_started_at = time.monotonic()
    call_graph = _build_call_graph(
        methods,
        calls,
        repo_root=repo_root,
        source_paths=source_paths,
        progress=progress,
        persisted_edges=persisted_edges,
        persisted_methods=persisted_methods,
        closed_world=closed_world,
        modules=modules,
        module_dependencies=module_dependencies,
    )
    dispatch_symbols = call_graph.symbols
    if call_graph_sink is not None:
        call_graph_sink(call_graph)
    adjacency = call_graph.adjacency
    synthetic_calls = call_graph.synthetic_calls
    call_count = call_graph.call_count
    locate = call_graph.locate
    if edge_confidence not in {"exact", "possible"}:
        raise ValueError(
            f"Invalid call-graph edge confidence {edge_confidence!r}; "
            "expected 'exact' or 'possible'."
        )
    if edge_confidence == "exact":
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
        f"→ Graphe d'appel : graphe d'appels interne construit · {joined_calls} "
        f"arête(s) rattachée(s) à {len(adjacency)} méthode(s) appelante(s)."
    )

    # A Mongo fact belongs to one method only when its source path, owner name,
    # line and module select a unique persisted method. No source is reopened
    # when rebuilding flows from an index.
    graph_methods = {method.id: method for method in methods}
    graph_methods.update({
        method.id: IntegrationMethod(
            id=method.id, module=method.module,
            qualified_method=method.qualified_method, path=method.path,
            start_line=method.start_line, end_line=method.end_line,
            input_endpoint_ids=(), output_endpoint_ids=(),
        )
        for method in persisted_methods if method.id not in graph_methods
    })
    methods_by_source: dict[tuple[str, str, str], list[IntegrationMethod]] = defaultdict(list)
    for method in graph_methods.values():
        parts = method.path.replace("\\", "/").removeprefix("./").split("/")
        method_name = method.qualified_method.rsplit(".", 1)[-1]
        for index in range(len(parts)):
            methods_by_source[(method.module, "/".join(parts[index:]), method_name)].append(method)
    if operations is None:
        operations = data_operations_from_modules(repo_root, modules)
    operations_by_method: dict[str, list[DataOperation]] = defaultdict(list)
    for operation in operations:
        relative = operation.path.replace("\\", "/").removeprefix("./")
        candidates = [
            method for method in methods_by_source.get(
                (operation.module, relative, operation.owner_method), ()
            )
            if method.start_line <= operation.line <= method.end_line
        ]
        if len(candidates) == 1:
            # The step cites the persisted method's path, the canonical
            # repository-relative coordinate of the join.
            operations_by_method[candidates[0].id].append(
                replace(operation, path=candidates[0].path)
            )
    for owned in operations_by_method.values():
        owned.sort(key=lambda item: (item.line, item.technology, item.operation, item.id))

    def report_reverse_chain(
        output_method: IntegrationMethod,
        output: MessageEndpoint,
        route: list[tuple[IntegrationMethod, MethodCall, bool]],
        *,
        input_endpoint: MessageEndpoint | None = None,
        stop_reason: str | None = None,
        explored: bool = False,
    ) -> None:
        if call_chain_progress is None:
            return
        if (
            call_chain_module is not None
            and output_method.module != call_chain_module
        ):
            return
        depth = len(route)
        parts = [f"OUT {output.topic}", output_method.qualified_method]
        parts.extend(hop.qualified_method for hop, _call, _inferred in route)
        if input_endpoint is not None:
            parts.append(f"IN {input_endpoint.topic}")
            prefix = f"Chaîne construite en direct (profondeur={depth})"
        elif explored:
            prefix = f"Chaîne explorée en direct (profondeur={depth})"
        else:
            parts.append(
                f"[arrêt profondeur={depth} : {stop_reason or 'aucun résultat'}]"
            )
            prefix = "Chaîne arrêtée en direct"
        call_chain_progress(f"  ↳ {prefix} : {' -> '.join(parts)}")

    methods_by_id = {method.id: method for method in methods}
    reverse_adjacency: dict[
        str, list[tuple[IntegrationMethod, MethodCall, bool]]
    ] = defaultdict(list)
    for caller_id, targets in adjacency.items():
        caller = methods_by_id.get(caller_id)
        if caller is None:
            continue
        for callee, call, inferred in targets:
            reverse_adjacency[callee.id].append((caller, call, inferred))

    if call_chain_progress is not None:
        reported_output_endpoint_ids: set[str] = set()
        for output_method in methods:
            if (
                not output_method.output_endpoint_ids
                or (
                    call_chain_module is not None
                    and output_method.module != call_chain_module
                )
            ):
                continue
            for output_id in output_method.output_endpoint_ids:
                output = endpoint_by_id.get(output_id)
                if output is None:
                    continue
                reported_output_endpoint_ids.add(output.id)
                queue = deque[
                    tuple[IntegrationMethod, list[tuple[IntegrationMethod, MethodCall, bool]]]
                ]([(output_method, [])])
                reverse_terminal_routes: dict[
                    tuple[tuple[str, ...], str],
                    list[tuple[IntegrationMethod, MethodCall, bool]],
                ] = {}
                report_reverse_chain(output_method, output, [], explored=True)

                def remember_reverse_terminal(
                    route: list[tuple[IntegrationMethod, MethodCall, bool]],
                    reason: str,
                ) -> None:
                    key = (
                        tuple(hop.qualified_method for hop, _call, _inferred in route),
                        reason,
                    )
                    reverse_terminal_routes.setdefault(key, route)

                while queue:
                    current, route = queue.popleft()
                    if current.input_endpoint_ids:
                        for input_id in current.input_endpoint_ids:
                            input_endpoint = endpoint_by_id.get(input_id)
                            if input_endpoint is not None:
                                report_reverse_chain(
                                    output_method,
                                    output,
                                    route,
                                    input_endpoint=input_endpoint,
                                )
                        continue
                    if len(route) >= max_hops:
                        remember_reverse_terminal(route, "limite de profondeur")
                        continue
                    predecessors = reverse_adjacency.get(current.id, [])
                    if not predecessors:
                        remember_reverse_terminal(route, "aucun appelant indexé")
                        continue
                    for caller, call, inferred in predecessors:
                        next_route = [*route, (caller, call, inferred)]
                        if caller.id == output_method.id or any(
                            previous.id == caller.id
                            for previous, _edge, _inferred in route
                        ):
                            remember_reverse_terminal(next_route, "cycle détecté")
                            continue
                        report_reverse_chain(
                            output_method, output, next_route, explored=True
                        )
                        queue.append((caller, next_route))
                for (_route_names, reason), route in sorted(reverse_terminal_routes.items())[:50]:
                    report_reverse_chain(
                        output_method, output, route, stop_reason=reason
                    )

        for output in endpoints:
            if (
                output.id in reported_output_endpoint_ids
                or output.module != call_chain_module
                or output.role not in {"call", "produce"}
            ):
                continue
            call_chain_progress(
                f"  ↳ Chaîne arrêtée en direct (profondeur=0) : OUT {output.topic} -> "
                "[aucune méthode OUT indexée]"
            )

        # The regular flow traversal remains forward for materialization, but
        # live diagnostics use the reverse output-to-input walk above.
        call_chain_progress = None

    components = call_graph.connected_components(tuple(method.id for method in methods))
    component_by_method = {
        method_id: component
        for component in components
        for method_id in component
    }
    report(
        f"→ Graphe d'appel : {len(components)} composante(s) connexe(s) dans le "
        "graphe d'appels interne."
    )
    report("→ Graphe d'appel : reconstruction des flux input → output...")

    # Contract-only HTTP inputs (for example generated OpenAPI interfaces)
    # have no source endpoint path for the AST materializer. If the same
    # indexed method also owns an output endpoint, the method itself is still
    # sufficient evidence for a direct input-to-output flow.
    input_methods = [
        entry for entry in methods
        if entry.input_endpoint_ids and (module is None or entry.module == module)
    ]
    application_modules = {
        module_identity(discovered)
        for discovered in modules if discovered.starts_application
    }

    def within_application_boundary(entry_module: str, target_module: str) -> bool:
        return target_module == entry_module or target_module not in application_modules

    if resume_from_entry < 0 or resume_from_entry > len(input_methods):
        raise ValueError(
            f"Invalid call-graph join resume offset {resume_from_entry}; "
            f"expected 0..{len(input_methods)}."
        )
    explored_entries = resume_from_entry
    last_reported_explored = 0
    last_exploration_report_at = time.monotonic()
    def trigger_entry(
        entry: IntegrationMethod, trigger: MessageEndpoint,
    ) -> tuple[IntegrationMethod, str]:
        """The entry method as a trigger's flows see it, and its entry class.

        An inherited route runs the inherited method on the inheritor: the
        flow belongs to the inheritor's module and dispatch keeps to the
        inheritor (ADR-88). Any other trigger runs on the method's owner.
        """
        if (
            trigger.qualified_name
            and inherited_method_anchor(trigger.snippet) == (entry.path, entry.start_line)
        ):
            return replace(entry, module=trigger.module or entry.module), trigger.qualified_name
        return entry, entry.qualified_method.rsplit(".", 1)[0]

    for base_entry in input_methods[resume_from_entry:]:
        for trigger_id in base_entry.input_endpoint_ids:
            trigger = endpoint_by_id.get(trigger_id)
            if trigger is None:
                continue
            entry, _entry_class = trigger_entry(base_entry, trigger)
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
                report_live_chain(entry, trigger, [], explored=True)
                report_live_chain(entry, trigger, [], output=output)
    explored = 0
    # Entries whose traversal stopped at ``max_hops`` on some route (BL-52).
    hop_limited_entries: set[str] = set()

    def dispatch_matches_entry(
        entry: IntegrationMethod,
        current: IntegrationMethod,
        target: IntegrationMethod,
        call: MethodCall,
        on_this: bool = True,
        entry_class: str | None = None,
    ) -> bool:
        """Keep dispatch from an inherited body on the entry's own subtype.

        An engine can report every concrete override of a template method's
        abstract call. The runtime receiver is the subtype that owns the
        indexed entry method; crossing to a sibling override creates
        impossible flows and false cycles.

        That receiver is known only while every hop since the entry, this
        call included, runs on ``this`` (``on_this``). Once the route calls
        another object, dispatch is unrestricted (ADR-43 amendment, BL-47).
        ``entry_class`` names the runtime subtype when it is not the entry
        method's owner: the inheritor of an inherited route (ADR-88).
        """
        if not on_this:
            return True
        entry_owner = entry_class or entry.qualified_method.rsplit(".", 1)[0]
        if dispatch_symbols is not None:
            current_owner = current.qualified_method.rsplit(".", 1)[0]
            target_owner = target.qualified_method.rsplit(".", 1)[0]
            # Ancestor keys carry the module of the declaring type, which can
            # be a helper module: compare owners by qualified name so the
            # rule also holds when the template body lives in another module.
            # The rule needs a known runtime subtype: when the entry itself is
            # declared by the inherited body's class, any subtype can be the
            # receiver and every override stays possible.
            target_ancestors = {
                owner for owner, _module in dispatch_symbols.ancestors(target_owner, target.module)
            }
            entry_ancestors = {
                owner for owner, _module in dispatch_symbols.ancestors(entry_owner, entry.module)
            }
            if (
                current_owner != target_owner
                and current_owner != entry_owner
                and current_owner in target_ancestors
                and current_owner in entry_ancestors
            ):
                return target_owner in entry_ancestors
            return True
        # Without source symbols (a rebuild from persisted edges), keep a
        # fanned-out inherited call site on the entry's own override.
        return entry_dispatch_allowed(
            f"{entry_owner}.{entry.qualified_method.rsplit('.', 1)[-1]}",
            current.qualified_method, target.qualified_method,
            (
                callee.qualified_method
                for callee, sibling, _inferred in adjacency.get(current.id, [])
                if sibling.call_line == call.call_line and sibling.caller_path == call.caller_path
            ),
            on_this=on_this,
        )

    for base_entry in input_methods[resume_from_entry:]:
        component = component_by_method.get(base_entry.id, frozenset({base_entry.id}))
        for trigger_id in base_entry.input_endpoint_ids:
            trigger = endpoint_by_id.get(trigger_id)
            if trigger is None:
                continue
            entry, entry_class = trigger_entry(base_entry, trigger)
            # A state carries whether every hop so far runs on ``this``: the
            # entry-subtype restriction holds only then (BL-47).
            search = deque[tuple[
                IntegrationMethod, list[tuple[IntegrationMethod, MethodCall, bool]], bool,
            ]]([(entry, [], True)])
            visited: set[tuple[str, bool, bool]] = {(entry.id, False, True)}
            terminal_routes: dict[
                tuple[tuple[str, ...], str],
                list[tuple[IntegrationMethod, MethodCall, bool]],
            ] = {}

            def remember_terminal(
                route: list[tuple[IntegrationMethod, MethodCall, bool]],
                reason: str,
            ) -> None:
                key = (tuple(hop.qualified_method for hop, _call, _inferred in route), reason)
                terminal_routes.setdefault(key, route)

            while search:
                current, route, route_on_this = search.popleft()
                for operation in operations_by_method.get(current.id, ()):
                    operation_path = operation.path
                    steps = [_endpoint_step(trigger, 1)]
                    for order, (hop, edge, _inferred) in enumerate(route, start=2):
                        steps.append(CodeFlowStep(
                            order=order, kind="method_call", name=hop.qualified_method,
                            path=edge.caller_path, start_line=edge.call_line,
                            end_line=edge.call_line,
                        ))
                    data_step = _operation_step(operation, len(steps) + 1, steps[0].kind)
                    traced_key_identity = (
                        trace_scalar_key_identity(
                            repo_root, route, entry, current,
                            operation.key_expression,
                            key_identity_base(
                                operation.resource_identity, operation.key_property,
                            ),
                        )
                        if repo_root is not None else None
                    )
                    if traced_key_identity:
                        data_step = replace(data_step, key_identity=traced_key_identity)
                    steps.append(data_step)
                    possible = any(
                        edge.dispatch_confidence == "possible" or inferred
                        for _hop, edge, inferred in route
                    )
                    record_flow(CodeFlow(
                        id=compute_code_flow_id(
                            entry.module, entry.path, entry.qualified_method,
                            steps[0].kind,
                            f"{trigger.id}|{operation_path}|{operation.line}|"
                            f"{operation.operation}",
                        ),
                        module=entry.module, method=entry.qualified_method,
                        path=entry.path, start_line=entry.start_line,
                        end_line=entry.end_line, status="potential",
                        confidence="low" if possible else "medium",
                        reason=(
                            "A source-backed possible call path reaches this data API attempt."
                            if possible else
                            "A source-backed call path reaches this data API attempt."
                        ),
                        steps=tuple(steps),
                    ))
                if len(route) >= max_hops:
                    remember_terminal(route, "limite de profondeur")
                    if adjacency.get(current.id):
                        # The route has calls left to follow.
                        hop_limited_entries.add(entry.id)
                    continue
                eligible_targets = sorted((
                    (target, call, signature_join)
                    for target, call, signature_join in adjacency.get(current.id, [])
                    if target.id in component
                    and within_application_boundary(entry.module, target.module)
                    and dispatch_matches_entry(
                        entry, current, target, call,
                        route_on_this and call_graph.on_this(current.id, call, target.id),
                        entry_class,
                    )
                ), key=lambda item: (
                    item[1].caller_path, item[1].call_line, item[0].id,
                    item[1].dispatch_confidence, item[2],
                ))
                if not eligible_targets and (route or not entry.output_endpoint_ids):
                    remember_terminal(route, "aucune suite d'appel")
                for target, call, signature_join in eligible_targets:
                    explored += 1
                    now = time.monotonic()
                    if now - last_exploration_report_at >= 5.0:
                        report(
                            f"→ Graphe d'appel : jointure en cours · méthode IN "
                            f"{explored_entries + 1}/{len(input_methods)} · "
                            f"{explored} transition(s) explorée(s) · "
                            f"{len(current_flows())} flux... "
                            f"({now - call_join_started_at:.0f} s)"
                        )
                        last_exploration_report_at = now
                    next_route = [*route, (target, call, signature_join)]
                    if target.id == entry.id or any(previous.id == target.id for previous, _edge, _signature in route):
                        remember_terminal(next_route, "cycle détecté")
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
                                "The call graph has a cyclic call path from this indexed entry method."
                            ),
                            steps=tuple(steps),
                        ))
                        continue
                    report_live_chain(entry, trigger, next_route, explored=True)
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
                            # The reason names the weakest hop the route
                            # actually takes, whichever engine built it: an
                            # exact symbol-pass call is a static call, not a
                            # dispatch fallback (review F14).
                            has_dispatch_candidate = any(
                                inferred and edge in synthetic_calls
                                for _hop, edge, inferred in next_route
                            )
                            record_flow(CodeFlow(
                                id=flow_id, module=entry.module, method=entry.qualified_method,
                                path=entry.path, start_line=entry.start_line, end_line=entry.end_line,
                                status="potential",
                                confidence="low" if has_possible_dispatch or has_signature_join else "medium",
                            reason=(
                                    "A source-declared Java dispatch fallback connects an indexed entry to an output through qualified types and compatible method signatures."
                                    if has_dispatch_candidate
                                    else
                                    "A unique indexed method signature joined a module-local engine call."
                                    if has_signature_join
                                    else
                                    "The call graph has a possible virtual-dispatch path from an indexed entry method to an indexed output method."
                                    if has_possible_dispatch
                                    else "The call graph resolved a static call path from an indexed entry method to an indexed output method."
                                ),
                                steps=tuple([*steps, _endpoint_step(output, len(steps) + 1)]),
                            ))
                            report_live_chain(entry, trigger, next_route, output=output)
                    possible = any(edge.dispatch_confidence == "possible" or inferred
                                   for _method, edge, inferred in next_route)
                    next_on_this = route_on_this and call_graph.on_this(current.id, call, target.id)
                    state = (target.id, possible, next_on_this)
                    if state not in visited:
                        visited.add(state)
                        search.append((target, next_route, next_on_this))
            for (_route_names, reason), route in sorted(terminal_routes.items())[:50]:
                report_live_chain(entry, trigger, route, stop_reason=reason)
        explored_entries += 1
        now = time.monotonic()
        if (
            explored_entries == len(input_methods)
            or explored_entries % 25 == 0
            or explored - last_reported_explored >= 5_000
            or now - last_exploration_report_at >= 5.0
        ):
            report(
                f"→ Graphe d'appel : jointure en cours · méthode IN "
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
            "hop_limited_entries": len(hop_limited_entries),
        })
    if reachability:
        # One predecessor tree per source/confidence, not a BFS per pair.
        tree_cache: dict[
            tuple[str, str], dict[str, tuple[str, IntegrationMethod, MethodCall, bool]]
        ] = {}
        max_cached_trees = 8
        direct_flows: dict[tuple[str, str], CodeFlow] = {}
        joined_pairs = {
            (flow.steps[0].endpoint_id, flow.steps[-1].endpoint_id)
            for flow in current_flows()
            if flow.status != "cycle" and flow.steps
        }

        def representative_route(
            source: IntegrationMethod, target: IntegrationMethod, confidence: str
        ) -> list[tuple[IntegrationMethod, MethodCall, bool]]:
            """Shortest route that passes every rule of the main join, or ``[]``.

            A reachability row only says that the engine reaches the output. The
            flow it yields must still respect the application boundary on
            every hop, the entry's dispatch and the hop limit (review F1).
            """
            key = (source.id, confidence)
            if key not in tree_cache:
                if len(tree_cache) >= max_cached_trees:
                    tree_cache.pop(next(iter(tree_cache)))
                predecessors: dict[str, tuple[str, IntegrationMethod, MethodCall, bool]] = {}
                queue = deque([(source, 0)])
                visited = {source.id}
                # The route a tree keeps to each method decides whether the
                # next hop still runs on ``this`` (BL-47).
                tree_on_this = {source.id: True}
                while queue:
                    current, depth = queue.popleft()
                    if depth >= max_hops:
                        continue
                    for candidate, call, inferred in adjacency.get(current.id, []):
                        hop_on_this = tree_on_this[current.id] and call_graph.on_this(
                            current.id, call, candidate.id,
                        )
                        # A direct engine witness must not contain synthetic
                        # AST edges or a weaker dispatch than the proven pair.
                        if (
                            inferred
                            or not within_application_boundary(source.module, candidate.module)
                            or (confidence == "medium" and call.dispatch_confidence != "exact")
                            or not dispatch_matches_entry(source, current, candidate, call, hop_on_this)
                        ):
                            continue
                        if candidate.id in visited:
                            continue
                        visited.add(candidate.id)
                        tree_on_this[candidate.id] = hop_on_this
                        predecessors[candidate.id] = (current.id, candidate, call, inferred)
                        queue.append((candidate, depth + 1))
                tree_cache[key] = predecessors
            predecessors = tree_cache[key]
            route: list[tuple[IntegrationMethod, MethodCall, bool]] = []
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
            # ``medium`` is an exact-only route; ``low`` needs possible dispatch.
            if edge_confidence == "exact" and relation.confidence != "medium":
                continue
            source_location = locate(relation.source, relation.source_path, relation.source_line)
            target_location = locate(relation.target, relation.target_path, relation.target_line)
            if source_location is None or target_location is None:
                continue
            source_method = source_location[0]
            target_method = target_location[0]
            if module is not None and source_method.module != module:
                continue
            if not within_application_boundary(source_method.module, target_method.module):
                continue
            for input_id in source_method.input_endpoint_ids:
                trigger = endpoint_by_id.get(input_id)
                if trigger is None:
                    continue
                for output_id in target_method.output_endpoint_ids:
                    output = endpoint_by_id.get(output_id)
                    if output is None:
                        continue
                    # A pair the join already connects keeps the join's
                    # flows: a reachability row never replaces them.
                    if (input_id, output_id) in joined_pairs:
                        continue
                    previous_flow = direct_flows.get((input_id, output_id))
                    if previous_flow is not None and previous_flow.confidence == "medium":
                        continue
                    route = representative_route(source_method, target_method, relation.confidence)
                    if not route:
                        # No route passes the join's rules: the row stays
                        # unused rather than becoming an unchecked flow.
                        continue
                    steps = [_endpoint_step(trigger, 1)]
                    for order, (hop, edge, _signature_join) in enumerate(route, start=2):
                        steps.append(CodeFlowStep(
                            order=order, kind="method_call", name=hop.qualified_method,
                            path=edge.caller_path, start_line=edge.call_line, end_line=edge.call_line,
                        ))
                    flow_id = compute_code_flow_id(
                        source_method.module, source_method.path, source_method.qualified_method,
                        _endpoint_step(trigger, 1).kind,
                        f"{trigger.topic}|{output.id}|{DIRECT_REACHABILITY_FLOW_MARKER}",
                    )
                    direct_flows[(input_id, output_id)] = CodeFlow(
                        id=flow_id, module=source_method.module, method=source_method.qualified_method,
                        path=source_method.path, start_line=source_method.start_line,
                        end_line=source_method.end_line,
                        status="potential", confidence=relation.confidence,
                        reason=(
                            "The engine proved reverse reachability and the indexed method-call graph "
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
        f"→ Graphe d'appel : jointure terminée · {explored} transition(s), "
        f"{len(reachability)} reachability(s), {len(current_flows())} flux dédupliqué(s)."
    )
    return _ensure_unique_code_flow_ids(_deduplicate_code_flows(
        mark_after_commit_publications(repo_root, current_flows()), endpoints,
    ))


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
                    next_endpoint = None
                    if index + 1 < len(endpoint_steps):
                        next_id = endpoint_steps[index + 1].endpoint_id
                        if next_id is not None:
                            next_endpoint = endpoint_by_id[next_id]
                    if next_endpoint is not None and next_endpoint.role not in {"serve", "consume"}:
                        # A following effect is a sibling, not this effect's
                        # target: the effect needs its own edge (review F2).
                        next_endpoint = None
                    if next_endpoint is not None and next_endpoint.module == flow.module:
                        # The effect continues in the same service: no
                        # inter-service edge is expected for it.
                        continue
                    matching_outgoing = outgoing.get(endpoint.id, [])
                    if next_endpoint is not None:
                        matching_outgoing = [
                            edge for edge in matching_outgoing
                            if edge.to_endpoint is not None
                            and edge.to_endpoint.id == next_endpoint.id
                        ]
                    # A publication reaching several consumers is a fan-out,
                    # not an ambiguity; an HTTP call must match one route.
                    distinct_targets = {
                        (edge.to_service, edge.to_endpoint.id if edge.to_endpoint else None)
                        for edge in matching_outgoing
                    }
                    ambiguous = len(distinct_targets) != len(matching_outgoing) or (
                        endpoint.system != "kafka" and len(distinct_targets) > 1
                    )
                    if not matching_outgoing or ambiguous:
                        status = "partial"
                        break
                if endpoint.role in {"serve", "consume"} and step is not endpoint_steps[0]:
                    if endpoint.module == flow.module:
                        # Reached from the same service: see above.
                        continue
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
