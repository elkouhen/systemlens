// Ordered source module: 07-navigation.js
// Owns toolbar navigation and panel visibility.
    function setToolbarTab(tab, options = {}) {
      graphState.analysisContextCollapsed = true;
      document.body.classList.toggle("catalogue-active", tab === "microservices");
      const { showingGraph, showingFlows, showingFlowGraph, graphVisible, resetView } = graphViewLifecycle.activate(tab, options);
      // The two navigation surfaces have different meanings: Graphe is the
      // static architecture view, while Flux de code only becomes a graph
      // after the user explicitly selects a flow from its list.
      if (resetView) {
        // The Flux de code panel is hidden behind the toolbar and does not
        // need a graph rebuild. Rebuild only when returning to Graphe, after
        // the static view has become visible again.
        if (tab === "graph") {
          rebuildGraph();
          if (!options.deferLayout) applyLayout(graphState.activeLayout);
        }
      }
      if (document.querySelector(".toolbar")?.classList.contains("has-details")) {
        setDetailsEmpty("Sélectionnez un nœud pour afficher ses détails.");
      }
      const showingMicroservices = tab === "microservices";
      const showingIssues = tab === "issues";
      const showingContracts = tab === "contracts";
      const showingAsyncApi = tab === "asyncapi";
      const showingDtoContracts = tab === "dto-contracts";
      const showingJpa = tab === "jpa";
      const showingRoutes = tab === "routes";
      const showingKafka = tab === "kafka";
      const showingCollections = tab === "collections";
      const showingPersistence = tab === "persistence";
      const showingContractView = showingContracts || showingAsyncApi || showingDtoContracts || showingJpa || showingPersistence;
      const mode = showingMicroservices
        ? "contracts"
        : showingFlows
        ? "architecture"
        : showingContractView
          ? "contracts"
          : showingIssues
            ? "diagnostics"
            : "architecture";
      Object.entries(modeTabs).forEach(([name, button]) => {
        const active = name === mode;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-selected", String(active));
      });
      Object.entries(modeGroups).forEach(([name, group]) => {
        group.hidden = name !== mode || (showingMicroservices && name === "contracts");
      });
      graphTab.classList.toggle("is-active", showingGraph);
      graphTab.setAttribute("aria-selected", String(showingGraph));
      microservicesTab.classList.toggle("is-active", showingMicroservices);
      microservicesTab.setAttribute("aria-selected", String(showingMicroservices));
      routesTab.classList.toggle("is-active", showingRoutes);
      routesTab.setAttribute("aria-selected", String(showingRoutes));
      contractsTab.classList.toggle("is-active", showingContracts);
      contractsTab.setAttribute("aria-selected", String(showingContracts));
      asyncApiTab.classList.toggle("is-active", showingAsyncApi);
      asyncApiTab.setAttribute("aria-selected", String(showingAsyncApi));
      dtoContractTab.classList.toggle("is-active", showingDtoContracts);
      dtoContractTab.setAttribute("aria-selected", String(showingDtoContracts));
      jpaTab.classList.toggle("is-active", showingJpa);
      jpaTab.setAttribute("aria-selected", String(showingJpa));
      kafkaTab.classList.toggle("is-active", showingKafka);
      kafkaTab.setAttribute("aria-selected", String(showingKafka));
      collectionsTab.classList.toggle("is-active", showingCollections);
      collectionsTab.setAttribute("aria-selected", String(showingCollections));
      persistenceTab.classList.toggle("is-active", showingPersistence);
      persistenceTab.setAttribute("aria-selected", String(showingPersistence));
      issuesTab.classList.toggle("is-active", showingIssues);
      issuesTab.setAttribute("aria-selected", String(showingIssues));
      flowsTab.classList.toggle("is-active", showingFlows);
      flowsTab.setAttribute("aria-selected", String(showingFlows));
      graphPanel.hidden = !showingGraph;
      microservicesPanel.hidden = !showingMicroservices;
      quickSearch.hidden = !showingGraph;
      graphContext.hidden = !showingGraph;
      // Keep the mode-specific graph toolbars aligned with the selected tab.
      // The Flux list has no graph controls; they only appear after a flow
      // explicitly opens its call graph.
      const graphModeContext = document.getElementById("graph-mode-context");
      const flowModeContext = document.getElementById("flow-mode-context");
      if (graphModeContext) graphModeContext.hidden = !showingGraph;
      if (flowModeContext) flowModeContext.hidden = !showingFlows;
      issuesPanel.hidden = !showingIssues;
      contractsPanel.hidden = !showingContracts;
      asyncApiContractPanel.hidden = !showingAsyncApi;
      dtoContractPanel.hidden = !showingDtoContracts;
      jpaPanel.hidden = !showingJpa;
      routesPanel.hidden = !showingRoutes;
      kafkaPanel.hidden = !showingKafka;
      collectionsPanel.hidden = !showingCollections;
      persistencePanel.hidden = !showingPersistence;
      flowsPanel.hidden = !showingFlows;
      graphLegend.hidden = !showingGraph;
      if (showingFlows && !showingFlowGraph) flowsPanel.dispatchEvent(new Event("systemlens:flows-open"));
    }
