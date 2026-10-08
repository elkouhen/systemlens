// Ordered source module: 55-code-flows.js
    const codeFlows = Array.isArray(graphData.code_flows) ? graphData.code_flows : [];
    const codeFlowsList = document.getElementById("code-flows");
    const codeFlowsEmpty = document.getElementById("code-flows-empty");
    const codeFlowFilter = document.getElementById("code-flow-filter");
    const codeFlowConfidence = document.getElementById("code-flow-confidence");
    const codeFlowKind = document.getElementById("code-flow-kind");
    const codeFlowMessageType = document.getElementById("code-flow-message-type");
    const codeFlowMessageTypes = document.getElementById("code-flow-message-types");
    const codeFlowCycles = document.getElementById("code-flow-cycles");
    const codeFlowsSummary = document.getElementById("code-flows-summary");
    const codeFlowFilterSummary = document.getElementById("code-flow-filter-summary");
    const codeFlowFilterReset = document.getElementById("code-flow-filter-reset");
    const codeFlowsTitle = document.getElementById("code-flows-title");
    let callTreePan = null;
    let callTreePanBound = false;
    let callTreeRenderSignature = "";

    function updateCallTreeStats(nodeCount = 0, edgeCount = 0) {
      graphState.callTreeVisibleNodeCount = nodeCount;
      graphState.callTreeVisibleEdgeCount = edgeCount;
      const stats = document.getElementById("graph-mode-context-stats");
      if (!stats) return;
      const active = Boolean(graphState.selectedCodeFlowId)
        && graphState.viewMode === "call-graph";
      stats.hidden = !active;
      stats.textContent = active
        ? `Arbre : ${nodeCount} nœud${nodeCount === 1 ? "" : "s"} · ${edgeCount} arc${edgeCount === 1 ? "" : "s"}`
        : "";
    }
    function codeFlowStepLabel(kind) {
      return ({
        http_entry: "Entrée HTTP",
        message_entry: "Entrée message",
        cron_entry: "Déclencheur Cron",
        http_call: "Appel HTTP",
        method_call: "Appel de méthode",
        message_publish: "Publication message",
        data_read: "Lecture de Data",
        data_write: "Écriture de Data",
      })[kind] || String(kind || "Étape").replaceAll("_", " ");
    }

    function codeFlowPriority(flow) {
      return flow.status === "cycle" ? 1 : 0;
    }

    function serviceIdsForCodeFlow(flow) {
      const callGraphOrder = callGraphForFlow(flow)?.node_order;
      if (Array.isArray(callGraphOrder) && callGraphOrder.length) {
        const callGraphServices = callGraphOrder
          .map(service => nodeIdForCodeFlowResource(service, "microservice"))
          .filter(Boolean);
        if (callGraphServices.length) return new Set(callGraphServices);
      }
      return new Set(
        [...new Set((flow.steps || [])
          .map(step => step.endpoint_id)
          .filter(Boolean))]
          .flatMap(endpointId => nodeIdsByEndpoint.get(endpointId) || [])
          .filter(nodeId => nodeDataById.get(nodeId)?.kind === "microservice")
      );
    }

    function callGraphArcCount(flow) {
      return callGraphForFlow(flow)?.edges?.length || 0;
    }

    function codeFlowProtocols(flow) {
      const protocols = new Set();
      (flow.steps || []).forEach(step => {
        if (/message|kafka|topic/i.test(`${step.kind} ${step.name} ${step.path}`)) protocols.add("kafka");
        if (/http|rest|api/i.test(`${step.kind} ${step.name} ${step.path}`)) protocols.add("http");
      });
      return protocols;
    }

    function codeFlowStats(flow) {
      const steps = flow.steps || [];
      return [
        ["Microservices", servicesForCodeFlow(flow).length],
        ["Appels", callGraphArcCount(flow)],
        ["Étapes", steps.length],
        ["Effets", steps.filter(step => ["message_publish", "http_call", "data_write"].includes(step.kind)).length],
      ];
    }

    function codeFlowDescription(flow) {
      const aiDescription = graphData.flow_descriptions?.[flow.id];
      if (aiDescription) return aiDescription;
      const steps = flow.steps || [];
      const trigger = steps[0];
      const effect = [...steps].reverse().find(step => (
        ["http_call", "message_publish", "data_read", "data_write"].includes(step.kind)
      ));
      if (!trigger || !effect) return flow.reason || "Flux potentiel détecté à partir des preuves indexées.";
      const triggerVerb = trigger.kind === "message_entry"
        ? "consomme"
        : trigger.kind === "cron_entry"
          ? "est déclenché par"
          : "reçoit";
      const effectVerb = effect.kind === "http_call"
        ? "appeler"
        : effect.kind === "message_publish"
          ? "publier"
          : effect.kind === "data_write"
            ? "écrire dans"
            : "lire";
      return `Dans ${flow.module}, le flux potentiel ${triggerVerb} ${trigger.name} peut ${effectVerb} ${effect.name}.`;
    }

    function sortCodeFlows(left, right) {
      return codeFlowPriority(left) - codeFlowPriority(right)
        || serviceIdsForCodeFlow(right).size - serviceIdsForCodeFlow(left).size
        || callGraphArcCount(right) - callGraphArcCount(left)
        || (right.steps?.length || 0) - (left.steps?.length || 0)
        || left.id.localeCompare(right.id);
    }

    function activeCodeFlowFilters() {
      const filters = [];
      if (codeFlowFilter.value.trim()) filters.push("recherche");
      if (codeFlowMessageType.value.trim()) filters.push("type de message");
      if (codeFlowConfidence.value !== "all") filters.push(codeFlowConfidence.options[codeFlowConfidence.selectedIndex]?.text || "confiance");
      if (codeFlowKind.value !== "all") filters.push(codeFlowKind.options[codeFlowKind.selectedIndex]?.text || "protocole");
      if (codeFlowCycles.getAttribute("aria-pressed") === "true") filters.push("cycles");
      return filters;
    }

    function updateCodeFlowFilterSummary() {
      const filters = activeCodeFlowFilters();
      codeFlowFilterSummary.textContent = filters.length
        ? `${filters.length} filtre${filters.length > 1 ? "s" : ""} actif${filters.length > 1 ? "s" : ""} · ${filters.join(" · ")}`
        : "Aucun filtre additionnel";
      codeFlowFilterReset.disabled = filters.length === 0;
    }

    function resetCodeFlowFilters() {
      codeFlowFilter.value = "";
      codeFlowMessageType.value = "";
      codeFlowConfidence.value = "all";
      codeFlowKind.value = "all";
      codeFlowCycles.setAttribute("aria-pressed", "false");
      renderCodeFlows();
    }

    // Build these once: rendering the Flux list must not repeatedly scan the
    // complete topology for every flow card.  Endpoint identities are the
    // persisted evidence; names and route labels are presentation only.
    const nodeIdsByResource = new Map();
    const nodeIdsByEndpoint = new Map();
    graphData.nodes.forEach(node => {
      const resourceKey = `${node.kind}:${node.name}`;
      nodeIdsByResource.set(resourceKey, [...(nodeIdsByResource.get(resourceKey) || []), node.id]);
      (node.ports || []).forEach(port => {
        if (!port.endpoint_id) return;
        nodeIdsByEndpoint.set(port.endpoint_id, [...(nodeIdsByEndpoint.get(port.endpoint_id) || []), node.id]);
      });
    });
    const messageTypes = [...new Set(graphData.nodes.flatMap(node => (
      (node.ports || []).map(port => port.message_type).filter(Boolean)
    )))].sort((left, right) => left.localeCompare(right));
    codeFlowMessageTypes.replaceChildren(...messageTypes.map(messageType => {
      const option = document.createElement("option");
      option.value = messageType;
      return option;
    }));
    const portsByEndpointId = new Map(graphData.nodes.flatMap(node => (
      (node.ports || []).map(port => [port.endpoint_id, port])
    )));
    const messageTypesForCodeFlow = flow => new Set(
      (flow.steps || []).flatMap(step => {
        const port = portsByEndpointId.get(step.endpoint_id);
        return port?.message_type ? [port.message_type] : [];
      })
    );
    const graphLinksByEndpoint = new Map();
    graphData.links.forEach((link, index) => {
      (link.endpoint_ids || []).forEach(endpointId => {
        graphLinksByEndpoint.set(endpointId, [
          ...(graphLinksByEndpoint.get(endpointId) || []), { edge: `edge-${index}`, link },
        ]);
      });
    });
    const internalOutputsByInput = new Map();
    (graphData.internal_port_links || []).forEach(link => {
      internalOutputsByInput.set(link.input_endpoint_id, new Set([
        ...(internalOutputsByInput.get(link.input_endpoint_id) || []), link.output_endpoint_id,
      ]));
    });
    const uniqueNodeId = ids => ids?.length === 1 ? ids[0] : null;
    const nodeIdForCodeFlowResource = (name, kind) => uniqueNodeId(
      nodeIdsByResource.get(`${kind}:${name}`)
    );
    const nodeForCallTreeTopic = name => graphData.nodes.find(node => (
      ["kafka_topic", "message_channel"].includes(node.kind)
      && node.name === name
    ));
    const routeForCallTreeEdge = edge => {
      const target = graphData.nodes.find(node => (
        node.kind === "microservice" && node.name === edge.target
      ));
      const route = target?.http_routes?.find(item => item.route === edge.label);
      return target && route ? { target, route } : null;
    };
    const openCallTreeNodeInspector = node => {
      if (!node) return;
      renderDetails(node.id);
      openArchitectureNodeInspector(node, { reset: true });
      hideInlineDetailsAfterModal();
    };
    const openCallTreeEdgeInspector = edge => {
      if (edge.kind === "kafka") {
        openCallTreeNodeInspector(nodeForCallTreeTopic(edge.label));
        return;
      }
      if (edge.kind === "rest") {
        const route = routeForCallTreeEdge(edge);
        if (route) openHttpRouteInspector(route.target, route.route);
      }
    };
    const nodeIdForEndpoint = endpointId => uniqueNodeId(nodeIdsByEndpoint.get(endpointId));

    function captureCallTreeCamera() {
      const canvas = graphCallTreeOverlay?.querySelector(".graph-call-tree-canvas");
      if (!canvas) return null;
      const viewport = graphCallTreeOverlay.getBoundingClientRect();
      const baseScale = Number(canvas.dataset.baseScale) || 1;
      const totalScale = baseScale * (graphState.callTreeZoom || 1);
      if (!totalScale) return null;
      return {
        left: Number.parseFloat(canvas.style.left) || 0,
        top: Number.parseFloat(canvas.style.top) || 0,
        totalScale,
        centerX: viewport.width / 2,
        centerY: viewport.height / 2,
      };
    }

    function renderCallTreeOverlay(preservedCamera = null) {
      if (!graphCallTreeOverlay) return;
      if (!callTreePanBound) {
        callTreePanBound = true;
        const finishCallTreePan = event => {
          if (!callTreePan || event.pointerId !== callTreePan.pointerId) return;
          callTreePan = null;
          window.removeEventListener("pointermove", moveCallTreePan, true);
          window.removeEventListener("pointerup", finishCallTreePan, true);
          window.removeEventListener("pointercancel", finishCallTreePan, true);
          try { graphCallTreeOverlay.releasePointerCapture?.(event.pointerId); } catch (_error) { /* overlay was rebuilt during the gesture */ }
        };
        const moveCallTreePan = event => {
          if (!callTreePan || event.pointerId !== callTreePan.pointerId) return;
          const canvas = graphCallTreeOverlay.querySelector(".graph-call-tree-canvas");
          if (!canvas) return;
          canvas.style.left = `${callTreePan.startLeft + event.clientX - callTreePan.startX}px`;
          canvas.style.top = `${callTreePan.startTop + event.clientY - callTreePan.startY}px`;
        };
        graphCallTreeOverlay.addEventListener("pointerdown", event => {
          const interactiveEdge = event.target.closest?.(".graph-call-tree-edge-label.is-clickable")
            || event.target.classList?.contains("graph-call-tree-edge")
            || event.target.classList?.contains("graph-call-tree-edge-hit");
          if (event.button !== 0 || event.target.closest?.(".graph-call-tree-node") || interactiveEdge) return;
          const canvas = graphCallTreeOverlay.querySelector(".graph-call-tree-canvas");
          if (!canvas) return;
          callTreePan = {
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            startLeft: Number.parseFloat(canvas.style.left) || 0,
            startTop: Number.parseFloat(canvas.style.top) || 0,
          };
          event.preventDefault();
          event.stopPropagation();
          graphCallTreeOverlay.setPointerCapture?.(event.pointerId);
          window.addEventListener("pointermove", moveCallTreePan, true);
          window.addEventListener("pointerup", finishCallTreePan, true);
          window.addEventListener("pointercancel", finishCallTreePan, true);
        }, true);
        graphCallTreeOverlay.addEventListener("wheel", event => {
          if (!graphState.selectedCodeFlowId || graphState.callGraphDisplayMode !== "tree") return;
          event.preventDefault();
          const bounds = graphCallTreeOverlay.getBoundingClientRect();
          const delta = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
          const factor = Math.exp(Math.max(-120, Math.min(120, delta)) * -.0006);
          zoomCallTree(factor, event.clientX - bounds.left, event.clientY - bounds.top);
        }, { passive: false });
      }
      const active = Boolean(graphState.selectedCodeFlowId)
        && graphState.viewMode === "call-graph";
      const treeActive = active;
      const viewport = graphCallTreeOverlay.getBoundingClientRect();
      const treeSignature = treeActive
        ? [
            graphState.selectedCodeFlowId,
            graphState.callTreeDepth,
            [...graphState.callTreeExpanded].sort().join(","),
            [...graphState.callTreeCollapsed].sort().join(","),
            Math.round(viewport.width),
            Math.round(viewport.height),
          ].join("|")
        : "inactive";
      if (!preservedCamera && treeSignature === callTreeRenderSignature) return;
      callTreeRenderSignature = treeSignature;
      graphCallTreeOverlay.hidden = !treeActive;
      document.getElementById("graph")?.classList.toggle("is-call-tree-hidden", treeActive);
      [graphLayersOverlay, graphGroupsOverlay, portPathOverlay, nodeLabelOverlay]
        .forEach(element => element?.classList.toggle("is-call-tree-hidden", treeActive));
      if (!treeActive) {
        graphCallTreeOverlay.replaceChildren();
        updateCallTreeStats();
        return;
      }
      const selectedFlows = (graphData.code_flows || []).filter(flow => (
        flow.id === graphState.selectedCodeFlowId
      ));
      const callGraph = callGraphForFlows(selectedFlows);
      const edges = (callGraph?.edges || []).filter(edge => edge.source !== edge.target);
      const order = callGraph?.node_order || callGraph?.nodes || [];
      const nodes = [...new Set([...order, ...edges.flatMap(edge => [edge.source, edge.target])])];
      const treePortsByEndpointId = new Map(graphData.nodes.flatMap(node => (
        (node.ports || []).map(port => [port.endpoint_id, port])
      )));
      const selectedFlow = selectedFlows[0] || null;
      const flowById = new Map(codeFlows.map(flow => [flow.id, flow]));
      const flowIdsByOutputEndpoint = new Map();
      const outputEndpointIdsByFlow = new Map();
      codeFlows.forEach(flow => {
        const outputEndpointIds = new Set((flow.steps || [])
          .map(step => step.endpoint_id)
          .filter(endpointId => treePortsByEndpointId.get(endpointId)?.direction === "out"));
        outputEndpointIdsByFlow.set(flow.id, outputEndpointIds);
        outputEndpointIds.forEach(endpointId => {
          flowIdsByOutputEndpoint.set(endpointId, [
            ...(flowIdsByOutputEndpoint.get(endpointId) || []), flow.id,
          ]);
        });
      });
      const childrenByInputEndpoint = new Map();
      const childrenByRootService = new Map();
      const incoming = new Set();
      edges.forEach((edge, index) => {
        const child = { edge, index };
        const sourceEndpointId = edge.endpoint_ids?.[0];
        const sourceFlowIds = flowIdsByOutputEndpoint.get(sourceEndpointId) || [];
        sourceFlowIds.forEach(flowId => {
          const triggerEndpointId = flowById.get(flowId)?.steps?.[0]?.endpoint_id;
          if (!triggerEndpointId) return;
          const children = childrenByInputEndpoint.get(triggerEndpointId) || [];
          if (!children.some(item => item.index === index)) children.push(child);
          childrenByInputEndpoint.set(triggerEndpointId, children);
        });
        childrenByRootService.set(edge.source, [
          ...(childrenByRootService.get(edge.source) || []), child,
        ]);
        incoming.add(edge.target);
      });
      const roots = nodes.filter(node => !incoming.has(node));
      const rootNames = roots.length ? roots : nodes.slice(0, 1);
      const rootInputEndpointId = selectedFlow?.steps?.[0]?.endpoint_id || null;
      const persistedTreeOccurrences = Array.isArray(callGraph?.call_tree?.occurrences)
        ? callGraph.call_tree.occurrences
        : [];
      const persistedTreeById = new Map(
        persistedTreeOccurrences.map(occurrence => [occurrence.id, occurrence]),
      );
      const persistedTreeRootIds = Array.isArray(callGraph?.call_tree?.root_occurrence_ids)
        ? callGraph.call_tree.root_occurrence_ids
        : [];
      const hasPersistedTree = persistedTreeRootIds.some(rootId => persistedTreeById.has(rootId));
      const causalTransitions = callGraph?.call_tree?.transitions;
      const causalRootFlowIds = Array.isArray(callGraph?.call_tree?.root_flow_ids)
        ? callGraph.call_tree.root_flow_ids
        : [];
      const hasCausalTree = Boolean(
        causalTransitions && causalRootFlowIds.length,
      );
      const maxOccurrences = 5000;
      let occurrenceCount = 0;
      const maxDepth = Math.max(1, Math.min(8, Number(graphState.callTreeDepth) || 1));
      const makeOccurrence = (
        name,
        depth,
        ancestorInputPorts,
        inputEndpointId = null,
        pathKey = name,
        ancestorNodes = new Set(),
        definition = null,
        flowId = null,
        ancestorFlows = new Set(),
      ) => {
        const currentFlowId = definition?.flow_id || flowId;
        const currentFlow = currentFlowId ? flowById.get(currentFlowId) : null;
        const occurrence = {
          id: `call-tree-${occurrenceCount++}`,
          name,
          pathKey,
          depth,
          children: [],
          cycle: Boolean(definition?.cycle),
          continuationUnknown: Boolean(definition?.continuation_unknown)
            || Boolean(hasCausalTree && currentFlowId && !currentFlow),
          flowId: currentFlowId,
          hiddenChildrenCount: Number(definition?.hidden_children_count || 0),
          expanded: false,
          collapsed: false,
        };
        const endpointCycle = inputEndpointId && ancestorInputPorts.has(inputEndpointId);
        const fallbackCycle = !inputEndpointId && ancestorNodes.has(name);
        const flowCycle = currentFlowId && ancestorFlows.has(currentFlowId);
        if (endpointCycle || fallbackCycle || flowCycle || occurrence.cycle) {
          occurrence.cycle = true;
          return occurrence;
        }
        const outgoing = hasCausalTree
          ? (causalTransitions[currentFlowId] || []).flatMap(transition => {
            const targetFlowIds = Array.isArray(transition.target_flow_ids)
              && transition.target_flow_ids.length
              ? transition.target_flow_ids
              : [null];
            return targetFlowIds.map(targetFlowId => ({
              edge: transition.edge,
              index: 0,
              flowId: targetFlowId,
            }));
          })
          : hasPersistedTree
          ? (definition?.children || [])
            .map(childId => persistedTreeById.get(childId))
            .filter(Boolean)
            .map((childDefinition, index) => ({
              definition: childDefinition,
              edge: childDefinition.edge || null,
              index,
            }))
          : inputEndpointId
            ? (childrenByInputEndpoint.get(inputEndpointId) || [])
            : (childrenByRootService.get(name) || []).filter(({ edge }) => (
              !rootInputEndpointId
              || outputEndpointIdsByFlow.get(selectedFlow?.id)?.has(edge.endpoint_ids?.[0])
            ));
        const expanded = graphState.callTreeExpanded.has(pathKey);
        const collapsed = graphState.callTreeCollapsed.has(pathKey);
        occurrence.expanded = expanded;
        occurrence.collapsed = collapsed;
        if (occurrenceCount >= maxOccurrences) {
          occurrence.hiddenChildrenCount = outgoing.length;
          return occurrence;
        }
        if (collapsed) {
          occurrence.hiddenChildrenCount = outgoing.length;
          return occurrence;
        }
        if (depth >= Math.min(8, maxDepth + (expanded ? 1 : 0))) {
          occurrence.hiddenChildrenCount = outgoing.length;
          return occurrence;
        }
        const nextInputPorts = new Set(ancestorInputPorts);
        if (inputEndpointId) nextInputPorts.add(inputEndpointId);
        const nextNodes = new Set(ancestorNodes);
        nextNodes.add(name);
        const nextFlows = new Set(ancestorFlows);
        if (currentFlowId) nextFlows.add(currentFlowId);
        outgoing.forEach(({ edge, index, definition: childDefinition, flowId: childFlowId }) => {
          const childPathKey = childDefinition?.path_key
            || `${pathKey}>${childFlowId || edge.target}:${edge.endpoint_ids?.[1] || edge.target}`;
          const child = makeOccurrence(
            childDefinition?.name || edge.target,
            depth + 1,
            nextInputPorts,
            childDefinition?.input_endpoint_id || edge.endpoint_ids?.[1],
            childPathKey,
            nextNodes,
            childDefinition,
            childFlowId,
            nextFlows,
          );
          if (edge) child.edge = edge;
          child.edgeIndex = index;
          occurrence.children.push(child);
        });
        return occurrence;
      };
      const rootsTree = hasCausalTree
        ? causalRootFlowIds
          .map(flowId => flowById.get(flowId))
          .filter(Boolean)
          .map(rootFlow => makeOccurrence(
            rootFlow.module,
            0,
            new Set(),
            rootFlow.steps?.[0]?.endpoint_id || null,
            rootFlow.id,
            new Set(),
            null,
            rootFlow.id,
            new Set(),
          ))
        : hasPersistedTree
        ? persistedTreeRootIds
          .map(rootId => persistedTreeById.get(rootId))
          .filter(Boolean)
          .map(definition => makeOccurrence(
            definition.name,
            0,
            new Set(),
            definition.input_endpoint_id || null,
            definition.path_key || definition.name,
            new Set(),
            definition,
          ))
        : rootNames.map(name => makeOccurrence(
          name,
          0,
          new Set(),
          rootNames.length === 1 ? rootInputEndpointId : null,
        ));
      if (!rootsTree.length) {
        graphCallTreeOverlay.textContent = "Aucun appel interservice résolu.";
        updateCallTreeStats();
        return;
      }
      const treeTrigger = selectedFlow?.steps?.[0] || null;
      const treeTriggerRootName = rootsTree[0]?.name || null;
      let leafIndex = 0;
      const assignPositions = occurrence => {
        occurrence.children.forEach(assignPositions);
        occurrence.x = occurrence.depth;
        occurrence.y = occurrence.children.length
          ? occurrence.children.reduce((sum, child) => sum + child.y, 0) / occurrence.children.length
          : leafIndex++;
      };
      rootsTree.forEach(assignPositions);
      const allOccurrences = [];
      const collect = occurrence => {
        allOccurrences.push(occurrence);
        occurrence.children.forEach(collect);
      };
      rootsTree.forEach(collect);
      const visibleTreeEdgeCount = allOccurrences.reduce(
        (count, occurrence) => count + occurrence.children.length,
        0,
      );
      updateCallTreeStats(allOccurrences.length, visibleTreeEdgeCount);
      const occurrenceCounts = new Map();
      allOccurrences.forEach(occurrence => {
        occurrenceCounts.set(occurrence.name, (occurrenceCounts.get(occurrence.name) || 0) + 1);
      });
      const direction = "lr";
      const gapX = 190;
      const gapY = 105;
      const cardWidth = 110;
      const cardHeight = 70;
      const logicalWidth = direction === "lr"
        ? (Math.max(...allOccurrences.map(item => item.x)) + 1) * gapX
        : Math.max(1, leafIndex) * gapX;
      const logicalHeight = direction === "lr"
        ? Math.max(1, leafIndex) * gapY
        : (Math.max(...allOccurrences.map(item => item.x)) + 1) * gapY;
      const scale = Math.min(1, (viewport.width - 32) / logicalWidth, (viewport.height - 32) / logicalHeight);
      const offsetX = Math.max(16, (viewport.width - logicalWidth * scale) / 2);
      const offsetY = Math.max(16, (viewport.height - logicalHeight * scale) / 2);
      const position = occurrence => direction === "lr"
        ? { x: occurrence.x * gapX, y: occurrence.y * gapY }
        : { x: occurrence.y * gapX, y: occurrence.x * gapY };
      const canvas = document.createElement("div");
      canvas.className = "graph-call-tree-canvas";
      canvas.style.width = `${logicalWidth}px`;
      canvas.style.height = `${logicalHeight}px`;
      canvas.dataset.baseScale = String(scale);
      let left = offsetX;
      let top = offsetY;
      let zoom = graphState.callTreeZoom || 1;
      if (preservedCamera) {
        const centerX = preservedCamera.centerX ?? viewport.width / 2;
        const centerY = preservedCamera.centerY ?? viewport.height / 2;
        const logicalCenterX = (centerX - preservedCamera.left) / preservedCamera.totalScale;
        const logicalCenterY = (centerY - preservedCamera.top) / preservedCamera.totalScale;
        zoom = Math.max(.5, Math.min(4, preservedCamera.totalScale / scale));
        const totalScale = scale * zoom;
        left = centerX - logicalCenterX * totalScale;
        top = centerY - logicalCenterY * totalScale;
      }
      graphState.callTreeZoom = zoom;
      canvas.style.left = `${left}px`;
      canvas.style.top = `${top}px`;
      canvas.style.transform = `scale(${scale * zoom})`;
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("width", String(logicalWidth));
      svg.setAttribute("height", String(logicalHeight));
      svg.setAttribute("aria-hidden", "true");
      const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
      defs.innerHTML = '<marker id="graph-call-tree-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#7c3aed"></path></marker>';
      svg.append(defs);
      const edgeDisplayLabel = edge => {
        const protocol = edge.kind === "kafka" ? "Kafka" : edge.kind === "rest" ? "HTTP" : edge.kind || "Appel";
        const prefix = edge.order ? `#${edge.order} · ` : "";
        const label = edge.label || protocol;
        const text = `${prefix}${label} · ${protocol}`;
        return text.length > 58 ? `${text.slice(0, 55)}…` : text;
      };
      const portsByEndpointId = new Map(
        graphData.nodes.flatMap(node => (node.ports || []).map(port => [port.endpoint_id, port]))
      );
      const treePortLabels = new Map();
      const treePortCounters = new Map();
      const treePortLabel = (endpointId, direction) => {
        const port = endpointId ? portsByEndpointId.get(endpointId) : null;
        const persistedLabel = String(port?.label || "").match(direction === "out" ? /O\d+/ : /I\d+/)?.[0];
        if (persistedLabel) return persistedLabel;
        if (!endpointId) return direction === "out" ? "OUT" : "IN";
        if (!treePortLabels.has(endpointId)) {
          const next = (treePortCounters.get(direction) || 0) + 1;
          treePortCounters.set(direction, next);
          treePortLabels.set(endpointId, `${direction === "out" ? "O" : "I"}${next}`);
        }
        return treePortLabels.get(endpointId);
      };
      edges
        .slice()
        .sort((left, right) => Number(left.order || 0) - Number(right.order || 0))
        .forEach(edge => {
          treePortLabel(edge.endpoint_ids?.[0], "out");
          treePortLabel(edge.endpoint_ids?.[1], "in");
        });
      const treePort = (endpointId, direction) => {
        const port = endpointId ? portsByEndpointId.get(endpointId) : null;
        return {
          endpointId,
          direction,
          label: treePortLabel(endpointId, direction),
          protocol: port?.system === "kafka" ? "kafka" : port?.system === "rest" ? "http" : "unknown",
          type: port?.type || "Endpoint",
          method: port?.method || "Méthode Java inconnue",
          name: port?.name || "Ressource inconnue",
          messageType: port?.message_type || null,
          path: port?.path || "Source inconnue",
          line: port?.line,
        };
      };
      const clearTreeTooltip = () => flowTooltipOverlay?.replaceChildren();
      const showTreeTooltip = (className, titleText, lines, clientX, clientY) => {
        if (!flowTooltipOverlay) return;
        const tooltip = document.createElement("span");
        tooltip.className = className;
        const title = document.createElement("strong");
        title.textContent = titleText;
        tooltip.append(title);
        lines.filter(Boolean).forEach((text, index) => {
          const line = document.createElement("span");
          line.className = index === 0 ? `${className}-kind` : `${className}-detail`;
          line.textContent = text;
          tooltip.append(line);
        });
        flowTooltipOverlay.replaceChildren(tooltip);
        const bounds = tooltip.getBoundingClientRect();
        const left = Math.max(8, Math.min(window.innerWidth - bounds.width - 8, clientX - bounds.width / 2));
        const below = clientY + bounds.height + 10 <= window.innerHeight;
        tooltip.dataset.placement = below ? "bottom" : "top";
        tooltip.style.setProperty("--tooltip-arrow-left", `${Math.max(10, Math.min(bounds.width - 10, clientX - left))}px`);
        tooltip.style.left = `${left}px`;
        tooltip.style.top = `${below ? clientY + 10 : Math.max(8, clientY - bounds.height - 10)}px`;
      };
      const showTreePortTooltip = (port, clientX, clientY) => {
        const direction = port.direction === "in" ? "IN" : "OUT";
        const qualifiedMethod = String(port.method || "").trim();
        const separator = qualifiedMethod.lastIndexOf("::");
        const ownerSeparator = separator >= 0 ? separator : qualifiedMethod.lastIndexOf(".");
        const javaClass = ownerSeparator > 0 ? qualifiedMethod.slice(0, ownerSeparator) : "Classe inconnue";
        const javaMethod = ownerSeparator > 0
          ? qualifiedMethod.slice(ownerSeparator + (separator >= 0 ? 2 : 1))
          : qualifiedMethod || "Méthode inconnue";
        showTreeTooltip("graph-port-tooltip", `${port.label} · Port ${direction}`, [
          `Classe Java : ${javaClass}`,
          `Méthode Java : ${javaMethod}`,
          `${port.type} · ${port.name}`,
          `${port.path}${port.line ? `:${port.line}` : ""}`,
        ], clientX, clientY);
      };
      const treeOccurrenceTooltipLines = occurrence => {
        const lines = [
          occurrence.cycle
            ? "Microservice · cycle détecté"
            : occurrence.continuationUnknown
              ? "Microservice · suite inconnue"
              : "Microservice",
          `Niveau ${occurrence.depth + 1}${occurrence.cycle || occurrence.continuationUnknown ? " · expansion arrêtée" : ""}`,
        ];
        const occurrenceCount = occurrenceCounts.get(occurrence.name) || 1;
        if (occurrenceCount > 1) lines.push(`Appelé ${occurrenceCount} fois dans l’arbre`);
        const input = occurrence.edge && treePort(occurrence.edge.endpoint_ids?.[1], "in");
        if (input?.endpointId) lines.push(`IN ${input.label} · ${input.method}`);
        const outputs = occurrence.children
          .map(child => treePort(child.edge.endpoint_ids?.[0], "out"))
          .filter(port => port.endpointId);
        if (outputs.length) {
          const outputText = outputs
            .slice(0, 3)
            .map(port => `${port.label} · ${port.method}`)
            .join(" | ");
          lines.push(`OUT ${outputText}${outputs.length > 3 ? " …" : ""}`);
        }
        return lines;
      };
      const treeEdgeTooltip = edge => {
        const sourcePort = treePort(edge.endpoint_ids?.[0], "out");
        const targetPort = treePort(edge.endpoint_ids?.[1], "in");
        const protocol = edge.kind === "kafka" ? "Kafka" : edge.kind === "rest" ? "HTTP" : edge.kind || "Appel";
        const lines = [
          `${edge.source} → ${edge.target}`,
          `${edge.label || "Relation"} · ${protocol}`,
        ];
        if (sourcePort.endpointId) {
          lines.push(`OUT ${sourcePort.label} · ${sourcePort.method}`);
          lines.push(`Ressource / paramètres : ${sourcePort.name}`);
        }
        if (targetPort.endpointId) lines.push(`IN ${targetPort.label} · ${targetPort.method}`);
        const messageTypes = [...new Set([sourcePort.messageType, targetPort.messageType].filter(Boolean))];
        if (messageTypes.length) lines.push(`Type de message : ${messageTypes.join(" · ")}`);
        return {
          title: `Arc${edge.order ? ` #${edge.order}` : ""}`,
          lines,
        };
      };
      allOccurrences.forEach(occurrence => {
        const source = position(occurrence);
        occurrence.children.forEach(child => {
          const target = position(child);
          const labelX = direction === "lr"
            ? (source.x + cardWidth + target.x) / 2
            : (source.x + target.x + cardWidth) / 2;
          const labelY = direction === "lr"
            ? (source.y + target.y + cardHeight) / 2 - 7
            : (source.y + cardHeight + target.y) / 2 - 7;
          const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
          if (direction === "lr") {
            const startX = source.x + cardWidth;
            const endX = target.x;
            const middleX = (startX + endX) / 2;
            line.setAttribute("d", `M ${startX} ${source.y + cardHeight / 2} C ${middleX} ${source.y + cardHeight / 2}, ${middleX} ${target.y + cardHeight / 2}, ${endX} ${target.y + cardHeight / 2}`);
          } else {
            const startY = source.y + cardHeight;
            const endY = target.y;
            const middleY = (startY + endY) / 2;
            line.setAttribute("d", `M ${source.x + cardWidth / 2} ${startY} C ${source.x + cardWidth / 2} ${middleY}, ${target.x + cardWidth / 2} ${middleY}, ${target.x + cardWidth / 2} ${endY}`);
          }
          const edgeHit = line.cloneNode();
          edgeHit.classList.remove("graph-call-tree-edge");
          edgeHit.classList.add("graph-call-tree-edge-hit");
          const protocolClass = child.edge.kind === "rest" ? "is-rest" : child.edge.kind === "kafka" ? "is-kafka" : "";
          line.classList.add("graph-call-tree-edge", protocolClass);
          edgeHit.classList.add(protocolClass);
          const edgeTooltip = treeEdgeTooltip(child.edge);
          line.setAttribute("title", [edgeTooltip.title, ...edgeTooltip.lines].join(" · "));
          line.addEventListener("mouseenter", event => {
            line.classList.add("is-hovered");
            showTreeTooltip("graph-edge-tooltip", edgeTooltip.title, edgeTooltip.lines, event.clientX, event.clientY);
          });
          line.addEventListener("mousemove", event => {
            const tooltip = flowTooltipOverlay?.firstElementChild;
            if (tooltip) showTreeTooltip("graph-edge-tooltip", edgeTooltip.title, edgeTooltip.lines, event.clientX, event.clientY);
          });
          line.addEventListener("mouseleave", () => { line.classList.remove("is-hovered"); clearTreeTooltip(); });
          const topicNode = child.edge.kind === "kafka"
            ? nodeForCallTreeTopic(child.edge.label)
            : null;
          const callTreeRoute = child.edge.kind === "rest"
            ? routeForCallTreeEdge(child.edge)
            : null;
          if (topicNode || callTreeRoute) {
            line.style.cursor = "pointer";
            line.addEventListener("click", event => {
              if (!event.shiftKey) return;
              event.preventDefault();
              event.stopPropagation();
              openCallTreeEdgeInspector(child.edge);
            });
            edgeHit.addEventListener("click", event => {
              if (!event.shiftKey) return;
              event.preventDefault();
              event.stopPropagation();
              openCallTreeEdgeInspector(child.edge);
            });
          }
          svg.append(edgeHit);
          svg.append(line);
          const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
          label.classList.add("graph-call-tree-edge-label");
          label.setAttribute("x", String(labelX));
          label.setAttribute("y", String(labelY));
          label.textContent = edgeDisplayLabel(child.edge);
          if (topicNode || callTreeRoute) {
            label.classList.add("is-clickable");
            label.addEventListener("click", event => {
              if (!event.shiftKey) return;
              event.preventDefault();
              event.stopPropagation();
              openCallTreeEdgeInspector(child.edge);
            });
          }
          const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
          title.textContent = `${child.edge.order ? `Arc #${child.edge.order} · ` : ""}${child.edge.label || child.edge.kind || "Appel"}`;
          label.append(title);
          svg.append(label);
        });
      });
      canvas.append(svg);
      allOccurrences.forEach(occurrence => {
        const node = document.createElement("button");
        node.type = "button";
        const occurrenceCount = occurrenceCounts.get(occurrence.name) || 1;
        const frequencyClass = occurrenceCount >= 4 ? "is-frequency-4"
          : occurrenceCount === 3 ? "is-frequency-3"
            : occurrenceCount === 2 ? "is-frequency-2" : "";
        node.className = `graph-call-tree-node${frequencyClass ? ` ${frequencyClass}` : ""}${occurrence.cycle ? " is-cycle" : ""}`;
        const original = nodeIdForCodeFlowResource(occurrence.name, "microservice");
        const originalNode = original ? nodeDataById.get(original) : null;
        node.style.setProperty("--card-accent", originalNode?.color || "#64748b");
        const icon = document.createElement("span");
        icon.className = "graph-node-card-icon is-service";
        const name = document.createElement("span");
        name.className = "graph-node-card-name";
        name.textContent = occurrence.name;
        const kind = document.createElement("span");
        kind.className = "graph-node-card-kind";
        kind.textContent = occurrence.cycle
          ? "Microservice · cycle"
          : occurrence.continuationUnknown
            ? "Microservice · suite inconnue"
          : occurrence.hiddenChildrenCount
            ? `Microservice · +${occurrence.hiddenChildrenCount} appels`
            : "Microservice";
        node.append(icon, name, kind);
        if (treeTrigger && occurrence.depth === 0 && occurrence.name === treeTriggerRootName) {
          const triggerKind = ({
            http_entry: ["HTTP", "is-http"],
            message_entry: ["Kafka", "is-kafka"],
            cron_entry: ["Cron", "is-cron"],
          })[treeTrigger.kind] || ["Déclencheur", "is-cron"];
          const triggerBadge = document.createElement("span");
          triggerBadge.className = `graph-node-trigger-badge ${triggerKind[1]}`;
          triggerBadge.textContent = `${triggerKind[0]} · ${treeTrigger.name || "Événement inconnu"}`;
          triggerBadge.title = "Événement déclencheur du graphe d’appel sélectionné";
          node.append(triggerBadge);
        }
        const ports = [
          occurrence.edge && treePort(occurrence.edge.endpoint_ids?.[1], "in"),
          ...occurrence.children.map(child => treePort(child.edge.endpoint_ids?.[0], "out")),
        ].filter(Boolean);
        const portsByDirection = ports.reduce((groups, port) => {
          const directionPorts = groups[port.direction] || [];
          if (!directionPorts.some(existing => (
            existing.endpointId && existing.endpointId === port.endpointId
          ))) directionPorts.push(port);
          groups[port.direction] = directionPorts;
          return groups;
        }, { in: [], out: [] });
        Object.entries(portsByDirection).forEach(([direction, directionPorts]) => {
          directionPorts.forEach((port, index) => {
            const anchor = document.createElement("span");
            anchor.className = `graph-node-port-reference is-${direction} is-${port.protocol}`;
            anchor.textContent = port.label;
            anchor.title = `${direction === "in" ? "Port d’entrée" : "Port de sortie"} · ${port.label}`;
            anchor.style.setProperty(
              "--port-offset",
              `${((index + 1) / (directionPorts.length + 1)) * 100}%`
            );
            anchor.addEventListener("pointerenter", event => showTreePortTooltip(port, event.clientX, event.clientY));
            anchor.addEventListener("pointermove", event => showTreePortTooltip(port, event.clientX, event.clientY));
            anchor.addEventListener("pointerleave", clearTreeTooltip);
            node.append(anchor);
          });
        });
        if (occurrence.cycle) {
          const badge = document.createElement("span");
          badge.className = "graph-node-root-badge graph-call-tree-cycle-badge";
          badge.textContent = "Cycle";
          node.append(badge);
        }
        const canCollapse = occurrence.children.length > 0;
        if (occurrence.hiddenChildrenCount || canCollapse) {
          const more = document.createElement("span");
          more.className = `graph-call-tree-more-badge${canCollapse ? " is-expanded" : ""}`;
          more.textContent = canCollapse ? "− replier" : `+${occurrence.hiddenChildrenCount} appels`;
          more.title = canCollapse
            ? "Replier cette branche"
            : `Afficher le niveau suivant (${occurrence.hiddenChildrenCount} appels)`;
          more.addEventListener("click", event => {
            event.preventDefault();
            event.stopPropagation();
            const camera = captureCallTreeCamera();
            if (canCollapse) {
              graphState.callTreeCollapsed.add(occurrence.pathKey);
              graphState.callTreeExpanded.delete(occurrence.pathKey);
            } else {
              graphState.callTreeCollapsed.delete(occurrence.pathKey);
              graphState.callTreeExpanded.add(occurrence.pathKey);
            }
            renderCallTreeOverlay(camera);
          });
          node.append(more);
        }
        node.title = occurrence.cycle
          ? `${occurrence.name} · cycle détecté`
          : occurrence.continuationUnknown
            ? `${occurrence.name} · suite inconnue`
          : `${occurrence.name} · occurrence ${occurrence.id}`;
        const point = position(occurrence);
        node.style.left = `${point.x}px`;
        node.style.top = `${point.y}px`;
        node.addEventListener("pointerover", event => {
          if (event.target.closest?.(".graph-node-port-reference")) return;
          node.classList.add("is-hovered");
          showTreeTooltip("graph-call-tree-entity-tooltip", occurrence.name, treeOccurrenceTooltipLines(occurrence), event.clientX, event.clientY);
        });
        node.addEventListener("pointermove", event => {
          if (event.target.closest?.(".graph-node-port-reference")) return;
          showTreeTooltip("graph-call-tree-entity-tooltip", occurrence.name, treeOccurrenceTooltipLines(occurrence), event.clientX, event.clientY);
        });
        node.addEventListener("mouseleave", () => { node.classList.remove("is-hovered"); clearTreeTooltip(); });
        node.addEventListener("click", event => {
          if (!event.shiftKey) return;
          event.preventDefault();
          event.stopPropagation();
          openCallTreeNodeInspector(originalNode);
        });
        canvas.append(node);
      });
      graphCallTreeOverlay.replaceChildren(canvas);
    }
    function zoomCallTree(factor, anchorX = null, anchorY = null) {
      if (graphState.callGraphDisplayMode !== "tree") return false;
      const canvas = graphCallTreeOverlay?.querySelector(".graph-call-tree-canvas");
      if (!canvas) return false;
      const bounds = graphCallTreeOverlay.getBoundingClientRect();
      const baseScale = Number(canvas.dataset.baseScale) || 1;
      const oldZoom = graphState.callTreeZoom || 1;
      const oldTotalScale = baseScale * oldZoom;
      const x = anchorX ?? bounds.width / 2;
      const y = anchorY ?? bounds.height / 2;
      const left = Number.parseFloat(canvas.style.left) || 0;
      const top = Number.parseFloat(canvas.style.top) || 0;
      const logicalX = (x - left) / oldTotalScale;
      const logicalY = (y - top) / oldTotalScale;
      const nextZoom = Math.max(.5, Math.min(4, oldZoom * factor));
      const nextTotalScale = baseScale * nextZoom;
      graphState.callTreeZoom = nextZoom;
      canvas.style.left = `${x - logicalX * nextTotalScale}px`;
      canvas.style.top = `${y - logicalY * nextTotalScale}px`;
      canvas.style.transform = `scale(${nextTotalScale})`;
      return true;
    }
    function expandAllCallTree() {
      if (!graphState.selectedCodeFlowId || graphState.callGraphDisplayMode !== "tree") return false;
      const camera = captureCallTreeCamera();
      graphState.callTreeDepth = 8;
      graphState.callTreeExpanded = new Set();
      graphState.callTreeCollapsed = new Set();
      renderCallTreeOverlay(camera);
      syncCallTreeDepthControl();
      return true;
    }
    function syncCallTreeDepthControl() {
      const active = Boolean(graphState.selectedCodeFlowId)
        && graphState.viewMode === "call-graph"
        && graphState.callGraphDisplayMode === "tree";
      const depth = Math.max(1, Math.min(8, Number(graphState.callTreeDepth) || 1));
      const depthControl = document.getElementById("call-tree-depth-control");
      const depthValue = document.getElementById("call-tree-depth-value");
      const depthDecrease = document.getElementById("call-tree-depth-decrease");
      const depthIncrease = document.getElementById("call-tree-depth-increase");
      if (depthControl) depthControl.hidden = !active;
      if (depthValue) depthValue.textContent = String(depth);
      if (depthDecrease) depthDecrease.disabled = !active || depth <= 1;
      if (depthIncrease) depthIncrease.disabled = !active || depth >= 8;
    }
    function adjustCallTreeDepth(delta) {
      if (!graphState.selectedCodeFlowId || graphState.callGraphDisplayMode !== "tree") return false;
      const currentDepth = Number(graphState.callTreeDepth) || 1;
      const nextDepth = Math.max(1, Math.min(8, currentDepth + delta));
      if (nextDepth === currentDepth) return false;
      const camera = captureCallTreeCamera();
      graphState.callTreeDepth = nextDepth;
      renderCallTreeOverlay(camera);
      syncCallTreeDepthControl();
      return true;
    }
    const uniqueTopologyLink = (endpointId, source) => {
      const candidates = (graphLinksByEndpoint.get(endpointId) || []).filter(candidate => (
        candidate.link.source === source
      ));
      return candidates.length === 1 ? candidates[0] : null;
    };
    function pathForCodeFlow(flow) {
      const serviceId = nodeIdForCodeFlowResource(flow.module, "microservice");
      if (!serviceId) return null;
      const nodes = [];
      const edges = [];
      const localLinks = [];
      const addFirst = id => { if (!nodes.length) nodes.push(id); };
      const addHop = topologyLink => {
        const source = nodes.at(-1);
        if (!source || !topologyLink || topologyLink.link.source !== source) return false;
        nodes.push(topologyLink.link.target);
        edges.push(topologyLink);
        return true;
      };
      const steps = flow.steps || [];
      const trigger = steps[0];
      if (!trigger) return null;
      const directKafkaLink = (topic, source, target) => graphData.links
        .map((link, index) => ({ link, index }))
        .find(({ link }) => (
          link.kind === "kafka"
          && link.label === topic
          && link.source === source
          && link.target === target
        ));

      if (trigger.kind === "message_entry") {
        const consumer = nodeIdForEndpoint(trigger.endpoint_id);
        if (consumer !== serviceId) return null;
        const incoming = (graphLinksByEndpoint.get(trigger.endpoint_id) || []).filter(link => (
          link.link.target === serviceId
        ));
        if (incoming.length !== 1) return null;
        const topicNode = incoming[0].link.source;
        // Include the concrete producer before the topic so a selected call
        // graph shows the asserted service-to-service message hop.
        const producers = graphData.links
          .map((link, index) => ({ link, index }))
          .filter(({ link }) => (
            link.target === topicNode
            && link.kind === "kafka"
            && nodeDataById.get(link.source)?.kind === "microservice"
            && link.label === incoming[0].link.label
          ));
        if (producers.length === 1) {
          const direct = directKafkaLink(incoming[0].link.label, producers[0].link.source, serviceId);
          if (direct) {
            addFirst(direct.link.source);
            nodes.push(serviceId);
            edges.push({ edge: `edge-${direct.index}`, link: direct.link });
          } else {
            addFirst(serviceId);
          }
        } else {
          addFirst(serviceId);
        }
      } else {
        addFirst(serviceId);
      }

      const inputEndpointId = trigger.endpoint_id;

      for (const step of steps.slice(1)) {
        if (step.kind === "http_call") {
          const candidate = uniqueTopologyLink(step.endpoint_id, nodes.at(-1));
          if (!candidate || !["rest", "mcp_http"].includes(candidate.link.kind) || !addHop(candidate)) return null;
          if (inputEndpointId && internalOutputsByInput.get(inputEndpointId)?.has(step.endpoint_id)) {
            localLinks.push({ input_endpoint_id: inputEndpointId, output_endpoint_id: step.endpoint_id });
          }
          continue;
        }
        if (step.kind === "message_publish") {
          const publishingService = nodes.at(-1);
          if (nodeDataById.get(publishingService)?.kind !== "microservice") return null;
          const output = uniqueTopologyLink(step.endpoint_id, publishingService);
          if (!output || output.link.kind !== "kafka") return null;
          // The selected method's publication is an external effect. Project
          // every proven consumer of its concrete topic so the root service
          // does not appear isolated merely because the persisted flow ended
          // at the publish step.
          graphData.links
            .map((link, index) => ({ link, index }))
            .filter(({ link }) => (
              link.kind === "kafka"
              && link.source === publishingService
              && link.label === output.link.label
              && nodeDataById.get(link.target)?.kind === "microservice"
            ))
            .forEach(({ link, index }) => {
              if (!nodes.includes(link.target)) nodes.push(link.target);
              edges.push({ edge: `edge-${index}`, link });
            });
          if (inputEndpointId && internalOutputsByInput.get(inputEndpointId)?.has(step.endpoint_id)) {
            localLinks.push({ input_endpoint_id: inputEndpointId, output_endpoint_id: step.endpoint_id });
          }
          continue;
        }
        if (step.kind === "message_entry") {
          const target = nodeIdForEndpoint(step.endpoint_id);
          const incoming = (graphLinksByEndpoint.get(step.endpoint_id) || []).find(candidate => (
            candidate.link.target === target && candidate.link.kind === "kafka"
          ));
          const direct = incoming && directKafkaLink(incoming.link.label, nodes.at(-1), target);
          if (!direct) return null;
          nodes.push(target);
          edges.push({ edge: `edge-${direct.index}`, link: direct.link });
        }
      }
      // A same-service input → output link is also persisted topology evidence.
      // It has no Sigma edge, so keep it separately while treating the flow as
      // reconciled rather than falsely reporting a partial graph.
      return edges.length || localLinks.length ? { nodes, edges, localLinks } : null;
    }

    function showCodeFlows(flows) {
      const pathResults = flows.map(flow => {
        const globalCallGraph = callGraphForFlow(flow);
        const globalNodes = (globalCallGraph?.node_order || globalCallGraph?.nodes || [])
          .map(service => nodeIdForCodeFlowResource(service, "microservice"))
          .filter(Boolean);
        const globalPath = globalNodes.length
          ? { nodes: globalNodes, edges: [], localLinks: [] }
          : null;
        const exactPath = globalPath || callGraphPathForCodeFlow(flow) || pathForCodeFlow(flow);
        return { path: exactPath || nodePathForCodeFlow(flow), reconciled: Boolean(exactPath) };
      }).filter(result => result.path);
      const paths = pathResults.map(result => result.path);
      if (!paths.length) return;
      const path = {
        nodes: [...new Set(paths.flatMap(candidate => candidate.nodes))],
        edges: [...new Map(paths.flatMap(candidate => candidate.edges || []).map(edge => [edge.edge, edge])).values()],
        localLinks: [...new Map(paths.flatMap(candidate => candidate.localLinks || [])
          .map(link => [`${link.input_endpoint_id}:${link.output_endpoint_id}`, link])).values()],
      };
      const flow = flows[0];
      graphState.callTreeDepth = 1;
      graphState.callTreeExpanded = new Set();
      graphState.callTreeCollapsed = new Set();
      graphState.callTreeZoom = 1;
      // The owning module is the consumer for an input-triggered flow. The
      // visual root must follow the exported call-graph path instead: it is
      // the upstream producer when a single Kafka source is proven, and the
      // entry service for HTTP, fan-in, or Cron flows.
      const rootNodeId = nodeIdForCodeFlowResource(flow.module, "microservice") || path.nodes[0];
      if (!rootNodeId) return;
      // Keep the catalogue selected while revealing the selected flow behind
      // it. Switching to Graphe remains an explicit navigation action.
      if (flowsTab?.getAttribute("aria-selected") === "true") {
        setToolbarTab("flows", { showFlowGraph: true });
      } else {
        setToolbarTab("graph");
      }
      showPath(path, path.nodes, {
        codeFlow: flow,
        codeFlowRootNodeId: rootNodeId,
        codeFlowTrigger: flow.steps?.[0] || null,
        showDetails: false,
        topologyReconciled: pathResults.every(result => result.reconciled),
      });
      syncCodeFlowSelection();
    }

    function showCodeFlow(flow) {
      showCodeFlows([flow]);
    }

    function callGraphPathForCodeFlow(flow) {
      const graph = callGraphForFlow(flow);
      if (!graph || !Array.isArray(graph.node_order) || graph.node_order.length < 2) return null;
      const nodes = graph.node_order
        .map(service => nodeIdForCodeFlowResource(service, "microservice"))
        .filter(Boolean);
      if (nodes.length < 2) return null;
      const endpointIds = new Set((flow.steps || [])
        .map(step => step.endpoint_id)
        .filter(Boolean));
      const localLinks = [];
      internalOutputsByInput.forEach((outputs, inputEndpointId) => {
        if (!endpointIds.has(inputEndpointId)) return;
        outputs.forEach(outputEndpointId => {
          if (endpointIds.has(outputEndpointId)) {
            localLinks.push({
              input_endpoint_id: inputEndpointId,
              output_endpoint_id: outputEndpointId,
            });
          }
        });
      });
      return { nodes, edges: [], localLinks };
    }

    function openCodeFlowInList(flow) {
      codeFlowCycles.setAttribute("aria-pressed", "false");
      codeFlowFilter.value = flow.id;
      setToolbarTab("flows");
      renderCodeFlows();
      requestAnimationFrame(() => codeFlowsList.querySelector(`[data-flow-id="${flow.id}"]`)?.scrollIntoView({ block: "nearest" }));
    }

    function nodePathForCodeFlow(flow) {
      const serviceId = nodeIdForCodeFlowResource(flow.module, "microservice");
      if (!serviceId) return null;
      const nodes = [];
      const add = id => { if (id && nodes.at(-1) !== id) nodes.push(id); };
      const steps = flow.steps || [];
      const trigger = steps[0];
      if (!trigger) return null;
      if (trigger.kind === "message_entry") {
        const incoming = (graphLinksByEndpoint.get(trigger.endpoint_id) || []).filter(link => (
          link.link.target === serviceId
        ));
        add(incoming.length === 1 ? incoming[0].link.source : null);
      }
      add(serviceId);
      steps.slice(1).forEach(step => {
        if (step.kind === "message_publish") {
          const outgoing = uniqueTopologyLink(step.endpoint_id, nodes.at(-1));
          add(outgoing?.link.target);
        }
        if (step.kind === "message_entry") {
          const incoming = uniqueTopologyLink(step.endpoint_id, nodes.at(-1));
          add(incoming?.link.target);
        }
        if (step.kind === "http_call") {
          const outgoing = uniqueTopologyLink(step.endpoint_id, nodes.at(-1));
          add(outgoing?.link.target);
        }
      });
      if (nodes.length >= 2) return { nodes, edges: [] };
      // Keep a partially reconciled interprocedural flow selectable from the
      // persisted endpoint evidence. This focuses the graph on the involved
      // services without pretending that a missing topology edge was proven.
      const evidenceNodes = [...new Set(steps.flatMap(step => (
        step.endpoint_id ? nodeIdsByEndpoint.get(step.endpoint_id) || [] : []
      )))]
        .filter(nodeId => nodeDataById.get(nodeId)?.kind === "microservice");
      if (evidenceNodes.length >= 2) return { nodes: evidenceNodes, edges: [] };
      return nodes.length ? { nodes, edges: [] } : null;
    }

    function syncCodeFlowSelection() {
      codeFlowsList.querySelectorAll(".code-flow-item").forEach(item => {
        item.classList.toggle("is-selected", item.dataset.flowId === graphState.selectedCodeFlowId);
      });
    }

    function servicesForCodeFlow(flow) {
      const callGraphOrder = callGraphForFlow(flow)?.node_order;
      if (Array.isArray(callGraphOrder) && callGraphOrder.length) {
        const names = callGraphOrder.filter(service => (
          nodeIdForCodeFlowResource(service, "microservice") !== null
        ));
        if (names.length) return [...new Set(names)];
      }
      return [...serviceIdsForCodeFlow(flow)]
        .map(nodeId => nodeDataById.get(nodeId)?.name)
        .filter(Boolean);
    }

    function showCodeFlowItemTooltip(flow, item) {
      const overlay = document.getElementById("graph-flow-tooltips");
      if (!overlay) return;
      overlay.hidden = false;
      const tooltip = document.createElement("span");
      tooltip.className = "graph-edge-tooltip code-flow-item-tooltip";
      const trigger = flow.steps?.[0];
      const title = document.createElement("strong");
      title.textContent = `${codeFlowStepLabel(trigger?.kind)} · ${trigger?.name || "Déclencheur inconnu"}`;
      tooltip.append(title);
      const description = document.createElement("span");
      description.className = "graph-edge-tooltip-detail";
      description.textContent = codeFlowDescription(flow);
      tooltip.append(description);
      const services = servicesForCodeFlow(flow);
      const stats = codeFlowStats(flow);
      const protocol = [...codeFlowProtocols(flow)].map(value => value.toUpperCase()).join(" + ") || "inconnu";
      const status = flow.status === "cycle"
        ? "Cycle détecté"
        : flow.reconciliation === "partial" ? "Réconciliation partielle" : "Topologie réconciliée";
      [
        `Parcours : ${services.join(" → ") || flow.module || "inconnu"}`,
        `${stats[0][1]} services · ${stats[1][1]} arcs · ${stats[2][1]} étapes · ${stats[3][1]} effets`,
        `Protocole : ${protocol} · Confiance : ${flow.confidence || "inconnue"}`,
        status,
      ].forEach(text => {
        const line = document.createElement("span");
        line.className = "graph-edge-tooltip-detail";
        line.textContent = text;
        tooltip.append(line);
      });
      overlay.replaceChildren(tooltip);
      const bounds = item.getBoundingClientRect();
      const tooltipBounds = tooltip.getBoundingClientRect();
      const left = Math.max(8, Math.min(window.innerWidth - tooltipBounds.width - 8, bounds.left + bounds.width / 2 - tooltipBounds.width / 2));
      const below = bounds.bottom + tooltipBounds.height + 10 <= window.innerHeight;
      tooltip.dataset.placement = below ? "bottom" : "top";
      tooltip.style.setProperty("--tooltip-arrow-left", `${Math.max(10, Math.min(tooltipBounds.width - 10, bounds.left + bounds.width / 2 - left))}px`);
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${below ? bounds.bottom + 10 : Math.max(8, bounds.top - tooltipBounds.height - 10)}px`;
    }

    function hideCodeFlowItemTooltip() {
      const overlay = document.getElementById("graph-flow-tooltips");
      if (!overlay) return;
      overlay.replaceChildren();
      const graphCanvas = document.getElementById("graph");
      overlay.hidden = Boolean(graphCanvas?.hidden);
    }

    function codeFlowItem(flow) {
      const item = document.createElement("li");
      const selected = graphState.selectedCodeFlowId === flow.id;
      item.className = `code-flow-item${flow.status === "cycle" ? " is-cycle" : ""}${flow.reconciliation === "partial" ? " is-partial" : ""}${selected ? " is-selected" : ""}`;
      item.dataset.flowId = flow.id;
      const header = document.createElement("div");
      header.className = "code-flow-header";
      const trigger = flow.steps?.[0];
      const title = document.createElement("div");
      title.className = "reference-title code-flow-title";
      title.textContent = `${codeFlowStepLabel(trigger?.kind)} · ${trigger?.name || "Déclencheur inconnu"}`;
      header.append(title);
      const exactPath = pathForCodeFlow(flow);
      const path = exactPath || nodePathForCodeFlow(flow);
      const meta = document.createElement("div");
      meta.className = "reference-meta";
      meta.textContent = flow.module;
      const description = document.createElement("p");
      description.className = "code-flow-reason";
      description.textContent = codeFlowDescription(flow);
      const stats = document.createElement("div");
      stats.className = "code-flow-stats";
      codeFlowStats(flow).forEach(([label, value]) => {
        const stat = document.createElement("span");
        stat.className = "code-flow-stat";
        stat.innerHTML = `<strong>${value}</strong><small>${label}</small>`;
        stats.append(stat);
      });
      if (path) {
        item.tabIndex = 0;
        item.addEventListener("click", () => showCodeFlow(flow));
        item.addEventListener("keydown", event => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            showCodeFlow(flow);
          }
        });
      } else {
        item.classList.add("is-unavailable");
      }
      item.addEventListener("pointerenter", () => showCodeFlowItemTooltip(flow, item));
      item.addEventListener("pointerleave", hideCodeFlowItemTooltip);
      item.addEventListener("focus", () => showCodeFlowItemTooltip(flow, item));
      item.addEventListener("blur", hideCodeFlowItemTooltip);
      item.append(header, meta, description, stats);
      return item;
    }

    function renderCodeFlows() {
      const query = codeFlowFilter.value.trim().toLocaleLowerCase();
      const cyclesOnly = codeFlowCycles.getAttribute("aria-pressed") === "true";
      const confidence = codeFlowConfidence.value;
      const kind = codeFlowKind.value;
      const messageTypeQuery = codeFlowMessageType.value.trim().toLocaleLowerCase();
      const visible = codeFlows.filter(flow => {
        const haystack = [
          flow.id,
          flow.module,
          flow.method,
          flow.reason,
          ...(flow.steps || []).flatMap(step => [step.kind, step.name, step.path]),
        ].join(" ").toLocaleLowerCase();
        const protocols = codeFlowProtocols(flow);
        const kindMatches = kind === "all"
          || (kind === "mixed" && protocols.size > 1)
          || protocols.has(kind);
        const messageTypeMatches = !messageTypeQuery
          || [...messageTypesForCodeFlow(flow)].some(messageType => (
            messageType.toLocaleLowerCase().includes(messageTypeQuery)
          ));
        return (!query || haystack.includes(query))
          && (!cyclesOnly || flow.status === "cycle")
          && (confidence === "all" || flow.confidence === confidence)
          && kindMatches
          && messageTypeMatches;
      });
      codeFlowsList.replaceChildren(...visible.sort(sortCodeFlows).map(codeFlowItem));
      syncCodeFlowSelection();
      codeFlowsEmpty.hidden = visible.length > 0;
      codeFlowsSummary.textContent = `${visible.length} flux affiché${visible.length > 1 ? "s" : ""} sur ${codeFlows.length} · cliquez sur un flux pour ouvrir son graphe d’appel.`;
      const cycleCount = codeFlows.filter(flow => flow.status === "cycle").length;
      codeFlowCycles.textContent = `Cycles uniquement (${cycleCount})`;
      codeFlowCycles.disabled = cycleCount === 0;
      updateCodeFlowFilterSummary();
      codeFlowsTitle.textContent = cyclesOnly
        ? `Cycles détectés (${visible.length})`
        : `Flux de code (${visible.length}/${codeFlows.length})`;
    }

    codeFlowFilter.addEventListener("input", renderCodeFlows);
    codeFlowConfidence.addEventListener("change", renderCodeFlows);
    codeFlowKind.addEventListener("change", renderCodeFlows);
    codeFlowMessageType.addEventListener("input", renderCodeFlows);
    codeFlowCycles.addEventListener("click", () => {
      codeFlowCycles.setAttribute("aria-pressed", String(codeFlowCycles.getAttribute("aria-pressed") !== "true"));
      renderCodeFlows();
    });
    codeFlowFilterReset.addEventListener("click", resetCodeFlowFilters);
    document.getElementById("flows-panel").addEventListener("systemlens:flows-open", renderCodeFlows);
    renderCodeFlows();
