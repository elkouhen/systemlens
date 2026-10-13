"""Rendering layer: turns indexed facts into CLI text/JSON and HTML exports.

This package is split by rendering concern (see each submodule's docstring).
This ``__init__`` is the public rendering surface used by other modules
(``cli``, ``indexer``, and tests); implementation modules should not be imported
directly across package boundaries.
"""

from systemlens.render.search import (
    FindingHit,
    FindingsSummary,
    RuleCount,
    render_search_json,
    render_search_text,
    render_summary_json,
    render_summary_text,
)
from systemlens.render.graph_json import (
    GraphEdgeInfo,
    GraphNodeInfo,
    GraphResult,
    GraphSite,
    OutboundCallHit,
    render_graph_json,
    render_graph_text,
)
from systemlens.render.html_export import (
    GraphExportInput,
    render_graph_html,
    render_graph_html_input,
)
from systemlens.render.module_graph import (
    EndpointHit,
    FlowResultInfo,
    FlowSiteInfo,
    ModuleDetail,
    ModuleGraphDependency,
    ModuleGraphResult,
    ModuleSummary,
    WorkspaceResult,
    WorkspaceServiceInfo,
    render_endpoints_json,
    render_endpoints_text,
    render_flow_json,
    render_flow_text,
    render_module_detail_json,
    render_module_detail_text,
    render_module_graph_html,
    render_module_graph_json,
    render_module_graph_text,
    render_modules_list_json,
    render_modules_list_text,
    render_workspace_json,
    render_workspace_text,
)
from systemlens.render.software_layers import render_software_layers_html, software_layer
from systemlens.render.namespaces import project_namespace, project_namespace_path, render_namespaces_html
# Kept importable for the test suite, which exercises this internal helper directly.
from systemlens.render._graph_view_helpers import _vscode_file_uri

__all__ = [
    "FindingHit",
    "FindingsSummary",
    "RuleCount",
    "render_search_json",
    "render_search_text",
    "render_summary_json",
    "render_summary_text",
    "GraphEdgeInfo",
    "GraphNodeInfo",
    "GraphResult",
    "GraphSite",
    "OutboundCallHit",
    "render_graph_json",
    "render_graph_text",
    "render_graph_html",
    "GraphExportInput",
    "render_graph_html_input",
    "EndpointHit",
    "FlowResultInfo",
    "FlowSiteInfo",
    "ModuleDetail",
    "ModuleGraphDependency",
    "ModuleGraphResult",
    "ModuleSummary",
    "WorkspaceResult",
    "WorkspaceServiceInfo",
    "render_endpoints_json",
    "render_endpoints_text",
    "render_flow_json",
    "render_flow_text",
    "render_module_detail_json",
    "render_module_detail_text",
    "render_module_graph_html",
    "render_module_graph_json",
    "render_module_graph_text",
    "render_modules_list_json",
    "render_modules_list_text",
    "render_workspace_json",
    "render_workspace_text",
    "render_software_layers_html",
    "render_namespaces_html",
    "project_namespace",
    "project_namespace_path",
    "software_layer",
    "_vscode_file_uri",
]
