import os
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Sequence

from systemlens.infrastructure.config import Config
from systemlens.indexing.dto_inventory import materialize_kafka_dto_definitions
from systemlens.indexing.freshness import current_endpoint_inventory_signature
from systemlens.indexing.file_inventory import (
    analysis_inputs_signature as _analysis_inputs_signature,
    changes_require_dependent_rescan as _changes_require_dependent_rescan,
    is_in_excluded_module as _is_in_excluded_module,
    list_repo_files as _list_repo_files,
    sha256_file as _sha256_file,
)
from systemlens.conventions.strategy1.indexing import requires_full_reindex
from systemlens.conventions.strategy1.kafka import (
    apply_kafka_endpoints,
    infer_kafka_endpoints as infer_strategy1_kafka_endpoints,
)
from systemlens.indexing.materializers import materialize_asyncapi_contracts, materialize_openapi_contracts
from systemlens.indexing.code_flows import (
    CODE_FLOW_SIGNATURE, _deduplicate_code_flows, materialize_code_flows,
    materialize_codeql_code_flows,
    materialize_kafka_flow_continuations, reconcile_code_flows,
)
from systemlens.indexing.integration_methods import materialize_integration_methods
from systemlens.domain.code_flows import CodeFlow, IntegrationMethod
from systemlens.indexing.codeql import (
    CodeQLCall,
    CodeQLReachability,
    CodeQLError,
    automatic_codeql_database,
    codeql_executable,
    extract_codeql_calls,
    extract_codeql_reachability,
)
from systemlens.discovery.java import parser as java_parser
from systemlens.domain.models import ArchitectureRelation, ExtractionDiagnostic, MessageEndpoint
from systemlens.domain.graph import build_graph, group_endpoints_by_module
from systemlens.domain.module_inventory import DiscoveredModule, module_identity
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
    local_spring_application_names,
)
from systemlens.storage.sqlite import Store
from systemlens.discovery.kubernetes import KubernetesDiscoveryError, discover_workloads
from systemlens.domain.runtime import KubernetesWorkload


ProgressCallback = Callable[[str], None]


@dataclass
class IndexReport:
    scanned: int
    skipped: int
    findings_added: int
    findings_removed: int
    deleted_files: int
    endpoints_added: int = 0
    endpoints_removed: int = 0


@dataclass(frozen=True)
class CallGraphProgress:
    """One explicitly provisional call-graph checkpoint.

    The checkpoint is emitted only after one local analyzer project has
    completed.  It is intentionally separate from the transactional SQLite
    snapshot: consumers must label it as incomplete until indexing commits.
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


CallGraphProgressCallback = Callable[[CallGraphProgress], None]


def _codeql_module_roots(
    repo_root: Path, java_paths: Sequence[str], modules: Sequence[DiscoveredModule]
) -> list[tuple[str, Path, str]]:
    """Return the source-owning build roots for module-scoped CodeQL runs.

    Each Java source belongs to its deepest discovered build module. This
    avoids treating an aggregator as a second analysis unit for its children,
    while preserving a root-level fallback for sources outside a descriptor.
    The returned prefix maps CodeQL's module-relative paths to repository
    relative evidence paths.
    """
    roots = sorted(modules, key=lambda module: len(module.path.resolve().parts), reverse=True)
    selected: dict[Path, str] = {}
    for relative_path in java_paths:
        candidate = repo_root / relative_path
        owner = next(
            (
                module for module in roots
                if module.path.resolve() == candidate.parent
                or module.path.resolve() in candidate.parents
            ),
            None,
        )
        if owner is None:
            selected.setdefault(repo_root.resolve(), "racine du dépôt")
        else:
            selected.setdefault(owner.path.resolve(), module_identity(owner))
    result: list[tuple[str, Path, str]] = []
    for root, name in sorted(selected.items(), key=lambda item: (item[1], str(item[0]))):
        prefix = "" if root == repo_root.resolve() else root.relative_to(repo_root.resolve()).as_posix()
        result.append((name, root, prefix))
    return result


def _partition_codeql_calls(
    calls: Sequence[CodeQLCall],
    roots: Sequence[tuple[str, Path, str]],
) -> list[tuple[str, list[CodeQLCall]]]:
    """Partition global CodeQL results by caller project for progress only."""
    by_project: dict[str, list[CodeQLCall]] = {
        name: [] for name, _root, _prefix in roots
    }
    ordered = sorted(roots, key=lambda item: len(item[2]), reverse=True)
    fallback = roots[0][0] if roots else "racine du dépôt"
    for call in calls:
        project = fallback
        for name, _root, prefix in ordered:
            if prefix and (
                call.caller_path == prefix
                or call.caller_path.startswith(f"{prefix}/")
            ):
                project = name
                break
            if not prefix:
                project = name
        by_project.setdefault(project, []).append(call)
    return [(name, by_project.get(name, [])) for name, _root, _prefix in roots]


def _report_progress(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)


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
    codeql_database: Path | None = None,
    call_graph_progress: CallGraphProgressCallback | None = None,
    codeql_progress: bool = False,
    generate_sources: bool = False,
) -> IndexReport:
    timer = _IndexStageTimer(progress)
    codeql_verbosity = "progress++" if codeql_progress else config.codeql_verbosity
    if codeql_database is not None and config.call_graph_engine != "codeql":
        raise ValueError("A CodeQL database requires the codeql call-graph engine.")
    topic_strategy = topic_strategy or config.strategy
    disabled = disabled or frozenset(config.disabled_extractors)
    # BACKLOG-16 P2 : purge les lru_cache d'analyse best-effort (package
    # Java, propriétés Spring, module Maven/Gradle) avant de relire le
    # repo — nécessaire dans un process long-vivant (serveur MCP) où
    # `reindex_findings` doit voir les fichiers tels qu'ils sont maintenant,
    # pas tels qu'un `systemlens index` précédent les avait mémorisés.
    clear_analysis_caches()
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
    )
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
    call_graph_engine = config.call_graph_engine
    engine_available = call_graph_engine == "codeql" and codeql_executable() is not None
    flow_signature = (
        f"{CODE_FLOW_SIGNATURE}|engine={call_graph_engine}|"
        f"available={engine_available}|hops={config.codeql_max_hops}|"
        f"paths={config.codeql_max_paths}"
    )
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
        full = True
        changed = sorted(current_paths)
        unchanged = set()
    _report_progress(
        progress,
        "→ Indexation : delta calculé "
        f"({len(changed)} fichier(s) à scanner, {len(unchanged)} inchangé(s), "
        f"{len(deleted)} supprimé(s)).",
    )

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
                configured_api_client_strategy1=topic_strategy == "strategy1",
            )
        )
        endpoints.extend(infer_kafka_endpoints(repo_root, changed))
        endpoints.extend(infer_markdown_topic_manifest_endpoints(repo_root, changed))
        endpoints.extend(infer_json_kafka_flow_graph_endpoints(repo_root, changed))
        _report_progress(progress, "  ✓ AST 1/1 : analyse terminée")
        if topic_strategy == "strategy1":
            endpoints = apply_kafka_endpoints(
                endpoints, infer_strategy1_kafka_endpoints(repo_root, changed)
            )
        _trace("endpoint_inference.end", endpoints=len(endpoints))
        timer.end("ast", "analyse AST")

        timer.begin(
            "endpoints",
            "→ Indexation : écriture des résultats "
            f"({len(endpoints)} endpoint(s)).",
        )
        store.replace_endpoints_for_files(changed, endpoints)
        timer.end("endpoints", "écriture des endpoints")
        _trace("store.endpoints_written", endpoints=len(endpoints))
        endpoints_added = len(endpoints)

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
        module_dependencies = discover_module_dependencies(repo_root, discovered_modules)
        store.replace_module_dependencies(module_dependencies)
        _trace("store.modules.end")
        timer.end("properties", "inventaire des projets et propriétés")
    else:
        _report_progress(progress, "→ Indexation : propriétés et inventaire des projets désactivés, snapshot conservé.")

    relation_modules = discovered_modules if "properties" not in disabled else store.all_modules()
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

    if (
        full
        or changed
        or deleted
        or codeql_database is not None
        or store.get_meta("code_flow_signature") != flow_signature
    ):
        timer.begin("flows", "→ Indexation : matérialisation des flux de code...")
        all_endpoints = store.all_endpoints()
        methods = materialize_integration_methods(
            repo_root, all_endpoints, list(current_hashes), relation_modules
        )
        store.replace_integration_methods(methods)
        flows = materialize_code_flows(repo_root, all_endpoints, relation_modules)
        if methods and call_graph_engine != "none" and (
            (call_graph_engine == "codeql" and codeql_database is not None) or engine_available
        ):
            engine_label = "CodeQL"
            _report_progress(progress, f"→ {engine_label} : préparation de l'analyse interprocédurale...")
            reachability: list[CodeQLReachability] = []
            prepared_codeql_flows: list[CodeFlow] | None = None
            codeql_stats: dict[str, int] = {}

            def publish_call_graph_progress(
                completed_projects: int,
                total_projects: int,
                project_name: str,
                calls: list[CodeQLCall],
            ) -> None:
                if call_graph_progress is None:
                    return
                if prepared_codeql_flows is not None:
                    available_sites = {(call.caller_path, call.call_line) for call in calls}
                    progress_flows = [
                        flow for flow in prepared_codeql_flows
                        if completed_projects == total_projects or (
                            any(step.kind == "method_call" for step in flow.steps)
                            and all(
                                (step.path, step.start_line) in available_sites
                                for step in flow.steps if step.kind == "method_call"
                            )
                        )
                    ]
                else:
                    progress_flows = materialize_codeql_code_flows(
                        methods, all_endpoints, calls, repo_root=repo_root,
                        source_paths=list(current_hashes),
                        max_hops=config.codeql_max_hops,
                        max_paths=config.codeql_max_paths,
                    )
                partial_flows = [
                    *flows,
                    *progress_flows,
                ]
                call_graph_progress(CallGraphProgress(
                    engine=call_graph_engine,
                    completed_projects=completed_projects,
                    total_projects=total_projects,
                    project_name=project_name,
                    endpoints=all_endpoints,
                    modules=relation_modules,
                    relations=relations,
                    integration_methods=methods,
                    code_flows=partial_flows,
                ))

            try:
                if codeql_database is not None:
                    timer.begin("codeql-extract", "→ CodeQL : extraction des appels Java depuis la base fournie...")
                    calls = extract_codeql_calls(
                        codeql_database, timeout_seconds=config.codeql_timeout_seconds,
                        threads=config.codeql_threads, ram_mb=config.codeql_ram_mb,
                        verbosity=codeql_verbosity,
                        progress=progress if codeql_verbosity is not None else None,
                    )
                    timer.end("codeql-extract", "extraction des appels CodeQL")
                    reachability = extract_codeql_reachability(
                        codeql_database, methods,
                        timeout_seconds=config.codeql_timeout_seconds,
                        threads=config.codeql_threads, ram_mb=config.codeql_ram_mb,
                    )
                    if call_graph_progress is not None:
                        prepared_codeql_flows = materialize_codeql_code_flows(
                            methods, all_endpoints, calls, repo_root=repo_root,
                            source_paths=list(current_hashes),
                            max_hops=config.codeql_max_hops,
                            max_paths=config.codeql_max_paths,
                            stats=codeql_stats, reachability=reachability,
                        )
                    publish_call_graph_progress(1, 1, "base CodeQL fournie", calls)
                else:
                    roots = _codeql_module_roots(
                        repo_root,
                        [path for path in current_hashes if path.endswith(".java")],
                        relation_modules,
                    )
                    stage = f"{call_graph_engine}-database"
                    timer.begin(stage, f"→ {engine_label} : création et extraction globale...")
                    if call_graph_engine == "codeql":
                        if codeql_verbosity is None:
                            if generate_sources:
                                database_context = automatic_codeql_database(
                                    repo_root,
                                    timeout_seconds=config.codeql_timeout_seconds,
                                    threads=config.codeql_threads,
                                    ram_mb=config.codeql_ram_mb,
                                    generate_sources=True,
                                )
                            else:
                                database_context = automatic_codeql_database(
                                    repo_root,
                                    timeout_seconds=config.codeql_timeout_seconds,
                                    threads=config.codeql_threads,
                                    ram_mb=config.codeql_ram_mb,
                                )
                        elif generate_sources:
                            database_context = automatic_codeql_database(
                                repo_root,
                                timeout_seconds=config.codeql_timeout_seconds,
                                threads=config.codeql_threads,
                                ram_mb=config.codeql_ram_mb,
                                verbosity=codeql_verbosity,
                                progress=progress,
                                generate_sources=True,
                            )
                        else:
                            database_context = automatic_codeql_database(
                                repo_root,
                                timeout_seconds=config.codeql_timeout_seconds,
                                threads=config.codeql_threads,
                                ram_mb=config.codeql_ram_mb,
                                verbosity=codeql_verbosity,
                                progress=progress,
                            )
                        with database_context as database:
                            assert database is not None
                            calls = extract_codeql_calls(
                                database, timeout_seconds=config.codeql_timeout_seconds,
                                threads=config.codeql_threads, ram_mb=config.codeql_ram_mb,
                                verbosity=codeql_verbosity,
                                progress=progress if codeql_verbosity is not None else None,
                            )
                            reachability = extract_codeql_reachability(
                                database, methods,
                                timeout_seconds=config.codeql_timeout_seconds,
                                threads=config.codeql_threads, ram_mb=config.codeql_ram_mb,
                            )
                        partitioned_calls = _partition_codeql_calls(calls, roots)
                        if call_graph_progress is not None:
                            prepared_codeql_flows = materialize_codeql_code_flows(
                                methods, all_endpoints, calls, repo_root=repo_root,
                                source_paths=list(current_hashes),
                                max_hops=config.codeql_max_hops,
                                max_paths=config.codeql_max_paths,
                                stats=codeql_stats, reachability=reachability,
                            )
                        completed_calls: list[CodeQLCall] = []
                        for number, (name, project_calls) in enumerate(partitioned_calls, start=1):
                            module_started_at = time.perf_counter()
                            _report_progress(
                                progress,
                                f"  • {engine_label} module {number}/{len(roots)} : {name}",
                            )
                            completed_calls.extend(project_calls)
                            publish_call_graph_progress(
                                number, len(roots), name, completed_calls
                            )
                            _report_progress(
                                progress,
                                f"    ✓ {name} : {len(project_calls)} appel(s) extrait(s) "
                                f"en {time.perf_counter() - module_started_at:.2f} s.",
                            )
                    timer.end(stage, f"création et extraction globale {engine_label}")
            except (CodeQLError, OSError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError(str(exc)) from exc
            _report_progress(progress, f"→ {engine_label} : {len(calls)} appel(s) extrait(s), jointure des méthodes...")
            timer.begin("call-graph-join", f"→ {engine_label} : jointure des méthodes et matérialisation des flux...")
            codeql_flows = prepared_codeql_flows if prepared_codeql_flows is not None else materialize_codeql_code_flows(
                methods, all_endpoints, calls, repo_root=repo_root,
                source_paths=list(current_hashes),
                max_hops=config.codeql_max_hops,
                max_paths=config.codeql_max_paths,
                stats=codeql_stats,
                reachability=reachability,
            )
            # AST and the interprocedural engine can describe the same
            # endpoint-to-endpoint flow. Keep one representative before
            # composing Kafka continuations, otherwise the same flow is
            # rendered more than once in the Flux view.
            flows = _deduplicate_code_flows([*flows, *codeql_flows])
            timer.end("call-graph-join", f"jointure {engine_label} et matérialisation des flux")
            limit_note = (
                f" limite atteinte ({config.codeql_max_paths} transitions)."
                if codeql_stats["truncated_paths"] else ""
            )
            _report_progress(
                progress,
                f"→ {engine_label} : "
                f"{codeql_stats['calls']} appel(s), {codeql_stats['joined_calls']} jointure(s), "
                f"{codeql_stats['explored_paths']} transition(s), "
                f"{len(codeql_flows)} flux interprocédural(aux), "
                f"{len(reachability)} reachability(s) directe(s).{limit_note}",
            )
        elif methods and call_graph_engine != "none":
            _report_progress(
                progress,
                f"→ Indexation : {call_graph_engine} indisponible ; flux interprocéduraux ignorés.",
            )
        flows = materialize_kafka_flow_continuations(flows, all_endpoints)
        service_aliases = {
            module_identity(module): local_spring_application_names(module.path, None)
            for module in relation_modules
        }
        flows = reconcile_code_flows(
            flows,
            all_endpoints,
            build_graph(
                group_endpoints_by_module(all_endpoints),
                strategy1=topic_strategy == "strategy1",
                service_aliases=service_aliases,
            ),
        )
        store.replace_code_flows(flows)
        store.set_meta(
            "code_flow_signature",
            flow_signature,
        )
        _report_progress(
            progress,
            f"→ Indexation : {len(flows)} parcours de code potentiel(s) matérialisé(s).",
        )
        timer.end("flows", "matérialisation des flux de code")

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
    internal_flows_by_module: Counter[str] = Counter()
    for flow in store.all_code_flows():
        involved_modules = {
            endpoint_by_id[step.endpoint_id].module
            for step in flow.steps
            if step.endpoint_id in endpoint_by_id and endpoint_by_id[step.endpoint_id].module
        }
        if not involved_modules or involved_modules == {flow.module}:
            internal_flows_by_module[flow.module] += 1
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
        | set(internal_flows_by_module)
    )
    _report_progress(progress, "→ Indexation : statistiques par module")
    for module_name in module_names:
        _report_progress(
            progress,
            f"  • {module_name} : {in_by_module[module_name]} IN, "
            f"{out_by_module[module_name]} OUT, "
            f"{internal_flows_by_module[module_name]} flux internes",
        )
    _report_progress(
        progress,
        "→ Indexation : statistiques globales : "
        f"{len(input_ports)} IN, {len(output_ports)} OUT, "
        f"{sum(internal_flows_by_module.values())} flux internes, "
        f"{len(module_names)} modules",
    )

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
    codeql_database: Path | None = None,
    call_graph_progress: CallGraphProgressCallback | None = None,
    codeql_progress: bool = False,
    generate_sources: bool = False,
) -> IndexReport:
    """Index one repository and publish its facts as an atomic snapshot."""
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
            codeql_database=codeql_database,
            call_graph_progress=call_graph_progress,
            codeql_progress=codeql_progress,
            generate_sources=generate_sources,
        )
