"""Call-graph delivery helpers: progress exports and direct engine queries.

These helpers keep the CodeQL-specific delivery paths (the provisional
progress graph written during a long join and the direct queries against an
existing CodeQL database) out of the Typer command module. They reach the
engine only through the ``callgraph`` registry so the delivery layer never
imports ``systemlens.engines``.
"""

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from systemlens.application.architecture_inventory import AnalysisProfile, ArchitectureInventory
from systemlens.application.architecture_projection import project_architecture_graph
from systemlens.callgraph.engine import CallGraphSession, EngineRequest
from systemlens.callgraph.registry import load_engine
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.domain.graph import group_endpoints_by_module
from systemlens.domain.module_inventory import module_identity
from systemlens.indexing.call_graph_stage import CallGraphProgress
from systemlens.render import GraphExportInput, render_graph_html


def render_graph_html_input(export_input: GraphExportInput) -> str:
    """Keep the CLI renderer seam patchable while using one typed input."""
    options = dict(export_input.options)
    positional = [
        options.pop("collections_by_service", None),
        options.pop("modules_by_service", None),
        options.pop("indexing_warnings", None),
        options.pop("build_modules", None),
        options.pop("module_dependencies", None),
        options.pop("source_roots", None),
        options.pop("findings_by_service", None),
        options.pop("root_path", None),
    ]
    return render_graph_html(
        export_input.endpoints_by_service,
        export_input.edges,
        *positional,
        **options,
    )


def write_call_graph_progress_html(
    repo_root: Path, destination: Path, checkpoint: CallGraphProgress
) -> None:
    """Write one visible, non-authoritative CodeQL progress graph atomically."""
    endpoints_by_module = group_endpoints_by_module(checkpoint.endpoints)
    modules_by_service = {
        module_identity(module): module
        for module in checkpoint.modules
    }
    endpoints_by_service = dict(endpoints_by_module)
    for module in checkpoint.modules:
        if module.starts_application:
            endpoints_by_service.setdefault(module_identity(module), [])
    inventory = ArchitectureInventory(
        endpoints_by_service=endpoints_by_service,
        endpoints_by_module=endpoints_by_module,
        findings_by_service={},
        endpoints=checkpoint.endpoints,
        findings=[],
        modules=checkpoint.modules,
        modules_by_service=modules_by_service,
        module_dependencies=[],
        relations=checkpoint.relations,
        diagnostics=[],
        warnings=[],
        source_roots=[repo_root],
        profile=AnalysisProfile(),
        code_flows=checkpoint.code_flows,
        integration_methods=checkpoint.integration_methods,
    )
    projection = project_architecture_graph(inventory, include_module_details=True)
    def format_methods(methods: list[IntegrationMethod]) -> str:
        return ", ".join(method.qualified_method for method in methods) or "aucun"
    if checkpoint.phase == "join":
        warning = (
            f"INDEXATION {checkpoint.engine.upper()} EN COURS — "
            f"jointure CodeQL : {checkpoint.completed_units}/{checkpoint.total_units} "
            "méthode(s) IN traitée(s). Ce graphe est provisoire et incomplet."
        )
    else:
        warning = (
            f"INDEXATION {checkpoint.engine.upper()} EN COURS — "
            f"{checkpoint.completed_projects}/{checkpoint.total_projects} projet(s) terminé(s) "
            f"(module Maven : {checkpoint.project_name}). "
            f"Méthodes Java cherchées : IN [{format_methods(checkpoint.project_input_methods)}] · "
            f"OUT [{format_methods(checkpoint.project_output_methods)}]. "
            "Ce graphe est provisoire et incomplet."
        )
    html = render_graph_html_input(GraphExportInput(
        projection.services_by_name,
        projection.edges,
        {
            "collections_by_service": projection.collections_by_service,
            "modules_by_service": projection.modules_by_service,
            "indexing_warnings": [warning],
            "build_modules": checkpoint.modules,
            "source_roots": [repo_root],
            "root_path": repo_root,
            "architecture_relations": checkpoint.relations,
            "integration_methods": checkpoint.integration_methods,
            "code_flows": checkpoint.code_flows,
            "progress_notice": warning,
        },
    ))
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(html, encoding="utf-8")
    temporary.replace(destination)


_QueryResult = TypeVar("_QueryResult")


def database_session_query(
    repo_root: Path, database: Path, query: Callable[[CallGraphSession], _QueryResult],
) -> _QueryResult:
    """Run one query against an existing CodeQL database through its engine."""
    engine = load_engine("codeql", database=database)
    assert engine is not None
    with engine.open(EngineRequest(repo_root=repo_root, allowed_paths=(), timeout_seconds=600)) as session:
        return query(session)
