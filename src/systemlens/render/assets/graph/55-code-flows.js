// Ordered source module: 55-code-flows.js
    const codeFlows = Array.isArray(graphData.code_flows) ? graphData.code_flows : [];
    const codeFlowsList = document.getElementById("code-flows");
    const codeFlowsEmpty = document.getElementById("code-flows-empty");
    const codeFlowFilter = document.getElementById("code-flow-filter");
    const codeFlowsTitle = document.getElementById("code-flows-title");

    function codeFlowLocation(step) {
      return `${step.path}:${step.start_line}`;
    }

    function codeFlowStepLabel(kind) {
      return ({
        http_entry: "Entrée HTTP",
        message_entry: "Entrée message",
        http_call: "Appel HTTP",
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
        }
      }
      return edges.length ? { nodes, edges } : null;
    }

    function showCodeFlow(flow) {
      const path = pathForCodeFlow(flow);
      if (!path) return;
      showPath(path, path.nodes, { codeFlow: flow, showDetails: false });
      syncCodeFlowSelection();
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
      item.className = `code-flow-item${selected ? " is-selected" : ""}`;
      item.dataset.flowId = flow.id;
      const header = document.createElement("div");
      header.className = "code-flow-header";
      const title = document.createElement("div");
      title.className = "reference-title code-flow-title";
      title.textContent = flow.method;
      const badges = document.createElement("div");
      badges.className = "code-flow-badges";
      [flow.status === "potential" ? "Potentiel" : (flow.status || "Statut inconnu"), `Confiance ${codeFlowConfidenceLabel(flow.confidence)}`].forEach(label => {
        const badge = document.createElement("span");
        badge.className = "detail-badge";
        badge.textContent = label;
        badges.append(badge);
      });
      header.append(title, badges);
      const path = pathForCodeFlow(flow);
      const action = document.createElement("button");
      action.type = "button";
      action.className = "reference-action";
      action.textContent = selected ? "Affiché dans le graphe" : "Afficher dans le graphe";
      action.setAttribute("aria-pressed", String(selected));
      action.disabled = path === null;
      action.title = path
        ? "Surligner les relations de ce flux dans le graphe principal"
        : "Ce flux ne peut pas être rapproché de la topologie affichée";
      if (path) action.addEventListener("click", () => showCodeFlow(flow));
      const meta = document.createElement("div");
      meta.className = "reference-meta";
      const trigger = flow.steps?.[0];
      meta.textContent = `${flow.module} · ${codeFlowStepLabel(trigger?.kind)} : ${trigger?.name || ""} · ${flow.steps?.length - 1 || 0} effet(s)`;
      const reason = document.createElement("p");
      reason.className = "code-flow-reason";
      reason.textContent = flow.reason === "The entry point and external effects occur in the same Java method."
        ? "Le point d’entrée et les effets externes se trouvent dans la même méthode Java."
        : flow.reason || "Parcours potentiel issu du code source.";
      const steps = document.createElement("ol");
      steps.className = "code-flow-steps";
      (flow.steps || []).forEach(step => {
        const stepItem = document.createElement("li");
        stepItem.className = "code-flow-step";
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
      const visible = codeFlows.filter(flow => {
        const haystack = [
          flow.id,
          flow.module,
          flow.method,
          flow.reason,
          ...(flow.steps || []).flatMap(step => [step.kind, step.name, step.path]),
        ].join(" ").toLocaleLowerCase();
        return !query || haystack.includes(query);
      });
      codeFlowsList.replaceChildren(...visible.map(codeFlowItem));
      syncCodeFlowSelection();
      codeFlowsEmpty.hidden = visible.length > 0;
      codeFlowsTitle.textContent = `Flux de code (${codeFlows.length})`;
    }

    codeFlowFilter.addEventListener("input", renderCodeFlows);
    document.getElementById("flows-panel").addEventListener("systemlens:flows-open", renderCodeFlows);
    renderCodeFlows();
