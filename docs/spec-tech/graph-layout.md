# Graph layout algorithms

Parent: [Technical specification](../SPEC-TECH.md).

The user-facing presentation and interaction contract is maintained in
[UX/UI rules](../UX-UI.md). This page owns coordinate systems, camera
mechanics, layout algorithms, complexity, and fallback behavior.

### Coordinate system and rendering

The HTML renderer keeps graph coordinates as the source of truth for layout.
Sigma's canvas and the HTML card overlays share the same full-window
workspace rectangle behind the floating navigation and details panel. Overlay
positions are obtained from Sigma's public
`graphToViewport` conversion using the raw graph coordinates; renderer
internal matrices and full-window canvas coordinates are not mixed with the
workspace-local overlay coordinates.

The browser controller is maintained as ordered source modules under
`src/systemlens/render/assets/graph/`: core setup, graph rebuilding and camera
events, controls, layouts, details, path exploration, and bootstrap wiring.
The stylesheet follows the same ordered-module convention: base graph,
widgets, presentation theme, then the shared visual charter. The export
assembler concatenates both CSS and JavaScript modules into the standalone
document.
Mutable selection, view, layout, and camera state is held in one
`graphState` object. Overlay refreshes go through one animation-frame
scheduler (`requestGraphRender`) so canvas and HTML overlays observe one
coalesced render cycle.

The static architecture placement uses a deterministic force-style solver. For
graphs up to 180 visible nodes it retains a 720-iteration budget. Larger
graphs use an adaptive budget of `max(120, round(130000 / n))` iterations,
where `n` is the number of connected layout nodes. For those large graphs,
repulsion uses a spatial grid and compares each node only with occupants of
its neighboring cells; the common-case cost becomes proportional to local
occupancy rather than all node pairs. The adaptive budget prevents the
constant 720-pass multiplier from dominating large exports. This is a
performance fallback: large graphs may settle less completely, while links,
visibility, and persisted architecture facts remain unchanged.

Module selection is tracked independently from node selection in `graphState`; it
uses the visible canonical `cluster_path` values to derive a hierarchy of path
prefixes. A module descriptor contains its canonical key, parent path, direct
child paths, and the visible node identifiers whose module path is an exact
match. This exact-match rule keeps direct membership distinct from descendant
membership and prevents filtered or guessed nodes from entering the details
list. Selecting a listed resource returns to the ordinary node-detail flow;
following a resource's module action selects the corresponding descriptor
without changing layout.

Persisted `runtime_namespaces` and `fact_namespaces` remain available in the
embedded snapshot for backward compatibility and evidence processing. The
details renderer does not expose them as architectural metadata and does not
prefix Kubernetes workload names with their runtime namespace; module
navigation is sourced exclusively from the canonical `cluster_path` value.

### Camera interactions

The selected call graph is rendered only as a separate hierarchical projection
of the persisted call-graph snapshot. It does not mutate the architecture
graph or the persisted node identifiers. The export stores a compact causal
transition index for each flow root. The browser expands that index into local
occurrences, while labels and node actions resolve to the original
microservice. The tree uses a deterministic depth-first projection with a
per-branch ancestry set, a configured depth limit, and the child order from the
causal transition index. Each occurrence labels its Java method from the input
port's indexed method mapping, falling back to the associated `CodeFlow.method`.
The card shows the short method name and keeps the qualified name in its title.
Visible arc labels are capped at 36 characters, including diagnostic labels.
Tooltips retain the indexed edge details. Hover and focus apply a transient
root-to-occurrence path highlight by comparing local occurrence path keys; this
does not alter the selected flow or snapshot.

Tree coordinates are assigned by depth and leaf order. Internal occurrences
are centered over their visible children, then the complete logical canvas is
scaled to the graph workspace. This gives repeated services independent cards
and prevents shared-node identity from collapsing distinct branches. A cycle
endpoint is retained as a terminal occurrence. Expansion is bounded by the
configured depth, so rendering cost is O(o + e), where `o` is the number of
expanded occurrences and `e` is the number of inspected outgoing edges.
An input-endpoint or flow ancestry set marks a repeated causal occurrence as a
cycle. A reachable service without a matching consumer flow is retained as a
terminal unknown-continuation occurrence. Tree construction also stops at
5,000 occurrences and exposes the remaining child count on the boundary node.
This bounds pathological cyclic or highly branching flows while preserving
repeated services that use distinct indexed input ports.
Duplicate evidence for the same causal relation is removed before transitions
are indexed. The identity is the protocol, source endpoint, target endpoint,
and topic or route. Distinct endpoint pairs on the same topic remain distinct,
because they may represent different producer or consumer contracts.
The tree projection is an independent overlay. Its background captures
primary-pointer drags and translates the tree canvas, while service cards keep
pointer interaction for selection. The graph context widget exposes the mode
and controls without duplicating the tree hierarchy. The tree canvas also
supports wheel and button zoom from 0.5x to 64x without changing persisted
graph coordinates. Tree DOM construction is keyed by the selected flow,
depth, expanded or collapsed branch paths, and overlay viewport; camera
refreshes reuse the existing tree when that signature is unchanged. This
avoids rebuilding every node, arc, port, and tooltip during ordinary canvas
refreshes.

The shared card size remains stable during navigation. Camera fitting starts
from Sigma's native complete overview. `All nodes` uses that state unchanged.
The default `Readable distance` mode zooms in by at least 1.6x, using projected
card spacing. For graphs with at most 12 nodes, it derives an overview ratio
from projected center span and available viewport space so all cards remain
visible before the collision pass.

For each overlapping pair, the required zoom factor is the smaller of its
horizontal and vertical separation factors: clearance on either axis is
enough. The factor is capped at 4, then nearby pair factors are sorted. The
90th percentile avoids letting one nearly coincident pair determine the
whole camera distance. The combined ForceAtlas2/Noverlap layout uses the
maximum factor. With `p` nearby pairs, sorting costs O(p log p).

The renderer keeps one graph and HTML overlay implementation for both node
rendering modes. Card mode uses the fixed 110×70 envelope. Symbol mode applies
a 30×30 overlay marker and reveals its overflowing adjacent name only on hover;
its fit calculation uses a 34×34 marker envelope, so label length does not force
the camera away from the graph. Switching modes redraws overlays and the Sigma
node reducer without rebuilding or re-parsing the persisted graph snapshot. It
does not invoke camera fitting or collision placement, so the camera state and
graph coordinates remain unchanged.

Relations remain rendered by Sigma independently of the HTML card overlays.
In symbol mode, Sigma's underlying node marker is reduced beneath the HTML
shape so that the card-oriented canvas glyph does not remain visible.
After the combined ForceAtlas2/Noverlap layout and readable camera fit, the
graph resolves residual collisions in projected screen space. The
solver uses 110×70 card envelopes or 30×30 symbol envelopes, converts the
adjusted centers back to graph coordinates.
Camera updates during pan and zoom are coalesced to the next animation frame.
Fit operations reset Sigma's normalized camera state synchronously before
calculating the selected mode. This makes overlapping fit requests idempotent
and prevents zero-duration camera animations from racing on repeated clicks.
The details section participates in the navigation panel's vertical flow but
does not participate in the Sigma workspace rectangle. The HTML node overlays
retain the same full-height coordinate surface as the canvas.
Opening or resizing details only changes the navigation panel's internal scroll
extent and therefore cannot introduce a clipping seam or reset the camera.
The desktop workspace starts at the window's left edge. The toolbar overlays
its upper-left portion, while nodes and relations remain rendered in the usable
space below the toolbar.
The Sigma canvas and HTML card overlays are recomputed from one camera state
per frame.

Wheel zoom is handled once for both the Sigma canvas and the HTML overlays;
the native Sigma wheel handler is disabled so hovering a card cannot change
the zoom behavior. Each wheel event applies a bounded exponential camera-ratio
step within the camera ratio bounds.

### Layout engines

The graph supports the combined ForceAtlas2/Noverlap placement and secondary
ForceAtlas2 or Noverlap strategies. Every placement restores initial node
positions before running the selected solver. Failure restores the previous
layout and reports it in the graph status. No layer or module packing is
performed.

Each layout applies the selected camera fit. `All nodes` resets to a complete
overview; `Readable distance` measures projected card spacing. The selected
mode is reapplied after a viewport resize. Node positions remain in graph
coordinates; cards are positioned from their projected centers after camera
updates.

### Browser verification

Browser integration tests capture PNG and JSON snapshots after graph actions.
They inspect rendered pixels, card size, visible node positions, and pan/zoom
behavior. Snapshots are written under `output/playwright/` for inspection.
The browser launcher uses Chromium and skips a test if it cannot start.
