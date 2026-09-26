// Ordered source module: 20-controls.js
    const details = document.getElementById("details");
    const quickSearch = document.getElementById("quick-search");
    const graphContext = document.getElementById("graph-context");
    const search = document.getElementById("search");
    const resetButton = document.getElementById("reset");
    const searchStatus = document.getElementById("search-status");
    const pathLock = document.getElementById("path-lock");
    const graphTab = document.getElementById("graph-tab");
    const resourcesTab = document.getElementById("resources-tab");
    const openApiTab = document.getElementById("openapi-tab");
    const kafkaTab = document.getElementById("kafka-tab");
    const persistenceTab = document.getElementById("persistence-tab");
    const issuesTab = document.getElementById("issues-tab");
    const flowsTab = document.getElementById("flows-tab");
    const graphLegend = document.getElementById("graph-legend");
    const dependencyDepth = document.getElementById("dependency-depth");
    const graphPanel = document.getElementById("graph-panel");
    const resourcesPanel = document.getElementById("resources-panel");
    const issuesPanel = document.getElementById("issues-panel");
    const flowsPanel = document.getElementById("flows-panel");
    const openApiPanel = document.getElementById("openapi-panel");
    const kafkaPanel = document.getElementById("kafka-panel");
    const persistencePanel = document.getElementById("persistence-panel");
    const graphCanvas = document.getElementById("graph");
    const indexingIssuesList = document.getElementById("indexing-issues");
    const indexingIssuesEmpty = document.getElementById("indexing-issues-empty");
    const indexingIssuesTitle = document.getElementById("indexing-issues-title");
    const indexingIssues = graphData.indexing_issues || [];
    const inventoryStatus = document.getElementById("inventory-status");
    const progressNotice = document.getElementById("progress-notice");
    const resourcesList = document.getElementById("resources-list");
    const resourcesEmpty = document.getElementById("resources-empty");
    const resourcesFilter = document.getElementById("resources-filter");
    const resourcesTitle = document.getElementById("resources-title");
    const openApiReferencesList = document.getElementById("openapi-references");
    const openApiReferencesEmpty = document.getElementById("openapi-references-empty");
    const openApiReferencesFilter = document.getElementById("openapi-reference-filter");
    const dtoReferencesList = document.getElementById("dto-references");
    const dtoReferencesEmpty = document.getElementById("dto-references-empty");
    const dtoReferencesFilter = document.getElementById("dto-reference-filter");
    const openapiReferencesTitle = document.getElementById("openapi-references-title");
    const dtoReferencesTitle = document.getElementById("dto-references-title");
    const asyncApiReferencesList = document.getElementById("asyncapi-references");
    const asyncApiReferencesEmpty = document.getElementById("asyncapi-references-empty");
    const asyncApiReferencesTitle = document.getElementById("asyncapi-references-title");
    const mongoClassReferencesList = document.getElementById("mongo-class-references");
    const mongoClassReferencesEmpty = document.getElementById("mongo-class-references-empty");
    const mongoClassReferencesFilter = document.getElementById("mongo-class-reference-filter");
    const mongoClassReferencesTitle = document.getElementById("mongo-class-references-title");
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
