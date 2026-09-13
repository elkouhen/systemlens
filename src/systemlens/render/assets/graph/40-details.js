// Ordered source module: 40-details.js
    function normalizeNodeName(name) {
      return name.trim().replace(/\\s+/g, " ").toLocaleLowerCase();
    }
    graphData.nodes.forEach(node => {
      const key = normalizeNodeName(node.name);
      nodesByNormalizedName.set(key, [...(nodesByNormalizedName.get(key) || []), node]);
    });
    function setToolbarTab(tab) {
      if (document.querySelector(".toolbar")?.classList.contains("has-details")) {
        setDetailsEmpty("Selectionnez un noeud pour afficher ses details.");
      }
      const showingGraph = tab === "graph";
      const showingDependencies = tab === "dependencies";
      const showingIssues = tab === "issues";
      const showingOpenApi = tab === "openapi";
      const showingKafka = tab === "kafka";
      const showingPersistence = tab === "persistence";
      const showingFlows = tab === "flows";
      graphTab.classList.toggle("is-active", showingGraph);
      graphTab.setAttribute("aria-selected", String(showingGraph));
      openApiTab.classList.toggle("is-active", showingOpenApi);
      openApiTab.setAttribute("aria-selected", String(showingOpenApi));
      kafkaTab.classList.toggle("is-active", showingKafka);
      kafkaTab.setAttribute("aria-selected", String(showingKafka));
      persistenceTab.classList.toggle("is-active", showingPersistence);
      persistenceTab.setAttribute("aria-selected", String(showingPersistence));
      buildTab.classList.toggle("is-active", showingDependencies);
      buildTab.setAttribute("aria-selected", String(showingDependencies));
      issuesTab.classList.toggle("is-active", showingIssues);
      issuesTab.setAttribute("aria-selected", String(showingIssues));
      flowsTab.classList.toggle("is-active", showingFlows);
      flowsTab.setAttribute("aria-selected", String(showingFlows));
      graphPanel.hidden = !showingGraph;
      quickSearch.hidden = !showingGraph;
      graphContext.hidden = !showingGraph;
      dependenciesPanel.hidden = !showingDependencies;
      issuesPanel.hidden = !showingIssues;
      openApiPanel.hidden = !showingOpenApi;
      kafkaPanel.hidden = !showingKafka;
      persistencePanel.hidden = !showingPersistence;
      flowsPanel.hidden = !showingFlows;
      graphLegend.hidden = !showingGraph;
      graphCanvas.hidden = showingDependencies;
      dependencyCanvas.hidden = !showingDependencies;
      if (showingFlows) flowsPanel.dispatchEvent(new Event("systemlens:flows-open"));
      if (showingDependencies) {
        const activeDependencyRenderer = ensureDependencyRenderer();
        requestAnimationFrame(() => {
          activeDependencyRenderer.refresh();
          activeDependencyRenderer.getCamera().animatedReset({ duration: 220 });
        });
      }
    }
    function renderIndexingIssues() {
      inventoryStatus.hidden = false;
      inventoryStatus.classList.toggle("is-warning", indexingIssues.length > 0);
      inventoryStatus.textContent = indexingIssues.length
        ? `${indexingIssues.length} fait${indexingIssues.length > 1 ? "s" : ""} à vérifier`
        : "Inventaire complet";
      inventoryStatus.title = indexingIssues.length
        ? "Ouvrir les problèmes d'indexation"
        : "Aucun fait non résolu dans cet inventaire";
      indexingIssuesTitle.textContent = `Problemes d'indexation (${indexingIssues.length})`;
      indexingIssuesList.replaceChildren();
      indexingIssuesEmpty.hidden = indexingIssues.length > 0;
      indexingIssues.forEach(issue => {
        const item = document.createElement("li");
        item.className = `indexing-issue ${issue.severity}`;
        const header = document.createElement("div");
        header.className = "indexing-issue-header";
        const severity = document.createElement("span");
        severity.className = "indexing-issue-severity";
        severity.textContent = issue.severity === "warning" ? "A corriger" : "A verifier";
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
        : "Aucun DTO de Topics détecté.";
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
      dtoReferencesTitle.textContent = `DTO de Topics (${visibleDtos.length}/${dtos.length})`;
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
        : "Aucune classe de persistance Data détectée.";
      visiblePersistenceClasses.forEach(item => mongoClassReferencesList.append(referenceItem(
        item.qualified_name,
        `${item.collection} · ${item.service} / ${item.module} · ${item.fields?.length || 0} champ(s)`,
        "Inspecter",
        () => openMongoPersistenceInspector(item.id),
      )));
      mongoClassReferencesTitle.textContent = `Classes de persistance (${visiblePersistenceClasses.length}/${persistenceClasses.length})`;
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
        roleLabel.textContent = role;
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
        const action = document.createElement("button");
        action.type = "button";
        action.className = "reference-action";
        action.textContent = "Afficher";
        action.title = "Afficher ce flux dans le graphe";
        action.addEventListener("click", () => showCodeFlow(flow));
        item.append(summary, action);
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
        button.title = actionTitle || "Explorer cet element dans le graphe";
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
        button.title = "Selectionner ce noeud dans le graphe";
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
      openInspector(`Persistance Data · ${item.name}`);
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
        : "Classe Java non retrouvee dans les sources indexees ; les relations de Topics restent disponibles.";
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
      appendDtoInspectorSection("Topics", dto.topics || []);
      appendDtoInspectorSection("Valeurs enum", dto.enum_values || []);
      appendDtoInspectorSection("Producteurs", dto.producers || []);
      appendDtoInspectorSection("Consommateurs", dto.consumers || []);
    }
