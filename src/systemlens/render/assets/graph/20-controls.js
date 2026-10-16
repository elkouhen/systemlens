// Ordered source module: 20-controls.js
    const details = document.getElementById("details");
    const quickSearch = document.getElementById("quick-search");
    const graphContext = document.getElementById("graph-context");
    const graphMinimap = document.getElementById("graph-minimap");
    const graphMinimapSvg = document.getElementById("graph-minimap-svg");
    const graphMinimapNodes = document.getElementById("graph-minimap-nodes");
    const graphMinimapViewport = document.getElementById("graph-minimap-viewport");
    let graphMinimapModel = null;
    let graphMinimapPointerId = null;
    let graphMinimapRenderScheduled = false;
    const MINIMAP_WIDTH = 180;
    const MINIMAP_HEIGHT = 110;
    const MINIMAP_PADDING = 10;
    function renderGraphMinimap() {
      if (!graphMinimap || !graphMinimapSvg || !graphMinimapNodes || !graphMinimapViewport) return;
      const isTree = Boolean(codeFlowSelection.flowId)
        && graphState.viewMode === "call-graph"
        && graphState.callGraphDisplayMode === "tree";
      const viewport = (isTree ? graphCallTreeOverlay : graphCanvas).getBoundingClientRect();
      const items = [];
      let thresholdReached = false;
      if (isTree) {
        const canvas = graphCallTreeOverlay.querySelector(".graph-call-tree-canvas");
        if (!canvas || graphCallTreeOverlay.hidden) {
          graphMinimap.hidden = true;
          graphMinimapModel = null;
          return;
        }
        const totalScale = Math.max(.001,
          (Number(canvas.dataset.baseScale) || 1) * (graphState.callTreeZoom || 1));
        const left = Number.parseFloat(canvas.style.left) || 0;
        const top = Number.parseFloat(canvas.style.top) || 0;
        const width = Number.parseFloat(canvas.style.width) || 1;
        const height = Number.parseFloat(canvas.style.height) || 1;
        const treeNodes = [...canvas.querySelectorAll(".graph-call-tree-node")];
        const stride = Math.max(1, Math.ceil(treeNodes.length / 700));
        treeNodes.forEach((node, index) => {
          if (index % stride !== 0 && node.dataset.pathKey !== graphState.callTreeSelectedPathKey) return;
          const x = left + ((Number.parseFloat(node.style.left) || 0) + node.offsetWidth / 2) * totalScale;
          const y = top + ((Number.parseFloat(node.style.top) || 0) + node.offsetHeight / 2) * totalScale;
          items.push({
            x, y,
            kind: "service",
            selected: node.dataset.pathKey === graphState.callTreeSelectedPathKey,
          });
        });
        thresholdReached = treeNodes.length > 12 || totalScale < .82
          || width * totalScale > viewport.width * 1.15
          || height * totalScale > viewport.height * 1.15;
        graphMinimapModel = { kind: "tree", left, top, totalScale, width, height, viewport };
      } else {
        if (graphState.viewMode !== "architecture" || !graphCanvas) {
          graphMinimap.hidden = true;
          graphMinimapModel = null;
          return;
        }
        const visibleIds = network.nodes().filter(id => {
          const attributes = network.getNodeAttributes(id);
          return isVisibleNodeId(id) && !attributes.hidden;
        });
        const stride = Math.max(1, Math.ceil(visibleIds.length / 700));
        visibleIds.forEach((id, index) => {
          if (index % stride !== 0 && id !== graphState.selectedId) return;
          const attributes = network.getNodeAttributes(id);
          const point = renderer.graphToViewport({ x: attributes.x, y: attributes.y });
          if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) return;
          const data = nodeDataById.get(id);
          items.push({
            x: point.x, y: point.y,
            kind: ["kafka_topic", "message_channel"].includes(data?.kind) ? "topic"
              : data?.kind === "microservice" ? "service" : "resource",
            selected: id === graphState.selectedId,
            id,
          });
        });
        thresholdReached = visibleIds.length > 24
          || items.some(item => item.x < 0 || item.x > viewport.width
            || item.y < 0 || item.y > viewport.height);
        graphMinimapModel = { kind: "architecture", viewport };
      }
      if (!items.length || !thresholdReached) {
        graphMinimap.hidden = true;
        graphMinimapModel = null;
        return;
      }
      graphMinimap.hidden = false;
      const extent = {
        left: Math.min(0, ...items.map(item => item.x)),
        right: Math.max(viewport.width, ...items.map(item => item.x)),
        top: Math.min(0, ...items.map(item => item.y)),
        bottom: Math.max(viewport.height, ...items.map(item => item.y)),
      };
      const paddingX = (extent.right - extent.left) * .04;
      const paddingY = (extent.bottom - extent.top) * .04;
      extent.left -= paddingX;
      extent.right += paddingX;
      extent.top -= paddingY;
      extent.bottom += paddingY;
      const xAt = x => MINIMAP_PADDING
        + (x - extent.left) / Math.max(1, extent.right - extent.left)
          * (MINIMAP_WIDTH - MINIMAP_PADDING * 2);
      const yAt = y => MINIMAP_PADDING
        + (y - extent.top) / Math.max(1, extent.bottom - extent.top)
          * (MINIMAP_HEIGHT - MINIMAP_PADDING * 2);
      const sampledItems = items.length <= 700 ? items : [
        ...items.filter(item => item.selected).slice(0, 1),
        ...items.filter(item => !item.selected).slice(0, 699),
      ];
      const nodesFragment = document.createDocumentFragment();
      sampledItems.forEach(item => {
        const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        dot.classList.add("graph-minimap-node", `is-${item.kind}`);
        if (item.selected) dot.classList.add("is-selected");
        dot.setAttribute("cx", String(xAt(item.x)));
        dot.setAttribute("cy", String(yAt(item.y)));
        dot.setAttribute("r", item.selected ? "2.5" : "1.7");
        nodesFragment.append(dot);
      });
      graphMinimapNodes.replaceChildren(nodesFragment);
      graphMinimapViewport.setAttribute("x", String(xAt(0)));
      graphMinimapViewport.setAttribute("y", String(yAt(0)));
      graphMinimapViewport.setAttribute("width", String(Math.max(2, xAt(viewport.width) - xAt(0))));
      graphMinimapViewport.setAttribute("height", String(Math.max(2, yAt(viewport.height) - yAt(0))));
      graphMinimapModel.mapPoint = (event) => {
        const svgRect = graphMinimapSvg.getBoundingClientRect();
        const point = graphMinimapSvg.createSVGPoint();
        point.x = event.clientX;
        point.y = event.clientY;
        let local;
        try { local = point.matrixTransform(graphMinimapSvg.getScreenCTM().inverse()); }
        catch (_error) {
          local = {
            x: (event.clientX - svgRect.left) / Math.max(1, svgRect.width) * MINIMAP_WIDTH,
            y: (event.clientY - svgRect.top) / Math.max(1, svgRect.height) * MINIMAP_HEIGHT,
          };
        }
        return {
          x: extent.left + (local.x - MINIMAP_PADDING)
            / (MINIMAP_WIDTH - MINIMAP_PADDING * 2) * (extent.right - extent.left),
          y: extent.top + (local.y - MINIMAP_PADDING)
            / (MINIMAP_HEIGHT - MINIMAP_PADDING * 2) * (extent.bottom - extent.top),
        };
      };
    }
    function scheduleGraphMinimapRender() {
      if (graphMinimapRenderScheduled) return;
      graphMinimapRenderScheduled = true;
      requestAnimationFrame(() => {
        graphMinimapRenderScheduled = false;
        renderGraphMinimap();
      });
    }
    function navigateGraphMinimap(event) {
      if (!graphMinimapModel) return;
      const point = graphMinimapModel.mapPoint(event);
      if (graphMinimapModel.kind === "tree") {
        const { viewport, totalScale, left, top } = graphMinimapModel;
        const canvas = graphCallTreeOverlay.querySelector(".graph-call-tree-canvas");
        if (!canvas) return;
        const logicalX = (point.x - left) / totalScale;
        const logicalY = (point.y - top) / totalScale;
        canvas.style.left = `${viewport.width / 2 - logicalX * totalScale}px`;
        canvas.style.top = `${viewport.height / 2 - logicalY * totalScale}px`;
        graphMinimapModel.left = Number.parseFloat(canvas.style.left) || 0;
        graphMinimapModel.top = Number.parseFloat(canvas.style.top) || 0;
        renderGraphMinimap();
        return;
      }
      const graphPoint = renderer.viewportToGraph({ x: point.x, y: point.y });
      const projected = renderer.graphToViewport(graphPoint);
      const camera = renderer.getCamera();
      const state = camera.getState();
      camera.setState({
        ...state,
        x: state.x - (viewport.width / 2 - projected.x) / Math.max(1, viewport.width),
        y: state.y + (viewport.height / 2 - projected.y) / Math.max(1, viewport.height),
      });
      renderer.refresh();
      requestGraphRender();
    }
    graphMinimap?.addEventListener("pointerdown", event => {
      if (event.button !== 0 || !graphMinimapModel) return;
      graphMinimapPointerId = event.pointerId;
      graphMinimap.setPointerCapture?.(event.pointerId);
      navigateGraphMinimap(event);
      event.preventDefault();
    });
    graphMinimap?.addEventListener("pointermove", event => {
      if (event.pointerId === graphMinimapPointerId) navigateGraphMinimap(event);
    });
    const finishGraphMinimap = event => {
      if (event.pointerId !== graphMinimapPointerId) return;
      graphMinimapPointerId = null;
      try { graphMinimap.releasePointerCapture?.(event.pointerId); } catch (_error) { /* map was hidden */ }
    };
    graphMinimap?.addEventListener("pointerup", finishGraphMinimap);
    graphMinimap?.addEventListener("pointercancel", finishGraphMinimap);
    const search = document.getElementById("search");
    const resetButton = document.getElementById("reset");
    const searchStatus = document.getElementById("search-status");
    const graphTab = document.getElementById("graph-tab");
    const microservicesTab = document.getElementById("microservices-tab");
    const contractsTab = document.getElementById("contracts-tab");
    const asyncApiTab = document.getElementById("asyncapi-tab");
    const dtoContractTab = document.getElementById("dto-contract-tab");
    const jpaTab = document.getElementById("jpa-tab");
    const routesTab = document.getElementById("routes-tab");
    const kafkaTab = document.getElementById("kafka-tab");
    const collectionsTab = document.getElementById("collections-tab");
    const persistenceTab = document.getElementById("persistence-tab");
    const issuesTab = document.getElementById("issues-tab");
    const flowsTab = document.getElementById("flows-tab");
    const modeTabs = {
      architecture: document.getElementById("architecture-mode-tab"),
      flows: document.getElementById("flows-mode-tab"),
      contracts: document.getElementById("contracts-mode-tab"),
      diagnostics: document.getElementById("diagnostics-mode-tab"),
    };
    const modeGroups = {
      architecture: document.getElementById("architecture-tabs-group"),
      flows: document.getElementById("flows-tabs-group"),
      contracts: document.getElementById("contracts-tabs-group"),
      diagnostics: document.getElementById("diagnostics-tabs-group"),
    };
    const graphLegend = document.getElementById("graph-legend");
    const dependencyDepth = document.getElementById("dependency-depth");
    const dependencyFocusOnly = document.getElementById("dependency-focus-only");
    const dependencyAnalysisControls = document.getElementById("dependency-analysis-controls");
    const dependencyAnalysisHelp = document.getElementById("dependency-analysis-help");
    const graphPanel = document.getElementById("graph-panel");
    const microservicesPanel = document.getElementById("microservices-panel");
    const issuesPanel = document.getElementById("issues-panel");
    const flowsPanel = document.getElementById("flows-panel");
    const contractsPanel = document.getElementById("contracts-panel");
    const asyncApiContractPanel = document.getElementById("asyncapi-contract-panel");
    const dtoContractPanel = document.getElementById("dto-contract-panel");
    const jpaPanel = document.getElementById("jpa-panel");
    const routesPanel = document.getElementById("routes-panel");
    const kafkaPanel = document.getElementById("kafka-panel");
    const collectionsPanel = document.getElementById("collections-panel");
    const persistencePanel = document.getElementById("persistence-panel");
    const graphCanvas = document.getElementById("graph");
    const indexingIssuesList = document.getElementById("indexing-issues");
    const indexingIssuesEmpty = document.getElementById("indexing-issues-empty");
    const indexingIssuesTitle = document.getElementById("indexing-issues-title");
    const indexingIssues = graphData.indexing_issues || [];
    const inventoryStatus = document.getElementById("inventory-status");
    const progressNotice = document.getElementById("progress-notice");
    const microservicesList = document.getElementById("microservices-list");
    const microservicesEmpty = document.getElementById("microservices-empty");
    const microservicesFilter = document.getElementById("microservices-filter");
    const resourceKindFilter = document.getElementById("resource-kind-filter");
    const resourceCatalogueSummary = document.getElementById("resource-catalogue-summary");
    const resourceCataloguePreview = document.getElementById("resource-catalogue-preview");
    const microservicesTitle = document.getElementById("microservices-title");
    const microserviceVisibilityList = document.getElementById("microservice-visibility-list");
    const microserviceVisibilityReset = document.getElementById("microservice-visibility-reset");
    const microserviceVisibilityFilter = document.getElementById("microservice-visibility-filter");
    const microserviceVisibilitySummary = document.getElementById("microservice-visibility-summary");
    const topicVisibilityList = document.getElementById("topic-visibility-list");
    const topicVisibilityReset = document.getElementById("topic-visibility-reset");
    const topicVisibilityFilter = document.getElementById("topic-visibility-filter");
    const topicVisibilitySummary = document.getElementById("topic-visibility-summary");
    const collectionsList = document.getElementById("collections-list");
    const collectionsEmpty = document.getElementById("collections-empty");
    const collectionsFilter = document.getElementById("collections-filter");
    const collectionsTitle = document.getElementById("collections-title");
    const openApiReferencesList = document.getElementById("openapi-references");
    const openApiReferencesEmpty = document.getElementById("openapi-references-empty");
    const openApiReferencesFilter = document.getElementById("openapi-reference-filter");
    const dtoContractReferencesList = document.getElementById("dto-contract-references");
    const dtoContractReferencesEmpty = document.getElementById("dto-contract-references-empty");
    const dtoContractReferencesFilter = document.getElementById("dto-contract-reference-filter");
    const dtoContractReferencesTitle = document.getElementById("dto-contract-references-title");
    const openapiReferencesTitle = document.getElementById("openapi-references-title");
    const routesList = document.getElementById("routes-list");
    const routesEmpty = document.getElementById("routes-empty");
    const routesFilter = document.getElementById("routes-filter");
    const routesTitle = document.getElementById("routes-title");
    const topicsList = document.getElementById("topics-list");
    const topicsEmpty = document.getElementById("topics-empty");
    const topicsFilter = document.getElementById("topics-filter");
    const topicsTitle = document.getElementById("topics-title");
    const asyncApiReferencesList = document.getElementById("asyncapi-panel-references");
    const asyncApiReferencesEmpty = document.getElementById("asyncapi-panel-empty");
    const asyncApiReferencesTitle = document.getElementById("asyncapi-panel-title");
    const asyncApiPanelFilter = document.getElementById("asyncapi-reference-filter");
    const mongoClassReferencesList = document.getElementById("mongo-class-references");
    const mongoClassReferencesEmpty = document.getElementById("mongo-class-references-empty");
    const mongoClassReferencesFilter = document.getElementById("mongo-class-reference-filter");
    const mongoClassReferencesTitle = document.getElementById("mongo-class-references-title");
    const jpaReferencesList = document.getElementById("jpa-references");
    const jpaReferencesEmpty = document.getElementById("jpa-references-empty");
    const jpaReferencesFilter = document.getElementById("jpa-reference-filter");
    const jpaReferencesTitle = document.getElementById("jpa-references-title");
    const layoutStatus = document.getElementById("layout-status");
    const layoutButtons = new Map([
      ["forceatlas2", document.getElementById("layout-forceatlas2")],
      ["noverlap", document.getElementById("layout-noverlap")],
    ]);
    const layoutLabels = new Map([
      ["forceatlas2", "regroupement des liens"],
      ["noverlap", "éviter les chevauchements"],
      ["forceatlas2-noverlap", "vue par graphe"],
    ]);
    const pathStops = [];
    const MAX_SIMPLE_PATH_DEPTH = 8;
    const MAX_SIMPLE_PATHS = 8;
    const MAX_SIMPLE_PATH_EXPLORATIONS = 2000;
    const microserviceNames = graphData.nodes
      .filter(node => node.kind === "microservice")
      .map(node => node.name)
      .filter(Boolean)
      .sort((left, right) => left.localeCompare(right));
    const topicNames = graphData.nodes
      .filter(node => ["kafka_topic", "message_channel"].includes(node.kind))
      .map(node => node.name)
      .filter(Boolean)
      .sort((left, right) => left.localeCompare(right));
    function createVisibilityController({
      names, hiddenItems, storageKey, list, filter, summary, reset,
      itemLabel, emptyLabel, emptySearchLabel,
    }) {
      const persist = () => {
        try {
          localStorage.setItem(storageKey, JSON.stringify([...hiddenItems].sort()));
        } catch (_error) { /* optional preference */ }
      };
      const render = () => {
        if (!list) return;
        list.replaceChildren();
        const query = (filter?.value || "").trim().toLocaleLowerCase();
        const hiddenNames = names.filter(name => hiddenItems.has(name));
        const matchingNames = query
          ? names.filter(name => name.toLocaleLowerCase().includes(query))
          : hiddenNames;
        if (summary) {
          const hiddenCount = hiddenNames.length;
          summary.textContent = query
            ? `${matchingNames.length} résultat${matchingNames.length > 1 ? "s" : ""} · ${hiddenCount} caché${hiddenCount > 1 ? "s" : ""}`
            : hiddenCount
              ? `${hiddenCount} ${itemLabel}${hiddenCount > 1 ? "s" : ""} caché${hiddenCount > 1 ? "s" : ""}`
              : `Aucun ${itemLabel} caché`;
        }
        if (!matchingNames.length) {
          const empty = document.createElement("p");
          empty.className = "microservice-visibility-empty";
          empty.textContent = query ? emptyLabel : emptySearchLabel;
          list.append(empty);
        }
        matchingNames.forEach(name => {
          const label = document.createElement("label");
          label.className = "microservice-visibility-item";
          const input = document.createElement("input");
          input.type = "checkbox";
          input.checked = !hiddenItems.has(name);
          input.setAttribute("aria-label", `Afficher ${name}`);
          input.addEventListener("change", () => {
            if (input.checked) hiddenItems.delete(name);
            else hiddenItems.add(name);
            persist();
            render();
            rebuildGraph();
          });
          label.append(input, document.createTextNode(name));
          list.append(label);
        });
      };
      reset?.addEventListener("click", () => {
        hiddenItems.clear();
        persist();
        render();
        rebuildGraph();
        window.dispatchEvent(new Event("systemlens:filters-changed"));
      });
      filter?.addEventListener("input", render);
      render();
    }
    createVisibilityController({
      names: microserviceNames,
      hiddenItems: hiddenMicroservices,
      storageKey: hiddenMicroservicesStorageKey,
      list: microserviceVisibilityList,
      filter: microserviceVisibilityFilter,
      summary: microserviceVisibilitySummary,
      reset: microserviceVisibilityReset,
      itemLabel: "microservice",
      emptyLabel: "Aucun microservice correspondant.",
      emptySearchLabel: "Utilisez la recherche pour en cacher un.",
    });
    createVisibilityController({
      names: topicNames,
      hiddenItems: hiddenTopics,
      storageKey: hiddenTopicsStorageKey,
      list: topicVisibilityList,
      filter: topicVisibilityFilter,
      summary: topicVisibilitySummary,
      reset: topicVisibilityReset,
      itemLabel: "topic",
      emptyLabel: "Aucun topic correspondant.",
      emptySearchLabel: "Utilisez la recherche pour en cacher un.",
    });
    function restoreInitialNodePositions() {
      network.forEachNode(node => {
        const position = initialNodePositions.get(node);
        network.setNodeAttribute(node, "x", position.x);
        network.setNodeAttribute(node, "y", position.y);
      });
    }
    function namespaceForNode(node) {
      const data = nodeDataById.get(node);
      if (data?.kind !== "microservice") {
        const producer = preferredOwnerForNode(node);
        const producerNamespace = producer?.cluster_path || producer?.project_namespace_path
          || producer?.project_namespace || producer?.architecture_namespace;
        if (producerNamespace) return producerNamespace;
      }
      if (data?.cluster_path) return data.cluster_path;
      if (data?.project_namespace_path) return data.project_namespace_path;
      if (data?.project_namespace) return data.project_namespace;
      if (data?.architecture_namespace) return data.architecture_namespace;
      const namespaces = [...(data?.runtime_namespaces || []), ...(data?.fact_namespaces || [])];
      if (namespaces.length) return namespaces[0];
      if (data?.kind !== "microservice") return "root";
      const neighbourNamespaces = network.neighbors(node).flatMap(neighbour => {
        const neighbourData = nodeDataById.get(neighbour);
        return [...(neighbourData?.runtime_namespaces || []), ...(neighbourData?.fact_namespaces || [])];
      });
      return neighbourNamespaces[0] || "root";
    }
    function ownerCandidatesForNode(node) {
      const data = nodeDataById.get(node);
      if (data?.kind === "microservice") return [node];
      if (data?.owner_service) {
        const ownerId = `microservice:${data.owner_service}`;
        if (nodeDataById.get(ownerId)) return [ownerId];
      }
      return [
        node,
        ...graphData.links
          .filter(link => link.target === node)
          .map(link => link.source)
          .filter(candidate => nodeDataById.get(candidate)?.kind === "microservice"),
      ];
    }
    function preferredOwnerForNode(node) {
      const layerOrder = ["api", "application", "orchestration", "infrastructure", "domain", "persistence", "external"];
      const layerRank = new Map(layerOrder.map((layer, index) => [layer, index]));
      return ownerCandidatesForNode(node)
        .map(candidate => nodeDataById.get(candidate))
        .filter(candidate => candidate?.kind === "microservice")
        .sort((left, right) => (
          (layerRank.get(right.layer) ?? -1) - (layerRank.get(left.layer) ?? -1)
          || left.name.localeCompare(right.name)
        ))[0];
    }
    function architectureLayerForNode(node) {
      const data = nodeDataById.get(node);
      if (data?.layer_label || data?.layer) return data.layer_label || data.layer;
      const owner = preferredOwnerForNode(node);
      return owner?.layer_label || owner?.layer || "Unknown";
    }
    function normalizeClusterPath(path) {
      const normalized = String(path || "root").replace(/^\/+|\/+$/g, "");
      return !normalized || normalized === "ROOT" ? "root" : normalized;
    }
    function visibleClusterPaths() {
      const paths = new Set(["root"]);
      network.nodes().filter(node => isVisibleNodeId(node)).forEach(node => {
        const path = normalizeClusterPath(namespaceForNode(node));
        if (path === "root") return;
        const segments = path.split("/").filter(Boolean);
        segments.forEach((_segment, index) => paths.add(segments.slice(0, index + 1).join("/")));
      });
      return paths;
    }
    function clusterDescriptorForPath(path) {
      const clusterPath = normalizeClusterPath(path);
      const exactPaths = new Map(network.nodes()
        .filter(node => isVisibleNodeId(node))
        .map(node => [node, normalizeClusterPath(namespaceForNode(node))]));
      const ids = [...exactPaths.entries()]
        .filter(([, nodePath]) => nodePath === clusterPath)
        .map(([id]) => id);
      const childPaths = new Set();
      visibleClusterPaths().forEach(candidate => {
        if (candidate === "root" || candidate === clusterPath) return;
        if (clusterPath === "root") {
          childPaths.add(candidate.split("/")[0]);
          return;
        }
        if (!candidate.startsWith(`${clusterPath}/`)) return;
        const nextSegment = candidate.slice(clusterPath.length + 1).split("/")[0];
        childPaths.add(`${clusterPath}/${nextSegment}`);
      });
      const parentPath = clusterPath === "root"
        ? null
        : clusterPath.includes("/")
          ? clusterPath.slice(0, clusterPath.lastIndexOf("/"))
          : "root";
      return {
        key: `cluster:${clusterPath}`,
        kind: "cluster",
        name: clusterPath === "root" ? "ROOT" : clusterPath,
        path: clusterPath,
        parentPath,
        childPaths: [...childPaths].sort((left, right) => left.localeCompare(right)),
        ids,
      };
    }
    function clusterPathForNode(node) {
      const data = nodeDataById.get(node);
      const owner = ownerCandidatesForNode(node)
        .map(candidate => nodeDataById.get(candidate))
        .find(candidate => candidate?.project_namespace_path || candidate?.project_namespace);
      const path = data?.project_namespace_path
        || data?.architecture_namespace_path
        || owner?.project_namespace_path
        || owner?.architecture_namespace_path
        || namespaceForNode(node);
