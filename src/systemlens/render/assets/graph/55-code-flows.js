// Ordered source module: 55-code-flows.js
    const codeFlows = Array.isArray(graphData.code_flows) ? graphData.code_flows : [];
    const codeFlowsList = document.getElementById("code-flows");
    const codeFlowsEmpty = document.getElementById("code-flows-empty");
    const codeFlowFilter = document.getElementById("code-flow-filter");
    const codeFlowCycles = document.getElementById("code-flow-cycles");
    const codeFlowsTitle = document.getElementById("code-flows-title");
    function codeFlowStepLabel(kind) {
      return ({
        http_entry: "Entrée HTTP",
        message_entry: "Entrée message",
        http_call: "Appel HTTP",
        method_call: "Appel de méthode",
        message_publish: "Publication message",
        data_read: "Lecture de Data",
        data_write: "Écriture de Data",
      })[kind] || String(kind || "Étape").replaceAll("_", " ");
    }

    function codeFlowConfidenceLabel(confidence) {
      return ({ low: "faible", medium: "moyenne", high: "élevée" })[confidence]
        || confidence
        || "inconnue";
    }

    function codeFlowPriority(flow) {
      return flow.status === "cycle" ? 1 : 0;
    }

    function compareCodeFlows(left, right) {
      return codeFlowPriority(right) - codeFlowPriority(left)
        || (right.steps?.length || 0) - (left.steps?.length || 0)
        || left.id.localeCompare(right.id);
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

      if (trigger.kind === "message_entry") {
        const consumer = nodeIdForEndpoint(trigger.endpoint_id);
        if (consumer !== serviceId) return null;
        const incoming = (graphLinksByEndpoint.get(trigger.endpoint_id) || []).filter(link => (
          link.link.target === serviceId
        ));
        if (incoming.length !== 1) return null;
        addFirst(incoming[0].link.source);
        if (!addHop(incoming[0])) return null;
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
          const outgoing = uniqueTopologyLink(step.endpoint_id, publishingService);
          if (!outgoing || outgoing.link.kind !== "kafka" || !addHop(outgoing)) return null;
          if (inputEndpointId && internalOutputsByInput.get(inputEndpointId)?.has(step.endpoint_id)) {
            localLinks.push({ input_endpoint_id: inputEndpointId, output_endpoint_id: step.endpoint_id });
          }
          continue;
        }
        if (step.kind === "message_entry") {
          const incoming = uniqueTopologyLink(step.endpoint_id, nodes.at(-1));
          if (!incoming || incoming.link.kind !== "kafka" || !addHop(incoming)) return null;
        }
      }
      // A same-service input → output link is also persisted topology evidence.
      // It has no Sigma edge, so keep it separately while treating the flow as
      // reconciled rather than falsely reporting a partial graph.
      return edges.length || localLinks.length ? { nodes, edges, localLinks } : null;
    }

    // The Flux tab is an inter-service navigation surface. Retain only flows
    // whose reconciled topology path crosses a microservice boundary; local
    // method paths and flows confined to one microservice remain persisted
    // source evidence but stay out of this tab.
    const interServiceCodeFlows = codeFlows.filter(flow => {
      const path = pathForCodeFlow(flow);
      if (!path) return false;
      const services = new Set(path.nodes.filter(nodeId => (
        nodeDataById.get(nodeId)?.kind === "microservice"
      )));
      return services.size >= 2;
    });

    function showCodeFlow(flow) {
      const exactPath = pathForCodeFlow(flow);
      const path = exactPath || nodePathForCodeFlow(flow);
      if (!path) return;
      const rootNodeId = nodeIdForCodeFlowResource(flow.module, "microservice");
      if (!rootNodeId) return;
      setToolbarTab("graph");
      showPath(path, path.nodes, {
        codeFlow: flow,
        codeFlowRootNodeId: rootNodeId,
        codeFlowTrigger: flow.steps?.[0] || null,
        showDetails: false,
        topologyReconciled: Boolean(exactPath),
      });
      syncCodeFlowSelection();
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
      return nodes.length ? { nodes, edges: [] } : null;
    }

    function syncCodeFlowSelection() {
      codeFlowsList.querySelectorAll(".code-flow-item").forEach(item => {
        const selected = item.dataset.flowId === graphState.selectedCodeFlowId;
        item.classList.toggle("is-selected", selected);
      });
    }

    function codeFlowItem(flow) {
      const item = document.createElement("li");
      const selected = graphState.selectedCodeFlowId === flow.id;
      item.className = `code-flow-item${flow.status === "cycle" ? " is-cycle" : ""}${selected ? " is-selected" : ""}`;
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
      if (path) {
        item.tabIndex = 0;
        item.title = "Afficher ce graphe d’appel dans la vue Graphe";
        item.addEventListener("click", () => showCodeFlow(flow));
        item.addEventListener("keydown", event => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            showCodeFlow(flow);
          }
        });
      } else {
        item.classList.add("is-unavailable");
        item.title = "Ce graphe d’appel ne peut pas être rapproché de la topologie affichée";
      }
      item.append(header, meta);
      return item;
    }

    function renderCodeFlows() {
      const query = codeFlowFilter.value.trim().toLocaleLowerCase();
      const cyclesOnly = codeFlowCycles.getAttribute("aria-pressed") === "true";
      const visible = interServiceCodeFlows.filter(flow => {
        const haystack = [
          flow.id,
          flow.module,
          flow.method,
          flow.reason,
          ...(flow.steps || []).flatMap(step => [step.kind, step.name, step.path]),
        ].join(" ").toLocaleLowerCase();
        return (!query || haystack.includes(query)) && (!cyclesOnly || flow.status === "cycle");
      });
      const byService = new Map();
      visible.forEach(flow => {
        const trigger = flow.steps?.[0];
        const triggerKey = `${codeFlowStepLabel(trigger?.kind)} · ${trigger?.name || "Déclencheur inconnu"}`;
        const service = byService.get(flow.module) || new Map();
        const group = service.get(triggerKey) || [];
        group.push(flow);
        service.set(triggerKey, group);
        byService.set(flow.module, service);
      });
      const serviceGroups = [...byService.entries()]
        .sort(([leftName, leftTriggers], [rightName, rightTriggers]) => (
          Math.max(...[...rightTriggers.values()].flat().map(codeFlowPriority))
          - Math.max(...[...leftTriggers.values()].flat().map(codeFlowPriority))
          || leftName.localeCompare(rightName)
        ))
        .map(([service, triggers], serviceIndex) => {
          const group = document.createElement("li");
          group.className = "code-flow-service-group";
          const serviceDetails = document.createElement("details");
          serviceDetails.open = true;
          const summary = document.createElement("summary");
          const count = [...triggers.values()].reduce((total, flows) => total + flows.length, 0);
          summary.textContent = `${service} · ${count} flux · cliquer pour afficher`;
          serviceDetails.append(summary);
          [...triggers.entries()].sort(([left], [right]) => left.localeCompare(right)).forEach(([trigger, flows]) => {
            const triggerDetails = document.createElement("details");
            triggerDetails.open = true;
            const triggerSummary = document.createElement("summary");
            triggerSummary.textContent = `${trigger} · ${flows.length} flux · cliquer pour afficher`;
            const list = document.createElement("ul");
            list.className = "references-list code-flow-group-list";
            list.append(...flows.sort(compareCodeFlows).map(codeFlowItem));
            triggerDetails.append(triggerSummary, list);
            serviceDetails.append(triggerDetails);
          });
          group.append(serviceDetails);
          return group;
        });
      codeFlowsList.replaceChildren(...serviceGroups);
      syncCodeFlowSelection();
      codeFlowsEmpty.hidden = visible.length > 0;
      const cycleCount = interServiceCodeFlows.filter(flow => flow.status === "cycle").length;
      codeFlowCycles.textContent = `Cycles uniquement (${cycleCount})`;
      codeFlowCycles.disabled = cycleCount === 0;
      codeFlowsTitle.textContent = cyclesOnly
        ? `Cycles détectés (${visible.length})`
        : `Flux inter-services (${visible.length}/${interServiceCodeFlows.length})`;
    }

    codeFlowFilter.addEventListener("input", renderCodeFlows);
    codeFlowCycles.addEventListener("click", () => {
      codeFlowCycles.setAttribute("aria-pressed", String(codeFlowCycles.getAttribute("aria-pressed") !== "true"));
      renderCodeFlows();
    });
    document.getElementById("flows-panel").addEventListener("systemlens:flows-open", renderCodeFlows);
    renderCodeFlows();
