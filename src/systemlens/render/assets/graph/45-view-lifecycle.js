// Ordered source module: 45-view-lifecycle.js
// Owns the boundary between the architecture projection and the flow views.
const graphViewLifecycle = (() => {
  const graphSurfaces = () => [
    graphCanvas,
    graphLayersOverlay,
    graphGroupsOverlay,
    portPathOverlay,
    nodeLabelOverlay,
    graphCallTreeOverlay,
    flowTooltipOverlay,
  ];

  function clearOverlays() {
    [
      graphLayersOverlay,
      graphGroupsOverlay,
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

  function resetSelection(viewMode) {
    graphState.selectedId = null;
    graphState.selectedClusterKey = null;
    graphState.relatedNodes = null;
    graphState.relatedEdges = null;
    graphState.dependencyFocusOnly = false;
    graphState.analysisPortEndpointId = null;
    graphState.selectedCodeFlowId = null;
    graphState.callGraphDisplayMode = "network";
    graphState.selectedCallGraphEdgeKey = null;
    graphState.pathMicroserviceOrder = new Map();
    graphState.codeFlowTreeCoordinates = new Map();
    graphState.relatedLocalPortLinks = new Set();
    graphState.codeFlowRootNodeId = null;
    graphState.codeFlowTrigger = null;
    graphState.viewMode = viewMode;
    dependencyFocusOnly.checked = false;
    dependencyAnalysisControls.disabled = true;
    dependencyAnalysisHelp.textContent = "Sélectionnez un nœud pour activer cette analyse.";
    dependencyFocusOnly.disabled = true;
    delete graphCanvas.dataset.selectedCodeFlow;
    delete graphCanvas.dataset.selectedCallGraphArc;
    delete graphCanvas.dataset.flowFocusRatio;
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

    graphState.viewMode = viewMode;
    if (!graphVisible) clearOverlays();
    setGraphVisibility(graphVisible);
    const resetView = (tab === "graph" || tab === "flows") && !showingFlowGraph;
    if (resetView) resetSelection(viewMode);
    return { showingGraph, showingFlows, showingFlowGraph, graphVisible, resetView };
  }

  return { activate, clearOverlays };
})();
