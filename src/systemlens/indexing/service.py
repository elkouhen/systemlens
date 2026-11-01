import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from systemlens.infrastructure.config import Config
from systemlens.indexing.dto_inventory import materialize_kafka_dto_definitions
from systemlens.indexing.freshness import current_endpoint_inventory_signature
from systemlens.indexing.file_inventory import (
    analysis_inputs_signature as _analysis_inputs_signature,
    changes_require_dependent_rescan as _changes_require_dependent_rescan,
    dependent_rescan_paths as _dependent_rescan_paths,
    is_in_excluded_module as _is_in_excluded_module,
    list_repo_files as _list_repo_files,
    sha256_file as _sha256_file,
)
from systemlens.conventions.strategy1.indexing import requires_full_reindex
from systemlens.indexing.materializers import materialize_asyncapi_contracts, materialize_openapi_contracts
from systemlens.indexing.code_flows import (
    CODE_FLOW_SIGNATURE,
)
from systemlens.indexing.call_graph_stage import (
    CallGraphProgress,
    CallGraphProgressCallback,
    JoinResume,
    ProgressCallback,
    _report_progress,
    run_call_graph_stage,
)
from systemlens.domain.data_operation_projection import data_operations_from_modules
from systemlens.domain.code_flows import (
    DATA_STEP_KINDS,
    CodeFlow,
)
from systemlens.callgraph.engine import CallGraphEngine
from systemlens.callgraph.registry import load_engine
from systemlens.callgraph.source import SourceEngine
from systemlens.indexing.source_generation import generate_sources as _generate_sources
from systemlens.discovery.java import parser as java_parser
from systemlens.domain.models import (
    ExtractionDiagnostic,
    MessageEndpoint,
    ensure_unique_endpoint_ids,
)
from systemlens.domain.module_inventory import module_identity
from systemlens.discovery.build.modules import (
    discover_module_dependencies,
    discover_modules,
    discover_excluded_module_paths,
)
from systemlens.indexing.relations import build_architecture_relations
from systemlens.scanner import (
    clear_analysis_caches,
    infer_framework_endpoints,
    infer_kafka_endpoints,
    infer_json_kafka_flow_graph_endpoints,
    infer_markdown_topic_manifest_endpoints,
)
from systemlens.storage.sqlite import Store
from systemlens.discovery.kubernetes import KubernetesDiscoveryError, discover_workloads
from systemlens.domain.runtime import KubernetesWorkload


__all__ = ["CallGraphProgress", "CallGraphProgressCallback", "IndexReport", "index_repo"]


@dataclass
class IndexReport:
    scanned: int
    skipped: int
    findings_added: int
    findings_removed: int
    deleted_files: int
    endpoints_added: int = 0
    endpoints_removed: int = 0
    codeql_timed_out: bool = False


# The call-graph stage and its progress contract live in ``call_graph_stage``.


class _IndexStageTimer:
    """Emit elapsed durations for the human-facing indexing stages."""

    def __init__(self, progress: ProgressCallback | None) -> None:
        self.progress = progress
        self.started_at: dict[str, float] = {}
        self.total_started_at = time.perf_counter()

    def begin(self, stage: str, message: str) -> None:
        _report_progress(self.progress, message)
        self.started_at[stage] = time.perf_counter()

    def end(self, stage: str, label: str) -> None:
        started_at = self.started_at.pop(stage, None)
        if started_at is not None:
            _report_progress(self.progress, f"  ✓ {label} : {time.perf_counter() - started_at:.2f} s")

    def total(self) -> None:
        _report_progress(
            self.progress,
            f"✓ Indexation terminée en {time.perf_counter() - self.total_started_at:.2f} s.",
        )


def _trace(stage: str, **fields: object) -> None:
    """Emit an opt-in, flush-on-write checkpoint for native crash diagnosis."""
    if os.environ.get("SYSTEMLENS_TRACE") != "1":
        return
    details = " ".join(f"{name}={value}" for name, value in fields.items())
    print(f"SYSTEMLENS_TRACE ts={time.monotonic():.6f} stage={stage} {details}".rstrip(), file=sys.stderr, flush=True)


def _resume_invalidating_paths(paths: Sequence[str]) -> list[str]:
    """Return changes that can invalidate a persisted CodeQL join checkpoint."""
    presentation_suffixes = {
        ".adoc", ".css", ".html", ".js", ".log", ".md", ".rst", ".svg", ".txt",
    }
    return [
        path for path in paths
        if Path(path).suffix.lower() not in presentation_suffixes
    ]


def _index_repo(
    repo_root: Path,
    config: Config,
    store: Store,
    full: bool = False,
    disabled: frozenset[str] = frozenset(),
    extra_files: list[str] | None = None,
    topic_strategy: str | None = None,
    progress: ProgressCallback | None = None,
    kubernetes: bool = False,
    kubernetes_namespace: str | None = None,
    call_graph_engine: CallGraphEngine | None = None,
    call_graph_progress: CallGraphProgressCallback | None = None,
    codeql_progress: bool = False,
    generate_sources: bool = False,
    resume_codeql_join: bool = False,
    show_call_chains: bool = False,
    show_call_chains_module: str | None = None,
    refresh_codeql_view: bool = False,
) -> IndexReport:
    timer = _IndexStageTimer(progress)
    # Engine diagnostics are opt-in (``codeql_progress``); older configs
    # never make the index command verbose.
    if refresh_codeql_view and show_call_chains_module is None:
        raise ValueError("Refreshing the CodeQL view requires a selected module.")
    if refresh_codeql_view and store.get_meta("codeql_call_graph_status") != "complete":
        raise ValueError(
            "Refreshing one module requires a complete global CodeQL graph; "
            "run a global index first."
        )
    topic_strategy = topic_strategy or config.strategy
    strategy1_enabled = topic_strategy == "strategy1"
    disabled = disabled or frozenset(config.disabled_extractors)
    # BACKLOG-16 P2 : purge les lru_cache d'analyse best-effort (package
    # Java, propriétés Spring, module Maven/Gradle) avant de relire le
    # repo — nécessaire lorsque plusieurs indexations s'enchaînent dans le même
    # `reindex_findings` doit voir les fichiers tels qu'ils sont maintenant,
    # pas tels qu'un `systemlens index` précédent les avait mémorisés.
    clear_analysis_caches()
    if generate_sources:
        timer.begin(
            "generation",
            "→ Indexation : génération des sources Maven/Gradle...",
        )
        _generate_sources(
            repo_root,
            timeout=config.codeql_timeout_seconds,
            progress=progress,
        )
        timer.end("generation", "génération des sources")
        # Generated sources are build inputs for this run even when their
        # plugin output has the same hash as a previous index.
        full = True
    _trace(
        "index_repo.begin", root=repo_root, full=full, disabled=",".join(sorted(disabled)),
        topic_strategy=topic_strategy,
    )
    discovered_modules = []
    if "properties" not in disabled:
        timer.begin("modules", "→ Indexation : découverte des projets Maven/Gradle...")
        _trace("modules.begin")
        discovered_modules = discover_modules(
            repo_root,
            enrich_architecture="module-architecture" not in disabled,
            use_tree_sitter="module-tree-sitter" not in disabled,
        )
        _trace("modules.end", count=len(discovered_modules))
        if discovered_modules:
            for module in discovered_modules:
                _report_progress(
                    progress,
                    f"  • [{module.build_system}/{module.kind}] {module.name}  {module.path}",
                )
        else:
            _report_progress(progress, "  • aucun projet Maven/Gradle détecté ; scan de la racine.")
        timer.end("modules", "découverte des projets Maven/Gradle")
    # Les signatures d'inventaire d'endpoints pilotent aussi l'analyse locale
    # (REST/Kafka/manifests) : une évolution du code
    # d'inférence ou de stratégie Kafka doit forcer un rescan complet.
    endpoint_signature = current_endpoint_inventory_signature()
    if store.get_meta("endpoint_inventory_signature") != endpoint_signature:
        full = True
    if store.get_meta("topic_strategy") != topic_strategy:
        full = True
    analysis_inputs_signature = _analysis_inputs_signature(repo_root)
    if store.get_meta("analysis_inputs_signature") != analysis_inputs_signature:
        full = True

    timer.begin("files", "→ Indexation : inventaire des fichiers du dépôt...")
    _trace("files.begin")
    excluded_module_paths = discover_excluded_module_paths(repo_root)
    current_hashes = _list_repo_files(
        repo_root,
        config,
        excluded_module_paths=excluded_module_paths,
        include_strategy1_generated_sources=strategy1_enabled,
        include_generated_sources=generate_sources,
    )
    # Extractors, method inventory, symbol pass and flow materialization all
    # read the same Java trees: keep one parse per file for this pass.
    java_parser.reserve_parse_cache(sum(path.endswith(".java") for path in current_hashes))
    if kubernetes:
        _report_progress(progress, "→ Indexation : découverte des workloads Kubernetes...")
        try:
            workloads = discover_workloads(namespace=kubernetes_namespace)
        except KubernetesDiscoveryError as exc:
            raise RuntimeError(str(exc)) from exc
        by_name: dict[str, list[KubernetesWorkload]] = {
            workload.name: [] for workload in workloads
        }
        for workload in workloads:
            by_name[workload.name].append(workload)
        discovered_modules = [
            replace(module, kubernetes_workloads=tuple(by_name.get(module.name, [])))
            for module in discovered_modules
        ]
    for rel_path in extra_files or []:
        candidate = repo_root / rel_path
        if candidate.is_file() and not _is_in_excluded_module(candidate, excluded_module_paths):
            current_hashes[rel_path] = _sha256_file(candidate)
    previous_hashes = store.get_file_hashes()
    _trace("files.end", current=len(current_hashes), previous=len(previous_hashes))
    timer.end("files", "inventaire des fichiers")

    timer.begin("delta", "→ Indexation : calcul du delta incrémental...")
    # The module inventory is intentionally materialized with the index rather
    # than reconstructed by `systemlens modules`: its configuration examples describe
    # the exact repository state that was audited.
    current_paths = set(current_hashes)
    previous_paths = set(previous_hashes)

    deleted = sorted(previous_paths - current_paths)

    if full:
        changed = sorted(current_paths)
    else:
        added = current_paths - previous_paths
        modified = {
            p
            for p in current_paths & previous_paths
            if current_hashes[p] != previous_hashes[p]
        }
        changed = sorted(added | modified)
    unchanged = current_paths - set(changed)
    # Strategy1 resolves service declarations against contracts in model-*
    # modules. Only a change to that cross-module join requires a full pass;
    # ordinary Java/configuration changes keep the incremental fast path.
    explicit_engine = call_graph_engine is not None
    engine: CallGraphEngine | None = call_graph_engine or load_engine(
        config.call_graph_engine,
        threads=config.codeql_threads,
        ram_mb=config.codeql_ram_mb,
    )
    if engine is not None and not engine.available():
        _report_progress(
            progress,
            f"→ Indexation : {engine.label} indisponible ; moteur source (tree-sitter) utilisé.",
        )
        engine = SourceEngine()
    if refresh_codeql_view and (engine is None or not engine.supports_module_refresh):
        raise ValueError(
            "Refreshing one module requires an engine that supports module refresh (codeql)."
        )
    engine_name = engine.name if engine is not None else "none"
    engine_label = engine.label if engine is not None else "Graphe d'appel"
    flow_signature = (
        f"{CODE_FLOW_SIGNATURE}|engine={engine_name}|"
        f"hops={config.codeql_max_hops}"
        f"|edge-confidence={config.codeql_edge_confidence}"
    )
    join_signature = f"{flow_signature}|inputs={analysis_inputs_signature}"
    resume_join_entries = 0
    resume_join_flows: list[CodeFlow] = []
    resume_join_methods_signature: str | None = None
    if resume_codeql_join:
        resume_invalidating_paths = _resume_invalidating_paths([*changed, *deleted])
        if full or resume_invalidating_paths:
            raise ValueError(
                "CodeQL join resume requires unchanged analysis inputs; "
                f"changed files: {', '.join(resume_invalidating_paths[:10])}. "
                "Run a normal index first."
            )
        if store.get_meta("code_flow_snapshot_status") != "partial":
            raise ValueError("No partial CodeQL join snapshot is available to resume.")
        if store.get_meta("codeql_join_signature") != join_signature:
            raise ValueError(
                "The partial CodeQL join checkpoint does not match the current "
                "repository or analysis configuration."
            )
        try:
            resume_join_entries = int(store.get_meta("codeql_join_completed_entries") or "0")
        except ValueError as exc:
            raise ValueError("The CodeQL join checkpoint offset is invalid.") from exc
        resume_join_methods_signature = store.get_meta("codeql_join_methods_signature")
        resume_join_flows = store.all_code_flows()
        _report_progress(
            progress,
            f"→ {engine_label} : reprise de la jointure après la méthode IN "
            f"{resume_join_entries} ({len(resume_join_flows)} flux persisté(s)).",
        )
    codeql_timed_out = False
    if (
        topic_strategy == "strategy1"
        and not full
        and requires_full_reindex(
            set(changed) | set(deleted), repo_root, discovered_modules
        )
    ):
        full = True
        changed = sorted(current_paths)
        unchanged = set()
    # Spring configuration and build descriptors are analysis dependencies of
    # Java endpoint facts.  Their own hash delta is insufficient: an unchanged
    # Java file can resolve to a different topic, URL, application name, or
    # module identity after one of these files changes or disappears.
    if not full and _changes_require_dependent_rescan(set(changed) | set(deleted)):
        dependent_paths = _dependent_rescan_paths(
            set(changed) | set(deleted),
            current_paths,
            repo_root,
            discovered_modules,
        )
        if dependent_paths is None:
            full = True
            changed = sorted(current_paths)
            unchanged = set()
        else:
            changed = sorted(set(changed) | dependent_paths)
            unchanged = current_paths - set(changed)
    _report_progress(
        progress,
        "→ Indexation : delta calculé "
        f"({len(changed)} fichier(s) à scanner, {len(unchanged)} inchangé(s), "
        f"{len(deleted)} supprimé(s)).",
    )
    timer.end("delta", "calcul du delta incrémental")

    # Les fichiers supprimés quittent toujours l'inventaire.
    endpoints_removed = store.count_endpoints_for_paths(deleted)
    store.remove_files(deleted)  # purge aussi les endpoints (K1)

    # Clear retired external-analyzer results once, including for files which
    # did not otherwise need a rescan.
    legacy_findings_removed = store.clear_findings_once("ast_only_analysis_v1")
    endpoints_added = 0
    endpoints: list[MessageEndpoint] = []
    diagnostics: list[ExtractionDiagnostic] = []
    if changed:
        endpoints_removed += store.count_endpoints_for_paths(changed)
        timer.begin(
            "ast",
            f"→ Indexation : analyse AST de {len(changed)} fichier(s) en une passe...",
        )
        _trace("endpoint_inference.begin")
        _report_progress(
            progress,
            f"  • AST 1/1 : base de code ({len(changed)} fichier(s))",
        )
        endpoints.extend(
            infer_framework_endpoints(
                repo_root,
                changed,
                configured_api_client_strategy1=strategy1_enabled,
            )
        )
        endpoints.extend(
            infer_kafka_endpoints(repo_root, changed, strategy1=strategy1_enabled)
        )
        endpoints.extend(infer_markdown_topic_manifest_endpoints(repo_root, changed))
        endpoints.extend(infer_json_kafka_flow_graph_endpoints(repo_root, changed))
        _report_progress(progress, "  ✓ AST 1/1 : analyse terminée")
        _trace("endpoint_inference.end", endpoints=len(endpoints))
        timer.end("ast", "analyse AST")

        timer.begin(
            "endpoints",
            "→ Indexation : écriture des résultats "
            f"({len(endpoints)} endpoint(s)).",
        )
        endpoints = ensure_unique_endpoint_ids(endpoints)
        store.replace_endpoints_for_files(changed, endpoints)
        timer.end("endpoints", "écriture des endpoints")
        _trace("store.endpoints_written", endpoints=len(endpoints))
        endpoints_added = len(endpoints)

        dynamic_endpoints: dict[tuple[str, str], list[str]] = defaultdict(list)
        for endpoint in endpoints:
            if not endpoint.topic_dynamic:
                continue
            extractor = endpoint.framework or endpoint.system
            reason = "topic-pattern" if "topicPattern" in endpoint.snippet else "unresolved-expression"
            dynamic_endpoints[(endpoint.path, extractor)].append(
                f"{endpoint.role} line {endpoint.start_line}: {reason}"
            )
        for (path, extractor), entries in sorted(dynamic_endpoints.items()):
            diagnostics.append(ExtractionDiagnostic(
                path=path,
                extractor=extractor,
                category="dynamic_endpoint",
                severity="info",
                detail=(
                    "Static endpoint expression remains unresolved; no relation was inferred. "
                    + "; ".join(sorted(set(entries)))
                ),
            ))

        for path in changed:
            if not path.endswith(".java"):
                continue
            parsed_java = java_parser.parse_java(str(repo_root), path)
            if parsed_java is not None and parsed_java[1].has_error:
                diagnostics.append(ExtractionDiagnostic(
                    path=path,
                    extractor="tree-sitter-java",
                    category="parse_failed",
                    severity="warning",
                    detail=(
                        "Java source contains syntax errors; partial facts may have been "
                        "extracted, but coverage is incomplete."
                    ),
                ))
        store.replace_extraction_diagnostics_for_files(changed, diagnostics)

    # Les empreintes de fichiers sont persistées afin de garder l'indexation
    # incrémentale.
    for path in changed:
        store.set_file_hash(path, current_hashes[path])

    store.set_meta("endpoint_inventory_signature", endpoint_signature)
    store.set_meta("endpoint_inventory_indexed", "1")
    store.set_meta("topic_strategy", topic_strategy)
    store.set_meta("analysis_inputs_signature", analysis_inputs_signature)
    store.delete_meta("vscode_wsl_distro")
    # Persist only after the scan path has completed.  The inventory remains
    # transactional with the rest of the index and represents the audited
    # repository state, not a partially failed scan.
    if "properties" not in disabled:
        timer.begin("properties", "→ Indexation : inventaire des projets et propriétés...")
        _trace("store.modules.begin", count=len(discovered_modules))
        store.replace_modules(discovered_modules)
        # Typed data operations live in their own table (relative paths only);
        # module discovery is a full rescan, so the table is rebuilt with it.
        store.replace_data_operations(data_operations_from_modules(repo_root, discovered_modules))
        module_dependencies = discover_module_dependencies(repo_root, discovered_modules)
        store.replace_module_dependencies(module_dependencies)
        _trace("store.modules.end")
        timer.end("properties", "inventaire des projets et propriétés")
    else:
        _report_progress(progress, "→ Indexation : propriétés et inventaire des projets désactivés, snapshot conservé.")

    relation_modules = discovered_modules if "properties" not in disabled else store.all_modules()
    data_operations = store.all_data_operations() or data_operations_from_modules(
        repo_root, relation_modules,
    )
    relation_dependencies = (
        module_dependencies if "properties" not in disabled else store.all_module_dependencies()
    )
    timer.begin("relations", "→ Indexation : matérialisation des relations d'architecture...")
    endpoints_by_service: dict[str, list[MessageEndpoint]] = {}
    for endpoint in store.all_endpoints():
        if endpoint.module:
            endpoints_by_service.setdefault(endpoint.module, []).append(endpoint)
    store.replace_kafka_dto_definitions(
        materialize_kafka_dto_definitions(endpoints_by_service, relation_modules)
    )
    # Also normalize a persisted module snapshot when module inventory refresh
    # is disabled for this run.
    store.replace_openapi_contracts(materialize_openapi_contracts(relation_modules))
    store.replace_asyncapi_contracts(materialize_asyncapi_contracts(relation_modules))
    relations = build_architecture_relations(
        relation_modules,
        store.all_endpoints(),
        relation_dependencies,
        kafka_reply_strategy1=topic_strategy == "strategy1",
    )
    store.replace_architecture_relations(relations)
    _report_progress(progress, f"→ Indexation : {len(relations)} relation(s) d'architecture matérialisée(s).")
    timer.end("relations", "matérialisation des relations d'architecture")

    should_index_flows = (
        full
        or changed
        or deleted
        or explicit_engine
        or refresh_codeql_view
        or store.get_meta("code_flow_signature") != flow_signature
    )
    if should_index_flows:
        stage_result = run_call_graph_stage(
            store=store,
            repo_root=repo_root,
            config=config,
            relation_modules=relation_modules,
            relation_dependencies=relation_dependencies,
            relations=relations,
            current_hashes=current_hashes,
            data_operations=data_operations,
            engine=engine,
            engine_name=engine_name,
            engine_label=engine_label,
            topic_strategy=topic_strategy,
            flow_signature=flow_signature,
            join_signature=join_signature,
            resume=(
                JoinResume(
                    entries=resume_join_entries,
                    flows=tuple(resume_join_flows),
                    methods_signature=resume_join_methods_signature,
                )
                if resume_codeql_join else None
            ),
            refresh_codeql_view=refresh_codeql_view,
            show_call_chains=show_call_chains,
            show_call_chains_module=show_call_chains_module,
            generate_sources=generate_sources,
            codeql_progress=codeql_progress,
            progress=progress,
            call_graph_progress=call_graph_progress,
            timer=timer,
        )
        codeql_timed_out = stage_result.timed_out
    else:
        _report_progress(
            progress,
            "→ Indexation : flux de code inchangés ; étape ignorée.",
        )

    timer.begin("statistics", "→ Indexation : calcul des statistiques finales...")
    indexed_ports = [
        endpoint for endpoint in store.all_endpoints()
        if endpoint.system in {"rest", "kafka"}
        and endpoint.role in {"serve", "consume", "call", "produce"}
    ]
    input_ports = [endpoint for endpoint in indexed_ports if endpoint.role in {"serve", "consume"}]
    output_ports = [endpoint for endpoint in indexed_ports if endpoint.role in {"call", "produce"}]
    # Keep the end-of-index output useful on large repositories: report one
    # compact line per module instead of flooding the terminal with every port.
    endpoint_by_id = {endpoint.id: endpoint for endpoint in indexed_ports}
    internal_integrations_by_module: Counter[str] = Counter()
    internal_data_paths_by_module: Counter[str] = Counter()
    for flow in store.all_code_flows():
        involved_modules = {
            endpoint_by_id[step.endpoint_id].module
            for step in flow.steps
            if step.endpoint_id in endpoint_by_id and endpoint_by_id[step.endpoint_id].module
        }
        if not involved_modules or involved_modules == {flow.module}:
            if flow.steps and flow.steps[-1].kind in DATA_STEP_KINDS:
                internal_data_paths_by_module[flow.module] += 1
            else:
                internal_integrations_by_module[flow.module] += 1
    in_by_module = Counter(
        endpoint.module or "<racine>"
        for endpoint in input_ports
    )
    out_by_module = Counter(
        endpoint.module or "<racine>"
        for endpoint in output_ports
    )
    module_names = sorted(
        {module_identity(module) for module in relation_modules}
        | set(in_by_module)
        | set(out_by_module)
        | set(internal_integrations_by_module)
        | set(internal_data_paths_by_module)
    )
    _report_progress(progress, "→ Indexation : statistiques par module")
    for module_name in module_names:
        _report_progress(
            progress,
            f"  • {module_name} : {in_by_module[module_name]} IN, "
            f"{out_by_module[module_name]} OUT, "
            f"{internal_integrations_by_module[module_name]} flux d'appel, "
            f"{internal_data_paths_by_module[module_name]} annotations de données",
        )
    _report_progress(
        progress,
        "→ Indexation : statistiques globales : "
        f"{len(input_ports)} IN, {len(output_ports)} OUT, "
        f"{sum(internal_integrations_by_module.values())} flux d'appel, "
        f"{sum(internal_data_paths_by_module.values())} annotations de données, "
        f"{len(module_names)} modules",
    )
    timer.end("statistics", "calcul des statistiques finales")

    _trace("index_repo.end", scanned=len(changed), skipped=len(unchanged))
    timer.total()
    return IndexReport(
        scanned=len(changed),
        skipped=len(unchanged),
        findings_added=0,
        findings_removed=legacy_findings_removed,
        deleted_files=len(deleted),
        endpoints_added=endpoints_added,
        endpoints_removed=endpoints_removed,
        codeql_timed_out=codeql_timed_out,
    )


def index_repo(
    repo_root: Path,
    config: Config,
    store: Store,
    full: bool = False,
    disabled: frozenset[str] = frozenset(),
    extra_files: list[str] | None = None,
    topic_strategy: str | None = None,
    progress: ProgressCallback | None = None,
    kubernetes: bool = False,
    kubernetes_namespace: str | None = None,
    call_graph_engine: CallGraphEngine | None = None,
    call_graph_progress: CallGraphProgressCallback | None = None,
    codeql_progress: bool = False,
    generate_sources: bool = False,
    resume_codeql_join: bool = False,
    show_call_chains: bool = False,
    show_call_chains_module: str | None = None,
    refresh_codeql_view: bool = False,
) -> IndexReport:
    """Index one repository, publishing explicit call-graph checkpoints when needed.

    ``call_graph_engine`` overrides the engine named by the configuration,
    for instance a CodeQL engine bound to an existing database.
    """
    with store.transaction():
        return _index_repo(
            repo_root,
            config,
            store,
            full=full,
            disabled=disabled,
            extra_files=extra_files,
            topic_strategy=topic_strategy,
            progress=progress,
            kubernetes=kubernetes,
            kubernetes_namespace=kubernetes_namespace,
            call_graph_engine=call_graph_engine,
            call_graph_progress=call_graph_progress,
            codeql_progress=codeql_progress,
            generate_sources=generate_sources,
            resume_codeql_join=resume_codeql_join,
            show_call_chains=show_call_chains,
            show_call_chains_module=show_call_chains_module,
            refresh_codeql_view=refresh_codeql_view,
        )
