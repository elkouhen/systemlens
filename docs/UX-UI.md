# UX/UI rules

This document is the normative UX/UI contract for the generated HTML export.
It defines how the Explorer communicates indexed architecture facts, how users
navigate the graph, and how the interface behaves in light, dark, and
constrained viewports.

The functional specification owns observable domain behavior. The technical
specification owns data structures, coordinate systems, and layout algorithms.
This document owns the visible presentation and interaction rules that connect
those contracts. A visual rule MUST be changed here first, then reflected in
the relevant implementation and regression test.

## Scope and principles

- The interface MUST present persisted architecture evidence. It MUST NOT turn
  an unresolved or ambiguous fact into a visually certain relationship.
- A visual state MAY derive from the selected resource, filter, flow, or
  camera, but it MUST NOT change persisted facts.
- The graph is the primary workspace. Controls and details MUST support graph
  reading without hiding the graph unnecessarily.
- Repeated information belongs in the most useful context once. Details panels
  provide depth; graph labels and list entries provide orientation.
- Visual semantics MUST remain stable when the theme, layout, node rendering
  mode, or viewport changes.

## Workspace shell

The HTML export MUST open on the Explorer with the architecture graph visible.
The workspace consists of grouped navigation, a compact control surface, a
graph area, contextual details, and an on-demand legend or inspector.

Navigation MUST keep graph views together and resource views together. The
graph group contains Architecture, Flux de code, and Diagnostics. The resource
group contains Ressources, OpenAPI, Messages, and Données. The current export
may expose additional contract and persistence views, but each view MUST have
one clear active state.

The legend is collapsed by default. The context widget is collapsed by default
for a selected call tree and can be opened with `Développer`. Secondary widgets
use the same surface, spacing, status, and interactive-row treatment as the
main details panel.

When the Flux or Quality view is active, resource navigation MAY be hidden to
preserve the analysis context. It MUST return when the user selects another
view.

The left navigation panel MUST provide a visible collapse control. Collapsing
it hides navigation content while retaining its header controls and leaves the
graph workspace unchanged. On constrained viewports, the panel uses one bounded
scroll region so the graph remains visible while users inspect details.

## Visual language

Microservices and resources use compact cards with a shared rendered geometry.
The current card envelope is 110 by 70 pixels. Type-specific styling MUST NOT
change card dimensions or cause placement to depend on label length.

Cards use a neutral interior and a type-specific coloured outline. The outline
communicates resource type and relative connectivity; the fill MUST NOT encode
connectivity or risk. Shapes distinguish microservices, Topics, Data, message
channels, and other resource categories.

The architecture graph keeps topology relations visible even when a relation
was not used as a placement edge. Isolated microservices remain visible in a
separate area so “no indexed dependency” is not confused with “missing”.

The same graph vocabulary MUST be used in architecture and selected-flow views:
cards, relation strokes, port labels, badges, selection halos, and protocol
colours retain their meaning. Architecture topology arcs and selected-flow
arcs use a fixed two-pixel screen stroke.

Light and dark themes MUST preserve contrast, hierarchy, protocol meaning, and
selection state. Theme changes MUST NOT alter indexed data, graph structure, or
selection.

## Navigation, selection, and inspection

Selecting a microservice or resource focuses its graph card and opens the
corresponding inspector. Relations in the inspector remain interactive and
open the target while preserving a breadcrumb path. The header back action
returns to the previous element; nested DTO and persistence-class views use
the same action and MUST NOT add a second in-body return control.

The inspector MUST show an explicit empty state when an indexed DTO, JPA, or
Mongo class has no fields. Source navigation is a distinct secondary action.
Actions for modules, source evidence, code flows, and resource focus remain
available after details are displayed in a modal.

Selecting a module title highlights that module and lists its currently visible
direct members in sorted order. Selecting one listed member returns to ordinary
node details. A module path is shown once in the Architecture section rather
than repeated in badges and raw metadata.

Filters MUST state their active effect and provide one action to clear the
additional filters. Hiding a resource removes its node and incident relations
from the current graph only. It MUST NOT modify indexed facts or source
configuration. Browser-local visibility preferences may persist for the export.

Search results MUST distinguish exact, ambiguous, invalid, repeated, and
unreachable requests. A rejected request leaves the current graph unchanged and
reports an actionable reason.

## Architecture graph views

The plain, module, and layered views are distinct projections of the same
architecture snapshot. Switching views MUST preserve facts while rebuilding
the visible projection and refitting the camera according to the active fit
mode.

Module rectangles MUST contain their visible cards, include title and padding,
and remain disjoint from sibling rectangles. A module may use multiple rows.
Within a module, microservices occupy the first placement sub-layer and
resources the second; this is conveyed by position and MUST NOT add synthetic
sub-layer labels.

Layered view uses bounded horizontal bands, shared left and right bounds, and a
visible title gutter. Bands are ordered by the canonical software-layer order,
with persistence at the bottom and external services in their dedicated
position. A module MUST be fully contained in its owning band.

Filtering, selection, zooming, resizing, and layout changes MUST recompute
container geometry. Stale modules, layers, coordinates, and overlays MUST be
cleared when the filtered graph is empty or no longer contains a previous
container.

## Camera and graph interaction

The graph area supports pan, bounded wheel zoom, zoom controls, centering, and
fit actions. `Tout le graphe` provides the complete overview. `Vue lisible`
may zoom further to keep fixed-size cards readable. Pan and zoom move the
camera, not graph nodes.

Wheel zoom MUST be handled once for the Sigma canvas and HTML overlays. Hovering
a card MUST NOT introduce a second native zoom behavior. Camera updates and
overlay updates are coalesced so canvas, cards, modules, and labels move as one
visual surface.

Compound views MUST keep cards and sibling module envelopes disjoint at the
active camera scale. Peripheral nodes may require panning. For graphs of at
most 12 nodes, readable fitting MUST retain a complete overview rather than
making the graph appear empty.

The desktop workspace begins at the window's left edge. The toolbar overlays
the upper-left graph area, while the usable graph surface begins below it.
Details-panel resizing MUST NOT reset the camera or create a clipping seam.

## Selected call tree

The call tree is the only call-graph rendering mode. It is an independent
hierarchical projection of one persisted flow and MUST NOT mutate the
architecture graph or persisted identifiers.

A service is rendered once per call occurrence. The same microservice may
therefore appear in several branches. Occurrences use local render identifiers,
while labels and actions resolve to the persisted microservice.

The tree exposes a global depth selector with 3, 5, and 8 levels and a
left-to-right or top-to-bottom orientation. The current default is depth 3 and
left-to-right. A branch stops at the configured depth. An input endpoint already
present on the current branch becomes a terminal cycle occurrence and is not
expanded. Reusing a service name with a different input port is not by itself a
cycle.

Nodes at the depth boundary expose the number of hidden calls. `+N appels`
expands one more level for that branch without changing the viewport. Expanded
nodes expose `− replier`; collapsed nodes expose `+N appels`. `Déplier tous les
nœuds` expands the visible tree using the current global depth limit.

The tree workspace supports drag-to-pan and bounded zoom from 0.5x to 4x.
Clicking a service occurrence keeps its selection action. The tree context
widget identifies the projection and controls without repeating the hierarchy.

Node tooltips MUST show the occurrence level, cycle state, microservice, and
available IN and OUT ports with their associated Java methods. Arc tooltips
MUST show order, source, target, protocol, indexed relation label, and message
type when available. Tooltip content is evidence already present in the
snapshot; it MUST NOT infer a missing fact.

The selected-flow heading identifies the projection without repeating a full
service or method sequence. Flow descriptions state the trigger and observed
effect. Catalogue entries stay compact, with descriptions limited to two lines
and statistics kept inline; complete evidence remains in the opened flow view.

## Ports, arcs, and tooltips

Inputs and outputs receive deterministic graph-wide identifiers: `I1`, `I2`,
and `O1`, `O2`. Inputs are distributed independently from outputs on opposite
card sides. A source-evidenced local flow may display `I4 → O3, O5`; the card
anchor remains the compact `I4` identifier.

Before a flow is selected, the architecture graph shows high-level cards and
persisted topology edges only. It MUST NOT display CodeQL ports, port-to-port
relations, or internal links by default.

In a selected flow, a port or arc can enter a transient analysis state. The
selected arc, label, and endpoint ports are highlighted. Clicking the same
target or pressing Escape clears the state; selecting another target moves it.
This MUST NOT change the selected flow, camera, or persisted facts.

Arc hit areas MUST be wider than their visible strokes. Selected arc layers are
raised above service cards so the route remains visible. Consecutive arcs that
leave the same service may highlight together while navigation still addresses
each persisted arc individually.

Port tooltips show identifier, direction, protocol endpoint, known Java type,
source-relative evidence path and line, associated method, and resolved REST
target when available. Arc tooltips show all mapped local outputs and both
service endpoints. Tooltips MUST remain limited to the hovered evidence and
MUST NOT add architecture facts.

## Call-flow catalogue and focused state

The Flux de code catalogue provides one entry for every available persisted
flow. Selecting an entry replaces the current tree; flows are not merged and
no side-by-side comparison is offered.

The catalogue may filter by confidence, protocol, Kafka message type, and cycle
status. A compact summary reports the visible count. Hovering or focusing an
entry exposes its complete description, trigger, service route, arc and step
counts, effects, protocol, confidence, and topology status.

Selecting a reconciled flow keeps the catalogue context available, shows the
tree, and displays a persistent banner with the selected flow and compact
evidence timeline. The entry microservice is marked `Racine`; the trigger and
protocol use explicit badges. These markers are presentation state only.

The focused tree is framed beside the toolbar on wide screens and below it
when that region is larger. Fit preserves readable fixed-size cards and margins,
does not zoom beyond the readable view, and reapplies after viewport resize.

## Responsive behavior and deferred concerns

At intermediate widths up to 1024 pixels, the left panel is limited to 320
pixels and 82 percent of viewport height. Its branded header remains visible.
Navigation uses a balanced four-column, two-row grid so destinations remain
visible without horizontal scrolling. Extended search hints may be hidden;
their label and example placeholder remain visible.

At constrained sizes, an empty context panel is hidden. Once it contains node
or path details, it replaces Explorer controls and starts at the top of its
single scroll region. Graph content remains visible outside that region.

ARIA semantics, keyboard navigation, and CDN dependency changes are outside
the current contract and remain deferred until explicitly reprioritized.

## Ownership and verification

The following documents provide the implementation detail for this contract:

- [HTML export behavior](spec-fonc/html-export.md) owns observable export
  states, inspectors, and flow catalogue behavior.
- [Port rendering](spec-fonc/port-rendering.md) owns endpoint evidence and
  port-to-port projection rules.
- [Layered view](spec-fonc/layered-view.md) and [module rendering](spec-fonc/modules.md)
  own architecture grouping invariants.
- [Placement and interaction](spec-fonc/placement.md) owns graph projection
  and interaction-specific behavior.
- [Graph layout algorithms](spec-tech/graph-layout.md) owns coordinate,
  camera, packing, complexity, and fallback mechanics.

Renderer tests cover card geometry, graph projections, tree expansion,
tooltips, camera synchronization, filters, and container containment. Browser
tests are the authority for interactions that depend on a real rendered
viewport.
