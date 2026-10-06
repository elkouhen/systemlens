// Ordered source module: 55-code-flows.js
    const codeFlows = Array.isArray(graphData.code_flows) ? graphData.code_flows : [];
    const codeFlowsList = document.getElementById("code-flows");
    const codeFlowsEmpty = document.getElementById("code-flows-empty");
    const codeFlowFilter = document.getElementById("code-flow-filter");
    const codeFlowScope = document.getElementById("code-flow-scope");
    const codeFlowConfidence = document.getElementById("code-flow-confidence");
    const codeFlowKind = document.getElementById("code-flow-kind");
    const codeFlowMessageType = document.getElementById("code-flow-message-type");
    const codeFlowMessageTypes = document.getElementById("code-flow-message-types");
    const codeFlowCycles = document.getElementById("code-flow-cycles");
    const codeFlowsSummary = document.getElementById("code-flows-summary");
    const codeFlowFilterSummary = document.getElementById("code-flow-filter-summary");
    const codeFlowFilterReset = document.getElementById("code-flow-filter-reset");
    const codeFlowsTitle = document.getElementById("code-flows-title");
    const codeFlowCompare = document.getElementById("code-flow-compare");
    let callTreePan = null;
    let callTreePanBound = false;
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

    function compareCodeFlows(left, right) {
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
      if (codeFlowScope.value !== "inter") filters.push(codeFlowScope.options[codeFlowScope.selectedIndex]?.text || "portée");
      if (codeFlowConfidence.value !== "all") filters.push(codeFlowConfidence.options[codeFlowConfidence.selectedIndex]?.text || "confiance");
      if (codeFlowKind.value !== "all") filters.push(codeFlowKind.options[codeFlowKind.selectedIndex]?.text || "protocole");
      if (codeFlowCycles.getAttribute("aria-pressed") === "true") filters.push("cycles");
      return filters;
    }

    function updateCodeFlowFilterSummary() {
      const filters = activeCodeFlowFilters();
      codeFlowFilterSummary.textContent = filters.length
        ? `${filters.length} filtre${filters.length > 1 ? "s" : ""} actif${filters.length > 1 ? "s" : ""} · ${filters.join(" · ")}`
        : "Aucun filtre additionnel · portée inter-services par défaut";
      codeFlowFilterReset.disabled = filters.length === 0;
    }

    function resetCodeFlowFilters() {
      codeFlowFilter.value = "";
      codeFlowMessageType.value = "";
      codeFlowScope.value = "inter";
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
    const nodeIdForEndpoint = endpointId => uniqueNodeId(nodeIdsByEndpoint.get(endpointId));

    function renderCallTreeOverlay() {
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
          if (event.button !== 0 || event.target.closest?.(".graph-call-tree-node")) return;
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
      }
      const active = Boolean(graphState.selectedCodeFlowId)
        && !graphState.comparisonMode
        && graphState.viewMode === "call-graph";
      const treeActive = active && graphState.callGraphDisplayMode === "tree";
      graphCallTreeOverlay.hidden = !treeActive;
      document.getElementById("graph")?.classList.toggle("is-call-tree-hidden", treeActive);
      [graphLayersOverlay, graphGroupsOverlay, portPathOverlay, nodeLabelOverlay]
        .forEach(element => element?.classList.toggle("is-call-tree-hidden", treeActive));
      const treeButton = document.getElementById("analysis-mode-tree");
      const depthControl = document.getElementById("call-tree-depth-control");
      const directionControl = document.getElementById("call-tree-direction-control");
      if (treeButton) {
        treeButton.hidden = !active;
        treeButton.setAttribute("aria-pressed", String(treeActive));
        treeButton.textContent = treeActive ? "Vue réseau" : "Vue arbre";
      }
      if (depthControl) depthControl.hidden = !treeActive;
      if (directionControl) directionControl.hidden = !treeActive;
      if (!treeActive) {
        graphCallTreeOverlay.replaceChildren();
        return;
      }
      const selectedFlows = (graphData.code_flows || []).filter(flow => (
        (graphState.selectedCodeFlowIds?.length
          ? graphState.selectedCodeFlowIds
          : [graphState.selectedCodeFlowId]).includes(flow.id)
      ));
      const callGraph = callGraphForFlows(selectedFlows);
      const edges = (callGraph?.edges || []).filter(edge => edge.source !== edge.target);
      const order = callGraph?.node_order || callGraph?.nodes || [];
      const nodes = [...new Set([...order, ...edges.flatMap(edge => [edge.source, edge.target])])];
      const childrenBySource = new Map();
      const incoming = new Set();
      edges.forEach((edge, index) => {
        const child = { edge, index };
        childrenBySource.set(edge.source, [...(childrenBySource.get(edge.source) || []), child]);
        incoming.add(edge.target);
      });
      const roots = nodes.filter(node => !incoming.has(node));
      const rootNames = roots.length ? roots : nodes.slice(0, 1);
      let occurrenceCount = 0;
      const maxDepth = Math.max(1, Math.min(8, Number(graphState.callTreeDepth) || 5));
      const makeOccurrence = (name, depth, ancestors) => {
        const occurrence = { id: `call-tree-${occurrenceCount++}`, name, depth, children: [], cycle: false };
        if (ancestors.has(name)) {
          occurrence.cycle = true;
          return occurrence;
        }
        if (depth >= maxDepth) return occurrence;
        const nextAncestors = new Set(ancestors).add(name);
        (childrenBySource.get(name) || []).forEach(({ edge, index }) => {
          const child = makeOccurrence(edge.target, depth + 1, nextAncestors);
          child.edge = edge;
          child.edgeIndex = index;
          occurrence.children.push(child);
        });
        return occurrence;
      };
      const rootsTree = rootNames.map(name => makeOccurrence(name, 0, new Set()));
      if (!rootsTree.length) {
        graphCallTreeOverlay.textContent = "Aucun appel interservice résolu.";
        return;
      }
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
      const direction = graphState.callTreeDirection === "tb" ? "tb" : "lr";
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
      const viewport = graphCallTreeOverlay.getBoundingClientRect();
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
      canvas.style.left = `${offsetX}px`;
      canvas.style.top = `${offsetY}px`;
      canvas.style.transform = `scale(${scale})`;
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
          line.classList.add("graph-call-tree-edge");
          svg.append(line);
          const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
          label.classList.add("graph-call-tree-edge-label");
          label.setAttribute("x", String(labelX));
          label.setAttribute("y", String(labelY));
          label.textContent = edgeDisplayLabel(child.edge);
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
        node.className = `graph-call-tree-node${occurrence.cycle ? " is-cycle" : ""}`;
        const original = nodeIdForCodeFlowResource(occurrence.name, "microservice");
        node.textContent = occurrence.name;
        node.title = occurrence.cycle
          ? `${occurrence.name} · cycle détecté`
          : `${occurrence.name} · occurrence ${occurrence.id}`;
        const point = position(occurrence);
        node.style.left = `${point.x}px`;
        node.style.top = `${point.y}px`;
        node.addEventListener("click", () => { if (original) selectNode(original); });
        canvas.append(node);
      });
      graphCallTreeOverlay.replaceChildren(canvas);
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

    // The default Flux tab is an inter-service navigation surface. The scope
    // selector below can broaden or narrow it to persisted local paths.
    const interServiceCodeFlows = codeFlows.filter(flow => {
      const path = pathForCodeFlow(flow);
      if (path) {
        const services = new Set(path.nodes.filter(nodeId => (
          nodeDataById.get(nodeId)?.kind === "microservice"
        )));
        if (services.size >= 2) return true;
      }
      // A persisted interprocedural flow is still useful evidence when one of
      // its topology edges is unresolved or absent from the export. Do not
      // hide it merely because the graph cannot prove a complete visual path.
      return serviceIdsForCodeFlow(flow).size >= 2;
    });
    const localCodeFlows = codeFlows.filter(flow => {
      const services = serviceIdsForCodeFlow(flow);
      const endpointIds = [...new Set((flow.steps || [])
        .map(step => step.endpoint_id)
        .filter(Boolean))];
      return endpointIds.length >= 2 && services.size === 1;
    });

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
        codeFlows: flows,
        codeFlowRootNodeId: rootNodeId,
        codeFlowTrigger: flow.steps?.[0] || null,
        showDetails: false,
        topologyReconciled: pathResults.every(result => result.reconciled),
      });
      renderComparisonGraphs(flows);
      syncCodeFlowSelection();
    }

    function showCodeFlow(flow) {
      showCodeFlows([flow]);
    }

    function renderComparisonGraphs(flows) {
      const comparison = document.getElementById("graph-comparison");
      if (!comparison) return;
      const graphElements = [
        graphCanvas,
        document.getElementById("graph-layers"),
        document.getElementById("graph-groups"),
        document.getElementById("graph-port-paths"),
        document.getElementById("graph-node-labels"),
        document.getElementById("graph-flow-tooltips"),
      ];
      graphElements.forEach(element => { if (element) element.hidden = flows.length > 1; });
      comparison.replaceChildren();
      comparison.hidden = flows.length < 2;
      if (flows.length < 2) return;
      const serviceCounts = new Map();
      flows.forEach(flow => {
        const graph = callGraphForFlow(flow) || {};
        [...new Set(graph.node_order || graph.nodes || [])].forEach(name => {
          serviceCounts.set(name, (serviceCounts.get(name) || 0) + 1);
        });
      });
      flows.forEach((flow, flowIndex) => {
        const graph = callGraphForFlow(flow) || {};
        const names = [...new Set(graph.node_order || graph.nodes || [])];
        const edges = (graph.edges || []).filter(edge => names.includes(edge.source) && names.includes(edge.target));
        const portForEndpoint = endpointId => [...nodeDataById.values()]
          .flatMap(candidate => candidate.ports || [])
          .find(candidate => candidate.endpoint_id === endpointId);
        const flowPortLabels = new Map();
        const flowPortCounters = new Map();
        edges
          .slice()
          .sort((left, right) => Number(left.order || 0) - Number(right.order || 0))
          .forEach(edge => {
            [[edge.endpoint_ids?.[0], "out"], [edge.endpoint_ids?.[1], "in"]].forEach(([endpointId, direction]) => {
              if (!endpointId || flowPortLabels.has(endpointId)) return;
              const next = (flowPortCounters.get(direction) || 0) + 1;
              flowPortCounters.set(direction, next);
              flowPortLabels.set(endpointId, `${direction === "out" ? "O" : "I"}${next}`);
            });
          });
        const portCode = (endpointId, direction) => {
          const port = portForEndpoint(endpointId);
          if (flowPortLabels.has(endpointId)) return flowPortLabels.get(endpointId);
          return port?.label?.match(direction === "out" ? /O\d+/ : /I\d+/)?.[0]
            || (direction === "out" ? "OUT" : "IN");
        };
        const children = new Map();
        const incoming = new Set();
        edges.forEach(edge => {
          children.set(edge.source, [...(children.get(edge.source) || []), edge.target]);
          incoming.add(edge.target);
        });
        const roots = names.filter(name => !incoming.has(name));
        const levels = new Map((roots.length ? roots : names.slice(0, 1)).map(name => [name, 0]));
        const queue = [...levels.keys()];
        for (let index = 0; index < queue.length; index += 1) {
          const source = queue[index];
          (children.get(source) || []).forEach(target => {
            if (levels.has(target)) return;
            levels.set(target, (levels.get(source) || 0) + 1);
            queue.push(target);
          });
        }
        names.forEach((name, index) => { if (!levels.has(name)) levels.set(name, index); });
        const byLevel = new Map();
        names.forEach(name => {
          const level = levels.get(name) || 0;
          byLevel.set(level, [...(byLevel.get(level) || []), name]);
        });
        const positions = new Map();
        const portsByNode = new Map();
        const addPort = (name, endpointId, direction) => {
          if (!endpointId) return;
          const list = portsByNode.get(name) || [];
          if (!list.some(port => port.endpointId === endpointId)) list.push({ endpointId, direction });
          portsByNode.set(name, list);
        };
        edges.forEach(edge => {
          addPort(edge.source, edge.endpoint_ids?.[0], "out");
          addPort(edge.target, edge.endpoint_ids?.[1], "in");
        });
        byLevel.forEach((levelNames, level) => {
          levelNames.sort((left, right) => left.localeCompare(right));
          levelNames.forEach((name, row) => positions.set(name, {
            x: 28 + level * 180,
            y: 28 + row * 82,
          }));
        });
        const width = Math.max(360, (Math.max(...levels.values(), 0) + 1) * 180 + 80);
        const height = Math.max(280, Math.max(...[...byLevel.values()].map(items => items.length), 1) * 82 + 70);
        const panel = document.createElement("section");
        panel.className = "comparison-flow-panel";
        panel.setAttribute("aria-label", `Graphe d’appel ${flowIndex + 1}`);
        const header = document.createElement("header");
        header.className = "comparison-flow-header";
        const title = document.createElement("strong");
        const trigger = flow.steps?.[0];
        const triggerName = trigger?.kind === "http_entry"
          ? `${flow.module} · ${trigger.name}`
          : trigger?.name || "Déclencheur inconnu";
        title.textContent = `${flowIndex + 1}. ${codeFlowStepLabel(trigger?.kind)} · ${triggerName}`;
        const meta = document.createElement("span");
        const protocol = [...codeFlowProtocols(flow)].map(value => value.toUpperCase()).join(" + ") || "Protocole inconnu";
        meta.textContent = `${flow.module} · ${names.length} services · ${edges.length} arcs · ${protocol}`;
        header.append(title, meta);
        const canvas = document.createElement("div");
        canvas.className = "comparison-flow-canvas";
        canvas.style.minWidth = `${width}px`;
        canvas.style.minHeight = `${height}px`;
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.classList.add("comparison-flow-svg");
        svg.style.width = `${width}px`;
        svg.style.height = `${height}px`;
        svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
        const markerId = `comparison-arrow-${flowIndex}`;
        svg.innerHTML = `<defs><marker id="${markerId}" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#7182d4"></path></marker></defs>`;
        edges.forEach((edge, edgeIndex) => {
          const source = positions.get(edge.source);
          const target = positions.get(edge.target);
          if (!source || !target) return;
          const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
          line.setAttribute("x1", String(source.x + 112));
          line.setAttribute("y1", String(source.y + 24));
          line.setAttribute("x2", String(target.x));
          line.setAttribute("y2", String(target.y + 24));
          line.classList.add("comparison-flow-edge");
          line.setAttribute("stroke", "#7182d4");
          line.setAttribute("stroke-width", "2");
          line.setAttribute("marker-end", `url(#${markerId})`);
          const hitLine = document.createElementNS("http://www.w3.org/2000/svg", "line");
          hitLine.classList.add("comparison-flow-edge-hit");
          hitLine.setAttribute("x1", line.getAttribute("x1"));
          hitLine.setAttribute("y1", line.getAttribute("y1"));
          hitLine.setAttribute("x2", line.getAttribute("x2"));
          hitLine.setAttribute("y2", line.getAttribute("y2"));
          const label = document.createElement("span");
          label.className = "comparison-flow-edge-label";
          const sourcePort = portForEndpoint(edge.endpoint_ids?.[0]);
          const targetPort = portForEndpoint(edge.endpoint_ids?.[1]);
          const protocol = edge.kind === "kafka" ? "Kafka" : edge.kind === "rest" ? "HTTP" : edge.kind || "Relation";
          const detail = sourcePort?.message_type?.split(".").at(-1)
            || targetPort?.message_type?.split(".").at(-1)
            || sourcePort?.name
            || targetPort?.name
            || "relation";
          const portMapping = `${portCode(edge.endpoint_ids?.[0], "out")} → ${portCode(edge.endpoint_ids?.[1], "in")}`;
          label.textContent = String(edge.order ?? edgeIndex + 1);
          label.title = `${portMapping} · ${detail} · ${protocol}`;
          label.style.left = `${Math.min(source.x + 112, target.x) + 12}px`;
          label.style.top = `${(source.y + target.y) / 2 + 17}px`;
          canvas.append(label);
          const setHighlighted = highlighted => {
            line.classList.toggle("is-highlighted", highlighted);
            hitLine.classList.toggle("is-highlighted", highlighted);
            label.classList.toggle("is-highlighted", highlighted);
          };
          hitLine.addEventListener("mouseenter", () => setHighlighted(true));
          hitLine.addEventListener("mouseleave", () => setHighlighted(false));
          label.addEventListener("mouseenter", () => setHighlighted(true));
          label.addEventListener("mouseleave", () => setHighlighted(false));
          svg.append(line, hitLine);
        });
        canvas.append(svg);
        positions.forEach((position, name) => {
          const node = document.createElement("div");
          node.className = "comparison-flow-node graph-node-card-label is-code-flow-node";
          node.style.left = `${position.x}px`;
          node.style.top = `${position.y}px`;
          const icon = document.createElement("span");
          icon.className = "graph-node-card-icon is-service";
          const nodeName = document.createElement("span");
          nodeName.className = "graph-node-card-name";
          nodeName.textContent = name;
          const kind = document.createElement("span");
          kind.className = "graph-node-card-kind";
          kind.textContent = "Microservice";
          node.append(icon, nodeName, kind);
          if (name === flow.module) {
            const rootBadge = document.createElement("span");
            rootBadge.className = "graph-node-root-badge";
            rootBadge.textContent = "Racine";
            node.append(rootBadge);
            const trigger = flow.steps?.[0];
            if (trigger?.name) {
              const triggerBadge = document.createElement("span");
              const protocol = trigger.kind === "http_entry" ? "HTTP" : trigger.kind === "cron_entry" ? "Cron" : "Kafka";
              triggerBadge.className = `graph-node-trigger-badge is-${protocol.toLowerCase()}`;
              triggerBadge.textContent = `${protocol} · ${trigger.name}`;
              triggerBadge.title = `Déclencheur ${protocol} · ${trigger.name}`;
              node.append(triggerBadge);
            }
          }
          if ((serviceCounts.get(name) || 0) > 1) {
            const shared = document.createElement("span");
            shared.className = "comparison-flow-shared-badge";
            shared.textContent = "Commun";
            shared.title = "Service présent dans plusieurs graphes comparés";
            node.append(shared);
          }
          (portsByNode.get(name) || []).forEach((port, index, ports) => {
            const anchor = document.createElement("span");
            const endpoint = [...nodeDataById.values()]
              .flatMap(candidate => candidate.ports || [])
              .find(candidate => candidate.endpoint_id === port.endpointId);
            const protocol = endpoint?.system === "kafka" ? "kafka" : endpoint?.system === "rest" ? "http" : "unknown";
            anchor.className = `graph-node-port-reference is-${port.direction} is-${protocol}`;
            anchor.textContent = portCode(port.endpointId, port.direction);
            anchor.style.setProperty("--port-offset", `${((index + 1) / (ports.length + 1)) * 100}%`);
            node.append(anchor);
          });
          canvas.append(node);
        });
        panel.append(header, canvas);
        comparison.append(panel);
      });
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
        const selected = graphState.selectedCodeFlowIds.includes(item.dataset.flowId);
        item.classList.toggle("is-selected", selected);
        const checkbox = item.querySelector(".code-flow-select");
        if (checkbox) checkbox.checked = selected;
      });
      const count = graphState.selectedCodeFlowIds.length;
      codeFlowCompare.disabled = count < 2;
      codeFlowCompare.textContent = count >= 2
        ? `Comparer ${count} flux`
        : "Comparer les flux sélectionnés";
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
      const comparison = document.getElementById("graph-comparison");
      overlay.hidden = Boolean(graphCanvas?.hidden && comparison?.hidden);
    }

    function codeFlowItem(flow) {
      const item = document.createElement("li");
      const selected = graphState.selectedCodeFlowIds.includes(flow.id);
      item.className = `code-flow-item${flow.status === "cycle" ? " is-cycle" : ""}${flow.reconciliation === "partial" ? " is-partial" : ""}${selected ? " is-selected" : ""}`;
      item.dataset.flowId = flow.id;
      const header = document.createElement("div");
      header.className = "code-flow-header";
      const select = document.createElement("input");
      select.type = "checkbox";
      select.className = "code-flow-select";
      select.checked = selected;
      select.title = "Ajouter ce flux à la comparaison";
      select.setAttribute("aria-label", `Ajouter le flux ${flow.id} à la comparaison`);
      select.addEventListener("click", event => event.stopPropagation());
      select.addEventListener("change", event => {
        const selectedIds = new Set(graphState.selectedCodeFlowIds);
        if (event.target.checked) selectedIds.add(flow.id);
        else selectedIds.delete(flow.id);
        graphState.selectedCodeFlowIds = [...selectedIds];
        syncCodeFlowSelection();
        const selectedFlows = codeFlows.filter(candidate => selectedIds.has(candidate.id));
        if (selectedFlows.length) showCodeFlows(selectedFlows);
        else setToolbarTab("flows");
      });
      const trigger = flow.steps?.[0];
      const title = document.createElement("div");
      title.className = "reference-title code-flow-title";
      title.textContent = `${codeFlowStepLabel(trigger?.kind)} · ${trigger?.name || "Déclencheur inconnu"}`;
      header.append(select, title);
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
      const scope = codeFlowScope.value;
      const confidence = codeFlowConfidence.value;
      const kind = codeFlowKind.value;
      const messageTypeQuery = codeFlowMessageType.value.trim().toLocaleLowerCase();
      const scopedCodeFlows = scope === "all"
        ? codeFlows
        : scope === "local"
          ? localCodeFlows
          : interServiceCodeFlows;
      const visible = scopedCodeFlows.filter(flow => {
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
      codeFlowsList.replaceChildren(...visible.sort(compareCodeFlows).map(codeFlowItem));
      syncCodeFlowSelection();
      codeFlowsEmpty.hidden = visible.length > 0;
      codeFlowsSummary.textContent = `${visible.length} flux affiché${visible.length > 1 ? "s" : ""} · ${scopedCodeFlows.length} dans cette portée · cochez au moins deux flux pour les comparer côte à côte.`;
      const cycleCount = scopedCodeFlows.filter(flow => flow.status === "cycle").length;
      codeFlowCycles.textContent = `Cycles uniquement (${cycleCount})`;
      codeFlowCycles.disabled = cycleCount === 0;
      updateCodeFlowFilterSummary();
      codeFlowsTitle.textContent = cyclesOnly
        ? `Cycles détectés (${visible.length})`
        : scope === "all"
          ? `Tous les flux (${visible.length}/${codeFlows.length})`
          : scope === "local"
            ? `Flux internes (${visible.length}/${localCodeFlows.length})`
            : `Flux inter-services (${visible.length}/${interServiceCodeFlows.length})`;
    }

    codeFlowFilter.addEventListener("input", renderCodeFlows);
    codeFlowScope.addEventListener("change", renderCodeFlows);
    codeFlowConfidence.addEventListener("change", renderCodeFlows);
    codeFlowKind.addEventListener("change", renderCodeFlows);
    codeFlowMessageType.addEventListener("input", renderCodeFlows);
    codeFlowCycles.addEventListener("click", () => {
      codeFlowCycles.setAttribute("aria-pressed", String(codeFlowCycles.getAttribute("aria-pressed") !== "true"));
      renderCodeFlows();
    });
    codeFlowFilterReset.addEventListener("click", resetCodeFlowFilters);
    codeFlowCompare.addEventListener("click", () => {
      const selected = codeFlows.filter(flow => graphState.selectedCodeFlowIds.includes(flow.id));
      if (selected.length >= 2) showCodeFlows(selected);
    });
    document.getElementById("flows-panel").addEventListener("systemlens:flows-open", renderCodeFlows);
    renderCodeFlows();
