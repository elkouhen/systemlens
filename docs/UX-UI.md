# UX/UI rules

This document owns the UX/UI aspect: visible presentation, widgets, selection,
inspection, layout, camera behavior, and interaction semantics. Product scope,
domain contracts, and implementation algorithms belong to their respective
aspect documents.

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

Navigation MUST keep the two primary workspaces stable: Architecture and
Ressources. Architecture exposes Graphe and Flux de code. Ressources opens one
catalogue containing all indexed resource types, including Diagnostics; type
categories and filters refine that list without introducing a second top-level
navigation surface. Each workspace and local view MUST have one clear active state.
The visible view tabs of a group MUST share their row equally and never
truncate their label.

The legend is collapsed by default. On desktop, the call-tree context widget
floats over the top left of the tree while a flow is open; it always shows
its trigger, tree counts, and consistency counts, so it has no expand
control. At 720 pixels and below it returns to the panel, collapsed by
default: `Développer` opens it, `Réduire` closes it, and changing view
collapses it again. Secondary widgets use the same surface, spacing, status,
and interactive-row treatment as the main details panel.

When the Flux or Quality view is active, resource navigation MAY be hidden to
preserve the analysis context. It MUST return when the user selects another
view.

The left navigation panel MUST provide a visible collapse control. Collapsing
it hides navigation content while retaining its header controls and leaves the
graph workspace unchanged; floating graph widgets start right of the
collapsed header. On constrained viewports, the panel uses one bounded
scroll region so the graph remains visible while users inspect details.

At 900 pixels of width or less the panel starts collapsed, and collapses
again when the window narrows past that width. Expanded there, it opens as a
drawer over the graph: the graph keeps its full width and no band is
reserved for the panel. Widening past 900 pixels expands it again. The rules
at 720 pixels and below are unchanged.

The theme control reads `☾ Sombre` in the light theme and `☼ Clair` in the
dark one; Obsidian names the dark palette in this document only. The search
example names two indexed services, linked by a relation when possible, as
`a ou a -> b`; an export with fewer than two services shows `service-a ou
service-a -> service-b`.

A status line sits under the branded header in every view. It starts with
the index status, `Index : complet` or `Index : N faits à vérifier`, which
opens the diagnostics catalogue. It continues with the indexed relation
count and `indexé le <date>` when the export carries the index time; the
date is omitted, never guessed, for an export of an older index.

In the Graphe view, the panel holds the search block, then one
`Filtres et affichage` group with `Visibilité`, `Ressources masquées`, and
`Plus d’options`, then the layout status, a full sentence such as
`Disposition active : vue par graphe.`

Camera and rendering controls form one floating graph toolbar anchored at
the top left of the graph area, beside the panel: `Tout le graphe`,
`Vue lisible`, `Centrer`, the zoom group, `Cartes`/`Symboles`, `Légende`,
and `Réinitialiser` while a selection exists. The graph area reserves the
band under the toolbar, and the band above the interaction hint, so a fit
never places a card under either. At 720 pixels and below, the toolbar
returns to the panel under the search block.

The floating graph toolbar ends with a row of type chips: one chip per
node-type filter with nodes in the export (microservices, external
microservices, topics, collections, other resources). A chip counts the nodes
of its type that are not hidden individually, and its pressed state mirrors
the matching `Types de nœuds` filter; clicking it toggles that filter. The
strip is hidden with the graph, in the selected call tree, and below 721
pixels.

The `Types de nœuds` filters read `Microservices`, `Externes`, `Topics`,
`Données`, and `Autres`. The `Types de relations` filters read
`Appels d’API`, `Messages`, `Accès aux données`, and `Autres`, so the topic
node type and the message relation type never share a label.

The resource catalogue MUST present one searchable list across indexed
microservices and architecture resources. Its search control MUST remain above
the category list and results. Opening Ressources MUST initially show all
resource types; category selection and filters refine the same list without
changing the graph or persisted selection. Each row MUST expose the resource
kind and relationship counts when available. Selecting a row opens its docked
preview; `Inspecter` opens the shared inspector for deeper details.

The catalogue title carries the visible and total counts once. Each category
shows its own count; an empty category stays listed but is disabled, and an
empty `Diagnostics` category reads `aucun diagnostic`. A count that does not
fit beside its label moves below it, aligned right. Rows are ordered by kind
(category order), then owner, then name. A row's height follows its content
in every layout, including the docked layout that moves the context under the
name.

Each row opens with a kind tag coloured like the graph type chips; a route
also shows its HTTP verb as a badge before the path. The Contexte column
reads `owner · detail` without repeating the kind; a node row details its
relation count, a served route its distinct HTTP clients. A zero count reads
as a muted `aucun client` or `aucune relation`, never `0 client`.

Every row offers `Inspecter`, or the muted label `Aperçu seul` when no
dedicated inspector exists. `Inspecter` appears on row hover, on keyboard
focus, and on the selected row; a click on the row itself opens the preview.
On a device without hover the action stays visible.

The catalogue page is a column bounded to 1180 pixels and centered. The
mode tabs keep their panel width, and the collapse control stays next to the
brand in the panel header.

For a DTO, JPA entity, or Mongo persistence class, the preview shows indexed
attribute names and types. A Mongo collection shows fields grouped by its
indexed persistence classes. Missing fields receive an explicit empty state.

When a DTO field has an indexed reference to another DTO, its type opens that
DTO in the same preview. Each resolved candidate is a separate choice. The
back action names the previous resource and returns to its attributes.

The preview lists directly related indexed resources as selectable links.
Choosing one selects its catalogue row and updates the preview. If filters hide
the target, the catalogue clears those filters to make the selected row visible.
The preview provides a back action for the previous resource. Closing the
inspector preserves the catalogue selection and its navigation path.

## Widget inventory and shared contract

A widget is a bounded interface surface that owns one user task. Widgets MUST
use the same visual grammar for their title, controls, body, status, empty
state, and actions. A widget MUST NOT duplicate another widget's complete
content or hide a resource inspector inside a different widget.

Each widget MAY contain the following regions, in this order when present:

- **Header:** title, optional context label, and the primary close or collapse
  action.
- **Controls:** filters, view selectors, search, or contextual actions that
  affect the widget body.
- **Body:** the graph, list, summary, evidence, or analysis content owned by
  the widget.
- **Footer:** secondary actions, source navigation, counts, or status details.

The header identifies the widget's subject once. Controls use the shared
segmented-control and button treatment. Lists use the same row height, padding,
hover treatment, selected treatment, and status-pill vocabulary. Empty and
error states explain what is unavailable and what the user can do next.

### Widget families

| Widget | Purpose | Required content | Interaction contract |
| --- | --- | --- | --- |
| Navigation widget | Switch between graph, flow, resource, contract, and diagnostics views. | View groups, active view, and collapse control. | Changes the workspace view and clears incompatible transient details. It does not contain resource facts. |
| Graph workspace widget | Provide the primary visual reading surface for architecture or one selected call tree. | Graph canvas, rendered nodes, relations, camera controls, and view-specific controls. | Pan, zoom, fit, and selection affect the current projection only. The graph remains visible while supporting widgets change. |
| Graph context widget | Expose the controls and summary needed to interpret the current graph. | Search or filters, layout and display actions, counts, and the active selection summary. | May be collapsed. Actions change the projection or transient state without duplicating the graph hierarchy. |
| Resource catalogue widget | Browse indexed topics, Mongo collections, routes, contracts, or persistence resources. | Search or filters, compact rows with a kind tag, a Contexte column in `owner · detail` order, attributes when indexed, and one total count in the title. | Selecting a resource updates the docked preview and its related-resource links. `Inspecter` opens the shared inspector; a row without inspector says `Aperçu seul`. Resource details MUST NOT expand inline in the result list. |
| Details widget | Show the selected graph element's immediate context and available analysis actions. | Identity, summary, relations, evidence, and actions for focus, modules, code flows, or source navigation. | It is contextual and may be hidden when an introspection window opens. It does not compete with the inspector identity header. |
| Introspection widget | Inspect one microservice, topic, route, collection, DTO, entity, or other indexed resource. | Identity header, breadcrumb from the second level, back and close actions; for a node, a four-tile relation summary, one row of secondary actions, and counted disclosure groups. | From 901 pixels, node, route, and HTTP call views dock to the right edge beside the graph; other views, and every view at 900 pixels or less, open as a centered modal. Related resources remain selectable and update the same inspection history. Each fact appears in one section only. |
| Flow catalogue widget | Select a persisted code flow or flow group for call-graph analysis. | A one-line kicker and the title with its count, compact flow rows, description, trigger, protocol, status, and summary statistics. | Selecting a row replaces the selected flow graph without moving the list. Full details are shown in the selected-flow view, not in every row. |
| Call-tree context widget | Explain and control the selected hierarchical call graph. | Flow identity, trigger event, visible tree node and edge counts, consistency diagnostic counts, the key of the access annotations, depth, expand-all, and tree-specific status. | Floats over the tree on desktop and shows its summary at all times; collapsed in the panel at compact widths and opened with `Développer`. It controls the tree without repeating its nodes or edges. Tree counts describe the visible projection and update when depth or expansion changes. Diagnostic counts cover the selected flow and distinguish possible risks, insufficient evidence, identified guarantees, and possible interactions. |
| Tooltip widget | Provide concise evidence for a hovered node, edge, row, or control. | Subject identity, relevant relation facts, and available status or type. | Appears on hover or focus and does not become a second navigation surface. It MUST NOT infer missing facts. |
| Legend widget | Explain stable graph colours, shapes, strokes, and badges. | Collapsible groups for node types, relations, connectivity, and evidence level, with one single-line entry per visual semantic. | Collapsed by default. When open, it reserves its footprint so no fitted card lies under it. It explains the graph and does not replace node or edge tooltips. |
| Analysis status widget | Report a transient selected port or edge and its related evidence. | Selected endpoint or edge, protocol, related resources, and a clear action. | Highlights the current analysis state without changing the graph data, camera, or persisted selection. |

### Shared states and visual rules

All widgets MUST support the states that apply to their content using the same
visual vocabulary:

- **Default:** the widget is available without an active selection.
- **Hover or focus:** the target is discoverable and has not been selected.
- **Selected:** the target drives the widget or graph context and remains
  visibly distinct from hover.
- **Expanded:** additional content is visible after an explicit disclosure
  action. The widget retains its identity and position.
- **Empty:** no matching or indexed content exists; the state explains why.
- **Unavailable:** an action cannot be applied in the current context and is
  visibly disabled rather than silently ignored.

Widget surfaces MUST use the shared border, radius, background, text, muted
text, focus, and selection tokens. Type or protocol colours MAY remain local
only when they encode graph semantics or an indexed status. Titles, controls,
rows, tags, and action placement MUST remain consistent across widget families.

Widget text uses the `--type-*` tokens, from `--type-caption` at 10 pixels to
`--type-entity-title`. No widget text is smaller than 10 pixels, including
card kickers, badges, and tooltips. Glyph-only controls MAY use larger sizes.

An interactive resource has one selection contract wherever it appears. In the
Architecture view, resource nodes and Topic relation edges are selectable;
HTTP and Mongo relation edges remain tooltip-only. In the selected call tree,
visible nodes and supported HTTP or Kafka edges expose their analysis or
inspection action. A tooltip MUST never suggest that an element is selectable
when the current widget does not support that action.

When a selection opens an introspection widget, the originating catalogue or
details widget keeps its state but does not render a second full inspector
inside itself. The introspection header owns the resource identity, breadcrumb,
back action, and close action. This rule applies equally to microservices,
topics, routes, Mongo collections, DTOs, JPA entities, and persistence classes.

## Visual language

Microservices and resources use compact cards with a shared rendered geometry.
The current card envelope is 110 by 70 pixels. Type-specific styling MUST NOT
change card dimensions or cause placement to depend on label length.

Cards use a neutral interior and a type-specific coloured outline. The outline
communicates resource type and relative connectivity; the fill MUST NOT encode
connectivity or risk. Shapes distinguish microservices, Topics, Data, message
channels, and other resource categories.

The legend groups its entries into node types, relations, connectivity, and
evidence level. Node types use 14-pixel shapes that match the card outlines.
Relation samples use the strokes actually drawn in the current theme. The
connectivity group shows the three outline colours, and the evidence group
defines the terms shown in node details without a colour of its own.

The architecture graph keeps topology relations visible even when a relation
was not used as a placement edge. Isolated microservices remain visible in a
separate area so “no indexed dependency” is not confused with “missing”.

The same graph vocabulary MUST be used in architecture and selected-flow views:
cards, relation strokes, port labels, badges, selection halos, and protocol
colours retain their meaning. Architecture topology edges and selected-flow
edges use a fixed two-pixel screen stroke. Architecture edges display an
arrowhead just before the target node so their direction remains visible when
cards overlap the underlying graph layer.

Light and dark themes MUST preserve contrast, hierarchy, protocol meaning, and
selection state. Theme changes MUST NOT alter indexed data, graph structure, or
selection.

The OpenAPI and AsyncAPI viewers follow the interface theme. In the dark theme,
their surfaces, text, borders, and form controls use the `--ui-*` tokens, while
HTTP method colours, protocol badges, and code highlighting keep their own
palette. The light theme keeps each viewer's original colours.

Obsidian is the default dark presentation for the architecture workspace.
It uses a graphite canvas (`#0b1018`), slate panels (`#101823`), neutral cards
(`#131e2b`), and a glacier-cyan interaction accent (`#67e8f9`). A subtle dot
grid provides orientation without competing with graph relations.

Navigation, catalogues, tooltips, and inspectors share these surfaces and
compact rounded controls. Cyan identifies interface selection and actions;
resource outlines, protocol strokes, and warning states retain their semantic
colours. Evidence uses a monospace font on a recessed surface.

The workspace opens in Obsidian when no valid theme preference exists,
regardless of the operating-system theme. An explicitly saved light or dark
preference takes precedence and survives reopening an export where browser
storage is available. The light presentation remains selectable.

## Navigation, selection, and inspection

Opening the dedicated introspection window for a microservice, Topic, Mongo
collection, or any other indexed resource requires `Shift + click` on the node
or supported relation. A plain click on an Architecture node selects it: the
details widget shows the node, its incident relations gain emphasis, and
unrelated nodes dim. A second plain click on the selected node, or a click on
the empty canvas, clears the selection. A plain click never opens the
introspection window and never changes persisted facts. A plain click on a
relation does nothing. The introspection MUST NOT render as a nested
sub-widget inside the left navigation widget.

The graph area shows a permanent interaction hint docked at its bottom left:
`clic` selects, `⇧ clic` inspects, the wheel zooms, dragging pans. The hint
is hidden with the Architecture graph, in the selected call tree, below 721
pixels of width, at 700 pixels of height or less, and while the inspector
is docked. It never overlaps the minimap or the legend. Relations in the
introspection window remain interactive and open the target while preserving
a breadcrumb path. The header back action
returns to the previous element; nested DTO and persistence-class views use
the same action and MUST NOT add a second in-body return control.

From 901 pixels of width, the inspector of a node, an HTTP route, or an HTTP
call docks to the right edge as a full-height panel (ADR-68). The graph stays
interactive, its right workspace band reserves the panel width, the legend
moves beside the panel, and the interaction hint hides. If the inspected card
sits under the panel when it opens, the camera centers on it. The inspected
node carries a halo while the panel shows it. Contract, schema, DTO, entity,
and runtime views stay a centered modal over a blurred graph; at 900 pixels
or less every view uses the modal. `Escape`, the close action, and a click on
the empty canvas close the inspector.

A node inspector opens with a strip of four tiles: `Relations`, `Prouvées`,
`Inférées` (conventional relations included and named), and `Sans niveau de
preuve`. The tiles sum to the relation total and replace the evidence badges
of the details header. One row follows with `Centrer dans le graphe`, `Flux
de code (N)` when the node has associated flows, and the VS Code action.

Each group of the node inspector is a disclosure group whose summary shows
its item count. The first three non-empty groups open; a group the widget
defines but leaves empty stays listed with a zero count and cannot open.
`Routes exposées` lists a method and path once; when a controller and an
OpenAPI file both declare it, the row lists each source with its location.
The persisted endpoints are unchanged.

The inspector MUST show an explicit empty state when an indexed DTO, JPA, or
Mongo class has no fields. Source navigation is a distinct secondary action.
Actions for modules, source evidence, code flows, and resource focus remain
available after details are displayed in the inspector.

Selecting a module title highlights that module and lists its currently visible
direct members in sorted order. Selecting one listed member returns to ordinary
node details. The Architecture section names the layer `Couche`. A module
path is shown once in the Architecture section rather than repeated in badges
and raw metadata. The project root module carries no navigable identity and
is not listed.

The introspection title names the current element; its breadcrumb appears only
after the user has navigated to a related element. A microservice lists every
outgoing HTTP call once, under `Routes appelées`; a call without a matching
route remains selectable as an unmatched call. The relation badges sum to the
displayed relation count; relations without an indexed evidence level are
counted as `N sans niveau de preuve`.

Filters MUST state their active effect and provide one action to clear the
additional filters. Hiding a resource removes its node and incident relations
from the current graph only. It MUST NOT modify indexed facts or source
configuration. Browser-local visibility preferences may persist for the export.

Search results MUST distinguish exact, ambiguous, invalid, repeated, and
unreachable requests. A rejected request leaves the current graph unchanged and
reports an actionable reason.

## Architecture graph

The architecture graph shows the indexed resources and relations in one graph
view. It does not draw software-layer bands or module containers. A resource's
module remains available in its details, and module details support hierarchy
navigation without switching graph layout. The software layer is a module
property (PDR-8): the inspector of a topic or a collection shows no `Couche`
row, while a microservice shows it when its layer is known.

Filtering, selection, zooming, resizing, and layout changes MUST keep graph
coordinates and node overlays synchronized. Empty or filtered graphs MUST clear
stale node cards and paths.

Changing a relation-type filter rebuilds and relayouts the visible graph using
only the selected dependency types. Excluded relations do not influence the
resulting placement.

Architecture cards keep their fixed screen-space geometry while the camera
moves. When rendered card bounds overlap, the affected cards hide their type
kicker while retaining the resource name and icon. Hovering or selecting a
crowded card restores its type kicker. The card inspector and tooltip retain
the complete resource details.

Selecting an architecture node emphasizes its incident relations and the
relations between its visible neighbors. Other visible relations remain
present at reduced emphasis. Clearing the selection restores their usual
weight. REST and Kafka relations keep distinct stroke patterns and colors.

## Camera and graph interaction

The graph area supports pan, bounded wheel zoom, zoom controls, centering, and
fit actions. `Tout le graphe` provides the complete overview. `Vue lisible`
may zoom further to keep fixed-size cards readable. The pressed state of
these two buttons shows the active fit mode. `Légende` opens or closes the
legend and mirrors its state. Pan and zoom move the
camera, not graph nodes.

The overview minimap appears for architecture graphs with more than 24 visible
nodes or with nodes outside the workspace. Once the user pans or zooms, by
gesture, zoom control, or minimap, it stays visible until the next fit, even
when every node is back on screen (PDR-6). It shows visible node positions and
the current viewport. Clicking or dragging it centers the camera on the
pointed position at the current zoom, without changing selection. Node
positions stay fixed in the minimap while the viewport rectangle moves. The
minimap samples at most 700 nodes.

The minimap MUST NOT overlap the legend, open or closed. It sits to the left of
the legend, or above it when the viewport is too narrow.

An open legend reserves its footprint in the graph workspace. It reserves a
right column when at least 560 pixels of graph width remain beside it, and a
bottom band otherwise. Opening or closing it refits the graph once. Below 721
pixels it reserves nothing and stays below the left panel.

Wheel zoom MUST be handled once for the Sigma canvas and HTML card overlays. Hovering
a card MUST NOT introduce a second native zoom behavior. Camera updates and
overlay updates are coalesced so canvas, cards, and labels move as one
visual surface.

A drag on the empty stage, on a card, or on the bare background of the bands
reserved for floating widgets moves the content exactly with the pointer, at
any zoom and any workspace aspect ratio. One wheel notch zooms by the same
step, about 1.2 times, whether the pointer is over the canvas, a card, a
relation edge, or a reserved band. The zoom keeps the graph point under the
pointer fixed. Controls, the legend, and the minimap keep their own wheel
behavior.

For graphs of at most 12 nodes, readable fitting MUST retain a complete
overview rather than making the graph appear empty.

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

Each occurrence card MUST expand to contain all of its data-operation and
diagnostic annotations. Cards with fewer annotations keep their own smaller
height. Visible branches MUST remain separated, with relation edges attached to
the rendered card centers.

The `+N appels` count belongs to the occurrence, not to the microservice name.
It MUST be derived from the occurrence's incoming endpoint and associated flow,
so two occurrences of the same microservice MAY display different hidden-call
counts.

The tree uses the node background as a recurrence scale for microservices:
neutral for one occurrence, light accent for two, medium accent for three, and
strong accent for four or more. The border continues to identify the node type.
The node tooltip MUST state the exact recurrence count.

Each occurrence shows its short Java method beneath the service name when
method evidence is available. The method line MUST remain visually distinct
from the service name and MUST retain its qualified name in the title. Long
names may be clipped to the card width.

Each occurrence MUST show only the IN and OUT ports referenced by the selected
flow. Ports appear as unlabelled markers on the left or right edge of the card;
their position conveys direction. Hovering a port shows direction, endpoint
details, and source information without its port number.
Each call edge MUST begin at the rightmost point of its source OUT port and end
at the leftmost point of its target IN port.

The call-tree context widget MUST summarize consistency diagnostics for the
selected flow. It MUST show counts for possible risks, insufficient evidence,
identified guarantees, and possible interactions, or state that no diagnostic
was detected. Counts MUST exclude diagnostics belonging to other flows.

A key docked at the bottom left of the tree, open by default and remembered
per viewer, decodes the access annotations of the cards (inside the
expanded widget at compact widths): `C`,
`R`, `U`, and `D` for create, read, update, and delete, `(C/U)` for an
undecided create or update, and `?` for an association with the handler whose
internal route is not established. A handler-only badge also has a dashed
outline. A badge tooltip MUST state the technology, guard, and transaction
of its operations as evidence or as not established, never as a guess.

The card MUST omit the repeated `Microservice` type label. An unknown
continuation MUST retain its `Suite inconnue` status; cycles and hidden calls
use their existing badges.

For a Strategy1 Kafka reception, the method block MUST show the signature of a
unique same-class `consume` method when its parameter types are indexed. A
persisted call edge may disambiguate overloaded `consume` methods. If the method
remains ambiguous, the block retains the annotated listener signature.

Hovering or focusing an occurrence MUST highlight its path from the root and
dim unrelated branches. Leaving the occurrence or moving focus MUST restore the
full tree. This emphasis is transient and MUST NOT change selection or the
persisted graph.

The tree uses a fixed left-to-right orientation and an incremental depth control
from 1 to 8 levels. The current default is depth 1, showing only the root
service. The `−` and `+` controls
change one level at a time. A branch stops at the configured depth. An input
endpoint or flow already present on the current branch becomes a terminal cycle
occurrence and is not expanded. A reachable microservice without an indexed
consumer flow remains a terminal occurrence labelled `suite inconnue`. Reusing
a service name with a different input port is not by itself a cycle.

Nodes at the depth boundary expose the number of hidden calls. `+N appels`
expands one more level for that branch without changing the viewport. Expanded
nodes expose `− replier`; collapsed nodes expose `+N appels`. `Déplier tout
l’arbre` expands every branch to the maximum depth and refits the tree like
`Tout voir`, because the tree extent changes. Changing the depth with `−` or
`+` refits the tree the same way. The selected occurrence stays selected.

The floating widget shows only the actions available now: `Sélection`
appears once an occurrence is selected, and a disabled action is hidden
rather than greyed. The tree area reserves the band under the widget and
the band above the docked key, so a fit never places a card under them.

The tree workspace supports drag-to-pan and bounded zoom from 0.5x to 64x.
The initial fit keeps a minimum canvas scale of 0.66 so deep trees do not shrink
cards without bound. At a total scale below 0.82, method parameters and operation
badges become visually quiet while service names and diagnostic borders remain
visible; hovering retains complete evidence in the tooltip. Clicking an
occurrence selects it and preserves that occurrence's screen position when the
tree changes. `Tout voir` fits the visible tree. `Sélection` recenters on the
selected occurrence. The tree context widget identifies the projection,
displays the indexed trigger event, and controls the tree without repeating the
hierarchy.

The call-tree minimap appears when more than 12 occurrences are visible, the
total scale is below 0.82, or the tree exceeds the workspace by 15 percent.
After a user pan or zoom it stays visible until the tree refits or another
flow opens (PDR-6). It shows occurrence positions and the current viewport. Clicking or dragging it
moves the tree camera while preserving the selected occurrence. It samples at
most 700 occurrences.

Edge labels in the tree MUST use a compact form that retains the order when
available, the relation label, and the protocol. Long relation labels may be
truncated on the canvas. Label placement MUST avoid cards and other visible
labels; when no clear position exists, the renderer hides that label while the
edge and its tooltip remain available. The selected occurrence path receives
label-placement priority. The edge tooltip MUST retain the full indexed details.

Node tooltips MUST show the occurrence level, cycle state, microservice, and
Java method when available. They MUST omit IN and OUT port inventories. Port
tooltips MUST show direction, endpoint details, source, and associated Java
method without displaying the port number.
Edge tooltips MUST show order, source, target, protocol, indexed relation label,
and message type when available. Tooltip content is evidence already present
in the snapshot; it MUST NOT infer a missing fact.

The selected-flow heading identifies the projection without repeating a full
service or method sequence. Flow descriptions state the trigger and observed
effect. Catalogue entries stay compact, with descriptions limited to two lines
and statistics kept inline; complete evidence remains in the opened flow view.

## Ports, edges, and tooltips

Inputs and outputs receive deterministic graph-wide identifiers: `I1`, `I2`,
and `O1`, `O2`. Inputs are distributed independently from outputs on opposite
card sides. A source-evidenced local flow may display `I4 → O3, O5`; the card
anchor remains the compact `I4` identifier.

Before a flow is selected, the architecture graph shows high-level cards and
persisted topology edges only. It MUST NOT display CodeQL ports, port-to-port
relations, or internal links by default.

In a selected flow, a port or edge can enter a transient analysis state. The
selected edge, label, and endpoint ports are highlighted. Clicking the same
target or pressing Escape clears the state; selecting another target moves it.
This MUST NOT change the selected flow, camera, or persisted facts.

Edge hit areas MUST be wider than their visible strokes. Selected edge layers are
raised above service cards so the route remains visible. Consecutive edges that
leave the same service may highlight together while navigation still addresses
each persisted edge individually.

Port tooltips show identifier, direction, protocol endpoint, known Java type,
source-relative evidence path and line, associated method, and resolved REST
target when available. Edge tooltips show all mapped local outputs and both
service endpoints. Tooltips MUST remain limited to the hovered evidence and
MUST NOT add architecture facts.

## Call-flow catalogue and focused state

The Flux de code catalogue provides one entry for every available persisted
flow. Selecting an entry replaces the current tree; flows are not merged and
no side-by-side comparison is offered.

The catalogue header is a one-line kicker, `Flux potentiels, pas une trace
d’exécution`, and the title with its count. Without an active filter, the
filter summary is muted text rather than a control. The tree controls live in the
floating widget over the tree, so opening a flow does not move the list
under the pointer.

While the catalogue is open and no flow is selected, the graph area shows an
empty state instead of a blank canvas. Its card repeats the kicker, is titled
`Aucun arbre d’appel sélectionné`, explains that selecting an entry point
unfolds the reachable HTTP and Kafka calls with their evidence level, and
lists the catalogue's flows as shortcuts in catalogue order: trigger kind or
HTTP verb, trigger name, service chain, step count, and the number of
diagnostics to verify (risks and insufficient evidence). At most eight
shortcuts are listed; a footer names how many more the catalogue holds.
Choosing a shortcut selects the same flow as the catalogue entry. The empty
state disappears as soon as a flow is open and returns when the selection is
cleared.

The catalogue may filter by confidence, protocol, Kafka message type, and cycle
status. A compact summary reports the visible count. Hovering or focusing an
entry exposes its complete description, trigger, service route, edge and step
counts, effects, protocol, confidence, and topology status.

Searching for `incohérence` or `incoherence` filters the catalogue to flows
with any possible consistency risk: `potential_risk`,
`potential_concurrent_write`, `potential_lost_update`, `potential_dirty_read`,
or `potential_read_write_anomaly`. Those entries show an `Incohérence possible`
badge. Insufficient evidence and `potential_interaction` remain separate states
and MUST NOT match this search.

Selecting a reconciled flow keeps the catalogue context available, shows the
tree, and displays a persistent banner with the selected flow and compact
evidence timeline. The entry microservice is marked `Racine`; the trigger and
protocol use explicit badges. These markers are presentation state only.

The focused tree is framed beside the toolbar on wide screens and below it
when that region is larger. Fit preserves readable fixed-size cards and margins,
does not zoom beyond the readable view, and reapplies after viewport resize.

## Runtime overlay

Aggregated APM traffic is an overlay on the architecture graph, on at load
and offered in the legend only when the export holds observations. It MUST
read without hover: counts are written on edges, and cards state entry-point
coverage rather than raw volume, which health probes dominate. Toggling it
MUST NOT move cards, the camera, or the selection, and turning it off MUST
restore the static rendering exactly.

Runtime annotations decorate existing edges, cards, tooltips, and details.
They MUST NOT draw a relation that the index lacks; unexplained traffic is
listed in the runtime gaps inspector. A silent edge stays distinguishable from
REST dashes. A missing service telemetry badge is neutral, not a warning.
Tooltip runtime lines are the one exception to evidence-only tooltips: they
state counts as sampled lower bounds. In a selected call tree, cards show
the count of their own entry point. Edge tooltips keep emitted and received
counts apart, because neither measures one call site. The flow toolbar
mirrors the legend toggle, which is hidden outside the graph tab.

The runtime legend states completeness before counts: one line per source
with its interval and its overflow or truncation state, written explicitly
when there is none. The hotspot inspector is a ranked list, not a chart;
its rows MUST select through the same path as a route consumer link, so
selection, inspector history, and camera behave identically. A row of an
overflowed service MUST read "incomplet" next to its value, and a missing
percentile MUST be shown as absent rather than as zero.

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
- [Module membership and navigation](spec-fonc/modules.md)
  own architecture grouping invariants.
- [Graph layout algorithms](spec-tech/graph-layout.md) owns coordinate,
  camera, packing, complexity, and fallback mechanics.

Renderer tests cover card geometry, graph projections, tree expansion,
tooltips, camera synchronization, filters, and container containment. Browser
tests are the authority for interactions that depend on a real rendered
viewport.
