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
        selectedCodeFlowId: null,
        viewMode: "architecture",
        selectedCallGraphEdgeKey: null,
        pathMicroserviceOrder: new Map(),
        codeFlowTreeCoordinates: new Map(),
      });
      delete graphCanvas.dataset.selectedCodeFlow;
      delete graphCanvas.dataset.selectedCallGraphArc;
      delete graphCanvas.dataset.flowFocusRatio;
      renderer.refresh();
      setDetailsEmpty("Sélectionnez un nœud ou un module pour afficher ses informations.");
      search.value = "";
      dependencyAnalysisControls.disabled = true;
      dependencyAnalysisHelp.textContent = "Sélectionnez un nœud pour activer cette analyse.";
      dependencyFocusOnly.checked = false;
      dependencyFocusOnly.disabled = true;
      clearPathControls();
      persistState();
    }
    function restoreState() {
    }
    function activeRenderer() {
      return renderer;
    }
    const renderCardsButton = document.getElementById("render-cards");
    const renderSymbolsButton = document.getElementById("render-symbols");
    const analysisModeClear = document.getElementById("analysis-mode-clear");
    const analysisModeCenter = document.getElementById("analysis-mode-center");
    const analysisModeExpandAll = document.getElementById("analysis-mode-expand-all");
    const callTreeDepthDecrease = document.getElementById("call-tree-depth-decrease");
    const callTreeDepthIncrease = document.getElementById("call-tree-depth-increase");
    const analysisContextCollapse = document.getElementById("analysis-context-collapse");
    const toolbar = document.getElementById("architecture-toolbar");
    const toolbarCollapse = document.getElementById("toolbar-collapse");
    graphCanvas.dataset.renderMode = graphState.renderMode;
    toolbarCollapse?.addEventListener("click", () => {
      const collapsed = toolbar?.classList.toggle("is-collapsed") || false;
      toolbarCollapse.setAttribute("aria-expanded", String(!collapsed));
      toolbarCollapse.setAttribute("aria-label", collapsed ? "Développer le panneau" : "Réduire le panneau");
      toolbarCollapse.setAttribute("title", collapsed ? "Développer le panneau" : "Réduire le panneau");
      toolbarCollapse.textContent = collapsed ? "›" : "‹";
    });
    analysisModeClear?.addEventListener("click", () => {
      graphState.analysisPortEndpointId = null;
      requestGraphRender();
    });
    analysisModeCenter?.addEventListener("click", () => {
      if (!graphState.selectedCodeFlowId) {
        fitCameraToVisibleGraph(renderer, "readable");
        return;
      }
      if (!graphState.relatedNodes?.size) return;
      const orderedNodes = [...(graphState.pathMicroserviceOrder?.keys() || [])];
      const remainingNodes = [...graphState.relatedNodes].filter(id => !orderedNodes.includes(id));
      scheduleFlowCameraFit({ nodes: [...orderedNodes, ...remainingNodes] });
    });
    analysisModeExpandAll?.addEventListener("click", () => {
      expandAllCallTree();
    });
    callTreeDepthDecrease?.addEventListener("click", () => {
      adjustCallTreeDepth(-1);
    });
    callTreeDepthIncrease?.addEventListener("click", () => {
      adjustCallTreeDepth(1);
    });
    analysisContextCollapse?.addEventListener("click", () => {
      graphState.analysisContextCollapsed = !graphState.analysisContextCollapsed;
      const context = document.getElementById("graph-mode-context");
      context?.classList.toggle("is-collapsed", graphState.analysisContextCollapsed);
      analysisContextCollapse.setAttribute("aria-expanded", String(!graphState.analysisContextCollapsed));
      analysisContextCollapse.textContent = graphState.analysisContextCollapsed ? "Développer" : "Réduire";
    });
    function updateFitModeControls(mode) {
      [
        [null, "overview"],
        [null, "readable"],
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
      const compoundView = targetRenderer === renderer
        && ["cluster", "elk"].includes(graphState.activeLayout);
      const collisionZoom = compoundView ? Math.max(1, requiredCardZoomIn(targetRenderer)) : 1;
      if (compoundView) {
        graphState.maximumCollisionFreeRatio = 1 / collisionZoom;
      } else if (targetRenderer === renderer) {
        graphState.maximumCollisionFreeRatio = 100;
      }
      if (nextMode === "readable") {
        const state = camera.getState();
        // Start from the complete overview, then move closer. Architecture
        // cards use the measured projected spacing.
        if (compoundView) {
          camera.setState({ ...state, ratio: Math.max(.01, state.ratio / Math.max(1.6, collisionZoom)) });
        } else if (targetRenderer === renderer && network.order <= 12) {
          camera.setState({ ...state, ratio: requiredSmallGraphOverviewRatio(targetRenderer) });
        } else {
          camera.setState({ ...state, ratio: Math.max(.01, state.ratio / Math.max(1.6, requiredCardZoomIn(targetRenderer))) });
        }
      } else if (compoundView) {
        const state = camera.getState();
        camera.setState({ ...state, ratio: Math.max(.01, state.ratio / collisionZoom) });
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
    document.getElementById("zoom-in").addEventListener("click", () => {
      if (zoomCallTree(1.25)) return;
      const renderer = activeRenderer();
      const camera = renderer.getCamera();
      const state = camera.getState();
      camera.setState({ ...state, ratio: Math.max(.01, state.ratio * .8) });
      requestGraphRender();
    });
    document.getElementById("zoom-out").addEventListener("click", () => {
      if (zoomCallTree(0.8)) return;
      const renderer = activeRenderer();
      const camera = renderer.getCamera();
      const state = camera.getState();
      camera.setState({ ...state, ratio: Math.min(graphState.maximumCollisionFreeRatio, state.ratio * 1.25) });
      requestGraphRender();
    });
    renderCardsButton.addEventListener("click", () => setNodeRenderMode("cards"));
    renderSymbolsButton.addEventListener("click", () => setNodeRenderMode("symbols"));
    resetButton.addEventListener("click", reset);
    dependencyDepth.addEventListener("change", () => {
      graphState.dependencyDepth = Number(dependencyDepth.value) || 1;
      if (!graphState.selectedId || graphState.selectedCodeFlowId) return;
      dependencyFocusOnly.checked = true;
      graphState.dependencyFocusOnly = true;
      updateSelectedNodeDependencyScope(graphState.selectedId);
      rebuildGraph();
      applyLayout(graphState.activeLayout);
    });
    dependencyFocusOnly.addEventListener("change", () => {
      if (!graphState.selectedId || graphState.selectedCodeFlowId) {
        dependencyFocusOnly.checked = false;
        return;
      }
      graphState.dependencyFocusOnly = dependencyFocusOnly.checked;
      rebuildGraph();
      applyLayout(graphState.activeLayout);
    });
    document.getElementById("inspector-close").addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      closeInspector();
    });
    inspectorBack.addEventListener("click", goBackInspector);
    inspectorModal.addEventListener("click", event => { if (event.target === inspectorModal) closeInspector(); });
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
    modeTabs.contracts.addEventListener("click", () => openResourceCatalogue("openapi_contract"));
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
    microservicesFilter.addEventListener("input", renderResourceCatalogue);
    resourceKindFilter?.addEventListener("change", renderResourceCatalogue);
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
    setToolbarTab("graph");
    function updateWorkspaceViewport(refit = false) {
      const root = document.documentElement;
      root.style.setProperty("--workspace-left", "0px");
      root.style.setProperty("--workspace-right", "0px");
      root.style.setProperty("--workspace-top", "0px");
      root.style.setProperty("--workspace-bottom", "0px");
      renderer?.refresh();
      requestGraphRender();
      if (refit) {
        if (graphState.selectedCodeFlowId && graphState.relatedNodes?.size) {
          centerCameraOnPath({ nodes: [...graphState.relatedNodes] });
        } else {
          fitCameraToVisibleGraph(activeRenderer());
        }
      }
    }
    updateWorkspaceViewport(false);
    applyLayout("forceatlas2-noverlap");
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
