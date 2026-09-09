// Ordered source module: 60-bootstrap.js
      if (position && !graphState.layeredView && !graphState.clusteredView) {
        renderer.getCamera().animate({ x: position.x, y: position.y, ratio: .55 }, { duration: 260 });
      }
      persistState();
    }
    function reset() {
      updateGraphState({
        selectedId: null,
        selectedClusterKey: null,
        relatedNodes: null,
        relatedEdges: null,
        pathMicroserviceOrder: new Map(),
      });
      if (document.querySelector('.filter-preset[data-preset="selection"]')?.classList.contains("is-active")) {
        setActiveRelationPreset("all");
      }
      renderer.refresh();
      setDetailsEmpty("Selectionnez un noeud ou un cluster pour afficher ses informations.");
      search.value = "";
      clearPathControls();
      persistState();
    }
    function restoreState() {
      const params = new URLSearchParams(location.hash.slice(1));
      const sourceId = params.get("from");
      const targetId = params.get("to");
      pathLock.checked = params.get("lock") === "1";
      const restoredStops = [sourceId, ...params.getAll("via"), targetId];
      if (
        sourceId
        && targetId
        && isValidPathStops(restoredStops)
      ) {
        pathStops.push(...restoredStops);
        renderPathQuery();
        showShortestPath();
        return;
      }
      const selectedIdFromUrl = params.get("selected");
      if (selectedIdFromUrl && nodeDataById.has(selectedIdFromUrl)) selectNode(selectedIdFromUrl);
    }
    function activeRenderer() {
      return dependencyCanvas.hidden ? renderer : ensureDependencyRenderer();
    }
    const fitOverviewButton = document.getElementById("fit-view");
    const fitReadableButton = document.getElementById("fit-readable");
    const renderCardsButton = document.getElementById("render-cards");
    const renderSymbolsButton = document.getElementById("render-symbols");
    graphCanvas.dataset.renderMode = graphState.renderMode;
    function updateFitModeControls(mode) {
      [
        [fitOverviewButton, "overview"],
        [fitReadableButton, "readable"],
      ].forEach(([button, buttonMode]) => {
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
      await requestGraphRender();
      if (graphState.renderMode !== nextMode) return;
      await fitCameraToVisibleGraph(renderer);
      if (graphState.renderMode !== nextMode) return;
      if (graphState.activeLayout === "forceatlas2-noverlap" && graphState.fitMode === "readable") {
        resolveGraphCardOverlaps();
        renderer.refresh();
        await fitCameraToVisibleGraph(renderer);
        await requestGraphRender();
      }
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
        // cards use the measured projected spacing; the build graph has no
        // HTML cards and uses the same minimum reading distance.
        const zoomIn = targetRenderer === renderer
          ? Math.max(1.6, requiredCardZoomIn(targetRenderer))
          : 1.6;
        camera.setState({ ...state, ratio: Math.max(.01, state.ratio / zoomIn) });
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
      const targetCanvas = targetRenderer === renderer ? graphCanvas : dependencyCanvas;
      targetCanvas.dataset.fitMode = nextMode;
      targetCanvas.dataset.fitRatio = String(targetRenderer.getCamera().getState().ratio);
    }
    document.getElementById("zoom-in").addEventListener("click", () => {
      const renderer = activeRenderer();
      const camera = renderer.getCamera();
      const state = camera.getState();
      camera.setState({ ...state, ratio: Math.max(.01, state.ratio * .8) });
      requestGraphRender();
    });
    document.getElementById("zoom-out").addEventListener("click", () => {
      const renderer = activeRenderer();
      const camera = renderer.getCamera();
      const state = camera.getState();
      camera.setState({ ...state, ratio: Math.min(100, state.ratio * 1.25) });
      requestGraphRender();
    });
    fitOverviewButton.addEventListener("click", () => fitCameraToVisibleGraph(activeRenderer(), "overview"));
    fitReadableButton.addEventListener("click", () => fitCameraToVisibleGraph(activeRenderer(), "readable"));
    renderCardsButton.addEventListener("click", () => setNodeRenderMode("cards"));
    renderSymbolsButton.addEventListener("click", () => setNodeRenderMode("symbols"));
    document.getElementById("reset").addEventListener("click", reset);
    document.getElementById("inspector-close").addEventListener("click", closeInspector);
    inspectorModal.addEventListener("click", event => { if (event.target === inspectorModal) closeInspector(); });
    window.addEventListener("keydown", event => { if (event.key === "Escape" && !inspectorModal.hidden) closeInspector(); });
    document.getElementById("show-path").addEventListener("click", showShortestPath);
    document.getElementById("show-simple-paths").addEventListener("click", showSimplePaths);
    document.getElementById("question-topic").addEventListener("click", () => {
      setToolbarTab("graph");
      applyRelationPreset("kafka");
      search.placeholder = "orders.created ou orders -> orders.created";
      search.focus();
    });
    document.getElementById("question-service").addEventListener("click", () => {
      setToolbarTab("graph");
      applyRelationPreset("all");
      search.placeholder = "orders ou orders -> payments";
      search.focus();
    });
    document.getElementById("question-path").addEventListener("click", () => {
      setToolbarTab("graph");
      search.focus();
    });
    document.getElementById("question-messages").addEventListener("click", () => {
      setToolbarTab("kafka");
      dtoReferencesFilter.focus();
    });
    layoutButtons.forEach((button, layout) => button.addEventListener("click", () => applyLayout(layout)));
    graphTab.addEventListener("click", () => setToolbarTab("graph"));
    openApiTab.addEventListener("click", () => setToolbarTab("openapi"));
    kafkaTab.addEventListener("click", () => setToolbarTab("kafka"));
    persistenceTab.addEventListener("click", () => setToolbarTab("persistence"));
    requestReplyTab.addEventListener("click", () => setToolbarTab("request-reply"));
    buildTab.addEventListener("click", () => setToolbarTab("dependencies"));
    issuesTab.addEventListener("click", () => setToolbarTab("issues"));
    pathsTab.addEventListener("click", () => setToolbarTab("paths"));
    inventoryStatus.addEventListener("click", () => setToolbarTab("issues"));
    filterPresetButtons.forEach(button => button.addEventListener("click", () => applyRelationPreset(button.dataset.preset)));
    [
      relationHttp,
      relationKafka,
      relationMongodb,
      nodeMicroservice,
      nodeExternalMicroservice,
      nodeKafkaTopic,
      nodeMongodbCollection,
      showProjectGroups,
    ].forEach(control => control.addEventListener("click", () => {
      setActiveRelationPreset(null);
      // Reflect the checkbox state synchronously. Rebuilding Sigma and
      // applying the active layout are intentionally asynchronous, while the
      // data contract is also consumed by compact-viewport integrations.
      const visibleRelationCount = graphData.links.filter(link => (
        isVisibleRelation(link.kind)
        && isVisibleNode(nodeDataById.get(link.source))
        && isVisibleNode(nodeDataById.get(link.target))
      )).length;
      document.getElementById("graph").dataset.relationCount = String(visibleRelationCount);
      if ([relationHttp, relationKafka, relationMongodb].includes(control)) {
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
    dtoReferencesFilter.addEventListener("input", renderReferences);
    mongoClassReferencesFilter.addEventListener("input", renderReferences);
    pathLock.addEventListener("change", persistState);
    pathQuery.addEventListener("keydown", event => {
      if (event.key === "Enter") showShortestPath();
    });
    renderIndexingIssues();
    renderAnalyzedPaths();
    renderReferences();
    renderRequestReplyPatterns();
    restoreState();
    let workspaceGeometry = null;
    function updateWorkspaceViewport(refit = false) {
      const toolbar = document.querySelector(".toolbar");
      const detailsPanel = document.getElementById("details");
      const desktop = window.innerWidth > 700;
      const toolbarRight = toolbar?.getBoundingClientRect().right || 0;
      const left = desktop ? Math.min(window.innerWidth - 220, toolbarRight + 24) : 0;
      const detailsHeight = detailsPanel
        ? Math.min(window.innerHeight * .42, detailsPanel.getBoundingClientRect().height + 24)
        : 0;
      const nextGeometry = {
        left: Math.max(0, left),
        bottom: Math.max(0, detailsHeight),
      };
      const geometryChanged = !workspaceGeometry
        || Math.abs(workspaceGeometry.left - nextGeometry.left) > .5
        || Math.abs(workspaceGeometry.bottom - nextGeometry.bottom) > .5;
      workspaceGeometry = nextGeometry;
      const root = document.documentElement;
      root.style.setProperty("--workspace-left", `${nextGeometry.left}px`);
      root.style.setProperty("--workspace-right", "0px");
      root.style.setProperty("--workspace-top", "0px");
      root.style.setProperty("--workspace-bottom", `${nextGeometry.bottom}px`);
      renderer?.refresh();
      dependencyRenderer?.refresh();
      requestGraphRender();
      if (refit && geometryChanged) fitCameraToVisibleGraph(activeRenderer());
    }
    const workspaceObserver = typeof ResizeObserver === "function"
      ? new ResizeObserver(() => updateWorkspaceViewport(true))
      : new MutationObserver(() => updateWorkspaceViewport(true));
    if (workspaceObserver instanceof MutationObserver) {
      workspaceObserver.observe(details, { attributes: true, childList: true, subtree: true });
    } else {
      workspaceObserver.observe(details);
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
      if (!query) { reset(); return; }
      if (query.includes("->")) return;
      const resolved = resolveExactNodeName(query);
      if (resolved.id) selectNode(resolved.id);
    });
    search.addEventListener("keydown", event => {
      if (event.key === "Enter") { event.preventDefault(); runExploreSearch(); }
    });
    window.addEventListener("resize", () => updateWorkspaceViewport(true));
