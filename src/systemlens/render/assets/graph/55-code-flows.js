// Ordered source module: 55-code-flows.js
    const codeFlows = Array.isArray(graphData.code_flows) ? graphData.code_flows : [];
    const codeFlowsList = document.getElementById("code-flows");
    const codeFlowsEmpty = document.getElementById("code-flows-empty");
    const codeFlowFilter = document.getElementById("code-flow-filter");
    const codeFlowCycles = document.getElementById("code-flow-cycles");
    const codeFlowsTitle = document.getElementById("code-flows-title");

    function codeFlowLocation(step) {
      return `${step.path}:${step.start_line}`;
    }

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

    function isGraphPortStep(step) {
      return ["http_entry", "message_entry", "http_call", "message_publish"].includes(step?.kind);
    }

    function nodeIdForCodeFlowResource(name, kind) {
      return graphData.nodes.find(node => node.name === name && node.kind === kind)?.id || null;
    }

    function graphLinkBetween(source, target, accepts = () => true) {
      const index = graphData.links.findIndex(link => (
        link.source === source && link.target === target && accepts(link)
      ));
      return index < 0 ? null : { edge: `edge-${index}`, link: graphData.links[index] };
    }

    function pathForCodeFlow(flow) {
      const serviceId = nodeIdForCodeFlowResource(flow.module, "microservice");
      if (!serviceId) return null;
      const nodes = [];
      const edges = [];
      const addFirst = id => { if (!nodes.length) nodes.push(id); };
      const addHop = (target, accepts) => {
        const source = nodes.at(-1);
        const edge = source ? graphLinkBetween(source, target, accepts) : null;
        if (!edge) return false;
        nodes.push(target);
        edges.push(edge);
        return true;
      };
      const topicId = name => nodeIdForCodeFlowResource(name, "kafka_topic");
      const serviceForEndpoint = endpointId => graphData.nodes.find(node => (
        node.kind === "microservice"
        && (node.ports || []).some(port => port.endpoint_id === endpointId)
      ))?.id || null;
      const steps = flow.steps || [];
      const trigger = steps[0];
      if (!trigger) return null;

      if (trigger.kind === "message_entry") {
        const topic = topicId(trigger.name);
        if (!topic) return null;
        addFirst(topic);
        if (!addHop(serviceId, link => link.kind === "kafka")) return null;
      } else {
        addFirst(serviceId);
      }

      for (const step of steps.slice(1)) {
        if (step.kind === "http_call") {
          const source = nodes.at(-1);
          const candidate = graphData.links.find((link, index) => (
            link.source === source
            && ["rest", "mcp_http"].includes(link.kind)
            && (String(link.label || "").includes(step.name) || link.kind === "mcp_http")
            && !edges.some(edge => edge.edge === `edge-${index}`)
          ));
          if (!candidate || !addHop(candidate.target, link => link === candidate)) return null;
          continue;
        }
        if (step.kind === "message_publish") {
          if (nodes.at(-1) !== serviceId && !addHop(serviceId, link => link.kind === "kafka")) return null;
          const topic = topicId(step.name);
          if (!topic || !addHop(topic, link => link.kind === "kafka")) return null;
          continue;
        }
        if (step.kind === "message_entry") {
          const topic = topicId(step.name);
          const consumer = serviceForEndpoint(step.endpoint_id);
          if (!topic || !consumer) return null;
          if (nodes.at(-1) !== topic && !addHop(topic, link => link.kind === "kafka")) return null;
          if (!addHop(consumer, link => link.kind === "kafka")) return null;
        }
      }
      return edges.length ? { nodes, edges } : null;
    }

    function showCodeFlow(flow) {
      const exactPath = pathForCodeFlow(flow);
      const path = exactPath || nodePathForCodeFlow(flow);
      if (!path) return;
      setToolbarTab("graph");
      showPath(path, path.nodes, {
        codeFlow: flow,
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
      const serviceForEndpoint = endpointId => graphData.nodes.find(node => (
        node.kind === "microservice"
        && (node.ports || []).some(port => port.endpoint_id === endpointId)
      ));
      const steps = flow.steps || [];
      const trigger = steps[0];
      if (!trigger) return null;
      if (trigger.kind === "message_entry") {
        add(nodeIdForCodeFlowResource(trigger.name, "kafka_topic"));
      }
      add(serviceId);
      steps.slice(1).forEach(step => {
        if (step.kind === "message_publish") add(nodeIdForCodeFlowResource(step.name, "kafka_topic"));
        if (step.kind === "message_entry") {
          add(nodeIdForCodeFlowResource(step.name, "kafka_topic"));
          add(serviceForEndpoint(step.endpoint_id)?.id);
        }
        if (step.kind === "http_call") {
          const source = serviceForEndpoint(step.endpoint_id);
          add(source?.ports?.find(port => port.endpoint_id === step.endpoint_id)?.target?.service
            ? nodeIdForCodeFlowResource(source.ports.find(port => port.endpoint_id === step.endpoint_id).target.service, "microservice")
            : null);
        }
      });
      return nodes.length ? { nodes, edges: [] } : null;
    }

    function syncCodeFlowSelection() {
      codeFlowsList.querySelectorAll(".code-flow-item").forEach(item => {
        const selected = item.dataset.flowId === graphState.selectedCodeFlowId;
        item.classList.toggle("is-selected", selected);
        const action = item.querySelector(".reference-action");
        if (!action) return;
        action.textContent = selected ? "Affiché dans le graphe" : "Afficher dans le graphe";
        action.setAttribute("aria-pressed", String(selected));
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
      const javaMethod = flow.vscode_uri ? document.createElement("a") : document.createElement("code");
      javaMethod.className = "code-flow-method";
      javaMethod.textContent = `Méthode Java : ${flow.method}`;
      if (flow.vscode_uri) {
        javaMethod.href = flow.vscode_uri;
        javaMethod.title = `Ouvrir ${flow.method} dans VS Code`;
      }
      const badges = document.createElement("div");
      badges.className = "code-flow-badges";
      [flow.status === "cycle" ? "Cycle détecté" : (flow.status === "potential" ? "Potentiel" : (flow.status || "Statut inconnu")), `Confiance ${codeFlowConfidenceLabel(flow.confidence)}`].forEach(label => {
        const badge = document.createElement("span");
        badge.className = "detail-badge";
        badge.textContent = label;
        badges.append(badge);
      });
      header.append(title, javaMethod, badges);
      const exactPath = pathForCodeFlow(flow);
      const path = exactPath || nodePathForCodeFlow(flow);
      if (path && !exactPath) {
        const topologyNotice = document.createElement("p");
        topologyNotice.className = "code-flow-topology-notice";
        topologyNotice.textContent = "Relations topologiques incomplètes : les étapes sont visibles, sans arête vérifiée.";
        header.append(topologyNotice);
      }
      const action = document.createElement("button");
      action.type = "button";
      action.className = "reference-action";
      action.textContent = selected
        ? (exactPath ? "Affiché dans le graphe" : "Étapes affichées (arêtes partielles)")
        : (exactPath ? "Afficher dans le graphe" : "Afficher les étapes (arêtes partielles)");
      action.setAttribute("aria-pressed", String(selected));
      action.disabled = path === null;
      action.title = path
        ? "Surligner les relations de ce flux dans le graphe principal"
        : "Ce flux ne peut pas être rapproché de la topologie affichée";
      if (path) {
        action.addEventListener("click", () => showCodeFlow(flow));
        item.addEventListener("click", event => {
          if (!event.target.closest("button")) showCodeFlow(flow);
        });
        item.title = "Afficher ce flux dans le graphe";
      }
      const meta = document.createElement("div");
      meta.className = "reference-meta";
      meta.textContent = `${flow.module} · ${(flow.steps?.length || 1) - 1} étape(s) après l’entrée`;
      const reason = document.createElement("p");
      reason.className = "code-flow-reason";
      reason.textContent = flow.reason === "The entry point and external effects occur in the same Java method."
        ? "Le point d’entrée et les effets externes se trouvent dans la même méthode Java."
        : flow.reason || "Parcours potentiel issu du code source.";
      const steps = document.createElement("ol");
      steps.className = "code-flow-steps";
      let portStepNumber = 0;
      (flow.steps || []).forEach(step => {
        const stepItem = document.createElement("li");
        stepItem.className = "code-flow-step";
        if (isGraphPortStep(step)) {
          portStepNumber += 1;
          stepItem.classList.add("is-graph-port-step");
          stepItem.dataset.flowPortStep = String(portStepNumber);
        }
        const summary = document.createElement("div");
        summary.className = "code-flow-step-summary";
        const kind = document.createElement("span");
        kind.className = "code-flow-step-kind";
        kind.textContent = codeFlowStepLabel(step.kind);
        const name = document.createElement("strong");
        name.textContent = step.name;
        summary.append(kind, name);
        const location = document.createElement("code");
        location.textContent = codeFlowLocation(step);
        stepItem.append(summary, location);
        steps.append(stepItem);
      });
      item.append(header, meta, reason, steps, action);
      return item;
    }

    function renderCodeFlows() {
      const query = codeFlowFilter.value.trim().toLocaleLowerCase();
      const cyclesOnly = codeFlowCycles.getAttribute("aria-pressed") === "true";
      const visible = codeFlows.filter(flow => {
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
          serviceDetails.open = Boolean(query) || serviceIndex === 0;
          const summary = document.createElement("summary");
          const count = [...triggers.values()].reduce((total, flows) => total + flows.length, 0);
          summary.textContent = `${service} · ${count} flux · cliquer pour afficher`;
          serviceDetails.append(summary);
          [...triggers.entries()].sort(([left], [right]) => left.localeCompare(right)).forEach(([trigger, flows]) => {
            const triggerDetails = document.createElement("details");
            triggerDetails.open = Boolean(query) || serviceIndex === 0;
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
      const cycleCount = codeFlows.filter(flow => flow.status === "cycle").length;
      codeFlowCycles.textContent = `Cycles uniquement (${cycleCount})`;
      codeFlowCycles.disabled = cycleCount === 0;
      codeFlowsTitle.textContent = cyclesOnly
        ? `Cycles détectés (${visible.length})`
        : `Flux de code (${visible.length}/${codeFlows.length})`;
    }

    codeFlowFilter.addEventListener("input", renderCodeFlows);
    codeFlowCycles.addEventListener("click", () => {
      codeFlowCycles.setAttribute("aria-pressed", String(codeFlowCycles.getAttribute("aria-pressed") !== "true"));
      renderCodeFlows();
    });
    document.getElementById("flows-panel").addEventListener("systemlens:flows-open", renderCodeFlows);
    renderCodeFlows();
