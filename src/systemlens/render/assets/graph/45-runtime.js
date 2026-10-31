// Ordered source module: 45-runtime.js
    // Aggregated Elastic APM observations (ADR-60). The overlay only annotates
    // static links, cards, tooltips, and details. It never adds a node or a
    // link and never changes layout, camera, selection, or the selected flow.
    // It starts enabled: an export only carries it when observations exist.
    const runtimeData = graphData.runtime || null;
    const runtimeState = { enabled: Boolean(runtimeData) };
    const runtimeCountFormat = new Intl.NumberFormat("fr-FR", {
      notation: "compact", maximumFractionDigits: 1,
    });
    const runtimeFailingRatio = .05;
    const runtimeKindLabels = {
      kafka_produce: "Publication Kafka",
      kafka_consume: "Consommation Kafka",
      rest_serve: "Route HTTP servie",
    };
    const runtimeEntryIntegrations = new Set(["rest_serve", "kafka_consume"]);
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
    function runtimeErrors(endpointIds) {
      // APM error groups attached to indexed entry points, each counted once.
      const indexes = new Set();
      (endpointIds || []).forEach(id => (
        runtimeData?.endpoint_errors?.[id] || []
      ).forEach(index => indexes.add(index)));
      return [...indexes].map(index => runtimeData.errors[index]).filter(Boolean);
    }
    function runtimeErrorCount(errors) {
      return errors.reduce((total, error) => total + (Number(error.occurrences) || 0), 0);
    }
    function runtimeErrorLine(error) {
      const where = error.transaction ? ` · ${error.transaction}` : "";
      const culprit = error.culprit ? ` · ${error.culprit}` : "";
      const seen = error.last_seen ? ` · dernière ${String(error.last_seen).slice(0, 16).replace("T", " ")}` : "";
      return `${error.exception_type || "Exception inconnue"} × ${runtimeCountFormat.format(Number(error.occurrences) || 0)}${where}${culprit}${seen}`;
    }
    function runtimeErrorsLabel(count) {
      return `${runtimeCountFormat.format(count)} erreur${count > 1 ? "s" : ""}`;
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
    function runtimeSourceLabel() {
      // The legend toggle names every source whose rows the export carries.
      const labels = (runtimeData?.window?.collections || []).map(item => item.source_label);
      return labels.length ? labels.join(" + ") : "Elastic APM";
    }
    function runtimeCompletenessLines() {
      // One line per source: window, interval, overflow or truncation state,
      // stated explicitly when none. Without a collection record (older
      // index), the state is unknown rather than silently complete.
      const collections = runtimeData?.window?.collections || [];
      if (!collections.length) return ["Complétude inconnue : collecte antérieure à l’historique des collectes."];
      return collections.map(item => {
        const parts = [
          `${item.source_label} · intervalle ${item.interval || "inconnu"}`,
          item.step ? `pas ${item.step}` : "",
          item.overflow_services?.length
            ? `débordement d’agrégation pour ${item.overflow_services.join(", ")} (compteurs minimaux)`
            : "",
          item.truncated ? "pagination tronquée" : "",
          !item.overflow_services?.length && !item.truncated ? "ni débordement ni troncature" : "",
        ].filter(Boolean);
        return parts.join(" · ");
      });
    }
    function runtimeDurationLabel(micros) {
      if (!Number.isFinite(micros)) return "n/d";
      return micros >= 1000 ? `${(micros / 1000).toLocaleString("fr-FR", { maximumFractionDigits: 0 })} ms` : `${Math.round(micros)} µs`;
    }
    function runtimeHotspotLabel(row) {
      const by = runtimeData?.hotspots?.by || "p95";
      const value = by === "failures" || by === "errors" ? `${by === "failures" ? "échecs" : "erreurs"} ${runtimeCountFormat.format(Number(row.value) || 0)}` : `${by} ${runtimeDurationLabel(Number(row.value))}`;
      const incomplete = row.completeness === "incomplete" ? " · incomplet (débordement)" : "";
      return `${row.service} · ${row.target} · ${value} · ${runtimeCountFormat.format(Number(row.calls) || 0)} appels${incomplete}`;
    }
    function runtimeHotspotEntries(rows) {
      // A row selects the entry point's service card, and its served route
      // when the index names one, exactly like a route consumer link does:
      // the related arcs then highlight through the selection. No camera move.
      return (rows || []).map(row => {
        const nodeId = `microservice:${row.service}`;
        const node = nodeDataById.get(nodeId);
        const route = (node?.http_routes || []).find(item => (row.endpoint_ids || []).includes(item.endpoint_id));
        return {
          label: runtimeHotspotLabel(row),
          title: node ? `Sélectionner ${row.service} et son point d’entrée ${row.target}` : "Service absent du graphe",
          modelNodeId: node ? nodeId : undefined,
          hotspotTarget: row.target,
          action: () => {
            if (!node) return;
            selectNode(nodeId, false, false);
            if (route) openHttpRouteInspector(node, route);
            else openArchitectureNodeInspector(node, { reset: true });
          },
        };
      });
    }
    function runtimeShortLabel(traffic, kind) {
      if (traffic.state === "silent") return "aucun trafic";
      const unit = kind === "kafka" ? "msg" : `appel${traffic.calls > 1 ? "s" : ""}`;
      const failing = traffic.calls && traffic.failures / traffic.calls >= runtimeFailingRatio;
      const ratio = failing ? ` · ${Math.round(traffic.failures / traffic.calls * 100)} % échecs` : "";
      return `${runtimeCountFormat.format(traffic.calls)} ${unit}${ratio}`;
    }
    function runtimeIsFailing(traffic) {
      return traffic?.state === "observed" && traffic.calls > 0
        && traffic.failures / traffic.calls >= runtimeFailingRatio;
    }
    function runtimeStateClass(traffic) {
      if (traffic.state === "silent") return "is-runtime-silent";
      return runtimeIsFailing(traffic) ? "is-runtime-failing" : "is-runtime-observed";
    }
    function runtimeArchitectureLinkLabel(link, start, end) {
      // Volume written on the arc itself: the overlay must read without hover.
      if (!runtimeOverlayActive()) return null;
      const traffic = runtimeLinkTraffic(link);
      if (!traffic) return null;
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.classList.add("graph-runtime-link-label", runtimeStateClass(traffic));
      // Beside a steep arc, above a flat one: never on the stroke itself.
      const steep = Math.abs(end[0] - start[0]) < Math.abs(end[1] - start[1]);
      label.setAttribute("x", String((start[0] + end[0]) / 2 + (steep ? 7 : 0)));
      label.setAttribute("y", String((start[1] + end[1]) / 2 + (steep ? 3 : -5)));
      label.setAttribute("text-anchor", steep ? "start" : "middle");
      label.textContent = runtimeShortLabel(traffic, link.kind);
      return label;
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
      if (runtimeIsFailing(traffic)) path.classList.add("is-runtime-failing");
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
      // A failing side wins: APM records a 4xx as a caller failure but a
      // server success, so the received count alone would hide it.
      const primary = runtimeIsFailing(emitted) && !runtimeIsFailing(received) ? emitted
        : received?.state === "observed" ? received
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
      if (runtimeIsFailing(primary)) line.classList.add("is-runtime-failing");
    }
    function decorateRuntimeCallTreeEdgeLabel(label, edge, x) {
      // Adds the measured volume as a first line above the protocol label so
      // the collision pass, which grows boxes upward, keeps both readable.
      if (!runtimeOverlayEnabled()) return;
      const { primary } = runtimeCallTreeEdgeTraffic(edge);
      if (!primary) return;
      const svgNamespace = "http://www.w3.org/2000/svg";
      const runtimeLine = document.createElementNS(svgNamespace, "tspan");
      runtimeLine.classList.add("graph-runtime-link-label", runtimeStateClass(primary));
      runtimeLine.setAttribute("x", String(x));
      runtimeLine.setAttribute("dy", "-1.2em");
      runtimeLine.textContent = `APM · ${runtimeShortLabel(primary, edge.kind)}`;
      const protocolLine = document.createElementNS(svgNamespace, "tspan");
      protocolLine.setAttribute("x", String(x));
      protocolLine.setAttribute("dy", "1.2em");
      protocolLine.textContent = label.textContent;
      label.replaceChildren(runtimeLine, protocolLine);
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
      const inputId = (edge?.endpoint_ids || [])[1];
      runtimeErrors(inputId ? [inputId] : []).forEach(error => lines.push(`APM · erreur : ${runtimeErrorLine(error)}`));
      return lines;
    }
    function runtimeScheduledKey(step) {
      // A scheduler trigger has no endpoint; its traffic is keyed by source.
      return step?.kind === "cron_entry" ? `cron:${step.path}:${step.start_line}` : null;
    }
    const runtimeDataKinds = {
      relational: new Set(["postgresql", "mysql", "oracle", "sqlserver", "db"]),
      mongodb: new Set(["mongodb"]),
    };
    function decorateRuntimeCallTreeDataAccess(card, serviceName, resources) {
      // APM measures database calls per service and engine, never per table:
      // the line states a service total next to the card's data accesses.
      if (!runtimeOverlayEnabled() || !resources.length) return;
      const destinations = runtimeData?.data_destinations?.[serviceName] || [];
      const kinds = new Set(resources.map(resource => (
        String(resource).startsWith("jpa:") ? "relational" : "mongodb"
      )));
      const matching = destinations.filter(item => [...kinds].some(kind => (
        runtimeDataKinds[kind].has(String(item.target).toLowerCase())
      )));
      if (!matching.length) return;
      const line = document.createElement("span");
      const failing = matching.some(item => runtimeIsFailing({ state: "observed", calls: item.calls, failures: item.failures }));
      line.className = `graph-node-runtime-badge graph-call-tree-runtime-badge graph-call-tree-runtime-data${failing ? " is-failing" : ""}`;
      line.textContent = `APM · BDD ${matching.map(item => (
        `${item.target} ${runtimeCountFormat.format(Number(item.calls) || 0)}`
      )).join(" · ")}`;
      line.title = "Appels aux bases observés pour tout le service sur la fenêtre, toutes tables et collections confondues (minimum échantillonné).";
      card.append(line);
    }
    function decorateRuntimeCallTreeNode(card, inputEndpointId) {
      if (!runtimeOverlayEnabled() || !inputEndpointId) return;
      const traffic = runtimeTraffic([inputEndpointId]);
      const errors = runtimeErrors([inputEndpointId]);
      if (!traffic && !errors.length) return;
      const badge = document.createElement("span");
      const failing = runtimeIsFailing(traffic) || errors.length > 0;
      const silent = traffic?.state === "silent";
      badge.className = `graph-node-runtime-badge graph-call-tree-runtime-badge${silent ? " is-silent" : ""}${failing ? " is-failing" : ""}`;
      const parts = [silent ? "APM · aucun trafic" : traffic ? `APM · ${runtimeTrafficLabel(traffic)}` : "APM"];
      if (errors.length) parts.push(runtimeErrorsLabel(runtimeErrorCount(errors)));
      badge.textContent = parts.join(" · ");
      badge.title = [
        silent
          ? "Point d’entrée indexé sans trafic observé sur la fenêtre."
          : "Exécutions observées de ce point d’entrée, toutes sources confondues (minimum échantillonné).",
        ...errors.map(runtimeErrorLine),
      ].join("\n");
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
      const errors = (runtimeData.errors || []).filter(error => error.service === name);
      const errorCount = runtimeErrorCount(errors);
      // Coverage of indexed entry points answers the overlay's question
      // (is the indexed code exercised?); raw volume is dominated by probes.
      const active = new Set();
      rows.filter(row => row.kind === "transaction" && row.status === "matched")
        .forEach(row => (row.endpoint_ids || []).forEach(id => active.add(id)));
      const entries = items => (items || [])
        .filter(item => item.service === name && runtimeEntryIntegrations.has(item.integration)).length;
      const silent = entries(runtimeData.silent);
      const unverified = entries(runtimeData.unverified);
      const total = active.size + silent + unverified;
      const volume = `${runtimeCountFormat.format(calls)} transaction${calls > 1 ? "s" : ""} entrante${calls > 1 ? "s" : ""}`;
      const label = total
        ? `APM · ${active.size}/${total} entrée${total > 1 ? "s" : ""} active${total > 1 ? "s" : ""}`
        : `APM · ${volume}`;
      const details = [
        total ? `${active.size} point${active.size > 1 ? "s" : ""} d’entrée indexé${active.size > 1 ? "s" : ""} avec trafic` : "",
        silent ? `${silent} sans trafic sur la fenêtre` : "",
        unverified ? `${unverified} non vérifiable${unverified > 1 ? "s" : ""}` : "",
        gaps ? `${gaps} route${gaps > 1 ? "s" : ""} observée${gaps > 1 ? "s" : ""} hors index` : "",
        errorCount ? `${runtimeErrorsLabel(errorCount)} APM (${errors.length} groupe${errors.length > 1 ? "s" : ""})` : "",
        volume,
      ].filter(Boolean);
      return {
        status, calls, failures, gaps, silent, unverified, active: active.size, total, errors, errorCount,
        failing: errorCount > 0 || (calls > 0 && failures / calls >= runtimeFailingRatio),
        label: errorCount ? `${label} · ${runtimeErrorsLabel(errorCount)}` : label,
        details,
      };
    }
    function decorateRuntimeCard(card, node) {
      if (!runtimeOverlayActive() || node?.kind !== "microservice") return;
      const summary = runtimeServiceSummary(node.name);
      if (!summary) return;
      const badge = document.createElement("span");
      const attention = summary.silent || summary.gaps;
      badge.className = `graph-node-runtime-badge is-${summary.status}${attention ? " has-gaps" : ""}${summary.failing ? " is-failing" : ""}`;
      badge.textContent = summary.label;
      badge.title = summary.status === "no_telemetry"
        ? "Aucune observation APM pour ce service : absence de télémétrie, pas preuve d’inactivité."
        : `${summary.details.join(" · ")} (Elastic APM, minimum échantillonné).`;
      card.classList.add("has-runtime-badge");
      card.append(badge);
    }
    function appendRuntimeNodeTooltip(tooltip, node, addLine) {
      if (!runtimeOverlayActive() || node?.kind !== "microservice") return;
      const summary = runtimeServiceSummary(node.name);
      if (!summary) return;
      addLine(tooltip, summary.label, "graph-entity-tooltip-detail");
      (summary.details || []).forEach(line => addLine(tooltip, line, "graph-entity-tooltip-detail"));
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
        ...runtimeCompletenessLines(),
        summary.status === "no_telemetry"
          ? "Aucune observation : télémétrie absente, pas preuve d’inactivité."
          : "Compteurs minimaux, dépendants de l’échantillonnage ; percentiles approximatifs.",
      ], group);
      appendList(
        "Points chauds",
        (runtimeData.hotspots?.rows || []).filter(row => row.service === node.name).map(runtimeHotspotLabel),
        group,
      );
      appendList("Erreurs APM", summary.errors.map(runtimeErrorLine), group);
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
    function openRuntimeHotspotsInspector() {
      if (!runtimeData) return;
      const hotspots = runtimeData.hotspots || { rows: [], not_compared: [], unverified: [] };
      openInspector("Points chauds observés");
      inspectorBody.classList.add("runtime-hotspots-inspector");
      appendList("Fenêtre", [
        runtimeWindowLabel(),
        ...runtimeCompletenessLines(),
        "Classement par p95 quand le backend fournit des percentiles, sinon par échecs. Valeurs approximatives et minimales ; aucune arête n’est ajoutée au graphe.",
      ], inspectorBody);
      appendActionList("Points chauds", runtimeHotspotEntries(hotspots.rows), inspectorBody);
      if (!(hotspots.rows || []).length) {
        appendList("Points chauds", ["Aucun point d’entrée rapproché ne porte cette mesure."], inspectorBody);
      }
      if (hotspots.without_metric) {
        appendList("Sans mesure", [`${hotspots.without_metric} point(s) d’entrée sans percentile, non classé(s).`], inspectorBody);
      }
      appendList(
        "Non comparables (hors index ou non attribuables)",
        (hotspots.not_compared || []).map(row => `${row.service} · ${row.target} · ${runtimeCountFormat.format(Number(row.calls) || 0)} appels`),
        inspectorBody,
      );
      appendList(
        "Indexés mais non vérifiables",
        (hotspots.unverified || []).map(item => `${item.service} · ${item.name} · ${item.reason}`),
        inspectorBody,
      );
    }
    function openRuntimeGapsInspector() {
      if (!runtimeData) return;
      openInspector("Écarts entre l’index et le trafic observé");
      const rows = runtimeData.observations || [];
      appendList("Fenêtre", [
        runtimeWindowLabel(),
        ...runtimeCompletenessLines(),
        "Compteurs minimaux, dépendants de l’échantillonnage. Aucune arête n’est ajoutée au graphe.",
      ], inspectorBody);
      appendList(
        "Erreurs APM (messages non conservés)",
        (runtimeData.errors || []).map(error => `${error.service || error.service_name} · ${runtimeErrorLine(error)}`),
        inspectorBody,
      );
      appendList(
        "Indexées sans trafic sur la fenêtre",
        (runtimeData.silent || []).map(item => (
          `${item.service} · ${runtimeKindLabels[item.integration] || item.integration} · ${item.name} · ${item.path}:${item.line}`
        )),
        inspectorBody,
      );
      appendList(
        "Observées mais absentes de l’index",
        rows.filter(row => row.status === "observed_only").map(row => `${row.service} · ${runtimeRowLabel(row)}`),
        inspectorBody,
      );
      appendList(
        "Indexées mais non vérifiables",
        (runtimeData.unverified || []).map(item => (
          `${item.service} · ${runtimeKindLabels[item.integration] || item.integration} · ${item.name} · ${item.reason}`
        )),
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
      toggle.append(checkbox, document.createTextNode(`Trafic observé (${runtimeSourceLabel()})`));
      const windowLine = document.createElement("div");
      windowLine.className = "legend-row legend-runtime-window";
      windowLine.textContent = runtimeWindowLabel();
      const completenessLine = document.createElement("div");
      completenessLine.className = "legend-row legend-runtime-completeness";
      completenessLine.textContent = runtimeCompletenessLines().join(" · ");
      const observed = document.createElement("div");
      observed.className = "legend-row";
      observed.innerHTML = '<span class="legend-line legend-runtime-observed"></span>Trafic observé, épaisseur selon le volume';
      const silent = document.createElement("div");
      silent.className = "legend-row";
      silent.innerHTML = '<span class="legend-line legend-runtime-silent"></span>Indexé, sans trafic sur la fenêtre';
      const failing = document.createElement("div");
      failing.className = "legend-row";
      failing.innerHTML = '<span class="legend-line legend-runtime-failing"></span>Trafic observé avec au moins 5 % d’échecs';
      const summary = runtimeData.summary || {};
      const summaryLine = document.createElement("div");
      summaryLine.className = "legend-row legend-runtime-summary";
      summaryLine.textContent = [
        `${summary.matched || 0} rapprochée${summary.matched > 1 ? "s" : ""}`,
        `${summary.static_only || 0} sans trafic`,
        `${summary.unverified || 0} non vérifiable${summary.unverified > 1 ? "s" : ""}`,
        `${summary.observed_only || 0} hors index`,
        ...(runtimeData.errors?.length ? [runtimeErrorsLabel(runtimeErrorCount(runtimeData.errors))] : []),
      ].join(" · ");
      const gapCount = (runtimeData.observations || [])
        .filter(row => ["observed_only", "external"].includes(row.status)).length
        + (runtimeData.unresolved_services || []).length
        + (runtimeData.silent || []).length
        + (runtimeData.unverified || []).length
        + (runtimeData.errors || []).length;
      const gaps = document.createElement("button");
      gaps.type = "button";
      gaps.id = "graph-runtime-gaps";
      gaps.className = "legend-runtime-gaps";
      gaps.textContent = `Écarts runtime · ${gapCount}`;
      gaps.addEventListener("click", openRuntimeGapsInspector);
      const hotspotCount = (runtimeData.hotspots?.rows || []).length;
      const hotspots = document.createElement("button");
      hotspots.type = "button";
      hotspots.id = "graph-runtime-hotspots";
      hotspots.className = "legend-runtime-gaps legend-runtime-hotspots";
      hotspots.textContent = `Points chauds · ${hotspotCount}`;
      hotspots.title = "Points d’entrée classés par latence p95 ou par échecs (valeurs approximatives et minimales).";
      hotspots.addEventListener("click", openRuntimeHotspotsInspector);
      const actions = document.createElement("div");
      actions.className = "legend-row legend-runtime-actions";
      actions.append(gaps, hotspots);
      section.append(toggle, windowLine, completenessLine, summaryLine, observed, failing, silent, actions);
      legend.append(section);
      // The legend is hidden outside the graph tab, so the call-flow toolbar
      // carries a synchronized toggle for the selected call tree.
      const flowToggle = document.createElement("button");
      flowToggle.type = "button";
      flowToggle.id = "flow-runtime-toggle";
      flowToggle.className = "graph-action-label graph-secondary-action flow-runtime-toggle";
      flowToggle.textContent = "Trafic APM";
      flowToggle.title = `Afficher le trafic observé (${runtimeSourceLabel()}) · ${runtimeWindowLabel()}`;
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
      setRuntimeEnabled(runtimeState.enabled);
      flowToggle.addEventListener("click", () => setRuntimeEnabled(!runtimeState.enabled));
    }
