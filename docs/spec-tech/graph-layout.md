# Graph layout algorithms

Parent: [Technical specification](../SPEC-TECH.md).


### Coordinate system and rendering

The HTML renderer keeps graph coordinates as the source of truth for layout.
Sigma's canvas and the HTML card/module overlays share the same full-window
workspace rectangle behind the floating navigation and details panel. Overlay positions are obtained from Sigma's public
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

Architecture-module titles are interactive overlay controls. Module selection
is tracked independently from node selection in `graphState`; it
uses the visible canonical `cluster_path` values to derive a hierarchy of path
prefixes. A module descriptor contains its canonical key, parent path, direct
child paths, and the visible node identifiers whose module path is an exact
match. This exact-match rule keeps direct membership distinct from descendant
membership and prevents filtered or guessed nodes from entering the details
list. Selecting a listed resource returns to the ordinary node-detail flow;
following a resource's module action applies the deterministic module layout
before selecting the corresponding descriptor.

Persisted `runtime_namespaces` and `fact_namespaces` remain available in the
embedded snapshot for backward compatibility and evidence processing. The
details renderer does not expose them as architectural metadata and does not
prefix Kubernetes workload names with their runtime namespace; module
navigation is sourced exclusively from the canonical `cluster_path` value.

### Camera interactions

The shared card size remains stable during navigation. Camera fitting starts
from Sigma's native complete overview. In the plain graph, `All nodes` uses
that state unchanged. In compound module and layer views, it measures the
projected card and sibling-module envelopes and applies the minimum
collision-free zoom. The default `Readable distance` mode also zooms in by at
least 1.6x. The historical 4x spacing guard remains limited to the plain graph;
compound views are not capped because a narrow viewport may require greater
separation. Graphs with at most 12 nodes in the plain graph instead derive an
overview ratio from their projected center span and the
available viewport after subtracting the fixed card width and height. This
keeps the complete card envelopes visible and relies on the following
collision pass for separation.
For each overlapping pair, the required factor is the smaller of its
horizontal and vertical separation factors: reaching the card clearance on
either axis is sufficient. Compound views use the maximum factor across card
pairs and module-envelope pairs. For module envelopes, the required factor is
derived from the gap between projected min/max center intervals on both axes,
including fixed card size, module header/padding and a positive sibling gap.
The factors are collected through the existing spatial grid and sorted, adding
O(p log p) work for p nearby card pairs plus O(m²) comparisons for m rendered
modules. Peripheral nodes may therefore leave the viewport. Panning and
zooming in never move nodes; zooming out is clamped to the collision-free ratio
in compound views.

The renderer keeps one graph and HTML overlay implementation for both node
rendering modes. Card mode uses the fixed 110×70 envelope. Symbol mode applies
a 30×30 overlay marker and reveals its overflowing adjacent name only on hover;
its fit calculation uses a 34×34 marker envelope, so label length does not force
the camera away from the graph. Switching modes redraws overlays and the Sigma
node reducer without rebuilding or re-parsing the persisted graph snapshot. It
does not invoke camera fitting or collision placement, so the camera state and
graph coordinates remain unchanged. Compound module and layer fits therefore
reserve the larger 110×70 card envelope even when Symbols is active; switching
back to Cards cannot introduce an overlap.

Relations remain rendered by Sigma independently of the HTML card overlays.
In symbol mode, Sigma's underlying node marker is reduced beneath the HTML
shape so that the card-oriented canvas glyph does not remain visible.
After the combined ForceAtlas2/Noverlap layout and readable camera fit, the
plain graph alone resolves residual collisions in projected screen space. The
solver uses 110×70 card envelopes or 30×30 symbol envelopes, converts the
adjusted centers back to graph coordinates, and leaves compound layouts
untouched.
Camera updates during pan and zoom are coalesced to the next animation frame.
Fit operations reset Sigma's normalized camera state synchronously before
calculating the selected mode. This makes overlapping fit requests idempotent
and prevents zero-duration camera animations from racing on repeated clicks.
The details section participates in the navigation panel's vertical flow but
does not participate in the Sigma workspace rectangle. The HTML layer, module,
and node overlays retain the same full-height coordinate surface as the canvas.
Opening or resizing details only changes the navigation panel's internal scroll
extent and therefore cannot introduce a clipping seam or reset the camera.
The desktop workspace starts at the window's left edge. The toolbar overlays
its upper-left portion, while nodes and relations remain rendered in the usable
space below the toolbar.
The Sigma canvas and the HTML card/module overlays are therefore recomputed
from one camera state per frame, preventing partially rebuilt containers from
appearing while the user drags the module view.

Wheel zoom is handled once for both the Sigma canvas and the HTML overlays;
the native Sigma wheel handler is disabled so hovering a card cannot change
the zoom behavior. Each wheel event applies a bounded exponential camera-ratio
step and respects the collision-free maximum ratio in compound views.

### Layout engines and architecture-module placement

The ELK layer layout loads independently from the ForceAtlas2 and Noverlap
modules used by the graph layouts, so unrelated dynamic imports cannot keep
the layer view in a pending state.

Force-based placement uses Sigma's node radius to preserve the graph structure;
it does not mutate positions after the camera fit to repair HTML-card overlap.

The module packer places microservices in a first sub-layer and
resources in a second sub-layer on separated grids, then
packs module rectangles with positive margins that include the complete
projected card/title envelope, not only the node-grid dimensions. Its graph-space
gaps are expressed in the same graph-coordinate scale as the rest of the
layout, while remaining large enough for the shared 110×70 card envelope. The
sub-layer assignment remains available as layout state and export diagnostics,
but the overlay does not render sub-layer titles.

Each layout starts with the shared camera-fit operation in the selected mode.
The `All nodes` and `Readable distance` actions select and immediately apply
their mode; the selection is reapplied after a viewport resize. This prevents
view switches from retaining a stale camera scale while allowing the readable
mode to favor card separation over a complete overview. In module and layer
views, cards and sibling module rectangles remain disjoint; parent rectangles
may overlap only by containing their children. Their fixed screen-space
dimensions are preserved during navigation.

Node positions remain in graph coordinates. Container geometry is rebuilt in
viewport coordinates from projected node centers after every camera update.

The module view uses this deterministic packing as its source of truth; it does
not wait for a compound force layout that could block the browser. When project
or other parent groups are enabled, their bounds are the union of the already
projected child-module bounds plus title/padding margins; node-grid gaps are
calibrated with the shared 110×70 card envelope and a dedicated vertical
separation between the two sub-layers. This explicit hierarchy prevents a
parent from being smaller than a nested module after zooming. Project groups
carry their owning module and full module path. The historical
`project_namespace` and `project_namespace_path` fields remain read-compatible
aliases for `cluster_path`; they do not represent Kubernetes namespaces.

Structural project groups contain only
their owning projects; resource nodes resolve their module
from incoming producer edges before consulting resource metadata; this keeps
topics and collections with their producing microservice.

When several services write the same resource, ownership is selected by the
lowest service layer in the canonical order; ties are resolved by service name
for deterministic exports.

### Layer placement

The layered view reuses this packer with an additional grouping key: modules
are first grouped by the canonical internal software-layer order (`api`,
`application`, `orchestration`, `infrastructure`, `domain`, `persistence`),
producing top-to-bottom layer bands. External microservices use the dedicated
`external` layer at the bottom and are not part of the internal dependency
order.

The same canonical resolver is used for layer placement, module boxes, and
band bounds, so a module cannot be placed in a band different from its
nodes. Each module uses the same two-part grid as the module view:
microservices first, then resources.

Layer-band bounds group every rendered resource with
`layeredLayerForNode`; they do not reassign non-microservice resources to the
visually nearest service layer. The module envelope and its owning band thus
consume exactly the same layer identity.

ELK may provide the initial compound layout, but the deterministic layer-aware
pack is the final collision guard and remains valid when ELK fails. The layered packer
checks each module envelope after placement. Modules that exceed the vertical
safety envelope are widened by adding columns, then the row width and all
positions are recomputed. This trades height for diagram width to preserve
layer separation.

Layer bands reserve a left graph-space gutter for their titles, so the title
overlay cannot cover the first architecture module.

### Browser verification

The layer-band calculation is implemented in the embedded `layer_geometry.js` module
and is covered by renderer geometry unit tests for ordering, containment, and
shared bounds. Browser integration tests additionally capture a PNG and JSON
geometry snapshot after each significant browser action (load, view change,
filter, selection, zoom, pan, and resize); the PNG pixels are inspected for
actual rendered content in the graph region, not only the DOM. The tests also
verify node-center visibility, fixed card dimensions, and pan/zoom
synchronization. Snapshots are written under `output/playwright/` for visual
inspection when a regression occurs.
The browser launcher tries Playwright Chromium first, then Firefox and WebKit;
an integration test is skipped only when all three engines fail to launch.

