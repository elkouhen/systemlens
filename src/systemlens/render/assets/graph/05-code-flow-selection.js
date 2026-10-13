// Ordered source module: 05-code-flow-selection.js
// Owns the selected flow and the context needed to render its call graph.
const codeFlowSelection = (() => {
  const emptySelection = Object.freeze({ flowId: null, rootNodeId: null, trigger: null });
  let selection = emptySelection;

  function select(flowId, { rootNodeId = null, trigger = null } = {}) {
    selection = flowId
      ? Object.freeze({ flowId, rootNodeId, trigger })
      : emptySelection;
    return selection;
  }

  function clear() {
    selection = emptySelection;
  }

  return {
    get flowId() { return selection.flowId; },
    get rootNodeId() { return selection.rootNodeId; },
    get trigger() { return selection.trigger; },
    select,
    clear,
  };
})();
