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
    const codeFlowsTitle = document.getElementById("code-flows-title");
    const codeFlowCollapse = document.getElementById("code-flow-collapse");
    const flowsPanel = document.getElementById("flows-panel");
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

    function codeFlowConfidenceLabel(confidence) {
      return ({ low: "faible", medium: "moyenne", high: "élevée" })[confidence]
        || confidence
        || "inconnue";
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

    function codeFlowSummary(flow) {
      const services = servicesForCodeFlow(flow);
      const serviceText = services.length
        ? `${services[0]}${services.length > 1 ? ` → ${services.slice(1).join(" → ")}` : ""}`
        : flow.module;
      const effects = (flow.steps || []).filter(step => ["message_publish", "http_call", "data_write"].includes(step.kind)).length;
      const confidence = codeFlowConfidenceLabel(flow.confidence);
      const alternatives = Math.max(1, Number(flow.alternative_count || 1));
      return `${serviceText} · ${services.length} service${services.length > 1 ? "s" : ""} · ${effects} effet${effects > 1 ? "s" : ""} · confiance ${confidence}${alternatives > 1 ? ` · ${alternatives} routes alternatives` : ""}`;
    }

    function compareCodeFlows(left, right) {
      return codeFlowPriority(right) - codeFlowPriority(left)
        || serviceIdsForCodeFlow(right).size - serviceIdsForCodeFlow(left).size
        || callGraphArcCount(right) - callGraphArcCount(left)
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

    function showCodeFlow(flow) {
      const globalCallGraph = callGraphForFlow(flow);
      const globalNodes = (globalCallGraph?.node_order || globalCallGraph?.nodes || [])
        .map(service => nodeIdForCodeFlowResource(service, "microservice"))
        .filter(Boolean);
      const globalPath = globalNodes.length
        ? { nodes: globalNodes, edges: [], localLinks: [] }
        : null;
      const exactPath = globalPath || callGraphPathForCodeFlow(flow) || pathForCodeFlow(flow);
      const path = exactPath || nodePathForCodeFlow(flow);
      if (!path) return;
      // The owning module is the consumer for an input-triggered flow. The
      // visual root must follow the exported call-graph path instead: it is
      // the upstream producer when a single Kafka source is proven, and the
      // entry service for HTTP, fan-in, or Cron flows.
      const rootNodeId = nodeIdForCodeFlowResource(flow.module, "microservice") || path.nodes[0];
      if (!rootNodeId) return;
      // Keep the flow list open when the user selected this flow there. This
      // lets the next flow be selected without reopening the Flux de code tab.
      if (flowsTab?.getAttribute("aria-selected") !== "true") {
        setToolbarTab("graph");
      }
      showPath(path, path.nodes, {
        codeFlow: flow,
        codeFlowRootNodeId: rootNodeId,
        codeFlowTrigger: flow.steps?.[0] || null,
        showDetails: false,
        topologyReconciled: Boolean(exactPath),
      });
      syncCodeFlowSelection();
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
        const selected = item.dataset.flowId === graphState.selectedCodeFlowId;
        item.classList.toggle("is-selected", selected);
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
      const summary = document.createElement("p");
      summary.className = "code-flow-summary";
      summary.textContent = codeFlowSummary(flow);
      const badges = document.createElement("div");
      badges.className = "code-flow-badges";
      [[`Confiance ${codeFlowConfidenceLabel(flow.confidence)}`, `is-confidence-${flow.confidence || "unknown"}`],
        [flow.reconciliation === "partial" ? "Réconciliation partielle" : "Topologie réconciliée", flow.reconciliation === "partial" ? "is-warning" : "is-complete"],
        [codeFlowProtocols(flow).size ? [...codeFlowProtocols(flow)].map(value => value.toUpperCase()).join(" + ") : "Protocole inconnu", "is-protocol"],
        [Math.max(1, Number(flow.alternative_count || 1)) > 1 ? `${flow.alternative_count} alternatives` : "Une route", "is-alternatives"]].forEach(([text, className]) => {
        const badge = document.createElement("span");
        badge.className = `detail-badge ${className}`;
        badge.textContent = text;
        badges.append(badge);
      });
      const servicesSection = document.createElement("div");
      servicesSection.className = "code-flow-services";
      const servicesLabel = document.createElement("div");
      servicesLabel.className = "code-flow-services-label";
      servicesLabel.textContent = "Services traversés";
      const serviceNames = servicesForCodeFlow(flow);
      if (serviceNames.length) {
        const serviceList = document.createElement("ol");
        serviceList.className = "code-flow-service-list";
        serviceNames.forEach(serviceName => {
          const serviceItem = document.createElement("li");
          serviceItem.textContent = serviceName;
          serviceList.append(serviceItem);
        });
        servicesSection.append(servicesLabel, serviceList);
      } else {
        const unresolved = document.createElement("div");
        unresolved.className = "code-flow-services-unresolved";
        unresolved.textContent = "Non résolus dans le graphe";
        servicesSection.append(servicesLabel, unresolved);
      }
      if (path) {
        item.tabIndex = 0;
        item.title = flow.reconciliation === "partial"
          ? "Afficher le flux ; sa réconciliation avec la topologie est partielle"
          : "Afficher ce graphe d’appel dans la vue Graphe";
        item.addEventListener("click", () => showCodeFlow(flow));
        item.addEventListener("keydown", event => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            showCodeFlow(flow);
          }
        });
      } else {
        item.classList.add("is-unavailable");
        item.title = flow.reconciliation === "partial"
          ? "Flux détecté ; le chemin complet ne peut pas être rapproché de la topologie affichée"
          : "Flux détecté ; le chemin n’est pas disponible dans la topologie affichée";
      }
      item.append(header, meta, summary, badges);
      item.append(servicesSection);
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
      const byTriggerType = new Map();
      visible.forEach(flow => {
        const trigger = flow.steps?.[0];
        const triggerKey = codeFlowStepLabel(trigger?.kind || "unknown");
        const group = byTriggerType.get(triggerKey) || [];
        group.push(flow);
        byTriggerType.set(triggerKey, group);
      });
      const triggerGroups = [...byTriggerType.entries()]
        .sort(([leftName, leftFlows], [rightName, rightFlows]) => (
          Math.max(...rightFlows.map(codeFlowPriority))
          - Math.max(...leftFlows.map(codeFlowPriority))
          || leftName.localeCompare(rightName)
        ))
        .map(([triggerType, flows]) => {
          const group = document.createElement("li");
          group.className = "code-flow-trigger-group";
          const triggerDetails = document.createElement("details");
          triggerDetails.open = true;
          const summary = document.createElement("summary");
          summary.textContent = `${triggerType} · ${flows.length} flux · cliquer pour afficher`;
          const list = document.createElement("ul");
          list.className = "references-list code-flow-group-list";
          list.append(...flows.sort(compareCodeFlows).map(codeFlowItem));
          triggerDetails.append(summary, list);
          group.append(triggerDetails);
          return group;
        });
      codeFlowsList.replaceChildren(...triggerGroups);
      syncCodeFlowSelection();
      codeFlowsEmpty.hidden = visible.length > 0;
      codeFlowsSummary.textContent = `${visible.length} flux affiché${visible.length > 1 ? "s" : ""} · ${scopedCodeFlows.length} dans cette portée · sélectionnez un flux pour ouvrir son graphe d’appel.`;
      const cycleCount = scopedCodeFlows.filter(flow => flow.status === "cycle").length;
      codeFlowCycles.textContent = `Cycles uniquement (${cycleCount})`;
      codeFlowCycles.disabled = cycleCount === 0;
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
    codeFlowCollapse.addEventListener("click", () => {
      const collapsed = flowsPanel.classList.toggle("is-code-flow-collapsed");
      codeFlowCollapse.setAttribute("aria-expanded", String(!collapsed));
      codeFlowCollapse.textContent = collapsed ? "Développer" : "Réduire";
    });
    document.getElementById("flows-panel").addEventListener("systemlens:flows-open", renderCodeFlows);
    renderCodeFlows();
