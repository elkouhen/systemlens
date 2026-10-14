// Ordered source module: 06-view-lifecycle.js
// Owns the boundary between the architecture projection and the flow views.
const graphViewLifecycle = (() => {
  const graphSurfaces = () => [
    graphCanvas,
    portPathOverlay,
    nodeLabelOverlay,
    graphCallTreeOverlay,
    flowTooltipOverlay,
  ];

  function clearOverlays() {
    [
      portPathOverlay,
      nodeLabelOverlay,
      graphCallTreeOverlay,
      flowTooltipOverlay,
    ].forEach(element => element?.replaceChildren());
  }

  function setGraphVisibility(visible) {
    graphSurfaces().forEach(element => {
      if (!element) return;
      const surfaceVisible = visible && (
        element !== graphCallTreeOverlay || graphState.viewMode === "call-graph"
      );
      element.hidden = !surfaceVisible;
      // SVG/canvas overlays have author-level display rules. Keep the
      // transition deterministic even when those rules override [hidden].
      element.style.display = surfaceVisible ? "" : "none";
      if (visible && element !== graphCallTreeOverlay) {
        element.classList.remove("is-call-tree-hidden");
      }
    });
    if (!visible && graphFlowStatus) graphFlowStatus.hidden = true;
  }

  function selectPathFlow(flow, rootNodeId, trigger) {
    codeFlowSelection.select(flow?.id || null, {
      rootNodeId: rootNodeId || null,
      trigger: trigger || null,
    });
    graphState.viewMode = flow ? "call-graph" : "architecture";
    if (flow) graphCanvas.dataset.selectedCodeFlow = flow.id;
    else delete graphCanvas.dataset.selectedCodeFlow;
  }

  function clearFlowSelection(viewMode = "architecture") {
    codeFlowSelection.clear();
    graphState.viewMode = viewMode;
    delete graphCanvas.dataset.selectedCodeFlow;
    delete graphCanvas.dataset.flowFocusRatio;
  }

  function resetSelection(viewMode) {
    graphState.selectedId = null;
    graphState.selectedClusterKey = null;
    graphState.relatedNodes = null;
    graphState.relatedEdges = null;
    graphState.dependencyFocusOnly = false;
    graphState.analysisPortEndpointId = null;
    clearFlowSelection(viewMode);
    graphState.selectedDiagnosticId = null;
    graphState.diagnosticFocusSnapshot = null;
    graphState.callGraphDisplayMode = "network";
    graphState.selectedCallGraphEdgeKey = null;
    graphState.pathMicroserviceOrder = new Map();
    graphState.codeFlowTreeCoordinates = new Map();
    graphState.relatedLocalPortLinks = new Set();
    dependencyFocusOnly.checked = false;
    dependencyAnalysisControls.disabled = true;
    dependencyAnalysisHelp.textContent = "Sélectionnez un nœud pour activer cette analyse.";
    dependencyFocusOnly.disabled = true;
    delete graphCanvas.dataset.selectedCallGraphArc;
  }

  function activate(tab, options = {}) {
    const showingGraph = tab === "graph";
    const showingFlows = tab === "flows";
    const showingFlowGraph = showingFlows && options.showFlowGraph === true;
    const graphVisible = !showingFlows || showingFlowGraph;
    const viewMode = showingGraph
      ? "architecture"
      : showingFlowGraph
        ? "call-graph"
        : "empty";

    const resetView = (tab === "graph" || tab === "flows") && !showingFlowGraph;
    if (resetView) resetSelection(viewMode);
    else graphState.viewMode = viewMode;
    if (!graphVisible) clearOverlays();
    setGraphVisibility(graphVisible);
    return { showingGraph, showingFlows, showingFlowGraph, graphVisible, resetView };
  }

  return { activate, clearOverlays, selectPathFlow, clearFlowSelection };
})();
