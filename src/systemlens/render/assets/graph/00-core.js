// Ordered source module: 00-core.js
    const graphData = JSON.parse(document.getElementById("graph-data").textContent);
    const hiddenMicroservicesStorageKey = "systemlens:hidden-microservices";
    const hiddenTopicsStorageKey = "systemlens:hidden-topics";
    const hiddenMicroservices = new Set((() => {
      try {
        const stored = JSON.parse(localStorage.getItem(hiddenMicroservicesStorageKey) || "[]");
        return Array.isArray(stored) ? stored.filter(value => typeof value === "string") : [];
      } catch (_error) {
        return [];
      }
    })());
    const hiddenTopics = new Set((() => {
      try {
        const stored = JSON.parse(localStorage.getItem(hiddenTopicsStorageKey) || "[]");
        return Array.isArray(stored) ? stored.filter(value => typeof value === "string") : [];
      } catch (_error) {
        return [];
      }
    })());
    function callGraphForFlow(flow) {
      const graph = graphData.call_graphs?.[flow?.call_graph_id]
        || flow?.call_graph
        || graphData.all_flows_call_graph;
      const flowTree = graph?.call_trees_by_flow?.[flow?.id];
      if (!graph || !flowTree) return graph;
      return {
        ...graph,
        call_tree: { ...(graph.call_tree || {}), ...flowTree },
      };
    }
    function callGraphForFlows(flows) {
      const graphs = flows.map(callGraphForFlow).filter(Boolean);
      if (graphs.length < 2) return graphs[0] || null;
      const nodes = [];
      const nodeSet = new Set();
      const edges = [];
      const edgeSet = new Set();
      graphs.forEach(graph => {
        (graph.node_order || graph.nodes || []).forEach(node => {
          if (nodeSet.has(node)) return;
          nodeSet.add(node);
          nodes.push(node);
        });
        (graph.edges || []).forEach(edge => {
          const key = JSON.stringify([
            edge.source,
            edge.target,
            edge.kind,
            edge.label,
            ...(edge.endpoint_ids || []),
          ]);
          if (edgeSet.has(key)) return;
          edgeSet.add(key);
          edges.push(edge);
        });
      });
      return { nodes, node_order: nodes, edges };
    }
    // HTML cards are rendered in screen space. Layouts and collision envelopes
    // must use the same dimensions in every view; only their graph positions
    // change when the camera zooms or pans.
    const GRAPH_CARD_SCALE = 1;
    const GRAPH_CARD_WIDTH = 110 * GRAPH_CARD_SCALE;
    const GRAPH_CARD_HEIGHT = 70 * GRAPH_CARD_SCALE;
    const themeToggle = document.getElementById("theme-toggle");
    const themeStorageKey = "systemlens:graph-theme";
    const storedTheme = (() => {
      try { return localStorage.getItem(themeStorageKey); } catch (_error) { return null; }
    })();
    const preferredTheme = storedTheme === "light" || storedTheme === "dark" ? storedTheme
      : "dark";
    document.documentElement.dataset.theme = preferredTheme;
    function updateThemeToggle() {
      const isDark = document.documentElement.dataset.theme === "dark";
      themeToggle.textContent = isDark ? "☼ Clair" : "☾ Sombre";
      themeToggle.title = isDark ? "Passer au thème clair" : "Passer au thème sombre";
      themeToggle.setAttribute("aria-label", themeToggle.title);
    }
    themeToggle.addEventListener("click", () => {
      const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = theme;
      try { localStorage.setItem(themeStorageKey, theme); } catch (_error) { /* optional preference */ }
      updateThemeToggle();
      // Label colours are style functions of the theme; a refresh re-reads them.
      renderer?.refresh();
    });
    updateThemeToggle();
    const nodeDataById = new Map(graphData.nodes.map(node => [node.id, node]));
    const linkedNodeIds = new Set(graphData.links.flatMap(link => [link.source, link.target]));
    const isolatedNodeIds = new Set(
      graphData.nodes.filter(node => !linkedNodeIds.has(node.id)).map(node => node.id)
    );
    const nodeSuggestions = document.getElementById("node-suggestions");
    const portPathOverlay = document.getElementById("graph-port-paths");
    const nodeLabelOverlay = document.getElementById("graph-node-labels");
    const graphCallTreeOverlay = document.getElementById("graph-call-tree");
    const flowTooltipOverlay = document.getElementById("graph-flow-tooltips");
    const graphFlowStatus = document.getElementById("graph-flow-status");
    // One technology name per resource kind: the kicker, the catalogue and
    // the inspector Architecture group read the same value.
    const nodeTechnology = node => node.technology
      || (node.kind === "kafka_topic" || node.kind === "message_channel" ? "Kafka" : null)
      || (node.kind === "mongodb_collection" ? "MongoDB" : null)
      || (node.kind === "jpa_entity" ? "JPA" : null);
    const nodeKindLabel = node => {
      const genericLabel = node.kind === "kafka_topic"
        ? "Topic"
        : ["mongodb_collection", "data_schema"].includes(node.kind)
          ? "Donnée"
          : node.kind === "jpa_entity"
            ? "Entité JPA"
          : node.kind === "message_channel"
            ? "Message"
            : node.external ? "Microservice externe" : "Microservice";
      // `Entité JPA` already names its technology.
      if (!["kafka_topic", "mongodb_collection", "data_schema", "message_channel"].includes(node.kind)) {
        return genericLabel;
      }
      const technology = nodeTechnology(node);
      return technology ? `${genericLabel} · ${technology}` : genericLabel;
    };
    // One label per resource kind: the catalogue tag, the catalogue preview
    // and the inspector title read the same word (PDR-10). docs/glossary.md
    // lists the same pairs.
    const resourceKindLabels = new Map([
      ["microservice", "Microservice"],
      ["external_microservice", "Microservice externe"],
      ["http_route", "Route HTTP"],
      ["served_route", "Route exposée"],
      ["called_route", "Route appelée"],
      ["openapi_contract", "Contrat OpenAPI"],
      ["asyncapi_contract", "Contrat AsyncAPI"],
      ["kafka_topic", "Topic"],
      ["message_channel", "Message"],
      ["mongodb_collection", "Collection"],
      ["data_schema", "Donnée"],
      ["jpa_entity", "Entité JPA"],
      ["dto", "DTO"],
      ["mongo_persistence_class", "Classe de persistance MongoDB"],
      ["diagnostic", "Diagnostic"],
    ]);
    const resourceKindLabel = kind => resourceKindLabels.get(kind) || "Ressource";
    const nodeResourceKind = node => (
      node.kind === "microservice" && node.external ? "external_microservice" : node.kind
    );
    // Every resource view is titled `<kind label> · <name>`.
    const resourceInspectorTitle = (kind, name) => `${resourceKindLabel(kind)} · ${name}`;
    const nodeKindSuggestion = node => (
      nodeKindLabel(node)
    );
    const nodeDisplayName = node => node.display_name || node.name;
    const appendGraphLabelName = (element, value) => {
      const text = String(value ?? "");
      let start = 0;
      for (let index = 1; index < text.length; index += 1) {
        const previous = text[index - 1];
        const current = text[index];
        const afterSeparator = "._-/".includes(previous);
        const camelCaseBoundary = /[a-z0-9]/.test(previous) && /[A-Z]/.test(current);
        if (!afterSeparator && !camelCaseBoundary) continue;
        element.append(document.createTextNode(text.slice(start, index)));
        element.append(document.createElement("wbr"));
        start = index;
      }
      element.append(document.createTextNode(text.slice(start)));
    };
    graphData.nodes
      .slice()
      .sort((left, right) => nodeDisplayName(left).localeCompare(nodeDisplayName(right)))
      .forEach(node => {
        const option = document.createElement("option");
        option.value = nodeDisplayName(node);
        option.label = nodeKindSuggestion(node);
        nodeSuggestions.append(option);
      });
    const RELATION_COLORS = Object.freeze({
      http: "#D55E00",
      kafkaPublish: "#009E73",
      kafkaConsume: "#0072B2",
      mongodb: "#CC79A7",
      jpa: "#7C3AED",
      build: "#475569",
    });
    function relationColor(link) {
      if (link.kind === "rest") return RELATION_COLORS.http;
      if (link.kind === "build") return RELATION_COLORS.build;
      if (link.kind === "jpa") return RELATION_COLORS.jpa;
      if (link.direction === "incoming") return RELATION_COLORS.kafkaConsume;
      if (link.direction === "data_access") return RELATION_COLORS.mongodb;
      if (link.kind.startsWith("enrichment_") && ["reads", "writes", "uses"].includes(link.label)) return RELATION_COLORS.mongodb;
      return RELATION_COLORS.kafkaPublish;
    }
    let network;
    let renderer;
    let renderOverlays = null;
    let initialNodePositions = new Map();
    const graphState = {
      selectedId: null,
      inspectedId: null,
      dependencyDepth: 1,
      dependencyFocusOnly: false,
      selectedClusterKey: null,
      hoveredId: null,
      relatedNodes: null,
      relatedEdges: null,
      relatedLocalPortLinks: new Set(),
      analysisPortEndpointId: null,
      selectedDiagnosticId: null,
      diagnosticFocusSnapshot: null,
      selectedCallGraphEdgeKey: null,
      analysisContextCollapsed: true,
      pathMicroserviceOrder: new Map(),
      codeFlowTreeCoordinates: new Map(),
      callTreeExpanded: new Set(),
      callTreeCollapsed: new Set(),
      cameraFitAdjusting: false,
      lastSafeCameraState: null,
      graphPanCleanup: null,
      graphWheelCleanup: null,
      activeLayout: "forceatlas2-noverlap",
      // The architecture graph and a selected call graph are different
      // projections of the same snapshot. Keep the projection explicit so a
      // stale selection cannot silently change the graph being rebuilt.
      viewMode: "architecture",
      layoutRequest: 0,
      fitMode: "readable",
      fitRequest: 0,
      renderMode: "cards",
      callGraphDisplayMode: "tree",
      callTreeDepth: 1,
      callTreeZoom: 1,
      callTreeFitComplete: false,
      // Set by a user pan, wheel, zoom control, or minimap move; cleared by
      // every fit. While set, the minimap stays on screen (PDR-6).
      cameraMovedByUser: false,
      callTreeMovedByUser: false,
      callTreeSelectedPathKey: null,
      callTreeVisibleNodeCount: 0,
      callTreeVisibleEdgeCount: 0,
    };
    // One wheel notch (100 pixels, three lines, or one page) scales both
    // graph surfaces, the Architecture graph and the selected call tree, by
    // the same step of about 1.2 around the pointer.
    const GRAPH_WHEEL_ZOOM_RATE = .0018;
    function graphWheelZoomScale(event) {
      const unit = event.deltaMode === 1 ? 100 / 3 : event.deltaMode === 2 ? 100 : 1;
      const delta = event.deltaY * unit;
      return Math.exp(-Math.max(-120, Math.min(120, delta)) * GRAPH_WHEEL_ZOOM_RATE);
    }
    // PDR-14: a pinch (a wheel event with ctrlKey) or a whole-line or page
    // delta (a classic mouse wheel) zooms; any other pixel delta, such as a
    // two-finger trackpad scroll, pans by its deltaX and deltaY. A pixel
    // mouse wheel cannot be told apart from a trackpad, so it pans too.
    function graphWheelGesture(event) {
      if (event.ctrlKey || event.deltaMode !== 0) return { zoom: graphWheelZoomScale(event) };
      return { pan: { x: event.deltaX, y: event.deltaY } };
    }
    function updateGraphState(patch) {
      Object.assign(graphState, patch);
      return graphState;
    }
    function requiredCardZoomIn(targetRenderer) {
      if (!targetRenderer || !network) return 1;
      const symbolMode = graphState.renderMode === "symbols";
      const cardWidth = !symbolMode ? GRAPH_CARD_WIDTH + 4 : 34;
      const cardHeight = !symbolMode ? GRAPH_CARD_HEIGHT + 4 : 34;
      const buckets = new Map();
      const requiredZooms = [];
      network.forEachNode((id, attributes) => {
        if (!isVisibleNodeId(id) || attributes.hidden) return;
        const point = targetRenderer.graphToViewport({ x: attributes.x, y: attributes.y });
        const cellX = Math.floor(point.x / cardWidth);
        const cellY = Math.floor(point.y / cardHeight);
        for (let offsetX = -1; offsetX <= 1; offsetX += 1) {
          for (let offsetY = -1; offsetY <= 1; offsetY += 1) {
            for (const other of buckets.get(`${cellX + offsetX}:${cellY + offsetY}`) || []) {
              const distanceX = Math.abs(point.x - other.x);
              const distanceY = Math.abs(point.y - other.y);
              if (distanceX >= cardWidth || distanceY >= cardHeight) continue;
              // Uniform zoom separates the cards as soon as either axis has
              // enough room. Taking the larger axis factor would over-zoom
              // every pair that is nearly aligned horizontally or vertically.
              const requiredZoom = Math.min(
                cardWidth / Math.max(distanceX, 1),
                cardHeight / Math.max(distanceY, 1),
              );
              requiredZooms.push(Math.min(4, requiredZoom));
            }
          }
        }
        const key = `${cellX}:${cellY}`;
        const bucket = buckets.get(key) || [];
        bucket.push(point);
        buckets.set(key, bucket);
      });
      if (!requiredZooms.length) return 1;
      requiredZooms.sort((left, right) => left - right);
      if (graphState.activeLayout === "forceatlas2-noverlap") {
        return requiredZooms[requiredZooms.length - 1];
      }
      // A single near-coincident pair must not dictate the camera distance for
      // the entire graph. Separating 90% of the initially overlapping pairs
      // gives a stable readable view while leaving local outliers explorable.
      return requiredZooms[Math.floor((requiredZooms.length - 1) * .9)];
    }
    function requiredSmallGraphOverviewRatio(targetRenderer) {
      if (!targetRenderer || !network?.order) return 1;
      const points = network.nodes()
        .filter(id => isVisibleNodeId(id) && !network.getNodeAttribute(id, "hidden"))
        .map(id => {
          const attributes = network.getNodeAttributes(id);
          return targetRenderer.graphToViewport({ x: attributes.x, y: attributes.y });
        });
      if (points.length < 2) return 1;
      const viewport = graphCanvas.getBoundingClientRect();
      const cardWidth = graphState.renderMode === "symbols" ? 34 : GRAPH_CARD_WIDTH + 8;
      const cardHeight = graphState.renderMode === "symbols" ? 34 : GRAPH_CARD_HEIGHT + 8;
      const spanX = Math.max(...points.map(point => point.x)) - Math.min(...points.map(point => point.x));
      const spanY = Math.max(...points.map(point => point.y)) - Math.min(...points.map(point => point.y));
      const availableWidth = Math.max(1, viewport.width - cardWidth);
      const availableHeight = Math.max(1, viewport.height - cardHeight);
      return Math.min(2, Math.max(1, spanX / availableWidth, spanY / availableHeight));
    }
    let renderFrameScheduled = false;
    let renderFramePromise = Promise.resolve();
    function requestGraphRender() {
      if (renderFrameScheduled) return renderFramePromise;
      renderFrameScheduled = true;
      renderFramePromise = new Promise(resolve => {
        requestAnimationFrame(() => {
          renderFrameScheduled = false;
          try {
            renderOverlays?.();
            renderGraphMinimap?.();
          } finally {
            resolve();
          }
        });
      });
      return renderFramePromise;
    }
    const layoutLibraries = Promise.all([
      import("https://esm.sh/graphology-layout-forceatlas2@0.10.1"),
      import("https://esm.sh/graphology-layout-noverlap@0.4.2"),
    ]).then(([forceAtlas2Module, noverlapModule]) => ({
      forceAtlas2: forceAtlas2Module.default,
      noverlap: noverlapModule.default,
    })).catch(error => {
      console.warn("Impossible de charger les dispositions du graphe.", error);
      return null;
    });

    // Sigma invokes reducers while it is constructed, so these controls must
    // exist before creating the renderer.
    const relationHttp = document.getElementById("relation-http");
    const relationKafka = document.getElementById("relation-kafka");
    const relationMongodb = document.getElementById("relation-mongodb");
    const relationOther = document.getElementById("relation-other");
    const nodeMicroservice = document.getElementById("node-microservice");
    const nodeExternalMicroservice = document.getElementById("node-external-microservice");
    const nodeKafkaTopic = document.getElementById("node-kafka-topic");
    const nodeMongodbCollection = document.getElementById("node-mongodb-collection");
    const nodeOther = document.getElementById("node-other");
    function relationCategory(link, source = link.source, target = link.target) {
      const kind = String(link.kind || "").toLocaleLowerCase();
      const label = String(link.label || "").toLocaleLowerCase();
      const endpointKinds = [source, target].map(id => nodeDataById.get(id)?.kind);
      if (kind === "rest" || ["calls", "call", "invokes", "http"].includes(label)) return "http";
      if (kind === "kafka"
          || endpointKinds.some(value => ["kafka_topic", "message_channel"].includes(value))
          || ["publishes", "consumes", "produce", "consume"].includes(label)) return "kafka";
      if (kind === "mongodb" || link.direction === "data_access"
          || endpointKinds.some(value => ["mongodb_collection", "data_schema"].includes(value))
          || ["reads", "writes", "uses"].includes(label)) return "mongodb";
      return "other";
    }
    function isVisibleRelation(link, source = link.source, target = link.target) {
      return {
        http: relationHttp.checked,
        kafka: relationKafka.checked,
        mongodb: relationMongodb.checked,
        other: relationOther.checked,
      }[relationCategory(link, source, target)];
    }
    function isVisibleNode(node) {
      if (!node) return false;
      if (node.kind === "microservice") {
        return !hiddenMicroservices.has(node.name)
          && (node.external ? nodeExternalMicroservice.checked : nodeMicroservice.checked);
      }
      if (["kafka_topic", "message_channel"].includes(node.kind)) {
        return !hiddenTopics.has(node.name) && nodeKafkaTopic.checked;
      }
      if (["mongodb_collection", "data_schema"].includes(node.kind)) return nodeMongodbCollection.checked;
      return nodeOther.checked;
    }
    function isVisibleNodeId(id) { return isVisibleNode(nodeDataById.get(id)); }
    // Sigma 4 compiles one program from declared primitives (ADR-69). The
    // node marker only sits under the fixed-size HTML card, so a filled
    // square is enough; relations use a straight path and an arrow head.
    const SIGMA_ARROW = Sigma.rendering.extremityArrow();
    const SIGMA_PRIMITIVES = {
      nodes: { shapes: [Sigma.rendering.sdfSquare()], layers: [Sigma.rendering.layerFill()] },
      edges: { paths: [Sigma.rendering.pathLine()], extremities: [SIGMA_ARROW] },
    };
    const sigmaLabelColor = () => (
      document.documentElement.dataset.theme === "dark" ? "#dce8f7" : "#172033"
    );
