// Ordered source module: 10-rebuild.js
    function layoutGraphNodes(nodes, links) {
      const layoutNodes = nodes.map((node, index) => {
        const angle = (Math.PI * 2 * index) / Math.max(1, nodes.length);
        return { ...node, x: Math.cos(angle), y: Math.sin(angle), vx: 0, vy: 0 };
      });
      const layoutById = new Map(layoutNodes.map(node => [node.id, node]));
      // The layout is deliberately recomputed from the visible dependencies.
      // This prevents hidden relation types from influencing node positions.
      for (let iteration = 0; iteration < 720; iteration += 1) {
        const cooling = .14 * (1 - iteration / 720) + .015;
        for (let i = 0; i < layoutNodes.length; i += 1) {
          for (let j = i + 1; j < layoutNodes.length; j += 1) {
            const a = layoutNodes[i], b = layoutNodes[j];
            const dx = b.x - a.x || (i < j ? .001 : -.001);
            const dy = b.y - a.y || .001;
            const distance2 = dx * dx + dy * dy + .012;
            const strength = 1.25 / distance2;
            a.vx -= dx * strength; a.vy -= dy * strength;
            b.vx += dx * strength; b.vy += dy * strength;
          }
        }
        links.forEach(link => {
          const source = layoutById.get(link.source), target = layoutById.get(link.target);
          if (!source || !target) return;
          const dx = target.x - source.x, dy = target.y - source.y;
          const distance = Math.hypot(dx, dy) || .001;
          // Leave room for the labels on either side of a relation. The
          // browser camera fits the resulting graph, so this only improves
          // readability instead of making a large graph harder to navigate.
          const desired = ["kafka", "request_reply"].includes(link.kind) ? 1.28 : link.kind === "mongodb" ? .86 : 1.02;
          const pull = (distance - desired) * .035;
          const ux = dx / distance, uy = dy / distance;
          source.vx += ux * pull; source.vy += uy * pull;
          target.vx -= ux * pull; target.vy -= uy * pull;
        });
        layoutNodes.forEach(node => {
          node.vx += -node.x * .008; node.vy += -node.y * .008;
          node.x += node.vx * cooling; node.y += node.vy * cooling;
          node.vx *= .72; node.vy *= .72;
        });
      }
      // Preserve the architectural reading after the force calculation:
      // each software layer occupies a horizontal row, with domain at the
      // bottom. Resources without a layer follow the services they connect.
      const layerOrder = ["api", "application", "orchestration", "infrastructure", "domain", "persistence", "external"];
      const cardSpacingX = 4.0;
      const cardSpacingY = 2.6;
      const nodesByLayer = new Map(layerOrder.map(layer => [layer, []]));
      const resourcesByLayer = new Map(layerOrder.map(layer => [layer, []]));
      const layerForNode = node => {
        if (node?.architecture_layer && nodesByLayer.has(node.architecture_layer)) return node.architecture_layer;
        if (node?.kind === "microservice" && nodesByLayer.has(node.layer)) return node.layer;
        const relatedLayers = links
          .filter(link => link.source === node?.id || link.target === node?.id)
          .map(link => layoutById.get(link.source === node.id ? link.target : link.source)?.layer)
          .filter(layer => nodesByLayer.has(layer));
        return relatedLayers[0] || "application";
      };
      layoutNodes.forEach(node => {
        if (node.kind === "microservice" && nodesByLayer.has(node.layer)) nodesByLayer.get(node.layer).push(node);
        else resourcesByLayer.get(layerForNode(node)).push(node);
      });
      let layerCursor = 0;
      nodesByLayer.forEach((items, layer) => {
        const serviceColumns = Math.min(6, Math.max(1, items.length));
        const resourceItems = resourcesByLayer.get(layer) || [];
        const resourceColumns = Math.min(4, Math.max(1, resourceItems.length));
        const serviceRows = Math.max(1, Math.ceil(items.length / serviceColumns));
        const resourceRows = resourceItems.length ? Math.ceil(resourceItems.length / resourceColumns) : 0;
        const rows = Math.max(serviceRows, resourceRows);
        const layerHeight = Math.max(3.8, (rows - 1) * cardSpacingY + 3.8);
        const centerY = -(layerCursor + layerHeight / 2);
        items.sort((left, right) => left.name.localeCompare(right.name));
        items.forEach((node, index) => {
          const column = index % serviceColumns;
          const row = Math.floor(index / serviceColumns);
          node.x = (column - (serviceColumns - 1) / 2) * cardSpacingX;
          node.y = centerY + (row - (serviceRows - 1) / 2) * cardSpacingY;
        });
        resourceItems.sort((left, right) => left.name.localeCompare(right.name));
        resourceItems.forEach((node, index) => {
          const column = index % resourceColumns;
          const row = Math.floor(index / resourceColumns);
          const serviceRight = ((serviceColumns - 1) / 2) * cardSpacingX;
          node.x = serviceRight + 4.8 + column * cardSpacingX;
          node.y = centerY + (row - (resourceRows - 1) / 2) * cardSpacingY;
        });
        layerCursor += layerHeight + 1.1;
      });
      const verticalOffset = layerCursor / 2;
      layoutNodes.forEach(node => { node.y += verticalOffset; });
      return layoutNodes;
    }
    function layoutIsolatedNodes(nodes, connectedNodes) {
      if (!nodes.length) return [];
      const startX = connectedNodes.length
        ? Math.max(...connectedNodes.map(node => node.x)) + 1.8
        : 0;
      // The HTML cards are fixed-size rectangles. Six columns keep the
      // default overview readable for medium/large graphs while preserving a
      // deterministic envelope that the camera can fit reliably.
      const columns = Math.min(6, Math.max(1, Math.ceil(Math.sqrt(nodes.length / 1.7))));
      return nodes.map((node, index) => ({
        ...node,
        // Keep the initial graph layout in the same coordinate envelope as
        // the fixed-size HTML cards. A sub-unit grid makes Sigma fit several
        // cards into the same CSS rectangle before any user interaction.
        x: startX + (index % columns) * 3.5,
        y: (Math.floor(index / columns) - (Math.ceil(nodes.length / columns) - 1) / 2) * 3.5,
        isolated: true,
      }));
    }
    function rebuildGraph() {
      const callGraphOnly = Boolean(graphState.selectedCodeFlowId);
      const visibleLinks = callGraphOnly ? [] : graphData.links.filter(link => (
        isVisibleRelation(link)
        && isVisibleNode(nodeDataById.get(link.source))
        && isVisibleNode(nodeDataById.get(link.target))
      ));
      const visibleNodeIds = new Set(visibleLinks.flatMap(link => [link.source, link.target]));
      const filteredNodes = graphData.nodes.filter(node => (
        isVisibleNode(node)
        && (!callGraphOnly || (
          node.kind === "microservice" && graphState.relatedNodes?.has(node.id)
        ))
      ));
      const connectedNodes = filteredNodes.filter(node => visibleNodeIds.has(node.id));
      // Keep visible services in the layout even when their only relations
      // point to a service hidden by the selected layer/namespace filter.
      const isolatedNodes = filteredNodes.filter(node => !visibleNodeIds.has(node.id));
      const positionedConnectedNodes = layoutGraphNodes(connectedNodes, visibleLinks);
      const layoutNodes = [
        ...positionedConnectedNodes,
        ...layoutIsolatedNodes(isolatedNodes, positionedConnectedNodes),
      ];
      graphState.graphPanCleanup?.();
      graphState.graphPanCleanup = null;
      graphState.graphWheelCleanup?.();
      graphState.graphWheelCleanup = null;
      // A filtered/layout graph has a new coordinate system. Never compare
      // its camera with a safe state captured from the previous graph.
      graphState.cameraFitAdjusting = false;
      graphState.lastSafeCameraState = null;
      graphState.clusterLayoutPositions = new Map();
      graphLayersOverlay.replaceChildren();
      graphGroupsOverlay.replaceChildren();
      portPathOverlay.replaceChildren();
      nodeLabelOverlay.replaceChildren();
      flowTooltipOverlay.replaceChildren();
      renderer?.kill();
      network = new graphology.MultiDirectedGraph();
      const selectedEndpointIds = new Set(
        graphData.links.flatMap((topologyLink, index) => (
          graphState.relatedEdges?.has(`edge-${index}`)
            ? (topologyLink.endpoint_ids || [])
            : []
        ))
      );
      const endpointOwners = new Map(
        graphData.nodes.flatMap(node => (
          node.kind === "microservice"
            ? (node.ports || []).map(port => [port.endpoint_id, node.id])
            : []
        ))
      );
      const selectedCallGraphLinks = callGraphOnly
        ? [
          ...graphData.links
          .map((link, index) => ({ link, index }))
          .filter(({ link, index }) => (
            graphState.relatedEdges?.has(`edge-${index}`)
            && [link.source, link.target].every(nodeId => nodeDataById.get(nodeId)?.kind === "microservice")
          )),
          ...(graphData.port_links || [])
            .filter(link => selectedEndpointIds.has(link.source_endpoint_id)
              && selectedEndpointIds.has(link.target_endpoint_id))
            .map((link, index) => ({
              link: {
                source: endpointOwners.get(link.source_endpoint_id),
                target: endpointOwners.get(link.target_endpoint_id),
                kind: link.kind,
                label: link.kind === "rest" ? "HTTP" : "Topic",
                endpoint_ids: [link.source_endpoint_id, link.target_endpoint_id],
              },
              index: `port-${index}`,
              edgeKey: `call-edge-${index}`,
            }))
            .filter(({ link }) => link.source && link.target && link.source !== link.target),
        ]
        : [];
      const callGraphEdgeKeys = new Set(selectedCallGraphLinks.map(({ index, edgeKey }) => edgeKey || `edge-${index}`));
      const visualNodeKind = node => {
        if (node.kind === "data_schema") return "mongodb_collection";
        if (node.kind === "message_channel") return "kafka_topic";
        return node.kind;
      };
      layoutNodes.forEach(node => network.addNode(node.id, {
        // The readable geometry is rendered by the HTML card overlay; Sigma
        // keeps its compact node marker underneath it.
        // Sigma's layout algorithms use this radius for collision solving.
        // It is deliberately larger than the tiny canvas marker because the
        // visible object is the fixed-size HTML card above it.
        label: "", x: node.x, y: node.y, size: 18, color: node.color,
        type: node.external ? "external_microservice"
          : ["microservice", "kafka_topic", "mongodb_collection"].includes(node.kind)
            ? node.kind : visualNodeKind(node) === "kafka_topic" || visualNodeKind(node) === "mongodb_collection"
              ? visualNodeKind(node) : "generic",
      }));
      visibleLinks.forEach((link, index) => network.addEdgeWithKey(`edge-${index}`, link.source, link.target, {
        label: link.label, size: .85, color: relationColor(link), kind: link.kind, type: "arrow",
      }));
      selectedCallGraphLinks.forEach(({ link, index, edgeKey }) => network.addEdgeWithKey(edgeKey || `edge-${index}`, link.source, link.target, {
        label: link.label, size: 1.8, color: relationColor(link), kind: link.kind,
        type: "arrow",
      }));
      initialNodePositions = new Map();
      network.forEachNode((node, attributes) => initialNodePositions.set(node, { x: attributes.x, y: attributes.y }));
      renderer = new Sigma(network, document.getElementById("graph"), {
        labelColor: { color: document.documentElement.dataset.theme === "dark" ? "#dce8f7" : "#172033" },
        nodeProgramClasses: {
          microservice: createNodeProgram(MICROSERVICE_FRAGMENT_SHADER),
          external_microservice: createNodeProgram(EXTERNAL_MICROSERVICE_FRAGMENT_SHADER),
          kafka_topic: createNodeProgram(KAFKA_TOPIC_FRAGMENT_SHADER),
          mongodb_collection: createNodeProgram(MONGODB_COLLECTION_FRAGMENT_SHADER),
          generic: createNodeProgram(MICROSERVICE_FRAGMENT_SHADER),
        },
        // Sigma uses a separate hover/picking layer. Its default hover
        // renderer is circular, so use the same rectangular programs here.
        nodeHoverProgramClasses: {
          microservice: createNodeProgram(MICROSERVICE_FRAGMENT_SHADER),
          external_microservice: createNodeProgram(EXTERNAL_MICROSERVICE_FRAGMENT_SHADER),
          kafka_topic: createNodeProgram(KAFKA_TOPIC_FRAGMENT_SHADER),
          mongodb_collection: createNodeProgram(MONGODB_COLLECTION_FRAGMENT_SHADER),
          generic: createNodeProgram(MICROSERVICE_FRAGMENT_SHADER),
        },
        // Keep clicks enabled while disabling Sigma's circular hover overlay;
        // selection feedback is rendered by the HTML card glow instead.
        enableNodeHoverEvents: true,
        hoverRenderer: () => {},
        renderEdgeLabels: false, labelDensity: .06, labelGridCellSize: 160, labelRenderedSizeThreshold: 10,
        // A drag can end close enough to a second click to trigger Sigma's
        // double-click zoom. Zoom remains available through the wheel and the
        // explicit controls, so disable this ambiguous gesture.
        doubleClickZoomingRatio: 1,
        // The shared wheel handler below keeps canvas and HTML-card zoom
        // behavior identical. Native Sigma wheel zoom would only receive
        // events whose target is the canvas.
        enableCameraZooming: false,
        // Panning is handled by the shared camera controller below. Disable
        // Sigma's native inertia so releasing the pointer cannot continue the
        // gesture with an unexpected drift.
        enableCameraPanning: false,
        inertiaDuration: 0,
        inertiaRatio: 0,
        labelAlignment: "center",
        nodeReducer: (node, data) => {
          if (!isVisibleNodeId(node)) return { ...data, hidden: true, label: "" };
          if (graphState.selectedCodeFlowId && nodeDataById.get(node)?.kind !== "microservice") {
            return { ...data, hidden: true, label: "" };
          }
          if (graphState.selectedCodeFlowId && !graphState.relatedNodes?.has(node)) {
            return { ...data, hidden: true, label: "" };
          }
          const renderedData = graphState.renderMode === "symbols" ? { ...data, size: .5 } : data;
          if (graphState.selectedCodeFlowId && graphState.relatedNodes.has(node)) {
            return {
              ...renderedData,
              size: renderedData.size * 1.22,
              highlighted: true,
              zIndex: 2,
            };
          }
          if (graphState.selectedCodeFlowId) return renderedData;
          if (!graphState.selectedId || graphState.relatedNodes.has(node)) {
            return renderedData;
          }
          return { ...renderedData, color: "#d8e0ea", label: "" };
        },
        edgeReducer: (edge, data) => {
          if (!isVisibleNodeId(network.source(edge)) || !isVisibleNodeId(network.target(edge))) return { ...data, hidden: true };
          if (graphState.selectedCodeFlowId) {
            return (graphState.relatedEdges.has(edge) || callGraphEdgeKeys.has(edge)) && !data.obstacleRouted
              ? { ...data, size: 2.1 }
              : { ...data, hidden: true };
          }
          if (graphState.selectedId && graphState.relatedEdges.has(edge)) return { ...data, size: 1.5 };
          if (graphState.clusteredView || graphState.layeredClusterView) return { ...data, size: .5 };
          if (graphState.selectedId) return { ...data, color: "#d8dee9", size: .35 };
          return data;
        },
      });
      function adaptiveSymbolLabelPlacements(nodePoints) {
        if (graphState.renderMode !== "symbols" || !nodePoints.size) return new Map();
        const viewport = graphCanvas.getBoundingClientRect();
        const cameraRatio = Math.max(.01, renderer.getCamera().getState().ratio || 1);
        const zoomDensity = Math.max(.7, Math.min(3, 1 / cameraRatio));
        const labelLimit = Math.max(4, Math.floor(
          viewport.width * viewport.height / 30000 * zoomDensity
        ));
        const candidates = [...nodePoints.entries()].map(([id, point]) => {
          const node = nodeDataById.get(id);
          const forced = id === graphState.selectedId || id === graphState.hoveredId;
          const kindPriority = node?.kind === "microservice" ? 3
            : ["kafka_topic", "message_channel"].includes(node?.kind) ? 2
              : 1;
          return {
            id, point, node, forced,
            score: network.degree(id) * 100 + kindPriority * 10 + Number(node?.complexity?.score || 0),
          };
        }).sort((left, right) => (
          Number(right.forced) - Number(left.forced)
          || right.score - left.score
          || String(left.node?.name || left.id).localeCompare(String(right.node?.name || right.id))
        ));
        const occupied = [];
        const placements = new Map();
        const measureContext = document.createElement("canvas").getContext("2d");
        if (measureContext) measureContext.font = "750 10px system-ui, -apple-system, BlinkMacSystemFont, sans-serif";
        const cardScale = GRAPH_CARD_SCALE;
        const labelOffset = 34 * cardScale;
        const overlaps = (left, right, gap = 3) => (
          left.left < right.right + gap && left.right + gap > right.left
          && left.top < right.bottom + gap && left.bottom + gap > right.top
        );
        const symbolRects = [...nodePoints.entries()].map(([id, point]) => ({
          id, left: point.x - 15, right: point.x + 15,
          top: point.y - 15, bottom: point.y + 15,
        }));
        candidates.forEach(candidate => {
          if (!candidate.forced && placements.size >= labelLimit) return;
          const labelText = String(candidate.node?.name || candidate.id);
          const textWidth = Math.max(44, (measureContext?.measureText(labelText).width || labelText.length * 7) + 8) * cardScale;
          const textHalfHeight = 9 * cardScale;
          const sides = candidate.point.x + labelOffset + textWidth <= viewport.width - 4
            ? ["right", "left"] : ["left", "right"];
          const options = sides.map(side => ({
            side,
            left: side === "right" ? candidate.point.x + labelOffset : candidate.point.x - labelOffset - textWidth,
            right: side === "right" ? candidate.point.x + labelOffset + textWidth : candidate.point.x - labelOffset,
            top: candidate.point.y - textHalfHeight,
            bottom: candidate.point.y + textHalfHeight,
          }));
          const placement = options.find(box => (
            box.left >= 4 && box.right <= viewport.width - 4
            && box.top >= 4 && box.bottom <= viewport.height - 4
            && !occupied.some(other => overlaps(box, other))
            && !symbolRects.some(symbol => symbol.id !== candidate.id && overlaps(box, symbol, 2))
          ));
          if (!placement && !candidate.forced) return;
          const selected = placement || options[0];
          placements.set(candidate.id, selected.side);
          occupied.push(selected);
        });
        return placements;
      }
      let libavoidRoutes = new Map();
      let libavoidRouteKey = "";
      let libavoidRequestId = 0;
      let libavoidUnavailableLogged = false;
      const routeWithLibavoid = (routeKey, routeGraph) => {
        if (!routeGraph.edges.length || routeKey === libavoidRouteKey) return;
        libavoidRouteKey = routeKey;
        const requestId = ++libavoidRequestId;
        libavoidLibrary.then(async libavoid => {
          if (!libavoid?.routeEdges) throw new Error("libavoid indisponible");
          await libavoid.init(
            "https://cdn.jsdelivr.net/npm/libavoid-js@0.5.0-beta.5/dist/libavoid.wasm"
          );
          return libavoid.routeEdges(routeGraph, {
            routingType: "orthogonal",
            shapeBufferDistance: 14,
            idealNudgingDistance: 18,
            crossingPenalty: 1000,
            fixedSharedPathPenalty: 100,
            nudgeOrthogonalSegmentsConnectedToShapes: true,
            nudgeOrthogonalTouchingColinearSegments: true,
            nudgeSharedPathsWithCommonEndPoint: true,
          });
        }).then(routes => {
          if (requestId !== libavoidRequestId) return;
          libavoidRoutes = routes;
          renderer.refresh();
          requestGraphRender();
        }).catch(error => {
          if (!libavoidUnavailableLogged) {
            console.warn("Impossible de charger libavoid ; routage orthogonal de repli utilisé.", error);
            libavoidUnavailableLogged = true;
          }
        });
      };
      renderOverlays = () => {
        nodeLabelOverlay.classList.toggle("is-symbol-mode", graphState.renderMode === "symbols");
        portPathOverlay.classList.toggle("is-symbol-mode", graphState.renderMode === "symbols");
        const nodePoints = new Map();
        const graphPointToViewport = graphPoint => {
          return renderer.graphToViewport(graphPoint);
        };
        network.forEachNode((id, attributes) => {
          if (
            !isVisibleNodeId(id)
            || attributes.hidden
            || (graphState.selectedCodeFlowId && !graphState.relatedNodes?.has(id))
          ) return;
          // graphToViewport is Sigma's public conversion and includes its
          // current camera, normalization and aspect-ratio handling. The
          // overlays use the same workspace rectangle as the renderer, so
          // these local coordinates are directly usable by the cards/groups.
          const point = graphPointToViewport({ x: attributes.x, y: attributes.y });
          const node = nodeDataById.get(id);
          if (!node || !point) return;
          nodePoints.set(id, point);
        });
        const adaptiveLabels = adaptiveSymbolLabelPlacements(nodePoints);
        graphCanvas.dataset.adaptiveLabelCount = String(adaptiveLabels.size);
        // Card dimensions stay constant. Overlap is an accepted overview
        // state; zoom and pan must never change card size or fight the camera.
        graphGroupsOverlay.replaceChildren();
        nodeLabelOverlay.replaceChildren();
        const layerOrder = ["api", "application", "orchestration", "infrastructure", "domain", "persistence", "external"];
        if (!graphState.layeredView && !graphState.clusteredView) {
          graphLayersOverlay.replaceChildren();
          graphGroupsOverlay.replaceChildren();
        } else {
        graphLayersOverlay.replaceChildren();
        const layerColors = { external: "#64748b", api: "#0891b2", application: "#2563eb", orchestration: "#9333ea", infrastructure: "#d97706", shared: "#64748b", module: "#475569", domain: "#7c3aed", persistence: "#0f766e" };
        const visibleServices = [...nodePoints.entries()]
          .map(([id, point]) => ({ id, point, node: nodeDataById.get(id) }))
          .filter(item => item.node?.kind === "microservice" && layeredLayerForNode(item.id));
        const visibleLayerItems = graphState.layeredClusterView
          ? [...nodePoints.entries()]
            .map(([id, point]) => ({ id, point, node: nodeDataById.get(id) }))
            .filter(item => layeredLayerForNode(item.id))
          : visibleServices;
        const layers = layerOrder
          .map(id => ({ id, items: visibleLayerItems.filter(item => layeredLayerForNode(item.id) === id) }))
          .filter(layer => layer.items.length);
        const layerCenters = layers.map(layer => ({
          ...layer,
          center: layer.items.reduce((sum, item) => sum + item.point.y, 0) / layer.items.length,
        }));
        const pointsByLayer = new Map(layerCenters.map(layer => [layer.id, layer.items.map(item => item.point)]));
        if (!nodePoints.size) {
          graphLayersOverlay.replaceChildren();
          graphGroupsOverlay.replaceChildren();
          return;
        }
        if (!graphState.layeredClusterView) {
          [...nodePoints.entries()]
            .filter(([id]) => nodeDataById.get(id)?.kind !== "microservice")
            .forEach(([id, point]) => {
              if (!layerCenters.length) return;
              const nearest = layerCenters.reduce((best, layer) => (
                Math.abs(layer.center - point.y) < Math.abs(best.center - point.y) ? layer : best
              ), layerCenters[0]);
              if (nearest) {
                pointsByLayer.get(nearest.id).push(point);
              }
            });
        }
        const allLayerPoints = graphState.layeredClusterView
          ? [...nodePoints.values()]
          : [...pointsByLayer.values()].flat();
        // Reserve a left gutter inside every layer band for its title.  The
        // module rectangles keep their graph-space positions, while the
        // widened band starts earlier so the overlay title never sits on top
        // of the first module.
        const layerTitleGutter = 182;
        const contentMinX = allLayerPoints.length
          ? Math.min(...allLayerPoints.map(point => point.x)) - 92
          : 0;
        const contentMaxX = allLayerPoints.length
          ? Math.max(...allLayerPoints.map(point => point.x)) + 92
          : window.innerWidth;
        // ELK can retain an empty layer shell when filters remove all of its
        // nodes. Never feed empty point sets to Math.min/Math.max: Infinity
        // collapses the band geometry to the top edge and makes every band
        // overlap after projection.
        const renderedLayerCenters = layerCenters.filter(layer => (
          (pointsByLayer.get(layer.id) || []).length > 0
        ));
        const layerBounds = renderedLayerCenters.map(layer => {
          const points = pointsByLayer.get(layer.id) || [];
          return {
            ...layer,
            contentTop: Math.min(...points.map(point => point.y))
              - GRAPH_CARD_HEIGHT / 2 - MODULE_PADDING_TOP - MODULE_GAP,
            contentBottom: Math.max(...points.map(point => point.y))
              + GRAPH_CARD_HEIGHT / 2 + MODULE_PADDING_BOTTOM + MODULE_GAP,
          };
        });
        if (!layerCenters.length) {
          graphLayersOverlay.replaceChildren();
        }
        const layerBands = SystemLensLayerGeometry.computeLayerBands(layerBounds, {
          contentMinX,
          contentMaxX,
          viewportWidth: window.innerWidth,
          titleGutter: layerTitleGutter,
        });
        if (!graphState.clusteredView || graphState.layeredClusterView) renderedLayerCenters.forEach((layer, index) => {
          const bandBounds = layerBands[index];
          const bandHeight = bandBounds.height;
          if (bandHeight < 4) return;
          const band = document.createElement("div");
          band.className = "graph-layer-band";
          band.dataset.layer = layer.id;
          band.style.left = `${bandBounds.left}px`; band.style.top = `${bandBounds.top}px`;
          band.style.width = `${bandBounds.width}px`;
          band.style.height = `${bandHeight}px`;
          band.style.setProperty("--layer-accent", layerColors[layer.id] || "#64748b");
          const title = document.createElement("span"); title.className = "graph-layer-title";
          title.textContent = layer.id.replaceAll("_", " "); band.append(title); graphLayersOverlay.append(band);
        });
        const namespaces = new Map();
        [...nodePoints.entries()].forEach(([id, point]) => {
          const node = nodeDataById.get(id);
          const layer = graphState.clusteredView && !graphState.layeredClusterView
            ? "namespaces"
            : graphState.layeredClusterView
              ? layeredLayerForNode(id)
              : node?.architecture_layer || (node?.kind === "microservice" ? node.layer : null);
          const values = graphState.clusteredView || graphState.layeredClusterView
            ? [namespaceForNode(id)]
            : node?.architecture_namespace
              ? [node.architecture_namespace]
              : [...new Set([...(node?.runtime_namespaces || []), ...(node?.fact_namespaces || [])])];
          values.forEach(namespace => {
            if (!layer || !namespace) return;
            const key = `${layer}:${namespace}`;
            const group = namespaces.get(key) || { layer, namespace, points: [], ids: [] };
            group.points.push(point); group.ids.push(id); namespaces.set(key, group);
          });
        });
        const namespaceBounds = new Map();
        namespaces.forEach(group => {
          let minX = Math.min(...group.points.map(point => point.x)) - 68;
          let maxX = Math.max(...group.points.map(point => point.x)) + 68;
          let minY = Math.min(...group.points.map(point => point.y)) - 44;
          let maxY = Math.max(...group.points.map(point => point.y)) + 44;
          if (graphState.clusteredView || graphState.layeredClusterView) {
            // The cards are HTML rectangles in viewport coordinates. Build
            // the namespace envelope from those projected centers so graph
            // zoom cannot make a module smaller than its visible children.
            // Keep this in sync with .graph-node-card-label's CSS scale.
            // The envelope must contain the rendered HTML card, not Sigma's
            // logical node dimensions.
            const cardHalfWidth = GRAPH_CARD_WIDTH / 2;
            const cardHalfHeight = GRAPH_CARD_HEIGHT / 2;
            minX = Math.min(...group.points.map(point => point.x)) - cardHalfWidth - MODULE_PADDING_X;
            maxX = Math.max(...group.points.map(point => point.x)) + cardHalfWidth + MODULE_PADDING_X;
            minY = Math.min(...group.points.map(point => point.y)) - cardHalfHeight - MODULE_PADDING_TOP;
            maxY = Math.max(...group.points.map(point => point.y)) + cardHalfHeight + MODULE_PADDING_BOTTOM;
          }
          const renderedWidth = Math.max(150, maxX - minX);
          const renderedHeight = Math.max(92, maxY - minY);
          const boundsKey = `${group.layer}:${group.namespace}`;
          const cluster = clusterDescriptorForPath(group.namespace);
          namespaceBounds.set(boundsKey, {
            minX, maxX: minX + renderedWidth, minY, maxY: minY + renderedHeight,
          });
          const box = document.createElement("div");
          box.className = `graph-namespace-group${graphState.selectedClusterKey === cluster.key ? " is-selected" : ""}`;
          box.dataset.namespace = group.namespace;
          box.dataset.layer = group.layer;
          box.dataset.clusterKey = cluster.key;
          box.style.left = `${minX}px`; box.style.top = `${minY}px`;
          box.style.width = `${renderedWidth}px`;
          box.style.height = `${renderedHeight}px`;
          box.style.setProperty("--namespace-accent", layerColors[group.layer] || "#64748b");
          const title = document.createElement("button");
          title.type = "button";
          title.className = "graph-namespace-title";
          title.textContent = group.namespace === "root" ? "ROOT" : group.namespace;
          title.title = `Afficher les éléments du module ${title.textContent}`;
          title.addEventListener("click", event => {
            event.stopPropagation();
            selectCluster(cluster);
          });
          box.append(title);
          graphLayersOverlay.append(box);
        });
        if (!showProjectGroups.checked) return;
        const namespaceGroupEntries = [...namespaces.entries()];
        (graphData.groups || []).forEach(group => {
          const children = group.children || [];
          const childNamespaceBounds = namespaceGroupEntries
            .filter(([, namespaceGroup]) => (
              (!group.namespace
                || namespaceGroup.namespace === group.namespace
                || namespaceGroup.namespace.startsWith(`${group.namespace}/`))
              && namespaceGroup.ids.some(id => children.includes(id))
            ))
            .map(([key]) => namespaceBounds.get(key))
            .filter(Boolean);
          let minX;
          let maxX;
          let minY;
          let maxY;
          if (childNamespaceBounds.length) {
            // A parent group is defined by its rendered children. This keeps
            // nested boxes aligned after zoom/pan and guarantees containment.
            minX = Math.min(...childNamespaceBounds.map(bounds => bounds.minX)) - 22;
            maxX = Math.max(...childNamespaceBounds.map(bounds => bounds.maxX)) + 22;
            minY = Math.min(...childNamespaceBounds.map(bounds => bounds.minY)) - 28;
            maxY = Math.max(...childNamespaceBounds.map(bounds => bounds.maxY)) + 22;
          } else {
            const points = children.map(id => nodePoints.get(id)).filter(Boolean);
            if (points.length < 2) return;
            minX = Math.min(...points.map(point => point.x)) - 68;
            maxX = Math.max(...points.map(point => point.x)) + 68;
            minY = Math.min(...points.map(point => point.y)) - 52;
            maxY = Math.max(...points.map(point => point.y)) + 52;
          }
          const container = document.createElement("div");
          const cluster = clusterDescriptorForPath(group.namespace || group.name || "root");
          container.className = `graph-project-group${graphState.selectedClusterKey === cluster.key ? " is-selected" : ""}`;
          container.dataset.namespaceGroup = group.namespace || group.name || "root";
          container.dataset.clusterKey = cluster.key;
          container.style.left = `${minX}px`;
          container.style.top = `${minY}px`;
          container.style.width = `${Math.max(150, maxX - minX)}px`;
          container.style.height = `${Math.max(120, maxY - minY)}px`;
          container.style.setProperty("--group-accent", "#64748b");
          const title = document.createElement("button");
          title.type = "button";
          title.className = "graph-project-group-title";
          title.textContent = group.name;
          title.title = `Afficher les éléments du module ${group.name}`;
          title.addEventListener("click", event => {
            event.stopPropagation();
            selectCluster(cluster);
          });
          container.append(title);
          graphGroupsOverlay.append(container);
        });
        }
        network.forEachNode((id, attributes) => {
          const point = nodePoints.get(id);
          const node = nodeDataById.get(id);
          if (!node || !point) return;
          if (graphState.selectedCodeFlowId && node.kind !== "microservice") return;
          const label = document.createElement("span");
          const isTopic = node.kind === "message_channel" || node.kind === "kafka_topic";
          const isDatabase = node.kind === "data_schema" || node.kind === "mongodb_collection";
          const isResource = isTopic || isDatabase;
          const adaptiveLabelSide = adaptiveLabels.get(id);
          const isCodeFlowNode = graphState.selectedCodeFlowId && graphState.relatedNodes?.has(id);
          const isCodeFlowRoot = graphState.selectedCodeFlowId && graphState.codeFlowRootNodeId === id;
          const trigger = isCodeFlowRoot ? graphState.codeFlowTrigger : null;
          label.className = `graph-node-card-label${isResource ? " is-resource" : ""}${isTopic ? " is-topic" : ""}${isDatabase ? " is-collection" : ""}${isCodeFlowRoot ? " is-graph-root" : ""}${adaptiveLabelSide ? " has-adaptive-label" : ""}${adaptiveLabelSide === "left" ? " is-label-left" : ""}${graphState.selectedId === id ? " is-selected" : ""}${graphState.hoveredId === id ? " is-hovered" : ""}${graphState.selectedId && graphState.selectedId !== id && graphState.relatedNodes && !graphState.relatedNodes.has(id) ? " is-dimmed" : ""}${isCodeFlowNode ? " is-code-flow-node" : ""}`;
          label.dataset.nodeKind = node.kind;
          label.dataset.nodeId = id;
          const cardScale = GRAPH_CARD_SCALE;
          label.style.setProperty("--graph-card-scale", String(cardScale));
          label.style.left = `${point.x}px`;
          label.style.top = `${point.y}px`;
          // Cards keep one fixed screen-space size in every view.
          label.style.transform = `translate(-50%, -50%) scale(${cardScale})`;
          label.style.setProperty("--card-accent", node.color || "#64748b");
          const icon = document.createElement("span");
          icon.className = `graph-node-card-icon ${isTopic ? "is-topic" : isDatabase ? "is-database" : "is-service"}`;
          const name = document.createElement("span");
          name.className = "graph-node-card-name";
          name.textContent = node.name;
          name.title = node.name;
          const kind = document.createElement("span");
          kind.className = "graph-node-card-kind";
          const kindLabel = node.technology || nodeKindLabel(node);
          kind.textContent = isCodeFlowRoot ? `${kindLabel} · Racine` : kindLabel;
          label.append(icon);
          label.append(name, kind);
          if (isCodeFlowRoot) {
            const rootBadge = document.createElement("span");
            rootBadge.className = "graph-node-root-badge";
            rootBadge.textContent = "Racine";
            rootBadge.title = "Racine du graphe d’appel sélectionné";
            label.append(rootBadge);
          }
          if (trigger?.name) {
            const triggerBadge = document.createElement("span");
            const isHttpTrigger = trigger.kind === "http_entry";
            triggerBadge.className = `graph-node-trigger-badge ${isHttpTrigger ? "is-http" : "is-kafka"}`;
            triggerBadge.textContent = `${isHttpTrigger ? "HTTP" : "Kafka"} · ${trigger.name}`;
            triggerBadge.title = "Déclencheur du graphe d’appel sélectionné";
            label.append(triggerBadge);
          }
          const portsByDirection = { in: [], out: [] };
          if (graphState.selectedCodeFlowId) {
          (node.ports || []).filter(port => port.label).forEach(port => {
            portsByDirection[port.direction]?.push(port);
          });
          Object.entries(portsByDirection).forEach(([portDirection, ports]) => ports.forEach((port, index) => {
            const anchor = document.createElement("span");
            anchor.className = `graph-node-port-reference is-${portDirection}`;
            anchor.dataset.endpointId = port.endpoint_id;
            // Keep the graph anchor compact. The full persisted presentation
            // label (including local outputs on an input) remains in the
            // tooltip and the inspector.
            anchor.style.setProperty("--port-offset", `${(index + 1) / (ports.length + 1) * 100}%`);
            anchor.textContent = String(port.label).split(" ← ", 1)[0];
            const direction = portDirection === "in" ? "Entrée" : "Sortie";
            const showPortTooltip = () => {
              const tooltip = document.createElement("span");
              tooltip.className = "graph-port-tooltip";
              const title = document.createElement("strong");
              title.textContent = `${port.label} — ${direction}`;
              const endpoint = document.createElement("span");
              const isTopicMessage = /kafka|topic|message/i.test(`${port.type} ${port.name}`);
              endpoint.textContent = isTopicMessage
                ? `${portDirection === "in" ? "Message reçu du topic" : "Message envoyé vers le topic"} : ${port.name}`
                : `${port.type} : ${port.name}`;
              const method = document.createElement("code");
              method.textContent = `Méthode Java : ${port.method || "inconnue"}`;
              tooltip.append(title, endpoint, method);
              if (port.message_type) {
                const messageType = document.createElement("code");
                messageType.textContent = `Type Java : ${port.message_type}`;
                tooltip.append(messageType);
              }
              if (port.local_outputs?.length) {
                const localOutputs = document.createElement("span");
                localOutputs.textContent = "Sorties internes mappées :";
                const outputList = document.createElement("ul");
                port.local_outputs.forEach(output => {
                  const item = document.createElement("li");
                  item.textContent = `${output.label} · ${output.type} : ${output.name} · ${output.method}${output.message_type ? ` · Type Java : ${output.message_type}` : ""}`;
                  outputList.append(item);
                });
                tooltip.append(localOutputs, outputList);
              }
              if (port.target) {
                const target = document.createElement("span");
                target.textContent = `Cible résolue : ${port.target.service} · ${port.target.label} · ${port.target.name}`;
                tooltip.append(target);
              }
              flowTooltipOverlay.replaceChildren(tooltip);
              const bounds = anchor.getBoundingClientRect();
              const tooltipBounds = tooltip.getBoundingClientRect();
              tooltip.style.left = `${Math.max(8, Math.min(window.innerWidth - tooltipBounds.width - 8, bounds.left))}px`;
              tooltip.style.top = `${bounds.bottom + tooltipBounds.height + 8 <= window.innerHeight ? bounds.bottom + 8 : Math.max(8, bounds.top - tooltipBounds.height - 8)}px`;
            };
            anchor.addEventListener("pointerenter", showPortTooltip);
            anchor.addEventListener("pointerleave", () => flowTooltipOverlay.replaceChildren());
            label.append(anchor);
          }));
          }
          // Cards sit above Sigma's canvas and therefore normally consume the
          // pointer stream. Pan the camera directly when a drag starts on a
          // card, while preserving a plain click for node selection. Keeping
          // the initial camera state fixed avoids jumps when overlays are
          // rebuilt during the drag.
          let forwardedPointer = null;
          let suppressNextCardClick = false;
          const finishForwardedPointer = event => {
            if (!forwardedPointer) return;
            const wasDrag = forwardedPointer.moved;
            forwardedPointer = null;
            suppressNextCardClick = wasDrag;
            nodeLabelOverlay.style.pointerEvents = "none";
            try { label.releasePointerCapture?.(event.pointerId); } catch (_error) { /* node was rebuilt during the gesture */ }
            window.removeEventListener("pointermove", moveForwardedPointer, true);
            window.removeEventListener("pointerup", finishForwardedPointer, true);
            window.removeEventListener("pointercancel", finishForwardedPointer, true);
            if (!wasDrag) selectNode(id);
          };
          const moveForwardedPointer = event => {
            if (!forwardedPointer || event.pointerId !== forwardedPointer.pointerId) return;
            if (Math.hypot(
              event.clientX - forwardedPointer.startX,
              event.clientY - forwardedPointer.startY,
            ) > 3) forwardedPointer.moved = true;
            if (!forwardedPointer.moved) return;
            const camera = renderer.getCamera();
            const state = forwardedPointer.cameraState;
            const viewport = document.getElementById("graph").getBoundingClientRect();
            const width = Math.max(viewport.width, 1);
            const height = Math.max(viewport.height, 1);
            camera.setState({
              ...state,
              x: state.x - (event.clientX - forwardedPointer.startX) / width,
              y: state.y + (event.clientY - forwardedPointer.startY) / height,
            });
          };
          label.addEventListener("pointerdown", event => {
            if (event.button !== 0 || forwardedPointer) return;
            forwardedPointer = {
              pointerId: event.pointerId,
              startX: event.clientX,
              startY: event.clientY,
              moved: false,
              cameraState: renderer.getCamera().getState(),
            };
            event.preventDefault();
            event.stopPropagation();
            label.setPointerCapture?.(event.pointerId);
            nodeLabelOverlay.style.pointerEvents = "none";
            window.addEventListener("pointermove", moveForwardedPointer, true);
            window.addEventListener("pointerup", finishForwardedPointer, true);
            window.addEventListener("pointercancel", finishForwardedPointer, true);
          });
          // Sigma also listens to the legacy mouse stream. Without stopping
          // it, a card drag would be handled once by our direct pan and once
          // by Sigma's native mouse pan, producing an amplified movement.
          label.addEventListener("mousedown", event => {
            if (event.button !== 0) return;
            event.preventDefault();
            event.stopImmediatePropagation();
          });
          label.addEventListener("click", event => {
            event.stopPropagation();
            if (suppressNextCardClick) {
              suppressNextCardClick = false;
              return;
            }
            selectNode(id);
          });
          nodeLabelOverlay.append(label);
        });
        // The SVG is deliberately projected from the port elements, rather
        // than from graph nodes. This keeps its endpoints attached to the
        // readable port anchors through every pan, zoom, and card scale.
        portPathOverlay.replaceChildren();
        if (graphState.renderMode === "symbols" || !graphState.selectedCodeFlowId) return;
        const svgNamespace = "http://www.w3.org/2000/svg";
        const marker = document.createElementNS(svgNamespace, "marker");
        marker.id = "graph-port-arrow";
        marker.setAttribute("viewBox", "0 0 8 8");
        marker.setAttribute("refX", "7");
        marker.setAttribute("refY", "4");
        marker.setAttribute("markerWidth", "6");
        marker.setAttribute("markerHeight", "6");
        marker.setAttribute("orient", "auto-start-reverse");
        const arrow = document.createElementNS(svgNamespace, "path");
        arrow.setAttribute("d", "M 0 0 L 8 4 L 0 8 z");
        arrow.setAttribute("fill", "context-stroke");
        marker.append(arrow);
        const definitions = document.createElementNS(svgNamespace, "defs");
        definitions.append(marker);
        portPathOverlay.append(definitions);
        const anchorsByEndpointId = new Map(
          [...nodeLabelOverlay.querySelectorAll(".graph-node-port-reference")]
            .map(anchor => [anchor.dataset.endpointId, anchor])
        );
        const overlayBounds = portPathOverlay.getBoundingClientRect();
        const obstacleBounds = [...nodeLabelOverlay.querySelectorAll(".graph-node-card-label")]
          .map(card => {
            const bounds = card.getBoundingClientRect();
            return {
              id: card.dataset.nodeId,
              left: bounds.left - overlayBounds.left,
              top: bounds.top - overlayBounds.top,
              right: bounds.right - overlayBounds.left,
              bottom: bounds.bottom - overlayBounds.top,
            };
          });
        const segmentIsClear = (start, end, obstacles) => obstacles.every(obstacle => {
          if (Math.abs(start[1] - end[1]) < .5) {
            const y = start[1];
            return y <= obstacle.top || y >= obstacle.bottom
              || Math.max(start[0], end[0]) <= obstacle.left
              || Math.min(start[0], end[0]) >= obstacle.right;
          }
          if (Math.abs(start[0] - end[0]) < .5) {
            const x = start[0];
            return x <= obstacle.left || x >= obstacle.right
              || Math.max(start[1], end[1]) <= obstacle.top
              || Math.min(start[1], end[1]) >= obstacle.bottom;
          }
          return false;
        });
        const orthogonalPath = (start, end, sourceId, targetId, occupiedSegments = []) => {
          const padding = 14;
          const viewportMargin = 42;
          const obstacles = obstacleBounds
            .filter(obstacle => ![sourceId, targetId].includes(obstacle.id))
            .map(obstacle => ({
              left: obstacle.left - padding,
              top: obstacle.top - padding,
              right: obstacle.right + padding,
              bottom: obstacle.bottom + padding,
            }));
          const xLanes = [...new Set([
            start[0], end[0], viewportMargin, Math.max(viewportMargin, overlayBounds.width - viewportMargin),
            ...obstacles.flatMap(obstacle => [obstacle.left, obstacle.right]),
          ])];
          const yLanes = [...new Set([
            start[1], end[1], viewportMargin, Math.max(viewportMargin, overlayBounds.height - viewportMargin),
            ...obstacles.flatMap(obstacle => [obstacle.top, obstacle.bottom]),
          ])];
          const candidates = [];
          const addCandidate = points => {
            const compact = points.filter((point, index) => (
              index === 0 || point[0] !== points[index - 1][0] || point[1] !== points[index - 1][1]
            ));
            if (compact.every((point, index) => index === 0 || segmentIsClear(compact[index - 1], point, obstacles))
              && pathIsClear(compact, [], occupiedSegments)) {
              const length = compact.slice(1).reduce((total, point, index) => (
                total + Math.abs(point[0] - compact[index][0]) + Math.abs(point[1] - compact[index][1])
              ), 0);
              const edgeLanePenalty = compact.slice(1, -1).some(point => (
                point[0] < 100 || point[0] > overlayBounds.width - 100
                || point[1] < 100 || point[1] > overlayBounds.height - 100
              )) ? 500 : 0;
              candidates.push({
                points: compact,
                score: length + (compact.length - 2) * 80 + edgeLanePenalty,
              });
            }
          };
          xLanes.forEach(x => addCandidate([start, [x, start[1]], [x, end[1]], end]));
          yLanes.forEach(y => addCandidate([start, [start[0], y], [end[0], y], end]));
          if (!candidates.length) return { d: `M ${start[0]} ${start[1]} L ${end[0]} ${end[1]}`, points: [start, end] };
          const points = candidates.sort((left, right) => left.score - right.score)[0].points;
          return {
            d: points.map((point, index) => `${index ? "L" : "M"} ${point[0]} ${point[1]}`).join(" "),
            points,
          };
        };
        const pointToSegmentDistance = (point, start, end) => {
          const dx = end[0] - start[0];
          const dy = end[1] - start[1];
          const lengthSquared = dx * dx + dy * dy;
          const ratio = lengthSquared ? Math.max(0, Math.min(1, (
            (point[0] - start[0]) * dx + (point[1] - start[1]) * dy
          ) / lengthSquared)) : 0;
          return Math.hypot(
            point[0] - (start[0] + ratio * dx),
            point[1] - (start[1] + ratio * dy),
          );
        };
        const pathIsClear = (points, obstacles, occupiedSegments = []) => {
          for (let index = 1; index < points.length; index += 1) {
            const [start, end] = [points[index - 1], points[index]];
            for (let step = 0; step <= 24; step += 1) {
              const ratio = step / 24;
              const x = start[0] + (end[0] - start[0]) * ratio;
              const y = start[1] + (end[1] - start[1]) * ratio;
              if (obstacles.some(obstacle => (
                x > obstacle.left && x < obstacle.right
                && y > obstacle.top && y < obstacle.bottom
              ))) return false;
            }
            for (let step = 0; step <= 24; step += 1) {
              const ratio = step / 24;
              const point = [
                start[0] + (end[0] - start[0]) * ratio,
                start[1] + (end[1] - start[1]) * ratio,
              ];
              if (occupiedSegments.some(([occupiedStart, occupiedEnd]) => (
                pointToSegmentDistance(point, occupiedStart, occupiedEnd) < 9
              ))) return false;
            }
          }
          return true;
        };
        const hybridPath = (start, end, sourceId, targetId, occupiedSegments = [], forceCurve = false) => {
          const padding = 14;
          const obstacles = obstacleBounds
            .filter(obstacle => ![sourceId, targetId].includes(obstacle.id))
            .map(obstacle => ({
              left: obstacle.left - padding,
              top: obstacle.top - padding,
              right: obstacle.right + padding,
              bottom: obstacle.bottom + padding,
            }));
          if (!forceCurve && pathIsClear([start, end], obstacles, occupiedSegments)) {
            return { d: `M ${start[0]} ${start[1]} L ${end[0]} ${end[1]}`, points: [start, end], obstacleRouted: false };
          }
          const deltaX = end[0] - start[0];
          const deltaY = end[1] - start[1];
          // Keep the handles visibly away from both card borders. A shallow
          // handle pair makes the detour look almost straight even when it
          // technically clears the obstacle.
          const bend = Math.max(72, Math.max(Math.abs(deltaX), Math.abs(deltaY)) * .48);
          const lanes = [...new Set([
            -72, -48, -24, 0, 24, 48, 72,
            ...obstacles.flatMap(obstacle => [obstacle.top - start[1], obstacle.bottom - start[1]]),
            ...obstacles.flatMap(obstacle => [obstacle.left - start[0], obstacle.right - start[0]]),
          ])];
          // A single lane puts both control points on the same line. Around
          // an obstacle that produces a shallow curve which can still graze
          // another card. Keep the endpoint handles long for continuity, but
          // cap the middle deviation so long service arcs stay readable.
          const middleDeviation = Math.min(48, Math.max(24, Math.hypot(deltaX, deltaY) * .12));
          const controlPointSkews = [-.32, -.18, .18, .32].map(factor => factor * middleDeviation);
          const candidates = [];
          const addCurve = (controlPoints, midpoint, lane, skew) => {
            const points = [];
            const cubicPoint = (p0, p1, p2, p3, t) => {
              const inverse = 1 - t;
              return [
                inverse ** 3 * p0[0] + 3 * inverse ** 2 * t * p1[0]
                  + 3 * inverse * t ** 2 * p2[0] + t ** 3 * p3[0],
                inverse ** 3 * p0[1] + 3 * inverse ** 2 * t * p1[1]
                  + 3 * inverse * t ** 2 * p2[1] + t ** 3 * p3[1],
              ];
            };
            for (let step = 0; step <= 32; step += 1) {
              const t = step / 32;
              points.push(cubicPoint(start, controlPoints[0], controlPoints[1], midpoint, t));
            }
            for (let step = 1; step <= 32; step += 1) {
              const t = step / 32;
              points.push(cubicPoint(midpoint, controlPoints[2], controlPoints[3], end, t));
            }
            if (pathIsClear(points, obstacles, occupiedSegments)) {
              candidates.push({
                d: `M ${start[0]} ${start[1]} C ${controlPoints[0][0]} ${controlPoints[0][1]}, ${controlPoints[1][0]} ${controlPoints[1][1]}, ${midpoint[0]} ${midpoint[1]} C ${controlPoints[2][0]} ${controlPoints[2][1]}, ${controlPoints[3][0]} ${controlPoints[3][1]}, ${end[0]} ${end[1]}`,
                points,
                obstacleRouted: true,
                score: Math.abs(lane) + Math.abs(skew) * .35 + bend,
              });
            }
          };
          if (Math.abs(deltaX) >= Math.abs(deltaY)) {
            const direction = Math.sign(deltaX || 1);
            const midpoint = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2];
            lanes.forEach(lane => controlPointSkews.forEach(skew => addCurve(
              [
                [start[0] + direction * bend, start[1]],
                [midpoint[0] - direction * bend * .35, midpoint[1] + lane + skew],
                [midpoint[0] + direction * bend * .35, midpoint[1] + lane + skew],
                [end[0] - direction * bend, end[1]],
              ],
              [midpoint[0], midpoint[1] + lane + skew],
              lane,
              skew,
            )));
          } else {
            const direction = Math.sign(deltaY || 1);
            const midpoint = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2];
            lanes.forEach(lane => controlPointSkews.forEach(skew => addCurve(
              [
                [start[0], start[1] + direction * bend],
                [midpoint[0] + lane + skew, midpoint[1] - direction * bend * .35],
                [midpoint[0] + lane + skew, midpoint[1] + direction * bend * .35],
                [end[0], end[1] - direction * bend],
              ],
              [midpoint[0] + lane + skew, midpoint[1]],
              lane,
              skew,
            )));
          }
          if (candidates.length) return candidates.sort((left, right) => left.score - right.score)[0];
          const orthogonal = orthogonalPath(start, end, sourceId, targetId, occupiedSegments);
          return { d: orthogonal.d, points: orthogonal.points, obstacleRouted: true };
        };
        const occupiedCallGraphSegments = [];
        const libavoidNodes = new Map();
        const libavoidEdges = [];
        const ensureLibavoidNode = (card, bounds, anchor, portId, side) => {
          const nodeId = card.dataset.nodeId;
          if (!libavoidNodes.has(nodeId)) {
            libavoidNodes.set(nodeId, {
              id: nodeId,
              x: bounds.left - overlayBounds.left,
              y: bounds.top - overlayBounds.top,
              width: bounds.width,
              height: bounds.height,
              ports: [],
            });
          }
          const node = libavoidNodes.get(nodeId);
          if (!node.ports.some(port => port.id === portId)) {
            const anchorBounds = (anchor || card).getBoundingClientRect();
            node.ports.push({
              id: portId,
              x: anchorBounds.left + anchorBounds.width / 2 - bounds.left,
              y: anchorBounds.top + anchorBounds.height / 2 - bounds.top,
              width: 1,
              height: 1,
              layoutOptions: { "org.eclipse.elk.port.side": side },
            });
          }
          return nodeId;
        };
        const routePointsToPath = (route, start, end, sourceId, targetId) => {
          if (!route?.sourcePoint || !route?.targetPoint) return null;
          const points = [route.sourcePoint, ...(route.bendPoints || []), route.targetPoint]
            .map(point => [point.x, point.y])
            .filter(point => point.every(Number.isFinite));
          if (points.length < 2) return null;
          points[0] = start;
          points[points.length - 1] = end;
          if (points.length === 2) {
            const obstacles = obstacleBounds
              .filter(obstacle => ![sourceId, targetId].includes(obstacle.id))
              .map(obstacle => ({
                left: obstacle.left - 14,
                top: obstacle.top - 14,
                right: obstacle.right + 14,
                bottom: obstacle.bottom + 14,
              }));
            const laneCandidates = [
              Math.max(start[1], end[1]) + 80,
              Math.min(start[1], end[1]) - 80,
              (start[1] + end[1]) / 2 + 120,
              (start[1] + end[1]) / 2 - 120,
            ].map(lane => Math.max(42, Math.min(overlayBounds.height - 42, lane)));
            const rectangular = laneCandidates.map(lane => [
              start,
              [start[0], lane],
              [end[0], lane],
              end,
            ]).find(candidate => pathIsClear(candidate, obstacles));
            if (rectangular) points.splice(0, points.length, ...rectangular);
          }
          // libavoid has no viewport boundary obstacle and may select y=0 or
          // y=height for a detour. Keep intermediate horizontal lanes inside
          // the readable graph area while preserving the port endpoints.
          const viewportMargin = 28;
          points.slice(1, -1).forEach(point => {
            point[0] = Math.max(viewportMargin, Math.min(overlayBounds.width - viewportMargin, point[0]));
            point[1] = Math.max(viewportMargin, Math.min(overlayBounds.height - viewportMargin, point[1]));
          });
          return {
            d: points.map((point, index) => `${index ? "L" : "M"} ${point[0]} ${point[1]}`).join(" "),
            points,
            obstacleRouted: true,
            router: "libavoid",
          };
        };
        const callGraphPath = (link, edgeKey, index) => {
          const sourceId = link.source;
          const targetId = link.target;
          const source = nodeLabelOverlay.querySelector(`[data-node-id="${CSS.escape(sourceId)}"]`);
          const target = nodeLabelOverlay.querySelector(`[data-node-id="${CSS.escape(targetId)}"]`);
          if (!source || !target) return null;
          const sourceAnchor = (link.endpoint_ids || [])
            .map(endpointId => anchorsByEndpointId.get(endpointId))
            .find(anchor => anchor?.classList.contains("is-out"));
          const targetAnchor = (link.endpoint_ids || [])
            .map(endpointId => anchorsByEndpointId.get(endpointId))
            .find(anchor => anchor?.classList.contains("is-in"));
          const sourceBounds = (sourceAnchor || source).getBoundingClientRect();
          const targetBounds = (targetAnchor || target).getBoundingClientRect();
          const sourceCenter = [sourceBounds.left + sourceBounds.width / 2, sourceBounds.top + sourceBounds.height / 2];
          const targetCenter = [targetBounds.left + targetBounds.width / 2, targetBounds.top + targetBounds.height / 2];
          const deltaX = targetCenter[0] - sourceCenter[0];
          const deltaY = targetCenter[1] - sourceCenter[1];
          const start = Math.abs(deltaX) >= Math.abs(deltaY)
            ? [sourceBounds.right + 6, sourceCenter[1]]
            : [sourceCenter[0], sourceBounds.bottom + 6];
          const end = Math.abs(deltaX) >= Math.abs(deltaY)
            ? [targetBounds.left - 6, targetCenter[1]]
            : [targetCenter[0], targetBounds.top - 6];
          const resolvedEdgeKey = edgeKey || `call-edge-${index}`;
          const sourcePortId = (link.endpoint_ids || []).find(endpointId => (
            anchorsByEndpointId.get(endpointId)?.classList.contains("is-out")
          )) || `${resolvedEdgeKey}-source`;
          const targetPortId = (link.endpoint_ids || []).find(endpointId => (
            anchorsByEndpointId.get(endpointId)?.classList.contains("is-in")
          )) || `${resolvedEdgeKey}-target`;
          const sourceSide = Math.abs(deltaX) >= Math.abs(deltaY) ? "EAST" : "SOUTH";
          const targetSide = Math.abs(deltaX) >= Math.abs(deltaY) ? "WEST" : "NORTH";
          ensureLibavoidNode(source, source.getBoundingClientRect(), sourceAnchor, sourcePortId, sourceSide);
          ensureLibavoidNode(target, target.getBoundingClientRect(), targetAnchor, targetPortId, targetSide);
          libavoidEdges.push({
            id: resolvedEdgeKey,
            source: sourceId,
            target: targetId,
          });
          const startPoint = [start[0] - overlayBounds.left, start[1] - overlayBounds.top];
          const endPoint = [end[0] - overlayBounds.left, end[1] - overlayBounds.top];
          const libavoidRouted = routePointsToPath(
            libavoidRoutes.get(resolvedEdgeKey),
            startPoint,
            endPoint,
            sourceId,
            targetId,
          );
          if (libavoidRouted) {
            return libavoidRouted;
          }
          const routed = orthogonalPath(
            [start[0] - overlayBounds.left, start[1] - overlayBounds.top],
            [end[0] - overlayBounds.left, end[1] - overlayBounds.top],
            sourceId,
            targetId,
            occupiedCallGraphSegments,
          );
          routed.obstacleRouted = true;
          return routed;
        };
        selectedCallGraphLinks.forEach(({ link, index, edgeKey }) => {
          const routed = callGraphPath(link, edgeKey, index);
          if (!routed) return;
          const resolvedEdgeKey = edgeKey || `edge-${index}`;
          const wasRouted = network.getEdgeAttribute(resolvedEdgeKey, "obstacleRouted") === true;
          if (wasRouted !== routed.obstacleRouted) {
            network.setEdgeAttribute(resolvedEdgeKey, "obstacleRouted", routed.obstacleRouted);
            renderer.refresh();
          }
          if (!routed.obstacleRouted) return;
          occupiedCallGraphSegments.push(...routed.points.slice(1).map((point, pointIndex) => (
            [routed.points[pointIndex], point]
          )));
          const path = document.createElementNS(svgNamespace, "path");
          path.classList.add("graph-call-path");
          if (link.kind === "kafka") path.classList.add("is-kafka");
          path.setAttribute("marker-end", "url(#graph-port-arrow)");
          if (routed.router) path.dataset.router = routed.router;
          path.setAttribute("d", routed.d);
          portPathOverlay.append(path);
        });
        routeWithLibavoid(
          [...libavoidEdges].map(edge => `${edge.id}:${edge.source}:${edge.target}`).join("|"),
          {
            id: "call-graph-routing",
            children: [
              ...libavoidNodes.values(),
              // libavoid routes around obstacles but has no viewport bounds.
              // These four virtual shapes keep detours inside the readable
              // SVG area instead of allowing a horizontal lane at its edge.
              { id: "__viewport-top", x: -100, y: -100, width: overlayBounds.width + 200, height: 128 },
              { id: "__viewport-bottom", x: -100, y: overlayBounds.height - 28, width: overlayBounds.width + 200, height: 128 },
              { id: "__viewport-left", x: -100, y: 0, width: 128, height: overlayBounds.height },
              { id: "__viewport-right", x: overlayBounds.width - 28, y: 0, width: 128, height: overlayBounds.height },
            ],
            edges: libavoidEdges,
          },
        );
        const occupiedPortSegments = [];
        const portPath = (source, target) => {
          const sourceBounds = source.getBoundingClientRect();
          const targetBounds = target.getBoundingClientRect();
          // Keep the arrowhead outside the target card. If the path ends
          // exactly on the card border, the card overlay paints over the
          // marker and makes the direction appear to be missing.
          const arrowGap = 6;
          const start = [sourceBounds.right + arrowGap - overlayBounds.left, sourceBounds.top + sourceBounds.height / 2 - overlayBounds.top];
          const end = [targetBounds.left - arrowGap - overlayBounds.left, targetBounds.top + targetBounds.height / 2 - overlayBounds.top];
          const routed = orthogonalPath(
            start,
            end,
            source.closest(".graph-node-card-label")?.dataset.nodeId,
            target.closest(".graph-node-card-label")?.dataset.nodeId,
            occupiedPortSegments,
          );
          occupiedPortSegments.push(...routed.points.slice(1).map((point, index) => (
            [routed.points[index], point]
          )));
          return routed.d;
        };
        const selectedPortLinks = [...new Map((graphData.port_links || []).filter(link => (
          !graphState.selectedCodeFlowId
          && (selectedEndpointIds.has(link.source_endpoint_id)
            || selectedEndpointIds.has(link.target_endpoint_id))
        )).map(link => [
          `${link.kind}:${link.source_endpoint_id}:${link.target_endpoint_id}`,
          link,
        ]))].map(([, link]) => link);
        (graphData.internal_port_links || []).forEach(link => {
          if (
            graphState.selectedCodeFlowId
            && !graphState.relatedLocalPortLinks?.has(
              `${link.input_endpoint_id}:${link.output_endpoint_id}`
            )
          ) return;
          const input = anchorsByEndpointId.get(link.input_endpoint_id);
          const output = anchorsByEndpointId.get(link.output_endpoint_id);
          if (!input?.classList.contains("is-in") || !output?.classList.contains("is-out")) return;
          const path = document.createElementNS(svgNamespace, "path");
          path.classList.add("graph-local-port-path");
          if (graphState.relatedLocalPortLinks?.has(
            `${link.input_endpoint_id}:${link.output_endpoint_id}`
          )) path.classList.add("is-code-flow-path");
          path.setAttribute("marker-end", "url(#graph-port-arrow)");
          path.setAttribute("d", portPath(input, output));
          portPathOverlay.append(path);
        });
        selectedPortLinks.forEach(link => {
          const source = anchorsByEndpointId.get(link.source_endpoint_id);
          const target = anchorsByEndpointId.get(link.target_endpoint_id);
          if (!source?.classList.contains("is-out") || !target?.classList.contains("is-in")) return;
          const path = document.createElementNS(svgNamespace, "path");
          path.classList.add("graph-port-path");
          if (link.kind === "kafka") path.classList.add("is-kafka");
          path.setAttribute("marker-end", "url(#graph-port-arrow)");
          path.setAttribute("d", portPath(source, target));
          portPathOverlay.append(path);
        });
      };
      // Camera updates can fire several times during one drag. Coalesce them
      // into the next animation frame so the canvas and its HTML overlays are
      // repainted from the same camera state. Rebuilding synchronously for
      // every intermediate pan state can briefly expose partial module boxes.
      let labelRefreshScheduled = false;
      const scheduleNodeLabelRefresh = () => {
        if (labelRefreshScheduled) return;
        labelRefreshScheduled = true;
        requestAnimationFrame(() => {
          labelRefreshScheduled = false;
          requestGraphRender();
        });
      };
      renderer.on("afterRender", scheduleNodeLabelRefresh);
      renderer.getCamera().on("updated", scheduleNodeLabelRefresh);
      requestGraphRender();
      renderer.on("enterNode", ({ node }) => { graphState.hoveredId = node; requestGraphRender(); });
      renderer.on("leaveNode", () => { graphState.hoveredId = null; requestGraphRender(); });
      renderer.on("clickNode", ({ node }) => selectNode(node));
      renderer.on("clickStage", reset);
      renderer.on("doubleClickStage", event => event.preventSigmaDefault?.());
      renderer.on("doubleClickNode", event => event.preventSigmaDefault?.());
      const graphCanvas = document.getElementById("graph");
      const handleGraphWheel = event => {
        if (event.target.closest?.(".toolbar, #details")) return;
        event.preventDefault();
        const camera = renderer.getCamera();
        const state = camera.getState();
        const viewport = graphCanvas.getBoundingClientRect();
        const cursor = {
          x: event.clientX - viewport.left,
          y: event.clientY - viewport.top,
        };
        const graphPoint = renderer.viewportToGraph(cursor);
        const delta = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
        const factor = Math.exp(Math.max(-120, Math.min(120, delta)) * .0012);
        const maximumRatio = ["cluster", "elk"].includes(graphState.activeLayout)
          ? graphState.maximumCollisionFreeRatio
          : 100;
        const ratio = Math.max(.01, Math.min(maximumRatio, state.ratio * factor));
        camera.setState({
          ...state,
          // A larger Sigma ratio is a zoom-out. Bound manual wheel changes
          // so one trackpad burst cannot jump across the safe camera state.
          ratio,
        });
        const projected = renderer.graphToViewport(graphPoint);
        const dx = cursor.x - projected.x;
        const dy = cursor.y - projected.y;
        const nextState = camera.getState();
        const width = Math.max(viewport.width, 1);
        const height = Math.max(viewport.height, 1);
        camera.setState({
          ...nextState,
          // Keep the graph point under the cursor fixed while changing scale.
          x: nextState.x - dx / width,
          y: nextState.y + dy / height,
        });
      };
      graphCanvas.addEventListener("wheel", handleGraphWheel, { passive: false });
      nodeLabelOverlay.addEventListener("wheel", handleGraphWheel, { passive: false });
      graphState.graphWheelCleanup = () => {
        graphCanvas.removeEventListener("wheel", handleGraphWheel);
        nodeLabelOverlay.removeEventListener("wheel", handleGraphWheel);
      };
      let graphPan = null;
      const finishGraphPan = event => {
        if (!graphPan || event.pointerId !== graphPan.pointerId) return;
        graphPan = null;
        window.removeEventListener("pointermove", moveGraphPan, true);
        window.removeEventListener("pointerup", finishGraphPan, true);
        window.removeEventListener("pointercancel", finishGraphPan, true);
        try { graphCanvas.releasePointerCapture?.(event.pointerId); } catch (_error) { /* canvas was rebuilt during the gesture */ }
      };
      const moveGraphPan = event => {
        if (!graphPan || event.pointerId !== graphPan.pointerId) return;
        const state = graphPan.cameraState;
        const viewport = graphCanvas.getBoundingClientRect();
        const width = Math.max(viewport.width, 1);
        const height = Math.max(viewport.height, 1);
        renderer.getCamera().setState({
          ...state,
          x: state.x - (event.clientX - graphPan.startX) / width,
          y: state.y + (event.clientY - graphPan.startY) / height,
        });
      };
      const startGraphPan = event => {
        if (event.button !== 0 || event.target.closest(".graph-node-card-label")) return;
        graphPan = {
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          cameraState: renderer.getCamera().getState(),
        };
        event.preventDefault();
        event.stopImmediatePropagation();
        graphCanvas.setPointerCapture?.(event.pointerId);
        window.addEventListener("pointermove", moveGraphPan, true);
        window.addEventListener("pointerup", finishGraphPan, true);
        window.addEventListener("pointercancel", finishGraphPan, true);
      };
      graphCanvas.addEventListener("pointerdown", startGraphPan, true);
      graphState.graphPanCleanup = () => {
        graphCanvas.removeEventListener("pointerdown", startGraphPan, true);
        try { graphCanvas.releasePointerCapture?.(graphPan?.pointerId); } catch (_error) { /* canvas was rebuilt during the gesture */ }
        window.removeEventListener("pointermove", moveGraphPan, true);
        window.removeEventListener("pointerup", finishGraphPan, true);
        window.removeEventListener("pointercancel", finishGraphPan, true);
      };
      graphCanvas.dataset.relationCount = String(visibleLinks.length);
      graphCanvas.dataset.visibleNodeCount = String(layoutNodes.length);
      graphCanvas.dataset.visibleNodeKinds = [...new Set(layoutNodes.map(node => node.kind))].sort().join(",");
      graphCanvas.dataset.invalidCoordinates = String(
        layoutNodes.some(node => !Number.isFinite(node.x) || !Number.isFinite(node.y))
      );
      graphCanvas.setAttribute("aria-label", `Graphe des interactions : ${visibleLinks.length} relations`);
      // Keep Sigma's native camera coordinate system; layout coordinates are
      // centered above so the complete vertical stack stays in view.
      renderer.getCamera().animatedReset({ duration: 0 });
    }
    rebuildGraph();
