// Ordered source module: 55-code-flows.js
    const codeFlows = Array.isArray(graphData.code_flows) ? graphData.code_flows : [];
    const codeFlowsList = document.getElementById("code-flows");
    const codeFlowsEmpty = document.getElementById("code-flows-empty");
    const codeFlowFilter = document.getElementById("code-flow-filter");
    const codeFlowConfidence = document.getElementById("code-flow-confidence");
    const codeFlowKind = document.getElementById("code-flow-kind");
    const codeFlowMessageType = document.getElementById("code-flow-message-type");
    const codeFlowMessageTypes = document.getElementById("code-flow-message-types");
    const codeFlowsSummary = document.getElementById("code-flows-summary");
    const codeFlowFilterSummary = document.getElementById("code-flow-filter-summary");
    const codeFlowFilterReset = document.getElementById("code-flow-filter-reset");
    const codeFlowsTitle = document.getElementById("code-flows-title");
    const flowDiagnostics = Array.isArray(graphData.flow_diagnostics) ? graphData.flow_diagnostics : [];
    let callTreePan = null;
    let callTreePanBound = false;
    let callTreeRenderSignature = "";

    function updateCallTreeStats(nodeCount = 0, edgeCount = 0) {
      graphState.callTreeVisibleNodeCount = nodeCount;
      graphState.callTreeVisibleEdgeCount = edgeCount;
      const stats = document.getElementById("flow-mode-context-stats");
      const diagnostics = document.getElementById("flow-mode-context-diagnostics");
      const active = Boolean(codeFlowSelection.flowId)
        && (graphState.viewMode === "call-graph" || graphState.callGraphDisplayMode === "tree");
      if (stats) {
        stats.hidden = !active;
        stats.textContent = active
          ? `Arbre : ${nodeCount} nœud${nodeCount === 1 ? "" : "s"} · ${edgeCount} arc${edgeCount === 1 ? "" : "s"}`
          : "";
      }
      if (diagnostics) {
        const selected = active
          ? flowDiagnostics.filter(item => item.flow_id === codeFlowSelection.flowId)
          : [];
        const risks = selected.filter(item => item.classification === "potential_risk").length;
        const uncertain = selected.filter(item => item.classification === "insufficient_evidence").length;
        const guarantees = selected.filter(item => item.classification === "guarantee_identified").length;
        diagnostics.hidden = !active;
        diagnostics.textContent = !active ? ""
          : selected.length === 0 ? "Cohérence : aucun diagnostic détecté"
            : `Cohérence : ${risks} risque${risks === 1 ? "" : "s"} · ${uncertain} analyse${uncertain === 1 ? "" : "s"} incertaine${uncertain === 1 ? "" : "s"} · ${guarantees} garantie${guarantees === 1 ? "" : "s"}`;
      }
    }
    function codeFlowStepLabel(kind) {
      return ({
        http_entry: "Entrée HTTP",
        message_entry: "Entrée message",
        cron_entry: "Déclencheur Cron",
        http_call: "Appel HTTP",
        method_call: "Appel de méthode",
        message_publish: "Publication message",
        data_read: "Lecture de Data",
        data_write: "Écriture de Data",
      })[kind] || String(kind || "Étape").replaceAll("_", " ");
    }

    function dataEntityLabel(resource) {
      return String(resource || "donnée inconnue").split(":").at(-1).split(".").at(-1);
    }

    function flowDiagnosticTitle(diagnostic) {
      return ({
        potential_concurrent_read_write: "LECTURE / ÉCRITURE · INTERACTION POSSIBLE",
        potential_concurrent_write: "ÉCRITURES CONCURRENTES POSSIBLES",
        potential_lost_update: "LOST UPDATE POSSIBLE",
        potential_dirty_read: "LECTURE NON VALIDÉE POSSIBLE",
        potential_read_write_anomaly: "ANOMALIE DE FRAÎCHEUR POSSIBLE",
        conflict_protection_identified: "PROTECTION DE CONFLIT IDENTIFIÉE",
      })[diagnostic.event_type] || "ÉCRITURE ASYNC / LECTURE SYNC";
    }

    function codeFlowPriority(flow) {
      return flow.status === "cycle" ? 1 : 0;
    }

    function serviceIdsForCodeFlow(flow) {
      const callGraphOrder = callGraphForFlow(flow)?.node_order;
      if (Array.isArray(callGraphOrder) && callGraphOrder.length) {
        const callGraphServices = callGraphOrder
          .map(service => nodeIdForCodeFlowResource(service, "microservice"))
          .filter(Boolean);
        if (callGraphServices.length) return new Set(callGraphServices);
      }
      return new Set(
        [...new Set((flow.steps || [])
          .map(step => step.endpoint_id)
          .filter(Boolean))]
          .flatMap(endpointId => nodeIdsByEndpoint.get(endpointId) || [])
          .filter(nodeId => nodeDataById.get(nodeId)?.kind === "microservice")
      );
    }

    function callGraphArcCount(flow) {
      return callGraphForFlow(flow)?.edges?.length || 0;
    }

    function codeFlowProtocols(flow) {
      const protocols = new Set();
      (flow.steps || []).forEach(step => {
        if (/message|kafka|topic/i.test(`${step.kind} ${step.name} ${step.path}`)) protocols.add("kafka");
        if (/http|rest|api/i.test(`${step.kind} ${step.name} ${step.path}`)) protocols.add("http");
      });
      return protocols;
    }

    function codeFlowStats(flow) {
      const steps = flow.steps || [];
      return [
        ["Microservices", servicesForCodeFlow(flow).length],
        ["Appels", callGraphArcCount(flow)],
        ["Étapes", steps.length],
        ["Effets", steps.filter(step => ["message_publish", "http_call", "data_read", "data_write"].includes(step.kind)).length],
      ];
    }

    function codeFlowDescription(flow) {
      const aiDescription = graphData.flow_descriptions?.[flow.id];
      if (aiDescription) return aiDescription;
      const steps = flow.steps || [];
      const trigger = steps[0];
      const effect = [...steps].reverse().find(step => (
        ["http_call", "message_publish", "data_read", "data_write"].includes(step.kind)
      ));
      if (!trigger || !effect) return flow.reason || "Flux potentiel détecté à partir des preuves indexées.";
      const triggerVerb = trigger.kind === "message_entry"
        ? "consomme"
        : trigger.kind === "cron_entry"
          ? "est déclenché par"
          : "reçoit";
      const dataVerb = ({
        read: "lire dans",
        insert: "insérer dans",
        update: "modifier dans",
        delete: "supprimer dans",
        create_or_update: "créer ou modifier dans",
        read_update: "lire et modifier dans",
      })[effect.action];
      const effectVerb = effect.kind === "http_call"
        ? "appeler"
        : effect.kind === "message_publish"
          ? "publier"
          : dataVerb || (effect.kind === "data_write"
            ? "écrire dans"
            : "lire");
      return `Dans ${flow.module}, le flux potentiel ${triggerVerb} ${trigger.name} peut ${effectVerb} ${effect.name}.`;
    }

    function sortCodeFlows(left, right) {
      return codeFlowPriority(left) - codeFlowPriority(right)
        || serviceIdsForCodeFlow(right).size - serviceIdsForCodeFlow(left).size
        || callGraphArcCount(right) - callGraphArcCount(left)
        || (right.steps?.length || 0) - (left.steps?.length || 0)
        || left.id.localeCompare(right.id);
    }

    function activeCodeFlowFilters() {
      const filters = [];
      if (codeFlowFilter.value.trim()) filters.push("recherche");
      return filters;
    }

    function updateCodeFlowFilterSummary() {
      const filters = activeCodeFlowFilters();
      codeFlowFilterSummary.textContent = filters.length
        ? `${filters.length} filtre${filters.length > 1 ? "s" : ""} actif${filters.length > 1 ? "s" : ""} · ${filters.join(" · ")}`
        : "Aucun filtre additionnel";
    }

    function resetCodeFlowFilters() {
      codeFlowFilter.value = "";
      renderCodeFlows();
    }

    // Build these once: rendering the Flux list must not repeatedly scan the
    // complete topology for every flow card.  Endpoint identities are the
    // persisted evidence; names and route labels are presentation only.
    const nodeIdsByResource = new Map();
    const nodeIdsByEndpoint = new Map();
    graphData.nodes.forEach(node => {
      const resourceKey = `${node.kind}:${node.name}`;
      nodeIdsByResource.set(resourceKey, [...(nodeIdsByResource.get(resourceKey) || []), node.id]);
      (node.ports || []).forEach(port => {
        if (!port.endpoint_id) return;
        nodeIdsByEndpoint.set(port.endpoint_id, [...(nodeIdsByEndpoint.get(port.endpoint_id) || []), node.id]);
      });
    });
    const messageTypes = [...new Set(graphData.nodes.flatMap(node => (
      (node.ports || []).map(port => port.message_type).filter(Boolean)
    )))].sort((left, right) => left.localeCompare(right));
    codeFlowMessageTypes?.replaceChildren(...messageTypes.map(messageType => {
      const option = document.createElement("option");
      option.value = messageType;
      return option;
    }));
    const portsByEndpointId = new Map(graphData.nodes.flatMap(node => (
      (node.ports || []).map(port => [port.endpoint_id, port])
    )));
    const messageTypesForCodeFlow = flow => new Set(
      (flow.steps || []).flatMap(step => {
        const port = portsByEndpointId.get(step.endpoint_id);
        return port?.message_type ? [port.message_type] : [];
      })
    );
    const graphLinksByEndpoint = new Map();
    graphData.links.forEach((link, index) => {
      (link.endpoint_ids || []).forEach(endpointId => {
        graphLinksByEndpoint.set(endpointId, [
          ...(graphLinksByEndpoint.get(endpointId) || []), { edge: `edge-${index}`, link },
        ]);
      });
    });
    const internalOutputsByInput = new Map();
    (graphData.internal_port_links || []).forEach(link => {
      internalOutputsByInput.set(link.input_endpoint_id, new Set([
        ...(internalOutputsByInput.get(link.input_endpoint_id) || []), link.output_endpoint_id,
      ]));
    });
    const uniqueNodeId = ids => ids?.length === 1 ? ids[0] : null;
    const nodeIdForCodeFlowResource = (name, kind) => uniqueNodeId(
      nodeIdsByResource.get(`${kind}:${name}`)
    );
    const nodeForCallTreeTopic = name => graphData.nodes.find(node => (
      ["kafka_topic", "message_channel"].includes(node.kind)
      && node.name === name
    ));
    const routeForCallTreeEdge = edge => {
      const target = graphData.nodes.find(node => (
        node.kind === "microservice" && node.name === edge.target
      ));
      const route = target?.http_routes?.find(item => item.route === edge.label);
      return target && route ? { target, route } : null;
    };
    const openCallTreeNodeInspector = node => {
      if (!node) return;
      renderDetails(node.id);
      openArchitectureNodeInspector(node, { reset: true });
      hideInlineDetailsAfterModal();
    };
    const openCallTreeEdgeInspector = edge => {
      if (edge.kind === "kafka") {
        openCallTreeNodeInspector(nodeForCallTreeTopic(edge.label));
        return;
      }
      if (edge.kind === "rest") {
        const route = routeForCallTreeEdge(edge);
        if (route) openHttpRouteInspector(route.target, route.route);
      }
    };
    const nodeIdForEndpoint = endpointId => uniqueNodeId(nodeIdsByEndpoint.get(endpointId));

    function captureCallTreeCamera() {
      const canvas = graphCallTreeOverlay?.querySelector(".graph-call-tree-canvas");
      if (!canvas) return null;
      const viewport = graphCallTreeOverlay.getBoundingClientRect();
      const baseScale = Number(canvas.dataset.baseScale) || 1;
      const totalScale = baseScale * (graphState.callTreeZoom || 1);
      if (!totalScale) return null;
      return {
        left: Number.parseFloat(canvas.style.left) || 0,
        top: Number.parseFloat(canvas.style.top) || 0,
        totalScale,
        centerX: viewport.width / 2,
        centerY: viewport.height / 2,
      };
    }

    function highlightCallTreePath(pathKey) {
      const overlay = graphCallTreeOverlay;
      if (!overlay) return;
      const isOnPath = candidate => Boolean(pathKey)
        && (candidate === pathKey || pathKey.startsWith(`${candidate}>`));
      overlay.querySelectorAll(".graph-call-tree-node").forEach(node => {
        const active = isOnPath(node.dataset.pathKey);
        node.classList.toggle("is-path-context", active);
        node.classList.toggle("is-path-muted", Boolean(pathKey) && !active);
      });
      overlay.querySelectorAll(".graph-call-tree-edge, .graph-call-tree-edge-label").forEach(edge => {
        const active = isOnPath(edge.dataset.targetPathKey);
        edge.classList.toggle("is-path-context", active);
        edge.classList.toggle("is-path-muted", Boolean(pathKey) && !active);
      });
    }

    function renderCallTreeOverlay(preservedCamera = null) {
      if (!graphCallTreeOverlay) return;
      if (!callTreePanBound) {
        callTreePanBound = true;
        const finishCallTreePan = event => {
          if (!callTreePan || event.pointerId !== callTreePan.pointerId) return;
          callTreePan = null;
          window.removeEventListener("pointermove", moveCallTreePan, true);
          window.removeEventListener("pointerup", finishCallTreePan, true);
          window.removeEventListener("pointercancel", finishCallTreePan, true);
          try { graphCallTreeOverlay.releasePointerCapture?.(event.pointerId); } catch (_error) { /* overlay was rebuilt during the gesture */ }
        };
        const moveCallTreePan = event => {
          if (!callTreePan || event.pointerId !== callTreePan.pointerId) return;
          const canvas = graphCallTreeOverlay.querySelector(".graph-call-tree-canvas");
          if (!canvas) return;
          canvas.style.left = `${callTreePan.startLeft + event.clientX - callTreePan.startX}px`;
          canvas.style.top = `${callTreePan.startTop + event.clientY - callTreePan.startY}px`;
        };
        graphCallTreeOverlay.addEventListener("pointerdown", event => {
          const interactiveEdge = event.target.closest?.(".graph-call-tree-edge-label.is-clickable")
            || event.target.classList?.contains("graph-call-tree-edge")
            || event.target.classList?.contains("graph-call-tree-edge-hit");
          if (event.button !== 0 || event.target.closest?.(".graph-call-tree-node") || interactiveEdge) return;
          const canvas = graphCallTreeOverlay.querySelector(".graph-call-tree-canvas");
          if (!canvas) return;
          callTreePan = {
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            startLeft: Number.parseFloat(canvas.style.left) || 0,
            startTop: Number.parseFloat(canvas.style.top) || 0,
          };
          event.preventDefault();
          event.stopPropagation();
          graphCallTreeOverlay.setPointerCapture?.(event.pointerId);
          window.addEventListener("pointermove", moveCallTreePan, true);
          window.addEventListener("pointerup", finishCallTreePan, true);
          window.addEventListener("pointercancel", finishCallTreePan, true);
        }, true);
        graphCallTreeOverlay.addEventListener("wheel", event => {
          if (!codeFlowSelection.flowId || graphState.callGraphDisplayMode !== "tree") return;
          event.preventDefault();
          const bounds = graphCallTreeOverlay.getBoundingClientRect();
          const delta = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
          const factor = Math.exp(Math.max(-120, Math.min(120, delta)) * -.0006);
          zoomCallTree(factor, event.clientX - bounds.left, event.clientY - bounds.top);
        }, { passive: false });
      }
      const active = Boolean(codeFlowSelection.flowId)
        && graphState.viewMode === "call-graph";
      const treeActive = active;
      const viewport = graphCallTreeOverlay.getBoundingClientRect();
      const treeSignature = treeActive
        ? [
            codeFlowSelection.flowId,
            graphState.callTreeDepth,
            graphState.selectedDiagnosticId || "",
            graphState.callTreeSelectedPathKey || "",
            [...graphState.callTreeExpanded].sort().join(","),
            [...graphState.callTreeCollapsed].sort().join(","),
            Math.round(viewport.width),
            Math.round(viewport.height),
          ].join("|")
        : "inactive";
      if (!preservedCamera && treeSignature === callTreeRenderSignature) return;
      callTreeRenderSignature = treeSignature;
      graphCallTreeOverlay.hidden = !treeActive;
      document.getElementById("graph")?.classList.toggle("is-call-tree-hidden", treeActive);
      [portPathOverlay, nodeLabelOverlay]
        .forEach(element => element?.classList.toggle("is-call-tree-hidden", treeActive));
      if (!treeActive) {
        graphCallTreeOverlay.replaceChildren();
        updateCallTreeStats();
        return;
      }
      const selectedFlows = (graphData.code_flows || []).filter(flow => (
        flow.id === codeFlowSelection.flowId
      ));
      const callGraph = callGraphForFlows(selectedFlows);
      const edges = (callGraph?.edges || []).filter(edge => edge.source !== edge.target);
      const selectedFlow = selectedFlows[0] || null;
      const selectedFlowDiagnostics = (graphData.flow_diagnostics || []).filter(item => (
        item.flow_id === codeFlowSelection.flowId
      ));
      const selectedDiagnostic = (graphData.flow_diagnostics || []).find(item => (
        item.id === graphState.selectedDiagnosticId
      )) || null;
      const selectedDiagnosticIndex = selectedDiagnostic
        ? (graphData.flow_diagnostics || [])
          .filter(item => item.flow_id === selectedDiagnostic.flow_id)
          .findIndex(item => item.id === selectedDiagnostic.id) + 1
        : 0;
      const asyncDiagnosticPathEndpoints = new Set(
        selectedDiagnostic?.async_path_endpoint_ids || selectedDiagnostic?.async_endpoint_ids || [],
      );
      const syncDiagnosticPathEndpoints = new Set(
        selectedDiagnostic?.sync_path_endpoint_ids || selectedDiagnostic?.sync_endpoint_ids || [],
      );
      const interactionDiagnosticPathEndpoints = new Set(
        selectedDiagnostic?.interaction_path_endpoint_ids || [],
      );
      const allCallFlows = [...codeFlows, ...(graphData.supporting_code_flows || [])];
      const flowById = new Map(allCallFlows.map(flow => [flow.id, flow]));
      const causalTransitions = callGraph?.call_tree?.transitions || {};
      const causalRootFlowIds = Array.isArray(callGraph?.call_tree?.root_flow_ids)
        ? callGraph.call_tree.root_flow_ids
        : [];
      const maxOccurrences = 5000;
      let occurrenceCount = 0;
      const maxDepth = Math.max(1, Math.min(8, Number(graphState.callTreeDepth) || 1));
      const makeOccurrence = (
        name,
        depth,
        ancestorInputPorts,
        inputEndpointId = null,
        pathKey = name,
        ancestorFlows = new Set(),
        flowIds = [],
      ) => {
        const currentFlowId = flowIds[0] || null;
        const currentFlow = currentFlowId ? flowById.get(currentFlowId) : null;
        const occurrence = {
          id: `call-tree-${occurrenceCount++}`,
          name,
          pathKey,
          depth,
          children: [],
          cycle: false,
          continuationUnknown: depth > 0 && !currentFlow,
          flowId: currentFlowId,
          flowIds,
          hiddenChildrenCount: 0,
          expanded: false,
          collapsed: false,
        };
        const endpointCycle = inputEndpointId && ancestorInputPorts.has(inputEndpointId);
        const flowCycle = flowIds.some(id => ancestorFlows.has(id));
        if (endpointCycle || flowCycle) {
          occurrence.cycle = true;
          return occurrence;
        }
        const byRelation = new Map();
        flowIds.forEach(sourceFlowId => {
          (causalTransitions[sourceFlowId] || []).forEach(transition => {
            const edge = transition.edge;
            const relationKey = [
              edge.kind,
              edge.endpoint_ids?.[0] || "",
              edge.label || "",
              edge.endpoint_ids?.[1] || "",
              edge.source || "",
              edge.target || "",
            ].join("|");
            const targetFlowIds = Array.isArray(transition.target_flow_ids)
              ? transition.target_flow_ids
              : [];
            const existing = byRelation.get(relationKey);
            if (existing) {
              existing.flowIds = [...new Set([...existing.flowIds, ...targetFlowIds])];
            } else {
              byRelation.set(relationKey, {
                edge,
                index: byRelation.size,
                flowIds: [...new Set(targetFlowIds)],
              });
            }
          });
        });
        const outgoing = [...byRelation.values()].map(relation => ({
          ...relation,
          flowId: relation.flowIds[0] || null,
        }));
        const expanded = graphState.callTreeExpanded.has(pathKey);
        const collapsed = graphState.callTreeCollapsed.has(pathKey);
        occurrence.expanded = expanded;
        occurrence.collapsed = collapsed;
        if (occurrenceCount >= maxOccurrences) {
          occurrence.hiddenChildrenCount = outgoing.length;
          return occurrence;
        }
        if (collapsed) {
          occurrence.hiddenChildrenCount = outgoing.length;
          return occurrence;
        }
        if (depth >= Math.min(8, maxDepth + (expanded ? 1 : 0))) {
          occurrence.hiddenChildrenCount = outgoing.length;
          return occurrence;
        }
        const nextInputPorts = new Set(ancestorInputPorts);
        if (inputEndpointId) nextInputPorts.add(inputEndpointId);
        const nextFlows = new Set(ancestorFlows);
        flowIds.forEach(id => nextFlows.add(id));
        outgoing.forEach(({ edge, index, flowId: childFlowId, flowIds: childFlowIds }) => {
          const childPathKey = `${pathKey}>${childFlowId || edge.target}:${edge.endpoint_ids?.[1] || edge.target}`;
          const child = makeOccurrence(
            edge.target,
            depth + 1,
            nextInputPorts,
            edge.endpoint_ids?.[1],
            childPathKey,
            nextFlows,
            childFlowIds,
          );
          if (edge) child.edge = edge;
          child.edgeIndex = index;
          occurrence.children.push(child);
        });
        return occurrence;
      };
      const rootsTree = causalRootFlowIds
        .map(flowId => flowById.get(flowId))
        .filter(Boolean)
        .map(rootFlow => makeOccurrence(
          rootFlow.module,
          0,
          new Set(),
          rootFlow.steps?.[0]?.endpoint_id || null,
          rootFlow.id,
          new Set(),
          [rootFlow.id],
        ));
      if (!rootsTree.length) {
        graphCallTreeOverlay.textContent = "Aucun appel interservice résolu.";
        updateCallTreeStats();
        return;
      }
      const treeTrigger = selectedFlow?.steps?.[0] || null;
      const treeTriggerRootName = rootsTree[0]?.name || null;
      const allOccurrences = [];
      const collect = occurrence => {
        allOccurrences.push(occurrence);
        occurrence.children.forEach(collect);
      };
      rootsTree.forEach(collect);
      const rootAccesses = (graphData.flow_data_access_flow_ids_by_root?.[codeFlowSelection.flowId] || [])
        .flatMap(id => graphData.flow_data_accesses?.[id] || []);
      const accessesByFlow = new Map();
      const accessesByInput = new Map();
      const accessesByHandler = new Map();
      const accessesByCron = new Map();
      const addAccess = (index, key, access) => {
        const matches = index.get(key) || [];
        matches.push(access);
        index.set(key, matches);
      };
      rootAccesses.forEach(access => {
        addAccess(accessesByFlow, access.flow_id, access);
        if (access.trigger_endpoint_id) {
          addAccess(accessesByInput, JSON.stringify([access.module, access.trigger_endpoint_id]), access);
          addAccess(accessesByHandler, JSON.stringify([
            access.module, access.trigger_endpoint_id, access.trigger_method,
          ]), access);
        } else {
          addAccess(accessesByCron, JSON.stringify([
            access.module, access.trigger_method, access.trigger_kind, access.trigger_name,
          ]), access);
        }
      });
      const inputForOccurrence = occurrence => occurrence.edge?.endpoint_ids?.[1]
        || (occurrence.depth === 0 ? selectedFlow?.steps?.[0]?.endpoint_id : null);
      const accessesForOccurrence = occurrence => {
        const inputEndpointId = inputForOccurrence(occurrence);
        const internalFlows = occurrence.flowIds.map(id => flowById.get(id)).filter(Boolean);
        const occurrenceCallPaths = internalFlows.map(flow => (
          (flow.steps || []).filter(step => step.kind === "method_call")
            .map(step => [step.name, step.path, step.start_line])
        ));
        const matches = new Map();
        const addMatch = (access, exactFlow) => {
          const accessPath = graphData.flow_data_access_call_paths?.[access.flow_id];
          const routeSupported = accessPath?.length > 0 && occurrenceCallPaths.some(path => (
            accessPath.length <= path.length && accessPath.every((call, index) => (
              JSON.stringify(call) === JSON.stringify(path[index])
            ))
          ));
          matches.set(access, Boolean(matches.get(access) || exactFlow || routeSupported));
        };
        occurrence.flowIds.forEach(id => (accessesByFlow.get(id) || []).forEach(access => {
          if (access.module === occurrence.name) addMatch(access, true);
        }));
        if (inputEndpointId && !internalFlows.length) {
          (accessesByInput.get(JSON.stringify([occurrence.name, inputEndpointId])) || [])
            .forEach(access => addMatch(access, false));
        } else if (inputEndpointId) {
          internalFlows.forEach(flow => (
            accessesByHandler.get(JSON.stringify([occurrence.name, inputEndpointId, flow.method])) || []
          ).forEach(access => addMatch(access, false)));
        } else {
          internalFlows.forEach(flow => (
            accessesByCron.get(JSON.stringify([
              occurrence.name, flow.method, flow.steps?.[0]?.kind, flow.steps?.[0]?.name,
            ])) || []
          ).forEach(access => addMatch(access, false)));
        }
        return [...matches].map(([access, routeSupported]) => ({
          ...access, handler_only: !routeSupported,
        }));
      };
      const diagnosticAnnotationsForOccurrence = occurrence => {
        const inputEndpointId = inputForOccurrence(occurrence);
        return selectedFlowDiagnostics.flatMap((diagnostic, index) => (
          ["write", "read"].flatMap(side => {
            const site = diagnostic[`${side}_site`];
            return inputEndpointId && site?.module === occurrence.name
              && site.trigger_endpoint_id === inputEndpointId
              ? [{ diagnostic, index, side }] : [];
          })
        ));
      };
      const crudLetters = (action, kind) => ({
        insert: "C",
        create: "C",
        read: "R",
        update: "U",
        delete: "D",
        read_update: "RU",
      })[action] || (kind === "data_read" ? "R" : "");
      const addCrudAction = (group, action, kind) => {
        if (action === "create_or_update") {
          group.createOrUpdate = true;
          return;
        }
        for (const letter of crudLetters(action, kind)) group.letters.add(letter);
      };
      const annotationGroupsByOccurrence = new Map();
      const annotationGroupsForOccurrence = occurrence => {
        if (annotationGroupsByOccurrence.has(occurrence)) {
          return annotationGroupsByOccurrence.get(occurrence);
        }
        const groups = new Map();
        const groupFor = resource => {
          const key = String(resource || "");
          if (!groups.has(key)) groups.set(key, {
            resource: key, name: dataEntityLabel(resource), letters: new Set(),
            createOrUpdate: false, handlerOnly: false, flowIds: new Set(),
            kinds: new Set(), annotations: [],
          });
          return groups.get(key);
        };
        accessesForOccurrence(occurrence).forEach(access => {
          const group = groupFor(access.resource);
          addCrudAction(group, access.action, access.kind);
          group.flowIds.add(access.flow_id);
          if (access.handler_only) group.handlerOnly = true;
        });
        diagnosticAnnotationsForOccurrence(occurrence).forEach(({ diagnostic, side }) => {
          const group = groupFor(diagnostic.resource);
          const kind = diagnostic.event_type === "async_write_sync_read"
            ? side === "write" ? "async" : "sync" : "interaction";
          addCrudAction(
            group, diagnostic[`${side}_action`], side === "read" ? "data_read" : "data_write",
          );
          group.kinds.add(kind);
          group.annotations.push({ diagnostic, side, kind });
        });
        annotationGroupsByOccurrence.set(occurrence, groups);
        return groups;
      };
      const visibleTreeEdgeCount = allOccurrences.reduce(
        (count, occurrence) => count + occurrence.children.length,
        0,
      );
      updateCallTreeStats(allOccurrences.length, visibleTreeEdgeCount);
      const occurrenceCounts = new Map();
      const nodeByOccurrence = new Map();
      allOccurrences.forEach(occurrence => {
        occurrenceCounts.set(occurrence.name, (occurrenceCounts.get(occurrence.name) || 0) + 1);
      });
      const edgeDisplayLabel = (edge, maxLength = 36) => {
        const protocol = edge.kind === "kafka" ? "Kafka" : edge.kind === "rest" ? "HTTP" : edge.kind || "Appel";
        const prefix = edge.order ? `#${edge.order} · ` : "";
        const suffix = ` · ${protocol}`;
        const label = String(edge.label || protocol);
        const labelWidth = Math.max(1, maxLength - prefix.length - suffix.length);
        const compactLabel = label.length > labelWidth
          ? `${label.slice(0, Math.max(0, labelWidth - 1))}…` : label;
        return `${prefix}${compactLabel}${suffix}`;
      };
      const direction = "lr";
      const cardWidth = 190;
      const siblingGap = 23;
      const maxEdgeLabelLength = allOccurrences.reduce((maximum, occurrence) => (
        occurrence.children.reduce((current, child) => (
          Math.max(current, edgeDisplayLabel(child.edge).length)
        ), maximum)
      ), 0);
      const estimatedEdgeLabelWidth = Math.min(320, maxEdgeLabelLength * 6);
      const gapX = Math.max(320, cardWidth + estimatedEdgeLabelWidth + 32);
      const canvas = document.createElement("div");
      canvas.className = "graph-call-tree-canvas";
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("aria-hidden", "true");
      const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
      const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
      marker.setAttribute("id", "graph-call-tree-arrow");
      marker.setAttribute("markerWidth", "8");
      marker.setAttribute("markerHeight", "8");
      marker.setAttribute("refX", "7");
      marker.setAttribute("refY", "4");
      marker.setAttribute("orient", "auto");
      const arrow = document.createElementNS("http://www.w3.org/2000/svg", "path");
      arrow.setAttribute("d", "M0,0 L8,4 L0,8 z");
      arrow.setAttribute("fill", "#7c3aed");
      marker.append(arrow);
      defs.append(marker);
      svg.append(defs);
      const portsByEndpointId = new Map(
        graphData.nodes.flatMap(node => (node.ports || []).map(port => [port.endpoint_id, port]))
      );
      const treePortLabels = new Map();
      const treePortCounters = new Map();
      const treePortLabel = (endpointId, direction) => {
        const port = endpointId ? portsByEndpointId.get(endpointId) : null;
        const persistedLabel = String(port?.label || "").match(direction === "out" ? /O\d+/ : /I\d+/)?.[0];
        if (persistedLabel) return persistedLabel;
        if (!endpointId) return direction === "out" ? "OUT" : "IN";
        if (!treePortLabels.has(endpointId)) {
          const next = (treePortCounters.get(direction) || 0) + 1;
          treePortCounters.set(direction, next);
          treePortLabels.set(endpointId, `${direction === "out" ? "O" : "I"}${next}`);
        }
        return treePortLabels.get(endpointId);
      };
      edges
        .slice()
        .sort((left, right) => Number(left.order || 0) - Number(right.order || 0))
        .forEach(edge => {
          treePortLabel(edge.endpoint_ids?.[0], "out");
          treePortLabel(edge.endpoint_ids?.[1], "in");
        });
      const treePort = (endpointId, direction) => {
        const port = endpointId ? portsByEndpointId.get(endpointId) : null;
        return {
          endpointId,
          direction,
          label: treePortLabel(endpointId, direction),
          protocol: port?.system === "kafka" ? "kafka" : port?.system === "rest" ? "http" : "unknown",
          type: port?.type || "Endpoint",
          method: port?.method || "Méthode Java inconnue",
          name: port?.name || "Ressource inconnue",
          messageType: port?.message_type || null,
          path: port?.path || "Source inconnue",
          line: port?.line,
        };
      };
      const methodForOccurrence = occurrence => {
        const inputEndpointId = inputForOccurrence(occurrence);
        const inputPort = inputEndpointId ? portsByEndpointId.get(inputEndpointId) : null;
        const inputMethod = String(inputPort?.method || "").trim();
        const preferredSignature = inputPort?.call_tree_signature;
        let candidates;
        if (preferredSignature) {
          candidates = [{ method: preferredSignature.method, parameterTypes: preferredSignature.parameter_types }];
        } else if (inputMethod.includes("::")) {
          candidates = [{ method: inputMethod, parameterTypes: inputPort.parameter_types }];
        } else {
          candidates = occurrence.flowIds.map(id => {
            const flow = flowById.get(id);
            const method = String(flow?.method || "").trim();
            const declaration = (graphData.codeql_call_graph?.nodes || []).find(node => (
              node.method === method && node.path === flow?.path
              && node.start_line === flow?.start_line
            ));
            return { method, parameterTypes: declaration?.parameter_types };
          }).filter(item => item.method);
        }
        const signatures = [...new Map(candidates.map(item => [
          JSON.stringify([item.method, item.parameterTypes]), item,
        ])).values()].map(item => {
          const separator = item.method.lastIndexOf("::");
          const start = separator >= 0 ? separator + 2 : item.method.lastIndexOf(".") + 1;
          const name = item.method.slice(start).split("(", 1)[0].trim();
          const parameters = Array.isArray(item.parameterTypes)
            ? `(${item.parameterTypes.join(", ")})` : null;
          return { name, parameters, full: `${item.method}${parameters || ""}` };
        }).filter(item => item.name);
        if (!signatures.length) return null;
        const labels = [...new Set(signatures.map(item => item.name))];
        return {
          label: labels.join(" · "),
          parameters: signatures.length === 1 ? signatures[0].parameters : null,
          title: signatures.map(item => item.full).join("\n"),
        };
      };
      const clearTreeTooltip = () => flowTooltipOverlay?.replaceChildren();
      const showTreeTooltip = (className, titleText, lines, clientX, clientY) => {
        if (!flowTooltipOverlay) return;
        const tooltip = document.createElement("span");
        tooltip.className = className;
        const title = document.createElement("strong");
        title.textContent = titleText;
        tooltip.append(title);
        lines.filter(Boolean).forEach((text, index) => {
          const line = document.createElement("span");
          line.className = index === 0 ? `${className}-kind` : `${className}-detail`;
          line.textContent = text;
          tooltip.append(line);
        });
        flowTooltipOverlay.replaceChildren(tooltip);
        const bounds = tooltip.getBoundingClientRect();
        const left = Math.max(8, Math.min(window.innerWidth - bounds.width - 8, clientX - bounds.width / 2));
        const below = clientY + bounds.height + 10 <= window.innerHeight;
        tooltip.dataset.placement = below ? "bottom" : "top";
        tooltip.style.setProperty("--tooltip-arrow-left", `${Math.max(10, Math.min(bounds.width - 10, clientX - left))}px`);
        tooltip.style.left = `${left}px`;
        tooltip.style.top = `${below ? clientY + 10 : Math.max(8, clientY - bounds.height - 10)}px`;
      };
      const showTreePortTooltip = (port, clientX, clientY) => {
        const direction = port.direction === "in" ? "IN" : "OUT";
        const qualifiedMethod = String(port.method || "").trim();
        const separator = qualifiedMethod.lastIndexOf("::");
        const ownerSeparator = separator >= 0 ? separator : qualifiedMethod.lastIndexOf(".");
        const javaClass = ownerSeparator > 0 ? qualifiedMethod.slice(0, ownerSeparator) : "Classe inconnue";
        const javaMethod = ownerSeparator > 0
          ? qualifiedMethod.slice(ownerSeparator + (separator >= 0 ? 2 : 1))
          : qualifiedMethod || "Méthode inconnue";
        showTreeTooltip("graph-port-tooltip", `${port.label} · Port ${direction}`, [
          `Classe Java : ${javaClass}`,
          `Méthode Java : ${javaMethod}`,
          `${port.type} · ${port.name}`,
          `${port.path}${port.line ? `:${port.line}` : ""}`,
        ], clientX, clientY);
      };
      const treeOccurrenceTooltipLines = occurrence => {
        const inputEndpointId = inputForOccurrence(occurrence);
        const lines = [
          occurrence.cycle
            ? "Microservice · cycle détecté"
            : occurrence.continuationUnknown
              ? "Microservice · suite inconnue"
              : "Microservice",
          `Niveau ${occurrence.depth + 1}${occurrence.cycle || occurrence.continuationUnknown ? " · expansion arrêtée" : ""}`,
        ];
        const method = methodForOccurrence(occurrence);
        if (method) lines.push(`Méthode Java : ${method.title.replaceAll("\n", " · ")}`);
        const occurrenceCount = occurrenceCounts.get(occurrence.name) || 1;
        if (occurrenceCount > 1) lines.push(`Appelé ${occurrenceCount} fois dans l’arbre`);
        const input = occurrence.edge && treePort(occurrence.edge.endpoint_ids?.[1], "in");
        if (input?.endpointId) lines.push(`IN ${input.label} · ${input.method}`);
        const outputs = occurrence.children
          .map(child => treePort(child.edge.endpoint_ids?.[0], "out"))
          .filter(port => port.endpointId);
        if (outputs.length) {
          const outputText = outputs
            .slice(0, 3)
            .map(port => `${port.label} · ${port.method}`)
            .join(" | ");
          lines.push(`OUT ${outputText}${outputs.length > 3 ? " …" : ""}`);
        }
        diagnosticAnnotationsForOccurrence(occurrence).forEach(({ diagnostic, side }) => {
          const classification = ({
            potential_risk: "Risque possible",
            guarantee_identified: "Garantie identifiée",
            insufficient_evidence: "Preuves insuffisantes",
          })[diagnostic.classification] || diagnostic.classification || "Diagnostic";
          lines.push(`Diagnostic · ${classification}`);
          if (diagnostic.resource_identity) lines.push(`Identité ressource · ${diagnostic.resource_identity}`);
          else lines.push("Identité ressource · non établie");
          if (diagnostic.key) lines.push(`Clé · ${diagnostic.key}`);
          if (diagnostic.key_identity) lines.push(`Identité clé · ${diagnostic.key_identity}`);
          else if (diagnostic.key) lines.push("Identité clé · non établie");
          if (diagnostic.causal_evidence) lines.push(`Lien causal · ${diagnostic.causal_evidence}`);
          if (diagnostic.order_evidence && diagnostic.order_evidence !== diagnostic.causal_evidence) {
            lines.push(`Ordre · ${diagnostic.order_evidence}`);
          }
          if (diagnostic.limitation) lines.push(`Limite · ${diagnostic.limitation}`);
          if (diagnostic[`${side}_phase`]) lines.push(`Phase · ${diagnostic[`${side}_phase`]}`);
          if (diagnostic[`${side}_transaction_id`]) lines.push(`Transaction · ${diagnostic[`${side}_transaction_id`]}`);
          if (diagnostic[`${side}_fields`]?.length) lines.push(`Champs · ${diagnostic[`${side}_fields`].join(", ")}`);
          if (diagnostic.guard) lines.push(`Protection · ${diagnostic.guard} (${diagnostic.guard_scope || "portée inconnue"})`);
          for (const dependencySide of ["write", "read"]) {
            const dependency = diagnostic[`${dependencySide}_dependency_site`];
            if (dependency?.module === occurrence.name
              && dependency.trigger_endpoint_id === inputEndpointId) {
              lines.push(`Lecture liée · ${dependency.path}:${dependency.line}`);
            }
          }
        });
        return lines;
      };
      const treeEdgeTooltip = edge => {
        const sourcePort = treePort(edge.endpoint_ids?.[0], "out");
        const targetPort = treePort(edge.endpoint_ids?.[1], "in");
        const protocol = edge.kind === "kafka" ? "Kafka" : edge.kind === "rest" ? "HTTP" : edge.kind || "Appel";
        const lines = [
          `${edge.source} → ${edge.target}`,
          `${edge.label || "Relation"} · ${protocol}`,
        ];
        if (sourcePort.endpointId) {
          lines.push(`OUT ${sourcePort.label} · ${sourcePort.method}`);
          lines.push(`Ressource / paramètres : ${sourcePort.name}`);
        }
        if (targetPort.endpointId) lines.push(`IN ${targetPort.label} · ${targetPort.method}`);
        const messageTypes = [...new Set([sourcePort.messageType, targetPort.messageType].filter(Boolean))];
        if (messageTypes.length) lines.push(`Type de message : ${messageTypes.join(" · ")}`);
        return {
          title: `Arc${edge.order ? ` #${edge.order}` : ""}`,
          lines,
        };
      };
      allOccurrences.forEach(occurrence => {
        const node = document.createElement("button");
        node.type = "button";
        const occurrenceCount = occurrenceCounts.get(occurrence.name) || 1;
        const frequencyClass = occurrenceCount >= 4 ? "is-frequency-4"
          : occurrenceCount === 3 ? "is-frequency-3"
            : occurrenceCount === 2 ? "is-frequency-2" : "";
        const isSelectedOccurrence = graphState.callTreeSelectedPathKey === occurrence.pathKey;
        node.className = `graph-call-tree-node${frequencyClass ? ` ${frequencyClass}` : ""}${occurrence.cycle ? " is-cycle" : ""}${isSelectedOccurrence ? " is-selected" : ""}`;
        node.dataset.pathKey = occurrence.pathKey;
        const original = nodeIdForCodeFlowResource(occurrence.name, "microservice");
        const originalNode = original ? nodeDataById.get(original) : null;
        node.style.setProperty("--card-accent", originalNode?.color || "#64748b");
        const icon = document.createElement("span");
        icon.className = "graph-node-card-icon is-service";
        const name = document.createElement("span");
        name.className = "graph-node-card-name";
        name.textContent = occurrence.name;
        const method = methodForOccurrence(occurrence);
        if (method) {
          const methodName = document.createElement("span");
          methodName.className = "graph-call-tree-method";
          const methodLabel = document.createElement("strong");
          methodLabel.className = "graph-call-tree-method-name";
          methodLabel.textContent = method.label;
          methodName.append(methodLabel);
          if (method.parameters !== null) {
            const parameters = document.createElement("span");
            parameters.className = "graph-call-tree-method-parameters";
            parameters.textContent = method.parameters;
            methodName.append(parameters);
          }
          methodName.title = method.title;
          node.append(icon, name, methodName);
        } else {
          node.append(icon, name);
        }
        if (occurrence.continuationUnknown) {
          const status = document.createElement("span");
          status.className = "graph-node-card-kind";
          status.textContent = "Suite inconnue";
          node.append(status);
        }
        const annotationGroups = [...annotationGroupsForOccurrence(occurrence).values()]
          .sort((a, b) => a.name.localeCompare(b.name, "fr")
            || a.resource.localeCompare(b.resource, "fr"));
        const annotationNameCounts = new Map();
        annotationGroups.forEach(group => annotationNameCounts.set(
          group.name, (annotationNameCounts.get(group.name) || 0) + 1,
        ));
        annotationGroups.forEach(group => {
          const certainLetters = [..."CRUD"].filter(letter => group.letters.has(letter)).join("");
          const letters = group.createOrUpdate
            ? certainLetters ? `${certainLetters} (C/U)` : "C/U"
            : certainLetters || "?";
          const selectedAnnotation = group.annotations.find(
            item => item.diagnostic.id === graphState.selectedDiagnosticId,
          );
          const kind = selectedAnnotation
            ? selectedAnnotation.kind
            : group.kinds.has("interaction") ? "interaction"
              : group.kinds.has("async") ? "async" : "sync";
          if (selectedAnnotation) node.classList.add(`is-diagnostic-${kind}`);
          const badge = document.createElement("span");
          badge.className = group.annotations.length
            ? `graph-call-tree-diagnostic-badge is-${kind}`
            : "graph-call-tree-access-badge";
          const qualifier = group.resource.includes(":")
            ? group.resource.split(":").at(-1).split(".").at(-2)
              || group.resource.split(":").at(-2)
            : "identité incertaine";
          const ambiguousName = annotationNameCounts.get(group.name) > 1;
          const entityName = ambiguousName
            ? `${group.name} (${qualifier})` : group.name;
          badge.textContent = `${letters} ${entityName}${group.handlerOnly ? " ?" : ""}`;
          if (ambiguousName) badge.title = group.resource;
          if (group.handlerOnly) {
            badge.title = `${badge.title ? `${badge.title} · ` : ""}Association possible au handler ; parcours interne non établi`;
          }
          if (group.flowIds.size) {
            badge.dataset.accessFlowIds = [...group.flowIds].sort().join(",");
          }
          if (group.annotations.length) {
            const diagnosticTitle = group.annotations.length > 1
              ? `${group.annotations.length} diagnostics associés · cliquer pour isoler`
              : "Cliquer pour isoler ce diagnostic";
            badge.title = `${diagnosticTitle}${badge.title ? ` · ${badge.title}` : ""}`;
            badge.addEventListener("click", event => {
              event.preventDefault();
              event.stopPropagation();
              const target = selectedAnnotation || group.annotations[0];
              if (selectedFlow) focusFlowDiagnostic(target.diagnostic, selectedFlow);
            });
          }
          node.append(badge);
        });
        if (treeTrigger && occurrence.depth === 0 && occurrence.name === treeTriggerRootName) {
          const triggerKind = ({
            http_entry: ["HTTP", "is-http"],
            message_entry: ["Kafka", "is-kafka"],
            cron_entry: ["Cron", "is-cron"],
          })[treeTrigger.kind] || ["Déclencheur", "is-cron"];
          const triggerBadge = document.createElement("span");
          triggerBadge.className = `graph-node-trigger-badge ${triggerKind[1]}`;
          triggerBadge.textContent = `${triggerKind[0]} · ${treeTrigger.name || "Événement inconnu"}`;
          triggerBadge.title = "Événement déclencheur du graphe d’appel sélectionné";
          node.append(triggerBadge);
        }
        const ports = [
          occurrence.edge && treePort(occurrence.edge.endpoint_ids?.[1], "in"),
          ...occurrence.children.map(child => treePort(child.edge.endpoint_ids?.[0], "out")),
        ].filter(Boolean);
        const portsByDirection = ports.reduce((groups, port) => {
          const directionPorts = groups[port.direction] || [];
          if (!directionPorts.some(existing => (
            existing.endpointId && existing.endpointId === port.endpointId
          ))) directionPorts.push(port);
          groups[port.direction] = directionPorts;
          return groups;
        }, { in: [], out: [] });
        Object.entries(portsByDirection).forEach(([direction, directionPorts]) => {
          directionPorts.forEach((port, index) => {
            const anchor = document.createElement("span");
            anchor.className = `graph-node-port-reference is-${direction} is-${port.protocol}`;
            anchor.textContent = port.label;
            anchor.title = `${direction === "in" ? "Port d’entrée" : "Port de sortie"} · ${port.label}`;
            anchor.style.setProperty(
              "--port-offset",
              `${((index + 1) / (directionPorts.length + 1)) * 100}%`
            );
            anchor.addEventListener("pointerenter", event => showTreePortTooltip(port, event.clientX, event.clientY));
            anchor.addEventListener("pointermove", event => showTreePortTooltip(port, event.clientX, event.clientY));
            anchor.addEventListener("pointerleave", clearTreeTooltip);
            node.append(anchor);
          });
        });
        if (occurrence.cycle) {
          const badge = document.createElement("span");
          badge.className = "graph-node-root-badge graph-call-tree-cycle-badge";
          badge.textContent = "Cycle";
          node.append(badge);
        }
        const canCollapse = occurrence.children.length > 0;
        if (occurrence.hiddenChildrenCount || canCollapse) {
          const more = document.createElement("span");
          more.className = `graph-call-tree-more-badge${canCollapse ? " is-expanded" : ""}`;
          more.textContent = canCollapse ? "− replier" : `+${occurrence.hiddenChildrenCount} appels`;
          more.title = canCollapse
            ? "Replier cette branche"
            : `Afficher le niveau suivant (${occurrence.hiddenChildrenCount} appels)`;
          more.addEventListener("click", event => {
            event.preventDefault();
            event.stopPropagation();
            const camera = captureCallTreeCamera();
            if (canCollapse) {
              graphState.callTreeCollapsed.add(occurrence.pathKey);
              graphState.callTreeExpanded.delete(occurrence.pathKey);
            } else {
              graphState.callTreeCollapsed.delete(occurrence.pathKey);
              graphState.callTreeExpanded.add(occurrence.pathKey);
            }
            renderCallTreeOverlay(camera);
          });
          node.append(more);
        }
        node.title = occurrence.cycle
          ? `${occurrence.name} · cycle détecté`
          : occurrence.continuationUnknown
            ? `${occurrence.name} · suite inconnue`
          : `${occurrence.name} · occurrence ${occurrence.id}`;
        node.addEventListener("pointerover", event => {
          if (event.target.closest?.(".graph-node-port-reference")) return;
          highlightCallTreePath(occurrence.pathKey);
          node.classList.add("is-hovered");
          showTreeTooltip("graph-call-tree-entity-tooltip", occurrence.name, treeOccurrenceTooltipLines(occurrence), event.clientX, event.clientY);
        });
        node.addEventListener("pointermove", event => {
          if (event.target.closest?.(".graph-node-port-reference")) return;
          showTreeTooltip("graph-call-tree-entity-tooltip", occurrence.name, treeOccurrenceTooltipLines(occurrence), event.clientX, event.clientY);
        });
        node.addEventListener("mouseleave", () => {
          node.classList.remove("is-hovered");
          highlightCallTreePath(null);
          clearTreeTooltip();
        });
        node.addEventListener("focus", () => highlightCallTreePath(occurrence.pathKey));
        node.addEventListener("blur", () => highlightCallTreePath(null));
        node.addEventListener("click", event => {
          event.preventDefault();
          event.stopPropagation();
          if (event.shiftKey) {
            openCallTreeNodeInspector(originalNode);
            return;
          }
          graphState.callTreeSelectedPathKey = occurrence.pathKey;
          renderCallTreeOverlay(captureCallTreeCamera());
        });
        canvas.append(node);
        nodeByOccurrence.set(occurrence, node);
      });
      graphCallTreeOverlay.replaceChildren(canvas);
      const cardHeightByOccurrence = new Map();
      const measureSubtree = occurrence => {
        const node = nodeByOccurrence.get(occurrence);
        const cardHeight = node.offsetHeight;
        cardHeightByOccurrence.set(occurrence, cardHeight);
        const childHeights = occurrence.children.map(measureSubtree);
        const childrenHeight = childHeights.reduce((sum, height) => sum + height, 0)
          + Math.max(0, childHeights.length - 1) * siblingGap;
        occurrence.subtreeHeight = Math.max(cardHeight, childrenHeight);
        return occurrence.subtreeHeight;
      };
      rootsTree.forEach(measureSubtree);
      const placeSubtree = (occurrence, bandTop) => {
        const childrenHeight = occurrence.children.reduce(
          (sum, child) => sum + child.subtreeHeight, 0,
        ) + Math.max(0, occurrence.children.length - 1) * siblingGap;
        occurrence.x = occurrence.depth * gapX;
        occurrence.y = bandTop
          + (occurrence.subtreeHeight - cardHeightByOccurrence.get(occurrence)) / 2;
        let childTop = bandTop + (occurrence.subtreeHeight - childrenHeight) / 2;
        occurrence.children.forEach(child => {
          placeSubtree(child, childTop);
          childTop += child.subtreeHeight + siblingGap;
        });
      };
      let rootTop = 0;
      rootsTree.forEach(root => {
        placeSubtree(root, rootTop);
        rootTop += root.subtreeHeight + siblingGap;
      });
      const logicalWidth = (Math.max(...allOccurrences.map(item => item.depth)) + 1) * gapX;
      const logicalHeight = Math.max(1, rootTop - siblingGap);
      const scale = Math.min(1, (viewport.width - 32) / logicalWidth, (viewport.height - 32) / logicalHeight);
      const offsetX = Math.max(16, (viewport.width - logicalWidth * scale) / 2);
      const offsetY = Math.max(16, (viewport.height - logicalHeight * scale) / 2);
      let left = offsetX;
      let top = offsetY;
      let zoom = graphState.callTreeZoom || 1;
      if (preservedCamera) {
        const centerX = preservedCamera.centerX ?? viewport.width / 2;
        const centerY = preservedCamera.centerY ?? viewport.height / 2;
        const logicalCenterX = (centerX - preservedCamera.left) / preservedCamera.totalScale;
        const logicalCenterY = (centerY - preservedCamera.top) / preservedCamera.totalScale;
        zoom = Math.max(.5, Math.min(64, preservedCamera.totalScale / scale));
        const totalScale = scale * zoom;
        left = centerX - logicalCenterX * totalScale;
        top = centerY - logicalCenterY * totalScale;
      }
      graphState.callTreeZoom = zoom;
      canvas.style.width = `${logicalWidth}px`;
      canvas.style.height = `${logicalHeight}px`;
      canvas.dataset.baseScale = String(scale);
      canvas.style.left = `${left}px`;
      canvas.style.top = `${top}px`;
      canvas.style.transform = `scale(${scale * zoom})`;
      svg.setAttribute("width", String(logicalWidth));
      svg.setAttribute("height", String(logicalHeight));
      nodeByOccurrence.forEach((node, occurrence) => {
        node.style.left = `${occurrence.x}px`;
        node.style.top = `${occurrence.y}px`;
      });
      const position = occurrence => ({ x: occurrence.x, y: occurrence.y });
      allOccurrences.forEach(occurrence => {
        const source = position(occurrence);
        occurrence.children.forEach(child => {
          const target = position(child);
          const sourceHeight = cardHeightByOccurrence.get(occurrence);
          const targetHeight = cardHeightByOccurrence.get(child);
          const sourceCenterY = source.y + sourceHeight / 2;
          const targetCenterY = target.y + targetHeight / 2;
          const labelX = direction === "lr"
            ? (source.x + cardWidth + target.x) / 2
            : (source.x + target.x + cardWidth) / 2;
          const labelY = direction === "lr"
            ? (sourceCenterY + targetCenterY) / 2 - 7
            : (source.y + sourceHeight + target.y) / 2 - 7;
          const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
          line.dataset.targetPathKey = child.pathKey;
          if (direction === "lr") {
            const startX = source.x + cardWidth;
            const endX = target.x;
            const middleX = (startX + endX) / 2;
            line.setAttribute("d", `M ${startX} ${sourceCenterY} C ${middleX} ${sourceCenterY}, ${middleX} ${targetCenterY}, ${endX} ${targetCenterY}`);
          } else {
            const startY = source.y + sourceHeight;
            const endY = target.y;
            const middleY = (startY + endY) / 2;
            line.setAttribute("d", `M ${source.x + cardWidth / 2} ${startY} C ${source.x + cardWidth / 2} ${middleY}, ${target.x + cardWidth / 2} ${middleY}, ${target.x + cardWidth / 2} ${endY}`);
          }
          const edgeHit = line.cloneNode();
          edgeHit.classList.remove("graph-call-tree-edge");
          edgeHit.classList.add("graph-call-tree-edge-hit");
          const protocolClass = child.edge.kind === "rest" ? "is-rest" : child.edge.kind === "kafka" ? "is-kafka" : "";
          line.classList.add("graph-call-tree-edge", protocolClass);
          edgeHit.classList.add(protocolClass);
          const edgeEndpointIds = new Set(child.edge.endpoint_ids || []);
          const edgeDiagnosticKind = [...edgeEndpointIds].some(endpointId => interactionDiagnosticPathEndpoints.has(endpointId))
            ? "interaction"
            : [...edgeEndpointIds].some(endpointId => asyncDiagnosticPathEndpoints.has(endpointId))
              ? "async"
            : [...edgeEndpointIds].some(endpointId => syncDiagnosticPathEndpoints.has(endpointId))
              ? "sync"
              : null;
          if (edgeDiagnosticKind) {
            line.classList.add(`is-diagnostic-${edgeDiagnosticKind}`);
            edgeHit.classList.add(`is-diagnostic-${edgeDiagnosticKind}`);
          } else if (selectedDiagnostic) {
            line.classList.add("is-diagnostic-muted");
            edgeHit.classList.add("is-diagnostic-muted");
          }
          const edgeTooltip = treeEdgeTooltip(child.edge);
          line.setAttribute("title", [edgeTooltip.title, ...edgeTooltip.lines].join(" · "));
          line.addEventListener("mouseenter", event => {
            line.classList.add("is-hovered");
            showTreeTooltip("graph-edge-tooltip", edgeTooltip.title, edgeTooltip.lines, event.clientX, event.clientY);
          });
          line.addEventListener("mousemove", event => {
            const tooltip = flowTooltipOverlay?.firstElementChild;
            if (tooltip) showTreeTooltip("graph-edge-tooltip", edgeTooltip.title, edgeTooltip.lines, event.clientX, event.clientY);
          });
          line.addEventListener("mouseleave", () => { line.classList.remove("is-hovered"); clearTreeTooltip(); });
          const topicNode = child.edge.kind === "kafka"
            ? nodeForCallTreeTopic(child.edge.label)
            : null;
          const callTreeRoute = child.edge.kind === "rest"
            ? routeForCallTreeEdge(child.edge)
            : null;
          if (topicNode || callTreeRoute) {
            line.style.cursor = "pointer";
            line.addEventListener("click", event => {
              if (!event.shiftKey) return;
              event.preventDefault();
              event.stopPropagation();
              openCallTreeEdgeInspector(child.edge);
            });
            edgeHit.addEventListener("click", event => {
              if (!event.shiftKey) return;
              event.preventDefault();
              event.stopPropagation();
              openCallTreeEdgeInspector(child.edge);
            });
          }
          svg.append(edgeHit);
          svg.append(line);
          const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
          label.classList.add("graph-call-tree-edge-label");
          label.dataset.targetPathKey = child.pathKey;
          if (edgeDiagnosticKind) {
            label.classList.add(`is-diagnostic-${edgeDiagnosticKind}`, "is-diagnostic-badge-label");
            const diagnosticLabel = `D${selectedDiagnosticIndex}`;
            const diagnosticBudget = Math.max(12, 36 - diagnosticLabel.length - 3);
            label.textContent = `${diagnosticLabel} · ${edgeDisplayLabel(child.edge, diagnosticBudget)}`;
          } else if (selectedDiagnostic) {
            label.classList.add("is-diagnostic-muted");
          }
          label.setAttribute("x", String(labelX));
          label.setAttribute("y", String(labelY));
          if (!edgeDiagnosticKind) label.textContent = edgeDisplayLabel(child.edge);
          if (topicNode || callTreeRoute) {
            label.classList.add("is-clickable");
            label.addEventListener("click", event => {
              if (!event.shiftKey) return;
              event.preventDefault();
              event.stopPropagation();
              openCallTreeEdgeInspector(child.edge);
            });
          }
          const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
          title.textContent = [
            edgeDiagnosticKind ? flowDiagnosticTitle(selectedDiagnostic) : "",
            treeEdgeTooltip(child.edge).title,
            ...treeEdgeTooltip(child.edge).lines,
          ].filter(Boolean).join(" · ");
          label.append(title);
          svg.append(label);
        });
      });
      canvas.prepend(svg);
    }
    function zoomCallTree(factor, anchorX = null, anchorY = null) {
      if (graphState.callGraphDisplayMode !== "tree") return false;
      const canvas = graphCallTreeOverlay?.querySelector(".graph-call-tree-canvas");
      if (!canvas) return false;
      const bounds = graphCallTreeOverlay.getBoundingClientRect();
      const baseScale = Number(canvas.dataset.baseScale) || 1;
      const oldZoom = graphState.callTreeZoom || 1;
      const oldTotalScale = baseScale * oldZoom;
      const x = anchorX ?? bounds.width / 2;
      const y = anchorY ?? bounds.height / 2;
      const left = Number.parseFloat(canvas.style.left) || 0;
      const top = Number.parseFloat(canvas.style.top) || 0;
      const logicalX = (x - left) / oldTotalScale;
      const logicalY = (y - top) / oldTotalScale;
      const nextZoom = Math.max(.5, Math.min(64, oldZoom * factor));
      const nextTotalScale = baseScale * nextZoom;
      graphState.callTreeZoom = nextZoom;
      canvas.style.left = `${x - logicalX * nextTotalScale}px`;
      canvas.style.top = `${y - logicalY * nextTotalScale}px`;
      canvas.style.transform = `scale(${nextTotalScale})`;
      return true;
    }
    function expandAllCallTree() {
      if (!codeFlowSelection.flowId || graphState.callGraphDisplayMode !== "tree") return false;
      const camera = captureCallTreeCamera();
      graphState.callTreeDepth = 8;
      graphState.callTreeExpanded = new Set();
      graphState.callTreeCollapsed = new Set();
      renderCallTreeOverlay(camera);
      syncCallTreeDepthControl();
      return true;
    }
    function syncCallTreeDepthControl() {
      const active = Boolean(codeFlowSelection.flowId)
        && (graphState.viewMode === "call-graph" || graphState.callGraphDisplayMode === "tree")
        && graphState.callGraphDisplayMode === "tree";
      const depth = Math.max(1, Math.min(8, Number(graphState.callTreeDepth) || 1));
      const depthControl = document.getElementById("flow-call-tree-depth-control");
      const depthValue = document.getElementById("flow-call-tree-depth-value");
      const depthDecrease = document.getElementById("flow-call-tree-depth-decrease");
      const depthIncrease = document.getElementById("flow-call-tree-depth-increase");
      if (depthControl) depthControl.hidden = !active;
      if (depthValue) depthValue.textContent = String(depth);
      if (depthDecrease) depthDecrease.disabled = !active || depth <= 1;
      if (depthIncrease) depthIncrease.disabled = !active || depth >= 8;
    }
    function adjustCallTreeDepth(delta) {
      if (!codeFlowSelection.flowId || graphState.callGraphDisplayMode !== "tree") return false;
      const currentDepth = Number(graphState.callTreeDepth) || 1;
      const nextDepth = Math.max(1, Math.min(8, currentDepth + delta));
      if (nextDepth === currentDepth) return false;
      const camera = captureCallTreeCamera();
      graphState.callTreeDepth = nextDepth;
      renderCallTreeOverlay(camera);
      syncCallTreeDepthControl();
      return true;
    }
    const uniqueTopologyLink = (endpointId, source) => {
      const candidates = (graphLinksByEndpoint.get(endpointId) || []).filter(candidate => (
        candidate.link.source === source
      ));
      return candidates.length === 1 ? candidates[0] : null;
    };
    function pathForCodeFlow(flow) {
      const serviceId = nodeIdForCodeFlowResource(flow.module, "microservice");
      if (!serviceId) return null;
      const nodes = [];
      const edges = [];
      const localLinks = [];
      const addFirst = id => { if (!nodes.length) nodes.push(id); };
      const addHop = topologyLink => {
        const source = nodes.at(-1);
        if (!source || !topologyLink || topologyLink.link.source !== source) return false;
        nodes.push(topologyLink.link.target);
        edges.push(topologyLink);
        return true;
      };
      const steps = flow.steps || [];
      const trigger = steps[0];
      if (!trigger) return null;
      const directKafkaLink = (topic, source, target) => graphData.links
        .map((link, index) => ({ link, index }))
        .find(({ link }) => (
          link.kind === "kafka"
          && link.label === topic
          && link.source === source
          && link.target === target
        ));

      if (trigger.kind === "message_entry") {
        const consumer = nodeIdForEndpoint(trigger.endpoint_id);
        if (consumer !== serviceId) return null;
        const incoming = (graphLinksByEndpoint.get(trigger.endpoint_id) || []).filter(link => (
          link.link.target === serviceId
        ));
        if (incoming.length !== 1) return null;
        const topicNode = incoming[0].link.source;
        // Include the concrete producer before the topic so a selected call
        // graph shows the asserted service-to-service message hop.
        const producers = graphData.links
          .map((link, index) => ({ link, index }))
          .filter(({ link }) => (
            link.target === topicNode
            && link.kind === "kafka"
            && nodeDataById.get(link.source)?.kind === "microservice"
            && link.label === incoming[0].link.label
          ));
        if (producers.length === 1) {
          const direct = directKafkaLink(incoming[0].link.label, producers[0].link.source, serviceId);
          if (direct) {
            addFirst(direct.link.source);
            nodes.push(serviceId);
            edges.push({ edge: `edge-${direct.index}`, link: direct.link });
          } else {
            addFirst(serviceId);
          }
        } else {
          addFirst(serviceId);
        }
      } else {
        addFirst(serviceId);
      }

      const inputEndpointId = trigger.endpoint_id;

      for (const step of steps.slice(1)) {
        if (step.kind === "http_call") {
          const candidate = uniqueTopologyLink(step.endpoint_id, nodes.at(-1));
          if (!candidate || !["rest", "enrichment_http"].includes(candidate.link.kind) || !addHop(candidate)) return null;
          if (inputEndpointId && internalOutputsByInput.get(inputEndpointId)?.has(step.endpoint_id)) {
            localLinks.push({ input_endpoint_id: inputEndpointId, output_endpoint_id: step.endpoint_id });
          }
          continue;
        }
        if (step.kind === "message_publish") {
          const publishingService = nodes.at(-1);
          if (nodeDataById.get(publishingService)?.kind !== "microservice") return null;
          const output = uniqueTopologyLink(step.endpoint_id, publishingService);
          if (!output || output.link.kind !== "kafka") return null;
          // The selected method's publication is an external effect. Project
          // every proven consumer of its concrete topic so the root service
          // does not appear isolated merely because the persisted flow ended
          // at the publish step.
          graphData.links
            .map((link, index) => ({ link, index }))
            .filter(({ link }) => (
              link.kind === "kafka"
              && link.source === publishingService
              && link.label === output.link.label
              && nodeDataById.get(link.target)?.kind === "microservice"
            ))
            .forEach(({ link, index }) => {
              if (!nodes.includes(link.target)) nodes.push(link.target);
              edges.push({ edge: `edge-${index}`, link });
            });
          if (inputEndpointId && internalOutputsByInput.get(inputEndpointId)?.has(step.endpoint_id)) {
            localLinks.push({ input_endpoint_id: inputEndpointId, output_endpoint_id: step.endpoint_id });
          }
          continue;
        }
        if (step.kind === "message_entry") {
          const target = nodeIdForEndpoint(step.endpoint_id);
          const incoming = (graphLinksByEndpoint.get(step.endpoint_id) || []).find(candidate => (
            candidate.link.target === target && candidate.link.kind === "kafka"
          ));
          const direct = incoming && directKafkaLink(incoming.link.label, nodes.at(-1), target);
          if (!direct) return null;
          nodes.push(target);
          edges.push({ edge: `edge-${direct.index}`, link: direct.link });
        }
      }
      // A same-service input → output link is also persisted topology evidence.
      // It has no Sigma edge, so keep it separately while treating the flow as
      // reconciled rather than falsely reporting a partial graph.
      return edges.length || localLinks.length ? { nodes, edges, localLinks } : null;
    }

    function showCodeFlows(flows) {
      const pathResults = flows.map(flow => {
        const globalCallGraph = callGraphForFlow(flow);
        const globalNodes = (globalCallGraph?.node_order || globalCallGraph?.nodes || [])
          .map(service => nodeIdForCodeFlowResource(service, "microservice"))
          .filter(Boolean);
        const globalPath = globalNodes.length
          ? { nodes: globalNodes, edges: [], localLinks: [] }
          : null;
        const exactPath = globalPath || callGraphPathForCodeFlow(flow) || pathForCodeFlow(flow);
        return { path: exactPath || nodePathForCodeFlow(flow), reconciled: Boolean(exactPath) };
      }).filter(result => result.path);
      const paths = pathResults.map(result => result.path);
      if (!paths.length) return;
      const path = {
        nodes: [...new Set(paths.flatMap(candidate => candidate.nodes))],
        edges: [...new Map(paths.flatMap(candidate => candidate.edges || []).map(edge => [edge.edge, edge])).values()],
        localLinks: [...new Map(paths.flatMap(candidate => candidate.localLinks || [])
          .map(link => [`${link.input_endpoint_id}:${link.output_endpoint_id}`, link])).values()],
      };
      const flow = flows[0];
      graphState.selectedDiagnosticId = null;
      graphState.diagnosticFocusSnapshot = null;
      graphState.callTreeDepth = 1;
      graphState.callTreeExpanded = new Set();
      graphState.callTreeCollapsed = new Set();
      graphState.callTreeSelectedPathKey = null;
      graphState.callTreeZoom = 1;
      // The owning module is the consumer for an input-triggered flow. The
      // visual root must follow the exported call-graph path instead: it is
      // the upstream producer when a single Kafka source is proven, and the
      // entry service for HTTP, fan-in, or Cron flows.
      const rootNodeId = nodeIdForCodeFlowResource(flow.module, "microservice") || path.nodes[0];
      if (!rootNodeId) return;
      // Keep the catalogue selected while revealing the selected flow behind
      // it. Switching to Graphe remains an explicit navigation action.
      if (flowsTab?.getAttribute("aria-selected") === "true") {
        setToolbarTab("flows", { showFlowGraph: true });
      } else {
        setToolbarTab("graph");
      }
      showPath(path, path.nodes, {
        codeFlow: flow,
        codeFlowRootNodeId: rootNodeId,
        codeFlowTrigger: flow.steps?.[0] || null,
        showDetails: false,
        topologyReconciled: pathResults.every(result => result.reconciled),
      });
      syncCodeFlowSelection();
      if (flowsTab?.getAttribute("aria-selected") === "true") {
        requestAnimationFrame(() => document.getElementById("architecture-toolbar")?.scrollTo({ top: 0, behavior: "instant" }));
      }
    }

    function showCodeFlow(flow) {
      showCodeFlows([flow]);
    }

    function callGraphPathForCodeFlow(flow) {
      const graph = callGraphForFlow(flow);
      if (!graph || !Array.isArray(graph.node_order) || graph.node_order.length < 2) return null;
      const nodes = graph.node_order
        .map(service => nodeIdForCodeFlowResource(service, "microservice"))
        .filter(Boolean);
      if (nodes.length < 2) return null;
      const endpointIds = new Set((flow.steps || [])
        .map(step => step.endpoint_id)
        .filter(Boolean));
      const localLinks = [];
      internalOutputsByInput.forEach((outputs, inputEndpointId) => {
        if (!endpointIds.has(inputEndpointId)) return;
        outputs.forEach(outputEndpointId => {
          if (endpointIds.has(outputEndpointId)) {
            localLinks.push({
              input_endpoint_id: inputEndpointId,
              output_endpoint_id: outputEndpointId,
            });
          }
        });
      });
      return { nodes, edges: [], localLinks };
    }

    function openCodeFlowInList(flow) {
      codeFlowFilter.value = flow.id;
      setToolbarTab("flows");
      requestAnimationFrame(() => codeFlowsList.querySelector(`[data-flow-id="${flow.id}"]`)?.scrollIntoView({ block: "nearest" }));
    }

    function nodePathForCodeFlow(flow) {
      const serviceId = nodeIdForCodeFlowResource(flow.module, "microservice");
      if (!serviceId) return null;
      const nodes = [];
      const add = id => { if (id && nodes.at(-1) !== id) nodes.push(id); };
      const steps = flow.steps || [];
      const trigger = steps[0];
      if (!trigger) return null;
      if (trigger.kind === "message_entry") {
        const incoming = (graphLinksByEndpoint.get(trigger.endpoint_id) || []).filter(link => (
          link.link.target === serviceId
        ));
        add(incoming.length === 1 ? incoming[0].link.source : null);
      }
      add(serviceId);
      steps.slice(1).forEach(step => {
        if (step.kind === "message_publish") {
          const outgoing = uniqueTopologyLink(step.endpoint_id, nodes.at(-1));
          add(outgoing?.link.target);
        }
        if (step.kind === "message_entry") {
          const incoming = uniqueTopologyLink(step.endpoint_id, nodes.at(-1));
          add(incoming?.link.target);
        }
        if (step.kind === "http_call") {
          const outgoing = uniqueTopologyLink(step.endpoint_id, nodes.at(-1));
          add(outgoing?.link.target);
        }
      });
      if (nodes.length >= 2) return { nodes, edges: [] };
      // Keep a partially reconciled interprocedural flow selectable from the
      // persisted endpoint evidence. This focuses the graph on the involved
      // services without pretending that a missing topology edge was proven.
      const evidenceNodes = [...new Set(steps.flatMap(step => (
        step.endpoint_id ? nodeIdsByEndpoint.get(step.endpoint_id) || [] : []
      )))]
        .filter(nodeId => nodeDataById.get(nodeId)?.kind === "microservice");
      if (evidenceNodes.length >= 2) return { nodes: evidenceNodes, edges: [] };
      return nodes.length ? { nodes, edges: [] } : null;
    }

    function syncCodeFlowSelection() {
      codeFlowsList.querySelectorAll(".code-flow-item").forEach(item => {
        item.classList.toggle("is-selected", item.dataset.flowId === codeFlowSelection.flowId);
      });
    }

    function servicesForCodeFlow(flow) {
      const callGraphOrder = callGraphForFlow(flow)?.node_order;
      if (Array.isArray(callGraphOrder) && callGraphOrder.length) {
        const names = callGraphOrder.filter(service => (
          nodeIdForCodeFlowResource(service, "microservice") !== null
        ));
        if (names.length) return [...new Set(names)];
      }
      return [...serviceIdsForCodeFlow(flow)]
        .map(nodeId => nodeDataById.get(nodeId)?.name)
        .filter(Boolean);
    }

    function showCodeFlowItemTooltip(flow, item) {
      const overlay = document.getElementById("graph-flow-tooltips");
      if (!overlay) return;
      overlay.hidden = false;
      const tooltip = document.createElement("span");
      tooltip.className = "graph-edge-tooltip code-flow-item-tooltip";
      const trigger = flow.steps?.[0];
      const title = document.createElement("strong");
      title.textContent = `${codeFlowStepLabel(trigger?.kind)} · ${trigger?.name || "Déclencheur inconnu"}`;
      tooltip.append(title);
      const description = document.createElement("span");
      description.className = "graph-edge-tooltip-detail";
      description.textContent = codeFlowDescription(flow);
      tooltip.append(description);
      const services = servicesForCodeFlow(flow);
      const stats = codeFlowStats(flow);
      const protocol = [...codeFlowProtocols(flow)].map(value => value.toUpperCase()).join(" + ") || "inconnu";
      const status = flow.status === "cycle"
        ? "Cycle détecté"
        : flow.reconciliation === "partial" ? "Réconciliation partielle" : "Topologie réconciliée";
      [
        `Parcours : ${services.join(" → ") || flow.module || "inconnu"}`,
        `${stats[0][1]} services · ${stats[1][1]} arcs · ${stats[2][1]} étapes · ${stats[3][1]} effets`,
        `Protocole : ${protocol} · Confiance : ${flow.confidence || "inconnue"}`,
        status,
      ].forEach(text => {
        const line = document.createElement("span");
        line.className = "graph-edge-tooltip-detail";
        line.textContent = text;
        tooltip.append(line);
      });
      overlay.replaceChildren(tooltip);
      const bounds = item.getBoundingClientRect();
      const tooltipBounds = tooltip.getBoundingClientRect();
      const left = Math.max(8, Math.min(window.innerWidth - tooltipBounds.width - 8, bounds.left + bounds.width / 2 - tooltipBounds.width / 2));
      const below = bounds.bottom + tooltipBounds.height + 10 <= window.innerHeight;
      tooltip.dataset.placement = below ? "bottom" : "top";
      tooltip.style.setProperty("--tooltip-arrow-left", `${Math.max(10, Math.min(tooltipBounds.width - 10, bounds.left + bounds.width / 2 - left))}px`);
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${below ? bounds.bottom + 10 : Math.max(8, bounds.top - tooltipBounds.height - 10)}px`;
    }

    function hideCodeFlowItemTooltip() {
      const overlay = document.getElementById("graph-flow-tooltips");
      if (!overlay) return;
      overlay.replaceChildren();
      const graphCanvas = document.getElementById("graph");
      overlay.hidden = Boolean(graphCanvas?.hidden);
    }

    function codeFlowItem(flow) {
      const item = document.createElement("li");
      const selected = codeFlowSelection.flowId === flow.id;
      item.className = `code-flow-item${flow.status === "cycle" ? " is-cycle" : ""}${flow.reconciliation === "partial" ? " is-partial" : ""}${selected ? " is-selected" : ""}`;
      item.dataset.flowId = flow.id;
      const header = document.createElement("div");
      header.className = "code-flow-header";
      const trigger = flow.steps?.[0];
      const title = document.createElement("div");
      title.className = "reference-title code-flow-title";
      title.textContent = `${codeFlowStepLabel(trigger?.kind)} · ${trigger?.name || "Déclencheur inconnu"}`;
      header.append(title);
      const exactPath = pathForCodeFlow(flow);
      const path = exactPath || nodePathForCodeFlow(flow);
      const meta = document.createElement("div");
      meta.className = "reference-meta";
      meta.textContent = flow.module;
      const description = document.createElement("p");
      description.className = "code-flow-reason";
      description.textContent = codeFlowDescription(flow);
      const stats = document.createElement("div");
      stats.className = "code-flow-stats";
      codeFlowStats(flow).forEach(([label, value]) => {
        const stat = document.createElement("span");
        stat.className = "code-flow-stat";
        const valueElement = document.createElement("strong");
        valueElement.textContent = value;
        const labelElement = document.createElement("small");
        labelElement.textContent = label;
        stat.append(valueElement, labelElement);
        stats.append(stat);
      });
      if (path) {
        item.tabIndex = 0;
        item.addEventListener("click", () => showCodeFlow(flow));
        item.addEventListener("keydown", event => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            showCodeFlow(flow);
          }
        });
      } else {
        item.classList.add("is-unavailable");
      }
      item.addEventListener("pointerenter", () => showCodeFlowItemTooltip(flow, item));
      item.addEventListener("pointerleave", hideCodeFlowItemTooltip);
      item.addEventListener("focus", () => showCodeFlowItemTooltip(flow, item));
      item.addEventListener("blur", hideCodeFlowItemTooltip);
      item.append(header, meta, description, stats);
      return item;
    }

    function focusFlowDiagnostic(diagnostic, flow) {
      if (!diagnostic || !flow) return;
      if (graphState.selectedDiagnosticId !== diagnostic.id) {
        graphState.diagnosticFocusSnapshot = {
          selectedCallGraphEdgeKey: graphState.selectedCallGraphEdgeKey,
          callTreeExpanded: [...graphState.callTreeExpanded],
          callTreeCollapsed: [...graphState.callTreeCollapsed],
          callTreeDepth: graphState.callTreeDepth,
          callTreeZoom: graphState.callTreeZoom,
        };
      }
      if (codeFlowSelection.flowId !== flow.id) showCodeFlow(flow);
      graphState.selectedDiagnosticId = diagnostic.id;
      graphState.callTreeDepth = Math.max(Number(graphState.callTreeDepth) || 1, 8);
      graphState.callTreeExpanded = new Set();
      graphState.callTreeCollapsed = new Set();
      rebuildGraph();
    }

    function restoreDiagnosticFullView() {
      const snapshot = graphState.diagnosticFocusSnapshot;
      graphState.selectedDiagnosticId = null;
      graphState.diagnosticFocusSnapshot = null;
      if (snapshot) {
        graphState.selectedCallGraphEdgeKey = snapshot.selectedCallGraphEdgeKey;
        graphState.callTreeExpanded = new Set(snapshot.callTreeExpanded || []);
        graphState.callTreeCollapsed = new Set(snapshot.callTreeCollapsed || []);
        graphState.callTreeDepth = snapshot.callTreeDepth || 1;
        graphState.callTreeZoom = snapshot.callTreeZoom || 1;
      }
      rebuildGraph();
    }

    function renderCodeFlows() {
      const query = codeFlowFilter.value.trim().toLocaleLowerCase();
      const visible = codeFlows.filter(flow => {
        const accesses = (graphData.flow_data_access_flow_ids_by_root?.[flow.id] || [])
          .flatMap(id => graphData.flow_data_accesses?.[id] || []);
        const haystack = [
          flow.id,
          flow.module,
          flow.method,
          flow.reason,
          ...(flow.steps || []).flatMap(step => [step.kind, step.name, step.path]),
          ...accesses.flatMap(access => [access.action, access.resource, access.module]),
        ].join(" ").toLocaleLowerCase();
        return !query || haystack.includes(query);
      });
      codeFlowsList.replaceChildren(...visible.sort(sortCodeFlows).map(codeFlowItem));
      syncCodeFlowSelection();
      codeFlowsEmpty.hidden = visible.length > 0;
      codeFlowsSummary.textContent = `${visible.length} flux d’appel affiché${visible.length > 1 ? "s" : ""} sur ${codeFlows.length}.`;
      updateCodeFlowFilterSummary();
      codeFlowsTitle.textContent = `Flux de code (${visible.length})`;
    }

    flowsPanel.addEventListener("systemlens:code-flow-open-in-graph", event => {
      const flow = codeFlows.find(item => item.id === event.detail?.flowId);
      if (flow) showCodeFlow(flow);
    });
    flowsPanel.addEventListener("systemlens:code-flow-open-in-list", event => {
      const flow = codeFlows.find(item => item.id === event.detail?.flowId);
      if (flow) openCodeFlowInList(flow);
    });

    codeFlowFilter.addEventListener("input", renderCodeFlows);
    document.getElementById("flows-panel").addEventListener("systemlens:flows-open", renderCodeFlows);
    renderCodeFlows();
