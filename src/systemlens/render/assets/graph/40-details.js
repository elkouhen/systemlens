// Ordered source module: 40-details.js
    function normalizeNodeName(name) {
      return name.trim().replace(/\\s+/g, " ").toLocaleLowerCase();
    }
    graphData.nodes.forEach(node => {
      const key = normalizeNodeName(node.name);
      nodesByNormalizedName.set(key, [...(nodesByNormalizedName.get(key) || []), node]);
    });
    function setToolbarTab(tab, options = {}) {
      const showingGraph = tab === "graph";
      const showingFlows = tab === "flows";
      const showingFlowGraph = showingFlows && options.showFlowGraph === true;
      const graphVisible = showingGraph || showingFlowGraph;
      if (showingGraph) graphState.viewMode = "architecture";
      else if (showingFlowGraph) graphState.viewMode = "call-graph";
      else if (showingFlows) graphState.viewMode = "empty";
      // The graph canvas and its overlays are fixed-position siblings of the
      // toolbar panels, so hiding graphPanel alone does not hide the rendered
      // architecture behind the Flux de code list.
      [
        graphCanvas,
        document.getElementById("graph-layers"),
        document.getElementById("graph-groups"),
        document.getElementById("graph-port-paths"),
        document.getElementById("graph-node-labels"),
        document.getElementById("graph-flow-tooltips"),
      ].forEach(element => { if (element) element.hidden = !graphVisible; });
      if (!graphVisible && graphFlowStatus) graphFlowStatus.hidden = true;
      // The two navigation surfaces have different meanings: Graphe is the
      // static architecture view, while Flux de code only becomes a graph
      // after the user explicitly selects a flow from its list.
      if ((tab === "graph" || tab === "flows") && !showingFlowGraph) {
        graphState.selectedId = null;
        graphState.selectedClusterKey = null;
        graphState.relatedNodes = null;
        graphState.relatedEdges = null;
        graphState.dependencyFocusOnly = false;
        graphState.analysisPortEndpointId = null;
        graphState.selectedCodeFlowId = null;
        graphState.selectedCallGraphEdgeKey = null;
        graphState.showAllCodeFlowPorts = false;
        graphState.pathMicroserviceOrder = new Map();
        graphState.codeFlowTreeCoordinates = new Map();
        graphState.relatedLocalPortLinks = new Set();
        graphState.codeFlowRootNodeId = null;
        dependencyFocusOnly.checked = false;
        dependencyFocusOnly.disabled = true;
        graphState.codeFlowTrigger = null;
        graphState.viewMode = tab === "graph" ? "architecture" : "empty";
        delete graphCanvas.dataset.selectedCodeFlow;
        delete graphCanvas.dataset.selectedCallGraphArc;
        delete graphCanvas.dataset.flowFocusRatio;
        // The Flux de code panel is hidden behind the toolbar and does not
        // need a graph rebuild. Rebuild only when returning to Graphe, after
        // the static view has become visible again.
        if (tab === "graph") {
          rebuildGraph();
          applyLayout(graphState.activeLayout);
        }
      }
      if (document.querySelector(".toolbar")?.classList.contains("has-details")) {
        setDetailsEmpty("Sélectionnez un nœud pour afficher ses détails.");
      }
      const showingResources = tab === "resources";
      const showingIssues = tab === "issues";
      const showingOpenApi = tab === "openapi";
      const showingKafka = tab === "kafka";
      const showingPersistence = tab === "persistence";
      const resourceTabGroup = document.getElementById("resource-tab-group");
      if (resourceTabGroup) resourceTabGroup.hidden = showingFlows || showingIssues;
      graphTab.classList.toggle("is-active", showingGraph);
      graphTab.setAttribute("aria-selected", String(showingGraph));
      resourcesTab.classList.toggle("is-active", showingResources);
      resourcesTab.setAttribute("aria-selected", String(showingResources));
      openApiTab.classList.toggle("is-active", showingOpenApi);
      openApiTab.setAttribute("aria-selected", String(showingOpenApi));
      kafkaTab.classList.toggle("is-active", showingKafka);
      kafkaTab.setAttribute("aria-selected", String(showingKafka));
      persistenceTab.classList.toggle("is-active", showingPersistence);
      persistenceTab.setAttribute("aria-selected", String(showingPersistence));
      issuesTab.classList.toggle("is-active", showingIssues);
      issuesTab.setAttribute("aria-selected", String(showingIssues));
      flowsTab.classList.toggle("is-active", showingFlows);
      flowsTab.setAttribute("aria-selected", String(showingFlows));
      graphPanel.hidden = !showingGraph;
      resourcesPanel.hidden = !showingResources;
      quickSearch.hidden = !showingGraph;
      graphContext.hidden = !showingGraph;
      issuesPanel.hidden = !showingIssues;
      openApiPanel.hidden = !showingOpenApi;
      kafkaPanel.hidden = !showingKafka;
      persistencePanel.hidden = !showingPersistence;
      flowsPanel.hidden = !showingFlows;
      graphLegend.hidden = !graphVisible;
      if (showingFlows) flowsPanel.dispatchEvent(new Event("systemlens:flows-open"));
    }
    function renderIndexingIssues() {
      const progress = graphData.progress_notice;
      progressNotice.hidden = !progress;
      progressNotice.textContent = progress || "";
      inventoryStatus.hidden = false;
      inventoryStatus.classList.toggle("is-warning", indexingIssues.length > 0);
      inventoryStatus.textContent = indexingIssues.length
        ? `${indexingIssues.length} fait${indexingIssues.length > 1 ? "s" : ""} à vérifier`
        : "Index complet";
      inventoryStatus.title = indexingIssues.length
        ? "Ouvrir les problèmes d'indexation"
        : "Aucun fait non résolu dans cet inventaire";
      indexingIssuesTitle.textContent = `Problèmes d’indexation (${indexingIssues.length})`;
      indexingIssuesList.replaceChildren();
      indexingIssuesEmpty.hidden = indexingIssues.length > 0;
      indexingIssues.forEach(issue => {
        const item = document.createElement("li");
        item.className = `indexing-issue ${issue.severity}`;
        const header = document.createElement("div");
        header.className = "indexing-issue-header";
        const severity = document.createElement("span");
        severity.className = "indexing-issue-severity";
        severity.textContent = issue.severity === "warning" ? "À corriger" : "À vérifier";
        const category = document.createElement("span");
        category.className = "indexing-issue-category";
        category.textContent = issue.category;
        const message = document.createElement("p");
        message.className = "indexing-issue-message";
        message.textContent = issue.message;
        header.append(severity, category);
        item.append(header, message);
        if (issue.location) {
          const location = document.createElement(issue.vscode_uri ? "a" : "code");
          location.className = "indexing-issue-location";
          location.textContent = issue.location;
          if (issue.vscode_uri) {
            location.href = issue.vscode_uri;
            location.title = "Ouvrir le fichier concerné dans VS Code";
          }
          item.append(location);
        }
        indexingIssuesList.append(item);
      });
    }
    function referenceItem(title, meta, actionLabel, action, disabled = false) {
      const item = document.createElement("li");
      item.className = "reference-item";
      const text = document.createElement("div");
      const name = document.createElement("div");
      name.className = "reference-title";
      name.textContent = title;
      const details = document.createElement("div");
      details.className = "reference-meta";
      details.textContent = meta;
      text.append(name, details);
      const button = document.createElement("button");
      button.className = "reference-action";
      button.type = "button";
      button.textContent = actionLabel;
      button.disabled = disabled;
      if (!disabled) button.addEventListener("click", action);
      item.append(text, button);
      return item;
    }
    function renderReferences() {
      openApiReferencesList.replaceChildren();
      const contracts = graphData.nodes.flatMap(node => (
        node.kind === "microservice"
          ? (node.openapi_contracts || []).map(contract => ({ service: node.name, contract }))
          : []
      ));
      const openApiQuery = openApiReferencesFilter.value.trim().toLocaleLowerCase();
      const visibleContracts = contracts.filter(({ service, contract }) => (
        !openApiQuery
        || service.toLocaleLowerCase().includes(openApiQuery)
        || contract.path.toLocaleLowerCase().includes(openApiQuery)
      ));
      openApiReferencesEmpty.hidden = visibleContracts.length > 0;
      openApiReferencesEmpty.textContent = openApiQuery && !visibleContracts.length
        ? "Aucun contrat ne correspond à ce filtre."
        : "Aucun contrat OpenAPI détecté.";
      visibleContracts.forEach(({ service, contract }) => {
        openApiReferencesList.append(referenceItem(
          contract.path,
          `${service} · ${contract.resources?.length || 0} ressource(s)`,
          contract.spec ? "Swagger UI" : "Indisponible",
          () => openOpenApiContract(contract),
          !contract.spec,
        ));
      });
      dtoReferencesList.replaceChildren();
      const dtos = graphData.kafka_dtos || [];
      const query = dtoReferencesFilter.value.trim().toLocaleLowerCase();
      const visibleDtos = dtos.filter(dto => (
        !query || dtoLabel(dto).toLocaleLowerCase().includes(query)
      ));
      dtoReferencesEmpty.hidden = visibleDtos.length > 0;
      dtoReferencesEmpty.textContent = query && !visibleDtos.length
        ? "Aucun DTO ne correspond à ce filtre."
        : "Aucun DTO de messages détecté.";
      visibleDtos.forEach(dto => {
        const exchangeCount = (dto.producers?.length || 0) + (dto.consumers?.length || 0);
        dtoReferencesList.append(referenceItem(
          dtoLabel(dto),
          `${dto.fields?.length || 0} champ(s) · ${dto.topics?.length || 0} topic(s) · ${exchangeCount} liaison(s)`,
          "Inspecter",
          () => openDtoInspector(dto.id),
        ));
      });
      openapiReferencesTitle.textContent = `Contrats OpenAPI (${visibleContracts.length}/${contracts.length})`;
      dtoReferencesTitle.textContent = `DTO de messages (${visibleDtos.length}/${dtos.length})`;
      const asyncContracts = graphData.nodes.flatMap(node => (
        node.kind === "microservice"
          ? (node.asyncapi_contracts || []).map(contract => ({ ...contract, module: node.name }))
          : []
      ));
      asyncApiReferencesList.replaceChildren();
      asyncApiReferencesEmpty.hidden = asyncContracts.length > 0;
      asyncContracts.forEach(contract => asyncApiReferencesList.append(referenceItem(
        contract.path,
        `${contract.module} · AsyncAPI ${contract.spec?.asyncapi || ""}`,
        "Inspecter",
        () => openAsyncApiContract(contract),
      )));
      asyncApiReferencesTitle.textContent = `Contrats AsyncAPI (${asyncContracts.length})`;
      mongoClassReferencesList.replaceChildren();
      const persistenceClasses = (graphData.mongo_persistence_classes || []).filter(
        item => item.root !== false
      );
      const mongoQuery = mongoClassReferencesFilter.value.trim().toLocaleLowerCase();
      const visiblePersistenceClasses = persistenceClasses.filter(item => (
        !mongoQuery || `${item.qualified_name} ${item.collection} ${item.service}`.toLocaleLowerCase().includes(mongoQuery)
      ));
      mongoClassReferencesEmpty.hidden = visiblePersistenceClasses.length > 0;
      mongoClassReferencesEmpty.textContent = mongoQuery && !visiblePersistenceClasses.length
        ? "Aucune classe de persistance ne correspond à ce filtre."
        : "Aucune classe de données persistées détectée.";
      visiblePersistenceClasses.forEach(item => mongoClassReferencesList.append(referenceItem(
        item.qualified_name,
        `${item.collection} · ${item.service} / ${item.module} · ${item.fields?.length || 0} champ(s)`,
        "Inspecter",
        () => openMongoPersistenceInspector(item.id),
      )));
      mongoClassReferencesTitle.textContent = `Classes de persistance (${visiblePersistenceClasses.length}/${persistenceClasses.length})`;
    }
    function renderResources() {
      resourcesList.replaceChildren();
      const query = resourcesFilter.value.trim().toLocaleLowerCase();
      const resources = graphData.nodes
        .slice()
        .sort((left, right) => left.name.localeCompare(right.name));
      const visibleResources = resources.filter(node => (
        !query || `${node.name} ${nodeKindLabel(node)}`.toLocaleLowerCase().includes(query)
      ));
      resourcesTitle.textContent = `Ressources (${visibleResources.length}/${resources.length})`;
      resourcesEmpty.hidden = visibleResources.length > 0;
      visibleResources.forEach(node => {
        const incoming = graphData.links.filter(link => link.target === node.id).length;
        const outgoing = graphData.links.filter(link => link.source === node.id).length;
        resourcesList.append(referenceItem(
          node.name,
          `${nodeKindLabel(node)} · ${incoming} entrée${incoming > 1 ? "s" : ""} · ${outgoing} sortie${outgoing > 1 ? "s" : ""}`,
          "Voir",
          () => {
            setToolbarTab("graph");
            selectNode(node.id);
          },
        ));
      });
    }
    function createDetailsGroup(title, open = true) {
      const group = document.createElement("details");
      group.className = "details-group";
      group.open = open;
      const summary = document.createElement("summary");
      summary.textContent = title;
      group.append(summary);
      details.append(group);
      return group;
    }
    function discardEmptyDetailsGroup(group) {
      if (!group.querySelector(".details-section")) group.remove();
    }
    function appendList(title, values, container = details) {
      if (!values.length) return;
      const section = document.createElement("section");
      section.className = "details-section";
      const heading = document.createElement("h2");
      heading.textContent = title;
      const list = document.createElement("ul");
      values.forEach(value => { const item = document.createElement("li"); item.textContent = value; list.append(item); });
      section.append(heading, list);
      container.append(section);
    }
    function appendPortFlowList(title, connections, container = details) {
      if (!connections.length) return;
      const section = document.createElement("section");
      section.className = "details-section";
      const heading = document.createElement("h2");
      heading.textContent = title;
      const list = document.createElement("div");
      list.className = "port-flow-list";
      const appendLeg = (flow, role, port) => {
        const leg = document.createElement("div");
        leg.className = "port-flow-leg";
        const roleLabel = document.createElement("span");
        roleLabel.className = "port-flow-role";
        roleLabel.textContent = `${port.label || "Port ?"} · ${role}`;
        const type = document.createElement("strong");
        type.textContent = port.type;
        const method = document.createElement("code");
        method.textContent = port.method;
        const resource = document.createElement("span");
        resource.className = "port-flow-resource";
        resource.textContent = port.name;
        leg.append(roleLabel, type, method, resource);
        flow.append(leg);
      };
      connections.forEach(connection => {
        const flow = document.createElement("article");
        flow.className = "port-flow";
        appendLeg(flow, "Entrée locale", connection.input);
        const localArrow = document.createElement("span");
        localArrow.className = "port-flow-arrow";
        localArrow.textContent = "↓ déclenche";
        flow.append(localArrow);
        appendLeg(flow, "Sortie locale", connection.output);
        if (connection.target) {
          const targetArrow = document.createElement("span");
          targetArrow.className = "port-flow-arrow";
          targetArrow.textContent = "↓ cible résolue";
          flow.append(targetArrow);
          appendLeg(flow, `Entrée de ${connection.target.service}`, connection.target);
        }
        if (connection.via.length) {
          const via = document.createElement("span");
          via.className = "port-flow-via";
          via.textContent = `Via ${connection.via.join(" → ")}`;
          flow.append(via);
        }
        list.append(flow);
      });
      section.append(heading, list);
      container.append(section);
    }
    function appendAssociatedCodeFlows(title, flows, container = details) {
      if (!flows.length) return;
      const section = document.createElement("section");
      section.className = "details-section";
      const heading = document.createElement("h2");
      heading.textContent = title;
      const list = document.createElement("ul");
      list.className = "references-list associated-code-flows";
      [...flows].sort(compareCodeFlows).forEach(flow => {
        const item = document.createElement("li");
        item.className = `reference-item${flow.status === "cycle" ? " is-cycle" : ""}`;
        const summary = document.createElement("div");
        const trigger = flow.steps?.[0];
        const label = document.createElement("strong");
        label.className = "reference-title";
        label.textContent = `${codeFlowStepLabel(trigger?.kind)} · ${trigger?.name || "Déclencheur inconnu"}`;
        const meta = document.createElement("div");
        meta.className = "reference-meta";
        meta.textContent = `${flow.status === "cycle" ? "Cycle détecté · " : ""}${flow.steps?.length || 0} étape(s) · ${flow.method}`;
        summary.append(label, meta);
        const actions = document.createElement("div");
        actions.className = "associated-code-flow-actions";
        const listAction = document.createElement("button");
        listAction.type = "button";
        listAction.className = "reference-action";
        listAction.textContent = "Flux";
        listAction.title = "Ouvrir ce flux dans l’onglet Flux";
        listAction.addEventListener("click", () => openCodeFlowInList(flow));
        const graphAction = document.createElement("button");
        graphAction.type = "button";
        graphAction.className = "reference-action";
        graphAction.textContent = "Graphe d’appel";
        graphAction.title = "Afficher ce flux dans le graphe d’appel";
        graphAction.addEventListener("click", () => showCodeFlow(flow));
        actions.append(listAction, graphAction);
        if (flow.vscode_uri) {
          const sourceAction = document.createElement("a");
          sourceAction.className = "reference-action";
          sourceAction.href = flow.vscode_uri;
          sourceAction.textContent = "Java";
          sourceAction.title = `Ouvrir ${flow.method} dans VS Code`;
          actions.append(sourceAction);
        }
        item.append(summary, actions);
        list.append(item);
      });
      section.append(heading, list);
      container.append(section);
    }
    function appendActionList(title, entries, container = details) {
      if (!entries.length) return;
      const section = document.createElement("section");
      section.className = "details-section";
      const heading = document.createElement("h2");
      heading.textContent = title;
      const list = document.createElement("ul");
      entries.forEach(({ label, title: actionTitle, action }) => {
        const item = document.createElement("li");
        item.className = "relation-item";
        const button = document.createElement("button");
        button.className = "relation-link";
        button.type = "button";
        button.textContent = label;
        button.title = actionTitle || "Afficher cet élément dans le graphe";
        button.addEventListener("click", action);
        item.append(button);
        list.append(item);
      });
      section.append(heading, list);
      container.append(section);
    }
    function appendFindings(findings, container = details) {
      if (!findings.length) return;
      const section = document.createElement("section");
      section.className = "details-section";
      const heading = document.createElement("h2");
      heading.textContent = `Findings (${findings.length})`;
      const list = document.createElement("ul");
      findings.forEach(finding => {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = finding.vscode_uri;
        link.textContent = `[${finding.severity}] ${finding.rule_id} · Ouvrir le fichier`;
        link.title = `${finding.path}:${finding.start_line} — Ouvrir ce finding dans VS Code`;
        const message = document.createElement("div");
        message.textContent = finding.message;
        item.append(link, message);
        list.append(item);
      });
      section.append(heading, list);
      container.append(section);
    }
    function appendRelationList(title, links, currentId, labelForLink, container = details) {
      const seen = new Set();
      const entries = links.flatMap(link => {
        const targetId = link.source === currentId ? link.target : link.source;
        const label = labelForLink(link);
        const key = `${targetId}::${label}`;
        if (seen.has(key)) return [];
        seen.add(key);
        return [{ targetId, label }];
      });
      if (!entries.length) return;
      const section = document.createElement("section");
      section.className = "details-section";
      const heading = document.createElement("h2");
      heading.textContent = title;
      const list = document.createElement("ul");
      entries.forEach(({ targetId, label }) => {
        const item = document.createElement("li");
        item.className = "relation-item";
        const button = document.createElement("button");
        button.className = "relation-link";
        button.type = "button";
        button.textContent = label;
        button.title = "Sélectionner ce nœud dans le graphe";
        button.addEventListener("click", () => selectNode(targetId));
        item.append(button);
        list.append(item);
      });
      section.append(heading, list);
      container.append(section);
    }
    const inspectorModal = document.getElementById("inspector-modal");
    const inspectorTitle = document.getElementById("inspector-title");
    const inspectorBody = document.getElementById("inspector-body");
    const dtoNavigation = [];
    const mongoNavigation = [];
    function closeInspector() {
      inspectorModal.hidden = true;
      inspectorBody.replaceChildren();
      inspectorBody.className = "inspector-body";
      dtoNavigation.splice(0);
      mongoNavigation.splice(0);
    }
    function openInspector(title) {
      inspectorTitle.textContent = title;
      inspectorBody.replaceChildren();
      inspectorBody.className = "inspector-body";
      inspectorModal.hidden = false;
    }
    function openOpenApiContract(contract) {
      openInspector(`OpenAPI · ${contract.path}`);
      if (contract.vscode_uri) {
        const link = document.createElement("a");
        link.href = contract.vscode_uri;
        link.textContent = "Ouvrir le fichier dans VS Code";
        link.className = "dto-summary";
        inspectorBody.append(link);
      }
      if (!contract.spec || !window.SwaggerUIBundle) {
        const message = document.createElement("p");
        message.className = "dto-summary";
        message.textContent = "La specification locale ou Swagger UI n'est pas disponible dans cet export.";
        inspectorBody.append(message);
        return;
      }
      inspectorBody.classList.add("swagger-ui");
      window.SwaggerUIBundle({
        spec: contract.spec,
        domNode: inspectorBody,
        deepLinking: false,
        docExpansion: "list",
        supportedSubmitMethods: [],
      });
    }
    function openAsyncApiContract(contract) {
      openInspector(`AsyncAPI · ${contract.path}`);
      if (!contract.spec || !customElements.get("asyncapi-component")) {
        const message = document.createElement("p");
        message.className = "dto-summary";
        message.textContent = "Le composant AsyncAPI officiel n'est pas disponible dans cet export.";
        inspectorBody.append(message);
        return;
      }
      inspectorBody.classList.add("asyncapi-inspector");
      const shell = document.createElement("div");
      shell.className = "asyncapi-shell";
      const spec = contract.spec;
      const summary = document.createElement("header");
      summary.className = "asyncapi-summary";
      const summaryCopy = document.createElement("div");
      summaryCopy.className = "asyncapi-summary-copy";
      const kicker = document.createElement("p");
      kicker.className = "asyncapi-summary-kicker";
      kicker.textContent = "Contrat événementiel";
      const title = document.createElement("h2");
      title.className = "asyncapi-summary-title";
      title.textContent = spec.info?.title || contract.path;
      summaryCopy.append(kicker, title);
      if (spec.info?.description) {
        const description = document.createElement("p");
        description.className = "asyncapi-summary-description";
        description.textContent = spec.info.description;
        summaryCopy.append(description);
      }
      const meta = document.createElement("div");
      meta.className = "asyncapi-summary-meta";
      [
        `v${spec.info?.version || "?"}`,
        `${Object.keys(spec.channels || {}).length} channel${Object.keys(spec.channels || {}).length > 1 ? "s" : ""}`,
        `${Object.keys(spec.operations || {}).length} opération${Object.keys(spec.operations || {}).length > 1 ? "s" : ""}`,
      ].forEach(label => {
        const badge = document.createElement("span");
        badge.className = "asyncapi-summary-badge";
        badge.textContent = label;
        meta.append(badge);
      });
      summary.append(summaryCopy, meta);
      shell.append(summary);
      if (contract.vscode_uri) {
        const link = document.createElement("a");
        link.href = contract.vscode_uri;
        link.textContent = "Ouvrir le fichier dans VS Code";
        link.className = "asyncapi-source-link";
        shell.append(link);
      }
      const component = document.createElement("asyncapi-component");
      component.schema = JSON.stringify(contract.spec);
      component.config = { show: { info: false, errors: false } };
      component.cssImportPath = window.systemlensAsyncApiCssImportPath || "";
      shell.append(component);
      inspectorBody.append(shell);
    }
    function appendDtoInspectorSection(title, entries, itemClass = "dto-tag") {
      if (!entries.length) return;
      const section = document.createElement("section");
      section.className = "dto-section";
      const heading = document.createElement("h2");
      heading.textContent = title;
      const list = document.createElement("ul");
      list.className = "dto-tags";
      entries.forEach(entry => {
        const item = document.createElement("li");
        item.className = itemClass;
        item.textContent = entry;
        list.append(item);
      });
      section.append(heading, list);
      inspectorBody.append(section);
    }
    function dtoDefinition(dtoName) {
      return [...(graphData.kafka_dtos || []), ...(graphData.project_dto_definitions || [])]
        .find(item => item.id === dtoName);
    }
    function dtoLabel(dto) {
      const definitions = [...(graphData.kafka_dtos || []), ...(graphData.project_dto_definitions || [])];
      const duplicate = definitions.filter(item => item.name === dto.name).length > 1;
      return duplicate && dto.qualified_name ? `${dto.name} · ${dto.qualified_name}` : dto.name;
    }
    function openDtoInspector(dtoName) {
      dtoNavigation.splice(0);
      renderDtoInspector(dtoName);
    }
    function openMongoPersistenceInspector(classId) {
      mongoNavigation.splice(0);
      renderMongoPersistenceInspector(classId);
    }
    function openNestedMongoPersistenceInspector(classId, parentClassId) {
      mongoNavigation.push(parentClassId);
      renderMongoPersistenceInspector(classId);
    }
    function returnToContainingMongoClass() {
      const parentClassId = mongoNavigation.pop();
      if (parentClassId) renderMongoPersistenceInspector(parentClassId);
    }
    function renderMongoPersistenceInspector(classId) {
      const item = (graphData.mongo_persistence_classes || []).find(candidate => candidate.id === classId);
      if (!item) return;
      openInspector(`Données persistées · ${item.name}`);
      inspectorBody.classList.add("dto-inspector");
      if (mongoNavigation.length) {
        const navigation = document.createElement("div");
        navigation.className = "dto-navigation";
        const back = document.createElement("button");
        back.className = "dto-back";
        back.type = "button";
        back.textContent = "← Retour";
        back.addEventListener("click", returnToContainingMongoClass);
        navigation.append(back);
        inspectorBody.append(navigation);
      }
      const summary = document.createElement("p");
      summary.className = "dto-summary";
      summary.textContent = `${item.qualified_name} · ${item.source}:${item.line}`;
      inspectorBody.append(summary);
      if (item.vscode_uri) {
        const sourceLink = document.createElement("a");
        sourceLink.href = item.vscode_uri;
        sourceLink.className = "dto-summary";
        sourceLink.textContent = "Ouvrir la classe dans VS Code";
        inspectorBody.append(sourceLink);
      }
      const fields = item.fields || [];
      if (fields.length) {
        const section = document.createElement("section");
        section.className = "dto-section";
        const heading = document.createElement("h2");
        heading.textContent = "Champs déclarés";
        const list = document.createElement("ul");
        list.className = "dto-fields";
        fields.forEach(field => {
          const row = document.createElement("li");
          row.className = "dto-field";
          const references = field.references || [];
          const type = document.createElement(references.length ? "button" : "span");
          type.className = "dto-field-type";
          type.textContent = field.type;
          if (references.length) {
            type.type = "button";
            type.title = "Ouvrir le type projet référencé";
            type.addEventListener("click", () => (
              openNestedMongoPersistenceInspector(references[0], item.id)
            ));
          }
          const name = document.createElement("span");
          name.className = "dto-field-name";
          name.textContent = field.name;
          row.append(type, name);
          list.append(row);
        });
        section.append(heading, list);
        inspectorBody.append(section);
      }
      appendDtoInspectorSection("Data", [item.collection]);
      appendDtoInspectorSection("Microservice", [item.service]);
      appendDtoInspectorSection("Projet de persistance", [item.module]);
    }
    function openNestedDtoInspector(dtoName, parentDtoName) {
      dtoNavigation.push(parentDtoName);
      renderDtoInspector(dtoName);
    }
    function returnToContainingDto() {
      const parentDtoName = dtoNavigation.pop();
      if (parentDtoName) renderDtoInspector(parentDtoName);
    }
    function renderDtoInspector(dtoName) {
      const dto = dtoDefinition(dtoName);
      if (!dto) return;
      openInspector(`DTO de Topic · ${dto.name}`);
      inspectorBody.classList.add("dto-inspector");
      if (dtoNavigation.length) {
        const navigation = document.createElement("div");
        navigation.className = "dto-navigation";
        const back = document.createElement("button");
        back.className = "dto-back";
        back.type = "button";
        back.textContent = "← Retour";
        back.title = `Retour vers ${dtoNavigation.at(-1)}`;
        back.addEventListener("click", returnToContainingDto);
        navigation.append(back);
        inspectorBody.append(navigation);
      }
      const summary = document.createElement("p");
      summary.className = "dto-summary";
      summary.textContent = dto.source
        ? `Classe source : ${dto.source}`
        : "Classe Java non retrouvée dans les sources indexées ; les relations de messages restent disponibles.";
      inspectorBody.append(summary);
      if (dto.vscode_uri) {
        const sourceLink = document.createElement("a");
        sourceLink.href = dto.vscode_uri;
        sourceLink.className = "dto-summary";
        sourceLink.textContent = "Ouvrir la classe dans VS Code";
        inspectorBody.append(sourceLink);
      }
      const fields = dto.fields || [];
      if (fields.length) {
        const section = document.createElement("section");
        section.className = "dto-section";
        const heading = document.createElement("h2");
        heading.textContent = "Champs declares";
        const list = document.createElement("ul");
        list.className = "dto-fields";
        fields.forEach(field => {
          const item = document.createElement("li");
          item.className = "dto-field";
          const references = field.dto_references || [];
          const type = document.createElement(references.length ? "button" : "span");
          type.className = "dto-field-type";
          type.textContent = field.type;
          if (references.length) {
            type.type = "button";
            const referencedDto = dtoDefinition(references[0]);
            type.title = `Ouvrir le type projet ${referencedDto ? dtoLabel(referencedDto) : references[0]}`;
            type.addEventListener("click", () => openNestedDtoInspector(references[0], dto.id));
          }
          const name = document.createElement("span");
          name.className = "dto-field-name";
          name.textContent = field.name;
          item.append(type, name);
          list.append(item);
        });
        section.append(heading, list);
        inspectorBody.append(section);
      }
      appendDtoInspectorSection("Messages", dto.topics || []);
      appendDtoInspectorSection("Valeurs enum", dto.enum_values || []);
      appendDtoInspectorSection("Producteurs", dto.producers || []);
      appendDtoInspectorSection("Consommateurs", dto.consumers || []);
    }
