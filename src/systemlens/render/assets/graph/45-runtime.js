// Ordered source module: 45-runtime.js
    // Aggregated Elastic APM observations (ADR-60). The overlay only annotates
    // static links, cards, tooltips, and details. It never adds a node or a
    // link and never changes layout, camera, selection, or the selected flow.
    const runtimeData = graphData.runtime || null;
    const runtimeState = { enabled: false };
    const runtimeCountFormat = new Intl.NumberFormat("fr-FR", {
      notation: "compact", maximumFractionDigits: 1,
    });
    const runtimeFailingRatio = .05;
    const runtimeKindLabels = {
      kafka_produce: "Publication Kafka",
      kafka_consume: "Consommation Kafka",
      rest_serve: "Route HTTP servie",
    };
    function runtimeOverlayEnabled() {
      return Boolean(runtimeData && runtimeState.enabled);
    }
    function runtimeOverlayActive() {
      // Architecture annotations; the selected call tree has its own hooks.
      return runtimeOverlayEnabled() && !codeFlowSelection.flowId;
    }
    function runtimeServiceName(nodeId) {
      const node = nodeDataById.get(nodeId);
      return node?.kind === "microservice" ? node.name : null;
    }
    function runtimeRowsForService(name) {
      return (runtimeData?.observations || []).filter(row => row.service === name);
    }
    function runtimeTraffic(endpointIds) {
      if (!runtimeData) return null;
      // A REST destination is measured per service pair, so several call
      // sites can share one observation. Count each observation once.
      const indexes = new Set();
      (endpointIds || []).forEach(id => (
        runtimeData.endpoint_observations?.[id] || []
      ).forEach(index => indexes.add(index)));
      if (indexes.size) {
        let calls = 0;
        let failures = 0;
        indexes.forEach(index => {
          const row = runtimeData.observations[index];
          calls += Number(row?.calls) || 0;
          failures += Number(row?.failures) || 0;
        });
        return { state: "observed", calls, failures, shared: indexes.size < (endpointIds || []).length };
      }
      const silent = new Set(runtimeData.silent_endpoint_ids || []);
      if ((endpointIds || []).length && endpointIds.every(id => silent.has(id))) return { state: "silent" };
      return null;
    }
    function runtimeLinkTraffic(link) {
      const traffic = runtimeTraffic(link?.endpoint_ids);
      if (traffic || link?.kind !== "rest") return traffic;
      // Outbound calls are not static-only endpoints. Silence is asserted only
      // when the caller's telemetry already reports some HTTP destination.
      const caller = runtimeServiceName(link.source);
      return caller && (runtimeData?.rest_call_covered || []).includes(caller) ? { state: "silent" } : null;
    }
    function runtimeTrafficLabel(traffic) {
      if (traffic.state === "silent") return "Aucun trafic observé sur la fenêtre";
      const calls = `${runtimeCountFormat.format(traffic.calls)} appel${traffic.calls > 1 ? "s" : ""}`;
      if (!traffic.failures) return calls;
      const ratio = traffic.calls ? traffic.failures / traffic.calls * 100 : 0;
      return `${calls} · ${ratio.toLocaleString("fr-FR", { maximumFractionDigits: 1 })} % d’échecs`;
    }
    function runtimeWindowLabel() {
      const collection = runtimeData?.window;
      if (!collection) return "";
      const day = value => String(value || "").slice(0, 10);
      const collected = Date.parse(collection.collected_at || "");
      const ageDays = Number.isFinite(collected) ? Math.max(0, Math.floor((Date.now() - collected) / 86400000)) : null;
      const age = ageDays === null ? "" : ageDays === 0 ? " · collecte du jour" : ` · collecte il y a ${ageDays} j`;
      return `${day(collection.start)} → ${day(collection.end)}${age} · ${collection.origin}`;
    }
    function decorateRuntimeArchitecturePath(path, link) {
      if (!runtimeOverlayActive()) return;
      const traffic = runtimeLinkTraffic(link);
      if (!traffic) return;
      if (traffic.state === "silent") {
        path.classList.add("is-runtime-silent");
        return;
      }
      path.classList.add("is-runtime-observed");
      path.style.setProperty("--runtime-stroke", `${(1.8 + Math.min(3.2, Math.log10(traffic.calls + 1) * .8)).toFixed(2)}px`);
      if (traffic.calls && traffic.failures / traffic.calls >= runtimeFailingRatio) path.classList.add("is-runtime-failing");
    }
    function runtimeEdgeData(link, data) {
      if (!runtimeOverlayActive() || !link) return data;
      const traffic = runtimeLinkTraffic(link);
      if (!traffic) return data;
      if (traffic.state === "silent") return { ...data, color: "#cbd5e1" };
      return { ...data, size: (data.size || 2) * 1.6 };
    }
    function appendRuntimeLinkTooltip(tooltip, link, addLine) {
      if (!runtimeOverlayActive()) return;
      const traffic = runtimeLinkTraffic(link);
      if (!traffic) return;
      addLine(tooltip, `APM · ${runtimeTrafficLabel(traffic)}`, "graph-edge-tooltip-runtime");
      if (traffic.state === "observed" && link.kind === "rest") {
        addLine(tooltip, "Mesuré par couple de services, minimum échantillonné", "graph-edge-tooltip-detail");
      }
    }
    function runtimeCallTreeEdgeTraffic(edge) {
      // An arc joins one output and one input endpoint. The input count is
      // per route or consumer for every source; the output count is per topic
      // or per service pair. Both are shown, never added together.
      const [outputId, inputId] = edge?.endpoint_ids || [];
      const received = inputId ? runtimeTraffic([inputId]) : null;
      const emitted = outputId ? runtimeTraffic([outputId]) : null;
      const primary = received?.state === "observed" ? received
        : emitted?.state === "observed" ? emitted
          : received || emitted;
      return { received, emitted, primary };
    }
    function decorateRuntimeCallTreeEdge(line, edge) {
      if (!runtimeOverlayEnabled()) return;
      const { primary } = runtimeCallTreeEdgeTraffic(edge);
      if (!primary) return;
      if (primary.state === "silent") {
        line.classList.add("is-runtime-silent");
        return;
      }
      line.classList.add("is-runtime-observed");
      line.style.setProperty("--runtime-stroke", `${(1.8 + Math.min(3.2, Math.log10(primary.calls + 1) * .8)).toFixed(2)}px`);
      if (primary.calls && primary.failures / primary.calls >= runtimeFailingRatio) line.classList.add("is-runtime-failing");
    }
    function runtimeCallTreeEdgeLines(edge) {
      if (!runtimeOverlayEnabled()) return [];
      const { received, emitted } = runtimeCallTreeEdgeTraffic(edge);
      const lines = [];
      if (emitted) {
        const scope = edge.kind === "rest" ? " (par couple de services)" : edge.kind === "kafka" ? " (tous consommateurs du topic)" : "";
        lines.push(`APM · émis : ${runtimeTrafficLabel(emitted)}${emitted.state === "observed" ? scope : ""}`);
      }
      if (received) {
        lines.push(`APM · reçu : ${runtimeTrafficLabel(received)}${received.state === "observed" ? " (toutes sources)" : ""}`);
      }
      return lines;
    }
    function decorateRuntimeCallTreeNode(card, inputEndpointId) {
      if (!runtimeOverlayEnabled() || !inputEndpointId) return;
      const traffic = runtimeTraffic([inputEndpointId]);
      if (!traffic) return;
      const badge = document.createElement("span");
      const failing = traffic.state === "observed" && traffic.calls
        && traffic.failures / traffic.calls >= runtimeFailingRatio;
      badge.className = `graph-node-runtime-badge graph-call-tree-runtime-badge${traffic.state === "silent" ? " is-silent" : ""}${failing ? " is-failing" : ""}`;
      badge.textContent = traffic.state === "silent" ? "APM · aucun trafic" : `APM · ${runtimeTrafficLabel(traffic)}`;
      badge.title = traffic.state === "silent"
        ? "Point d’entrée indexé sans trafic observé sur la fenêtre."
        : "Exécutions observées de ce point d’entrée, toutes sources confondues (minimum échantillonné).";
      card.classList.add("has-runtime-badge");
      card.append(badge);
    }
    function runtimeServiceSummary(name) {
      const status = runtimeData?.services?.[name];
      if (!status) return null;
      if (status === "no_telemetry") return { status, label: "Sans télémétrie APM" };
      const rows = runtimeRowsForService(name);
      const inbound = rows.filter(row => row.kind === "transaction" && ["matched", "observed_only"].includes(row.status));
      const calls = inbound.reduce((total, row) => total + (Number(row.calls) || 0), 0);
      const failures = inbound.reduce((total, row) => total + (Number(row.failures) || 0), 0);
      const gaps = rows.filter(row => row.status === "observed_only").length;
      const parts = [`APM · ${runtimeCountFormat.format(calls)} entrée${calls > 1 ? "s" : ""}`];
      if (gaps) parts.push(`${gaps} écart${gaps > 1 ? "s" : ""}`);
      return {
        status, calls, failures, gaps,
        failing: calls > 0 && failures / calls >= runtimeFailingRatio,
        label: parts.join(" · "),
      };
    }
    function decorateRuntimeCard(card, node) {
      if (!runtimeOverlayActive() || node?.kind !== "microservice") return;
      const summary = runtimeServiceSummary(node.name);
      if (!summary) return;
      const badge = document.createElement("span");
      badge.className = `graph-node-runtime-badge is-${summary.status}${summary.gaps ? " has-gaps" : ""}${summary.failing ? " is-failing" : ""}`;
      badge.textContent = summary.label;
      badge.title = summary.status === "no_telemetry"
        ? "Aucune observation APM pour ce service : absence de télémétrie, pas preuve d’inactivité."
        : "Transactions entrantes observées par Elastic APM (minimum échantillonné).";
      card.classList.add("has-runtime-badge");
      card.append(badge);
    }
    function appendRuntimeNodeTooltip(tooltip, node, addLine) {
      if (!runtimeOverlayActive() || node?.kind !== "microservice") return;
      const summary = runtimeServiceSummary(node.name);
      if (summary) addLine(tooltip, summary.label, "graph-entity-tooltip-detail");
    }
    function runtimeRowLabel(row) {
      const kind = row.kind === "transaction" ? "Entrée" : "Sortie";
      const counts = runtimeTrafficLabel({ state: "observed", calls: Number(row.calls) || 0, failures: Number(row.failures) || 0 });
      return `${kind} · ${row.target} · ${counts}${row.hint ? ` · ${row.hint}` : ""}`;
    }
    function appendRuntimeDetails(node) {
      if (!runtimeOverlayActive() || node?.kind !== "microservice") return;
      const summary = runtimeServiceSummary(node.name);
      if (!summary) return;
      const group = createDetailsGroup("Trafic observé", true);
      const rows = runtimeRowsForService(node.name);
      appendList("Fenêtre", [
        runtimeWindowLabel(),
        summary.status === "no_telemetry"
          ? "Aucune observation : télémétrie absente, pas preuve d’inactivité."
          : "Compteurs minimaux, dépendants de l’échantillonnage APM.",
      ], group);
      appendList("Rapprochées avec l’index", rows.filter(row => row.status === "matched").map(runtimeRowLabel), group);
      appendList(
        "Sans trafic sur la fenêtre",
        (runtimeData.silent || [])
          .filter(item => item.service === node.name)
          .map(item => `${runtimeKindLabels[item.integration] || item.integration} · ${item.name} · ${item.path}:${item.line}`),
        group,
      );
      appendList(
        "Non vérifiables",
        (runtimeData.unverified || [])
          .filter(item => item.service === node.name)
          .map(item => `${runtimeKindLabels[item.integration] || item.integration} · ${item.name} · ${item.reason}`),
        group,
      );
      appendList(
        "Observées hors index",
        rows.filter(row => ["observed_only", "external"].includes(row.status)).map(row => (
          `${row.status === "external" ? "Externe" : "Absente de l’index"} · ${runtimeRowLabel(row)}`
        )),
        group,
      );
      discardEmptyDetailsGroup(group);
    }
    function openRuntimeGapsInspector() {
      if (!runtimeData) return;
      openInspector("Écarts entre l’index et le trafic observé");
      const rows = runtimeData.observations || [];
      appendList("Fenêtre", [
        runtimeWindowLabel(),
        "Compteurs minimaux, dépendants de l’échantillonnage APM. Aucune arête n’est ajoutée au graphe.",
      ], inspectorBody);
      appendList(
        "Observées mais absentes de l’index",
        rows.filter(row => row.status === "observed_only").map(row => `${row.service} · ${runtimeRowLabel(row)}`),
        inspectorBody,
      );
      appendList(
        "Vers des cibles externes",
        rows.filter(row => row.status === "external").map(row => `${row.service} · ${runtimeRowLabel(row)}`),
        inspectorBody,
      );
      appendList(
        "Services APM non rapprochés (runtime.service_map)",
        (runtimeData.unresolved_services || []).map(item => (
          `${item.name} · ${item.status}${item.candidates?.length ? ` · ${item.candidates.join(", ")}` : ""}`
        )),
        inspectorBody,
      );
      appendList(
        "Services indexés sans télémétrie",
        Object.entries(runtimeData.services || {})
          .filter(([, status]) => status === "no_telemetry")
          .map(([name]) => name),
        inspectorBody,
      );
    }
    function setupRuntimeOverlay() {
      const legend = document.querySelector("#graph-legend .legend-content");
      if (!runtimeData || !legend) return;
      const section = document.createElement("div");
      section.id = "graph-runtime-legend";
      section.className = "legend-runtime";
      const toggle = document.createElement("label");
      toggle.className = "legend-row legend-runtime-toggle";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.id = "graph-runtime-toggle";
      toggle.append(checkbox, document.createTextNode("Trafic observé (Elastic APM)"));
      const windowLine = document.createElement("div");
      windowLine.className = "legend-row legend-runtime-window";
      windowLine.textContent = runtimeWindowLabel();
      const observed = document.createElement("div");
      observed.className = "legend-row";
      observed.innerHTML = '<span class="legend-line legend-runtime-observed"></span>Trafic observé, épaisseur selon le volume';
      const silent = document.createElement("div");
      silent.className = "legend-row";
      silent.innerHTML = '<span class="legend-line legend-runtime-silent"></span>Indexé, sans trafic sur la fenêtre';
      const gapCount = (runtimeData.observations || [])
        .filter(row => ["observed_only", "external"].includes(row.status)).length
        + (runtimeData.unresolved_services || []).length;
      const gaps = document.createElement("button");
      gaps.type = "button";
      gaps.id = "graph-runtime-gaps";
      gaps.className = "legend-runtime-gaps";
      gaps.textContent = `Écarts runtime · ${gapCount}`;
      gaps.addEventListener("click", openRuntimeGapsInspector);
      section.append(toggle, windowLine, observed, silent, gaps);
      legend.append(section);
      // The legend is hidden outside the graph tab, so the call-flow toolbar
      // carries a synchronized toggle for the selected call tree.
      const flowToggle = document.createElement("button");
      flowToggle.type = "button";
      flowToggle.id = "flow-runtime-toggle";
      flowToggle.className = "graph-action-label graph-secondary-action flow-runtime-toggle";
      flowToggle.textContent = "Trafic APM";
      flowToggle.title = `Afficher le trafic observé par Elastic APM · ${runtimeWindowLabel()}`;
      flowToggle.setAttribute("aria-pressed", "false");
      document.querySelector("#flow-mode-context .graph-mode-context-actions")?.prepend(flowToggle);
      const setRuntimeEnabled = enabled => {
        runtimeState.enabled = enabled;
        checkbox.checked = enabled;
        flowToggle.setAttribute("aria-pressed", String(enabled));
        flowToggle.classList.toggle("is-active", enabled);
        document.getElementById("graph")?.setAttribute("data-runtime-overlay", enabled ? "on" : "off");
        renderer?.refresh();
        requestGraphRender();
        if (codeFlowSelection.flowId) renderCallTreeOverlay();
        const selected = graphState.selectedId && nodeDataById.get(graphState.selectedId);
        if (selected?.kind === "microservice" && !details.classList.contains("is-empty")) renderDetails(selected.id);
      };
      checkbox.addEventListener("change", () => setRuntimeEnabled(checkbox.checked));
      flowToggle.addEventListener("click", () => setRuntimeEnabled(!runtimeState.enabled));
    }
