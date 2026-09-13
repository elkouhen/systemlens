"""Assemble the standalone interactive HTML graph export."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import cast

from systemlens.domain.graph import GraphEdge
from systemlens.domain.code_flows import CodeFlow, IntegrationMethod
from systemlens.domain.models import (
    ArchitectureRelation,
    ExtractionDiagnostic,
    Finding,
    GraphFact,
    MessageEndpoint,
)
from systemlens.domain.module_inventory import DiscoveredModule, ModuleDependency
from systemlens.render.graph_view_model import build_graph_view_model


_ASSET_ROOT = Path(__file__).parent / "assets"
_GRAPH_HTML_TEMPLATE = (_ASSET_ROOT / "graph.html").read_text(encoding="utf-8")
_GRAPH_STYLE_MODULES = tuple(sorted((_ASSET_ROOT / "graph").glob("*.css")))
_GRAPH_CSS = "".join(
    path.read_text(encoding="utf-8") for path in _GRAPH_STYLE_MODULES
)
_GRAPH_JS_MODULES = tuple(sorted((_ASSET_ROOT / "graph").glob("*.js")))
_GRAPH_JS = "\n".join(path.read_text(encoding="utf-8") for path in _GRAPH_JS_MODULES)
_LAYER_GEOMETRY_JS = (_ASSET_ROOT / "layer_geometry.js").read_text(encoding="utf-8")


def render_graph_html(
    endpoints_by_service: dict[str, list[MessageEndpoint]],
    edges: list[GraphEdge],
    collections_by_service: dict[str, list[str]] | None = None,
    modules_by_service: dict[str, DiscoveredModule] | None = None,
    indexing_warnings: list[str] | None = None,
    build_modules: list[DiscoveredModule] | None = None,
    module_dependencies: list[ModuleDependency] | None = None,
    source_roots: list[Path] | None = None,
    findings_by_service: dict[str, list[Finding]] | None = None,
    root_path: Path | None = None,
    request_reply_strategy1: bool = False,
    diagnostics: list[ExtractionDiagnostic] | None = None,
    kafka_dto_definitions: list[dict[str, object]] | None = None,
    openapi_contracts: list[dict[str, object]] | None = None,
    graph_facts: list[GraphFact] | None = None,
    strategy1: bool = False,
    architecture_relations: list[ArchitectureRelation] | None = None,
    code_flows: list[CodeFlow] | None = None,
    integration_methods: list[IntegrationMethod] | None = None,
) -> str:
    """Render a graph view model as one self-contained HTML document."""
    view_model = build_graph_view_model(
        endpoints_by_service=endpoints_by_service,
        edges=edges,
        collections_by_service=collections_by_service,
        modules_by_service=modules_by_service,
        indexing_warnings=indexing_warnings,
        build_modules=build_modules,
        module_dependencies=module_dependencies,
        source_roots=source_roots,
        findings_by_service=findings_by_service,
        root_path=root_path,
        request_reply_strategy1=request_reply_strategy1,
        diagnostics=diagnostics,
        kafka_dto_definitions=kafka_dto_definitions,
        openapi_contracts=openapi_contracts,
        graph_facts=graph_facts,
        strategy1=strategy1,
        architecture_relations=architecture_relations,
        integration_methods=integration_methods,
    )
    serialized_code_flows = [
        {
            "id": flow.id,
            "module": flow.module,
            "method": flow.method,
            "path": flow.path,
            "start_line": flow.start_line,
            "end_line": flow.end_line,
            "status": flow.status,
            "confidence": flow.confidence,
            "reason": flow.reason,
            "steps": [
                {
                    "order": step.order,
                    "kind": step.kind,
                    "name": step.name,
                    "path": step.path,
                    "start_line": step.start_line,
                    "end_line": step.end_line,
                    "endpoint_id": step.endpoint_id,
                    "operation": step.operation,
                }
                for step in flow.steps
            ],
        }
        for flow in (code_flows or [])
    ]
    view_model["code_flows"] = serialized_code_flows
    flow_counts = Counter(flow.module for flow in (code_flows or []))
    nodes = cast(list[dict[str, object]], view_model["nodes"])
    for node in nodes:
        name = node.get("name")
        if node.get("kind") == "microservice" and isinstance(name, str):
            node["internal_flow_count"] = flow_counts[name]
    graph_data = json.dumps(
        view_model,
        ensure_ascii=False,
    ).replace("</", "<\\/")
    return (
        _GRAPH_HTML_TEMPLATE.replace("__GRAPH_CSS__", _GRAPH_CSS)
        .replace("__GRAPH_JS__", _GRAPH_JS)
        .replace("__GRAPH_DATA__", graph_data)
        .replace("__LAYER_GEOMETRY__", _LAYER_GEOMETRY_JS)
    )
