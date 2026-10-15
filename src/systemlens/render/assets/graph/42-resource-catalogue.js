// Ordered source module: 42-resource-catalogue.js
    const resourceCatalogueCategories = document.getElementById("resource-catalogue-categories");
    const resourceServiceFilter = document.getElementById("resource-service-filter");
    const resourceCatalogueKindLabels = new Map([
      ["all", "Toutes"],
      ["microservice", "Microservices"],
      ["http_route", "Routes HTTP"],
      ["openapi_contract", "Contrats OpenAPI"],
      ["asyncapi_contract", "Contrats AsyncAPI"],
      ["kafka_topic", "Topics"],
      ["mongodb_collection", "Collections"],
      ["jpa_entity", "Entités JPA"],
      ["dto", "DTOs"],
      ["diagnostic", "Diagnostics"],
      ["other", "Autres"],
    ]);
    let resourceCatalogueEntries = [];
    let resourceCatalogueByKey = new Map();
    let resourceCatalogueByNodeId = new Map();
    let resourceCatalogueSelection = null;
    const resourceCatalogueHistory = [];
    const resourceCatalogueBack = document.getElementById("resource-catalogue-back");
    const resourceCatalogueAttributes = document.getElementById("resource-catalogue-attributes");
    const resourceCatalogueRelated = document.getElementById("resource-catalogue-related");
    function resourceCatalogueOwner(entry) {
      return entry.service || entry.owner || "";
    }
    function resourceCatalogueEntryLabel(entry) {
      return entry.method ? `${entry.method} ${entry.name}` : entry.name;
    }
    function buildResourceCatalogueEntries() {
      const entries = [];
      graphData.nodes.forEach(node => {
        const base = {
          id: node.id,
          kind: node.kind,
          name: nodeDisplayName(node),
          service: node.kind === "microservice" ? node.name : node.owner || node.service || "",
          node,
          inspect: () => architectureNodeReference(node, "Voir"),
        };
        entries.push(base);
        if (node.kind !== "microservice") return;
        (node.resources || []).forEach(resource => {
          const match = String(resource).match(/^(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+(.+)$/);
          if (!match) return;
          entries.push({
            id: `route:${node.name}:${match[1]}:${match[2]}`,
            kind: "http_route",
            name: match[2],
            service: node.name,
            method: match[1],
            source: node.openapi_files?.[0],
            meta: `${node.name} · route exposée`,
            route: { route: match[0] },
            ownerNode: node,
          });
        });
        (node.http_routes || []).forEach(route => {
          const routeText = route.route || route.path || "";
          const match = routeText.match(/^(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+(.+)$/);
          const method = route.method || match?.[1] || "HTTP";
          const path = match?.[2] || routeText || "Route HTTP";
          entries.push({
            id: `route:${node.name}:${method}:${path}`,
            kind: "http_route",
            name: path,
            service: node.name,
            method,
            source: route.location || route.path,
            meta: `${node.name} · ${route.consumers?.length || route.callers?.length || 0} client(s)`,
            route,
            ownerNode: node,
          });
        });
        (node.openapi_contracts || []).forEach(contract => entries.push({
          id: `openapi:${node.name}:${contract.path}`,
          kind: "openapi_contract",
          name: contract.path,
          service: node.name,
          source: contract.path,
          meta: `${node.name} · ${contract.resources?.length || 0} ressource(s)`,
          contract,
          ownerNode: node,
        }));
        (node.asyncapi_contracts || []).forEach(contract => entries.push({
          id: `asyncapi:${node.name}:${contract.path}`,
          kind: "asyncapi_contract",
          name: contract.path,
          service: node.name,
          source: contract.path,
          meta: `${node.name} · contrat de messages`,
          contract,
          ownerNode: node,
        }));
      });
      (graphData.resource_descriptions || []).forEach(item => {
        const kind = item.kind === "mongo_persistence_class" ? "other" : item.kind;
        entries.push({
          id: item.id || `${kind}:${item.qualified_name || item.name}`,
          kind,
          name: item.name || item.qualified_name || "Ressource",
          service: item.owner || item.service || "",
          source: item.source?.path,
          meta: `${item.owner || item.service || ""} · ${item.attributes?.length || 0} champ(s)`,
          description: item.qualified_name,
          descriptionKind: item.kind,
          attributes: item.attributes || [],
          dtoName: kind === "dto" ? (item.id || item.qualified_name || item.name) : undefined,
        });
      });
      (graphData.kafka_dtos || []).forEach(dto => entries.push({
        id: `dto:${dto.id || dto.name}`,
        kind: "dto",
        name: dto.name || dtoLabel(dto),
        service: dto.service || dto.owner || "",
        meta: `${dto.fields?.length || 0} champ(s) · ${dto.topics?.length || 0} topic(s)`,
        dtoName: dto.id || dto.name || dtoLabel(dto),
        fields: dto.fields || [],
        topics: dto.topics || [],
      }));
      (graphData.indexing_issues || []).forEach((issue, index) => entries.push({
        id: `diagnostic:${issue.id || index}`,
        kind: "diagnostic",
        name: issue.message || issue.category || "Fait à vérifier",
        meta: `${issue.severity === "warning" ? "À corriger" : "À vérifier"} · ${issue.category || "Indexation"}`,
        source: issue.location || issue.path,
        description: issue.message || issue.category || "Problème d’indexation",
      }));
      const merged = new Map();
      entries.forEach(entry => {
        const identity = entry.kind === "dto"
          ? `dto:${entry.dtoName || entry.id}`
          : entry.id;
        const previous = merged.get(identity);
        if (!previous) {
          entry.catalogueKey = identity;
          merged.set(identity, entry);
          return;
        }
        if ((entry.meta || "").length > (previous.meta || "").length) previous.meta = entry.meta;
        previous.source ||= entry.source;
        previous.service ||= entry.service;
        previous.dtoName ||= entry.dtoName;
        previous.description ||= entry.description;
        previous.descriptionKind ||= entry.descriptionKind;
        previous.attributes ||= entry.attributes;
        previous.fields ||= entry.fields;
        previous.topics ||= entry.topics;
        if (entry.kind === "http_route" && entry.route) {
          previous.route = entry.route;
          previous.source = entry.source || previous.source;
        }
      });
      return [...merged.values()].sort((left, right) => left.name.localeCompare(right.name));
    }
    function resourceCatalogueEntryMatches(entry, kind, query, service) {
      const kindMatch = kind === "all" || (kind === "kafka_topic"
        ? ["kafka_topic", "message_channel"].includes(entry.kind)
        : kind === "other" ? !resourceCatalogueKindLabels.has(entry.kind) : entry.kind === kind);
      const serviceMatch = service === "all" || resourceCatalogueOwner(entry) === service;
      const queryMatch = !query || `${entry.name} ${entry.kind} ${entry.service} ${entry.description || ""}`.toLocaleLowerCase().includes(query);
      return kindMatch && serviceMatch && queryMatch;
    }
    function renderResourceCatalogueControls() {
      if (!resourceCatalogueCategories || !resourceServiceFilter) return;
      resourceCatalogueCategories.replaceChildren();
      resourceCatalogueEntries = buildResourceCatalogueEntries();
      resourceCatalogueByKey = new Map(resourceCatalogueEntries.map(entry => [entry.catalogueKey, entry]));
      resourceCatalogueByNodeId = new Map(resourceCatalogueEntries.filter(entry => entry.node).map(entry => [entry.node.id, entry]));
      const counts = new Map();
      resourceCatalogueEntries.forEach(entry => counts.set(entry.kind, (counts.get(entry.kind) || 0) + 1));
      ["all", "microservice", "http_route", "openapi_contract", "asyncapi_contract", "kafka_topic", "mongodb_collection", "jpa_entity", "dto", "diagnostic", "other"].forEach(kind => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "resource-catalogue-category";
        button.dataset.kind = kind;
        button.textContent = resourceCatalogueKindLabels.get(kind);
        const count = document.createElement("small");
        count.textContent = kind === "all" ? resourceCatalogueEntries.length : counts.get(kind) || 0;
        button.append(count);
        button.addEventListener("click", () => {
          resourceKindFilter.value = kind;
          renderResourceCatalogue();
        });
        resourceCatalogueCategories.append(button);
      });
      const services = [...new Set(resourceCatalogueEntries.map(resourceCatalogueOwner).filter(Boolean))].sort((a, b) => a.localeCompare(b));
      resourceServiceFilter.replaceChildren(new Option("Tous les microservices", "all"), ...services.map(service => new Option(service, service)));
    }
    function resourceCatalogueRelationLabel(entry, link) {
      const outgoing = link.source === entry.node.id;
      const relationKind = String(link.kind || "");
      if (relationKind === "kafka") {
        if (entry.kind === "microservice") return outgoing ? "Publie sur" : "Consomme";
        return outgoing ? "Consommé par" : "Publié par";
      }
      if (relationKind === "rest") return outgoing ? "Appelle" : "Appelé par";
      if (relationKind === "jpa") return outgoing ? "Déclare" : "Déclaré par";
      if (relationKind === "mongodb") return outgoing ? "Accède à" : "Utilisée par";
      if (relationKind.startsWith("enrichment_")) return `Relation importée · ${link.label || relationKind}`;
      return `${outgoing ? "Vers" : "Depuis"} · ${link.label || relationKind}`;
    }
    function resourceCatalogueRelatedEntries(entry) {
      const related = new Map();
      const add = (target, relation, rank = 0) => {
        if (!target || target.catalogueKey === entry.catalogueKey) return;
        const previous = related.get(target.catalogueKey);
        if (!previous || rank > previous.rank) related.set(target.catalogueKey, { target, relation, rank });
      };
      if (entry.node) {
        graphData.links.forEach(link => {
          if (link.source !== entry.node.id && link.target !== entry.node.id) return;
          const otherId = link.source === entry.node.id ? link.target : link.source;
          add(resourceCatalogueByNodeId.get(otherId), resourceCatalogueRelationLabel(entry, link),
            String(link.kind || "").startsWith("enrichment_") ? 1 : 3);
        });
      }
      const ownerName = resourceCatalogueOwner(entry);
      if (entry.kind === "microservice") {
        resourceCatalogueEntries.forEach(candidate => {
          if (!candidate.node && resourceCatalogueOwner(candidate) === entry.node.name) {
            add(candidate, "Ressource du service", 2);
          }
        });
      } else if (ownerName) {
        const owner = resourceCatalogueEntries.find(candidate => candidate.node?.kind === "microservice" && candidate.node.name === ownerName);
        add(owner, "Service propriétaire", 2);
      }
      if (["kafka_topic", "message_channel"].includes(entry.kind)) {
        resourceCatalogueEntries.forEach(candidate => {
          if (candidate.kind === "dto" && candidate.topics?.includes(entry.node.name)) add(candidate, "Type de message", 2);
        });
      } else if (entry.kind === "dto") {
        (entry.topics || []).forEach(topic => {
          const target = resourceCatalogueEntries.find(candidate => ["kafka_topic", "message_channel"].includes(candidate.node?.kind) && candidate.node.name === topic);
          add(target, "Topic du message", 2);
        });
      }
      const routeName = candidate => `${candidate.method} ${candidate.name}`;
      const contractTopics = contract => new Set(Object.values(contract?.spec?.channels || {})
        .map(channel => channel?.address).filter(Boolean));
      if (entry.kind === "openapi_contract") {
        const routes = new Set(entry.contract?.resources || []);
        resourceCatalogueEntries.forEach(candidate => {
          if (candidate.kind === "http_route" && candidate.service === entry.service && routes.has(routeName(candidate))) {
            add(candidate, "Route du contrat", 2);
          }
        });
      } else if (entry.kind === "http_route") {
        resourceCatalogueEntries.forEach(candidate => {
          if (candidate.kind === "openapi_contract" && candidate.service === entry.service
              && candidate.contract?.resources?.includes(routeName(entry))) add(candidate, "Contrat OpenAPI", 2);
        });
      } else if (entry.kind === "asyncapi_contract") {
        const topics = contractTopics(entry.contract);
        resourceCatalogueEntries.forEach(candidate => {
          if (["kafka_topic", "message_channel"].includes(candidate.kind) && topics.has(candidate.node.name)) {
            add(candidate, "Canal du contrat", 2);
          }
        });
      } else if (["kafka_topic", "message_channel"].includes(entry.kind)) {
        resourceCatalogueEntries.forEach(candidate => {
          if (candidate.kind === "asyncapi_contract" && contractTopics(candidate.contract).has(entry.node.name)) {
            add(candidate, "Contrat AsyncAPI", 2);
          }
        });
      }
      return [...related.values()].sort((left, right) => right.rank - left.rank
        || left.target.kind.localeCompare(right.target.kind)
        || resourceCatalogueEntryLabel(left.target).localeCompare(resourceCatalogueEntryLabel(right.target)));
    }
    function selectResourceCatalogueEntry(entry, recordHistory = true) {
      if (!entry) return;
      if (recordHistory && resourceCatalogueSelection && resourceCatalogueSelection.catalogueKey !== entry.catalogueKey) {
        resourceCatalogueHistory.push(resourceCatalogueSelection.catalogueKey);
      }
      resourceCatalogueSelection = entry;
      const query = microservicesFilter.value.trim().toLocaleLowerCase();
      if (!resourceCatalogueEntryMatches(entry, resourceKindFilter?.value || "all", query, resourceServiceFilter?.value || "all")) {
        microservicesFilter.value = "";
        resourceKindFilter.value = "all";
        resourceServiceFilter.value = "all";
        renderResourceCatalogue();
      }
      microservicesList.querySelectorAll(".resource-catalogue-row").forEach(row => {
        row.classList.toggle("is-selected", row.dataset.catalogueKey === entry.catalogueKey);
      });
      renderResourceCataloguePreview(entry);
      [...microservicesList.children].find(row => row.dataset.catalogueKey === entry.catalogueKey)
        ?.scrollIntoView({ block: "nearest" });
    }
    function resourceCatalogueAttributeGroups(entry) {
      if (entry.kind === "mongodb_collection") {
        return (entry.node?.persistence_classes || []).map(item => ({
          label: item.name,
          fields: item.fields || [],
        }));
      }
      if (entry.kind === "dto" || entry.kind === "jpa_entity" || entry.descriptionKind === "mongo_persistence_class") {
        return [{ label: "", fields: entry.attributes?.length ? entry.attributes : entry.fields || entry.node?.fields || [] }];
      }
      return [];
    }
    function resourceCatalogueFieldTargets(entry, field) {
      if (entry.kind !== "dto") return [];
      return [...new Set(field.dto_references || [])]
        .map(reference => resourceCatalogueByKey.get(`dto:${reference}`))
        .filter(target => target && target.catalogueKey !== entry.catalogueKey);
    }
    function renderResourceCatalogueAttributes(entry) {
      const groups = resourceCatalogueAttributeGroups(entry);
      resourceCatalogueAttributes.replaceChildren();
      resourceCatalogueAttributes.hidden = !groups.length && entry.kind !== "mongodb_collection";
      if (resourceCatalogueAttributes.hidden) return;
      const heading = document.createElement("h4");
      heading.textContent = entry.kind === "mongodb_collection" ? "Champs des classes associées" : "Attributs indexés";
      resourceCatalogueAttributes.append(heading);
      if (!groups.length) {
        const empty = document.createElement("p");
        empty.className = "resource-catalogue-attributes-empty";
        empty.textContent = "Aucun attribut indexé.";
        resourceCatalogueAttributes.append(empty);
        return;
      }
      groups.forEach(group => {
        if (group.label) {
          const label = document.createElement("p");
          label.className = "resource-catalogue-attributes-group";
          label.textContent = group.label;
          resourceCatalogueAttributes.append(label);
        }
        if (!group.fields.length) {
          const empty = document.createElement("p");
          empty.className = "resource-catalogue-attributes-empty";
          empty.textContent = "Aucun attribut indexé.";
          resourceCatalogueAttributes.append(empty);
          return;
        }
        const list = document.createElement("ul");
        list.className = "resource-catalogue-attributes-list";
        group.fields.forEach(field => {
          const item = document.createElement("li");
          const name = document.createElement("span");
          name.textContent = field.name;
          const types = document.createElement("div");
          types.className = "resource-catalogue-attribute-types";
          const targets = resourceCatalogueFieldTargets(entry, field);
          const referenceCount = new Set(field.dto_references || []).size;
          if (targets.length > 1) {
            const type = document.createElement("small");
            type.textContent = field.type || "Types possibles";
            types.append(type);
          }
          targets.forEach(target => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "resource-catalogue-attribute-link";
            const duplicateName = targets.some(other => other !== target && other.name === target.name);
            const label = referenceCount === 1 ? field.type || target.name
              : duplicateName ? target.dtoName : target.name;
            button.textContent = `${label} ›`;
            button.title = `Explorer les attributs de ${target.name}`;
            button.addEventListener("click", () => selectResourceCatalogueEntry(target));
            types.append(button);
          });
          if (!targets.length) {
            const type = document.createElement("small");
            type.textContent = field.type || "Type inconnu";
            types.append(type);
          }
          item.append(name, types);
          list.append(item);
        });
        resourceCatalogueAttributes.append(list);
      });
    }
    function renderResourceCataloguePreview(entry) {
      if (!resourceCataloguePreview) return;
      const title = document.getElementById("resource-catalogue-preview-title");
      const copy = document.getElementById("resource-catalogue-preview-copy");
      const incoming = entry.node ? graphData.links.filter(link => link.target === entry.node.id).length : 0;
      const outgoing = entry.node ? graphData.links.filter(link => link.source === entry.node.id).length : 0;
      title.textContent = resourceCatalogueEntryLabel(entry);
      copy.replaceChildren();
      const kind = document.createElement("span"); kind.className = "resource-catalogue-preview-kind"; kind.textContent = resourceCatalogueKindLabels.get(entry.kind) || nodeKindLabel(entry.node || { kind: entry.kind });
      const stats = document.createElement("span"); stats.className = "resource-catalogue-preview-stats"; stats.textContent = entry.meta || `${incoming} entrée(s) · ${outgoing} sortie(s)`;
      copy.append(kind, stats);
      resourceCataloguePreview.querySelectorAll(".resource-catalogue-preview-owner, .resource-catalogue-preview-source").forEach(item => item.remove());
      if (resourceCatalogueOwner(entry)) { const owner = document.createElement("p"); owner.className = "resource-catalogue-preview-owner"; owner.textContent = `Propriétaire · ${resourceCatalogueOwner(entry)}`; resourceCatalogueAttributes.before(owner); }
      if (entry.source) { const source = document.createElement("p"); source.className = "resource-catalogue-preview-source"; source.textContent = `Source · ${entry.source}`; resourceCatalogueAttributes.before(source); }
      const previous = resourceCatalogueByKey.get(resourceCatalogueHistory.at(-1));
      resourceCatalogueBack.hidden = !previous;
      resourceCatalogueBack.textContent = previous ? `← ${resourceCatalogueEntryLabel(previous)}` : "← Retour";
      renderResourceCatalogueAttributes(entry);
      const heading = document.createElement("h4");
      const relations = resourceCatalogueRelatedEntries(entry);
      heading.textContent = `Ressources liées (${relations.length})`;
      const list = document.createElement("ul");
      list.className = "resource-catalogue-related-list";
      relations.forEach(({ target, relation }) => {
        const item = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "resource-catalogue-related-link";
        const name = document.createElement("span");
        name.className = "resource-catalogue-related-name";
        name.textContent = resourceCatalogueEntryLabel(target);
        const meta = document.createElement("small");
        meta.textContent = `${resourceCatalogueKindLabels.get(target.kind) || nodeKindLabel(target.node || { kind: target.kind })} · ${relation}`;
        button.append(name, meta);
        button.addEventListener("click", () => selectResourceCatalogueEntry(target));
        item.append(button);
        list.append(item);
      });
      resourceCatalogueRelated.replaceChildren(heading, list);
      if (!relations.length) {
        const empty = document.createElement("p");
        empty.className = "resource-catalogue-related-empty";
        empty.textContent = "Aucune ressource liée dans l’index.";
        resourceCatalogueRelated.append(empty);
      }
      resourceCataloguePreview.classList.add("has-selection");
    }
    function renderResourceCatalogue() {
      microservicesList.replaceChildren();
      if (!resourceCatalogueEntries.length) renderResourceCatalogueControls();
      const query = microservicesFilter.value.trim().toLocaleLowerCase();
      const kind = resourceKindFilter?.value || "all";
      const service = resourceServiceFilter?.value || "all";
      resourceCatalogueCategories?.querySelectorAll(".resource-catalogue-category").forEach(button => {
        const active = button.dataset.kind === kind;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-pressed", String(active));
      });
      const visible = resourceCatalogueEntries.filter(entry => resourceCatalogueEntryMatches(entry, kind, query, service));
      microservicesTitle.textContent = kind === "diagnostic"
        ? `Diagnostics (${visible.length}/${resourceCatalogueEntries.length})`
        : `Catalogue des ressources (${visible.length}/${resourceCatalogueEntries.length})`;
      if (resourceCatalogueSummary) resourceCatalogueSummary.textContent = `${visible.length} ressource${visible.length > 1 ? "s" : ""} affichée${visible.length > 1 ? "s" : "e"}`;
      microservicesEmpty.hidden = visible.length > 0;
      visible.forEach(entry => {
        const selectResource = () => selectResourceCatalogueEntry(entry);
        const item = referenceItem(resourceCatalogueEntryLabel(entry), entry.meta || `${resourceCatalogueKindLabels.get(entry.kind) || "Ressource"} · ${entry.service || ""}`, "", selectResource);
        item.classList.add("architecture-resource-reference", "resource-catalogue-row");
        item.dataset.catalogueKey = entry.catalogueKey;
        item.classList.toggle("is-selected", resourceCatalogueSelection?.catalogueKey === entry.catalogueKey);
        const text = item.firstElementChild;
        const meta = text?.querySelector(".reference-meta");
        if (text && meta) item.insertBefore(meta, item.lastElementChild);
        const action = item.querySelector(".reference-action");
        const actionGroup = document.createElement("div");
        actionGroup.className = "resource-catalogue-actions";
        action?.replaceWith(actionGroup);
        const inspectTarget = entry.node || entry.ownerNode;
        if (entry.dtoName || entry.contract || inspectTarget) {
          const inspect = document.createElement("button");
          inspect.type = "button";
          inspect.className = "reference-action resource-catalogue-inspect";
          inspect.textContent = "Inspecter";
          inspect.title = "Ouvrir les détails d’introspection";
          inspect.addEventListener("click", event => {
            event.stopPropagation();
            selectResourceCatalogueEntry(entry);
            if (entry.dtoName) {
              openDtoInspector(entry.dtoName);
            } else if (entry.kind === "openapi_contract") {
              openOpenApiContract(entry.contract);
            } else if (entry.kind === "asyncapi_contract") {
              openAsyncApiContract(entry.contract);
            } else if (entry.kind === "http_route") {
              openHttpRouteInspector(inspectTarget, entry.route);
            } else {
              selectNode(inspectTarget.id, false, false);
              openArchitectureNodeInspector(inspectTarget, { reset: true });
              hideInlineDetailsAfterModal();
            }
          });
          actionGroup.append(inspect);
        } else {
          actionGroup.remove();
        }
        item.addEventListener("click", event => { if (!event.target.closest("button, a")) selectResource(); });
        microservicesList.append(item);
      });
    }
    function openResourceCatalogue(kind = "all") {
      if (resourceKindFilter) resourceKindFilter.value = kind;
      setToolbarTab("microservices");
      renderResourceCatalogue();
    }
    resourceKindFilter?.addEventListener("change", renderResourceCatalogue);
    resourceServiceFilter?.addEventListener("change", renderResourceCatalogue);
    microservicesFilter.addEventListener("input", renderResourceCatalogue);
    resourceCatalogueBack?.addEventListener("click", () => {
      const previous = resourceCatalogueByKey.get(resourceCatalogueHistory.pop());
      selectResourceCatalogueEntry(previous, false);
    });
    renderResourceCatalogueControls();
