// Ordered source module: 10-rebuild.js
    let callGraphArcNavigator = { links: [], views: [], activeIndex: null };
    let selectedCallGraphArcVisualTimer = null;
    const clearCallGraphAnalysisFocus = () => {
      graphState.analysisPortEndpointId = null;
      document.querySelectorAll(
        ".graph-call-path.is-analysis-selected, .graph-call-label.is-analysis-selected, "
        + ".graph-local-port-path.is-analysis-selected, .graph-port-path.is-analysis-selected"
      ).forEach(element => element.classList.remove("is-analysis-selected"));
    };
    const clearSelectedCallGraphArc = () => {
      graphState.selectedCallGraphEdgeKey = null;
      callGraphArcNavigator.activeIndex = null;
      document.getElementById("graph")?.removeAttribute("data-selected-call-graph-arc");
      document.querySelectorAll(".graph-call-path, .graph-call-label").forEach(element => {
        element.classList.remove("is-keyboard-selected");
        if (element.classList.contains("graph-call-path")) {
          element.style.removeProperty("stroke");
          element.style.removeProperty("stroke-width");
          element.style.removeProperty("filter");
        }
      });
      syncSelectedCallGraphArcVisual();
      flowTooltipOverlay.replaceChildren();
    };
    const syncSelectedCallGraphArcVisual = () => {
      const edgeKey = document.getElementById("graph")?.dataset.selectedCallGraphArc;
      if (!edgeKey) {
        if (selectedCallGraphArcVisualTimer) {
          clearInterval(selectedCallGraphArcVisualTimer);
          selectedCallGraphArcVisualTimer = null;
        }
        return;
      }
      document.querySelectorAll(".graph-call-path, .graph-call-label").forEach(element => {
        const active = element.dataset.arcKey === edgeKey;
        element.classList.toggle("is-keyboard-selected", active);
        if (element.classList.contains("graph-call-path") && !element.classList.contains("graph-arc-hit-area")) {
          if (active) {
            element.style.setProperty("stroke", "#dc2626");
            element.style.setProperty("stroke-width", "2px");
            element.style.removeProperty("filter");
          } else {
            element.style.removeProperty("stroke");
            element.style.removeProperty("stroke-width");
            element.style.removeProperty("filter");
          }
        }
      });
    };
    const keepSelectedCallGraphArcVisible = () => {
      syncSelectedCallGraphArcVisual();
      if (!document.getElementById("graph")?.dataset.selectedCallGraphArc) return;
      if (!selectedCallGraphArcVisualTimer) {
        selectedCallGraphArcVisualTimer = setInterval(syncSelectedCallGraphArcVisual, 80);
      }
    };
    const callGraphArcVisualObserver = new MutationObserver(syncSelectedCallGraphArcVisual);
    callGraphArcVisualObserver.observe(document.getElementById("graph-port-paths"), { childList: true });
    const navigateVisibleCallGraphArc = direction => {
      const paths = [...document.querySelectorAll(".graph-call-path:not(.graph-arc-hit-area)")];
      const keys = [...new Set(paths.map(path => path.dataset.arcKey).filter(Boolean))];
      if (!keys.length) return;
      clearCallGraphAnalysisFocus();
      const currentIndex = keys.indexOf(graphState.selectedCallGraphEdgeKey);
      const nextIndex = (currentIndex < 0 ? (direction > 0 ? 0 : keys.length - 1) : currentIndex + direction + keys.length) % keys.length;
      const edgeKey = keys[nextIndex];
      graphState.selectedCallGraphEdgeKey = edgeKey;
      const graphCanvas = document.getElementById("graph");
      graphCanvas?.setAttribute("data-selected-call-graph-arc", edgeKey);
      document.querySelectorAll(".graph-call-path, .graph-call-label").forEach(element => {
        element.classList.toggle("is-keyboard-selected", element.dataset.arcKey === edgeKey);
      });
      keepSelectedCallGraphArcVisible();
      const viewIndex = callGraphArcNavigator.views.findIndex(view => view.edgeKey === edgeKey);
      if (viewIndex >= 0) {
        focusCallGraphArc(viewIndex);
      } else {
        requestAnimationFrame(() => {
          const retryIndex = callGraphArcNavigator.views.findIndex(view => view.edgeKey === edgeKey);
          if (retryIndex >= 0) focusCallGraphArc(retryIndex);
        });
      }
    };
    const focusCallGraphArc = index => {
      const { views } = callGraphArcNavigator;
      if (!views.length) return;
      const normalizedIndex = (index + views.length) % views.length;
      const view = views[normalizedIndex];
      if (!view) return;
      clearCallGraphAnalysisFocus();
      callGraphArcNavigator.activeIndex = normalizedIndex;
      graphState.selectedCallGraphEdgeKey = view.edgeKey;
      document.getElementById("graph")?.setAttribute("data-selected-call-graph-arc", view.edgeKey);
      views.forEach(candidate => {
        const active = candidate === view;
        candidate.path.classList.toggle("is-keyboard-selected", active);
        candidate.arcLabel.classList.toggle("is-keyboard-selected", active);
        candidate.hitArea.classList.toggle("is-keyboard-selected", active);
        if (active) {
          candidate.path.style.setProperty("stroke", "#dc2626");
          candidate.path.style.setProperty("stroke-width", "2px");
          candidate.path.style.removeProperty("filter");
        } else {
          candidate.path.style.removeProperty("stroke");
          candidate.path.style.removeProperty("stroke-width");
          candidate.path.style.removeProperty("filter");
        }
      });
      view.showTooltip();
    };
    if (!window.__systemlensCallGraphArcKeyboardNavigation) {
      window.__systemlensCallGraphArcKeyboardNavigation = true;
      window.addEventListener("keydown", event => {
        if (!graphState.selectedCodeFlowId) return;
        if (event.target.closest?.("input, select, textarea, [contenteditable='true']")) return;
        if (event.key === "Escape" && graphState.selectedCallGraphEdgeKey) {
          event.preventDefault();
          clearSelectedCallGraphArc();
        } else if (event.key.toLowerCase() === "n") {
          event.preventDefault();
          navigateVisibleCallGraphArc(1);
        } else if (event.key.toLowerCase() === "p") {
          event.preventDefault();
          navigateVisibleCallGraphArc(-1);
        }
      });
    }
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
          const desired = link.kind === "kafka" ? 1.28 : link.kind === "mongodb" ? .86 : 1.02;
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
      if (graphState.selectedCodeFlowId && graphState.pathMicroserviceOrder?.size) {
        // Call graphs are read as a directed tree/DAG. Use the persisted
        // service edges to place each level horizontally and its branches
        // vertically; fall back to the historical lane for flows without
        // exported call-graph edges.
        const serviceGap = 4.8;
        const resourceGap = 2.4;
        const serviceX = new Map();
        layoutNodes.forEach(node => {
          const treePosition = graphState.codeFlowTreeCoordinates?.get(node.id);
          if (treePosition) {
            node.x = treePosition.x;
            node.y = treePosition.y;
            serviceX.set(node.id, node.x);
            return;
          }
          const order = graphState.pathMicroserviceOrder.get(node.id);
          if (order) {
            serviceX.set(node.id, (order - 1) * serviceGap);
            node.x = (order - 1) * serviceGap;
            node.y = 0;
          }
        });
        layoutNodes.forEach(node => {
          if (serviceX.has(node.id)) return;
          const neighbours = links
            .filter(link => link.source === node.id || link.target === node.id)
            .map(link => serviceX.get(link.source === node.id ? link.target : link.source))
            .filter(value => Number.isFinite(value));
          if (!neighbours.length) return;
          node.x = (Math.min(...neighbours) + Math.max(...neighbours)) / 2;
          if (neighbours.length === 1) node.x += resourceGap;
          node.y = 0;
        });
      }
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
      const callGraphOnly = graphState.viewMode === "call-graph"
        && Boolean(graphState.selectedCodeFlowId);
      if (callGraphOnly && !graphState.selectedCallGraphEdgeKey) {
        graphState.selectedCallGraphEdgeKey = document.getElementById("graph")?.dataset.selectedCallGraphArc || null;
      }
      const selectedFlow = callGraphOnly
        ? (graphData.code_flows || []).find(flow => flow.id === graphState.selectedCodeFlowId)
        : null;
      const selectedCallGraph = callGraphForFlow(selectedFlow);
      const dependencyFocusOnly = graphState.dependencyFocusOnly && graphState.selectedId && !callGraphOnly;
      const visibleLinks = callGraphOnly
        ? []
        : graphData.links.filter((link, index) => (
          isVisibleRelation(link)
          && isVisibleNode(nodeDataById.get(link.source))
          && isVisibleNode(nodeDataById.get(link.target))
          && (!dependencyFocusOnly || graphState.relatedEdges?.has(`edge-${index}`))
        ));
      // Topology service links predate the NetworkX call-graph projection and
      // may not carry the concrete ports used by the selected flow. Restore
      // those endpoint ids from the projected edge so the overlay can route
      // each connector from the exact OUT port to the exact IN port.
      if (callGraphOnly && selectedCallGraph?.edges?.length) {
        const callGraphEndpointsByPair = new Map(
          selectedCallGraph.edges.map(edge => [
            `${edge.source}->${edge.target}`,
            edge.endpoint_ids || [],
          ])
        );
        visibleLinks.forEach(link => {
          const pair = `${nodeDataById.get(link.source)?.name}->${nodeDataById.get(link.target)?.name}`;
          const endpointIds = callGraphEndpointsByPair.get(pair);
          if (endpointIds?.length) link.endpoint_ids = endpointIds;
        });
      }
      const visibleNodeIds = new Set(visibleLinks.flatMap(link => [link.source, link.target]));
      const filteredNodes = graphData.nodes.filter(node => (
        isVisibleNode(node)
        && (!dependencyFocusOnly || graphState.relatedNodes?.has(node.id))
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
      const selectedCallGraphLinks = callGraphOnly
        ? [
          ...(selectedCallGraph?.edges || [])
            .map((edge, index) => ({ edge, index }))
            .sort((left, right) => {
              const leftOrder = Number(left.edge.order);
              const rightOrder = Number(right.edge.order);
              if (leftOrder > 0 && rightOrder > 0) return leftOrder - rightOrder || left.index - right.index;
              if (leftOrder > 0) return -1;
              if (rightOrder > 0) return 1;
              return left.index - right.index;
            })
            .map(({ edge, index }, order) => ({
              order: order + 1,
              link: {
                source: `microservice:${edge.source}`,
                target: `microservice:${edge.target}`,
                kind: edge.kind,
                label: edge.label,
                order: order + 1,
                endpoint_ids: edge.endpoint_ids || [],
              },
              index,
              edgeKey: `call-edge-${index}`,
            }))
            .map(({ order, link, index }) => ({
              link: { ...link, order },
              index,
              edgeKey: `call-edge-${index}`,
            }))
            .filter(({ link }) => (
              nodeDataById.has(link.source)
              && nodeDataById.has(link.target)
              && link.source !== link.target
            )),
        ]
        : [];
      const selectedCallGraphEdgeIndex = selectedCallGraphLinks.findIndex(
        ({ edgeKey }) => edgeKey === graphState.selectedCallGraphEdgeKey,
      );
      if (selectedCallGraphEdgeIndex < 0) {
        const firstCallGraphLink = selectedCallGraphLinks[0];
        graphState.selectedCallGraphEdgeKey = firstCallGraphLink?.edgeKey || null;
        const graphCanvas = document.getElementById("graph");
        if (graphState.selectedCallGraphEdgeKey) {
          graphCanvas?.setAttribute("data-selected-call-graph-arc", graphState.selectedCallGraphEdgeKey);
        } else {
          graphCanvas?.removeAttribute("data-selected-call-graph-arc");
        }
      }
      const callGraphPortLabels = new Map();
      const callGraphPortCounters = new Map();
      const portNodeIdByEndpoint = new Map(
        [...nodeDataById.values()].flatMap(node => (
          (node.ports || []).map(port => [port.endpoint_id, node.id])
        ))
      );
      const callGraphPortLabel = (port, direction) => {
        const globalLabel = String(port?.label || "").match(direction === "out" ? /O\d+/ : /I\d+/)?.[0];
        if (!callGraphOnly || !port?.endpoint_id) return globalLabel || (direction === "out" ? "OUT" : "IN");
        if (!callGraphPortLabels.has(port.endpoint_id)) {
          const counterKey = `${portNodeIdByEndpoint.get(port.endpoint_id) || "unknown"}:${direction}`;
          const next = (callGraphPortCounters.get(counterKey) || 0) + 1;
          callGraphPortCounters.set(counterKey, next);
          callGraphPortLabels.set(port.endpoint_id, `${direction === "out" ? "O" : "I"}${next}`);
        }
        return callGraphPortLabels.get(port.endpoint_id);
      };
      selectedCallGraphLinks.forEach(({ link }) => {
        (link.endpoint_ids || []).forEach(endpointId => {
          const port = [...nodeDataById.values()]
            .flatMap(node => node.ports || [])
            .find(candidate => candidate.endpoint_id === endpointId);
          if (port) callGraphPortLabel(port, port.direction);
        });
      });
      callGraphArcNavigator = { links: selectedCallGraphLinks, views: [], activeIndex: null };
      const callGraphEdgeKeys = new Set(selectedCallGraphLinks.map(({ index, edgeKey }) => edgeKey || `edge-${index}`));
      const currentSelectedCallGraphEdgeKey = graphState.selectedCallGraphEdgeKey
        || document.getElementById("graph")?.dataset.selectedCallGraphArc
        || null;
      const visualNodeKind = node => {
        if (node.kind === "data_schema") return "mongodb_collection";
        if (node.kind === "message_channel") return "kafka_topic";
        return node.kind;
      };
      const placeGraphTooltip = (
        tooltip,
        bounds,
        clientX = bounds.left + bounds.width / 2,
        clientY = bounds.bottom,
      ) => {
        flowTooltipOverlay.replaceChildren(tooltip);
        const tooltipBounds = tooltip.getBoundingClientRect();
        const left = Math.max(8, Math.min(window.innerWidth - tooltipBounds.width - 8, clientX - tooltipBounds.width / 2));
        const below = clientY + tooltipBounds.height + 10 <= window.innerHeight;
        tooltip.dataset.placement = below ? "bottom" : "top";
        tooltip.style.setProperty("--tooltip-arrow-left", `${Math.max(10, Math.min(tooltipBounds.width - 10, clientX - left))}px`);
        tooltip.style.left = `${left}px`;
        tooltip.style.top = `${below ? clientY + 10 : Math.max(8, clientY - tooltipBounds.height - 10)}px`;
      };
      const addTooltipLine = (tooltip, text, className = "") => {
        const line = document.createElement("span");
        if (className) line.className = className;
        line.textContent = text;
        tooltip.append(line);
      };
      const showDependencyTooltip = (link, clientX, clientY) => {
        const source = nodeDataById.get(link.source);
        const target = nodeDataById.get(link.target);
        const tooltip = document.createElement("span");
        tooltip.className = "graph-edge-tooltip";
        const sourceIsTopic = ["kafka_topic", "message_channel"].includes(source?.kind);
        const targetIsTopic = ["kafka_topic", "message_channel"].includes(target?.kind);
        if (sourceIsTopic !== targetIsTopic) {
          const topic = sourceIsTopic ? source : target;
          const service = sourceIsTopic ? target : source;
          const action = sourceIsTopic ? "Consomme" : "Publie";
          const title = document.createElement("strong");
          title.textContent = service?.name || topic?.name || "Relation";
          tooltip.append(title);
          const details = [action, topic?.name].filter(Boolean).join(" · ");
          if (details) addTooltipLine(tooltip, details, "graph-edge-tooltip-kind");
        } else {
          const title = document.createElement("strong");
          title.textContent = [source?.name, target?.name].filter(Boolean).join(" → ") || "Relation";
          tooltip.append(title);
          const details = [
            link.kind === "kafka" ? "Kafka" : link.kind === "rest" ? "HTTP" : link.kind,
            link.label,
          ].filter(Boolean).join(" · ");
          if (details) addTooltipLine(tooltip, details, "graph-edge-tooltip-kind");
        }
        placeGraphTooltip(
          tooltip,
          { left: clientX, right: clientX, top: clientY, bottom: clientY, width: 0, height: 0 },
          clientX,
          clientY,
        );
      };
      const showNodeTooltip = (node, element, event) => {
        const tooltip = document.createElement("span");
        tooltip.className = "graph-entity-tooltip";
        const title = document.createElement("strong");
        title.textContent = node.name;
        tooltip.append(title);
        if (node.kind === "microservice") {
          const inputs = (node.ports || []).filter(port => port.direction === "in");
          const outputs = (node.ports || []).filter(port => port.direction === "out");
          addTooltipLine(tooltip, `${inputs.length} entrée${inputs.length === 1 ? "" : "s"} · ${outputs.length} sortie${outputs.length === 1 ? "" : "s"}`);
          const topics = [...new Set((node.ports || []).filter(port => /kafka|topic|message/i.test(port.type || "")).map(port => port.name))];
          if (topics.length) addTooltipLine(tooltip, `Messages : ${topics.slice(0, 4).join(", ")}${topics.length > 4 ? "…" : ""}`, "graph-entity-tooltip-detail");
          if (node.technology || node.build_system) addTooltipLine(tooltip, [node.technology, node.build_system].filter(Boolean).join(" · "), "graph-entity-tooltip-detail");
        } else if (node.kind === "kafka_topic") {
          const producers = new Set(graphData.links
            .filter(link => link.kind === "kafka" && link.target === node.id)
            .map(link => link.source)).size;
          const consumers = new Set(graphData.links
            .filter(link => link.kind === "kafka" && link.source === node.id)
            .map(link => link.target)).size;
          addTooltipLine(tooltip, `${producers} producteur${producers > 1 ? "s" : ""} · ${consumers} consommateur${consumers > 1 ? "s" : ""}`);
          const types = [...new Set([...(node.published_message_types || []), ...(node.consumed_message_types || [])])];
          if (types.length) addTooltipLine(tooltip, `Types : ${types.slice(0, 3).join(", ")}${types.length > 3 ? "…" : ""}`, "graph-entity-tooltip-detail");
        } else if (node.kind === "mongodb_collection") {
          const services = new Set(graphData.links
            .filter(link => link.kind === "mongodb" && link.target === node.id)
            .map(link => link.source)).size;
          addTooltipLine(tooltip, `${services} service${services > 1 ? "s" : ""} utilisateur${services > 1 ? "s" : ""}`);
          const classes = (node.persistence_classes || []).map(item => item.name).filter(Boolean);
          if (classes.length) addTooltipLine(tooltip, `Accès : ${classes.slice(0, 3).join(", ")}${classes.length > 3 ? "…" : ""}`, "graph-entity-tooltip-detail");
        }
        placeGraphTooltip(tooltip, element.getBoundingClientRect(), event.clientX);
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
      visibleLinks.forEach((link, index) => {
        network.addEdgeWithKey(`edge-${index}`, link.source, link.target, {
          // Keep architecture arcs visually aligned with the fixed 2px SVG
          // routes used by the selected call graph.
          size: 2, color: relationColor(link), kind: link.kind, type: "arrow",
        });
        network.addEdgeWithKey(`edge-hit-${index}`, link.source, link.target, {
          size: 14, color: "rgba(0,0,0,0)", kind: link.kind, hitArea: true, type: "line",
        });
      });
      selectedCallGraphLinks.forEach(({ link, index, edgeKey }) => network.addEdgeWithKey(edgeKey || `edge-${index}`, link.source, link.target, {
        label: String(link.order ?? index + 1), size: 2, color: relationColor(link), kind: link.kind,
        type: "arrow",
      }));
      initialNodePositions = new Map();
      network.forEachNode((node, attributes) => initialNodePositions.set(node, { x: attributes.x, y: attributes.y }));
      renderer = new Sigma(network, document.getElementById("graph"), {
        labelColor: { color: document.documentElement.dataset.theme === "dark" ? "#dce8f7" : "#172033" },
        // SVG overlays use fixed screen-pixel strokes. Keep Sigma edges and
        // the underlying node markers on the same screen-space scale.
        zoomToSizeRatioFunction: () => 1,
        edgeReducer: (edge, data) => {
          if (data.hitArea || !renderer) return data;
          const screenScale = renderer.scaleSize(1);
          return {
            ...data,
            // Sigma's graph correction varies with the fitted graph extent.
            // Compensate it so architecture arcs remain 2px on screen, like
            // the SVG arcs rendered for the selected call graph.
            size: 2 / Math.max(screenScale, .001),
          };
        },
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
        enableEdgeHoverEvents: true,
        hoverRenderer: () => {},
        renderEdgeLabels: true, labelDensity: .06, labelGridCellSize: 160, labelRenderedSizeThreshold: 10,
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
          if (data.hitArea) return data;
          if (graphState.selectedCodeFlowId) {
            // The selected call graph is rendered once by the LibAvoid SVG
            // overlay. Keeping Sigma's straight edge underneath would draw
            // every connector twice and make routes appear broken.
            return { ...data, hidden: true };
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
        libavoidRoutes = new Map();
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
        const updateAnalysisModeIndicator = () => {
          const context = document.getElementById("graph-mode-context");
          const contextCopy = document.getElementById("graph-mode-context-copy");
          const title = document.getElementById("graph-mode-context-title");
          const pathLabel = document.getElementById("graph-mode-context-path");
          const help = document.getElementById("graph-mode-context-help");
          const clear = document.getElementById("analysis-mode-clear");
          const portsToggle = document.getElementById("analysis-ports-toggle");
          const backToFlows = document.getElementById("analysis-mode-back");
          const backToArchitecture = document.getElementById("analysis-mode-architecture");
          const contextCollapse = document.getElementById("analysis-context-collapse");
          if (!context || !title || !help || !clear) return;
          const active = Boolean(graphState.selectedCodeFlowId);
          context.hidden = !active;
          context.classList.toggle("is-collapsed", Boolean(graphState.analysisContextCollapsed));
          if (contextCopy) contextCopy.id = "graph-mode-context-copy";
          if (contextCollapse) {
            contextCollapse.hidden = !active;
            contextCollapse.setAttribute("aria-expanded", String(!graphState.analysisContextCollapsed));
            contextCollapse.textContent = graphState.analysisContextCollapsed ? "Développer" : "Réduire";
          }
          if (portsToggle) {
            portsToggle.hidden = !active;
            portsToggle.setAttribute("aria-pressed", String(Boolean(graphState.showAllCodeFlowPorts)));
            portsToggle.textContent = graphState.showAllCodeFlowPorts
              ? "Afficher les ports référencés"
              : "Afficher tous les ports";
          }
          if (backToFlows) backToFlows.hidden = !active;
          if (backToArchitecture) backToArchitecture.hidden = !active;
          if (!active) return;
          const flowPath = [...(graphState.pathMicroserviceOrder?.keys() || [])]
            .map(id => nodeDataById.get(id)?.name)
            .filter(Boolean)
            .join(" → ");
          const trigger = graphState.codeFlowTrigger?.name;
          title.textContent = flowPath
            ? `Graphe d’appel · ${flowPath}`
            : trigger ? `Graphe d’appel · ${trigger}` : "Graphe d’appel";
          if (pathLabel) {
            const selectedFlow = (graphData.code_flows || []).find(flow => flow.id === graphState.selectedCodeFlowId);
            const portsByEndpointId = new Map(
              graphData.nodes.flatMap(node => (node.ports || []).map(port => [port.endpoint_id, port]))
            );
            pathLabel.replaceChildren();
            pathLabel.classList.add("graph-mode-tree");
            const callGraph = selectedCallGraph;
            const edgeList = (callGraph?.edges || []).filter(edge => edge.source !== edge.target);
            const graphNodes = [...new Set([
              ...(callGraph?.nodes || []),
              ...edgeList.flatMap(edge => [edge.source, edge.target]),
            ])];
            const childrenBySource = new Map();
            const incoming = new Set();
            edgeList.forEach(edge => {
              childrenBySource.set(edge.source, [
                ...(childrenBySource.get(edge.source) || []), edge,
              ]);
              incoming.add(edge.target);
            });
            const roots = graphNodes.filter(node => !incoming.has(node));
            const treeRoots = roots.length ? roots : graphNodes;
            const endpointLabel = endpointId => {
              const port = portsByEndpointId.get(endpointId);
              const code = port ? callGraphPortLabel(port, port.direction) : "·";
              const detail = port?.message_type?.split(".").at(-1) || port?.name || "relation";
              const protocol = port?.system === "kafka" ? "Kafka" : port?.system === "rest" ? "HTTP" : "";
              return { code, detail, protocol, title: port?.label || endpointId || "Port inconnu" };
            };
            const edgeLabel = edge => {
              const sourcePort = endpointLabel(edge.endpoint_ids?.[0]);
              const targetPort = endpointLabel(edge.endpoint_ids?.[1]);
              const detail = sourcePort.detail !== "relation" ? sourcePort.detail : targetPort.detail;
              const protocol = sourcePort.protocol || targetPort.protocol || (edge.kind === "kafka" ? "Kafka" : edge.kind === "rest" ? "HTTP" : edge.kind || "Relation");
              return {
                text: `${sourcePort.code} → ${targetPort.code} · ${detail} · ${protocol}`,
                title: `${sourcePort.title} → ${targetPort.title} · ${edge.label || ""}`,
              };
            };
            const renderTreeNode = (nodeName, ancestors = new Set()) => {
              const item = document.createElement("li");
              item.className = "graph-mode-tree-node";
              const node = document.createElement("strong");
              node.textContent = nodeName;
              item.append(node);
              if (ancestors.has(nodeName)) {
                item.classList.add("is-cycle");
                node.title = "Cycle détecté dans le graphe d’appel";
                return item;
              }
              const children = childrenBySource.get(nodeName) || [];
              if (!children.length) return item;
              const childList = document.createElement("ul");
              childList.className = "graph-mode-tree-children";
              const nextAncestors = new Set(ancestors).add(nodeName);
              children.forEach(edge => {
                const branch = document.createElement("li");
                branch.className = "graph-mode-tree-branch";
                const relation = edgeLabel(edge);
                const label = document.createElement("span");
                label.className = "graph-mode-tree-edge";
                label.textContent = relation.text;
                label.title = relation.title;
                branch.append(label, renderTreeNode(edge.target, nextAncestors));
                childList.append(branch);
              });
              item.append(childList);
              return item;
            };
            if (treeRoots.length) {
              const tree = document.createElement("ul");
              tree.className = "graph-mode-tree-list";
              treeRoots.forEach(root => tree.append(renderTreeNode(root)));
              pathLabel.append(tree);
            } else {
              pathLabel.textContent = "Parcours de code sélectionné";
            }
          }
          help.textContent = graphState.analysisPortEndpointId
            ? "Arc associé sélectionné · cliquez sur un autre arc ou port pour changer"
            : "Cliquez sur un port ou un arc pour afficher sa relation";
          clear.hidden = !graphState.analysisPortEndpointId;
        };
        const toggleAnalysisEndpoint = (endpointId, event) => {
          if (!endpointId) return;
          event?.preventDefault();
          event?.stopPropagation();
          graphState.analysisPortEndpointId = graphState.analysisPortEndpointId === endpointId
            ? null
            : endpointId;
          updateAnalysisModeIndicator();
          requestGraphRender();
        };
        updateAnalysisModeIndicator();
        const selectedFlow = graphState.selectedCodeFlowId
          ? (graphData.code_flows || []).find(flow => flow.id === graphState.selectedCodeFlowId)
          : null;
        const referencedCodeFlowPortIds = new Set([
          ...(selectedFlow?.steps || []).map(step => step.endpoint_id),
          ...(selectedCallGraph?.edges || []).flatMap(edge => edge.endpoint_ids || []),
        ].filter(Boolean));
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
          if (graphState.selectedCodeFlowId && node.kind !== "microservice") return;
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
          const kindLabel = nodeKindLabel(node);
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
            const isCronTrigger = trigger.kind === "cron_entry";
            const triggerLabel = isCronTrigger ? "Cron" : isHttpTrigger ? "HTTP" : "Kafka";
            triggerBadge.className = `graph-node-trigger-badge ${isCronTrigger ? "is-cron" : isHttpTrigger ? "is-http" : "is-kafka"}`;
            triggerBadge.textContent = `${triggerLabel} · ${trigger.name}`;
            triggerBadge.title = "Déclencheur du graphe d’appel sélectionné";
            label.append(triggerBadge);
          }
          const portsByDirection = { in: [], out: [] };
          if (graphState.selectedCodeFlowId) {
          (node.ports || []).filter(port => (
            port.label
            && (graphState.showAllCodeFlowPorts || referencedCodeFlowPortIds.has(port.endpoint_id))
          )).forEach(port => {
            portsByDirection[port.direction]?.push(port);
          });
          Object.entries(portsByDirection).forEach(([portDirection, ports]) => ports.forEach((port, index) => {
            const anchor = document.createElement("span");
            const portProtocol = port.system === "kafka" ? "kafka" : port.system === "rest" ? "http" : "unknown";
            anchor.className = `graph-node-port-reference is-${portDirection} is-${portProtocol}`;
            anchor.dataset.endpointId = port.endpoint_id;
            anchor.classList.toggle(
              "is-analysis-selected",
              graphState.analysisPortEndpointId === port.endpoint_id,
            );
            anchor.title = "Analyser l’arc associé à ce port";
            // Keep the graph anchor compact. The full endpoint presentation
            // remains in the tooltip and the inspector.
            anchor.style.setProperty("--port-offset", `${(index + 1) / (ports.length + 1) * 100}%`);
            anchor.textContent = callGraphPortLabel(port, portDirection);
            const direction = portDirection === "in" ? "Entrée" : "Sortie";
            const showPortTooltip = () => {
              const tooltip = document.createElement("span");
              tooltip.className = "graph-port-tooltip";
              const title = document.createElement("strong");
              title.textContent = `${callGraphPortLabel(port, portDirection)} · ${direction} · ${port.label}`;
              tooltip.append(title);
              const protocol = document.createElement("span");
              protocol.className = "graph-port-tooltip-meta";
              protocol.textContent = `${port.type || "Endpoint"} · ${port.method || "Méthode inconnue"}`;
              const endpoint = document.createElement("span");
              endpoint.className = "graph-port-tooltip-topic";
              const isTopicMessage = /kafka|topic|message/i.test(`${port.type} ${port.name}`);
              endpoint.textContent = isTopicMessage
                ? `${portDirection === "in" ? "Topic en entrée (consommé)" : "Topic en sortie (publié)"} : ${port.name}`
                : `${port.system === "rest" ? "Ressource HTTP" : "Ressource"} : ${port.name}`;
              const evidence = document.createElement("code");
              evidence.textContent = `${port.path || "Source inconnue"}${port.line ? `:${port.line}` : ""}`;
              tooltip.append(title, protocol, endpoint, evidence);
              if (port.message_type) {
                const messageType = document.createElement("span");
                messageType.className = "graph-port-tooltip-type";
                messageType.textContent = `Type de message : ${port.message_type}`;
                tooltip.append(messageType);
              } else if (port.message_type_warning) {
                const messageType = document.createElement("span");
                messageType.className = "graph-port-tooltip-warning";
                messageType.textContent = `⚠ ${port.message_type_warning}`;
                tooltip.append(messageType);
              }
              if (port.target) {
                const target = document.createElement("span");
                target.className = "graph-port-tooltip-section";
                target.textContent = `Cible résolue : ${port.target.service} · ${port.target.label} · ${port.target.name}`;
                tooltip.append(target);
              }
              flowTooltipOverlay.replaceChildren(tooltip);
              const bounds = anchor.getBoundingClientRect();
              const tooltipBounds = tooltip.getBoundingClientRect();
              const preferredLeft = bounds.left + bounds.width / 2 - tooltipBounds.width / 2;
              const left = Math.max(8, Math.min(window.innerWidth - tooltipBounds.width - 8, preferredLeft));
              const below = bounds.bottom + tooltipBounds.height + 10 <= window.innerHeight;
              tooltip.dataset.placement = below ? "bottom" : "top";
              tooltip.style.setProperty("--tooltip-arrow-left", `${Math.max(10, Math.min(tooltipBounds.width - 10, bounds.left + bounds.width / 2 - left))}px`);
              tooltip.style.left = `${left}px`;
              tooltip.style.top = `${below ? bounds.bottom + 10 : Math.max(8, bounds.top - tooltipBounds.height - 10)}px`;
            };
            anchor.addEventListener("pointerenter", showPortTooltip);
            anchor.addEventListener("pointerleave", () => flowTooltipOverlay.replaceChildren());
            anchor.addEventListener("click", event => {
              toggleAnalysisEndpoint(port.endpoint_id, event);
            });
            label.append(anchor);
          }));
          }
          label.addEventListener("pointerenter", event => {
            if (event.target.closest?.(".graph-node-port-reference")) return;
            showNodeTooltip(node, label, event);
          });
          label.addEventListener("pointerleave", event => {
            if (!label.contains(event.relatedTarget)) flowTooltipOverlay.replaceChildren();
          });
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
            if (event.button !== 0 || forwardedPointer || event.target.closest?.(".graph-node-port-reference")) return;
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
            if (event.button !== 0 || event.target.closest?.(".graph-node-port-reference")) return;
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
        if (graphState.renderMode !== "symbols" && !graphState.selectedCodeFlowId) {
          const svgNamespace = "http://www.w3.org/2000/svg";
          visibleLinks.forEach(link => {
            const source = nodePoints.get(link.source);
            const target = nodePoints.get(link.target);
            if (!source || !target) return;
            const hitArea = document.createElementNS(svgNamespace, "path");
            hitArea.classList.add("graph-dependency-hit-area");
            hitArea.setAttribute("d", `M ${source.x} ${source.y} L ${target.x} ${target.y}`);
            hitArea.addEventListener("pointerenter", event => {
              showDependencyTooltip(link, event.clientX, event.clientY);
            });
            hitArea.addEventListener("pointermove", event => {
              showDependencyTooltip(link, event.clientX, event.clientY);
            });
            hitArea.addEventListener("pointerleave", () => flowTooltipOverlay.replaceChildren());
            portPathOverlay.append(hitArea);
          });
        }
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
          const viewportMargin = 90;
          const obstacles = obstacleBounds
            .filter(obstacle => ![sourceId, targetId].includes(obstacle.id))
            .map(obstacle => ({
              left: obstacle.left - padding,
              top: obstacle.top - padding,
              right: obstacle.right + padding,
              bottom: obstacle.bottom + padding,
            }));
          const xLanes = [...new Set([
            start[0], end[0],
            ...obstacles.flatMap(obstacle => [obstacle.left, obstacle.right]),
          ])].filter((lane, index, lanes) => (
            (lane === start[0] || lane === end[0] || (lane >= viewportMargin && lane <= overlayBounds.width - viewportMargin))
            && lanes.indexOf(lane) === index
          ));
          const yLanes = [...new Set([
            start[1], end[1],
            ...obstacles.flatMap(obstacle => [obstacle.top, obstacle.bottom]),
          ])].filter((lane, index, lanes) => (
            (lane === start[1] || lane === end[1] || (lane >= viewportMargin && lane <= overlayBounds.height - viewportMargin))
            && lanes.indexOf(lane) === index
          ));
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
              candidates.push({
                points: compact,
                score: length + (compact.length - 2) * 80,
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
        const portPathIsExternal = (points, sourceSide, targetSide) => {
          if (points.length < 2) return false;
          const [start, next] = points;
          const previous = points[points.length - 2];
          const end = points[points.length - 1];
          const epsilon = 1;
          const leavesSource = sourceSide === "EAST"
            ? next[0] >= start[0] - epsilon
            : sourceSide === "WEST"
              ? next[0] <= start[0] + epsilon
              : sourceSide === "SOUTH"
                ? next[1] >= start[1] - epsilon
                : previous[1] <= start[1] + epsilon;
          const entersTarget = targetSide === "EAST"
            ? previous[0] <= end[0] + epsilon
            : targetSide === "WEST"
              ? previous[0] >= end[0] - epsilon
              : targetSide === "SOUTH"
                ? previous[1] <= end[1] + epsilon
                : previous[1] >= end[1] - epsilon;
          return leavesSource && entersTarget;
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
          if (!forceCurve
            && portPathIsExternal([start, end], "EAST", "WEST")
            && pathIsClear([start, end], obstacles, occupiedSegments)) {
            return { d: `M ${start[0]} ${start[1]} L ${end[0]} ${end[1]}`, points: [start, end], obstacleRouted: false };
          }
          // Prefer a short orthogonal detour when a card blocks the direct
          // lane. The previous curve-first strategy could choose a very long
          // bottom/top U because its smoothness score did not account for the
          // actual detour length. Orthogonal routes remain explicit and are
          // easier to scan in a call graph.
          const orthogonal = orthogonalPath(start, end, sourceId, targetId, occupiedSegments);
          const directLength = Math.hypot(end[0] - start[0], end[1] - start[1]);
          const orthogonalLength = orthogonal.points.slice(1).reduce((total, point, index) => (
            total + Math.abs(point[0] - orthogonal.points[index][0])
              + Math.abs(point[1] - orthogonal.points[index][1])
          ), 0);
          if (orthogonal.points.length <= 5 && orthogonalLength <= Math.max(220, directLength * 2.2)) {
            return { d: orthogonal.d, points: orthogonal.points, obstacleRouted: true, router: "orthogonal-fallback" };
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
            if (portPathIsExternal(points, "EAST", "WEST")
              && pathIsClear(points, obstacles, occupiedSegments)) {
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
          return { d: orthogonal.d, points: orthogonal.points, obstacleRouted: true };
        };
        const occupiedCallGraphSegments = [];
        const libavoidNodes = new Map();
        const libavoidEdges = [];
        const addLibavoidObstacle = card => {
          const nodeId = card.dataset.nodeId;
          if (libavoidNodes.has(nodeId)) return;
          const bounds = card.getBoundingClientRect();
          libavoidNodes.set(nodeId, {
            id: nodeId,
            x: bounds.left - overlayBounds.left,
            y: bounds.top - overlayBounds.top,
            width: bounds.width,
            height: bounds.height,
            ports: [],
          });
        };
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
            const portX = side === "EAST"
              ? anchorBounds.right - bounds.left
              : side === "WEST"
                ? anchorBounds.left - bounds.left
                : anchorBounds.left + anchorBounds.width / 2 - bounds.left;
            const portY = side === "SOUTH"
              ? anchorBounds.bottom - bounds.top
              : side === "NORTH"
                ? anchorBounds.top - bounds.top
                : anchorBounds.top + anchorBounds.height / 2 - bounds.top;
            node.ports.push({
              id: portId,
              x: portX,
              y: portY,
              width: 1,
              height: 1,
              layoutOptions: { "org.eclipse.elk.port.side": side },
            });
          }
          return nodeId;
        };
        // Libavoid must know every visible microservice, not only the source
        // and target of the edges currently being routed. Otherwise an
        // unrelated service is invisible to the obstacle model and an arc can
        // cross straight through its card.
        [...nodeLabelOverlay.querySelectorAll(".graph-node-card-label")]
          .filter(card => nodeDataById.get(card.dataset.nodeId)?.kind === "microservice")
          .forEach(addLibavoidObstacle);
        const routePointsToPath = (route, start, end, sourceId, targetId) => {
          if (!route?.sourcePoint || !route?.targetPoint) return null;
          const points = [route.sourcePoint, ...(route.bendPoints || []), route.targetPoint]
            .map(point => [point.x, point.y])
            .filter(point => point.every(Number.isFinite));
          if (points.length < 2) return null;
          const directLength = Math.hypot(end[0] - start[0], end[1] - start[1]);
          const routeLength = points.slice(1).reduce((total, point, index) => (
            total + Math.abs(point[0] - points[index][0]) + Math.abs(point[1] - points[index][1])
          ), 0);
          // Libavoid is obstacle-aware but does not optimize for the visible
          // viewport. Reject pathological routes that travel around the
          // entire canvas; the bounded local fallback can then choose a
          // shorter corridor.
          if (routeLength > Math.max(280, directLength * 2.6)) return null;
          points[0] = start;
          points[points.length - 1] = end;
          if (points.length === 2) {
            const obstacles = obstacleBounds
              .filter(obstacle => ![sourceId, targetId].includes(obstacle.id))
              .map(obstacle => ({
                left: obstacle.left - overlayBounds.left - 14,
                top: obstacle.top - overlayBounds.top - 14,
                right: obstacle.right - overlayBounds.left + 14,
                bottom: obstacle.bottom - overlayBounds.top + 14,
              }));
            if (!pathIsClear([start, end], obstacles)) {
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
          }
          // Libavoid receives virtual viewport obstacles below, so modifying
          // its points after routing would invalidate its obstacle guarantees.
          // Reject an out-of-bounds route and let the geometry fallback choose
          // a safe path instead.
          const viewportMargin = 90;
          const routeInsideViewport = points.slice(1, -1).every(([x, y]) => (
            x >= viewportMargin
            && x <= overlayBounds.width - viewportMargin
            && y >= viewportMargin
            && y <= overlayBounds.height - viewportMargin
          ));
          if (!routeInsideViewport) return null;
          const routeObstacles = obstacleBounds
            .filter(obstacle => ![sourceId, targetId].includes(obstacle.id))
            .map(obstacle => ({
              left: obstacle.left - overlayBounds.left - 14,
              top: obstacle.top - overlayBounds.top - 14,
              right: obstacle.right - overlayBounds.left + 14,
              bottom: obstacle.bottom - overlayBounds.top + 14,
            }));
          if (!portPathIsExternal(points, "EAST", "WEST")) return null;
          if (!pathIsClear(points, routeObstacles)) return null;
          const roundedPath = points.length < 3
            ? `M ${points[0][0]} ${points[0][1]} L ${points[points.length - 1][0]} ${points[points.length - 1][1]}`
            : points.slice(1, -1).reduce((path, point, index) => {
              const previous = points[index];
              const next = points[index + 2];
              const previousDistance = Math.hypot(point[0] - previous[0], point[1] - previous[1]);
              const nextDistance = Math.hypot(next[0] - point[0], next[1] - point[1]);
              const radius = Math.min(12, previousDistance / 2, nextDistance / 2);
              const entry = [
                point[0] + ((previous[0] - point[0]) * radius / previousDistance),
                point[1] + ((previous[1] - point[1]) * radius / previousDistance),
              ];
              const exit = [
                point[0] + ((next[0] - point[0]) * radius / nextDistance),
                point[1] + ((next[1] - point[1]) * radius / nextDistance),
              ];
              return `${path} L ${entry[0]} ${entry[1]} Q ${point[0]} ${point[1]} ${exit[0]} ${exit[1]}`;
            }, `M ${points[0][0]} ${points[0][1]}`)
              + ` L ${points[points.length - 1][0]} ${points[points.length - 1][1]}`;
          return {
            d: roundedPath,
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
          const sourcePort = (nodeDataById.get(sourceId)?.ports || []).find(port => (
            port.direction === "out" && (!link.label || port.name === link.label)
          ));
          const targetPort = (nodeDataById.get(targetId)?.ports || []).find(port => (
            port.direction === "in" && (!link.label || port.name === link.label)
          ));
          const sourceAnchor = (link.endpoint_ids || [])
            .map(endpointId => anchorsByEndpointId.get(endpointId))
            .find(anchor => anchor?.classList.contains("is-out"))
            || [...(sourcePort ? [anchorsByEndpointId.get(sourcePort.endpoint_id)] : [])][0];
          const targetAnchor = (link.endpoint_ids || [])
            .map(endpointId => anchorsByEndpointId.get(endpointId))
            .find(anchor => anchor?.classList.contains("is-in"))
            || [...(targetPort ? [anchorsByEndpointId.get(targetPort.endpoint_id)] : [])][0];
          if (!sourceAnchor || !targetAnchor) return null;
          const sourceCardBounds = source.getBoundingClientRect();
          const targetCardBounds = target.getBoundingClientRect();
          const sourceBounds = sourceAnchor.getBoundingClientRect();
          const targetBounds = targetAnchor.getBoundingClientRect();
          // OUT arcs leave through the right edge of their badge and IN arcs
          // arrive at its left edge. Connecting to the badge centers makes
          // arrowheads look detached and hides the direction of the arc.
          const start = [sourceBounds.right, sourceBounds.top + sourceBounds.height / 2];
          const end = [targetBounds.left, targetBounds.top + targetBounds.height / 2];
          const startPoint = [start[0] - overlayBounds.left, start[1] - overlayBounds.top];
          const endPoint = [end[0] - overlayBounds.left, end[1] - overlayBounds.top];
          const resolvedEdgeKey = edgeKey || `call-edge-${index}`;
          const sourcePortId = (link.endpoint_ids || []).find(endpointId => (
            anchorsByEndpointId.get(endpointId)?.classList.contains("is-out")
          )) || `${resolvedEdgeKey}-source`;
          const targetPortId = (link.endpoint_ids || []).find(endpointId => (
            anchorsByEndpointId.get(endpointId)?.classList.contains("is-in")
          )) || `${resolvedEdgeKey}-target`;
          const sourceSide = "EAST";
          const targetSide = "WEST";
          ensureLibavoidNode(source, sourceCardBounds, sourceAnchor, sourcePortId, sourceSide);
          ensureLibavoidNode(target, targetCardBounds, targetAnchor, targetPortId, targetSide);
          libavoidEdges.push({
            id: resolvedEdgeKey,
            source: sourceId,
            target: targetId,
          });
          const libavoidRouted = routePointsToPath(
            libavoidRoutes.get(resolvedEdgeKey),
            startPoint,
            endPoint,
            sourceId,
            targetId,
          );
          if (libavoidRouted) return libavoidRouted;
          const fallback = hybridPath(
            startPoint,
            endPoint,
            sourceId,
            targetId,
            occupiedCallGraphSegments,
          );
          fallback.router = "libavoid-fallback";
          return fallback;
        };
        const portsByEndpointId = new Map(
          [...nodeDataById.values()].flatMap(node => (node.ports || []).map(port => [port.endpoint_id, port]))
        );
        const shortPortLabel = (port, direction) => {
          return callGraphPortLabel(port, direction);
        };
        const createArcTooltip = (link, sourcePort, targetPort) => {
            const tooltip = document.createElement("span");
            tooltip.className = "graph-arc-tooltip";
            const sourceName = nodeDataById.get(link.source)?.name;
            const targetName = nodeDataById.get(link.target)?.name;
            const title = document.createElement("strong");
            title.textContent = [sourceName, targetName].filter(Boolean).join(" → ") || "Relation";
            const relation = document.createElement("span");
            relation.className = "graph-arc-tooltip-relation";
            const order = link.order ? `Arc #${link.order} · ` : "";
            const protocol = link.kind === "kafka" ? "Kafka" : link.kind === "rest" ? "HTTP" : link.kind;
            const relationText = [protocol, link.label].filter(Boolean).join(" · ");
            relation.textContent = `${order}${relationText || "Relation"}`;
            tooltip.append(title, relation);
            return tooltip;
        };
        const showArcTooltip = (path, link, sourcePort, targetPort, event = null) => {
            const tooltip = createArcTooltip(link, sourcePort, targetPort);
            flowTooltipOverlay.replaceChildren(tooltip);
            const bounds = path.getBoundingClientRect();
            const tooltipBounds = tooltip.getBoundingClientRect();
            const anchorX = event?.clientX || bounds.left + bounds.width / 2;
            const left = Math.max(8, Math.min(window.innerWidth - tooltipBounds.width - 8, anchorX - tooltipBounds.width / 2));
            const below = bounds.bottom + tooltipBounds.height + 10 <= window.innerHeight;
            tooltip.dataset.placement = below ? "bottom" : "top";
            tooltip.style.setProperty("--tooltip-arrow-left", `${Math.max(10, Math.min(tooltipBounds.width - 10, anchorX - left))}px`);
            tooltip.style.left = `${left}px`;
            tooltip.style.top = `${below ? bounds.bottom + 10 : Math.max(8, bounds.top - tooltipBounds.height - 10)}px`;
        };
        const bindArcTooltip = (path, link, sourcePort, targetPort, edgeKey) => {
          path.addEventListener("pointerenter", event => showArcTooltip(path, link, sourcePort, targetPort, event));
          path.addEventListener("pointerleave", () => {
            if (graphState.selectedCallGraphEdgeKey !== edgeKey) flowTooltipOverlay.replaceChildren();
          });
        };
        const callGraphArcTooltipLabel = (link, sourcePort, targetPort) => {
          const source = nodeDataById.get(link.source)?.name;
          const target = nodeDataById.get(link.target)?.name;
          const ports = `${shortPortLabel(sourcePort, "out")} → ${shortPortLabel(targetPort, "in")}`;
          const relation = link.label || (link.kind === "kafka" ? "Kafka" : link.kind || "Relation");
          const arc = link.order ? `Arc #${link.order} · ` : "";
          return `${arc}${[source, target].filter(Boolean).join(" → ") || "Relation"} · ${relation} · ${ports}`;
        };
        const addArcHitArea = path => {
          const hitArea = path.cloneNode();
          hitArea.classList.add("graph-arc-hit-area");
          hitArea.removeAttribute("marker-end");
          portPathOverlay.append(hitArea);
          return hitArea;
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
          if (routed.obstacleRouted) {
            occupiedCallGraphSegments.push(...routed.points.slice(1).map((point, pointIndex) => (
              [routed.points[pointIndex], point]
            )));
          }
          // Sigma's call-graph edges are hidden while a flow is selected, so
          // every route (including a clear direct segment) must be drawn in
          // the port overlay. Otherwise direct arcs silently disappear.
          const path = document.createElementNS(svgNamespace, "path");
          path.classList.add("graph-call-path");
          if (link.kind === "rest") path.classList.add("is-rest");
          path.dataset.arcKey = resolvedEdgeKey;
          if (resolvedEdgeKey === currentSelectedCallGraphEdgeKey) {
            path.classList.add("is-keyboard-selected");
          }
          if (link.kind === "kafka") path.classList.add("is-kafka");
          if ((link.endpoint_ids || []).includes(graphState.analysisPortEndpointId)) {
            path.classList.add("is-analysis-selected");
          }
          path.setAttribute("marker-end", "url(#graph-port-arrow)");
          if (routed.router) path.dataset.router = routed.router;
          path.setAttribute("d", routed.d);
          portPathOverlay.append(path);
          const arcLabel = document.createElementNS(svgNamespace, "text");
          arcLabel.classList.add("graph-call-label");
          if (link.kind === "rest") arcLabel.classList.add("is-rest");
          arcLabel.dataset.arcKey = resolvedEdgeKey;
          if (resolvedEdgeKey === currentSelectedCallGraphEdgeKey) {
            arcLabel.classList.add("is-keyboard-selected");
          }
          if (link.kind === "kafka") arcLabel.classList.add("is-kafka");
          if ((link.endpoint_ids || []).includes(graphState.analysisPortEndpointId)) {
            arcLabel.classList.add("is-analysis-selected");
          }
          const sourcePort = (link.endpoint_ids || [])
            .map(endpointId => portsByEndpointId.get(endpointId))
            .find(port => port?.direction === "out")
            || (nodeDataById.get(link.source)?.ports || []).find(port => (
              port.direction === "out" && (!link.label || port.name === link.label)
            ));
          const targetPort = (link.endpoint_ids || [])
            .map(endpointId => portsByEndpointId.get(endpointId))
            .find(port => port?.direction === "in")
            || (nodeDataById.get(link.target)?.ports || []).find(port => (
              port.direction === "in" && (!link.label || port.name === link.label)
            ));
          const shortPortLabel = (port, direction) => {
            return callGraphPortLabel(port, direction);
          };
          arcLabel.textContent = String(link.order ?? index + 1);
          arcLabel.setAttribute(
            "aria-label",
            `${shortPortLabel(sourcePort, "out")} → ${shortPortLabel(targetPort, "in")}`,
          );
          const labelPoint = path.getPointAtLength(path.getTotalLength() / 2);
          arcLabel.setAttribute("x", String(labelPoint.x));
          arcLabel.setAttribute("y", String(labelPoint.y - 8));
          portPathOverlay.append(arcLabel);
          const hitArea = addArcHitArea(path);
          const tooltipLabel = callGraphArcTooltipLabel(link, sourcePort, targetPort);
          hitArea.setAttribute("aria-label", tooltipLabel);
          hitArea.setAttribute("title", tooltipLabel);
          if (resolvedEdgeKey === currentSelectedCallGraphEdgeKey) {
            hitArea.classList.add("is-keyboard-selected");
          }
          hitArea.addEventListener("pointerenter", () => {
            path.classList.add("is-analysis-hovered");
            arcLabel.classList.add("is-analysis-hovered");
          });
          hitArea.addEventListener("pointerleave", () => {
            path.classList.remove("is-analysis-hovered");
            arcLabel.classList.remove("is-analysis-hovered");
          });
          hitArea.addEventListener("click", event => {
            if (graphState.selectedCallGraphEdgeKey === resolvedEdgeKey) {
              clearSelectedCallGraphArc();
              event?.preventDefault();
              event?.stopPropagation();
              return;
            }
            graphState.selectedCallGraphEdgeKey = resolvedEdgeKey;
            clearCallGraphAnalysisFocus();
            const viewIndex = callGraphArcNavigator.views.findIndex(view => view.edgeKey === resolvedEdgeKey);
            if (viewIndex >= 0) focusCallGraphArc(viewIndex);
            event?.preventDefault();
            event?.stopPropagation();
          });
          bindArcTooltip(hitArea, link, sourcePort, targetPort, resolvedEdgeKey);
          callGraphArcNavigator.views.push({
            edgeKey: resolvedEdgeKey,
            path,
            arcLabel,
            hitArea,
            showTooltip: () => showArcTooltip(path, link, sourcePort, targetPort),
          });
        });
        const activeIndex = callGraphArcNavigator.views.findIndex(
          view => view.edgeKey === graphState.selectedCallGraphEdgeKey,
        );
        callGraphArcNavigator.activeIndex = activeIndex >= 0 ? activeIndex : null;
        if (activeIndex >= 0) {
          requestAnimationFrame(() => focusCallGraphArc(activeIndex));
        }
        const libavoidRouteKeyForGeometry = [
          ...[...libavoidNodes.values()].map(node => (
            `${node.id}:${node.x}:${node.y}:${node.width}:${node.height}:`
            + (node.ports || []).map(port => `${port.id}:${port.x}:${port.y}`).join(",")
          )),
          ...libavoidEdges.map(edge => `${edge.id}:${edge.source}:${edge.target}`),
        ].join("|");
        routeWithLibavoid(
          libavoidRouteKeyForGeometry,
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
          if ([link.input_endpoint_id, link.output_endpoint_id].includes(graphState.analysisPortEndpointId)) {
            path.classList.add("is-analysis-selected");
          }
          if (graphState.relatedLocalPortLinks?.has(
            `${link.input_endpoint_id}:${link.output_endpoint_id}`
          )) path.classList.add("is-code-flow-path");
          path.setAttribute("marker-end", "url(#graph-port-arrow)");
          path.setAttribute("d", portPath(input, output));
          portPathOverlay.append(path);
          const hitArea = addArcHitArea(path);
          hitArea.addEventListener("click", event => {
            toggleAnalysisEndpoint(link.output_endpoint_id || link.input_endpoint_id, event);
          });
          bindArcTooltip(
            hitArea,
            {
              kind: "internal",
              label: "Flux interne",
              source: `microservice:${output.closest(".graph-node-card-label")?.dataset.nodeId || ""}`,
              target: `microservice:${input.closest(".graph-node-card-label")?.dataset.nodeId || ""}`,
            },
            portsByEndpointId.get(link.output_endpoint_id),
            portsByEndpointId.get(link.input_endpoint_id),
          );
        });
        selectedPortLinks.forEach(link => {
          const source = anchorsByEndpointId.get(link.source_endpoint_id);
          const target = anchorsByEndpointId.get(link.target_endpoint_id);
          if (!source?.classList.contains("is-out") || !target?.classList.contains("is-in")) return;
          const path = document.createElementNS(svgNamespace, "path");
          path.classList.add("graph-port-path");
          if (link.kind === "rest") path.classList.add("is-rest");
          if (link.kind === "kafka") path.classList.add("is-kafka");
          if ([link.source_endpoint_id, link.target_endpoint_id].includes(graphState.analysisPortEndpointId)) {
            path.classList.add("is-analysis-selected");
          }
          path.setAttribute("marker-end", "url(#graph-port-arrow)");
          path.setAttribute("d", portPath(source, target));
          portPathOverlay.append(path);
          const hitArea = addArcHitArea(path);
          hitArea.addEventListener("click", event => {
            toggleAnalysisEndpoint(link.source_endpoint_id || link.target_endpoint_id, event);
          });
          bindArcTooltip(
            hitArea,
            {
              kind: link.kind,
              label: link.kind === "kafka" ? source.dataset.endpointId : "HTTP",
              source: `microservice:${source.closest(".graph-node-card-label")?.dataset.nodeId || ""}`,
              target: `microservice:${target.closest(".graph-node-card-label")?.dataset.nodeId || ""}`,
            },
            portsByEndpointId.get(link.source_endpoint_id),
            portsByEndpointId.get(link.target_endpoint_id),
          );
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
      renderer.on("enterEdge", ({ edge, event }) => {
        if (String(edge).startsWith("call-edge-")) return;
        const match = String(edge).match(/^edge(?:-hit)?-(\d+)$/);
        const link = match ? visibleLinks[Number(match[1])] : null;
        if (!link) return;
        const graphBounds = document.getElementById("graph").getBoundingClientRect();
        const pointer = event?.originalEvent || event;
        const clientX = pointer?.clientX ?? graphBounds.left + graphBounds.width / 2;
        const clientY = pointer?.clientY ?? graphBounds.top + graphBounds.height / 2;
        showDependencyTooltip(link, clientX, clientY);
      });
      renderer.on("leaveEdge", () => flowTooltipOverlay.replaceChildren());
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
      keepSelectedCallGraphArcVisible();
      // Keep Sigma's native camera coordinate system; layout coordinates are
      // centered above so the complete vertical stack stays in view.
      renderer.getCamera().animatedReset({ duration: 0 });
    }
    rebuildGraph();
