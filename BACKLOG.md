# Backlog: graph readability

Status: implemented; browser verification pending. Owner: SystemLens.
This backlog covers four
rendering improvements for the architecture graph and selected call tree:
progressive detail, arc labels, navigation, and visual hierarchy. No task is
marked complete until its acceptance criteria and relevant checks pass.

## 1. Index and scope

| Document | Purpose |
|---|---|
| [Archived consistency backlog](BACKLOG.consistency-diagnostics.md) | Historical snapshot of the former diagnostic rendering tasks. |
| [Earlier backlog](BACKLOG.previous.md) | Historical index of data-operation work. |
| [UX/UI rules](docs/UX-UI.md) | Authoritative visible behavior and interaction rules. |
| [HTML behavior](docs/spec-fonc/html-export.md) | Authoritative export behavior. |
| [Graph layout](docs/spec-tech/graph-layout.md) | Camera, coordinates, layout, and performance rules. |

The archived backlogs retain their original task status. The linked plans own
the current status of deferred
[consistency analysis](docs/plans/async-write-read-consistency.md) and
[data operations](docs/plans/data-operations-in-internal-flows.md).
This plan changes only the HTML projection. It does not alter persisted facts,
flow detection, diagnostic classification, or source evidence. The HTML concerns
deferred in
[AGENTS.md](AGENTS.md#deferred-html-accessibility-and-delivery-concerns)
remain outside this backlog.

## 2. Starting point and design direction

The architecture view uses fixed-size cards, a Sigma canvas, and HTML overlays.
Its layout and camera logic live in
[layout](src/systemlens/render/assets/graph/30-layouts.js) and
[controls](src/systemlens/render/assets/graph/20-controls.js). The call tree
uses measured card heights but a fixed 190-pixel width. It fits the complete
tree to the viewport and places arc labels at edge midpoints in
[call-tree rendering](src/systemlens/render/assets/graph/55-code-flows.js).

> **Why & What - Progressive graph detail**
>
> **What:** Use the rendered screen size to choose how much detail appears on
> each card, without changing its layout footprint during zoom.
>
> **Why:** A complete overview can make card text too small to read. Keeping
> geometry fixed prevents a zoom step from moving ports and arcs.
>
> **Alternative:** Enlarge every card at low zoom. This preserves all content
> but consumes more space and can increase overlap.
>
> **Fallback:** Keep the full details in selection and inspection views when
> a card is too small for them.

> **Why & What - Selective arc labels**
>
> **What:** Place labels only when their measured bounds avoid cards and other
> visible labels; preserve full edge details on inspection.
>
> **Why:** The call tree currently estimates label width from character count
> and places labels at midpoints. Dense branches can still compete for space.
>
> **Alternative:** Reserve enough space for every full label. This makes every
> label visible but can produce a very wide tree.
>
> **Fallback:** Hide a colliding label while retaining the arc and its tooltip.

## 3. Implementation tasks

Tasks are ordered by dependency. Each task names an inspectable output and a
behavioral acceptance check. Update the owning specifications with the code.

### G01. Establish baselines and settle display rules

- [ ] Record screenshots or browser snapshots for a small architecture, a dense
  architecture, a shallow call tree, and a deep annotated call tree at 1440x900
  and 900x700. Use checked-in examples or deterministic browser fixtures.
- [x] Specify overview, readable, focused, and selected detail levels in
  [UX/UI](docs/UX-UI.md). Define which facts stay visible at each level and
  how a user obtains hidden details. Clarify the existing port text and tooltip
  rules against the current call-tree implementation.
- [ ] Acceptance: a reviewer can identify the service and graph direction in
  the overview, and can reach the full method and evidence from a selected
  occurrence. The specification matches current port behavior before later
  tasks use it as a contract.

### G02. Apply progressive detail to architecture cards

- [x] Implement screen-size-based detail levels in
  [graph rebuilding](src/systemlens/render/assets/graph/10-rebuild.js) and
  [graph styling](src/systemlens/render/assets/graph/00-graph.css). Keep the
  selected and hovered resource identifiable at every level.
- [x] Preserve card coordinates, relation endpoints, and source facts when
  zoom or fit changes the visible text. Expose omitted details through the
  existing inspector or tooltip.
- [ ] Acceptance: fit and zoom switch detail predictably without moving nodes,
  losing selection, or hiding the selected name. Verify small and dense graphs
  in both themes and at a constrained viewport width.

### G03. Apply progressive detail to call-tree cards

- [x] Use the same detail principles for service name, method, parameter types,
  annotations, and status badges in
  [call-tree rendering](src/systemlens/render/assets/graph/55-code-flows.js).
  Keep all data reachable through existing inspection surfaces.
- [x] Maintain measured layout bounds and port attachment while zooming. A
  selected occurrence must retain an obvious visual marker at overview scale.
- [ ] Acceptance: a deep tree can be read first as a sequence of services;
  zooming or selecting reveals method and annotation detail. Zoom alone does
  not reroute arcs or shift occurrence coordinates.

### G04. Measure and prioritize call-tree arc labels

- [x] Replace character-count spacing as the sole label fit rule. Measure
  rendered labels, test candidate positions against cards and placed labels,
  and choose a deterministic visible subset when space is insufficient.
- [x] Prioritize the selected arc or highlighted path, then short labels that
  fit. Keep order, protocol, and the complete indexed label in the arc tooltip.
- [ ] Acceptance: labels do not cover cards or one another in the dense fixture;
  every arc remains visible and inspectable. Repeated renders with identical
  inputs produce the same labels and positions.

### G05. Keep navigation stable during tree exploration

- [x] Preserve the user's viewed region, selected occurrence, and zoom when
  changing depth or expanding and collapsing a branch. Provide a direct action
  to recenter on the selected occurrence and a separate action to fit the tree.
- [x] Update
  [call-tree controls](src/systemlens/render/assets/graph/55-code-flows.js)
  and the shared
  [camera controls](src/systemlens/render/assets/graph/20-controls.js)
  only where each view needs them.
- [ ] Acceptance: expanding a branch does not unexpectedly reset the camera;
  recentering makes the selected occurrence visible; fitting still shows the
  complete visible tree. Test repeated service occurrences and cycles.

### G06. Add a viewport overview for large graphs

- [x] Add a compact overview of the visible architecture graph and call tree,
  including the current viewport. Allow a click or drag in the overview to
  move the camera without changing graph selection or indexed data.
- [x] Show the overview only when the visible graph exceeds the viewport by a
  documented threshold. Bound its rendering cost for large architecture
  graphs and a call tree near its existing occurrence limit.
- [ ] Acceptance: overview and main viewport stay synchronized after zoom,
  pan, filter, branch expansion, and resize. On a small graph, the overview
  remains absent and existing controls work as before.

### G07. Clarify visual hierarchy and status semantics

- [x] Consolidate normal, hovered, selected, path-focused, muted, and
  diagnostic styling in the graph CSS modules. Keep protocol identity and
  diagnostic state distinguishable with colour plus stroke or shape.
- [x] Reduce the emphasis of unrelated relations during focus without making
  them disappear. Keep multiple annotations available on an elastic card and
  ensure the legend describes only visible semantics.
- [ ] Acceptance: selection and diagnostic focus remain distinguishable in
  light and dark themes and on a dense graph. Clearing focus restores the
  ordinary appearance with no stale classes or hidden relations.

### G08. Verify the combined reading workflow

- [x] Add behavioral coverage in
  [browser tests](tests/test_browser_export.py) for detail transitions, label
  collisions, camera preservation, overview navigation, and visual state reset.
  Use rendered bounds and state, not only generated HTML strings.
- [x] Update [UX/UI](docs/UX-UI.md),
  [HTML behavior](docs/spec-fonc/html-export.md), and
  [graph layout](docs/spec-tech/graph-layout.md) for the final behavior.
- [ ] Acceptance: the same fixtures support tracing a path from entry to leaf,
  finding an arc's full label, and returning to the complete overview. Run the
  focused Python checks and browser suite in [AGENTS.md](AGENTS.md#validation);
  record any unavailable browser prerequisite explicitly.
