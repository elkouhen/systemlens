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
    // The Contexte column always reads `kind · owner · detail`; the kind
    // labels are shared with the inspector titles (`resourceKindLabels`).
    const resourceCatalogueCount = (count, word) => `${count} ${word}${count > 1 ? "s" : ""}`;
    const resourceCatalogueContext = (...parts) => parts.filter(Boolean).join(" · ");
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
    // A row, its preview and its inspector title share one kind label.
    function resourceCatalogueKindLabel(entry) {
      return resourceKindLabel(entry.labelKind || entry.descriptionKind || (entry.node ? nodeResourceKind(entry.node) : entry.kind));
    }
    function resourceCatalogueEntryLabel(entry) {
      return entry.method ? `${entry.method} ${entry.name}` : entry.name;
    }
    // Served routes do not carry their callers; the owning service lists
    // them in `http_callers`, one entry per calling site.
    function resourceCatalogueRouteClientCount(node, route) {
      if (route.consumers?.length || route.callers?.length) return route.consumers?.length || route.callers?.length;
      const routeText = route.route || "";
      return new Set((node.http_callers || [])
        .filter(caller => caller.route === routeText)
        .map(caller => caller.service || caller.location)).size;
    }
    function buildResourceCatalogueEntries() {
      const entries = [];
      const relationCounts = new Map();
      (graphData.links || []).forEach(link => {
        [link.source, link.target].forEach(endpoint => {
          const nodeId = typeof endpoint === "object" ? endpoint?.id : endpoint;
          if (nodeId) relationCounts.set(nodeId, (relationCounts.get(nodeId) || 0) + 1);
        });
      });
      graphData.nodes.forEach(node => {
        const service = node.kind === "microservice" ? node.name : node.owner || node.service || "";
        const kindLabel = resourceKindLabel(nodeResourceKind(node));
        const base = {
          id: node.id,
          kind: node.kind,
          name: nodeDisplayName(node),
          service,
          tagKind: nodeResourceKind(node),
          // Row-only context; the preview keeps its entry and exit counts.
          rowMeta: resourceCatalogueContext(
            kindLabel,
            service && service !== nodeDisplayName(node) ? service : "",
            resourceCatalogueCount(relationCounts.get(node.id) || 0, "relation"),
          ),
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
            meta: resourceCatalogueContext(resourceKindLabel("served_route"), node.name),
            labelKind: "served_route",
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
            labelKind: route.role === "call" ? "called_route" : "served_route",
            meta: route.role === "call"
              ? resourceCatalogueContext(resourceKindLabel("called_route"), node.name)
              : resourceCatalogueContext(
                resourceKindLabel("served_route"), node.name,
                resourceCatalogueCount(resourceCatalogueRouteClientCount(node, route), "client"),
              ),
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
          meta: resourceCatalogueContext(resourceKindLabel("openapi_contract"), node.name, resourceCatalogueCount(contract.resources?.length || 0, "ressource")),
          contract,
          ownerNode: node,
        }));
        (node.asyncapi_contracts || []).forEach(contract => entries.push({
          id: `asyncapi:${node.name}:${contract.path}`,
          kind: "asyncapi_contract",
          name: contract.path,
          service: node.name,
          source: contract.path,
          meta: resourceCatalogueContext(resourceKindLabel("asyncapi_contract"), node.name),
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
          meta: resourceCatalogueContext(
            resourceKindLabel(item.kind),
            item.owner || item.service,
            resourceCatalogueCount(item.attributes?.length || 0, "champ"),
          ),
          mongoClassId: item.kind === "mongo_persistence_class" ? item.id : undefined,
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
        meta: resourceCatalogueContext(
          resourceKindLabel("dto"), dto.service || dto.owner,
          resourceCatalogueCount(dto.fields?.length || 0, "champ"),
          resourceCatalogueCount(dto.topics?.length || 0, "topic"),
        ),
        dtoName: dto.id || dto.name || dtoLabel(dto),
        fields: dto.fields || [],
        topics: dto.topics || [],
      }));
      (graphData.indexing_issues || []).forEach((issue, index) => entries.push({
        id: `diagnostic:${issue.id || index}`,
        kind: "diagnostic",
        name: issue.message || issue.category || "Fait à vérifier",
        meta: resourceCatalogueContext(resourceKindLabel("diagnostic"), issue.severity === "warning" ? "À corriger" : "À vérifier", issue.category || "Indexation"),
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
        previous.labelKind ||= entry.labelKind;
        previous.attributes ||= entry.attributes;
        previous.fields ||= entry.fields;
        previous.topics ||= entry.topics;
        if (entry.kind === "http_route" && entry.route) {
          previous.route = entry.route;
          previous.labelKind = entry.labelKind || previous.labelKind;
          previous.source = entry.source || previous.source;
        }
      });
      // Default order: kind (category order), then owner, then name.
      const kindOrder = [...resourceCatalogueKindLabels.keys()].filter(kind => kind !== "all");
      const kindRank = entry => {
        const rank = kindOrder.indexOf(entry.kind);
        return rank === -1 ? kindOrder.indexOf("other") : rank;
      };
      return [...merged.values()].sort((left, right) => (
        kindRank(left) - kindRank(right)
        || resourceCatalogueOwner(left).localeCompare(resourceCatalogueOwner(right))
        || left.name.localeCompare(right.name)
        || String(left.method || "").localeCompare(String(right.method || ""))
      ));
    }
    // A zero count reads as a muted `aucun client` rather than `0 client`.
    const resourceCatalogueFeminineWords = new Set(["ressource", "relation"]);
    function renderResourceCatalogueContext(element, parts) {
      element.replaceChildren();
      parts.forEach((part, index) => {
        if (index) element.append(" · ");
        const zero = String(part).match(/^0 (\S+)$/);
        if (!zero) {
          element.append(part);
          return;
        }
        const muted = document.createElement("span");
        muted.className = "resource-catalogue-zero";
        muted.textContent = `${resourceCatalogueFeminineWords.has(zero[1]) ? "aucune" : "aucun"} ${zero[1]}`;
        element.append(muted);
      });
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
        // The title carries the total; categories carry their own count and
        // an empty category stays listed but cannot be chosen.
        if (kind !== "all") {
          const count = document.createElement("small");
          count.textContent = kind === "diagnostic" && !counts.get(kind)
            ? "aucun diagnostic"
            : counts.get(kind) || 0;
          button.append(count);
          button.disabled = !counts.get(kind);
        }
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
            add(candidate, "Ressource du microservice", 2);
          }
        });
      } else if (ownerName) {
        const owner = resourceCatalogueEntries.find(candidate => candidate.node?.kind === "microservice" && candidate.node.name === ownerName);
        add(owner, "Microservice propriétaire", 2);
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
      const kind = document.createElement("span"); kind.className = "resource-catalogue-preview-kind"; kind.textContent = resourceCatalogueKindLabel(entry);
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
        meta.textContent = `${resourceCatalogueKindLabel(target)} · ${relation}`;
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
      if (resourceCatalogueSummary) {
        resourceCatalogueSummary.textContent = "";
        resourceCatalogueSummary.hidden = true;
      }
      microservicesEmpty.hidden = visible.length > 0;
      visible.forEach(entry => {
        const selectResource = () => selectResourceCatalogueEntry(entry);
        const item = referenceItem(resourceCatalogueEntryLabel(entry), entry.meta || entry.rowMeta || resourceCatalogueContext(
          resourceCatalogueKindLabel(entry),
          entry.service && entry.service !== entry.name ? entry.service : "",
        ), "", selectResource);
        item.classList.add("architecture-resource-reference", "resource-catalogue-row");
        item.dataset.catalogueKey = entry.catalogueKey;
        item.classList.toggle("is-selected", resourceCatalogueSelection?.catalogueKey === entry.catalogueKey);
        const text = item.firstElementChild;
        const meta = text?.querySelector(".reference-meta");
        // The kind moves to a coloured tag; the Contexte column keeps the
        // owner and the relation summary.
        const [kindLabel, ...contextParts] = String(meta?.textContent || "").split(" · ");
        const tag = document.createElement("span");
        tag.className = "resource-kind-tag";
        tag.dataset.kind = entry.tagKind || entry.kind;
        tag.textContent = kindLabel || resourceCatalogueKindLabel(entry);
        const title = text?.querySelector(".reference-title");
        if (title && entry.method) {
          const verb = document.createElement("span");
          verb.className = "resource-http-verb";
          verb.dataset.method = entry.method;
          verb.textContent = entry.method;
          title.replaceChildren(verb, " ", entry.name);
        }
        title?.before(tag);
        if (meta) renderResourceCatalogueContext(meta, contextParts);
        if (text && meta) item.insertBefore(meta, item.lastElementChild);
        const action = item.querySelector(".reference-action");
        const actionGroup = document.createElement("div");
        actionGroup.className = "resource-catalogue-actions";
        action?.replaceWith(actionGroup);
        const inspectTarget = entry.node || entry.ownerNode;
        if (entry.dtoName || entry.contract || entry.mongoClassId || inspectTarget) {
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
            } else if (entry.mongoClassId) {
              openMongoPersistenceInspector(entry.mongoClassId);
            } else if (entry.kind === "openapi_contract") {
              openOpenApiContract(entry.contract);
            } else if (entry.kind === "asyncapi_contract") {
              openAsyncApiContract(entry.contract);
            } else if (entry.kind === "http_route" && entry.route?.role === "call") {
              // A called route opens the caller's call inspector; its provider
              // is the call's target, never the caller (PDR-10).
              const call = (inspectTarget.http_calls || []).find(item => item.route === entry.route.route);
              openHttpCallInspector(inspectTarget, entry.route, call);
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
          const unavailable = document.createElement("span");
          unavailable.className = "resource-catalogue-no-inspector";
          unavailable.textContent = "Aperçu seul";
          unavailable.title = "Aucun inspecteur dédié : l’aperçu présente les faits indexés.";
          actionGroup.append(unavailable);
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
