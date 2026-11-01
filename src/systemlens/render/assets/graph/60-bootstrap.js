// Ordered source module: 60-bootstrap.js
    function reset() {
      closeInspector();
      expandedHttpRoute = null;
      updateGraphState({
        selectedId: null,
        dependencyFocusOnly: false,
        selectedClusterKey: null,
        relatedNodes: null,
        relatedEdges: null,
        analysisPortEndpointId: null,
        selectedDiagnosticId: null,
        diagnosticFocusSnapshot: null,
        selectedCallGraphEdgeKey: null,
        pathMicroserviceOrder: new Map(),
        codeFlowTreeCoordinates: new Map(),
      });
      delete graphCanvas.dataset.selectedCallGraphArc;
      clearPathControls();
      renderer.refresh();
      setDetailsEmpty("Sélectionnez un nœud ou un module pour afficher ses informations.");
      dependencyAnalysisControls.disabled = true;
      dependencyAnalysisHelp.textContent = "Sélectionnez un nœud pour activer cette analyse.";
      dependencyFocusOnly.checked = false;
      dependencyFocusOnly.disabled = true;
      persistState();
    }
    function restoreState() {
    }
    function activeRenderer() {
      return renderer;
    }
    const renderCardsButton = document.getElementById("render-cards");
    const renderSymbolsButton = document.getElementById("render-symbols");
    const graphModeCenter = document.getElementById("graph-mode-center");
    const flowModeCenter = document.getElementById("flow-mode-center");
    const flowTreeCenterSelection = document.getElementById("flow-tree-center-selection");
    const flowModeExpandAll = document.getElementById("flow-mode-expand-all");
    const flowDiagnosticFullView = document.getElementById("flow-diagnostic-full-view");
    const flowTreeDepthDecrease = document.getElementById("flow-call-tree-depth-decrease");
    const flowTreeDepthIncrease = document.getElementById("flow-call-tree-depth-increase");
    const toolbar = document.getElementById("architecture-toolbar");
    const toolbarCollapse = document.getElementById("toolbar-collapse");
    const graphModeContext = document.getElementById("graph-mode-context");
    const flowModeContext = document.getElementById("flow-mode-context");
    // The graph toolbar floats over the top of the graph area on desktop
    // (docs/UX-UI.md, "Camera and graph interaction") and returns to the
    // panel at compact widths, where the panel stacks above the graph.
    const compactGraphToolbar = window.matchMedia("(max-width: 720px)");
    const graphTypeCountsStrip = document.getElementById("graph-type-counts");
    function placeGraphToolbar() {
      if (!toolbar || !graphPanel || !graphModeContext) return;
      const quickSearchBlock = document.getElementById("quick-search");
      const floating = !compactGraphToolbar.matches;
      graphModeContext.classList.toggle("is-floating", floating);
      if (floating) {
        if (graphModeContext.parentElement === toolbar) toolbar.after(graphModeContext);
        const searchAnchor = flowModeContext?.parentElement === toolbar ? flowModeContext : graphPanel;
        if (quickSearchBlock) toolbar.insertBefore(quickSearchBlock, searchAnchor);
        if (graphTypeCountsStrip && graphTypeCountsStrip.parentElement !== graphModeContext) {
          graphModeContext.append(graphTypeCountsStrip);
        }
      } else {
        toolbar.insertBefore(graphModeContext, graphPanel);
        if (quickSearchBlock) toolbar.insertBefore(quickSearchBlock, graphModeContext);
        if (graphTypeCountsStrip && graphTypeCountsStrip.parentElement === graphModeContext) {
          graphModeContext.after(graphTypeCountsStrip);
        }
      }
    }
    // The call-tree context floats over the tree on desktop (U5); its access
    // key docks at the bottom left of the canvas.
    const flowAccessKeyDock = document.getElementById("flow-access-key-dock");
    const flowAccessKey = document.getElementById("flow-mode-context-key");
    const flowModeCopy = document.getElementById("flow-mode-context-copy");
    const FLOW_KEY_STORAGE = "systemlens.flowAccessKeyOpen";
    try {
      if (flowAccessKeyDock && window.localStorage.getItem(FLOW_KEY_STORAGE) === "false") flowAccessKeyDock.open = false;
    } catch (_error) { /* browser storage is optional */ }
    flowAccessKeyDock?.addEventListener("toggle", () => {
      try { window.localStorage.setItem(FLOW_KEY_STORAGE, String(flowAccessKeyDock.open)); } catch (_error) { /* optional */ }
    });
    function placeFlowContext() {
      if (!toolbar || !graphPanel || !flowModeContext) return;
      const floating = !compactGraphToolbar.matches;
      flowModeContext.classList.toggle("is-floating", floating);
      if (floating) {
        if (flowModeContext.parentElement === toolbar) toolbar.after(flowModeContext);
        if (flowAccessKey && flowAccessKeyDock && flowAccessKey.parentElement !== flowAccessKeyDock) flowAccessKeyDock.append(flowAccessKey);
      } else {
        toolbar.insertBefore(flowModeContext, graphPanel);
        if (flowAccessKey && flowModeCopy && flowAccessKey.parentElement !== flowModeCopy) flowModeCopy.append(flowAccessKey);
      }
    }
    if (toolbar && graphPanel) {
      if (flowModeContext) toolbar.insertBefore(flowModeContext, graphPanel);
      placeGraphToolbar();
      placeFlowContext();
    }
    const graphFilterSummary = document.getElementById("graph-filter-summary");
    const graphFilterControls = [
      relationHttp, relationKafka, relationMongodb, relationOther,
      nodeMicroservice, nodeExternalMicroservice, nodeKafkaTopic,
      nodeMongodbCollection, nodeOther,
    ];
    function updateGraphFilterSummary() {
      if (!graphFilterSummary) return;
      const hiddenCategories = graphFilterControls.filter(control => !control.checked).length;
      const hiddenResources = hiddenMicroservices.size + hiddenTopics.size;
      const parts = [];
      if (hiddenCategories) parts.push(`${hiddenCategories} catégorie${hiddenCategories > 1 ? "s" : ""}`);
      if (hiddenResources) parts.push(`${hiddenResources} ressource${hiddenResources > 1 ? "s" : ""}`);
      graphFilterSummary.textContent = parts.length ? `${parts.join(" · ")} masquée${hiddenCategories + hiddenResources > 1 ? "s" : ""}` : "Tous affichés";
    }
    // Index status line and per-type counts (docs/UX-UI.md, "Workspace shell").
    const indexStatusMeta = document.getElementById("index-status-meta");
    const graphTypeCounts = document.getElementById("graph-type-counts");
    function formatIndexedAt(value) {
      const parsed = new Date(`${String(value).replace(" ", "T")}Z`);
      if (Number.isNaN(parsed.getTime())) return null;
      return parsed.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
    }
    function updateIndexStatusMeta() {
      if (!indexStatusMeta) return;
      const relations = (graphData.links || []).length;
      const parts = [`${relations} relation${relations > 1 ? "s" : ""} indexée${relations > 1 ? "s" : ""}`];
      const date = graphData.indexed_at ? formatIndexedAt(graphData.indexed_at) : null;
      if (date) parts.push(`indexé le ${date}`);
      indexStatusMeta.textContent = parts.join(" · ");
    }
    const typeCountCategories = [
      { key: "microservices", control: nodeMicroservice, color: "#ef4444", singular: "microservice", plural: "microservices",
        matches: node => node.kind === "microservice" && !node.external, hidden: node => hiddenMicroservices.has(node.name) },
      { key: "external", control: nodeExternalMicroservice, color: "#94a3b8", singular: "service externe", plural: "services externes",
        matches: node => node.kind === "microservice" && node.external, hidden: node => hiddenMicroservices.has(node.name) },
      { key: "messages", control: nodeKafkaTopic, color: "#f59e0b", singular: "topic", plural: "topics",
        matches: node => ["kafka_topic", "message_channel"].includes(node.kind), hidden: node => hiddenTopics.has(node.name) },
      { key: "data", control: nodeMongodbCollection, color: "#3b82f6", singular: "collection", plural: "collections",
        matches: node => ["mongodb_collection", "data_schema"].includes(node.kind), hidden: () => false },
      { key: "other", control: nodeOther, color: "#8b5cf6", singular: "autre ressource", plural: "autres ressources",
        matches: node => !["microservice", "kafka_topic", "message_channel", "mongodb_collection", "data_schema"].includes(node.kind), hidden: () => false },
    ];
    function renderGraphTypeCounts() {
      if (!graphTypeCounts) return;
      const chips = typeCountCategories.map(category => {
        const count = graphData.nodes.filter(node => category.matches(node) && !category.hidden(node)).length;
        if (!count) return null;
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "graph-type-chip";
        chip.dataset.typeKey = category.key;
        chip.style.setProperty("--chip-color", category.color);
        chip.setAttribute("aria-pressed", String(category.control.checked));
        chip.textContent = `${count} ${count > 1 ? category.plural : category.singular}`;
        chip.title = category.control.checked ? "Masquer ce type du graphe" : "Réafficher ce type dans le graphe";
        chip.addEventListener("click", () => category.control.click());
        return chip;
      }).filter(Boolean);
      graphTypeCounts.replaceChildren(...chips);
      graphTypeCounts.hidden = chips.length === 0;
    }
    function refreshGraphSummaries() {
      updateIndexStatusMeta();
      renderGraphTypeCounts();
    }
    graphFilterControls.forEach(control => control.addEventListener("change", refreshGraphSummaries));
    window.addEventListener("systemlens:filters-changed", refreshGraphSummaries);
    window.addEventListener("systemlens:graph-rebuilt", refreshGraphSummaries);
    refreshGraphSummaries();
    graphFilterControls.forEach(control => control.addEventListener("change", updateGraphFilterSummary));
    window.addEventListener("systemlens:filters-changed", updateGraphFilterSummary);
    updateGraphFilterSummary();
    graphCanvas.dataset.renderMode = graphState.renderMode;
    // At 900 px or less the panel starts collapsed and reopens as a drawer
    // over the graph, which then keeps its full width (backlog U8).
    const toolbarDrawerQuery = window.matchMedia("(max-width: 900px)");
    function setToolbarCollapsed(collapsed, refit = true) {
      toolbar?.classList.toggle("is-collapsed", collapsed);
      document.body.classList.toggle("toolbar-drawer-open", !collapsed && toolbarDrawerQuery.matches);
      toolbarCollapse?.setAttribute("aria-expanded", String(!collapsed));
      toolbarCollapse?.setAttribute("aria-label", collapsed ? "Développer le panneau" : "Réduire le panneau");
      toolbarCollapse?.setAttribute("title", collapsed ? "Développer le panneau" : "Réduire le panneau");
      if (toolbarCollapse) toolbarCollapse.textContent = collapsed ? "›" : "‹";
      if (typeof updateWorkspaceViewport === "function" && workspaceViewportReady) updateWorkspaceViewport(refit);
    }
    let workspaceViewportReady = false;
    toolbarCollapse?.addEventListener("click", () => {
      setToolbarCollapsed(!toolbar?.classList.contains("is-collapsed"));
    });
    function syncToolbarDrawer() {
      if (document.body.classList.contains("catalogue-active")) return;
      setToolbarCollapsed(toolbarDrawerQuery.matches);
    }
    toolbarDrawerQuery.addEventListener?.("change", syncToolbarDrawer);
    if (toolbarDrawerQuery.matches) setToolbarCollapsed(true, false);
    const centerFlow = () => {
      if (!graphState.relatedNodes?.size) return;
      const orderedNodes = [...(graphState.pathMicroserviceOrder?.keys() || [])];
      const remainingNodes = [...graphState.relatedNodes].filter(id => !orderedNodes.includes(id));
      scheduleFlowCameraFit({ nodes: [...orderedNodes, ...remainingNodes] });
    };
    graphModeCenter?.addEventListener("click", () => fitCameraToVisibleGraph(renderer, "readable"));
    flowModeCenter?.addEventListener("click", () => {
      if (!fitCallTree()) centerFlow();
    });
    flowTreeCenterSelection?.addEventListener("click", () => centerSelectedCallTreeOccurrence());
    flowModeExpandAll?.addEventListener("click", () => {
      expandAllCallTree();
    });
    flowDiagnosticFullView?.addEventListener("click", () => {
      restoreDiagnosticFullView();
    });
    flowTreeDepthDecrease?.addEventListener("click", () => {
      adjustCallTreeDepth(-1);
    });
    flowTreeDepthIncrease?.addEventListener("click", () => {
      adjustCallTreeDepth(1);
    });
    document.getElementById("flow-context-toggle")?.addEventListener("click", () => {
      graphState.analysisContextCollapsed = !graphState.analysisContextCollapsed;
      syncFlowContextCollapse();
    });
    syncFlowContextCollapse();
    const graphFitOverview = document.getElementById("graph-fit-overview");
    const graphFitReadable = document.getElementById("graph-fit-readable");
    const graphLegendToggle = document.getElementById("graph-legend-toggle");
    graphFitOverview?.addEventListener("click", () => fitCameraToVisibleGraph(renderer, "overview"));
    graphFitReadable?.addEventListener("click", () => fitCameraToVisibleGraph(renderer, "readable"));
    const graphLegendElement = document.getElementById("graph-legend");
    function syncLegendToggle() {
      graphLegendToggle?.setAttribute("aria-pressed", String(Boolean(graphLegendElement?.open)));
    }
    graphLegendToggle?.addEventListener("click", () => {
      if (!graphLegendElement) return;
      graphLegendElement.open = !graphLegendElement.open;
      syncLegendToggle();
    });
    graphLegendElement?.addEventListener("toggle", syncLegendToggle);
    syncLegendToggle();
    function updateFitModeControls(mode) {
      [
        [graphFitOverview, "overview"],
        [graphFitReadable, "readable"],
      ].forEach(([button, buttonMode]) => {
        if (!button) return;
        const active = mode === buttonMode;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-pressed", String(active));
      });
    }
    async function setNodeRenderMode(mode) {
      const nextMode = mode === "symbols" ? "symbols" : "cards";
      updateGraphState({ renderMode: nextMode });
      [
        [renderCardsButton, "cards"],
        [renderSymbolsButton, "symbols"],
      ].forEach(([button, buttonMode]) => {
        const active = nextMode === buttonMode;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-pressed", String(active));
      });
      graphCanvas.dataset.renderModePending = nextMode;
      renderer.refresh();
      await requestGraphRender();
      if (graphState.renderMode !== nextMode) return;
      graphCanvas.dataset.renderMode = nextMode;
      delete graphCanvas.dataset.renderModePending;
    }
    async function fitCameraToVisibleGraph(targetRenderer = renderer, mode = graphState.fitMode) {
      if (!targetRenderer) return;
      const fitRequest = ++graphState.fitRequest;
      const nextMode = mode === "overview" ? "overview" : "readable";
      updateGraphState({ fitMode: nextMode });
      updateFitModeControls(nextMode);
      targetRenderer.refresh();
      // Sigma's normalized overview is the default camera state. Apply it
      // synchronously so repeated or rapid fit actions cannot interleave two
      // zero-duration animations and compound the readable zoom.
      const camera = targetRenderer.getCamera();
      camera.setState({ x: .5, y: .5, ratio: 1, angle: 0 });
      targetRenderer.refresh();
      if (fitRequest !== graphState.fitRequest) return;
      if (nextMode === "readable") {
        const state = camera.getState();
        // Start from the complete overview, then move closer. Architecture
        // cards use the measured projected spacing.
        if (targetRenderer === renderer && network.order <= 12) {
          camera.setState({ ...state, ratio: requiredSmallGraphOverviewRatio(targetRenderer) });
        } else {
          camera.setState({ ...state, ratio: Math.max(.01, state.ratio / Math.max(1.6, requiredCardZoomIn(targetRenderer))) });
        }
      }
      targetRenderer.refresh();
      if (targetRenderer === renderer) {
        const layoutRequest = graphState.layoutRequest;
        // Wait for the exact coalesced overlay frame. Waiting for an unrelated
        // animation frame can mark the fit complete while fixed-size cards
        // still use the previous camera state.
        await requestGraphRender();
        if (fitRequest !== graphState.fitRequest || layoutRequest !== graphState.layoutRequest) return;
      }
      graphCanvas.dataset.fitMode = nextMode;
      graphCanvas.dataset.fitRatio = String(targetRenderer.getCamera().getState().ratio);
    }
    const zoomIn = () => {
      if (zoomCallTree(1.25)) return;
      const renderer = activeRenderer();
      const camera = renderer.getCamera();
      const state = camera.getState();
      camera.setState({ ...state, ratio: Math.max(.01, state.ratio * .8) });
      requestGraphRender();
    };
    const zoomOut = () => {
      if (zoomCallTree(0.8)) return;
      const renderer = activeRenderer();
      const camera = renderer.getCamera();
      const state = camera.getState();
      camera.setState({ ...state, ratio: Math.min(100, state.ratio * 1.25) });
      requestGraphRender();
    };
    ["graph-zoom-in", "flow-zoom-in"].forEach(id => document.getElementById(id)?.addEventListener("click", zoomIn));
    ["graph-zoom-out", "flow-zoom-out"].forEach(id => document.getElementById(id)?.addEventListener("click", zoomOut));
    renderCardsButton.addEventListener("click", () => setNodeRenderMode("cards"));
    renderSymbolsButton.addEventListener("click", () => setNodeRenderMode("symbols"));
    resetButton.addEventListener("click", reset);
    dependencyDepth.addEventListener("change", () => {
      graphState.dependencyDepth = Number(dependencyDepth.value) || 1;
      if (!graphState.selectedId || codeFlowSelection.flowId) return;
      dependencyFocusOnly.checked = true;
      graphState.dependencyFocusOnly = true;
      updateSelectedNodeDependencyScope(graphState.selectedId);
      rebuildGraph();
      applyLayout(graphState.activeLayout);
    });
    dependencyFocusOnly.addEventListener("change", () => {
      if (!graphState.selectedId || codeFlowSelection.flowId) {
        dependencyFocusOnly.checked = false;
        return;
      }
      graphState.dependencyFocusOnly = dependencyFocusOnly.checked;
      rebuildGraph();
      applyLayout(graphState.activeLayout);
    });
    document.getElementById("inspector-close").addEventListener("click", event => {
      event.preventDefault();
      event.stopImmediatePropagation();
      closeInspector();
    });
    inspectorBack.addEventListener("click", goBackInspector);
    inspectorModal.addEventListener("pointerdown", event => event.stopPropagation());
    inspectorModal.addEventListener("click", event => {
      event.stopPropagation();
      if (event.target === inspectorModal) closeInspector();
    });
    window.addEventListener("keydown", event => { if (event.key === "Escape" && !inspectorModal.hidden) closeInspector(); });
    layoutButtons.forEach((button, layout) => button.addEventListener("click", () => applyLayout(layout)));
    graphTab.addEventListener("click", () => setToolbarTab("graph"));
    microservicesTab.addEventListener("click", () => setToolbarTab("microservices"));
    kafkaTab.addEventListener("click", () => openResourceCatalogue("kafka_topic"));
    collectionsTab.addEventListener("click", () => openResourceCatalogue("mongodb_collection"));
    persistenceTab.addEventListener("click", () => openResourceCatalogue("mongodb_collection"));
    routesTab.addEventListener("click", () => openResourceCatalogue("http_route"));
    contractsTab.addEventListener("click", () => openResourceCatalogue("openapi_contract"));
    asyncApiTab.addEventListener("click", () => openResourceCatalogue("asyncapi_contract"));
    dtoContractTab.addEventListener("click", () => openResourceCatalogue("dto"));
    jpaTab.addEventListener("click", () => openResourceCatalogue("jpa_entity"));
    issuesTab.addEventListener("click", () => setToolbarTab("issues"));
    flowsTab.addEventListener("click", () => setToolbarTab("flows"));
    modeTabs.architecture.addEventListener("click", () => setToolbarTab("graph"));
    modeTabs.flows.addEventListener("click", () => setToolbarTab("flows"));
    modeTabs.contracts.addEventListener("click", () => openResourceCatalogue("all"));
    modeTabs.diagnostics.addEventListener("click", () => setToolbarTab("issues"));
    inventoryStatus.addEventListener("click", () => openResourceCatalogue("diagnostic"));
    [
      relationHttp,
      relationKafka,
      relationMongodb,
      relationOther,
      nodeMicroservice,
      nodeExternalMicroservice,
      nodeKafkaTopic,
      nodeMongodbCollection,
      nodeOther,
    ].forEach(control => control.addEventListener("click", () => {
      // Reflect the checkbox state synchronously. Rebuilding Sigma and
      // applying the active layout are intentionally asynchronous, while the
      // data contract is also consumed by compact-viewport integrations.
      const visibleRelationCount = graphData.links.filter(link => (
        isVisibleRelation(link)
        && isVisibleNode(nodeDataById.get(link.source))
        && isVisibleNode(nodeDataById.get(link.target))
      )).length;
      document.getElementById("graph").dataset.relationCount = String(visibleRelationCount);
      if ([relationHttp, relationKafka, relationMongodb, relationOther].includes(control)) {
        reset();
        rebuildGraph();
        applyLayout(graphState.activeLayout);
        return;
      }
      reset();
      rebuildGraph();
      applyLayout(graphState.activeLayout);
    }));
    openApiReferencesFilter.addEventListener("input", renderReferences);
    routesFilter.addEventListener("input", renderReferences);
    asyncApiPanelFilter.addEventListener("input", renderReferences);
    topicsFilter.addEventListener("input", renderTopics);
    dtoContractReferencesFilter.addEventListener("input", () => {
      renderReferences();
    });
    jpaReferencesFilter.addEventListener("input", renderReferences);
    mongoClassReferencesFilter.addEventListener("input", renderReferences);
    collectionsFilter.addEventListener("input", renderCollections);
    renderIndexingIssues();
    renderReferences();
    renderResourceCatalogue();
    renderTopics();
    renderCollections();
    restoreState();
    dependencyAnalysisControls.disabled = true;
    dependencyAnalysisHelp.textContent = "Sélectionnez un nœud pour activer cette analyse.";
    dependencyFocusOnly.disabled = true;
    // The first projection starts after every widget has registered its handlers.
    // The viewport is established below before the initial layout runs.
    setToolbarTab("graph", { deferLayout: true });
    // An open legend reserves its footprint so a fit never places a card
    // under it: a right column on wide screens, or a bottom band when that
    // column would leave the graph too narrow. Compact viewports keep the
    // full workspace and stack the legend below the panel instead.
    const workspaceLegend = document.getElementById("graph-legend");
    const MIN_GRAPH_WIDTH_BESIDE_LEGEND = 560;
    function legendReservation(reservedLeft = 0) {
      const none = { right: 0, bottom: 0 };
      if (!workspaceLegend || workspaceLegend.hidden || !workspaceLegend.open) return none;
      if (window.innerWidth <= 720) return none;
      const bounds = workspaceLegend.getBoundingClientRect();
      if (!bounds.width) return none;
      const right = Math.round(window.innerWidth - bounds.left + 12);
      if (window.innerWidth - reservedLeft - right >= MIN_GRAPH_WIDTH_BESIDE_LEGEND) return { right, bottom: 0 };
      return { right: 0, bottom: Math.round(window.innerHeight - bounds.top + 12) };
    }
    let lastLegendReservation = "0:0";
    if (workspaceLegend) {
      new MutationObserver(() => {
        const left = Number.parseFloat(document.documentElement.style.getPropertyValue("--workspace-left")) || 0;
        const next = legendReservation(left);
        const key = `${next.right}:${next.bottom}`;
        if (key === lastLegendReservation) return;
        lastLegendReservation = key;
        updateWorkspaceViewport(!workspaceLegend.hidden);
      }).observe(workspaceLegend, { attributes: true, attributeFilter: ["open", "hidden"] });
    }
    function updateWorkspaceViewport(refit = false) {
      const root = document.documentElement;
      const toolbarBounds = toolbar?.getBoundingClientRect();
      const compactViewport = window.innerWidth <= 720;
      const reservedLeft = toolbar?.classList.contains("is-collapsed") || compactViewport
        || toolbarDrawerQuery.matches
        ? 0
        : Math.min((toolbarBounds?.width || 0) + (toolbarBounds?.left || 0) + 24, window.innerWidth * .48);
      root.style.setProperty("--workspace-left", `${Math.round(reservedLeft)}px`);
      // Floating graph widgets start right of a collapsed panel header.
      const collapsedPanelRight = toolbar?.classList.contains("is-collapsed") && !compactViewport
        ? Math.round((toolbarBounds?.right || 0) - 4)
        : Math.round(reservedLeft);
      root.style.setProperty("--floating-left", `${collapsedPanelRight}px`);
      const legendSpace = legendReservation(reservedLeft);
      // A docked inspector owns the right edge: cards and fits stay beside it.
      const inspectorDock = Number.parseFloat(root.style.getPropertyValue("--inspector-dock-width")) || 0;
      const reservedRight = Math.max(legendSpace.right, inspectorDock ? inspectorDock + 12 : 0);
      root.style.setProperty("--workspace-right", `${reservedRight}px`);
      // Reserve the band under the floating graph toolbar so fits and cards
      // never sit below it.
      const floatingContext = [graphModeContext, flowModeContext].find(element => (
        element?.classList.contains("is-floating") && !element.hidden
        && getComputedStyle(element).display !== "none"
      ));
      const floatingToolbarBounds = floatingContext ? floatingContext.getBoundingClientRect() : null;
      const reservedTop = floatingToolbarBounds && floatingToolbarBounds.height
        ? Math.round(floatingToolbarBounds.bottom + 8)
        : 0;
      root.style.setProperty("--workspace-top", `${reservedTop}px`);
      // The interaction hint sits at the bottom of the graph; keep cards above it.
      const interactionHint = document.getElementById("graph-interaction-hint");
      const hintBounds = interactionHint && getComputedStyle(interactionHint).display !== "none"
        ? interactionHint.getBoundingClientRect()
        : null;
      const hintBand = hintBounds && hintBounds.height
        ? Math.round(window.innerHeight - hintBounds.top + 8)
        : 0;
      const keyDock = document.getElementById("flow-access-key-dock");
      const keyBounds = keyDock && !keyDock.hidden ? keyDock.getBoundingClientRect() : null;
      const keyBand = keyBounds && keyBounds.height
        ? Math.round(window.innerHeight - keyBounds.top + 8)
        : 0;
      root.style.setProperty("--workspace-bottom", `${Math.max(legendSpace.bottom, hintBand, keyBand)}px`);
      // Sigma rejects a zero-size container: only redraw while a graph
      // surface is on screen (the Flux list and the catalogue hide it).
      const graphOnScreen = graphCanvas.getBoundingClientRect().width > 0
        || !graphCallTreeOverlay.hidden;
      if (!graphOnScreen) return;
      renderer?.refresh();
      requestGraphRender();
      if (refit) {
        if (codeFlowSelection.flowId && graphState.relatedNodes?.size) {
          centerCameraOnPath({ nodes: [...graphState.relatedNodes] });
        } else {
          fitCameraToVisibleGraph(activeRenderer());
        }
      }
    }
    updateWorkspaceViewport(false);
    workspaceViewportReady = true;
    window.addEventListener("systemlens:inspector-layout-changed", () => updateWorkspaceViewport(false));
    const reservedBands = () => ["--workspace-top", "--workspace-bottom"]
      .map(name => document.documentElement.style.getPropertyValue(name)).join("|");
    let lastReservedBands = reservedBands();
    function refreshToolbarReservation() {
      updateWorkspaceViewport(false);
      const next = reservedBands();
      if (next !== lastReservedBands) {
        lastReservedBands = next;
        if (codeFlowSelection.flowId && !graphCallTreeOverlay.hidden) {
          requestAnimationFrame(() => fitCallTree());
        } else if (!codeFlowSelection.flowId && graphCanvas.getBoundingClientRect().width > 0) {
          fitCameraToVisibleGraph(activeRenderer());
        }
      }
    }
    if ("ResizeObserver" in window) {
      const chromeObserver = new ResizeObserver(() => refreshToolbarReservation());
      [graphModeContext, flowModeContext, flowAccessKeyDock].forEach(element => element && chromeObserver.observe(element));
    }
    document.addEventListener("systemlens:view-changed", () => requestAnimationFrame(refreshToolbarReservation));
    document.addEventListener("systemlens:call-tree-chrome-changed", () => requestAnimationFrame(refreshToolbarReservation));
    compactGraphToolbar.addEventListener?.("change", () => {
      placeGraphToolbar();
      placeFlowContext();
      refreshToolbarReservation();
    });
    function runExploreSearch() {
      const query = search.value.trim();
      searchStatus.textContent = "";
      if (!query) { reset(); return; }
      if (query.includes("->")) {
        showShortestPath(query, true);
        return;
      }
      const resolved = resolveExactNodeName(query);
      if (resolved.error) { searchStatus.textContent = resolved.error; return; }
      selectNode(resolved.id);
    }
    search.addEventListener("input", () => {
      const query = search.value.trim();
      searchStatus.textContent = "";
      if (!query) return;
      // Keep the field usable while composing an itinerary. An exact service
      // name is selected only after Enter, otherwise typing `service-a ->`
      // immediately opens the service details and disrupts the next step.
    });
    search.addEventListener("keydown", event => {
      if (event.key === "Enter") { event.preventDefault(); runExploreSearch(); }
    });
    window.addEventListener("resize", () => updateWorkspaceViewport(true));
    setupRuntimeOverlay();
    if (graphData.call_graphs_only) {
      setToolbarTab("flows");
      codeFlowsList.querySelector(".code-flow-item:not(.is-unavailable)")?.click();
    } else {
      applyLayout("forceatlas2-noverlap");
    }
