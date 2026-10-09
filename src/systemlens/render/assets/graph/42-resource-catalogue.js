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
    function resourceCatalogueOwner(entry) {
      return entry.service || entry.owner || "";
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
            ownerNode: node,
          });
        });
        (node.http_routes || []).forEach(route => entries.push({
          id: `route:${node.name}:${route.method || ""}:${route.route || route.path || ""}`,
          kind: "http_route",
          name: route.route || route.path || "Route HTTP",
          service: node.name,
          method: route.method || "HTTP",
          source: route.location || route.path,
          meta: `${node.name} · ${route.consumers?.length || route.callers?.length || 0} client(s)`,
          ownerNode: node,
        }));
        (node.openapi_contracts || []).forEach(contract => entries.push({
          id: `openapi:${node.name}:${contract.path}`,
          kind: "openapi_contract",
          name: contract.path,
          service: node.name,
          source: contract.path,
          meta: `${node.name} · ${contract.resources?.length || 0} ressource(s)`,
          ownerNode: node,
        }));
        (node.asyncapi_contracts || []).forEach(contract => entries.push({
          id: `asyncapi:${node.name}:${contract.path}`,
          kind: "asyncapi_contract",
          name: contract.path,
          service: node.name,
          source: contract.path,
          meta: `${node.name} · contrat de messages`,
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
        });
      });
      (graphData.kafka_dtos || []).forEach(dto => entries.push({
        id: `dto:${dto.id || dto.name}`,
        kind: "dto",
        name: dto.name || dtoLabel(dto),
        service: dto.service || dto.owner || "",
        meta: `${dto.fields?.length || 0} champ(s) · ${dto.topics?.length || 0} topic(s)`,
      }));
      (graphData.indexing_issues || []).forEach((issue, index) => entries.push({
        id: `diagnostic:${issue.id || index}`,
        kind: "diagnostic",
        name: issue.message || issue.category || "Fait à vérifier",
        meta: `${issue.severity === "warning" ? "À corriger" : "À vérifier"} · ${issue.category || "Indexation"}`,
        source: issue.location || issue.path,
        description: issue.message || issue.category || "Problème d’indexation",
      }));
      const seen = new Set();
      return entries.filter(entry => {
        if (seen.has(entry.id)) return false;
        seen.add(entry.id);
        return true;
      }).sort((left, right) => left.name.localeCompare(right.name));
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
    function renderResourceCataloguePreview(entry) {
      if (!resourceCataloguePreview) return;
      const title = document.getElementById("resource-catalogue-preview-title");
      const copy = document.getElementById("resource-catalogue-preview-copy");
      const incoming = entry.node ? graphData.links.filter(link => link.target === entry.node.id).length : 0;
      const outgoing = entry.node ? graphData.links.filter(link => link.source === entry.node.id).length : 0;
      title.textContent = entry.name;
      copy.replaceChildren();
      const kind = document.createElement("span"); kind.className = "resource-catalogue-preview-kind"; kind.textContent = resourceCatalogueKindLabels.get(entry.kind) || nodeKindLabel(entry.node || { kind: entry.kind });
      const stats = document.createElement("span"); stats.className = "resource-catalogue-preview-stats"; stats.textContent = entry.meta || `${incoming} entrée(s) · ${outgoing} sortie(s)`;
      copy.append(kind, stats);
      resourceCataloguePreview.querySelectorAll(".resource-catalogue-preview-owner, .resource-catalogue-preview-source").forEach(item => item.remove());
      if (resourceCatalogueOwner(entry)) { const owner = document.createElement("p"); owner.className = "resource-catalogue-preview-owner"; owner.textContent = `Propriétaire · ${resourceCatalogueOwner(entry)}`; resourceCataloguePreview.append(owner); }
      if (entry.source) { const source = document.createElement("p"); source.className = "resource-catalogue-preview-source"; source.textContent = `Source · ${entry.source}`; resourceCataloguePreview.append(source); }
      resourceCataloguePreview.classList.add("has-selection");
    }
    function renderResourceCatalogue() {
      microservicesList.replaceChildren();
      if (!resourceCatalogueEntries.length) renderResourceCatalogueControls();
      const query = microservicesFilter.value.trim().toLocaleLowerCase();
      const kind = resourceKindFilter?.value || "all";
      const service = resourceServiceFilter?.value || "all";
      const visible = resourceCatalogueEntries.filter(entry => resourceCatalogueEntryMatches(entry, kind, query, service));
      microservicesTitle.textContent = kind === "diagnostic"
        ? `Diagnostics (${visible.length}/${resourceCatalogueEntries.length})`
        : `Catalogue des ressources (${visible.length}/${resourceCatalogueEntries.length})`;
      if (resourceCatalogueSummary) resourceCatalogueSummary.textContent = `${visible.length} ressource${visible.length > 1 ? "s" : ""} affichée${visible.length > 1 ? "s" : "e"}`;
      microservicesEmpty.hidden = visible.length > 0;
      visible.forEach(entry => {
        const item = referenceItem(entry.method ? `${entry.method} ${entry.name}` : entry.name, entry.meta || `${resourceCatalogueKindLabels.get(entry.kind) || "Ressource"} · ${entry.service || ""}`, "Voir", () => {
          microservicesList.querySelectorAll(".is-selected").forEach(selected => selected.classList.remove("is-selected"));
          item.classList.add("is-selected");
          renderResourceCataloguePreview(entry);
          if (entry.node) return;
        });
        item.classList.add("architecture-resource-reference", "resource-catalogue-row");
        const text = item.firstElementChild;
        const meta = text?.querySelector(".reference-meta");
        if (text && meta) item.insertBefore(meta, item.lastElementChild);
        item.addEventListener("click", event => { if (!event.target.closest("button, a")) item.querySelector("button")?.click(); });
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
    renderResourceCatalogueControls();
