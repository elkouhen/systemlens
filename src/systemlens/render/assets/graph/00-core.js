// Ordered source module: 00-core.js
    const graphData = JSON.parse(document.getElementById("graph-data").textContent);
    // HTML cards are rendered in screen space. Layouts and collision envelopes
    // must use the same dimensions in every view; only their graph positions
    // change when the camera zooms or pans.
    const GRAPH_CARD_SCALE = 1;
    const GRAPH_CARD_WIDTH = 110 * GRAPH_CARD_SCALE;
    const GRAPH_CARD_HEIGHT = 70 * GRAPH_CARD_SCALE;
    const MODULE_PADDING_X = 24;
    const MODULE_PADDING_TOP = 42;
    const MODULE_PADDING_BOTTOM = 24;
    const MODULE_GAP = 8;
    const themeToggle = document.getElementById("theme-toggle");
    const themeStorageKey = "systemlens:graph-theme";
    const storedTheme = (() => {
      try { return localStorage.getItem(themeStorageKey); } catch (_error) { return null; }
    })();
    const preferredTheme = storedTheme === "light" || storedTheme === "dark" ? storedTheme
      : (window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark");
    document.documentElement.dataset.theme = preferredTheme;
    function updateThemeToggle() {
      const isDark = document.documentElement.dataset.theme === "dark";
      themeToggle.textContent = isDark ? "☼ Clair" : "☾ Sombre";
      themeToggle.title = isDark ? "Passer au thème clair" : "Passer au thème sombre";
      themeToggle.setAttribute("aria-label", themeToggle.title);
    }
    themeToggle.addEventListener("click", () => {
      const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = theme;
      try { localStorage.setItem(themeStorageKey, theme); } catch (_error) { /* optional preference */ }
      updateThemeToggle();
      const labelColor = { color: theme === "dark" ? "#dce8f7" : "#172033" };
      renderer?.setSetting("labelColor", labelColor);
      dependencyRenderer?.setSetting("labelColor", labelColor);
      renderer?.refresh(); dependencyRenderer?.refresh();
    });
    updateThemeToggle();
    const nodeDataById = new Map(graphData.nodes.map(node => [node.id, node]));
    const linkedNodeIds = new Set(graphData.links.flatMap(link => [link.source, link.target]));
    const isolatedNodeIds = new Set(
      graphData.nodes.filter(node => !linkedNodeIds.has(node.id)).map(node => node.id)
    );
    const nodeSuggestions = document.getElementById("node-suggestions");
    const graphLayersOverlay = document.getElementById("graph-layers");
    const graphGroupsOverlay = document.getElementById("graph-groups");
    const nodeLabelOverlay = document.getElementById("graph-node-labels");
    const showProjectGroups = document.getElementById("show-project-groups");
    const nodeKindLabel = node => {
      if (node.kind === "kafka_topic") return "Topic Kafka";
      if (node.kind === "mongodb_collection") return "Collection MongoDB";
      if (node.kind === "data_schema") {
        const technology = (node.technology || "").toLowerCase();
        return ["sql", "postgresql", "mysql", "mariadb"].includes(technology)
          ? "Table SQL" : "Schéma de données";
      }
      if (node.kind === "message_channel") return "Canal de messages";
      return node.external ? "Service externe" : "Microservice";
    };
    const nodeKindSuggestion = node => (
      nodeKindLabel(node)
    );
    graphData.nodes
      .slice()
      .sort((left, right) => left.name.localeCompare(right.name))
      .forEach(node => {
        const option = document.createElement("option");
        option.value = node.name;
        option.label = nodeKindSuggestion(node);
        nodeSuggestions.append(option);
      });
    const graphSummary = document.getElementById("graph-summary");
    const summaryCounts = {
      microservices: graphData.nodes.filter(node => node.kind === "microservice").length,
      channels: graphData.nodes.filter(node => ["kafka_topic", "message_channel"].includes(node.kind)).length,
      dataResources: graphData.nodes.filter(node => ["mongodb_collection", "data_schema"].includes(node.kind)).length,
    };
    const summaryItems = [
      `${summaryCounts.microservices} service${summaryCounts.microservices > 1 ? "s" : ""}`,
      `${summaryCounts.channels} ${summaryCounts.channels > 1 ? "canaux" : "canal"}`,
      `${summaryCounts.dataResources} ressource${summaryCounts.dataResources > 1 ? "s" : ""} de données`,
      `${graphData.links.length} relation${graphData.links.length > 1 ? "s" : ""}`,
      ...(isolatedNodeIds.size
        ? [`${isolatedNodeIds.size} ressource${isolatedNodeIds.size > 1 ? "s" : ""} isolée${isolatedNodeIds.size > 1 ? "s" : ""}`]
        : []),
    ];
    summaryItems.forEach(text => {
      const item = document.createElement("span");
      item.className = "graph-summary-item";
      item.textContent = text;
      graphSummary.append(item);
    });
    const RELATION_COLORS = Object.freeze({
      http: "#D55E00",
      kafkaPublish: "#009E73",
      kafkaConsume: "#0072B2",
      requestReply: "#7C3AED",
      mongodb: "#CC79A7",
      build: "#475569",
    });
    function relationColor(link) {
      if (link.kind === "rest") return RELATION_COLORS.http;
      if (link.kind === "build") return RELATION_COLORS.build;
      if (link.kind === "request_reply") return RELATION_COLORS.requestReply;
      if (link.direction === "incoming") return RELATION_COLORS.kafkaConsume;
      if (link.direction === "data_access") return RELATION_COLORS.mongodb;
      if (link.kind.startsWith("mcp_") && ["reads", "writes", "uses"].includes(link.label)) return RELATION_COLORS.mongodb;
      return RELATION_COLORS.kafkaPublish;
    }
    function dependencyGraphData() {
      return graphData.build_dependencies || { nodes: [], links: [] };
    }
    function buildHierarchyPositions(nodes, links) {
      // Sugiyama starts by condensing cycles. The resulting component graph is
      // acyclic and can therefore be assigned stable dependency layers.
      const adjacency = new Map(nodes.map(node => [node.id, []]));
      links.forEach(link => adjacency.get(link.source)?.push(link.target));
      const indexes = new Map(), lowlinks = new Map(), stack = [], onStack = new Set(), components = [];
      let nextIndex = 0;
      function visit(nodeId) {
        indexes.set(nodeId, nextIndex); lowlinks.set(nodeId, nextIndex); nextIndex += 1;
        stack.push(nodeId); onStack.add(nodeId);
        for (const targetId of adjacency.get(nodeId) || []) {
          if (!indexes.has(targetId)) {
            visit(targetId);
            lowlinks.set(nodeId, Math.min(lowlinks.get(nodeId), lowlinks.get(targetId)));
          } else if (onStack.has(targetId)) {
            lowlinks.set(nodeId, Math.min(lowlinks.get(nodeId), indexes.get(targetId)));
          }
        }
        if (lowlinks.get(nodeId) !== indexes.get(nodeId)) return;
        const component = [];
        for (;;) {
          const member = stack.pop(); onStack.delete(member); component.push(member);
          if (member === nodeId) break;
        }
        components.push(component.sort());
      }
      nodes.map(node => node.id).sort().forEach(nodeId => { if (!indexes.has(nodeId)) visit(nodeId); });
      const componentByNode = new Map();
      components.forEach((component, index) => component.forEach(nodeId => componentByNode.set(nodeId, index)));
      const successors = components.map(() => new Set());
      const indegrees = components.map(() => 0);
      links.forEach(link => {
        const source = componentByNode.get(link.source), target = componentByNode.get(link.target);
        if (source === target || successors[source].has(target)) return;
        successors[source].add(target); indegrees[target] += 1;
      });
      const levels = components.map(() => 0);
      const queue = components.map((_component, index) => index).filter(index => indegrees[index] === 0).sort((a, b) => a - b);
      for (let cursor = 0; cursor < queue.length; cursor += 1) {
        const component = queue[cursor];
        [...successors[component]].sort((a, b) => a - b).forEach(target => {
          levels[target] = Math.max(levels[target], levels[component] + 1);
          indegrees[target] -= 1;
          if (indegrees[target] === 0) queue.push(target);
        });
      }
      const layers = new Map();
      components.forEach((component, index) => {
        const level = levels[index];
        layers.set(level, [...(layers.get(level) || []), ...component]);
      });
      const positions = new Map();
      [...layers.entries()].sort(([left], [right]) => left - right).forEach(([level, nodeIds]) => {
        nodeIds.sort();
        const center = (nodeIds.length - 1) / 2;
        nodeIds.forEach((nodeId, row) => positions.set(nodeId, { x: level * 2.8, y: row - center }));
      });
      return positions;
    }
    let network;
    let renderer;
    let renderOverlays = null;
    let initialNodePositions = new Map();
    const graphState = {
      selectedId: null,
      selectedClusterKey: null,
      hoveredId: null,
      relatedNodes: null,
      relatedEdges: null,
      selectedCodeFlowId: null,
      pathMicroserviceOrder: new Map(),
      layeredView: false,
      clusteredView: false,
      layeredClusterView: false,
      clusterLayoutPositions: new Map(),
      cameraFitAdjusting: false,
      lastSafeCameraState: null,
      graphPanCleanup: null,
      graphWheelCleanup: null,
      activeLayout: "forceatlas2-noverlap",
      layoutRequest: 0,
      fitMode: "readable",
      fitRequest: 0,
      maximumCollisionFreeRatio: 100,
      renderMode: "cards",
    };
    function updateGraphState(patch) {
      Object.assign(graphState, patch);
      return graphState;
    }
    function requiredCardZoomIn(targetRenderer) {
      if (!targetRenderer || !network) return 1;
      const compoundView = ["cluster", "elk"].includes(graphState.activeLayout);
      const symbolMode = graphState.renderMode === "symbols";
      // Compound views must remain collision-free when the user switches
      // between Symbols and Cards without moving the camera. Reserve the
      // largest envelope even when symbols are currently rendered.
      const cardWidth = compoundView || !symbolMode ? GRAPH_CARD_WIDTH + 4 : 34;
      const cardHeight = compoundView || !symbolMode ? GRAPH_CARD_HEIGHT + 4 : 34;
      const buckets = new Map();
      const requiredZooms = [];
      network.forEachNode((id, attributes) => {
        if (!isVisibleNodeId(id) || attributes.hidden) return;
        const point = targetRenderer.graphToViewport({ x: attributes.x, y: attributes.y });
        const cellX = Math.floor(point.x / cardWidth);
        const cellY = Math.floor(point.y / cardHeight);
        for (let offsetX = -1; offsetX <= 1; offsetX += 1) {
          for (let offsetY = -1; offsetY <= 1; offsetY += 1) {
            for (const other of buckets.get(`${cellX + offsetX}:${cellY + offsetY}`) || []) {
              const distanceX = Math.abs(point.x - other.x);
              const distanceY = Math.abs(point.y - other.y);
              if (distanceX >= cardWidth || distanceY >= cardHeight) continue;
              // Uniform zoom separates the cards as soon as either axis has
              // enough room. Taking the larger axis factor would over-zoom
              // every pair that is nearly aligned horizontally or vertically.
              const requiredZoom = Math.min(
                cardWidth / Math.max(distanceX, 1),
                cardHeight / Math.max(distanceY, 1),
              );
              requiredZooms.push(compoundView ? requiredZoom : Math.min(4, requiredZoom));
            }
          }
        }
        const key = `${cellX}:${cellY}`;
        const bucket = buckets.get(key) || [];
        bucket.push(point);
        buckets.set(key, bucket);
      });
      if (compoundView) {
        const groupedPoints = new Map();
        network.forEachNode((id, attributes) => {
          if (!isVisibleNodeId(id) || attributes.hidden) return;
          const modulePath = namespaceForNode(id);
          const layer = graphState.activeLayout === "elk" ? layeredLayerForNode(id) : "modules";
          const key = `${layer}:${modulePath}`;
          const point = targetRenderer.graphToViewport({ x: attributes.x, y: attributes.y });
          const points = groupedPoints.get(key) || [];
          points.push(point);
          groupedPoints.set(key, points);
        });
        const bounds = [...groupedPoints.values()].map(points => ({
          minX: Math.min(...points.map(point => point.x)),
          maxX: Math.max(...points.map(point => point.x)),
          minY: Math.min(...points.map(point => point.y)),
          maxY: Math.max(...points.map(point => point.y)),
        }));
        const requiredGapX = GRAPH_CARD_WIDTH + 2 * MODULE_PADDING_X + MODULE_GAP;
        const requiredGapY = GRAPH_CARD_HEIGHT + MODULE_PADDING_TOP
          + MODULE_PADDING_BOTTOM + MODULE_GAP;
        for (let leftIndex = 0; leftIndex < bounds.length; leftIndex += 1) {
          for (let rightIndex = leftIndex + 1; rightIndex < bounds.length; rightIndex += 1) {
            const left = bounds[leftIndex];
            const right = bounds[rightIndex];
            const gapX = Math.max(left.minX - right.maxX, right.minX - left.maxX, 0);
            const gapY = Math.max(left.minY - right.maxY, right.minY - left.maxY, 0);
            const zoomX = gapX > 0 ? requiredGapX / gapX : Number.POSITIVE_INFINITY;
            const zoomY = gapY > 0 ? requiredGapY / gapY : Number.POSITIVE_INFINITY;
            const required = Math.min(zoomX, zoomY);
            if (Number.isFinite(required)) requiredZooms.push(required);
          }
        }
      }
      if (!requiredZooms.length) return 1;
      requiredZooms.sort((left, right) => left - right);
      if (["forceatlas2-noverlap", "cluster", "elk"].includes(graphState.activeLayout)) {
        return requiredZooms[requiredZooms.length - 1];
      }
      // A single near-coincident pair must not dictate the camera distance for
      // the entire graph. Separating 90% of the initially overlapping pairs
      // gives a stable readable view while leaving local outliers explorable.
      return requiredZooms[Math.floor((requiredZooms.length - 1) * .9)];
    }
    function requiredSmallGraphOverviewRatio(targetRenderer) {
      if (!targetRenderer || !network?.order) return 1;
      const points = network.nodes()
        .filter(id => isVisibleNodeId(id) && !network.getNodeAttribute(id, "hidden"))
        .map(id => {
          const attributes = network.getNodeAttributes(id);
          return targetRenderer.graphToViewport({ x: attributes.x, y: attributes.y });
        });
      if (points.length < 2) return 1;
      const viewport = graphCanvas.getBoundingClientRect();
      const cardWidth = graphState.renderMode === "symbols" ? 34 : GRAPH_CARD_WIDTH + 8;
      const cardHeight = graphState.renderMode === "symbols" ? 34 : GRAPH_CARD_HEIGHT + 8;
      const spanX = Math.max(...points.map(point => point.x)) - Math.min(...points.map(point => point.x));
      const spanY = Math.max(...points.map(point => point.y)) - Math.min(...points.map(point => point.y));
      const availableWidth = Math.max(1, viewport.width - cardWidth);
      const availableHeight = Math.max(1, viewport.height - cardHeight);
      return Math.min(2, Math.max(1, spanX / availableWidth, spanY / availableHeight));
    }
    let renderFrameScheduled = false;
    let renderFramePromise = Promise.resolve();
    function requestGraphRender() {
      if (renderFrameScheduled) return renderFramePromise;
      renderFrameScheduled = true;
      renderFramePromise = new Promise(resolve => {
        requestAnimationFrame(() => {
          renderFrameScheduled = false;
          try {
            renderOverlays?.();
          } finally {
            resolve();
          }
        });
      });
      return renderFramePromise;
    }
    const layoutLibraries = Promise.all([
      import("https://esm.sh/graphology-layout-forceatlas2@0.10.1"),
      import("https://esm.sh/graphology-layout-noverlap@0.4.2"),
    ]).then(([forceAtlas2Module, noverlapModule]) => ({
      forceAtlas2: forceAtlas2Module.default,
      noverlap: noverlapModule.default,
      elk: typeof window.ELK === "function" ? new window.ELK() : null,
    })).catch(error => {
      console.warn("Impossible de charger les dispositions du graphe.", error);
      return null;
    });

    // Sigma invokes reducers while it is constructed, so these controls must
    // exist before creating the renderer.
    const relationHttp = document.getElementById("relation-http");
    const relationKafka = document.getElementById("relation-kafka");
    const relationMongodb = document.getElementById("relation-mongodb");
    const relationOther = document.getElementById("relation-other");
    const nodeMicroservice = document.getElementById("node-microservice");
    const nodeExternalMicroservice = document.getElementById("node-external-microservice");
    const nodeKafkaTopic = document.getElementById("node-kafka-topic");
    const nodeMongodbCollection = document.getElementById("node-mongodb-collection");
    const nodeOther = document.getElementById("node-other");
    function relationCategory(link, source = link.source, target = link.target) {
      const kind = String(link.kind || "").toLocaleLowerCase();
      const label = String(link.label || "").toLocaleLowerCase();
      const endpointKinds = [source, target].map(id => nodeDataById.get(id)?.kind);
      if (kind === "rest" || ["calls", "call", "invokes", "http"].includes(label)) return "http";
      if (["kafka", "request_reply"].includes(kind)
          || endpointKinds.some(value => ["kafka_topic", "message_channel"].includes(value))
          || ["publishes", "consumes", "produce", "consume"].includes(label)) return "kafka";
      if (kind === "mongodb" || link.direction === "data_access"
          || endpointKinds.some(value => ["mongodb_collection", "data_schema"].includes(value))
          || ["reads", "writes", "uses"].includes(label)) return "mongodb";
      return "other";
    }
    function isVisibleRelation(link, source = link.source, target = link.target) {
      return {
        http: relationHttp.checked,
        kafka: relationKafka.checked,
        mongodb: relationMongodb.checked,
        other: relationOther.checked,
      }[relationCategory(link, source, target)];
    }
    function isVisibleNode(node) {
      if (!node) return false;
      if (node.kind === "microservice") {
        return (node.external ? nodeExternalMicroservice.checked : nodeMicroservice.checked)
          ;
      }
      if (["kafka_topic", "message_channel"].includes(node.kind)) return nodeKafkaTopic.checked;
      if (["mongodb_collection", "data_schema"].includes(node.kind)) return nodeMongodbCollection.checked;
      return nodeOther.checked;
    }
    function isVisibleNodeId(id) { return isVisibleNode(nodeDataById.get(id)); }
    const NODE_VERTEX_SHADER = `
      attribute vec2 a_position;
      attribute float a_size;
      attribute vec4 a_color;
      uniform float u_ratio;
      uniform float u_scale;
      uniform mat3 u_matrix;
      varying vec4 v_color;
      void main() {
        gl_Position = vec4((u_matrix * vec3(a_position, 1.0)).xy, 0.0, 1.0);
        gl_PointSize = a_size * u_ratio * u_scale * 2.0;
        v_color = a_color;
      }
    `;
    const MICROSERVICE_FRAGMENT_SHADER = `
      precision mediump float;
      varying vec4 v_color;
      void main() {
        vec2 point = gl_PointCoord - vec2(.5);
        vec2 bounds = vec2(.46, .34);
        vec2 corner = abs(point) - (bounds - .06);
        float distance = length(max(corner, 0.0)) + min(max(corner.x, corner.y), 0.0) - .06;
        float alpha = 1.0 - smoothstep(-.014, .014, distance);
        if (alpha < .01) discard;
        float border = smoothstep(-.02, .035, distance);
        vec3 fill = mix(vec3(.98, .99, 1.0), v_color.rgb, .18);
        vec3 card = mix(fill, v_color.rgb, border * .82 + .18);
        float icon = max(abs(point.x - .31), abs(point.y - .21)) - .035;
        card = mix(card, v_color.rgb, 1.0 - smoothstep(-.01, .01, icon));
        gl_FragColor = vec4(card, v_color.a * alpha);
      }
    `;
    const EXTERNAL_MICROSERVICE_FRAGMENT_SHADER = `
      precision mediump float;
      varying vec4 v_color;
      void main() {
        vec2 point = gl_PointCoord - vec2(.5);
        vec2 bounds = vec2(.46, .34);
        vec2 corner = abs(point) - (bounds - .06);
        float distance = length(max(corner, 0.0)) + min(max(corner.x, corner.y), 0.0) - .06;
        float alpha = 1.0 - smoothstep(-.014, .014, distance);
        if (alpha < .01) discard;
        float border = smoothstep(-.02, .035, distance);
        vec3 fill = mix(vec3(.98, .99, 1.0), v_color.rgb, .18);
        vec3 card = mix(fill, v_color.rgb, border * .82 + .18);
        float icon = max(abs(point.x - .31), abs(point.y - .21)) - .035;
        card = mix(card, v_color.rgb, 1.0 - smoothstep(-.01, .01, icon));
        gl_FragColor = vec4(card, v_color.a * alpha);
      }
    `;
    const KAFKA_TOPIC_FRAGMENT_SHADER = `
      precision mediump float;
      varying vec4 v_color;
      void main() {
        vec2 point = gl_PointCoord - vec2(.5);
        vec2 bounds = vec2(.46, .34);
        vec2 corner = abs(point) - (bounds - .06);
        float distance = length(max(corner, 0.0)) + min(max(corner.x, corner.y), 0.0) - .06;
        float alpha = 1.0 - smoothstep(-.014, .014, distance);
        if (alpha < .01) discard;
        float border = smoothstep(-.02, .035, distance);
        vec3 fill = vec3(.98, .99, 1.0);
        vec3 card = mix(fill, v_color.rgb, border);
        float bar1 = max(abs(point.x - .22) - .012, abs(point.y - .31) - .05);
        float bar2 = max(abs(point.x - .19) - .012, abs(point.y - .31) - .05);
        float bar3 = max(abs(point.x - .16) - .012, abs(point.y - .31) - .05);
        float icon = min(bar1, min(bar2, bar3));
        card = mix(card, v_color.rgb, 1.0 - smoothstep(-.01, .01, icon));
        gl_FragColor = vec4(card, v_color.a * alpha);
      }
    `;
    const MONGODB_COLLECTION_FRAGMENT_SHADER = `
      precision mediump float;
      varying vec4 v_color;
      void main() {
        vec2 point = gl_PointCoord - vec2(.5);
        vec2 bounds = vec2(.46, .34);
        float radius = .06;
        vec2 corner = abs(point) - (bounds - radius);
        float distance = length(max(corner, 0.0)) + min(max(corner.x, corner.y), 0.0) - radius;
        float alpha = 1.0 - smoothstep(-.014, .014, distance);
        if (alpha < .01) discard;
        float border = smoothstep(-.02, .035, distance);
        vec3 fill = vec3(.98, .99, 1.0);
        vec3 card = mix(fill, v_color.rgb, border);
        float disk = length(vec2(point.x - .31, point.y - .22)) - .045;
        card = mix(card, v_color.rgb, 1.0 - smoothstep(-.01, .01, disk));
        gl_FragColor = vec4(card, v_color.a * alpha);
      }
    `;
    const packedColorBuffer = new ArrayBuffer(4);
    const packedColorBytes = new Uint8Array(packedColorBuffer);
    const packedColorFloat = new Float32Array(packedColorBuffer);
    function packColor(color) {
      color = typeof color === "string" && color ? color : "#94a3b8";
      const value = color.startsWith("#") ? color.slice(1) : color;
      packedColorBytes[0] = parseInt(value.slice(0, 2), 16) || 0;
      packedColorBytes[1] = parseInt(value.slice(2, 4), 16) || 0;
      packedColorBytes[2] = parseInt(value.slice(4, 6), 16) || 0;
      packedColorBytes[3] = 254;
      return packedColorFloat[0];
    }
    function compileShader(gl, type, source) {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        throw new Error(`Impossible de compiler le shader WebGL: ${gl.getShaderInfoLog(shader)}`);
      }
      return shader;
    }
    function createNodeProgram(fragmentShader) {
      return class ShapeNodeProgram {
        constructor(gl) {
          this.gl = gl;
          this.array = new Float32Array();
          this.buffer = gl.createBuffer();
          const vertexShader = compileShader(gl, gl.VERTEX_SHADER, NODE_VERTEX_SHADER);
          const pixelShader = compileShader(gl, gl.FRAGMENT_SHADER, fragmentShader);
          this.program = gl.createProgram();
          gl.attachShader(this.program, vertexShader);
          gl.attachShader(this.program, pixelShader);
          gl.linkProgram(this.program);
          if (!gl.getProgramParameter(this.program, gl.LINK_STATUS)) {
            throw new Error(`Impossible d'associer le shader WebGL: ${gl.getProgramInfoLog(this.program)}`);
          }
          this.positionLocation = gl.getAttribLocation(this.program, "a_position");
          this.sizeLocation = gl.getAttribLocation(this.program, "a_size");
          this.colorLocation = gl.getAttribLocation(this.program, "a_color");
          this.matrixLocation = gl.getUniformLocation(this.program, "u_matrix");
          this.ratioLocation = gl.getUniformLocation(this.program, "u_ratio");
          this.scaleLocation = gl.getUniformLocation(this.program, "u_scale");
          this.bind();
        }
        allocate(capacity) { this.array = new Float32Array(capacity * 4); }
        process(data, hidden, offset) {
          const index = offset * 4;
          if (hidden) {
            this.array.fill(0, index, index + 4);
            return;
          }
          this.array[index] = data.x;
          this.array[index + 1] = data.y;
          this.array[index + 2] = data.size;
          this.array[index + 3] = packColor(data.color);
        }
        bind() {
          const gl = this.gl;
          gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
          gl.enableVertexAttribArray(this.positionLocation);
          gl.enableVertexAttribArray(this.sizeLocation);
          gl.enableVertexAttribArray(this.colorLocation);
          gl.vertexAttribPointer(this.positionLocation, 2, gl.FLOAT, false, 16, 0);
          gl.vertexAttribPointer(this.sizeLocation, 1, gl.FLOAT, false, 16, 8);
          gl.vertexAttribPointer(this.colorLocation, 4, gl.UNSIGNED_BYTE, true, 16, 12);
        }
        bufferData() { this.gl.bufferData(this.gl.ARRAY_BUFFER, this.array, this.gl.DYNAMIC_DRAW); }
        render(params) {
          if (!this.array.length) return;
          const gl = this.gl;
          gl.useProgram(this.program);
          gl.uniform1f(this.ratioLocation, 1 / Math.sqrt(params.ratio));
          gl.uniform1f(this.scaleLocation, params.scalingRatio);
          gl.uniformMatrix3fv(this.matrixLocation, false, params.matrix);
          gl.drawArrays(gl.POINTS, 0, this.array.length / 4);
        }
      };
    }
