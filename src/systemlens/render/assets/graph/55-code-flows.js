// Ordered source module: 55-code-flows.js
    const codeFlows = Array.isArray(graphData.code_flows) ? graphData.code_flows : [];
    const codeFlowsList = document.getElementById("code-flows");
    const codeFlowsEmpty = document.getElementById("code-flows-empty");
    const codeFlowFilter = document.getElementById("code-flow-filter");
    const codeFlowsTitle = document.getElementById("code-flows-title");

    function codeFlowLocation(step) {
      return `${step.path}:${step.start_line}`;
    }

    function codeFlowItem(flow) {
      const item = document.createElement("li");
      item.className = "code-flow-item";
      const header = document.createElement("div");
      header.className = "code-flow-header";
      const title = document.createElement("div");
      title.className = "reference-title";
      title.textContent = flow.method;
      const badges = document.createElement("div");
      badges.className = "code-flow-badges";
      [flow.status || "potential", `confiance ${flow.confidence || "unknown"}`].forEach(label => {
        const badge = document.createElement("span");
        badge.className = "detail-badge";
        badge.textContent = label;
        badges.append(badge);
      });
      header.append(title, badges);
      const meta = document.createElement("div");
      meta.className = "reference-meta";
      const trigger = flow.steps?.[0];
      meta.textContent = `${flow.module} · ${trigger?.kind || "entry"} ${trigger?.name || ""} · ${flow.steps?.length - 1 || 0} effet(s)`;
      const reason = document.createElement("p");
      reason.className = "code-flow-reason";
      reason.textContent = flow.reason || "Parcours potentiel issu du code source.";
      const steps = document.createElement("ol");
      steps.className = "code-flow-steps";
      (flow.steps || []).forEach(step => {
        const stepItem = document.createElement("li");
        stepItem.className = "code-flow-step";
        const name = document.createElement("strong");
        name.textContent = `${step.kind} · ${step.name}`;
        const location = document.createElement("code");
        location.textContent = codeFlowLocation(step);
        stepItem.append(name, location);
        steps.append(stepItem);
      });
      item.append(header, meta, reason, steps);
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
      codeFlowsEmpty.hidden = visible.length > 0;
      codeFlowsTitle.textContent = `Flux de code (${codeFlows.length})`;
    }

    codeFlowFilter.addEventListener("input", renderCodeFlows);
    renderCodeFlows();
