// Ordered source module: 30-layouts.js
      return path === "root" ? "ROOT" : path;
    }
    function setActiveLayout(layout) {
      layoutButtons.forEach((button, key) => {
        const active = key === layout;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-pressed", String(active));
      });
    }
    function resolveGraphCardOverlaps() {
      const cardScale = GRAPH_CARD_SCALE;
      const symbolMode = graphState.renderMode === "symbols";
      const cardWidth = (symbolMode ? 30 : 110) * cardScale;
      const cardHeight = (symbolMode ? 30 : 70) * cardScale;
      const gap = 4;
      const ids = network.nodes().filter(id => isVisibleNodeId(id));
      const points = new Map(ids.map(id => {
          const attributes = network.getNodeAttributes(id);
          const point = renderer.graphToViewport({ x: attributes.x, y: attributes.y });
          return [id, { x: point.x, y: point.y }];
        }));
      const cellWidth = cardWidth + gap;
      const cellHeight = cardHeight + gap;
      const separatePair = (left, right) => {
        const overlapX = cellWidth - Math.abs(left.x - right.x);
        const overlapY = cellHeight - Math.abs(left.y - right.y);
        if (overlapX <= 0 || overlapY <= 0) return false;
        const moveAlongX = overlapX <= overlapY;
        const direction = moveAlongX
          ? (left.x >= right.x ? 1 : -1)
          : (left.y >= right.y ? 1 : -1);
        if (moveAlongX) {
          left.x += direction * overlapX / 2;
          right.x -= direction * overlapX / 2;
        } else {
          left.y += direction * overlapY / 2;
          right.y -= direction * overlapY / 2;
        }
        return true;
      };
      // Small and medium architectures benefit from an exact pair pass. It
      // avoids the stale-cell problem of an in-place spatial hash when a card
      // crosses a bucket during the same pass. Keep the hash path for large
      // graphs so collision repair remains bounded instead of quadratic.
      for (let pass = 0; pass < (ids.length <= 400 ? 32 : 48); pass += 1) {
        if (ids.length <= 400) {
          let changed = false;
          for (let leftIndex = 0; leftIndex < ids.length; leftIndex += 1) {
            for (let rightIndex = leftIndex + 1; rightIndex < ids.length; rightIndex += 1) {
              changed = separatePair(points.get(ids[leftIndex]), points.get(ids[rightIndex])) || changed;
            }
          }
          if (!changed) break;
          continue;
        }
        const buckets = new Map();
        let changed = false;
        ids.forEach(id => {
          const point = points.get(id);
          if (!point) return;
          const cellX = Math.floor(point.x / cellWidth);
          const cellY = Math.floor(point.y / cellHeight);
          for (let offsetX = -1; offsetX <= 1; offsetX += 1) {
            for (let offsetY = -1; offsetY <= 1; offsetY += 1) {
              for (const otherId of buckets.get(`${cellX + offsetX}:${cellY + offsetY}`) || []) {
                const other = points.get(otherId);
                if (!other) continue;
                changed = separatePair(point, other) || changed;
              }
            }
          }
          const key = `${cellX}:${cellY}`;
          const bucket = buckets.get(key) || [];
          bucket.push(id);
          buckets.set(key, bucket);
        });
        if (!changed) break;
      }
      points.forEach((point, id) => {
        const graphPoint = renderer.viewportToGraph(point);
        network.mergeNodeAttributes(id, { x: graphPoint.x, y: graphPoint.y });
      });
    }

    async function applyLayout(layout) {
      if (!["forceatlas2", "noverlap", "forceatlas2-noverlap"].includes(layout)) {
        layout = "forceatlas2-noverlap";
      }
      const request = ++graphState.layoutRequest;
      const previousLayout = graphState.activeLayout;
      updateGraphState({
        activeLayout: layout,
        lastSafeCameraState: null,
        cameraFitAdjusting: false,
      });
      const label = layoutLabels.get(layout);
      if (graphState.viewMode === "call-graph") {
        updateGraphState({
          selectedId: null,
          dependencyFocusOnly: false,
          selectedClusterKey: null,
          hoveredId: null,
          relatedNodes: null,
          relatedEdges: null,
          selectedDiagnosticId: null,
          diagnosticFocusSnapshot: null,
          selectedCallGraphEdgeKey: null,
          pathMicroserviceOrder: new Map(),
          codeFlowTreeCoordinates: new Map(),
        });
        dependencyFocusOnly.checked = false;
        dependencyAnalysisControls.disabled = true;
        dependencyAnalysisHelp.textContent = "Sélectionnez un nœud pour activer cette analyse.";
        dependencyFocusOnly.disabled = true;
        clearPathControls();
        setDetailsEmpty("Sélectionnez un nœud pour afficher ses informations.");
      }
      const zoomOutButton = document.getElementById("graph-zoom-out");
      if (zoomOutButton) {
        zoomOutButton.disabled = false;
        zoomOutButton.title = "Dézoomer";
      }
      setActiveLayout(layout);
      layoutStatus.textContent = `Calcul de la disposition ${label}…`;
      const libraries = await layoutLibraries;
      if (request !== graphState.layoutRequest) return;
      if (libraries === null) {
        updateGraphState({ activeLayout: previousLayout });
        setActiveLayout(previousLayout);
        layoutStatus.textContent = "Les dispositions du graphe sont indisponibles ; la disposition initiale est conservée.";
        return;
      }
      try {
        restoreInitialNodePositions();
        if (layout === "forceatlas2" || layout === "forceatlas2-noverlap") {
          libraries.forceAtlas2.assign(network, {
            iterations: Math.min(320, Math.max(120, network.order * 4)),
            settings: {
              adjustSizes: true,
              barnesHutOptimize: network.order >= 30,
              barnesHutTheta: .7,
              gravity: .7,
              scalingRatio: 35,
              slowDown: 3,
            },
          });
        }
        if (["noverlap", "forceatlas2", "forceatlas2-noverlap"].includes(layout)) {
          libraries.noverlap.assign(network, {
            maxIterations: 240,
            settings: { expansion: 2.2, gridSize: 80, margin: 55, ratio: 1.5, speed: 2 },
          });
        }
      } catch (error) {
        console.error(`Impossible de calculer la disposition ${label}.`, error);
        updateGraphState({ activeLayout: previousLayout });
        setActiveLayout(previousLayout);
        restoreInitialNodePositions();
        renderer.refresh();
        layoutStatus.textContent = `La disposition ${label} a echoue ; la disposition initiale est restauree.`;
        return;
      }
      if (request !== graphState.layoutRequest) return;
      renderer.refresh();
      await fitCameraToVisibleGraph(renderer);
      await new Promise(resolve => setTimeout(resolve, 300));
      if (request !== graphState.layoutRequest) return;
      if (layout === "forceatlas2-noverlap" && graphState.fitMode === "readable") {
        resolveGraphCardOverlaps();
        renderer.refresh();
        await fitCameraToVisibleGraph(renderer);
      }
      if (codeFlowSelection.flowId && graphState.relatedNodes?.size) {
        centerCameraOnPath({ nodes: [...graphState.relatedNodes] });
      }
      requestGraphRender();
      layoutStatus.textContent = `${label} actif.`;
    }
    const nodesByNormalizedName = new Map();
