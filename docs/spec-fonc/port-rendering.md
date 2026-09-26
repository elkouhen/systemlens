# Port-to-port interaction rendering

Parent: [Functional specification](../SPEC-FONC.md).


The Explorer renders a microservice as one compact rectangle with its numbered
input ports (`I<n>`) distributed along the left side and its numbered output
ports (`O<n>`) distributed along the right side. A source-evidenced internal
flow draws its local `I<n> → O<n>` relationship inside that rectangle. When
one input reaches several local outputs, it draws one directed relationship to
each output.

When a persisted REST or Kafka topology edge has both a source endpoint and a
resolved target endpoint, the export draws a directed port-to-port path from
the source `O<n>` to the target `I<n>`. For Kafka, the static architecture graph
represents the interaction only through the topic resource:
`microservice → topic → microservice`. It does not add a direct
microservice-to-microservice arc. Kafka topics remain visible as indexed
resources; these paths are readable projections of the same evidence, not
replacements for the topic relation. Several ports on one side are distributed
deterministically to avoid overlap. Cycles remain visible as directed return
paths. An unresolved target, dynamic topic, or ambiguous route MUST NOT create
a port-to-port path. The corresponding endpoint evidence remains visible as a
partial topic relation when it can be represented without pairing it to
another service. Hovering a port displays its identifier, direction,
protocol endpoint, statically inferred Java parameter/message type when known,
source-relative evidence path and line, associated Java method and, for a resolved REST call, its
resolved target; an external caller is never presented as an input's output.
Arc tooltips carry every mapped local output (`O<n>`) and the source/target
services; their `Port IN` and `Port OUT` headings identify the direction, so
the topic or route is shown only once. Port tooltips remain limited to the
endpoint itself. The
tooltip does not add or infer any architecture fact.
In a selected flow view, clicking a port or an arc enters transient
analysis mode. The selected arc, its label, and its endpoint ports are
highlighted; clicking the same port or arc clears the focus, and clicking
another port or arc moves it. This interaction does not change the view,
selected flow, camera, or persisted facts.
The selected call graph displays only its referenced IN and OUT ports by
default. The analysis context provides an `Afficher tous les ports` control to
show every indexed port on the services in the selected graph. Returning to a
different flow restores the referenced-port default; this control changes only
the rendered view and does not change persisted facts.
The visible arc remains visually thin, but its interactive hit area is wider so
that selecting an arc remains usable in a dense graph.
Its primary card title is the input trigger, while the Java method remains
visible as source evidence. Flow selection reconciles every integration step
only through its persisted endpoint identifier: route labels and resource names
are never used to choose an edge. A local input-to-output port relation is
reconciled evidence even though it has no inter-node topology edge. If an
endpoint is absent, unresolved, or maps to more than one displayed relation,
the card explicitly marks the graph view as having partial edges rather than
presenting it as a verified path.
For a REST input declared only by OpenAPI and implemented through a generated
interface, the port can display its unique same-module Java `@Override` method
on a `@RestController` when its name exactly matches the contract
`operationId`; otherwise the method remains unknown.
Detected CodeQL call cycles and concrete Kafka topic cycles are retained as
potential `cycle` flows. They are visually distinguished and listed before
non-cyclic flows; within each category, flows traversing more distinct
microservices appear first. The service count comes from the persisted
call-graph node order when available, with endpoint evidence as a fallback.
The Flux tab provides a `Cycles only` control with the detected-cycle count to
isolate them immediately.
Explorer path selections are ephemeral. URL fragments such as `from`, `to`,
`via`, and `lock` are ignored and never alter the initial graph rendering.
Selecting a microservice distinguishes two flow views. `Flux associés` remains
visible and lists every persisted flow owned by the service or traversing one
of its indexed ports; each compact entry identifies its trigger, length, cycle
status and Java method, and can display that flow in Explorer. `Flux internes`
is a separate collapsed disclosure by default: it contains only the potential
local input-to-output paths inferred within that service, so the primary
inspector remains focused on topology. Associated-flow entries provide actions
to open the Flux tab, highlight the flow in Explorer, or open its persisted
Java method evidence in VS Code when the export has a resolvable source root.

The HTML microservice export provides an inspector for each statically typed
Topic message. It shows the indexed payload-type identity, message topic, and
producer and consumer services. When the matching Java type is indexed, its
inspector also shows its source, declared fields, enum values, and conservative
recursive project-type navigation.

The HTML export opens on the Explorer tab with the global architecture graph.
Before a flow selection it shows only the high-level node cards and
persisted topology edges: it does not show CodeQL input/output ports,
port-to-port relations, or internal links. Hovering a microservice, topic,
collection, or topology arc shows a concise contextual tooltip; the Flux tab
presents a compact list of persisted potential flows whose endpoint
evidence spans at least two microservices, grouped by trigger type; it
does not show method, confidence, or status details before selection. A flow
remains listed when one of its topology edges is unresolved or absent from
the export; selecting it marks the graph as partial instead of hiding the
persisted interprocedural evidence. Flows confined to one microservice remain
excluded from the default list. Each flow
card also displays the ordered microservice names from its persisted call graph
when available, or resolves them from indexed endpoint identities as a fallback.
The flow list is ordered by descending number of distinct microservices
traversed, then by descending number of call-graph arcs, then by flow length.
This diagnostic text belongs to the widget and does not change graph path
rendering. The Flux tab
provides a
scope selector with `Inter-services`, `Tous les flux`, and `Flux internes`.
`Flux internes` isolates persisted flows with distinct input/output endpoints
belonging to the same microservice; `Tous les flux` additionally includes
flows whose topology cannot be fully reconciled.
The Flux tab also provides independent filters for confidence (`élevée`,
`moyenne`, `faible`), protocol (`HTTP`, `Kafka`, `Mixte`), and Kafka message
type. The message-type field offers native autocomplete values from the
indexed Kafka ports and accepts partial text matching. A compact
summary reports the number of visible flows and each card summarizes its
service sequence, effects, confidence, reconciliation status, and alternative
route count. The filter area reports the active filters and provides one
action to restore the default scope and clear every additional filter. A
selected flow can be recentered from the analysis banner.
Equivalent persisted routes that render the same interaction graph are grouped into
one visible flow, preventing duplicate graph cards while retaining their count
in the export model.
Selecting a reconciled flow outside the Flux de code tab opens
 the Explorer tab and displays only the microservices involved in the path,
 their indexed ports, and the direct dependencies between those ports. Topics
 remain available in the selected service's badges and tooltips, but are not
rendered as nodes in this focused view. The focused view uses graph levels and
vertical offsets for sibling branches, so a branched interaction graph is presented
as a tree/DAG rather than as a misleading single lane. The
exported flow graph always has one visible root. When the indexed evidence has
several incoming producers, the entry service remains the root; when it
contains a cycle, the return arc is omitted from this visual projection so the
graph remains navigable as an arborescence. The persisted flow and its cycle
status are not altered. The
selected nodes retain a visible
halo. Selecting it keeps the Flux tab and
its card geometry unchanged; the selected card is marked in place instead of
replacing the left widget with the graph detail view. Its entry microservice is marked `Racine`: in Cards, it appears in the secondary label; in Symbols, it appears as a badge. A second badge makes the persisted entry trigger explicit, with the exact HTTP route or Kafka topic and a protocol-specific color. These markers are presentation state derived from the selected persisted flow graph; they do not infer or persist an architecture fact. The camera frames the selected flow inside the visible
workspace beside the toolbar on wide screens and below it when that is the
larger available region, while preserving margins for fixed-size cards. This
fit never zooms in beyond the current readable view, is reapplied after a
viewport resize, and cannot make the toolbar scroll horizontally.
When a flow is selected from the Flux de code tab, that tab remains active so
the user can select another flow directly. The selected flow still updates the
Explorer graph and its analysis state; opening the Explorer tab remains
available through the normal tab control.
The export keeps the architecture graph and the selected call graph as
explicitly separated view modes. Opening Graphe selects the architecture mode
and rebuilds only the persisted topology projection. Opening Flux de code
selects an empty mode until a flow is chosen; selecting a flow activates the
call-graph mode without changing the selected Flux de code tab. A layout or
selection change in one mode MUST NOT reuse the node, edge, port, or call-graph
selection state of the other mode.
Architecture topology arcs and selected call-graph arcs use the same fixed
2-pixel screen thickness; graph zoom compensation MUST NOT make one view's
arcs appear thinner or thicker than the other's.
When a selected flow occupies less space than the available focus area, its
specific framing may zoom in (a camera ratio below the overview ratio) so the
flow remains readable; the ratio is bounded and the cards remain inside the
visible margins.
The selected interaction graph's service-to-service arcs are rendered as orthogonal
straight segments between the actual output and input ports. ELK.js keeps the
node placement while `@mr_mint/elkjs-libavoid` computes obstacle-avoiding routes
around the visible microservice cards, with a padding margin and nudging for
parallel dependencies. A local orthogonal router is used only as a runtime
fallback when the external WASM module cannot be loaded.
Endpoint-to-endpoint dependencies are projected into the selected
service-to-service arc. The selected flow's canonical NetworkX arcs are the
sole source for the focused overlay, so topology links and port links cannot
create a second copy of the same dependency.
Each selected arc displays its port mapping in the form `Ox → Iy`, using the
actual indexed output and input labels.
The arc label uses the same protocol-specific color as its associated arc in
both light and dark themes.
In a selected flow graph, clicking an input or output port enters a transient
analysis mode: the associated topology or local flow arc and its label are
highlighted. Clicking the same port clears the analysis highlight; selecting a
different flow resets it. Port analysis does not change the selected flow,
camera position, or persisted architecture facts.
Interaction-graph arcs use the same stroke thickness as ordinary topology paths; their
selection remains identifiable through the selected-flow styling and colour.
Clearing or replacing the selection restores the ordinary filtered graph.
The focused graph displays a persistent context banner naming the selected
flow and showing a compact evidence timeline. Each timeline item prioritizes
the indexed port (`I`/`O`), topic or resource, protocol, and message type;
non-integration method calls are deliberately omitted from this summary;
the complete indexed step is available from its tooltip. This avoids a long
repetition of generic method-step names while preserving the full flow. The
banner also explains that ports and arcs are analysis controls. When an arc is
focused, the banner offers an explicit action to clear the analysis focus.
Arc labels are intentionally subdued until their arc is hovered or selected;
this keeps the route geometry readable without removing the port mapping.

The export uses grouped navigation: the Graph group contains Graph, Flux de
code, and Diagnostics; the Resources group contains Ressources, OpenAPI,
Messages, and Données. Fit controls are labelled `Tout le graphe` and `Vue lisible`.
The legend remains collapsed by default to preserve graph space and can be
opened on demand.
When the Flux or Quality view is active, the Resources group is temporarily
hidden to keep the analysis context focused; it reappears when another view
is selected.

The architecture vocabulary is extensible: `Donnée` represents a persisted
data resource or contract, while `Message` represents a messaging channel.
The persisted model keeps the technical kinds `data_schema` and `message_channel`.
MongoDB
collections, SQL tables, Redis keyspaces, object-store datasets, Kafka,
RabbitMQ, SQS, and webhook streams are technology-specific evidence, not the
primary architecture category.

The export uses a responsive workspace layout with grouped navigation rather
than one flat list: Architecture, Flux de code, and Diagnostics belong to the graph
group; Ressources, OpenAPI, Messages, and Données belong to the resources
group.
`Resources` is a filterable inventory of every persisted graph node; selecting
an item opens it in Explorer and focuses its graph card. `Topics` and `Data`
are data-schema reference views: Java classes defining exchanged event data
and persisted data respectively. They retain the generic `Topic` and `Data`
architecture categories rather than implying a single storage or messaging
technology. It includes
compact architecture counters, contextual details integrated into the left
panel, and a full-size resource inspector. On narrow viewports the left panel
uses one bounded, scrollable region so the graph remains visible while users
inspect controls or resource details. At intermediate widths up to 1024 px it
is limited to 320 px and 82% of the viewport height. Its branded header remains
visible while the panel content scrolls, and its navigation tabs use one
balanced four-column, two-row grid immediately after the branded header, so
all seven destinations remain visible without horizontal scrolling. The extended search
hint is hidden at those widths while its label and example placeholder remain
visible.

The Architecture tab always opens the static architecture graph. The Flux de
code tab initially shows only its catalogue; selecting a flow explicitly
opens its Graphe d’appel while keeping the catalogue visible. The call-graph
context banner names the current projection and provides direct actions to
return to the flow catalogue or return to Architecture.

The panel typography uses a stable functional scale: 10 px uppercase kickers,
11 px compact controls, 12 px body copy, 13 px section prompts, 16 px view
titles, and 19 px selected-resource or itinerary titles. A component's size is
derived from its role in that hierarchy rather than from its individual tab.
Interactive widgets use one visual grammar across the panel: segmented tabs
and graph selectors share the same control height, inner radius, border,
surface, and blue selected state; collapsible Explorer sections use the same
bordered surface and outer radius. Pills remain reserved for filters, counters,
and status metadata so their shape continues to communicate a distinct role.
All widget families also consume one semantic palette for headings, body text,
muted metadata, accent text, panel surfaces, nested surfaces, controls, borders,
focus, and selected states. Large headings use the shared heading colour in
every tab and inspector; in the dark theme that colour is a restrained lavender
rather than white. Technology, relation, severity, and confidence colours are
excluded because they encode architecture data or status rather than chrome.

The export opens with a dark blue presentation (or follows the browser's light
preference), and provides a theme toggle in the graph
toolbar. The selected theme is stored only in browser local storage and does
not affect persisted inventory facts or exported architecture data.

Its initial view places resource and itinerary search inside Explorer,
immediately below the navigation tabs and before camera and rendering controls.
The search is the single entry point for exact resource selection and shortest
itinerary queries. Advanced actions reuse its current itinerary to list simple
alternatives or lock the selection; they do not introduce a second path input.
Graph-specific context follows the search: its first row contains zoom, fit
and reset actions; its second row contains the graph-view and node-rendering
selectors; architecture counters and inventory status follow. This whole context appears only in
Explorer, so domain tabs start directly with their own content. A dedicated,
compact `Displayed nodes and edges` control lets users independently select
API, Topic, Data-access and other edge categories, and internal services,
external services, messaging resources, Data resources and other node
categories. This filtering applies equally to native and MCP-enriched graph
vocabularies. Placement strategies remain available as advanced controls.
The toolbar does not expose a separate path-history section. Paths remain
available from the Explorer search, while itinerary comparison remains in the
advanced actions.
The Explorer search field does not select a node while text is being composed:
selection and itinerary evaluation occur when the user presses Enter. This
allows a query such as `service-a -> service-b` to be entered continuously
without opening the first microservice as soon as its name is complete.

The graph offers three primary views: graph, layers, and modules. Grouped and
non-overlapping placement strategies remain secondary options. The layers view
uses ELK.js compound nodes to arrange resources in a deterministic hierarchy:
software layers are stacked vertically, modules are nested inside their
layer, and projects/resources are placed inside each module without
overlap. The internal canonical order is `api`, `application`, `orchestration`,
`infrastructure`, `domain`, then `persistence`;
`persistence` is always the lowest layer. In Strategy1, the `CYCLE-DE-VIE`
project group is rendered in the Orchestration layer.

External microservices are rendered in a dedicated `External services` layer
at the bottom, after the internal layer order.

Shared libraries and other non-deployable projects are not rendered as layers.

The three primary views are presented as a permanently visible segmented
selector with direct `Graph`, `Layers`, and `Modules` choices; changing
views MUST NOT require cycling through intermediate views. Placement strategies
remain secondary controls. On desktop, the navigation panel uses a compact
340 px maximum width, 10 px outer margins, and dense internal spacing. The
graph viewport spans the full window behind the floating navigation panel, so
the area below the panel continues to render both nodes and edges. Zoom
actions are grouped as a compact `−` / `+`
control, and the adjacent segmented fit control exposes two explicit modes:
`All`, which frames every visible node in the plain graph and uses the widest
collision-free scale in the module and layer views, and `Readable`, which keeps
the same center but zooms in until cards have useful reading separation. The
selected mode is reapplied after a view or window-size change, and `Readable
distance` is the initial mode. Reapplying either selected mode MUST be
idempotent: repeated or rapid clicks MUST produce the same camera framing and
MUST NOT compound the previous zoom.

Users can pan and zoom to explore the remaining graph. In the layers and
modules views, relations are visually subdued. In every view, microservice,
Topic, message channel,
Data resource, data schema, and equivalent resource cards share the same
rendered width, height, and scale. Their semantic differences are conveyed by
compact icons aligned with the name, plus border and color. The secondary kind
label uses the full inner card width instead of reserving a permanent icon
column.

Users can switch node rendering between `Cards` and `Symbols` without changing
the active graph, filters, layout, selection, node positions, or current camera
framing. The switch redraws node representations in place and MUST NOT reapply
either fit mode or rerun collision placement. `Cards` remains the default.
In `Symbols`, microservices use compact hexagons, Topics and message
channels use circles, and Data resources and data schemas use small
squares. Symbols use the same visual rule as cards: a light surface tinted by
the resource accent, a defined accent border and a restrained depth shadow;
the dark theme uses an accent-tinted slate surface rather than a saturated
solid fill. Node names use adaptive, collision-aware labeling: the viewport
shows a bounded sample prioritizing connected and semantically important
nodes, and progressively admits more labels as the user zooms in. A selected
or hovered node's name is always visible and MAY extend beyond the symbol
envelope. `Readable` fitting uses the compact symbol envelope in the plain
graph. Module and layer views retain full module-envelope clearance so
switching rendering mode never changes their camera framing.
The hovered symbol and its label are rendered above every non-hovered symbol,
so another geometric marker cannot obscure the visible resource name.
In the plain graph's `Readable` fit, a final screen-space collision pass keeps
the active card or symbol envelopes from overlapping. This pass does not run in
the layers or modules views, whose deterministic placement remains unchanged.

During pan and zoom, the graph and its module overlays remain synchronized so
cards and their containing rectangles move together without transient partial
redraws.

The details panel is integrated into the left navigation panel and is hidden
until a resource, architecture module, build project, or itinerary is selected. Opening it MUST
NOT resize or crop the full-window graph workspace; only the floating panel's
contents scroll vertically. The detail view keeps the Explorer search and graph
controls available above the selected content, while display filters and
advanced actions remain collapsed to preserve vertical space. The selection
action is hidden when nothing is selected and becomes an explicit `Fermer`
action while details are shown. Architecture counters remain visible as compact
context indicators. Clearing the selection restores the complete Explorer
controls. Buttons inside details use the full available width for
resource and module names rather than inheriting the compact square dimensions
of toolbar icon buttons. Node selection MUST NOT
move, zoom, refit, or otherwise alter the camera in any primary view. Selection
may update emphasis and details, but every node and module retains its current
screen position.

In the layers and modules views, each module exposes a full-width clickable
header. Selecting that header highlights the module and opens its member list;
the module body remains available for graph panning.

Module details MUST support direct hierarchy navigation: the parent module,
direct child modules, and resources assigned directly to the selected module
are actionable when present. Selecting a listed resource opens its ordinary
resource details. Conversely, every resource detail exposes its canonical
module as an action; following it switches directly to the Modules view and
selects that module. Nested module membership is derived from canonical
slash-separated paths. A parent module does not claim resources that
are assigned only to one of its descendants.

Resource details MUST NOT present Kubernetes runtime namespaces or enrichment
manifest namespaces as architectural grouping information. Kubernetes workload
entries identify the workload by kind and name without displaying a namespace
prefix. Namespace fields MAY remain in the embedded snapshot for compatibility
and source evidence, but the report's navigable structure uses modules only.

Panning MUST also work when the drag starts on a node card; a simple click on
the same card MUST continue to select the node.

Double-clicking MUST NOT change the camera zoom accidentally after a pan;
zoom remains available through the wheel and the explicit zoom controls.

Releasing a pan MUST stop the camera immediately; no inertial continuation is
allowed.

Automatic collision protection MUST NOT zoom the camera during a pan; it may
only constrain an explicit zoom-out operation.

In the module and layer views, zooming out remains available until fixed-size
cards would make sibling module envelopes overlap. The camera is then clamped
to the collision-free ratio calculated for the current viewport; panning and
zooming in remain available.
