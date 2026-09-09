// Ordered source module: 60-bootstrap.js
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
      renderer.refresh();
      setDetailsEmpty("Selectionnez un noeud ou un module pour afficher ses informations.");
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
        // cards use the measured projected spacing; the build graph has no
        // HTML cards and uses the same minimum reading distance.
        if (targetRenderer === renderer && network.order <= 12) {
          camera.setState({ ...state, ratio: requiredSmallGraphOverviewRatio(targetRenderer) });
        } else {
          const zoomIn = targetRenderer === renderer
            ? Math.max(1.6, requiredCardZoomIn(targetRenderer))
            : 1.6;
          camera.setState({ ...state, ratio: Math.max(.01, state.ratio / zoomIn) });
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
    layoutButtons.forEach((button, layout) => button.addEventListener("click", () => applyLayout(layout)));
    graphTab.addEventListener("click", () => setToolbarTab("graph"));
    openApiTab.addEventListener("click", () => setToolbarTab("openapi"));
    kafkaTab.addEventListener("click", () => setToolbarTab("kafka"));
    persistenceTab.addEventListener("click", () => setToolbarTab("persistence"));
    requestReplyTab.addEventListener("click", () => setToolbarTab("request-reply"));
    buildTab.addEventListener("click", () => setToolbarTab("dependencies"));
    issuesTab.addEventListener("click", () => setToolbarTab("issues"));
    inventoryStatus.addEventListener("click", () => setToolbarTab("issues"));
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
      showProjectGroups,
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
    dtoReferencesFilter.addEventListener("input", renderReferences);
    mongoClassReferencesFilter.addEventListener("input", renderReferences);
    pathLock.addEventListener("change", persistState);
    pathQuery.addEventListener("keydown", event => {
      if (event.key === "Enter") showShortestPath();
    });
    renderIndexingIssues();
    renderReferences();
    renderRequestReplyPatterns();
    restoreState();
    function updateWorkspaceViewport(refit = false) {
      const toolbar = document.querySelector(".toolbar");
      const desktop = window.innerWidth > 700;
      const toolbarRight = toolbar?.getBoundingClientRect().right || 0;
      const left = desktop ? Math.min(window.innerWidth - 220, toolbarRight + 10) : 0;
      const root = document.documentElement;
      root.style.setProperty("--workspace-left", `${Math.max(0, left)}px`);
      root.style.setProperty("--workspace-right", "0px");
      root.style.setProperty("--workspace-top", "0px");
      root.style.setProperty("--workspace-bottom", "0px");
      renderer?.refresh();
      dependencyRenderer?.refresh();
      requestGraphRender();
      if (refit) fitCameraToVisibleGraph(activeRenderer());
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
