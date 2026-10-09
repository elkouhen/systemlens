// Ordered source module: 20-controls.js
    const details = document.getElementById("details");
    const quickSearch = document.getElementById("quick-search");
    const graphContext = document.getElementById("graph-context");
    const search = document.getElementById("search");
    const resetButton = document.getElementById("reset");
    const searchStatus = document.getElementById("search-status");
    const graphTab = document.getElementById("graph-tab");
    const microservicesTab = document.getElementById("microservices-tab");
    const contractsTab = document.getElementById("contracts-tab");
    const asyncApiTab = document.getElementById("asyncapi-tab");
    const dtoContractTab = document.getElementById("dto-contract-tab");
    const jpaTab = document.getElementById("jpa-tab");
    const routesTab = document.getElementById("routes-tab");
    const kafkaTab = document.getElementById("kafka-tab");
    const collectionsTab = document.getElementById("collections-tab");
    const persistenceTab = document.getElementById("persistence-tab");
    const issuesTab = document.getElementById("issues-tab");
    const flowsTab = document.getElementById("flows-tab");
    const modeTabs = {
      architecture: document.getElementById("architecture-mode-tab"),
      flows: document.getElementById("flows-mode-tab"),
      contracts: document.getElementById("contracts-mode-tab"),
      diagnostics: document.getElementById("diagnostics-mode-tab"),
    };
    const modeGroups = {
      architecture: document.getElementById("architecture-tabs-group"),
      flows: document.getElementById("flows-tabs-group"),
      contracts: document.getElementById("contracts-tabs-group"),
      diagnostics: document.getElementById("diagnostics-tabs-group"),
    };
    const graphLegend = document.getElementById("graph-legend");
    const dependencyDepth = document.getElementById("dependency-depth");
    const dependencyFocusOnly = document.getElementById("dependency-focus-only");
    const dependencyAnalysisControls = document.getElementById("dependency-analysis-controls");
    const dependencyAnalysisHelp = document.getElementById("dependency-analysis-help");
    const graphPanel = document.getElementById("graph-panel");
    const microservicesPanel = document.getElementById("microservices-panel");
    const issuesPanel = document.getElementById("issues-panel");
    const flowsPanel = document.getElementById("flows-panel");
    const contractsPanel = document.getElementById("contracts-panel");
    const asyncApiContractPanel = document.getElementById("asyncapi-contract-panel");
    const dtoContractPanel = document.getElementById("dto-contract-panel");
    const jpaPanel = document.getElementById("jpa-panel");
    const routesPanel = document.getElementById("routes-panel");
    const kafkaPanel = document.getElementById("kafka-panel");
    const collectionsPanel = document.getElementById("collections-panel");
    const persistencePanel = document.getElementById("persistence-panel");
    const graphCanvas = document.getElementById("graph");
    const indexingIssuesList = document.getElementById("indexing-issues");
    const indexingIssuesEmpty = document.getElementById("indexing-issues-empty");
    const indexingIssuesTitle = document.getElementById("indexing-issues-title");
    const indexingIssues = graphData.indexing_issues || [];
    const inventoryStatus = document.getElementById("inventory-status");
    const progressNotice = document.getElementById("progress-notice");
    const microservicesList = document.getElementById("microservices-list");
    const microservicesEmpty = document.getElementById("microservices-empty");
    const microservicesFilter = document.getElementById("microservices-filter");
    const microservicesTitle = document.getElementById("microservices-title");
    const microserviceVisibilityList = document.getElementById("microservice-visibility-list");
    const microserviceVisibilityReset = document.getElementById("microservice-visibility-reset");
    const microserviceVisibilityFilter = document.getElementById("microservice-visibility-filter");
    const microserviceVisibilitySummary = document.getElementById("microservice-visibility-summary");
    const topicVisibilityList = document.getElementById("topic-visibility-list");
    const topicVisibilityReset = document.getElementById("topic-visibility-reset");
    const topicVisibilityFilter = document.getElementById("topic-visibility-filter");
    const topicVisibilitySummary = document.getElementById("topic-visibility-summary");
    const collectionsList = document.getElementById("collections-list");
    const collectionsEmpty = document.getElementById("collections-empty");
    const collectionsFilter = document.getElementById("collections-filter");
    const collectionsTitle = document.getElementById("collections-title");
    const openApiReferencesList = document.getElementById("openapi-references");
    const openApiReferencesEmpty = document.getElementById("openapi-references-empty");
    const openApiReferencesFilter = document.getElementById("openapi-reference-filter");
    const dtoContractReferencesList = document.getElementById("dto-contract-references");
    const dtoContractReferencesEmpty = document.getElementById("dto-contract-references-empty");
    const dtoContractReferencesFilter = document.getElementById("dto-contract-reference-filter");
    const dtoContractReferencesTitle = document.getElementById("dto-contract-references-title");
    const openapiReferencesTitle = document.getElementById("openapi-references-title");
    const routesList = document.getElementById("routes-list");
    const routesEmpty = document.getElementById("routes-empty");
    const routesFilter = document.getElementById("routes-filter");
    const routesTitle = document.getElementById("routes-title");
    const topicsList = document.getElementById("topics-list");
    const topicsEmpty = document.getElementById("topics-empty");
    const topicsFilter = document.getElementById("topics-filter");
    const topicsTitle = document.getElementById("topics-title");
    const asyncApiReferencesList = document.getElementById("asyncapi-panel-references");
    const asyncApiReferencesEmpty = document.getElementById("asyncapi-panel-empty");
    const asyncApiReferencesTitle = document.getElementById("asyncapi-panel-title");
    const asyncApiPanelFilter = document.getElementById("asyncapi-reference-filter");
    const mongoClassReferencesList = document.getElementById("mongo-class-references");
    const mongoClassReferencesEmpty = document.getElementById("mongo-class-references-empty");
    const mongoClassReferencesFilter = document.getElementById("mongo-class-reference-filter");
    const mongoClassReferencesTitle = document.getElementById("mongo-class-references-title");
    const jpaReferencesList = document.getElementById("jpa-references");
    const jpaReferencesEmpty = document.getElementById("jpa-references-empty");
    const jpaReferencesFilter = document.getElementById("jpa-reference-filter");
    const jpaReferencesTitle = document.getElementById("jpa-references-title");
    const layoutStatus = document.getElementById("layout-status");
    const layoutButtons = new Map([
      ["forceatlas2", document.getElementById("layout-forceatlas2")],
      ["noverlap", document.getElementById("layout-noverlap")],
      ["forceatlas2-noverlap", document.getElementById("layout-forceatlas2-noverlap")],
      ["elk", document.getElementById("layout-elk")],
      ["cluster", document.getElementById("layout-cluster")],
    ]);
    const layoutLabels = new Map([
      ["forceatlas2", "regroupement des liens"],
      ["noverlap", "éviter les chevauchements"],
      ["forceatlas2-noverlap", "vue par graphe"],
      ["elk", "vue par couches"],
      ["cluster", "vue par modules"],
    ]);
    const pathStops = [];
    const MAX_SIMPLE_PATH_DEPTH = 8;
    const MAX_SIMPLE_PATHS = 8;
    const MAX_SIMPLE_PATH_EXPLORATIONS = 2000;
    const microserviceNames = graphData.nodes
      .filter(node => node.kind === "microservice")
      .map(node => node.name)
      .filter(Boolean)
      .sort((left, right) => left.localeCompare(right));
    const topicNames = graphData.nodes
      .filter(node => ["kafka_topic", "message_channel"].includes(node.kind))
      .map(node => node.name)
      .filter(Boolean)
      .sort((left, right) => left.localeCompare(right));
    function createVisibilityController({
      names, hiddenItems, storageKey, list, filter, summary, reset,
      itemLabel, emptyLabel, emptySearchLabel,
    }) {
      const persist = () => {
        try {
          localStorage.setItem(storageKey, JSON.stringify([...hiddenItems].sort()));
        } catch (_error) { /* optional preference */ }
      };
      const render = () => {
        if (!list) return;
        list.replaceChildren();
        const query = (filter?.value || "").trim().toLocaleLowerCase();
        const hiddenNames = names.filter(name => hiddenItems.has(name));
        const matchingNames = query
          ? names.filter(name => name.toLocaleLowerCase().includes(query))
          : hiddenNames;
        if (summary) {
          const hiddenCount = hiddenNames.length;
          summary.textContent = query
            ? `${matchingNames.length} résultat${matchingNames.length > 1 ? "s" : ""} · ${hiddenCount} caché${hiddenCount > 1 ? "s" : ""}`
            : hiddenCount
              ? `${hiddenCount} ${itemLabel}${hiddenCount > 1 ? "s" : ""} caché${hiddenCount > 1 ? "s" : ""}`
              : `Aucun ${itemLabel} caché`;
        }
        if (!matchingNames.length) {
          const empty = document.createElement("p");
          empty.className = "microservice-visibility-empty";
          empty.textContent = query ? emptyLabel : emptySearchLabel;
          list.append(empty);
        }
        matchingNames.forEach(name => {
          const label = document.createElement("label");
          label.className = "microservice-visibility-item";
          const input = document.createElement("input");
          input.type = "checkbox";
          input.checked = !hiddenItems.has(name);
          input.setAttribute("aria-label", `Afficher ${name}`);
          input.addEventListener("change", () => {
            if (input.checked) hiddenItems.delete(name);
            else hiddenItems.add(name);
            persist();
            render();
            rebuildGraph();
          });
          label.append(input, document.createTextNode(name));
          list.append(label);
        });
      };
      reset?.addEventListener("click", () => {
        hiddenItems.clear();
        persist();
        render();
        rebuildGraph();
      });
      filter?.addEventListener("input", render);
      render();
    }
    createVisibilityController({
      names: microserviceNames,
      hiddenItems: hiddenMicroservices,
      storageKey: hiddenMicroservicesStorageKey,
      list: microserviceVisibilityList,
      filter: microserviceVisibilityFilter,
      summary: microserviceVisibilitySummary,
      reset: microserviceVisibilityReset,
      itemLabel: "microservice",
      emptyLabel: "Aucun microservice correspondant.",
      emptySearchLabel: "Utilisez la recherche pour en cacher un.",
    });
    createVisibilityController({
      names: topicNames,
      hiddenItems: hiddenTopics,
      storageKey: hiddenTopicsStorageKey,
      list: topicVisibilityList,
      filter: topicVisibilityFilter,
      summary: topicVisibilitySummary,
      reset: topicVisibilityReset,
      itemLabel: "topic",
      emptyLabel: "Aucun topic correspondant.",
      emptySearchLabel: "Utilisez la recherche pour en cacher un.",
    });
    function restoreInitialNodePositions() {
      network.forEachNode(node => {
        const position = initialNodePositions.get(node);
        network.setNodeAttribute(node, "x", position.x);
        network.setNodeAttribute(node, "y", position.y);
      });
    }
    function namespaceForNode(node) {
      const data = nodeDataById.get(node);
      if (data?.kind !== "microservice") {
        const producer = preferredOwnerForNode(node);
        const producerNamespace = producer?.cluster_path || producer?.project_namespace_path
          || producer?.project_namespace || producer?.architecture_namespace;
        if (producerNamespace) return producerNamespace;
      }
      if (data?.cluster_path) return data.cluster_path;
      if (data?.project_namespace_path) return data.project_namespace_path;
      if (data?.project_namespace) return data.project_namespace;
      if (data?.architecture_namespace) return data.architecture_namespace;
      const namespaces = [...(data?.runtime_namespaces || []), ...(data?.fact_namespaces || [])];
      if (namespaces.length) return namespaces[0];
      if (data?.kind !== "microservice") return "root";
      const neighbourNamespaces = network.neighbors(node).flatMap(neighbour => {
        const neighbourData = nodeDataById.get(neighbour);
        return [...(neighbourData?.runtime_namespaces || []), ...(neighbourData?.fact_namespaces || [])];
      });
      return neighbourNamespaces[0] || "root";
    }
    function ownerCandidatesForNode(node) {
      const data = nodeDataById.get(node);
      if (data?.kind === "microservice") return [node];
      if (data?.owner_service) {
        const ownerId = `microservice:${data.owner_service}`;
        if (nodeDataById.get(ownerId)) return [ownerId];
      }
      return [
        node,
        ...graphData.links
          .filter(link => link.target === node)
          .map(link => link.source)
          .filter(candidate => nodeDataById.get(candidate)?.kind === "microservice"),
      ];
    }
    function preferredOwnerForNode(node) {
      const layerOrder = ["api", "application", "orchestration", "infrastructure", "domain", "persistence", "external"];
      const layerRank = new Map(layerOrder.map((layer, index) => [layer, index]));
      return ownerCandidatesForNode(node)
        .map(candidate => nodeDataById.get(candidate))
        .filter(candidate => candidate?.kind === "microservice")
        .sort((left, right) => (
          (layerRank.get(right.layer) ?? -1) - (layerRank.get(left.layer) ?? -1)
          || left.name.localeCompare(right.name)
        ))[0];
    }
    function architectureLayerForNode(node) {
      const data = nodeDataById.get(node);
      if (data?.layer_label || data?.layer) return data.layer_label || data.layer;
      const owner = preferredOwnerForNode(node);
      return owner?.layer_label || owner?.layer || "Unknown";
    }
    function layeredLayerForNode(node) {
      const layerOrder = ["api", "application", "orchestration", "infrastructure", "domain", "persistence", "external"];
      const data = nodeDataById.get(node);
      const declaredLayer = data?.architecture_layer || data?.layer_label;
      if (declaredLayer && layerOrder.includes(declaredLayer)) return declaredLayer;
      if (data?.kind === "microservice" && layerOrder.includes(data.layer)) return data.layer;
      const neighbourLayer = network.neighbors(node)
        .map(neighbour => nodeDataById.get(neighbour)?.layer)
        .find(layer => layerOrder.includes(layer));
      return neighbourLayer || "application";
    }
    function normalizeClusterPath(path) {
      const normalized = String(path || "root").replace(/^\/+|\/+$/g, "");
      return !normalized || normalized === "ROOT" ? "root" : normalized;
    }
    function visibleClusterPaths() {
      const paths = new Set(["root"]);
      network.nodes().filter(node => isVisibleNodeId(node)).forEach(node => {
        const path = normalizeClusterPath(namespaceForNode(node));
        if (path === "root") return;
        const segments = path.split("/").filter(Boolean);
        segments.forEach((_segment, index) => paths.add(segments.slice(0, index + 1).join("/")));
      });
      return paths;
    }
    function clusterDescriptorForPath(path) {
      const clusterPath = normalizeClusterPath(path);
      const exactPaths = new Map(network.nodes()
        .filter(node => isVisibleNodeId(node))
        .map(node => [node, normalizeClusterPath(namespaceForNode(node))]));
      const ids = [...exactPaths.entries()]
        .filter(([, nodePath]) => nodePath === clusterPath)
        .map(([id]) => id);
      const childPaths = new Set();
      visibleClusterPaths().forEach(candidate => {
        if (candidate === "root" || candidate === clusterPath) return;
        if (clusterPath === "root") {
          childPaths.add(candidate.split("/")[0]);
          return;
        }
        if (!candidate.startsWith(`${clusterPath}/`)) return;
        const nextSegment = candidate.slice(clusterPath.length + 1).split("/")[0];
        childPaths.add(`${clusterPath}/${nextSegment}`);
      });
      const parentPath = clusterPath === "root"
        ? null
        : clusterPath.includes("/")
          ? clusterPath.slice(0, clusterPath.lastIndexOf("/"))
          : "root";
      return {
        key: `cluster:${clusterPath}`,
        kind: "cluster",
        name: clusterPath === "root" ? "ROOT" : clusterPath,
        path: clusterPath,
        parentPath,
        childPaths: [...childPaths].sort((left, right) => left.localeCompare(right)),
        ids,
      };
    }
    function clusterPathForNode(node) {
      const data = nodeDataById.get(node);
      const owner = ownerCandidatesForNode(node)
        .map(candidate => nodeDataById.get(candidate))
        .find(candidate => candidate?.project_namespace_path || candidate?.project_namespace);
      const path = data?.project_namespace_path
        || data?.architecture_namespace_path
        || owner?.project_namespace_path
        || owner?.architecture_namespace_path
        || namespaceForNode(node);
