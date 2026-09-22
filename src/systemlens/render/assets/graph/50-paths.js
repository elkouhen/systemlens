// Ordered source module: 50-paths.js
    function revealDetails() {
      details.classList.remove("is-empty");
      const toolbar = document.querySelector(".toolbar");
      toolbar?.classList.add("has-details");
      resetButton.disabled = false;
      resetButton.textContent = "Fermer";
      resetButton.title = "Fermer le détail";
      resetButton.setAttribute("aria-label", resetButton.title);
      requestAnimationFrame(() => toolbar?.scrollTo({ top: 0 }));
    }
    function setDetailsEmpty(message) {
      details.classList.add("is-empty");
      document.querySelector(".toolbar")?.classList.remove("has-details");
      resetButton.disabled = true;
      resetButton.textContent = "Réinitialiser";
      resetButton.title = "Réinitialiser la sélection";
      resetButton.setAttribute("aria-label", resetButton.title);
      details.replaceChildren();
      const empty = document.createElement("div");
      empty.className = "details-empty";
      empty.textContent = message;
      details.append(empty);
    }
    function persistState() {
      // Explorer state is ephemeral; URL fragments never drive rendering.
    }
    function clearPathControls() {
      pathQuery.value = "";
      pathStops.splice(0, pathStops.length);
      graphState.selectedCodeFlowId = null;
      graphState.codeFlowRootNodeId = null;
      graphState.codeFlowTrigger = null;
      graphState.codeFlowTreeCoordinates = new Map();
      graphState.relatedLocalPortLinks = new Set();
      graphFlowStatus.hidden = true;
      delete graphCanvas.dataset.selectedCodeFlow;
      delete graphCanvas.dataset.flowFocusRatio;
    }
    function restResourceLabel(link, target) {
      const servicePrefix = `${target.name}: `;
      if (link.label === `${target.name}: API`) return "";
      return link.label.startsWith(servicePrefix) ? link.label.slice(servicePrefix.length) : link.label;
    }
    function contractsForPublishedRestResource(node, resource) {
      const contracts = node.openapi_contracts || [];
      const matchingContracts = contracts.filter(contract => (
        (contract.resources || []).includes(resource)
      ));
      return matchingContracts.length || contracts.length === 1
        ? (matchingContracts.length ? matchingContracts : contracts)
        : [];
    }
    function relationText(link) {
      const source = nodeDataById.get(link.source);
      const target = nodeDataById.get(link.target);
      const warning = link.message_type_warning ? ` · ⚠ ${link.message_type_warning}` : "";
      if (link.kind === "rest") {
        const resource = restResourceLabel(link, target);
        return resource
          ? `API · ${source.name} appelle ${target.name} (${resource})`
          : `API · ${source.name} appelle ${target.name} (contrat non indexe)`;
      }
      if (link.kind === "mongodb") return `Données · ${source.name} stocke dans ${target.name}`;
      if (source.kind === "microservice") {
        const types = link.published_message_types || [];
        return `Topic · ${source.name} publie${types.length ? ` <${types.join(", ")}>` : ""} sur ${target.name}${warning}`;
      }
      const types = link.consumed_message_types || [];
      return `Topic · ${target.name} consomme ${source.name}${types.length ? ` <${types.join(", ")}>` : ""}${warning}`;
    }
    function shortestPath(sourceId, targetId, matchesLink = () => true) {
      const outgoing = new Map();
      graphData.links.forEach((link, index) => {
        if (!matchesLink(link)) return;
        if (!outgoing.has(link.source)) outgoing.set(link.source, []);
        outgoing.get(link.source).push({ target: link.target, edge: `edge-${index}`, link });
      });
      const queue = [sourceId];
      const previous = new Map([[sourceId, null]]);
      for (let cursor = 0; cursor < queue.length; cursor += 1) {
        const current = queue[cursor];
        if (current === targetId) break;
        for (const step of outgoing.get(current) || []) {
          if (previous.has(step.target)) continue;
          previous.set(step.target, { node: current, edge: step.edge, link: step.link });
          queue.push(step.target);
        }
      }
      if (!previous.has(targetId)) return null;
      const nodes = [];
      const edges = [];
      for (let current = targetId; current !== null;) {
        nodes.unshift(current);
        const step = previous.get(current);
        if (step === null) break;
        edges.unshift(step);
        current = step.node;
      }
      return { nodes, edges };
    }
    function isValidPathStops(stops) {
      return stops.length >= 2
        && stops.every(id => nodeDataById.has(id)
          && ["microservice", "kafka_topic"].includes(nodeDataById.get(id).kind))
        && new Set(stops).size === stops.length;
    }
    function exactPathForStops(stops) {
      if (!isValidPathStops(stops)) return null;
      const edges = [];
      for (let index = 0; index < stops.length - 1; index += 1) {
        const edge = graphLinkBetween(stops[index], stops[index + 1]);
        if (!edge) return null;
        edges.push(edge);
      }
      return { nodes: [...stops], edges };
    }
    function shortestPathThrough(stops) {
      const path = { nodes: [], edges: [] };
      for (let index = 0; index < stops.length - 1; index += 1) {
        const segment = shortestPath(stops[index], stops[index + 1], link => link.kind === "kafka");
        if (segment === null) return null;
        path.nodes.push(...(index === 0 ? segment.nodes : segment.nodes.slice(1)));
        path.edges.push(...segment.edges);
      }
      return path;
    }
    function allSimplePaths(sourceId, targetId, maxDepth = MAX_SIMPLE_PATH_DEPTH, maxPaths = MAX_SIMPLE_PATHS, maxExplorations = MAX_SIMPLE_PATH_EXPLORATIONS) {
      const outgoing = new Map();
      graphData.links.forEach((link, index) => {
        if (link.kind !== "kafka") return;
        if (!outgoing.has(link.source)) outgoing.set(link.source, []);
        outgoing.get(link.source).push({ target: link.target, edge: `edge-${index}`, link });
      });
      outgoing.forEach((steps, source) => {
        const firstStepByTarget = new Map();
        steps.sort((left, right) => (
          nodeDataById.get(left.target).name.localeCompare(nodeDataById.get(right.target).name)
        )).forEach(step => {
          if (!firstStepByTarget.has(step.target)) firstStepByTarget.set(step.target, step);
        });
        outgoing.set(source, [...firstStepByTarget.values()]);
      });
      const paths = [];
      const queue = [{ nodes: [sourceId], edges: [] }];
      let explorations = 0;
      for (let cursor = 0; cursor < queue.length && paths.length < maxPaths; cursor += 1) {
        const candidate = queue[cursor];
        if (candidate.edges.length >= maxDepth) continue;
        const current = candidate.nodes[candidate.nodes.length - 1];
        for (const step of outgoing.get(current) || []) {
          if (candidate.nodes.includes(step.target)) continue;
          explorations += 1;
          if (explorations > maxExplorations) return { paths, limited: true };
          const nextNodes = [...candidate.nodes, step.target];
          const nextEdges = [...candidate.edges, step];
          if (step.target === targetId) {
            paths.push({ nodes: nextNodes, edges: nextEdges });
            continue;
          }
          queue.push({ nodes: nextNodes, edges: nextEdges });
        }
      }
      return {
        paths: paths.sort((left, right) => left.nodes.length - right.nodes.length || (
          left.nodes.map(id => nodeDataById.get(id).name).join("\\u0000").localeCompare(
            right.nodes.map(id => nodeDataById.get(id).name).join("\\u0000")
          )
        )),
        limited: paths.length >= maxPaths,
      };
    }
    function resolveExactNodeName(name, allowedKinds = null) {
      const candidates = nodesByNormalizedName.get(normalizeNodeName(name)) || [];
      if (!candidates.length) return { error: `Noeud introuvable : ${name}. Saisissez son nom exact.` };
      const eligible = allowedKinds
        ? candidates.filter(candidate => allowedKinds.includes(nodeDataById.get(candidate.id).kind))
        : candidates;
      if (!eligible.length) return { error: `Type de noeud invalide : ${name}.` };
      if (eligible.length > 1) return { error: `Nom ambigu : ${name}. Precisez un nom de noeud unique.` };
      return { id: eligible[0].id };
    }
    function parsePathQuery(query = pathQuery.value) {
      const names = query.split("->").map(name => name.trim());
      if (names.length < 2) return { error: "Saisissez au moins deux noeuds separes par ->." };
      if (names.some(name => !name)) return { error: "Chaque etape de l'itineraire doit avoir un nom : retirez le -> en trop ou renseignez le noeud manquant." };
      const stops = [];
      for (const [index, name] of names.entries()) {
        const endpoint = index === 0 || index === names.length - 1;
        const resolved = resolveExactNodeName(
          name,
          endpoint ? ["microservice"] : ["microservice", "kafka_topic"],
        );
        if (resolved.error) return resolved;
        stops.push(resolved.id);
      }
      if (stops.some(id => !["microservice", "kafka_topic"].includes(nodeDataById.get(id).kind))) {
        return { error: "Un itineraire de topics ne peut contenir que des microservices et des topics." };
      }
      if (nodeDataById.get(stops[0]).kind !== "microservice" || nodeDataById.get(stops.at(-1)).kind !== "microservice") {
        return { error: "Un itineraire de topics doit commencer et se terminer par un microservice." };
      }
      if (new Set(stops).size !== stops.length) {
        return { error: "Un itineraire ne peut pas repeter le meme noeud." };
      }
      return { stops };
    }
    function renderPathQuery() {
      const query = pathStops.map(id => nodeDataById.get(id).name).join(" -> ");
      pathQuery.value = query;
      search.value = query;
      searchStatus.textContent = "";
    }
    function setPathMicroserviceOrder(path, codeFlow = null) {
      graphState.pathMicroserviceOrder = new Map();
      graphState.codeFlowTreeCoordinates = new Map();
      let order = 1;
      path.nodes.forEach(id => {
        if (nodeDataById.get(id).kind !== "microservice") return;
        graphState.pathMicroserviceOrder.set(id, order);
        order += 1;
      });
      const callGraphEdges = codeFlow?.call_graph?.edges || [];
      if (!callGraphEdges.length) return;
      const serviceIdsByName = new Map(
        [...graphState.pathMicroserviceOrder.keys()].map(id => [nodeDataById.get(id).name, id])
      );
      const children = new Map();
      const incoming = new Set();
      callGraphEdges.forEach(edge => {
        const source = serviceIdsByName.get(edge.source);
        const target = serviceIdsByName.get(edge.target);
        if (!source || !target || source === target) return;
        children.set(source, [...(children.get(source) || []), target]);
        incoming.add(target);
      });
      const roots = [...graphState.pathMicroserviceOrder.keys()]
        .filter(id => !incoming.has(id));
      if (!roots.length) roots.push(...graphState.pathMicroserviceOrder.keys());
      const levels = new Map(roots.map(id => [id, 0]));
      const queue = [...roots];
      for (let index = 0; index < queue.length; index += 1) {
        const source = queue[index];
        (children.get(source) || []).forEach(target => {
          const nextLevel = (levels.get(source) || 0) + 1;
          if (!levels.has(target)) {
            levels.set(target, nextLevel);
            queue.push(target);
          }
        });
      }
      const byLevel = new Map();
      [...graphState.pathMicroserviceOrder.keys()].forEach(id => {
        const level = levels.get(id) ?? 0;
        byLevel.set(level, [...(byLevel.get(level) || []), id]);
      });
      byLevel.forEach((ids, level) => {
        ids.sort((left, right) => (
          nodeDataById.get(left).name.localeCompare(nodeDataById.get(right).name)
        ));
        const offset = (ids.length - 1) / 2;
        ids.forEach((id, index) => {
          graphState.codeFlowTreeCoordinates.set(id, {
            x: level * 4.8,
            y: (index - offset) * 3.2,
          });
        });
      });
    }
    function renderPathDetails(path, context = {}) {
      revealDetails();
      details.replaceChildren();
      const pathNodeLabel = (id, index) => {
        const node = nodeDataById.get(id);
        const topicDtos = node.kind === "kafka_topic"
          ? (graphData.kafka_dtos || [])
            .filter(dto => (dto.topics || []).includes(node.name))
            .sort((left, right) => dtoLabel(left).localeCompare(dtoLabel(right)))
          : [];
        const dtoSuffix = topicDtos.length ? ` (${topicDtos.map(dto => dtoLabel(dto)).join(", ")})` : "";
        return `${index + 1}. ${node.name} : ${nodeKindLabel(node)}${dtoSuffix}`;
      };
      const header = document.createElement("header");
      header.className = "path-details-header";
      const kicker = document.createElement("p");
      kicker.className = "path-details-kicker";
      kicker.textContent = context.codeFlow ? "Flux de code sélectionné" : "Analyse du flux";
      const title = document.createElement("h1");
      title.className = "path-details-title";
      title.textContent = context.codeFlow?.method
        || (pathStops.length > 2 ? "Chemin avec noeuds intermediaires" : "Chemin le plus court");
      const summary = document.createElement("p");
      summary.className = "path-details-summary";
      const serviceCount = path.nodes.filter(id => nodeDataById.get(id).kind === "microservice").length;
      const topicCount = path.nodes.filter(id => nodeDataById.get(id).kind === "kafka_topic").length;
      summary.textContent = `${serviceCount} microservice${serviceCount > 1 ? "s" : ""} · ${topicCount} topic${topicCount > 1 ? "s" : ""}`;
      header.append(kicker, title, summary);
      details.append(header);
      const overview = document.createElement("section");
      overview.className = "details-section";
      const overviewTitle = document.createElement("h2");
      overviewTitle.textContent = "Parcours";
      const overviewList = document.createElement("ol");
      overviewList.className = "path-overview";
      path.nodes.forEach((id, index) => {
        const item = document.createElement("li");
        item.className = "path-overview-item";
        const node = nodeDataById.get(id);
        item.classList.add(node.kind === "kafka_topic" ? "is-topic" : node.kind === "mongodb_collection" ? "is-collection" : node.external ? "is-external" : "is-service");
        const stop = document.createElement("button");
        stop.type = "button";
        stop.className = "path-overview-stop";
        stop.textContent = pathNodeLabel(id, index);
        stop.title = `Afficher les details et les preuves de ${node.name}`;
        stop.addEventListener("click", () => selectNode(id, true));
        item.append(stop);
        overviewList.append(item);
      });
      overview.append(overviewTitle, overviewList);
      details.append(overview);
    }
    function centerCameraOnPath(path) {
      const nodeIds = path.nodes.filter(id => (
        network.hasNode(id)
        && isVisibleNodeId(id)
      ));
      if (!nodeIds.length) return false;
      const viewport = graphCanvas.getBoundingClientRect();
      const toolbarRect = document.querySelector(".toolbar").getBoundingClientRect();
      const margin = 24;
      const focusArea = {
        left: margin,
        right: viewport.width - margin,
        top: margin,
        bottom: viewport.height - margin,
      };
      const roomBesideToolbar = viewport.right - toolbarRect.right;
      const roomBelowToolbar = viewport.bottom - toolbarRect.bottom;
      if (roomBesideToolbar >= 260) {
        focusArea.left = toolbarRect.right - viewport.left + margin;
      } else if (roomBelowToolbar >= 180) {
        focusArea.top = toolbarRect.bottom - viewport.top + margin;
      }
      const cardWidth = graphState.renderMode === "symbols" ? 34 : GRAPH_CARD_WIDTH;
      const cardHeight = graphState.renderMode === "symbols" ? 34 : GRAPH_CARD_HEIGHT;
      const availableWidth = Math.max(1, focusArea.right - focusArea.left - cardWidth - 2 * margin);
      const availableHeight = Math.max(1, focusArea.bottom - focusArea.top - cardHeight - 2 * margin);
      const camera = renderer.getCamera();
      let ratio = camera.getState().ratio;
      // Re-project after every ratio change. Sigma's graph-to-viewport
      // conversion also applies aspect-ratio and normalization corrections,
      // so a single estimate can leave a multi-service flow outside the
      // focus area.
      for (let pass = 0; pass < 4; pass += 1) {
        const projected = nodeIds.map(id => {
          const attributes = network.getNodeAttributes(id);
          return renderer.graphToViewport({ x: attributes.x, y: attributes.y });
        }).filter(point => Number.isFinite(point.x) && Number.isFinite(point.y));
        if (!projected.length) return false;
        const spanX = Math.max(...projected.map(point => point.x))
          - Math.min(...projected.map(point => point.x));
        const spanY = Math.max(...projected.map(point => point.y))
          - Math.min(...projected.map(point => point.y));
        // The selected flow can be much smaller than the overview graph. A
        // factor below 1 is intentional: it zooms in to make a short flow
        // readable instead of keeping the overview distance.
        const ratioFactor = Math.max(.01, spanX / availableWidth, spanY / availableHeight);
        const nextRatio = Math.max(.01, Math.min(100, ratio * ratioFactor));
        if (Math.abs(nextRatio - ratio) < .005) break;
        ratio = nextRatio;
        camera.setState({ ...camera.getState(), ratio });
        renderer.refresh();
      }

      const targetCenter = {
        x: (focusArea.left + focusArea.right) / 2,
        y: (focusArea.top + focusArea.bottom) / 2,
      };
      for (let pass = 0; pass < 8; pass += 1) {
        const projectedAfterZoom = nodeIds.map(id => {
          const attributes = network.getNodeAttributes(id);
          return renderer.graphToViewport({ x: attributes.x, y: attributes.y });
        }).filter(point => Number.isFinite(point.x) && Number.isFinite(point.y));
        if (!projectedAfterZoom.length) break;
        const projectedCenter = {
          x: (Math.min(...projectedAfterZoom.map(point => point.x))
            + Math.max(...projectedAfterZoom.map(point => point.x))) / 2,
          y: (Math.min(...projectedAfterZoom.map(point => point.y))
            + Math.max(...projectedAfterZoom.map(point => point.y))) / 2,
        };
        const deltaX = targetCenter.x - projectedCenter.x;
        const deltaY = targetCenter.y - projectedCenter.y;
        if (Math.abs(deltaX) <= .5 && Math.abs(deltaY) <= .5) break;
        const ratioState = camera.getState();
        camera.setState({
          ...ratioState,
          x: ratioState.x - deltaX * ratioState.ratio / Math.max(viewport.width, 1),
          y: ratioState.y + deltaY * ratioState.ratio / Math.max(viewport.height, 1),
        });
        renderer.refresh();
      }
      graphCanvas.dataset.flowFocusRatio = String(ratio);
      renderer.refresh();
      requestGraphRender();
      return true;
    }
    function scheduleFlowCameraFit(path, attempt = 0) {
      requestAnimationFrame(() => {
        if (centerCameraOnPath(path) || attempt >= 8) return;
        scheduleFlowCameraFit(path, attempt + 1);
      });
    }
    function showPath(path, stops = path.nodes, context = {}) {
      pathStops.splice(0, pathStops.length, ...stops);
      renderPathQuery();
      graphState.selectedId = path.nodes[0];
      graphState.relatedNodes = new Set(path.nodes);
      graphState.relatedEdges = new Set(path.edges.map(step => step.edge));
      graphState.relatedLocalPortLinks = new Set((path.localLinks || []).map(link => (
        `${link.input_endpoint_id}:${link.output_endpoint_id}`
      )));
      graphState.analysisPortEndpointId = null;
      graphState.selectedCodeFlowId = context.codeFlow?.id || null;
      graphState.showAllCodeFlowPorts = false;
      graphState.codeFlowRootNodeId = context.codeFlowRootNodeId || null;
      graphState.codeFlowTrigger = context.codeFlowTrigger || null;
      graphFlowStatus.hidden = context.topologyReconciled !== false;
      if (!graphFlowStatus.hidden) {
        graphFlowStatus.textContent = "Relations topologiques incomplètes : les étapes sont surlignées sans arête vérifiée.";
      }
      if (graphState.selectedCodeFlowId) graphCanvas.dataset.selectedCodeFlow = graphState.selectedCodeFlowId;
      else delete graphCanvas.dataset.selectedCodeFlow;
      setPathMicroserviceOrder(path, context.codeFlow);
      if (graphState.selectedCodeFlowId) rebuildGraph();
      else renderer.refresh();
      // The normal Explorer deliberately has no port overlays. Rebuild them
      // after setting the selected call graph so its ports and local links
      // are projected from the selected nodes only.
      requestGraphRender();
      if (context.showDetails !== false) renderPathDetails(path, context);
      else {
        resetButton.disabled = false;
        resetButton.textContent = "Réinitialiser";
        resetButton.title = "Réinitialiser la sélection";
        resetButton.setAttribute("aria-label", resetButton.title);
      }
      if (context.codeFlow) {
        // Rebuilds replace Sigma's renderer; publish a valid provisional fit
        // value synchronously, then compute the exact camera fit on the next
        // frame once Sigma has projected the new node set.
        graphCanvas.dataset.flowFocusRatio = "1";
        scheduleFlowCameraFit(path);
      }
      else {
        delete graphCanvas.dataset.flowFocusRatio;
        renderer.getCamera().animatedReset({ duration: 220 });
      }
      persistState();
    }
    function renderSimplePathChoices(paths, limited) {
      revealDetails();
      details.replaceChildren();
      const section = document.createElement("section");
      section.className = "details-section simple-paths";
      const title = document.createElement("h2");
      title.textContent = "Chemins simples disponibles";
      const summary = document.createElement("p");
      summary.className = "simple-paths-summary";
      summary.textContent = `${paths.length} chemin${paths.length > 1 ? "s" : ""} propose${paths.length > 1 ? "s" : ""}, sans repeter de noeud, sur au plus ${MAX_SIMPLE_PATH_DEPTH} relations.${limited ? ` Recherche limitee a ${MAX_SIMPLE_PATHS} chemins et ${MAX_SIMPLE_PATH_EXPLORATIONS} explorations.` : ""}`;
      const list = document.createElement("ol");
      list.className = "simple-paths-list";
      paths.forEach((path, index) => {
        const item = document.createElement("li");
        const choice = document.createElement("button");
        choice.type = "button";
        choice.className = "simple-path-choice";
        choice.textContent = `${index + 1}. ${path.nodes.map(id => nodeDataById.get(id).name).join(" → ")}`;
        choice.addEventListener("click", () => showPath(path));
        item.append(choice);
        list.append(item);
      });
      section.append(title, summary, list);
      details.append(section);
    }
    function showShortestPath(query = pathQuery.value, preserveGraphOnError = false) {
      const parsed = parsePathQuery(query);
      if (parsed.error) {
        if (preserveGraphOnError) { searchStatus.textContent = parsed.error; return false; }
        graphState.selectedId = null; graphState.relatedNodes = null; graphState.relatedEdges = null; graphState.relatedLocalPortLinks = new Set(); graphState.pathMicroserviceOrder = new Map();
        renderer.refresh();
        setDetailsEmpty(parsed.error);
        pathStops.splice(0, pathStops.length);
        persistState();
        return;
      }
      const stops = parsed.stops;
      const path = shortestPathThrough(stops);
      if (path === null) {
        const message = "Aucun itineraire de topics oriente ne passe par les noeuds demandes dans cet ordre.";
        if (preserveGraphOnError) { searchStatus.textContent = message; return false; }
        graphState.selectedId = null; graphState.relatedNodes = null; graphState.relatedEdges = null; graphState.relatedLocalPortLinks = new Set(); graphState.pathMicroserviceOrder = new Map();
        renderer.refresh();
        setDetailsEmpty(message);
        persistState();
        return false;
      }
      showPath(path, stops);
      return true;
    }
    function showSimplePaths() {
      const parsed = parsePathQuery();
      if (parsed.error) {
        graphState.selectedId = null; graphState.relatedNodes = null; graphState.relatedEdges = null; graphState.pathMicroserviceOrder = new Map();
        renderer.refresh();
        setDetailsEmpty(parsed.error);
        pathStops.splice(0, pathStops.length);
        persistState();
        return;
      }
      if (parsed.stops.length !== 2) {
        setDetailsEmpty("Les chemins simples se recherchent entre un microservice source et un microservice cible, sans noeud intermediaire impose.");
        return;
      }
      const simplePaths = allSimplePaths(parsed.stops[0], parsed.stops[1]);
      graphState.selectedId = null; graphState.relatedNodes = null; graphState.relatedEdges = null; graphState.relatedLocalPortLinks = new Set(); graphState.pathMicroserviceOrder = new Map();
      pathStops.splice(0, pathStops.length);
      renderer.refresh();
      if (!simplePaths.paths.length) {
        setDetailsEmpty(`Aucun chemin simple oriente, de ${nodeDataById.get(parsed.stops[0]).name} vers ${nodeDataById.get(parsed.stops[1]).name}, dans les limites de recherche.`);
        persistState();
        return;
      }
      renderSimplePathChoices(simplePaths.paths, simplePaths.limited);
      persistState();
    }
    function appendServiceKafkaActivities(node, role, title, links, container) {
      if (!links.length) return;
      const section = document.createElement("section");
      section.className = "details-section";
      const heading = document.createElement("h2");
      heading.textContent = title;
      const list = document.createElement("ul");
      list.className = "service-kafka-list";
      const topicIds = [...new Set(links.map(link => role === "produce" ? link.target : link.source))];
      topicIds.sort((left, right) => nodeDataById.get(left).name.localeCompare(nodeDataById.get(right).name));
      topicIds.forEach(topicId => {
        const topic = nodeDataById.get(topicId);
        const item = document.createElement("li");
        item.className = "service-kafka-item";
        const topicButton = document.createElement("button");
        topicButton.type = "button";
        topicButton.className = "service-kafka-topic";
        topicButton.textContent = topic.name;
        topicButton.title = `Afficher le detail du topic ${topic.name}`;
        topicButton.addEventListener("click", () => selectNode(topicId));
        item.append(topicButton);
        const meta = document.createElement("div");
        meta.className = "service-kafka-meta";
        const dtos = (graphData.kafka_dtos || []).filter(dto => {
          const matchesRole = (
            (role === "produce" && (dto.producers || []).includes(node.name))
            || (role === "consume" && (dto.consumers || []).includes(node.name))
          );
          return (dto.topics || []).includes(topic.name) && matchesRole;
        }).sort((left, right) => dtoLabel(left).localeCompare(dtoLabel(right)));
        if (dtos.length) {
          dtos.forEach(dto => {
            const dtoButton = document.createElement("button");
            dtoButton.type = "button";
            dtoButton.textContent = `DTO · ${dtoLabel(dto)}`;
            dtoButton.title = `Afficher la structure de ${dtoLabel(dto)}`;
            dtoButton.addEventListener("click", () => openDtoInspector(dto.id));
            meta.append(dtoButton);
          });
        } else {
          const unknown = document.createElement("span");
          unknown.textContent = "DTO non indexe";
          meta.append(unknown);
        }
        item.append(meta);
        list.append(item);
      });
      section.append(heading, list);
      container.append(section);
    }
    function renderDetails(id) {
      const node = nodeDataById.get(id);
      const indexedEdges = graphData.links.filter(link => link.source === id || link.target === id);
      const edges = indexedEdges.filter(
        link => isVisibleRelation(link) && (link.source === id || link.target === id)
      );
      const isMicroservice = node.kind === "microservice";
      const publishedApiCount = isMicroservice ? (node.resources || []).length : 0;
      const publishedTopicCount = isMicroservice ? new Set(
        indexedEdges.filter(link => relationCategory(link) === "kafka" && link.source === id).map(link => link.target)
      ).size : 0;
      const collectionCount = isMicroservice ? new Set(
        indexedEdges.filter(link => relationCategory(link) === "mongodb" && link.source === id).map(link => link.target)
      ).size : 0;
      revealDetails();
      details.replaceChildren();
      const kindLabel = nodeKindLabel(node);
      const complexity = node.complexity;
      const header = document.createElement("header");
      header.className = "details-header";
      if (complexity) header.classList.add(`is-${complexity.level}`);
      const kicker = document.createElement("p");
      kicker.className = "details-kicker";
      kicker.textContent = kindLabel;
      const title = document.createElement("h1");
      title.className = "details-title";
      title.textContent = node.name;
      const meta = document.createElement("div");
      meta.className = "details-meta";
      const relationBadge = document.createElement("span");
      relationBadge.className = "detail-badge";
      relationBadge.textContent = edges.length === indexedEdges.length
        ? `Relations : ${indexedEdges.length}`
        : `Relations indexees : ${indexedEdges.length}`;
      meta.append(relationBadge);
      if (edges.length !== indexedEdges.length) {
        const visibleBadge = document.createElement("span");
        visibleBadge.className = "detail-badge";
        visibleBadge.textContent = `Affichees : ${edges.length}`;
        meta.append(visibleBadge);
      }
      if (isMicroservice) {
        [
          `${publishedApiCount} API${publishedApiCount > 1 ? "s" : ""} exposee${publishedApiCount > 1 ? "s" : ""}`,
          `${publishedTopicCount} topic${publishedTopicCount > 1 ? "s" : ""} publie${publishedTopicCount > 1 ? "s" : ""}`,
          `${collectionCount} donnée${collectionCount > 1 ? "s" : ""} utilisée${collectionCount > 1 ? "s" : ""}`,
        ].forEach(label => { const badge = document.createElement("span"); badge.className = "detail-badge"; badge.textContent = label; meta.append(badge); });
      }
      const confidenceLabels = { proved: "prouvee", inferred: "inferee", conventional: "conventionnelle" };
      ["proved", "inferred", "conventional"].forEach(confidence => {
        const count = edges.filter(link => link.confidence === confidence).length;
        if (!count) return;
        const badge = document.createElement("span");
        badge.className = "detail-badge";
        badge.textContent = `${count} ${confidenceLabels[confidence]}`;
        badge.title = `Relation ${confidenceLabels[confidence]} : ${[...new Set(edges.filter(link => link.confidence === confidence).map(link => link.provenance))].join(", ")}`;
        meta.append(badge);
      });
      if (complexity) {
        const scoreBadge = document.createElement("span");
        scoreBadge.className = `detail-badge complexity ${complexity.level}`;
        const connectivityLabels = { low: "basse", medium: "médiane", high: "élevée" };
        scoreBadge.textContent = `Connectivité relative : ${connectivityLabels[complexity.level]} (${complexity.score})`;
        const breakdown = complexity.breakdown || {};
        scoreBadge.title = `APIs : ${breakdown.http || 0} · Messages : ${breakdown.kafka || 0} · Données : ${breakdown.mongodb || 0} · Rang relatif ${complexity.rank}/${complexity.population} · Tiers : ${complexity.tier_start}-${complexity.tier_end}`;
        meta.append(scoreBadge);
      }
      header.append(kicker, title, meta);
      details.append(header);
      if (!isMicroservice) {
        const clusterPath = clusterPathForNode(id);
        const architectureGroup = createDetailsGroup("Architecture");
        appendList("Layer", [architectureLayerForNode(id)], architectureGroup);
        appendActionList("Module", clusterPath ? [{
          label: clusterPath,
          title: `Naviguer vers le module ${clusterPath}`,
          action: () => selectCluster(clusterDescriptorForPath(clusterPath)),
        }] : [], architectureGroup);
        discardEmptyDetailsGroup(architectureGroup);
      }
      if (node.kind === "microservice") {
        if (node.vscode_uri) {
          const moduleAction = document.createElement("a");
          moduleAction.className = "module-open-action";
          moduleAction.href = node.vscode_uri;
          const buildSystem = node.build_system === "gradle" ? "Gradle" : "Maven";
          moduleAction.textContent = `Ouvrir le projet ${buildSystem} dans VS Code`;
          moduleAction.title = `Ouvrir le repertoire racine du projet ${node.name}`;
          details.append(moduleAction);
        }
        const httpCalls = edges.filter(link => link.kind === "rest" && link.source === id);
        const kafkaPublications = edges.filter(link => link.kind === "kafka" && link.source === id);
        const kafkaConsumptions = edges.filter(link => link.kind === "kafka" && link.target === id);
        const mongoCollections = edges.filter(link => link.kind === "mongodb" && link.source === id);
        const openApiContracts = node.openapi_contracts || [];
        const asyncApiContracts = node.asyncapi_contracts || [];
        const kubernetesWorkloads = node.kubernetes_workloads || [];
        const clusterPath = clusterPathForNode(id);
        const architectureGroup = createDetailsGroup("Architecture");
        appendList("Layer", [node.layer_label || "Unknown"], architectureGroup);
        appendActionList("Module", clusterPath ? [{
          label: clusterPath,
          title: `Naviguer vers le module ${clusterPath}`,
          action: () => selectCluster(clusterDescriptorForPath(clusterPath)),
        }] : [], architectureGroup);
        discardEmptyDetailsGroup(architectureGroup);
      const ports = node.ports || [];
      const associatedEndpointIds = new Set(ports.map(port => port.endpoint_id));
      const associatedFlows = (graphData.code_flows || []).filter(flow => (
        flow.module === node.name
        || (flow.steps || []).some(step => associatedEndpointIds.has(step.endpoint_id))
      ));
      if (ports.length) {
        const portsGroup = createDetailsGroup("Ports d'intégration", false);
        const portDescription = port => `${port.label} · ${port.type} · ${port.name}`;
        appendList(
          "Entrées",
          ports.filter(port => port.direction === "in").map(portDescription),
          portsGroup,
        );
        appendList(
          "Sorties",
          ports.filter(port => port.direction === "out").map(portDescription),
          portsGroup,
        );
        discardEmptyDetailsGroup(portsGroup);
        const portsByEndpointId = new Map(ports.map(port => [port.endpoint_id, port]));
          const connections = (graphData.code_flows || []).flatMap(flow => {
            if (flow.module !== node.name) return [];
            const input = portsByEndpointId.get(flow.steps?.[0]?.endpoint_id);
            if (!input) return [];
            const via = flow.steps.filter(step => step.kind === "method_call")
              .map(step => step.name);
            return flow.steps.slice(1).flatMap(step => {
              const output = portsByEndpointId.get(step.endpoint_id);
              if (!output || output.direction !== "out") return [];
              return [{ input, output, target: output.target || null, via }];
            });
          });
          const uniqueConnections = connections.filter((connection, index) => (
            connections.findIndex(candidate => (
              candidate.input.endpoint_id === connection.input.endpoint_id
              && candidate.output.endpoint_id === connection.output.endpoint_id
            )) === index
          ));
          const internalFlowsGroup = createDetailsGroup("Flux internes", false);
          appendPortFlowList("Flux potentiels", uniqueConnections, internalFlowsGroup);
          discardEmptyDetailsGroup(internalFlowsGroup);
        }
        if (kubernetesWorkloads.length) {
          const kubernetesGroup = createDetailsGroup("Kubernetes");
          appendList("Workloads", kubernetesWorkloads.map(workload => {
            const request = `requests CPU ${workload.cpu_request_millicores ?? "-"}m · RAM ${workload.memory_request_bytes ?? "-"}B`;
            const limit = `limits CPU ${workload.cpu_limit_millicores ?? "-"}m · RAM ${workload.memory_limit_bytes ?? "-"}B`;
            return `${workload.kind} ${workload.name} · replicas ${workload.replicas ?? "-"} · ${request} · ${limit}`;
          }), kubernetesGroup);
          discardEmptyDetailsGroup(kubernetesGroup);
        }
        const publishedApis = [
          ...openApiContracts.map(contract => ({
            label: `${contract.spec ? "Contrat OpenAPI" : "Contrat OpenAPI indisponible"} · ${contract.path}`,
            title: `Ouvrir le contrat OpenAPI ${contract.path}`,
            action: () => openOpenApiContract(contract),
          })),
          ...(node.resources || [])
            .filter(resource => !contractsForPublishedRestResource(node, resource).length)
            .map(resource => ({
              label: `API · ${resource}`,
              title: "Mettre en evidence les consommateurs de cette API",
              action: () => focusPublishedRestResource(id, resource),
            })),
        ];
        const relationsGroup = createDetailsGroup("Relations");
        appendRelationList("APIs consommees", httpCalls, id, link => (
          `API de ${nodeDataById.get(link.target).name}`
        ), relationsGroup);
        appendActionList("APIs publiees", publishedApis, relationsGroup);
        appendActionList("Contrats AsyncAPI", asyncApiContracts.map(contract => ({
          label: `AsyncAPI · ${contract.path}`,
          title: `Inspecter le contrat AsyncAPI ${contract.path}`,
          action: () => openAsyncApiContract(contract),
        })), relationsGroup);
        appendServiceKafkaActivities(node, "consume", "Messages consommés", kafkaConsumptions, relationsGroup);
        appendServiceKafkaActivities(node, "produce", "Messages publiés", kafkaPublications, relationsGroup);
        appendRelationList("Données", mongoCollections, id, link => (
          nodeDataById.get(link.target).name
        ), relationsGroup);
        discardEmptyDetailsGroup(relationsGroup);
        const associatedFlowsGroup = createDetailsGroup("Flux associés");
        appendAssociatedCodeFlows("Flux associés", associatedFlows, associatedFlowsGroup);
        discardEmptyDetailsGroup(associatedFlowsGroup);
        const sourceEntries = [
          ...openApiContracts.map(contract => ({
            label: `OpenAPI · ${contract.path}`,
            title: `Ouvrir ${contract.path} dans VS Code`,
            action: () => { if (contract.vscode_uri) window.location.href = contract.vscode_uri; },
          })),
          ...(node.kafka_endpoints || []).map(endpoint => ({
            label: `Topic · ${endpoint.location}`,
            title: `Ouvrir ${endpoint.location} dans VS Code`,
            action: () => { if (endpoint.vscode_uri) window.location.href = endpoint.vscode_uri; },
          })),
        ];
        const sourcesGroup = createDetailsGroup("Sources", false);
        appendActionList("Fichiers de preuve", sourceEntries, sourcesGroup);
        discardEmptyDetailsGroup(sourcesGroup);
        const qualityGroup = createDetailsGroup("Diagnostics", false);
        appendFindings(node.findings || [], qualityGroup);
        discardEmptyDetailsGroup(qualityGroup);
        const groupOrder = ["Architecture", "Ports d'intégration", "Relations", "Flux associés", "Flux internes", "Kubernetes", "Sources", "Diagnostics"];
        [...details.querySelectorAll(":scope > .details-group")]
          .sort((left, right) => {
            const leftRank = groupOrder.indexOf(left.querySelector("summary")?.textContent);
            const rightRank = groupOrder.indexOf(right.querySelector("summary")?.textContent);
            return (leftRank === -1 ? groupOrder.length : leftRank)
              - (rightRank === -1 ? groupOrder.length : rightRank);
          })
          .forEach(group => details.append(group));
      }
      if (node.kind === "kafka_topic") {
        const relationsGroup = createDetailsGroup("Relations");
        appendRelationList("Services producteurs", edges.filter(link => link.kind === "kafka" && link.target === id), id,
          link => nodeDataById.get(link.source).name, relationsGroup);
        appendRelationList("Services consommateurs", edges.filter(link => link.kind === "kafka" && link.source === id), id,
          link => nodeDataById.get(link.target).name, relationsGroup);
        const dtos = (graphData.kafka_dtos || [])
          .filter(dto => (dto.topics || []).includes(node.name))
          .sort((left, right) => dtoLabel(left).localeCompare(dtoLabel(right)));
        appendActionList("DTO de topic", dtos.map(dto => ({
          label: dtoLabel(dto),
          title: "Afficher les champs et les relations de topic de ce DTO",
          action: () => openDtoInspector(dto.id),
        })), relationsGroup);
        const indexedDtoTypes = new Set(dtos.flatMap(dto => [dto.id, dto.name, dto.qualified_name].filter(Boolean)));
        const unresolvedTypes = [...new Set([
          ...(node.published_message_types || []),
          ...(node.consumed_message_types || []),
        ])].filter(type => !indexedDtoTypes.has(type) && !indexedDtoTypes.has(type.split(".").at(-1)));
        appendList("Types de message non resolus", unresolvedTypes, relationsGroup);
        const endpointSources = graphData.nodes
          .filter(candidate => candidate.kind === "microservice")
          .flatMap(candidate => (candidate.kafka_endpoints || []).map(endpoint => ({ service: candidate.name, ...endpoint })))
          .filter(endpoint => endpoint.topic === node.name);
        appendActionList("Sources producteurs et consommateurs", endpointSources.map(endpoint => ({
          label: `${endpoint.service} · ${endpoint.role === "produce" ? "publication" : "consommation"} · ${endpoint.location}`,
          title: `Ouvrir ${endpoint.location} dans VS Code`,
          action: () => { if (endpoint.vscode_uri) window.location.href = endpoint.vscode_uri; },
        })), relationsGroup);
        discardEmptyDetailsGroup(relationsGroup);
      }
      if (node.kind === "mongodb_collection") {
        const relationsGroup = createDetailsGroup("Relations");
        const persistenceClasses = node.persistence_classes || [];
        appendRelationList("Services utilisant cette donnée", edges.filter(link => link.kind === "mongodb" && link.target === id), id,
          link => nodeDataById.get(link.source).name, relationsGroup);
        appendActionList("Classes Java de persistance", persistenceClasses.map(item => ({
          label: item.qualified_name,
          title: "Afficher les champs et la source de cette classe",
          action: () => openMongoPersistenceInspector(item.id),
        })), relationsGroup);
        if (!persistenceClasses.length) {
          appendList("Classes Java de persistance", [
            "Aucune classe Java associée dans l’index. Relancez systemlens index après la mise à jour.",
          ], relationsGroup);
        }
        discardEmptyDetailsGroup(relationsGroup);
      }
      if (["data_schema", "message_channel"].includes(node.kind)) {
        const factsGroup = createDetailsGroup("Ressource enrichie");
        if (node.technology) appendList("Technologie", [node.technology], factsGroup);
        const architectureMetadataKeys = new Set([
          "architecture_layer", "cluster", "cluster_path", "fact_namespaces", "layer",
          "namespace", "namespaces", "project_namespace", "project_namespace_path", "runtime_namespaces",
        ]);
        const metadata = Object.entries(node.metadata || {})
          .filter(([key]) => !architectureMetadataKeys.has(key) && !(key === "technology" && node.technology))
          .map(([key, value]) => `${key} : ${Array.isArray(value) ? value.join(", ") : String(value)}`);
        appendList("Métadonnées", metadata.length ? metadata : ["Aucune métadonnée"], factsGroup);
        appendRelationList("Relations", edges.filter(link => link.source === id || link.target === id), id,
          link => `${link.label} · ${nodeDataById.get(link.source === id ? link.target : link.source)?.name || "ressource"}`,
          factsGroup);
        discardEmptyDetailsGroup(factsGroup);
      }
    }
    function renderClusterDetails(cluster) {
      const resolvedCluster = clusterDescriptorForPath(cluster.path || cluster.name);
      const members = [...new Set(resolvedCluster.ids)]
        .map(id => nodeDataById.get(id))
        .filter(Boolean)
        .sort((left, right) => left.name.localeCompare(right.name));
      revealDetails();
      details.replaceChildren();
      const header = document.createElement("header");
      header.className = "details-header";
      const kicker = document.createElement("p");
      kicker.className = "details-kicker";
      kicker.textContent = "Module";
      const title = document.createElement("h1");
      title.className = "details-title";
      title.textContent = resolvedCluster.name;
      const meta = document.createElement("div");
      meta.className = "details-meta";
      const count = document.createElement("span");
      count.className = "detail-badge";
      count.textContent = `${members.length} ressource${members.length > 1 ? "s" : ""} directe${members.length > 1 ? "s" : ""}`;
      meta.append(count);
      const childCount = document.createElement("span");
      childCount.className = "detail-badge";
      childCount.textContent = `${resolvedCluster.childPaths.length} sous-module${resolvedCluster.childPaths.length > 1 ? "s" : ""}`;
      meta.append(childCount);
      header.append(kicker, title, meta);
      details.append(header);
      appendActionList("Module parent", resolvedCluster.parentPath ? [{
        label: resolvedCluster.parentPath === "root" ? "ROOT" : resolvedCluster.parentPath,
        title: "Naviguer vers le module parent",
        action: () => selectCluster(clusterDescriptorForPath(resolvedCluster.parentPath)),
      }] : []);
      appendActionList("Sous-modules", resolvedCluster.childPaths.map(childPath => ({
        label: childPath,
        title: `Naviguer vers le sous-module ${childPath}`,
        action: () => selectCluster(clusterDescriptorForPath(childPath)),
      })));
      appendActionList("Ressources contenues", members.map(member => ({
        label: `${member.name} · ${nodeKindLabel(member)}`,
        title: `Afficher les détails de ${member.name}`,
        action: () => selectNode(member.id),
      })));
      if (!members.length) appendList("Ressources contenues", ["Aucune ressource directe"]);
    }
    async function selectCluster(cluster) {
      if (!pathLock.checked) clearPathControls();
      if (!graphState.layeredView && !graphState.clusteredView) await applyLayout("cluster");
      const resolvedCluster = clusterDescriptorForPath(cluster.path || cluster.name);
      updateGraphState({
        selectedId: null,
        selectedClusterKey: resolvedCluster.key,
        relatedNodes: null,
        relatedEdges: null,
        relatedLocalPortLinks: new Set(),
        selectedCodeFlowId: null,
        codeFlowRootNodeId: null,
        codeFlowTrigger: null,
        pathMicroserviceOrder: new Map(),
      });
      delete graphCanvas.dataset.selectedCodeFlow;
      renderer.refresh();
      requestGraphRender();
      renderClusterDetails(resolvedCluster);
      persistState();
    }
    function focusNodeRelations(id, matches) {
      if (!pathLock.checked) clearPathControls();
      graphState.pathMicroserviceOrder = new Map();
      graphState.selectedId = id;
      graphState.selectedClusterKey = null;
      graphState.relatedNodes = new Set([id]);
      graphState.relatedEdges = new Set();
      graphState.relatedLocalPortLinks = new Set();
      graphState.selectedCodeFlowId = null;
      graphState.codeFlowRootNodeId = null;
      graphState.codeFlowTrigger = null;
      delete graphCanvas.dataset.selectedCodeFlow;
      network.forEachEdge((edge, attributes, source, target) => {
        if (!isVisibleRelation(attributes, source, target) || !matches(attributes, source, target)) return;
        graphState.relatedEdges.add(edge); graphState.relatedNodes.add(source); graphState.relatedNodes.add(target);
      });
      renderer.refresh();
      renderDetails(id);
      persistState();
    }
    function focusPublishedRestResource(id, resource) {
      const target = nodeDataById.get(id);
      focusNodeRelations(id, (link, _source, targetId) => (
        link.kind === "rest" && targetId === id && restResourceLabel(link, target) === resource
      ));
    }
    function selectNode(id, preservePath = false) {
      if (!preservePath && !pathLock.checked) clearPathControls();
      graphState.pathMicroserviceOrder = new Map();
      graphState.selectedId = id;
      graphState.selectedClusterKey = null;
      graphState.relatedNodes = new Set([id]);
      graphState.relatedEdges = new Set();
      graphState.relatedLocalPortLinks = new Set();
      graphState.selectedCodeFlowId = null;
      graphState.codeFlowRootNodeId = null;
      graphState.codeFlowTrigger = null;
      delete graphCanvas.dataset.selectedCodeFlow;
      network.forEachEdge((edge, attributes, source, target) => {
        if (!isVisibleRelation(attributes, source, target)) return;
        if (source === id || target === id) {
          graphState.relatedEdges.add(edge); graphState.relatedNodes.add(source); graphState.relatedNodes.add(target);
        }
      });
      renderer.refresh();
      renderDetails(id);
      persistState();
    }
