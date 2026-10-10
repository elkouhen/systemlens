// Ordered source module: 40-details.js
    function normalizeNodeName(name) {
      return name.trim().replace(/\\s+/g, " ").toLocaleLowerCase();
    }
    function compareCodeFlows(left, right) {
      const leftCycle = left.status === "cycle" ? 0 : 1;
      const rightCycle = right.status === "cycle" ? 0 : 1;
      return leftCycle - rightCycle
        || (right.steps?.length || 0) - (left.steps?.length || 0)
        || String(left.method || "").localeCompare(String(right.method || ""));
    }
    graphData.nodes.forEach(node => {
      const key = normalizeNodeName(node.name);
      nodesByNormalizedName.set(key, [...(nodesByNormalizedName.get(key) || []), node]);
    });
    function setToolbarTab(tab, options = {}) {
      graphState.analysisContextCollapsed = true;
      document.body.classList.toggle("catalogue-active", tab === "microservices");
      const { showingGraph, showingFlows, graphVisible, resetView } = graphViewLifecycle.activate(tab, options);
      // The two navigation surfaces have different meanings: Graphe is the
      // static architecture view, while Flux de code only becomes a graph
      // after the user explicitly selects a flow from its list.
      if (resetView) {
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
      const showingMicroservices = tab === "microservices";
      const showingIssues = tab === "issues";
      const showingContracts = tab === "contracts";
      const showingAsyncApi = tab === "asyncapi";
      const showingDtoContracts = tab === "dto-contracts";
      const showingJpa = tab === "jpa";
      const showingRoutes = tab === "routes";
      const showingKafka = tab === "kafka";
      const showingCollections = tab === "collections";
      const showingPersistence = tab === "persistence";
      const showingContractView = showingContracts || showingAsyncApi || showingDtoContracts || showingJpa || showingPersistence;
      const mode = showingMicroservices
        ? "contracts"
        : showingFlows
        ? "architecture"
        : showingContractView
          ? "contracts"
          : showingIssues
            ? "diagnostics"
            : "architecture";
      Object.entries(modeTabs).forEach(([name, button]) => {
        const active = name === mode;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-selected", String(active));
      });
      Object.entries(modeGroups).forEach(([name, group]) => {
        group.hidden = name !== mode || (showingMicroservices && name === "contracts");
      });
      graphTab.classList.toggle("is-active", showingGraph);
      graphTab.setAttribute("aria-selected", String(showingGraph));
      microservicesTab.classList.toggle("is-active", showingMicroservices);
      microservicesTab.setAttribute("aria-selected", String(showingMicroservices));
      routesTab.classList.toggle("is-active", showingRoutes);
      routesTab.setAttribute("aria-selected", String(showingRoutes));
      contractsTab.classList.toggle("is-active", showingContracts);
      contractsTab.setAttribute("aria-selected", String(showingContracts));
      asyncApiTab.classList.toggle("is-active", showingAsyncApi);
      asyncApiTab.setAttribute("aria-selected", String(showingAsyncApi));
      dtoContractTab.classList.toggle("is-active", showingDtoContracts);
      dtoContractTab.setAttribute("aria-selected", String(showingDtoContracts));
      jpaTab.classList.toggle("is-active", showingJpa);
      jpaTab.setAttribute("aria-selected", String(showingJpa));
      kafkaTab.classList.toggle("is-active", showingKafka);
      kafkaTab.setAttribute("aria-selected", String(showingKafka));
      collectionsTab.classList.toggle("is-active", showingCollections);
      collectionsTab.setAttribute("aria-selected", String(showingCollections));
      persistenceTab.classList.toggle("is-active", showingPersistence);
      persistenceTab.setAttribute("aria-selected", String(showingPersistence));
      issuesTab.classList.toggle("is-active", showingIssues);
      issuesTab.setAttribute("aria-selected", String(showingIssues));
      flowsTab.classList.toggle("is-active", showingFlows);
      flowsTab.setAttribute("aria-selected", String(showingFlows));
      graphPanel.hidden = !showingGraph;
      microservicesPanel.hidden = !showingMicroservices;
      quickSearch.hidden = !showingGraph;
      graphContext.hidden = !showingGraph;
      issuesPanel.hidden = !showingIssues;
      contractsPanel.hidden = !showingContracts;
      asyncApiContractPanel.hidden = !showingAsyncApi;
      dtoContractPanel.hidden = !showingDtoContracts;
      jpaPanel.hidden = !showingJpa;
      routesPanel.hidden = !showingRoutes;
      kafkaPanel.hidden = !showingKafka;
      collectionsPanel.hidden = !showingCollections;
      persistencePanel.hidden = !showingPersistence;
      flowsPanel.hidden = !showingFlows;
      graphLegend.hidden = !showingGraph;
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
    const detailsActionRegistry = new Map();
    let detailsActionSequence = 0;
    function registerDetailsAction(element, action) {
      if (typeof action !== "function") return;
      const actionId = `details-action-${detailsActionSequence += 1}`;
      detailsActionRegistry.set(actionId, action);
      element.dataset.detailsActionId = actionId;
    }
    function resetDetailsActionRegistry() {
      detailsActionRegistry.clear();
      detailsActionSequence = 0;
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
    const httpMethodOrder = new Map([
      ["GET", 0], ["POST", 1], ["PUT", 2], ["DELETE", 3],
      ["PATCH", 4], ["HEAD", 5], ["OPTIONS", 6], ["TRACE", 7], ["ANY", 8],
    ]);
    function httpRouteParts(route) {
      const value = typeof route === "string" ? route : route.route || "";
      const [method, ...pathParts] = String(value).split(" ");
      return { method: method || "ANY", path: pathParts.join(" ") || value };
    }
    function httpRouteComparator(left, right) {
      const leftParts = httpRouteParts(left.route || left);
      const rightParts = httpRouteParts(right.route || right);
      return leftParts.path.localeCompare(rightParts.path)
        || (httpMethodOrder.get(leftParts.method) ?? 99) - (httpMethodOrder.get(rightParts.method) ?? 99)
        || leftParts.method.localeCompare(rightParts.method)
        || String(left.service || "").localeCompare(String(right.service || ""));
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
      const dtos = graphData.kafka_dtos || [];
      dtoContractReferencesList.replaceChildren();
      const query = dtoContractReferencesFilter.value.trim().toLocaleLowerCase();
      const visibleDtos = dtos.filter(dto => (
        !query || dtoLabel(dto).toLocaleLowerCase().includes(query)
      ));
      visibleDtos.forEach(dto => {
        const exchangeCount = (dto.producers?.length || 0) + (dto.consumers?.length || 0);
        const reference = () => referenceItem(
          dtoLabel(dto),
          `${dto.fields?.length || 0} champ(s) · ${dto.topics?.length || 0} topic(s) · ${exchangeCount} liaison(s)`,
          "Inspecter",
          () => openDtoInspector(dto.id),
        );
        dtoContractReferencesList.append(reference());
      });
      openapiReferencesTitle.textContent = `Contrats OpenAPI (${visibleContracts.length}/${contracts.length})`;
      dtoContractReferencesTitle.textContent = `DTOs de messages (${visibleDtos.length}/${dtos.length})`;
      dtoContractReferencesEmpty.hidden = visibleDtos.length > 0;
      dtoContractReferencesEmpty.textContent = query && !visibleDtos.length
        ? "Aucun DTO ne correspond à ce filtre."
        : "Aucun DTO de messages détecté.";
      routesList.replaceChildren();
      const routes = graphData.nodes.flatMap(node => (
        node.kind === "microservice"
          ? (node.http_routes || [])
            .filter(route => route.role === "serve")
            .map(route => ({ service: node.name, node, route }))
          : []
      ));
      const routesQuery = routesFilter.value.trim().toLocaleLowerCase();
      const visibleRoutes = routes
        .filter(({ service, route }) => (
          !routesQuery
          || `${service} ${route.route}`.toLocaleLowerCase().includes(routesQuery)
        ))
        .sort(httpRouteComparator);
      routesEmpty.hidden = visibleRoutes.length > 0;
      routesEmpty.textContent = routesQuery && !visibleRoutes.length
        ? "Aucune route ne correspond à ce filtre."
        : "Aucune route HTTP détectée.";
      const routesByService = new Map();
      visibleRoutes.forEach(item => {
        routesByService.set(item.service, [...(routesByService.get(item.service) || []), item]);
      });
      routesByService.forEach((serviceRoutes, service) => {
        const groupItem = document.createElement("li");
        groupItem.className = "route-provider-group";
        const group = document.createElement("details");
        group.open = true;
        const summary = document.createElement("summary");
        summary.textContent = `${service} · ${serviceRoutes.length} route${serviceRoutes.length > 1 ? "s" : ""}`;
        group.append(summary);
        const routeList = document.createElement("ul");
        routeList.className = "references-list route-provider-routes";
        const routesByPath = new Map();
        serviceRoutes.forEach(item => {
          const path = httpRouteParts(item.route).path;
          routesByPath.set(path, [...(routesByPath.get(path) || []), item]);
        });
        [...routesByPath.entries()].sort(([left], [right]) => left.localeCompare(right)).forEach(([path, pathRoutes]) => {
          const pathItem = document.createElement("li");
          pathItem.className = "route-path-group";
          const pathTitle = document.createElement("strong");
          pathTitle.textContent = path;
          pathItem.append(pathTitle);
          const methodList = document.createElement("ul");
          methodList.className = "references-list route-methods";
          pathRoutes.sort(httpRouteComparator).forEach(({ node, route }) => {
            const parts = httpRouteParts(route);
            const callers = [...new Set((node.http_callers || [])
              .filter(item => item.route === route.route)
              .map(item => item.service))].sort((left, right) => left.localeCompare(right));
            const item = document.createElement("li");
            item.className = "reference-item route-reference-item";
            const toggle = document.createElement("button");
            toggle.className = "route-reference-toggle";
            toggle.type = "button";
            const label = document.createElement("span");
            label.className = "route-reference-label";
            const method = document.createElement("span");
            method.className = "route-reference-method";
            method.textContent = parts.method;
            const routePath = document.createElement("span");
            routePath.className = "route-reference-path";
            routePath.textContent = parts.path;
            label.append(method, routePath);
            const clientCount = document.createElement("span");
            clientCount.className = "route-client-count";
            clientCount.textContent = `${callers.length} client${callers.length > 1 ? "s" : ""}`;
            toggle.append(label, clientCount);
            toggle.addEventListener("click", () => openHttpRouteInspector(node, route));
            item.append(toggle);
            methodList.append(item);
          });
          pathItem.append(methodList);
          routeList.append(pathItem);
        });
        group.append(routeList);
        groupItem.append(group);
        routesList.append(groupItem);
      });
      routesTitle.textContent = `Routes (${visibleRoutes.length}/${routes.length})`;
      const asyncContracts = graphData.nodes.flatMap(node => (
        node.kind === "microservice"
          ? (node.asyncapi_contracts || []).map(contract => ({ ...contract, module: node.name }))
          : []
      ));
      const asyncApiQuery = asyncApiPanelFilter.value.trim().toLocaleLowerCase();
      const visibleAsyncContracts = asyncContracts.filter(contract => (
        !asyncApiQuery
        || contract.module.toLocaleLowerCase().includes(asyncApiQuery)
        || contract.path.toLocaleLowerCase().includes(asyncApiQuery)
      ));
      asyncApiReferencesList.replaceChildren();
      asyncApiReferencesEmpty.hidden = visibleAsyncContracts.length > 0;
      asyncApiReferencesEmpty.textContent = asyncApiQuery && !visibleAsyncContracts.length
        ? "Aucun contrat ne correspond à ce filtre."
        : "Aucun contrat AsyncAPI détecté.";
      visibleAsyncContracts.forEach(contract => asyncApiReferencesList.append(referenceItem(
        contract.path,
        `${contract.module} · AsyncAPI ${contract.spec?.asyncapi || ""}`,
        "Inspecter",
        () => openAsyncApiContract(contract),
      )));
      asyncApiReferencesTitle.textContent = `Contrats AsyncAPI (${visibleAsyncContracts.length}/${asyncContracts.length})`;
      jpaReferencesList.replaceChildren();
      mongoClassReferencesList.replaceChildren();
      const persistenceClasses = (graphData.resource_descriptions || [])
        .filter(item => ["mongo_persistence_class", "jpa_entity"].includes(item.kind));
      const jpaQuery = jpaReferencesFilter.value.trim().toLocaleLowerCase();
      const jpaClasses = persistenceClasses.filter(item => item.kind === "jpa_entity");
      const visibleJpaClasses = jpaClasses.filter(item => (
        !jpaQuery || `${item.qualified_name} ${item.owner} ${item.technology}`.toLocaleLowerCase().includes(jpaQuery)
      ));
      jpaReferencesEmpty.hidden = visibleJpaClasses.length > 0;
      jpaReferencesEmpty.textContent = jpaQuery && !visibleJpaClasses.length
        ? "Aucune entité JPA ne correspond à ce filtre."
        : "Aucune entité JPA détectée.";
      visibleJpaClasses.forEach(item => jpaReferencesList.append(referenceItem(
        item.qualified_name,
        `${item.technology} · ${item.owner} · ${item.attributes?.length || 0} champ(s)`,
        "Inspecter",
        () => openJpaEntityInspector(item.navigation?.inspect),
      )));
      jpaReferencesTitle.textContent = `Entités JPA (${visibleJpaClasses.length}/${jpaClasses.length})`;
      const mongoQuery = mongoClassReferencesFilter.value.trim().toLocaleLowerCase();
      const mongoClasses = persistenceClasses.filter(item => item.kind === "mongo_persistence_class");
      const visibleMongoClasses = mongoClasses.filter(item => (
        !mongoQuery || `${item.qualified_name} ${item.owner} ${item.technology}`.toLocaleLowerCase().includes(mongoQuery)
      ));
      mongoClassReferencesEmpty.hidden = visibleMongoClasses.length > 0;
      mongoClassReferencesEmpty.textContent = mongoQuery && !visibleMongoClasses.length
        ? "Aucune classe Mongo ne correspond à ce filtre."
        : "Aucune classe Mongo détectée.";
      visibleMongoClasses.forEach(item => mongoClassReferencesList.append(referenceItem(
        item.qualified_name,
        `${item.technology} · ${item.owner} · ${item.attributes?.length || 0} champ(s)`,
        "Inspecter",
        () => openMongoPersistenceInspector(item.navigation?.inspect),
      )));
      mongoClassReferencesTitle.textContent = `Classes Mongo (${visibleMongoClasses.length}/${mongoClasses.length})`;
    }
    function renderMicroservices() {
      microservicesList.replaceChildren();
      const query = microservicesFilter.value.trim().toLocaleLowerCase();
      const kind = resourceKindFilter?.value || "all";
      const resources = graphData.nodes
        .filter(node => kind === "all"
          || (kind === "other"
            ? !["microservice", "kafka_topic", "message_channel", "mongodb_collection", "jpa_entity"].includes(node.kind)
            : kind === "kafka_topic" ? ["kafka_topic", "message_channel"].includes(node.kind) : node.kind === kind))
        .slice()
        .sort((left, right) => left.name.localeCompare(right.name));
      const visibleResources = resources.filter(node => (
        !query || `${node.name} ${nodeKindLabel(node)} ${node.owner || ""} ${node.service || ""}`.toLocaleLowerCase().includes(query)
      ));
      microservicesTitle.textContent = `Catalogue des ressources (${visibleResources.length}/${resources.length})`;
      if (resourceCatalogueSummary) resourceCatalogueSummary.textContent = `${visibleResources.length} ressource${visibleResources.length > 1 ? "s" : ""} affichée${visibleResources.length > 1 ? "s" : "e"}`;
      microservicesEmpty.hidden = visibleResources.length > 0;
      visibleResources.forEach(node => {
        const incoming = graphData.links.filter(link => link.target === node.id).length;
        const outgoing = graphData.links.filter(link => link.source === node.id).length;
        microservicesList.append(architectureNodeReference(
          node,
          `${nodeKindLabel(node)}${node.owner || node.service ? ` · ${node.owner || node.service}` : ""} · ${incoming} entrée${incoming > 1 ? "s" : ""} · ${outgoing} sortie${outgoing > 1 ? "s" : ""}`,
        ));
      });
    }
    function renderCollections() {
      collectionsList.replaceChildren();
      const query = collectionsFilter.value.trim().toLocaleLowerCase();
      const collections = graphData.nodes
        .filter(node => node.kind === "mongodb_collection")
        .slice()
        .sort((left, right) => left.name.localeCompare(right.name));
      const visibleCollections = collections.filter(node => (
        !query || node.name.toLocaleLowerCase().includes(query)
      ));
      collectionsTitle.textContent = `Collections (${visibleCollections.length}/${collections.length})`;
      collectionsEmpty.hidden = visibleCollections.length > 0;
      visibleCollections.forEach(node => {
        const incoming = graphData.links.filter(link => link.target === node.id).length;
        const outgoing = graphData.links.filter(link => link.source === node.id).length;
        collectionsList.append(architectureNodeReference(
          node,
          `Mongo · ${incoming} lecture${incoming > 1 ? "s" : ""} · ${outgoing} écriture${outgoing > 1 ? "s" : ""}`,
        ));
      });
    }
    function renderTopics() {
      topicsList.replaceChildren();
      const query = topicsFilter.value.trim().toLocaleLowerCase();
      const topics = graphData.nodes
        .filter(node => ["kafka_topic", "message_channel"].includes(node.kind))
        .slice()
        .sort((left, right) => left.name.localeCompare(right.name));
      const visibleTopics = topics.filter(node => !query || node.name.toLocaleLowerCase().includes(query));
      topicsTitle.textContent = `Topics (${visibleTopics.length}/${topics.length})`;
      topicsEmpty.hidden = visibleTopics.length > 0;
      visibleTopics.forEach(node => {
        const incoming = graphData.links.filter(link => link.target === node.id).length;
        const outgoing = graphData.links.filter(link => link.source === node.id).length;
        topicsList.append(architectureNodeReference(
          node,
          `${incoming} producteur${incoming > 1 ? "s" : ""} · ${outgoing} consommateur${outgoing > 1 ? "s" : ""}`,
        ));
      });
    }
    function architectureNodeReference(node, meta) {
      const item = referenceItem(nodeDisplayName(node), meta, "Voir", () => {
        if (document.body.classList.contains("catalogue-active")) {
          renderCataloguePreview(node);
          return;
        }
        setToolbarTab("graph");
        selectNode(node.id);
      });
      item.classList.add("architecture-resource-reference");
      item.addEventListener("click", event => {
        if (!event.target.closest("button, a")) item.querySelector("button")?.click();
      });
      return item;
    }
    function renderCataloguePreview(node) {
      if (!resourceCataloguePreview) return;
      const title = document.getElementById("resource-catalogue-preview-title");
      const copy = document.getElementById("resource-catalogue-preview-copy");
      const incoming = graphData.links.filter(link => link.target === node.id).length;
      const outgoing = graphData.links.filter(link => link.source === node.id).length;
      title.textContent = nodeDisplayName(node);
      copy.replaceChildren();
      const kind = document.createElement("span");
      kind.className = "resource-catalogue-preview-kind";
      kind.textContent = nodeKindLabel(node);
      const stats = document.createElement("span");
      stats.className = "resource-catalogue-preview-stats";
      stats.textContent = `${incoming} entrée${incoming > 1 ? "s" : ""} · ${outgoing} sortie${outgoing > 1 ? "s" : ""}`;
      copy.append(kind, stats);
      const owner = node.owner || node.service || node.project_namespace;
      resourceCataloguePreview.querySelectorAll(".resource-catalogue-preview-owner").forEach(item => item.remove());
      if (owner) {
        const ownerLine = document.createElement("p");
        ownerLine.className = "resource-catalogue-preview-owner";
        ownerLine.textContent = `Propriétaire · ${owner}`;
        resourceCataloguePreview.append(ownerLine);
      }
      resourceCataloguePreview.classList.add("has-selection");
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
        const openInList = () => openCodeFlowInList(flow);
        listAction.addEventListener("click", openInList);
        registerDetailsAction(listAction, openInList);
        const graphAction = document.createElement("button");
        graphAction.type = "button";
        graphAction.className = "reference-action";
        graphAction.textContent = "Arbre d’appel";
        graphAction.title = "Afficher ce flux dans le graphe d’appel";
        const openInGraph = () => showCodeFlow(flow);
        graphAction.addEventListener("click", openInGraph);
        registerDetailsAction(graphAction, openInGraph);
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
      entries.forEach(({ label, title: actionTitle, action, modelNodeId, inspectorKind, inspectorId, inspectorService, inspectorPath, inspectorRoute }) => {
        const item = document.createElement("li");
        item.className = "relation-item";
        const button = document.createElement("button");
        button.className = "relation-link";
        button.type = "button";
        if (modelNodeId) button.dataset.modelNodeId = modelNodeId;
        if (inspectorKind && inspectorId) {
          button.dataset.inspectorKind = inspectorKind;
          button.dataset.inspectorId = inspectorId;
        }
        if (inspectorKind && inspectorService && inspectorPath) {
          button.dataset.inspectorKind = inspectorKind;
          button.dataset.inspectorService = inspectorService;
          button.dataset.inspectorPath = inspectorPath;
        }
        if (["route", "http-call"].includes(inspectorKind) && inspectorService && inspectorRoute) {
          button.dataset.inspectorKind = inspectorKind;
          button.dataset.inspectorService = inspectorService;
          button.dataset.inspectorRoute = inspectorRoute;
        }
        button.textContent = label;
        button.title = actionTitle || "Afficher cet élément dans le graphe";
        button.addEventListener("click", action);
        registerDetailsAction(button, action);
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
        button.dataset.modelNodeId = targetId;
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
    const inspectorBack = document.getElementById("inspector-back");
    const inspectorBreadcrumb = document.getElementById("inspector-breadcrumb");
    const inspectorBody = document.getElementById("inspector-body");
    const dtoNavigation = [];
    const mongoNavigation = [];
    const architectureInspectorHistory = [];
    let inspectorBackAction = null;
    let architectureInspectorNode = null;
    function renderArchitectureInspectorNavigation() {
      inspectorBack.hidden = architectureInspectorHistory.length === 0
        && !inspectorBackAction
        && !dtoNavigation.length
        && !mongoNavigation.length;
      inspectorBreadcrumb.replaceChildren();
      if (!architectureInspectorNode) return;
      const path = [...architectureInspectorHistory, architectureInspectorNode];
      path.forEach((node, index) => {
        if (index) {
          const separator = document.createElement("span");
          separator.className = "inspector-breadcrumb-separator";
          separator.textContent = "›";
          inspectorBreadcrumb.append(separator);
        }
        const item = document.createElement(index === path.length - 1 ? "span" : "button");
        item.textContent = nodeDisplayName(node);
        if (item.tagName === "BUTTON") {
          item.type = "button";
          item.className = "inspector-breadcrumb-link";
          item.title = `Revenir à ${nodeDisplayName(node)}`;
          item.addEventListener("click", () => {
            architectureInspectorHistory.splice(index);
            selectNode(node.id);
            openArchitectureNodeInspector(node, { push: false });
          });
        }
        inspectorBreadcrumb.append(item);
      });
    }
    function closeInspector() {
      inspectorModal.hidden = true;
      hideInlineDetailsAfterModal();
      inspectorBody.replaceChildren();
      inspectorBody.className = "inspector-body";
      if (resourceCataloguePreview) {
        resourceCataloguePreview.classList.remove("has-selection");
        document.getElementById("resource-catalogue-preview-title").textContent = "Aucune ressource sélectionnée";
        document.getElementById("resource-catalogue-preview-copy").textContent = "Sélectionnez une ligne dans le catalogue pour afficher son contexte, ses relations et sa source.";
        resourceCataloguePreview.querySelectorAll(".resource-catalogue-preview-owner, .resource-catalogue-preview-source").forEach(item => item.remove());
        microservicesList?.querySelectorAll(".is-selected").forEach(item => item.classList.remove("is-selected"));
      }
      dtoNavigation.splice(0);
      mongoNavigation.splice(0);
      architectureInspectorHistory.splice(0);
      inspectorBackAction = null;
      architectureInspectorNode = null;
      inspectorBack.hidden = true;
      inspectorBreadcrumb.replaceChildren();
    }
    function openInspector(title, options = {}) {
      if (options.preserveArchitectureNavigation !== true) {
        architectureInspectorHistory.splice(0);
        architectureInspectorNode = null;
      }
      inspectorBackAction = null;
      inspectorTitle.textContent = title;
      inspectorBody.replaceChildren();
      inspectorBody.className = "inspector-body";
      inspectorModal.hidden = false;
      renderArchitectureInspectorNavigation();
    }
    function openArchitectureNodeInspector(node, options = {}) {
      if (options.reset) architectureInspectorHistory.splice(0);
      if (options.push && architectureInspectorNode && architectureInspectorNode.id !== node.id) {
        architectureInspectorHistory.push(architectureInspectorNode);
      }
      inspectorBackAction = options.backAction || null;
      architectureInspectorNode = node;
      openInspector(`${nodeKindLabel(node)} · ${nodeDisplayName(node)}`, {
        preserveArchitectureNavigation: true,
      });
      renderArchitectureInspectorNavigation();
      const widget = details.cloneNode(true);
      widget.removeAttribute("id");
      widget.classList.remove("is-empty");
      widget.classList.add("architecture-node-inspector");
      const clonedHeader = widget.querySelector(".details-header");
      clonedHeader?.querySelector(".details-kicker")?.remove();
      clonedHeader?.querySelector(".details-title")?.remove();
      clonedHeader?.classList.add("inspector-details-summary");
      widget.addEventListener("click", event => {
        const button = event.target.closest("[data-model-node-id]");
        if (button) {
          const target = nodeDataById.get(button.dataset.modelNodeId);
          if (!target) return;
          selectNode(target.id, false, false);
          openArchitectureNodeInspector(target, { push: true });
          return;
        }
        const inspectorButton = event.target.closest("[data-inspector-kind]");
        if (!inspectorButton) {
          const actionId = event.target.closest("[data-details-action-id]")?.dataset.detailsActionId;
          const action = actionId && detailsActionRegistry.get(actionId);
          if (action) {
            closeInspector();
            action();
          }
          return;
        }
        const id = inspectorButton.dataset.inspectorId;
        const parentNode = architectureInspectorNode;
        if (inspectorButton.dataset.inspectorKind === "dto") openDtoInspector(id);
        if (inspectorButton.dataset.inspectorKind === "jpa") openJpaEntityInspector(id);
        if (inspectorButton.dataset.inspectorKind === "mongo") openMongoPersistenceInspector(id);
        if (["openapi", "asyncapi"].includes(inspectorButton.dataset.inspectorKind)) {
          const owner = graphData.nodes.find(node => node.name === inspectorButton.dataset.inspectorService);
          const contract = owner?.[inspectorButton.dataset.inspectorKind === "openapi" ? "openapi_contracts" : "asyncapi_contracts"]
            ?.find(item => item.path === inspectorButton.dataset.inspectorPath);
          if (contract) {
            if (inspectorButton.dataset.inspectorKind === "openapi") openOpenApiContract(contract);
            else openAsyncApiContract(contract);
          }
        }
        if (inspectorButton.dataset.inspectorKind === "route") {
          const owner = graphData.nodes.find(node => node.name === inspectorButton.dataset.inspectorService);
          const route = owner?.http_routes?.find(item => item.route === inspectorButton.dataset.inspectorRoute);
          if (owner && route) openHttpRouteInspector(owner, route);
        }
        if (inspectorButton.dataset.inspectorKind === "http-call") {
          const owner = graphData.nodes.find(node => node.name === inspectorButton.dataset.inspectorService);
          const route = owner?.http_routes?.find(item => (
            item.role === "call" && item.route === inspectorButton.dataset.inspectorRoute
          ));
          const call = owner?.http_calls?.find(item => item.route === inspectorButton.dataset.inspectorRoute);
          if (owner && route) openHttpCallInspector(owner, route, call);
        }
        if (parentNode) {
          inspectorBackAction = () => {
            selectNode(parentNode.id);
            openArchitectureNodeInspector(parentNode, { push: false });
          };
          renderArchitectureInspectorNavigation();
        }
      });
      inspectorBody.append(widget);
    }
    function goBackArchitectureInspector() {
      const previous = architectureInspectorHistory.pop();
      if (!previous) return;
      selectNode(previous.id);
      openArchitectureNodeInspector(previous, { push: false });
    }
    function goBackInspector() {
      if (dtoNavigation.length) {
        returnToContainingDto();
        renderArchitectureInspectorNavigation();
        return;
      }
      if (mongoNavigation.length) {
        returnToContainingMongoClass();
        renderArchitectureInspectorNavigation();
        return;
      }
      if (inspectorBackAction) {
        const action = inspectorBackAction;
        inspectorBackAction = null;
        action();
        return;
      }
      goBackArchitectureInspector();
    }
    function openOpenApiContract(contract) {
      openInspector(`OpenAPI · ${contract.path}`);
      if (contract.vscode_uri) {
        const link = document.createElement("a");
        link.href = contract.vscode_uri;
        link.textContent = "Ouvrir le fichier dans VS Code";
        link.className = "inspector-source-link";
        inspectorBody.append(link);
      }
      if (!contract.spec || !window.SwaggerUIBundle) {
        const message = document.createElement("p");
        message.className = "dto-summary";
        message.textContent = "La spécification locale ou Swagger UI n'est pas disponible dans cet export.";
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
        `${Object.keys(spec.channels || {}).length} canal${Object.keys(spec.channels || {}).length > 1 ? "s" : ""}`,
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
        link.className = "inspector-source-link asyncapi-source-link";
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
    function findArchitectureNode(kinds, name, owner) {
      return graphData.nodes.find(node => (
        kinds.includes(node.kind)
        && node.name === name
        && (!owner || node.owner === owner)
      ));
    }
    function openArchitectureReference(node, backAction) {
      if (!node) return;
      selectNode(node.id, false, false);
      openArchitectureNodeInspector(node, { reset: true, backAction });
    }
    function architectureReferenceEntry(node, label, title, backAction) {
      return node ? {
        label,
        title,
        modelNodeId: node.id,
        action: () => openArchitectureReference(node, backAction),
      } : null;
    }
    function appendInspectorNodeOrValue(title, node, label, titleText, backAction) {
      const entry = architectureReferenceEntry(node, label, titleText, backAction);
      if (entry) appendActionList(title, [entry], inspectorBody);
      else appendDtoInspectorSection(title, [label]);
    }
    function appendFieldTypeControls(row, fieldType, references, targetLabel, openReference) {
      const typeControls = document.createElement("div");
      typeControls.className = "dto-field-types";
      if (!references.length) {
        const type = document.createElement("span");
        type.className = "dto-field-type";
        type.textContent = fieldType;
        typeControls.append(type);
      } else {
        references.forEach((reference, index) => {
          const type = document.createElement("button");
          type.type = "button";
          type.className = "dto-field-type";
          type.textContent = index === 0 ? fieldType : `Voir ${targetLabel(reference)}`;
          type.title = `Ouvrir le type projet ${targetLabel(reference)}`;
          type.addEventListener("click", () => openReference(reference));
          typeControls.append(type);
        });
      }
      row.append(typeControls);
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
    function dtoInspectorKindLabel(dto) {
      const roles = (dto.roles || []).map(role => role.toLowerCase());
      if (roles.some(role => role.includes("rest"))) return "DTO REST";
      if (roles.some(role => role.includes("jpa"))) return "DTO d'entité JPA";
      if (dto.root === false) return "DTO projet";
      return "DTO de message";
    }
    function openDtoInspector(dtoName) {
      dtoNavigation.splice(0);
      renderDtoInspector(dtoName);
    }
    function openMongoPersistenceInspector(classId) {
      mongoNavigation.splice(0);
      renderMongoPersistenceInspector(classId);
    }
    function openJpaEntityInspector(entityId) {
      const entity = nodeDataById.get(entityId);
      if (!entity || entity.kind !== "jpa_entity") return;
      openInspector(`Entité JPA · ${entity.display_name || entity.name}`);
      inspectorBody.classList.add("dto-inspector");
      const summary = document.createElement("p");
      summary.className = "dto-summary";
      summary.textContent = `${entity.name} · ${entity.source_path}:${entity.source_line}`;
      inspectorBody.append(summary);
      if (entity.vscode_uri) {
        const sourceLink = document.createElement("a");
        sourceLink.href = entity.vscode_uri;
        sourceLink.className = "inspector-source-link";
        sourceLink.textContent = "Ouvrir la classe dans VS Code";
        inspectorBody.append(sourceLink);
      }
      const ownerNode = findArchitectureNode(["microservice"], entity.owner);
      appendInspectorNodeOrValue(
        "Microservice",
        ownerNode,
        entity.owner || "Microservice non identifié",
        `Afficher le microservice ${entity.owner || "propriétaire"}`,
        () => openJpaEntityInspector(entity.id),
      );
      const fields = entity.fields || [];
      const section = document.createElement("section");
      section.className = "dto-section";
      const heading = document.createElement("h2");
      heading.textContent = "Attributs déclarés";
      const list = document.createElement("ul");
      list.className = "dto-fields";
      if (fields.length) {
        fields.forEach(field => {
          const row = document.createElement("li");
          row.className = "dto-field";
          const type = document.createElement("span");
          type.className = "dto-field-type";
          type.textContent = field.type;
          const name = document.createElement("span");
          name.className = "dto-field-name";
          name.textContent = field.name;
          row.append(type, name);
          list.append(row);
        });
      } else {
        const empty = document.createElement("li");
        empty.className = "dto-field";
        empty.textContent = "Aucun attribut indexé.";
        list.append(empty);
      }
      section.append(heading, list);
      inspectorBody.append(section);
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
      const summary = document.createElement("p");
      summary.className = "dto-summary";
      summary.textContent = `${item.qualified_name} · ${item.source}:${item.line}`;
      inspectorBody.append(summary);
      if (item.vscode_uri) {
        const sourceLink = document.createElement("a");
        sourceLink.href = item.vscode_uri;
        sourceLink.className = "inspector-source-link";
        sourceLink.textContent = "Ouvrir la classe dans VS Code";
        inspectorBody.append(sourceLink);
      }
      const collectionNode = findArchitectureNode(
        ["mongodb_collection"],
        item.collection,
        item.service,
      );
      const serviceNode = findArchitectureNode(["microservice"], item.service);
      appendInspectorNodeOrValue(
        "Data",
        collectionNode,
        item.collection || "Collection non identifiée",
        `Afficher la donnée ${item.collection || "associée"}`,
        () => renderMongoPersistenceInspector(item.id),
      );
      appendInspectorNodeOrValue(
        "Microservice",
        serviceNode,
        item.service || "Microservice non identifié",
        `Afficher le microservice ${item.service || "associé"}`,
        () => renderMongoPersistenceInspector(item.id),
      );
      if (item.module) {
        appendActionList("Projet de persistance", [{
          label: item.module,
          title: `Naviguer vers le projet ${item.module}`,
          action: () => {
            closeInspector();
            selectCluster(clusterDescriptorForPath(item.module));
          },
        }], inspectorBody);
      }
      const fields = item.fields || [];
      {
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
          const name = document.createElement("span");
          name.className = "dto-field-name";
          name.textContent = field.name;
          appendFieldTypeControls(
            row,
            field.type,
            references,
            reference => (graphData.mongo_persistence_classes || [])
              .find(candidate => candidate.id === reference)?.name || reference,
            reference => openNestedMongoPersistenceInspector(reference, item.id),
          );
          row.append(name);
          list.append(row);
        });
        if (!fields.length) {
          const empty = document.createElement("li");
          empty.className = "dto-field dto-empty-state";
          empty.textContent = "Aucun champ indexé.";
          list.append(empty);
        }
        section.append(heading, list);
        inspectorBody.append(section);
      }
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
      openInspector(`${dtoInspectorKindLabel(dto)} · ${dto.name}`);
      inspectorBody.classList.add("dto-inspector");
      const summary = document.createElement("p");
      summary.className = "dto-summary";
      summary.textContent = dto.source
        ? `Classe source : ${dto.source}`
        : "Classe Java non retrouvée dans les sources indexées ; les relations de messages restent disponibles.";
      inspectorBody.append(summary);
      if (dto.vscode_uri) {
        const sourceLink = document.createElement("a");
        sourceLink.href = dto.vscode_uri;
        sourceLink.className = "inspector-source-link";
        sourceLink.textContent = "Ouvrir la classe dans VS Code";
        inspectorBody.append(sourceLink);
      }
      const fields = dto.fields || [];
      {
        const section = document.createElement("section");
        section.className = "dto-section";
        const heading = document.createElement("h2");
        heading.textContent = "Champs déclarés";
        const list = document.createElement("ul");
        list.className = "dto-fields";
        fields.forEach(field => {
          const item = document.createElement("li");
          item.className = "dto-field";
          const references = field.dto_references || [];
          const name = document.createElement("span");
          name.className = "dto-field-name";
          name.textContent = field.name;
          appendFieldTypeControls(
            item,
            field.type,
            references,
            reference => {
              const referencedDto = dtoDefinition(reference);
              return referencedDto ? dtoLabel(referencedDto) : reference;
            },
            reference => openNestedDtoInspector(reference, dto.id),
          );
          item.append(name);
          list.append(item);
        });
        if (!fields.length) {
          const empty = document.createElement("li");
          empty.className = "dto-field dto-empty-state";
          empty.textContent = "Aucun champ indexé.";
          list.append(empty);
        }
        section.append(heading, list);
        inspectorBody.append(section);
      }
      const returnToDto = () => renderDtoInspector(dto.id);
      const messageEntries = (dto.topics || [])
        .map(topic => architectureReferenceEntry(
          findArchitectureNode(["kafka_topic", "message_channel"], topic),
          topic,
          `Afficher le topic ${topic}`,
          returnToDto,
        ))
        .filter(Boolean);
      if (messageEntries.length) appendActionList("Messages", messageEntries, inspectorBody);
      const unresolvedMessages = (dto.topics || []).filter(topic => (
        !findArchitectureNode(["kafka_topic", "message_channel"], topic)
      ));
      appendDtoInspectorSection("Messages non résolus", unresolvedMessages);
      appendDtoInspectorSection("Valeurs enum", dto.enum_values || []);
      const producerEntries = (dto.producers || [])
        .map(service => architectureReferenceEntry(
          findArchitectureNode(["microservice"], service),
          service,
          `Afficher le producteur ${service}`,
          returnToDto,
        ))
        .filter(Boolean);
      if (producerEntries.length) appendActionList("Producteurs", producerEntries, inspectorBody);
      const unresolvedProducers = (dto.producers || []).filter(service => (
        !findArchitectureNode(["microservice"], service)
      ));
      appendDtoInspectorSection("Producteurs non résolus", unresolvedProducers);
      const consumerEntries = (dto.consumers || [])
        .map(service => architectureReferenceEntry(
          findArchitectureNode(["microservice"], service),
          service,
          `Afficher le consommateur ${service}`,
          returnToDto,
        ))
        .filter(Boolean);
      if (consumerEntries.length) appendActionList("Consommateurs", consumerEntries, inspectorBody);
      const unresolvedConsumers = (dto.consumers || []).filter(service => (
        !findArchitectureNode(["microservice"], service)
      ));
      appendDtoInspectorSection("Consommateurs non résolus", unresolvedConsumers);
    }
