"""The call-graph stage of an index pass: methods, engine calls and flows.

``index_repo`` runs this stage after endpoints, modules and relations are
persisted. It materializes integration methods and same-method flows, asks
the configured engine for calls, reconstructs interprocedural flows with
checkpoints, reconciles them against the topology, and persists the flow
snapshot. The stage never imports an engine module: it only uses the engine
seam in ``callgraph/`` (ADR-61).
"""

import hashlib
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from systemlens.callgraph.engine import CallGraphEngine, CallGraphSession, EngineRequest
from systemlens.callgraph.model import (
    CallGraphError,
    CallGraphTimeout,
    MethodCall,
    SourceMethod,
)
from systemlens.conventions.strategy1.graph import STRATEGY1_REST_TARGET_POLICY
from systemlens.domain.code_flows import (
    DATA_STEP_KINDS,
    CallGraphEdge,
    CodeFlow,
    IntegrationMethod,
    PersistedCallGraphMethod,
)
from systemlens.domain.data_operations import DataOperation
from systemlens.domain.graph import DEFAULT_REST_TARGET_POLICY, build_graph, group_endpoints_by_module
from systemlens.domain.message_types import informative_message_type
from systemlens.domain.models import ArchitectureRelation, MessageEndpoint, ensure_unique_endpoint_ids
from systemlens.domain.module_inventory import DiscoveredModule, ModuleDependency, module_identity
from systemlens.indexing.code_flows import (
    CallGraph,
    _deduplicate_code_flows,
    _ensure_unique_code_flow_ids,
    call_graph_join_methods_signature,
    materialize_call_graph_flows,
    materialize_code_flows,
    reconcile_code_flows,
)
from systemlens.indexing.dto_inventory import materialize_kafka_dto_definitions
from systemlens.indexing.integration_methods import (
    materialize_integration_methods,
    path_module_resolver,
    scheduled_trigger_endpoints,
)
from systemlens.indexing.relations import build_architecture_relations
from systemlens.infrastructure.config import Config
from systemlens.scanner import local_spring_application_names
from systemlens.storage.persisted_names import (
    CALL_GRAPH_METHOD_ID_PREFIX,
    META_CALL_GRAPH_EDGE_COUNT,
    META_CALL_GRAPH_STATUS,
    META_INPUT_OUTPUT_FLOW_COUNT,
    META_INPUT_OUTPUT_STATUS,
    META_JOIN_COMPLETED_ENTRIES,
    META_JOIN_METHODS_SIGNATURE,
    META_JOIN_SIGNATURE,
)
from systemlens.storage.sqlite import Store


ProgressCallback = Callable[[str], None]


def _report_progress(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)


class StageTimer(Protocol):
    """The elapsed-time reporter of the index pass."""

    def begin(self, stage: str, message: str) -> None: ...

    def end(self, stage: str, label: str) -> None: ...


@dataclass(frozen=True)
class CallGraphProgress:
    """One explicitly provisional method-call checkpoint.

    The checkpoint is emitted only after one local analyzer project has
    completed. Consumers must label it as incomplete until final indexing
    reconciliation commits the complete snapshot.
    """

    engine: str
    completed_projects: int
    total_projects: int
    project_name: str
    endpoints: list[MessageEndpoint]
    modules: list[DiscoveredModule]
    relations: list[ArchitectureRelation]
    integration_methods: list[IntegrationMethod]
    code_flows: list[CodeFlow]
    project_input_methods: list[IntegrationMethod]
    project_output_methods: list[IntegrationMethod]
    phase: str = "projects"
    completed_units: int = 0
    total_units: int = 0


CallGraphProgressCallback = Callable[[CallGraphProgress], None]


def _call_graph_module_roots(
    repo_root: Path, java_paths: Sequence[str], modules: Sequence[DiscoveredModule]
) -> list[tuple[str, Path, str]]:
    """Return the source-owning build roots for module-scoped engine runs.

    Each Java source belongs to its deepest discovered build module. This
    avoids treating an aggregator as a second analysis unit for its children,
    while preserving a root-level fallback for sources outside a descriptor.
    The returned prefix maps an engine's module-relative paths to repository
    relative evidence paths.
    """
    modules_by_path = {
        module.path.resolve(): module
        for module in modules
    }
    selected: dict[Path, str] = {}
    for relative_path in java_paths:
        candidate = (repo_root / relative_path).resolve()
        owner = None
        directory = candidate.parent
        while True:
            owner = modules_by_path.get(directory)
            if owner is not None or directory == repo_root.resolve():
                break
            parent = directory.parent
            if parent == directory:
                break
            directory = parent
        if owner is None:
            selected.setdefault(repo_root.resolve(), "racine du dépôt")
        else:
            selected.setdefault(owner.path.resolve(), module_identity(owner))
    result: list[tuple[str, Path, str]] = []
    for root, name in sorted(selected.items(), key=lambda item: (item[1], str(item[0]))):
        prefix = "" if root == repo_root.resolve() else root.relative_to(repo_root.resolve()).as_posix()
        result.append((name, root, prefix))
    return result


def _partition_calls(
    calls: Sequence[MethodCall],
    roots: Sequence[tuple[str, Path, str]],
) -> list[tuple[str, list[MethodCall]]]:
    """Partition global engine results by caller project for checkpoints."""
    by_project: dict[str, list[MethodCall]] = {
        name: [] for name, _root, _prefix in roots
    }
    by_prefix = {prefix: name for name, _root, prefix in roots}
    fallback = by_prefix.get("", roots[0][0] if roots else "racine du dépôt")
    for call in calls:
        project = fallback
        path_parts = call.caller_path.strip("/").split("/")
        for end in range(len(path_parts), -1, -1):
            prefix = "/".join(path_parts[:end])
            if prefix in by_prefix:
                project = by_prefix[prefix]
                break
        by_project.setdefault(project, []).append(call)
    return [(name, by_project.get(name, [])) for name, _root, _prefix in roots]


def _engine_method_name(name: str) -> str:
    """Spell an engine's qualified method name as the index does.

    The same rule as the call-graph join: ``$`` separates a nested type,
    and a leading ``$`` marks the unnamed package.
    """
    return name.replace("$", ".").lstrip(".")


@dataclass(frozen=True)
class JoinResume:
    """A persisted join checkpoint to resume from, validated by the caller."""

    entries: int = 0
    flows: tuple[CodeFlow, ...] = ()
    methods_signature: str | None = None


@dataclass(frozen=True)
class CallGraphStageResult:
    flows: list[CodeFlow]
    timed_out: bool


def _mark_flow_snapshot_in_progress(
    store: Store,
    current_hashes: Mapping[str, str],
    endpoints: Sequence[MessageEndpoint],
) -> None:
    """Make every checkpoint of this stage an explicitly partial snapshot.

    The stage commits intermediate checkpoints (ADR-37). Until its final
    reconciliation, the flow snapshot is not current: the signature stays
    unset and the status says ``partial``, so a run that stops after any
    checkpoint makes the next index retry the stage (ADR-7, ADR-37). Flows
    that name a deleted file or a purged endpoint are dropped before the
    first checkpoint, so they cannot survive a crash.
    """
    store.delete_meta("code_flow_signature")
    store.set_meta("code_flow_snapshot_status", "partial")
    endpoint_ids = {endpoint.id for endpoint in endpoints}
    persisted = store.all_code_flows()
    surviving = [
        flow for flow in persisted
        if flow.path in current_hashes
        and all(
            (not step.path.endswith(".java") or step.path in current_hashes)
            and (step.endpoint_id is None or step.endpoint_id in endpoint_ids)
            for step in flow.steps
        )
    ]
    if len(surviving) != len(persisted):
        store.replace_code_flows(surviving)


def run_call_graph_stage(
    *,
    store: Store,
    repo_root: Path,
    config: Config,
    relation_modules: list[DiscoveredModule],
    relation_dependencies: list[ModuleDependency],
    relations: list[ArchitectureRelation],
    current_hashes: dict[str, str],
    data_operations: Sequence[DataOperation],
    engine: CallGraphEngine | None,
    engine_name: str,
    engine_label: str,
    topic_strategy: str,
    flow_signature: str,
    join_signature: str,
    resume: JoinResume | None,
    refresh_call_graph_view: bool,
    show_call_chains: bool,
    show_call_chains_module: str | None,
    generate_sources: bool,
    engine_progress: bool,
    progress: ProgressCallback | None,
    call_graph_progress: CallGraphProgressCallback | None,
    timer: StageTimer,
) -> CallGraphStageResult:
    """Materialize and persist the flow snapshot of one index pass.

    ``resume`` carries a validated join checkpoint; ``None`` starts the join
    from the first input method. The result reports whether the engine
    reached its deadline, in which case the persisted snapshot stays partial.
    """
    resume_call_graph_join = resume is not None
    resume_join_entries = resume.entries if resume is not None else 0
    resume_join_flows: list[CodeFlow] = list(resume.flows) if resume is not None else []
    resume_join_methods_signature = resume.methods_signature if resume is not None else None
    call_graph_timed_out = False
    timer.begin("flows", "→ Indexation : matérialisation des flux de code...")
    all_endpoints = store.all_endpoints()
    _mark_flow_snapshot_in_progress(store, current_hashes, all_endpoints)
    scheduler_triggers = scheduled_trigger_endpoints(
        repo_root, list(current_hashes), all_endpoints, relation_modules
    )

    def flow_endpoints() -> list[MessageEndpoint]:
        # Scheduler triggers only seed flows; they are never persisted.
        return [*all_endpoints, *scheduler_triggers]

    methods = materialize_integration_methods(
        repo_root, flow_endpoints(), list(current_hashes), relation_modules
    )
    store.replace_integration_methods(methods)
    reported_chain_signatures: set[tuple[str, tuple[tuple[str, str], ...]]] = set()

    def report_built_chains(values: Sequence[CodeFlow]) -> None:
        if not show_call_chains or progress is None:
            return
        endpoint_by_id = {endpoint.id: endpoint for endpoint in all_endpoints}
        for flow in values:
            if (
                show_call_chains_module is not None
                and flow.module != show_call_chains_module
            ):
                continue
            endpoint_steps = [step for step in flow.steps if step.endpoint_id]
            if len(endpoint_steps) < 2:
                continue
            first = endpoint_by_id.get(endpoint_steps[0].endpoint_id or "")
            last = endpoint_by_id.get(endpoint_steps[-1].endpoint_id or "")
            if (
                first is None
                or last is None
                or first.module != flow.module
                or last.module != flow.module
                or first.role not in {"serve", "consume"}
                or last.role not in {"call", "produce"}
            ):
                continue
            chain_steps: list[tuple[str, str]] = []
            for step in flow.steps:
                if step.kind in {"message_entry", "http_entry"}:
                    chain_steps.append(("IN", step.name))
                elif step.kind == "method_call":
                    chain_steps.append(("METHOD", step.name))
                elif step.kind in {"message_publish", "http_call"}:
                    chain_steps.append(("OUT", step.name))
            signature = (flow.id, tuple(chain_steps))
            if signature in reported_chain_signatures:
                continue
            reported_chain_signatures.add(signature)
            rendered = " -> ".join(
                f"{kind} {name}" if kind != "METHOD" else name
                for kind, name in chain_steps
            )
            _report_progress(progress, f"  ↳ Chaîne construite : {rendered}")

    persisted_call_edges: list[CallGraphEdge] = []
    persisted_call_graph_methods: list[PersistedCallGraphMethod] = []
    if refresh_call_graph_view:
        persisted_call_edges = store.all_call_edges()
        persisted_call_graph_methods = store.all_call_graph_methods()
    else:
        store.replace_call_edges([])
        store.replace_call_graph_methods([])
        store.delete_meta(META_CALL_GRAPH_STATUS)
        store.delete_meta(META_CALL_GRAPH_EDGE_COUNT)
    store.delete_meta(META_INPUT_OUTPUT_STATUS)
    store.delete_meta(META_INPUT_OUTPUT_FLOW_COUNT)

    def enrich_strategy1_kafka_types(session: CallGraphSession) -> None:
        """Complete missing Strategy1 producer types from engine evidence."""
        nonlocal all_endpoints, relations
        if topic_strategy != "strategy1":
            return
        evidence = session.payload_types()
        by_site: dict[tuple[str, int], set[str]] = {}
        for item in evidence:
            # Engine evidence follows the same rule as the AST: a type that
            # names no DTO (``Object``, ``String``) stays unknown (ADR-76).
            informative = informative_message_type(item.message_type)
            if informative is not None:
                by_site.setdefault((item.path, item.line), set()).add(informative)

        def complete_endpoint(endpoint: MessageEndpoint) -> MessageEndpoint:
            types = by_site.get((endpoint.path, endpoint.start_line), set())
            if (
                endpoint.framework == "kafka-topic-strategy1"
                and endpoint.role == "produce"
                and endpoint.message_type is None
                and len(types) == 1
            ):
                return replace(endpoint, message_type=next(iter(types)))
            return endpoint

        enriched = [
            complete_endpoint(endpoint)
            for endpoint in all_endpoints
        ]
        if enriched == all_endpoints:
            return
        completed_count = sum(
            1 for before, after in zip(all_endpoints, enriched) if before != after
        )
        enriched = ensure_unique_endpoint_ids(enriched)
        store.replace_endpoints_for_files(
            sorted({endpoint.path for endpoint in all_endpoints}), enriched
        )
        all_endpoints = enriched
        endpoints_by_service = {
            module: items
            for module, items in group_endpoints_by_module(all_endpoints).items()
        }
        store.replace_kafka_dto_definitions(
            materialize_kafka_dto_definitions(endpoints_by_service, relation_modules)
        )
        relations = build_architecture_relations(
            relation_modules,
            all_endpoints,
            relation_dependencies,
            kafka_reply_strategy1=True,
            repo_root=repo_root,
        )
        store.replace_architecture_relations(relations)
        _report_progress(
            progress,
            f"→ {engine_label} : {completed_count} "
            "type(s) Kafka Strategy1 complété(s).",
        )

    def persist_call_graph(call_graph: CallGraph) -> None:
        edges = list(call_graph.edges())
        if refresh_call_graph_view and call_graph_view_prefix:
            prefix = call_graph_view_prefix.rstrip("/") + "/"
            methods_projection = [
                method for method in store.all_call_graph_methods()
                if not (
                    method.path == call_graph_view_prefix
                    or method.path.startswith(prefix)
                )
            ]
        else:
            methods_projection = []
        graph_methods = call_graph.methods
        if refresh_call_graph_view and call_graph_view_prefix:
            graph_methods = tuple(
                method for method in graph_methods
                if not (
                    method.path == call_graph_view_prefix
                    or method.path.startswith(prefix)
                )
            )
        methods_projection.extend(graph_methods)
        # An engine method is the indexed method of the same name whose line
        # range holds its line: the engine locates the declaration, the index
        # starts at its first annotation, and an engine may spell nested types
        # with ``$`` (call-graph review Q10).
        ranges_by_name: dict[tuple[str, str], list[tuple[int, int]]] = {}
        for known in methods_projection:
            ranges_by_name.setdefault((known.qualified_method, known.path), []).append(
                (known.start_line, known.end_line)
            )
        eligible_paths = set(current_hashes)
        module_of_path = path_module_resolver(repo_root, relation_modules)
        for method in engine_method_projection:
            if method.path not in eligible_paths:
                continue
            name = _engine_method_name(method.qualified_method)
            if name.startswith("<anonymous"):
                # An anonymous class method has no identity (ADR-62): the
                # join attributes its calls to the creating method.
                continue
            ranges = ranges_by_name.setdefault((name, method.path), [])
            if any(start <= method.start_line <= end for start, end in ranges):
                continue
            module = module_of_path(method.path) or ""
            methods_projection.append(PersistedCallGraphMethod(
                id=CALL_GRAPH_METHOD_ID_PREFIX + hashlib.sha256(
                    f"{module}|{method.path}|{method.start_line}|{name}".encode()
                ).hexdigest()[:24],
                module=module,
                qualified_method=name,
                path=method.path,
                start_line=method.start_line,
                end_line=method.start_line,
            ))
            ranges.append((method.start_line, method.start_line))
        store.replace_call_graph_methods(methods_projection)
        store.replace_call_edges(edges)
        store.set_meta(
            META_CALL_GRAPH_STATUS,
            "partial" if call_graph_timed_out else "complete",
        )
        store.set_meta(META_CALL_GRAPH_EDGE_COUNT, str(len(edges)))
        store.commit_checkpoint()
        _report_progress(
            progress,
            f"→ {engine_label} : checkpoint graphe d'appels persisté · {len(edges)} arête(s).",
        )

    current_join_methods_signature = call_graph_join_methods_signature(methods)
    if resume_call_graph_join:
        input_method_count = sum(bool(method.input_endpoint_ids) for method in methods)
        checkpoint_matches = (
            resume_join_methods_signature == current_join_methods_signature
            and 0 <= resume_join_entries <= input_method_count
        )
        if not checkpoint_matches:
            _report_progress(
                progress,
                f"→ {engine_label} : checkpoint de reprise incompatible avec les méthodes "
                "IN courantes ; reprise complète de la jointure.",
            )
            resume_join_entries = 0
            resume_join_flows = []
            store.set_meta(META_JOIN_COMPLETED_ENTRIES, "0")
            store.set_meta(META_JOIN_METHODS_SIGNATURE, current_join_methods_signature)
    else:
        store.set_meta(META_JOIN_METHODS_SIGNATURE, current_join_methods_signature)
    flows = materialize_code_flows(
        repo_root, all_endpoints, relation_modules, data_operations,
    )
    if (methods or refresh_call_graph_view) and engine is not None:
        if not resume_call_graph_join:
            store.delete_meta(META_JOIN_COMPLETED_ENTRIES)
            store.set_meta(META_JOIN_SIGNATURE, join_signature)
            store.set_meta(META_JOIN_COMPLETED_ENTRIES, "0")
            store.set_meta("code_flow_snapshot_status", "partial")
        _report_progress(progress, f"→ {engine_label} : préparation de l'analyse interprocédurale...")
        prepared_call_graph_flows: list[CodeFlow] | None = None
        call_graph_stats: dict[str, int] = {}
        calls: list[MethodCall] = []
        engine_method_projection: list[SourceMethod] = []
        call_graph_view_prefix = ""
        selected_view_module: DiscoveredModule | None = None
        if refresh_call_graph_view and show_call_chains_module is not None:
            selected_view_module = next(
                (
                    item for item in relation_modules
                    if module_identity(item) == show_call_chains_module
                    or item.name == show_call_chains_module
                ),
                None,
            )
            if selected_view_module is not None:
                relative_module_path = selected_view_module.path.resolve().relative_to(
                    repo_root.resolve()
                ).as_posix()
                call_graph_view_prefix = (
                    "" if relative_module_path == "." else relative_module_path
                )

        def publish_call_graph_progress(
            completed_projects: int,
            total_projects: int,
            project_name: str,
            calls: list[MethodCall],
            project_prefix: str = "",
            skip_materialization: bool = False,
        ) -> None:
            if skip_materialization:
                progress_flows = []
            elif prepared_call_graph_flows is not None:
                available_sites = {(call.caller_path, call.call_line) for call in calls}
                progress_flows = [
                    flow for flow in prepared_call_graph_flows
                    if completed_projects == total_projects or (
                        any(step.kind == "method_call" for step in flow.steps)
                        and all(
                            (step.path, step.start_line) in available_sites
                            for step in flow.steps if step.kind == "method_call"
                        )
                    )
                ]
            else:
                progress_flows = materialize_call_graph_flows(
                    methods, flow_endpoints(), calls, repo_root=repo_root,
                    modules=relation_modules,
                    operations=data_operations,
                    source_paths=list(current_hashes),
                    max_hops=config.engine_max_hops,
                    edge_confidence=config.engine_edge_confidence,
                    closed_world=not engine.resolves_types,
                    module_dependencies=relation_dependencies,
                    progress=progress,
                    call_graph_sink=persist_call_graph,
                )
            partial_flows = [
                *flows,
                *progress_flows,
            ]
            partial_flows = _ensure_unique_code_flow_ids(
                _deduplicate_code_flows(partial_flows, all_endpoints)
            )
            store.replace_code_flows(partial_flows)
            store.delete_meta("code_flow_signature")
            store.set_meta("code_flow_snapshot_status", "partial")
            store.commit_checkpoint()
            method_paths = {
                method.path for method in methods
                if not project_prefix
                or method.path == project_prefix
                or method.path.startswith(f"{project_prefix}/")
            }
            project_input_methods = sorted(
                (
                    method for method in methods
                    if method.path in method_paths and method.input_endpoint_ids
                ),
                key=lambda method: (method.path, method.start_line, method.id),
            )
            project_output_methods = sorted(
                (
                    method for method in methods
                    if method.path in method_paths and method.output_endpoint_ids
                ),
                key=lambda method: (method.path, method.start_line, method.id),
            )
            input_names = ", ".join(
                method.qualified_method for method in project_input_methods
            ) or "aucun"
            output_names = ", ".join(
                method.qualified_method for method in project_output_methods
            ) or "aucun"
            _report_progress(
                progress,
                f"→ {engine_label} : checkpoint {completed_projects}/{total_projects} "
                f"persisté · module {project_name} · "
                f"IN [{input_names}] · OUT [{output_names}] · "
                f"{len(partial_flows)} flux provisoire(s).",
            )
            if call_graph_progress is not None:
                call_graph_progress(CallGraphProgress(
                    engine=engine_name,
                    completed_projects=completed_projects,
                    total_projects=total_projects,
                    project_name=project_name,
                    endpoints=all_endpoints,
                    modules=relation_modules,
                    relations=relations,
                    integration_methods=methods,
                    code_flows=partial_flows,
                    project_input_methods=project_input_methods,
                    project_output_methods=project_output_methods,
                ))

        def publish_join_checkpoint(
            partial_call_graph_flows: list[CodeFlow],
            completed_methods: int,
            total_methods: int,
        ) -> None:
            partial_flows = _ensure_unique_code_flow_ids(
                _deduplicate_code_flows(
                    [*flows, *partial_call_graph_flows], all_endpoints
                )
            )
            report_built_chains(partial_flows)
            store.replace_code_flows(partial_flows)
            store.delete_meta("code_flow_signature")
            store.set_meta("code_flow_snapshot_status", "partial")
            store.set_meta(META_JOIN_COMPLETED_ENTRIES, str(completed_methods))
            store.commit_checkpoint()
            _report_progress(
                progress,
                f"→ {engine_label} : checkpoint jointure {completed_methods}/{total_methods} "
                f"méthode(s) IN · {len(partial_flows)} flux provisoire(s).",
            )
            if call_graph_progress is not None:
                call_graph_progress(CallGraphProgress(
                    engine=engine_name,
                    completed_projects=1,
                    total_projects=1,
                    project_name=f"jointure {engine_label}",
                    endpoints=all_endpoints,
                    modules=relation_modules,
                    relations=relations,
                    integration_methods=methods,
                    code_flows=partial_flows,
                    project_input_methods=[],
                    project_output_methods=[],
                    phase="join",
                    completed_units=completed_methods,
                    total_units=total_methods,
                ))

        def report_module_progress(
            project_calls_by_module: Sequence[tuple[str, list[MethodCall]]],
            roots: Sequence[tuple[str, Path, str]],
        ) -> None:
            """Report module progress and optionally publish checkpoints."""
            completed_calls: list[MethodCall] = []
            for number, (name, project_calls) in enumerate(project_calls_by_module, start=1):
                module_started_at = time.perf_counter()
                _report_progress(
                    progress,
                    f"  • {engine_label} module {number}/{len(roots)} : {name}",
                )
                completed_calls.extend(project_calls)
                project_prefix = next(
                    (prefix for root_name, _root, prefix in roots if root_name == name),
                    "",
                )
                if call_graph_progress is not None:
                    publish_call_graph_progress(
                        number,
                        len(project_calls_by_module),
                        name,
                        completed_calls,
                        project_prefix,
                    )
                _report_progress(
                    progress,
                    f"    ✓ {name} : {len(project_calls)} appel(s) traité(s) "
                    f"en {time.perf_counter() - module_started_at:.2f} s.",
                )

        engine_deadline = time.monotonic() + config.engine_timeout_seconds
        roots = _call_graph_module_roots(
            repo_root,
            [path for path in current_hashes if path.endswith(".java")],
            relation_modules,
        ) or [(f"projet {engine_label}", repo_root, "")]
        try:
            stage = f"{engine.name}-database"
            timer.begin(
                stage,
                f"→ {engine_label} : préparation et rafraîchissement du module..."
                if refresh_call_graph_view
                else f"→ {engine_label} : préparation et extraction globale...",
            )
            with engine.open(EngineRequest(
                repo_root=repo_root,
                allowed_paths=list(current_hashes),
                timeout_seconds=config.engine_timeout_seconds,
                deadline=engine_deadline,
                include_generated_sources=generate_sources,
                progress=progress,
                verbose=engine_progress,
            )) as session:
                enrich_strategy1_kafka_types(session)
                if refresh_call_graph_view and selected_view_module is not None:
                    roots = [(
                        module_identity(selected_view_module),
                        selected_view_module.path,
                        call_graph_view_prefix,
                    )]
                    calls = []
                else:
                    calls = session.calls()
                engine_method_projection = session.methods(
                    call_graph_view_prefix if refresh_call_graph_view else ""
                )
                scoped_project_calls = _partition_calls(calls, roots)
                # The reachability query adds no flow since ADR-78: a row
                # becomes a flow only through a route the join already
                # checks. The index no longer runs it (ADR-84).
            prepared_call_graph_flows = materialize_call_graph_flows(
                methods, flow_endpoints(), calls, repo_root=repo_root,
                modules=relation_modules,
                operations=data_operations,
                source_paths=list(current_hashes),
                max_hops=config.engine_max_hops,
                edge_confidence=config.engine_edge_confidence,
                closed_world=not engine.resolves_types,
                module_dependencies=relation_dependencies,
                stats=call_graph_stats,
                progress=progress,
                call_chain_progress=progress if show_call_chains else None,
                call_chain_module=show_call_chains_module,
                join_checkpoint=publish_join_checkpoint,
                resume_from_entry=resume_join_entries,
                initial_flows=resume_join_flows,
                call_graph_sink=persist_call_graph,
                persisted_edges=persisted_call_edges if refresh_call_graph_view else None,
                persisted_methods=persisted_call_graph_methods,
            )
            if not engine.resolves_types:
                # Without typed calls, a same-method path is already an
                # AST flow; only interprocedural paths add evidence.
                prepared_call_graph_flows = [
                    flow for flow in prepared_call_graph_flows
                    if any(step.kind == "method_call" for step in flow.steps)
                ]
            report_module_progress(scoped_project_calls, roots)
            timer.end(
                stage,
                f"préparation et rafraîchissement du module {engine_label}"
                if refresh_call_graph_view
                else f"préparation et extraction globale {engine_label}",
            )
        except CallGraphTimeout as exc:
            # A deadline is a soft indexing boundary. AST facts, relations
            # and any calls obtained before it remain useful; continue so
            # the committed snapshot can still be exported as partial. A
            # deadline in a later query (methods, payload types) keeps the
            # calls the completed call query already returned.
            calls = exc.calls or calls
            call_graph_timed_out = True
            _report_progress(
                progress,
                f"→ {engine_label} : délai dépassé ; poursuite avec les faits déjà indexés "
                "et exécution des post-traitements.",
            )
        except (CallGraphError, OSError) as exc:
            raise RuntimeError(str(exc)) from exc
        if refresh_call_graph_view:
            _report_progress(
                progress,
                f"→ {engine_label} : graphe global réutilisé, "
                "jointure des méthodes du module...",
            )
        else:
            _report_progress(
                progress,
                f"→ {engine_label} : {len(calls)} appel(s) extrait(s), "
                "jointure des méthodes...",
            )
        timer.begin("call-graph-join", f"→ {engine_label} : jointure des méthodes et matérialisation des flux...")
        call_graph_flows = prepared_call_graph_flows if prepared_call_graph_flows is not None else materialize_call_graph_flows(
            methods, flow_endpoints(), calls, repo_root=repo_root,
            modules=relation_modules,
            operations=data_operations,
            source_paths=list(current_hashes),
            max_hops=config.engine_max_hops,
            edge_confidence=config.engine_edge_confidence,
            closed_world=not engine.resolves_types,
            module_dependencies=relation_dependencies,
            stats=call_graph_stats,
            progress=progress,
            call_chain_progress=progress if show_call_chains else None,
            call_chain_module=show_call_chains_module,
            join_checkpoint=publish_join_checkpoint,
            resume_from_entry=resume_join_entries,
            initial_flows=resume_join_flows,
            call_graph_sink=persist_call_graph,
        )
        # AST and the interprocedural engine can describe the same
        # endpoint-to-endpoint flow. Keep one representative while the
        # engine call graph remains the source of internal call edges.
        flows = _ensure_unique_code_flow_ids(
            _deduplicate_code_flows([*flows, *call_graph_flows], all_endpoints)
        )
        report_built_chains(flows)
        store.replace_code_flows(flows)
        store.delete_meta("code_flow_signature")
        store.set_meta(
            META_INPUT_OUTPUT_STATUS,
            "partial" if call_graph_timed_out else "complete",
        )
        store.set_meta(META_INPUT_OUTPUT_FLOW_COUNT, str(len(call_graph_flows)))
        store.set_meta(META_JOIN_COMPLETED_ENTRIES, str(
            sum(bool(method.input_endpoint_ids) for method in methods)
        ))
        store.commit_checkpoint()
        _report_progress(
            progress,
            f"→ {engine_label} : checkpoint input → output persisté · "
            f"{len(call_graph_flows)} flux interprocédural(aux).",
        )
        timer.end("call-graph-join", f"jointure {engine_label} et matérialisation des flux")
        _report_progress(
            progress,
            f"→ {engine_label} : "
            f"{call_graph_stats['calls']} appel(s), {call_graph_stats['joined_calls']} jointure(s), "
            f"{call_graph_stats['explored_paths']} transition(s), "
            f"{len(call_graph_flows)} flux interprocédural(aux).",
        )
    service_aliases = {
        module_identity(module): local_spring_application_names(module.path, None)
        for module in relation_modules
    }
    flows = reconcile_code_flows(
        _ensure_unique_code_flow_ids(flows),
        all_endpoints,
        build_graph(
            group_endpoints_by_module(all_endpoints),
            rest_policy=(
                STRATEGY1_REST_TARGET_POLICY
                if topic_strategy == "strategy1"
                else DEFAULT_REST_TARGET_POLICY
            ),
            service_aliases=service_aliases,
        ),
    )
    store.replace_code_flows(_ensure_unique_code_flow_ids(flows))
    if call_graph_timed_out:
        # Do not mark an incomplete interprocedural pass as current. The
        # next incremental index must retry the engine even when source files
        # are unchanged. The flows come from a partial call set, so no join
        # checkpoint may survive: a resume would trust them as complete.
        store.delete_meta("code_flow_signature")
        store.set_meta("code_flow_snapshot_status", "partial")
        store.delete_meta(META_JOIN_SIGNATURE)
        store.delete_meta(META_JOIN_COMPLETED_ENTRIES)
    else:
        store.set_meta("code_flow_signature", flow_signature)
        store.set_meta("code_flow_snapshot_status", "complete")
        store.delete_meta(META_JOIN_SIGNATURE)
        store.delete_meta(META_JOIN_COMPLETED_ENTRIES)
    data_path_count = sum(
        bool(flow.steps) and flow.steps[-1].kind in DATA_STEP_KINDS
        for flow in flows
    )
    _report_progress(
        progress,
        f"→ Indexation : {len(flows) - data_path_count} flux de code "
        f"et {data_path_count} annotation(s) d'accès aux données collectée(s).",
    )
    timer.end("flows", "matérialisation des flux de code")
    return CallGraphStageResult(flows=flows, timed_out=call_graph_timed_out)
