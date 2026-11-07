# HTML export behaviour

Parent: [Functional specification](../SPEC-FONC.md).

## Export modes

`systemlens export microservices --html FILE --call-graphs-only` produces a
standalone HTML view centered on indexed call graphs. It opens the call-flow
catalogue, selects the first available graph, and retains data-access and
consistency annotations attached to those flows. The embedded snapshot omits
unrelated resource catalogues, contracts, and architecture inventory. The
default HTML export remains unchanged. This option requires `--html` and does
not apply to JSON exports.

`--no-call-graph-annotations` works with either HTML export mode. It omits
read, write, and delete annotations and consistency diagnostics from call
graphs while retaining their services, calls, and flow selection. The default
includes those annotations. The option requires `--html FILE` and does not
change the persisted index or JSON export.

Consistency diagnostics for a selected call graph cover its full persisted
reachable flow topology. A diagnostic between accesses inside a descendant
flow appears on each selected root that reaches that flow. The catalogue search
term `incohérence` (or `incoherence`) finds flows with a possible consistency
risk, including concurrent-write, lost-update, dirty-read, and freshness
risks. Insufficient evidence and a possible interaction without an established
consistency fault do not match that search.

`--review-aids` adds the read-modify-write and dual-write candidates to the
consistency diagnostics of the HTML export. They are review aids under the
gate of the data-operations plan: each says "protection not established" or
"recovery not established", links both source sites, and never claims a
defect. The option requires `--html FILE` and the call-graph annotations; it
is off by default and changes neither the index nor the JSON export.

```bash
systemlens export microservices --html calls.html --call-graphs-only --no-call-graph-annotations
systemlens export microservices --html calls.html --review-aids
```

The consolidated presentation and interaction contract is maintained in
[UX/UI rules](../UX-UI.md). This page owns export states, inspectors, flow
catalogue behavior, and evidence semantics; it does not duplicate the global
visual language or camera contract.

## Runtime observations overlay

When the index holds observations from `systemlens observe`, the legend
offers a "Trafic observé (<sources>)" toggle naming each source, with the
collection window, its age, the backend origin, one completeness line per
source (interval, overflow or truncation, stated explicitly when none, or
"inconnue" for rows collected before collection history), and a status
summary (matched, silent, unverifiable, unexplained). The toggle is on at
every load. Without observations, neither the toggle nor any runtime data
is exported.

When on, the architecture view annotates existing elements only:

- Edges with traffic thicken with volume and carry their count ("3 appels",
  "105 msg"); a failure ratio of 5 percent or more turns them red. Silent
  indexed edges turn grey and dashed and read "aucun trafic"; an edge whose
  silence the telemetry cannot prove keeps its static style.
- Service cards show entry-point coverage, such as "APM · 2/3 entrées
  actives", amber when an entry is silent or traffic is unexplained.
  An indexed service without any observation shows "Sans télémétrie APM".
- Card tooltips detail active, silent, unverifiable, and unexplained entries
  and the inbound volume. Service details add a "Trafic observé" group.
- APM error groups add "N erreurs" to the service card and an "Erreurs
  APM" list to its details, without any error message.
- "Écarts runtime" opens an inspector listing error groups, silent and
  unverifiable indexed integrations, `observed_only` and `external`
  traffic, unmapped APM services, and services without telemetry. Its count
  covers all of them.
- "Points chauds" opens an inspector ranking the matched entry points by
  p95 latency when the backend provides percentiles, otherwise by
  failures, with calls and an "incomplet" mark for an overflowed service.
  Selecting a row selects the service card and opens the route inspector
  of its served route, so the edges into it highlight through the existing
  selection; entry points that are not comparable or not verifiable are
  listed apart. Service details repeat the service's own hotspots.

In a selected call tree, a "Trafic APM" button in the flow toolbar mirrors
the legend toggle. Each card shows the observed executions of its entry
route or consumer, or "APM · aucun trafic" for a silent indexed entry.
A scheduler root card shows its observed runs. A card with data-access
annotations adds "APM · BDD postgresql 1,1 k", a whole-service total per
database engine. A card whose entry point has APM error groups adds
"N erreurs" and turns red; its tooltip lists exception type, count, and code location. Edges
thicken with volume, and their label gains an "APM · …" line, red when
either the caller or the callee side fails. Their
tooltip lists the emitted count, per topic or service pair, and the received
count, from all sources, separately.

The overlay draws no new edge and never changes layout, camera, filters, or
selection. Counts are sampled lower bounds; REST counts measure a service
pair, not a call site.

## Architecture and resource inspectors

New browser sessions open in the Obsidian dark theme. The theme control reads
`☾ Sombre` or `☼ Clair` and retains the user's explicit light or dark
preference where local storage is available. At 900 pixels of width or less
the navigation panel starts collapsed and opens as a drawer over the graph.
The palette and shared component treatment follow [UX/UI rules](../UX-UI.md).

The AsyncAPI inspector loads its embedded, compressed viewer when a contract
is opened and displays a loading message while preparing it. Subsequent
inspections reuse the viewer. If browser decompression or viewer initialization
fails, the inspector displays the persisted contract as JSON. Closing or
switching the inspector cancels the pending display update.

The OpenAPI and AsyncAPI inspectors follow the light or dark theme of the
export. The AsyncAPI summary shows the contract version once, for example `v1`
whether the contract declares `1` or `v1`. The OpenAPI viewer still loads from
its CDN, so it needs network access the first time it opens.


Microservice cards in the Explorer view remain compact rectangles. A card with
persisted internal code flows displays their count, so services with discovered
flows can be identified directly on the graph. The inspector does not display
an inventory of input and output ports. When a persisted code flow links an
input endpoint to an output endpoint of the same service, it displays only that
source-evidenced potential internal flow and any engine-resolved intermediate
method calls. It does not imply that every input reaches every output.

The Architecture graph renders persisted JPA entity declarations as `Entité JPA`
resource nodes owned by their microservice. Selecting a resource displays its
owner, usages and class name in the main inspector. A separate class action
opens the indexed attributes, qualified name, source location and source
navigation. The `maps` relation identifies the mapping without implying a
database read/write edge. Mongo persistence classes and indexed DTOs use the
same resource description and two-level inspection model.

Source and module actions use absolute `vscode://file/` locations. When the
indexed project has moved, `--root-path` sets the local project root used for
these links, including sources in nested modules. Indexed source paths remain
relative and contain no machine-specific root.

The service inspector lists persisted Java DTOs associated with indexed JPA
entities and DTOs used by REST controller signatures when no OpenAPI contract
is indexed. The list includes the DTO role and source location; it does not
claim a runtime mapper or serialization path.

Each microservice inspector lists all indexed HTTP routes found in that
microservice, separating exposed routes from called routes. An exposed route is
selectable. Its route inspector lists the consuming microservices and the
source locations of their matched HTTP calls. A route with no matched consumer
remains visible and is reported as such; the architecture graph does not add a
separate visual node for every route.

The visible navigation has two primary workspaces: Architecture and Ressources.
Architecture contains the graph and the code-flow catalogue. Opening the
catalogue without a selected flow shows an empty state in the graph area
that lists the flows as shortcuts; choosing one selects that flow. Ressources
opens one searchable catalogue for indexed resources, routes, contracts,
DTOs, persistence classes, and diagnostics. Its type and service filters change only
the catalogue projection. The [UX/UI rules](../UX-UI.md) own the navigation
and selection contract.

Selecting an HTTP route for inspection opens the common inspector with its
provider, client count, calling microservices, source evidence, and REST DTOs.
Provider and client microservices remain interactive and preserve the route
inspector in the back history. A microservice's called routes use the same
links to open the target provider route.

When a selected HTTP route has REST DTO evidence on its provider microservice,
the route details list the DTO names, roles, and source locations. Topic
details list associated Kafka DTOs, and Mongo collection details list their
associated persistence classes.

The resource catalogue keeps its filtered result list visible during resource
inspection. Relations to other graph nodes remain interactive and reopen the
inspector on the target. Once the user has followed a relation, the
inspector displays the visited path, makes previous architecture elements
selectable in the breadcrumb, and provides a back action without closing the
component. A first-level inspector shows no breadcrumb.

A microservice inspector lists each outgoing HTTP call once under `Routes
appelées`. Its relation badges sum to the displayed relation count, with
`N sans niveau de preuve` for relations that carry no evidence level. The
project root module is not listed.

The catalogue title shows the visible and total counts, for example
`Catalogue des ressources (24/24)`. Categories show their own counts, and an
empty category is disabled; an empty `Diagnostics` category reads `aucun
diagnostic`. Rows are ordered by kind, owner, then name. Each
row starts with a kind tag such as `Route exposée`, and a route shows its
verb as a badge. The Contexte column reads `owner · detail`, for example
`inventory-service · 1 client`; a zero count reads `aucun client`. A row
without a dedicated inspector shows `Aperçu seul` instead of `Inspecter`,
which appears on hover and on the selected row.

Selecting a catalogue row shows its type, owner, source, and indexed related
resources in the right-hand preview. Each related-resource link selects that
resource in the catalogue. A back action returns to the previous preview;
closing `Inspecter` leaves this preview and its path available. Graph neighbors
come from persisted links. Routes, contracts, and Kafka DTOs use their explicit
owner, contract-route, channel address, or topic associations.

The preview lists indexed attribute names and types for DTOs, JPA entities,
and Mongo persistence classes. A Mongo collection lists fields by associated
persistence class. Resources with no indexed fields show an explicit empty
state; unrelated resource types do not show an attribute section.

An indexed DTO reference on an attribute is selectable and opens the target
DTO in the same preview. Several resolved reference candidates remain separate
choices. The preview back action returns to the containing DTO; a type without
a resolved target remains plain text and does not imply a target.

Nested DTO and Mongo persistence-class inspections use the same header back
action as all other inspector navigation; they do not add a second in-body
return control.

Non-architecture inspectors clear the previous architecture breadcrumb before
rendering their content. Their header back action returns to the inspector that
opened them, while nested DTO and Mongo inspections keep their containing-class
history. DTO titles identify message, REST, JPA, or project context when the
indexed roles provide it. JPA, DTO, and Mongo inspectors always expose an
explicit empty-field state when no fields were indexed, and source navigation
is rendered as a distinct secondary action.

The architecture inspector reuses the selected resource details while keeping
the modal title as the single identity header. Actions rendered in the details
widget, including modules, code flows, source evidence, and resource focus,
remain available after the details are displayed in the modal. Actions that
switch to another workspace view close the inspector before changing the view.
Known target microservices, topics, collections, and persistence owners are
selectable from their corresponding specialized inspectors. When extraction
retains several candidate field types, each candidate is exposed as a separate
navigation action.

## Call graph data and annotations

The exported `call_graphs` map describes service-level propagation for one flow
or flow group. Each graph contains participating nodes, deterministic order,
traversal levels, a compact call tree, per-flow causal transition indexes,
complete directed edges with display orders and endpoint identifiers, and
service triggers. The flow picker starts with paths that have no upstream
indexed call flow, plus one stable entry for each uncovered cycle or
disconnected component. It then omits a root graph when every service and
exact directed call edge appears in a larger root graph. Calls with different
endpoint IDs remain distinct even when their displayed labels match.

Equal graphs retain their separate flow entries. Downstream flows
remain in the supporting flow data for selected roots that reach them.

The first traversal level is the root set. Selecting a call graph keeps its
edge order and trigger metadata visible.

Each microservice occurrence in a selected call graph shows one badge per
entity associated with its internal flow and input. The badge combines the
source-evidenced operations in CRUD order: `C` for create, `R` for read, `U`
for update, and `D` for delete, followed by the entity name. `C/U` marks an
operation that may create or update; it does not claim that both happened.
For example, `R (C/U) ProductEntity` combines a read with that alternative.

An access in the shared entry handler remains a potential annotation on each
branch because static evidence does not establish which branch executes it.
The badge adds `?` after the entity name when only the handler or input port
connects that access to the occurrence, including when method-call paths diverge.
Hovering the badge explains this scope.
When distinct resource IDs share a short entity name, the badges add a
qualifier instead of merging those identities.

When an anomaly concerns the entity, its badge remains selectable for diagnosis.

Hovering a badge also states the persisted operation facts of its group: the
technology (`mongo`, `jpa`, or `jdbc`), the guard with its scope or
"not established", and the transaction scope or "unknown". A group with an
operation whose action is unknown says so; such an operation contributes no
CRUD letter.

Call-tree cards grow to contain every data-operation and diagnostic badge. Their
height depends on the annotations on that occurrence, so a card with many badges
does not enlarge unrelated cards. Branch spacing and edge attachment follow the
rendered card heights.

## Selected flow navigation

When a single code flow is selected, the graph displays the selected directed
call graph as a hierarchy of call occurrences. A microservice is therefore
rendered once per occurrence in the expanded tree, so the same service may
appear in several branches.

Each occurrence highlights the Java method associated with its input port in a
colored block below the microservice name. The declared parameter types appear
beneath the method name when the index contains them. An empty signature is shown
as `()`.

Call-tree cards show the service name without repeating its microservice type.
An occurrence with no indexed consumer flow keeps its `Suite inconnue` status.
Cycles and hidden-call counts remain visible through their separate badges.

If the input port has no indexed method, the renderer uses the associated flow
method when available. Hovering the block shows the qualified signature. Missing
method evidence omits the block; missing signature evidence omits the parameter
line. Long names and types may be clipped to the card width.

A constructor or initializer node (`Owner.<init>`, `Owner.<clinit>`,
ADR-75) is part of the exported call graph and of the flows that pass
through it. It owns no port and starts no flow, so no occurrence shows it
as its method block.

For a Strategy1 Kafka reception, the call-tree card shows the signature of a
unique `consume` method in the listener's class when its parameter types are
indexed. If the class has overloaded `consume` methods, a unique indexed call
selects the signature. Otherwise, the card shows the annotated listener
signature. This display choice does not change indexed method facts.

The tree mode uses a fixed left-to-right orientation and an incremental depth
control from 1 to 8 levels. The default is depth 1, showing only the root
occurrence. The `−` and `+` controls change one level at a time. Edge labels
retain the order, relation label, and protocol when available. The renderer
measures label bounds and hides labels that cannot be placed without covering a
card or another visible label. Tooltips retain the full indexed details.

The full tree scales to the available viewport with a minimum initial scale of
0.66. At total scale below 0.82, method parameters and operation badges become
visually quiet while service names and diagnostic borders remain visible.
Hovering retains complete evidence in the tooltip. The tree remains zoomable.
When a branch reaches the configured depth, expansion stops.
When a branch reaches an input endpoint already displayed on the current branch,
the occurrence is rendered as a cycle endpoint and is not expanded further. A
reachable target without an indexed consumer flow is rendered as a leaf with an
unknown continuation. A repeated service using a different input port is therefore
not marked as a cycle solely because its name is repeated.

At the depth boundary, the node displays the number of hidden child calls;
clicking that `+N appels` badge reveals one additional level for that branch
without changing the current tree viewport. Every visible node with descendants
displays a `− replier` badge that collapses that branch and preserves the
viewport. A collapsed node displays a `+N appels` badge to reopen its descendants.
The depth control can still be increased to reveal more levels globally. The tree
is the only call-graph rendering mode.

The upper graph toolbar shows `Centrer`, zoom, fit, selection reset, layout, and
node-rendering actions in the architecture view. In the call-graph view it keeps
centering, zoom, `Tout voir`, `Sélection`, and the tree controls including
`Déplier tout l’arbre`; it does not repeat the call hierarchy. These controls
stay in place, disabled, until a flow is selected. `Déplier tout l’arbre`
refits the expanded tree. The context widget is collapsed by default;
`Développer` shows the flow identity, trigger, tree counts, consistency summary,
and the key of the access annotations, and `Réduire` hides them. Changing view
collapses it again. Dragging the empty tree workspace pans the
tree, while clicking a service occurrence keeps its selection action. `Shift +
click` on a service occurrence or call arc opens its dedicated introspection
window. Hovering a service occurrence displays its level and cycle state.

The service occurrence tooltip displays its level, cycle state, and Java method
when available. Call-tree ports use unlabelled markers: inputs sit on the left
edge and outputs on the right. Hovering a port displays its direction, source,
and associated Java method without showing the port number. Hovering an edge
displays its order, source, target, protocol, indexed relation label and message
type when available.

The call-graph heading identifies the view without displaying a service or
method sequence. Flow descriptions state the trigger and the observed effect
without presenting an ordered method-call sequence.

Flow entries use a compact summary layout: the description is limited to two
lines and statistics are displayed inline. Status and protocol details remain
available in the opened flow view rather than expanding every list entry.
Clicking the entry still opens the complete flow view, so the compact layout
does not remove any indexed information.

The Flux tab explains the selection action directly above the catalogue:
clicking a flow entry opens its call graph, replacing the previously displayed
flow. The Architecture summary also reports the number of persisted indexing
signals, or explicitly states that no signal was found; this is a navigation
aid, not a completeness claim beyond the persisted diagnostics.

Hovering or focusing a flow entry opens a tooltip with its complete description,
trigger, service route, edge and step counts, effects, protocol, confidence and
topology status. The tooltip is dismissed when the pointer or keyboard focus
leaves the entry.

In the selected flow graph, each edge has a transparent hover target. Hovering
an edge or its order label highlights that edge and its label without changing
the persisted graph data.

## Explorer workspace and ports

The exported Explorer presents the graph in a shared workspace: a compact
control surface, a searchable graph context, summary counters, and a separate
legend and inspector. The visual treatment adapts to light and dark themes and
constrained viewports without changing graph data, selection state, or the
meaning of the existing controls. No widget text is smaller than 10 pixels.

The Graphe panel starts with the search field, followed by the filters.
Camera and rendering controls float over the top of the graph area; at
compact widths they return to the panel. A status line under the header
reads `Index : complet` or `Index : N faits à vérifier`, then the relation
count and the index date when the export carries it. The layout status
reads `Disposition active : <disposition>.` Type chips in the floating
toolbar count the nodes per type filter and toggle that filter. A microservice tooltip labels its build
tool, for example `Build : maven`.

The legend groups node types, relations, connectivity, and evidence level.
When open, it reserves a right column or a bottom band of the graph workspace,
so no fitted card lies under it. Secondary widgets use the same surface,
spacing, status, and interactive-row treatment for details, filters, code
flows, references, and indexing issues.

Dense graph views show a minimap. Its node positions remain fixed as the camera
moves, while the viewport outline tracks pan and zoom. Clicking or dragging the
map recenters the graph without changing the selected resource or flow.

Every HTTP/Kafka input and output receives a deterministic identifier that is
global to the exported graph: `I1`, `I2`, … for inputs and `O1`, `O2`, … for
outputs. When a persisted code flow proves that an input reaches an output in the
same service, the exported input label includes those local outputs, for example
`I4 → O3, O5`. The graph anchor shows its compact global identifier (`I4`); its
tooltip and the inspector retain the complete label. Inputs and outputs are
centred and distributed independently on their respective sides of a card rather
than sharing one positional index.

A selected service lists only its triggered inputs, while the Flux widget uses the
same labels; method and Data steps remain ordered but unlabelled. When a selected
flow port is backed by Kafka, its tooltip explicitly shows `Topic en entrée` for a
consumed topic or `Topic en sortie` for a published topic. The Flux entry also
lists all input and output topics carried by the persisted flow; HTTP ports keep
their method and route presentation.
