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

```bash
systemlens export microservices --html calls.html --call-graphs-only --no-call-graph-annotations
```

The consolidated presentation and interaction contract is maintained in
[UX/UI rules](../UX-UI.md). This page owns export states, inspectors, flow
catalogue behavior, and evidence semantics; it does not duplicate the global
visual language or camera contract.

## Architecture and resource inspectors

New browser sessions open in the Obsidian dark theme. The theme control retains
the user's explicit light or dark preference where local storage is available.
The palette and shared component treatment follow [UX/UI rules](../UX-UI.md).

The AsyncAPI inspector loads its embedded, compressed viewer when a contract
is opened and displays a loading message while preparing it. Subsequent
inspections reuse the viewer. If browser decompression or viewer initialization
fails, the inspector displays the persisted contract as JSON. Closing or
switching the inspector cancels the pending display update.


Microservice cards in the Explorer view remain compact rectangles. A card with
persisted internal code flows displays their count, so services with discovered
flows can be identified directly on the graph. The inspector does not display
an inventory of input and output ports. When a persisted code flow links an
input endpoint to an output endpoint of the same service, it displays only that
source-evidenced potential internal flow and any CodeQL-resolved intermediate
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
Architecture contains the graph and the code-flow catalogue. Ressources opens
one searchable catalogue for indexed resources, routes, contracts, DTOs,
persistence classes, and diagnostics. Its type and service filters change only
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
inspector on the target. The inspector displays the visited path, makes
previous architecture elements selectable in the breadcrumb, and provides a
back action without closing the component.

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
exact directed call arc appears in a larger root graph. Calls with different
endpoint IDs remain distinct even when their displayed labels match.

Equal graphs retain their separate flow entries. Downstream flows
remain in the supporting flow data for selected roots that reach them.

The first traversal level is the root set. Selecting a call graph keeps its
arc order and trigger metadata visible.

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

Call-tree cards grow to contain every data-operation and diagnostic badge. Their
height depends on the annotations on that occurrence, so a card with many badges
does not enlarge unrelated cards. Branch spacing and arc attachment follow the
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

For a Strategy1 Kafka reception, the call-tree card shows the signature of a
directly called `consume` method when exactly one such method has indexed
parameter types. Otherwise, it shows the annotated listener signature. This
display choice does not change the Kafka input port or indexed method facts.

The tree mode uses a fixed left-to-right orientation and an incremental depth
control from 1 to 8 levels. The default is depth 1, showing only the root
occurrence. The `−` and `+` controls change one level at a time. Arc labels retain
the order, relation label, and protocol when available. Long labels are shortened
on the canvas; the arc tooltip retains the full indexed details. The renderer
estimates label width at 6 pixels per character and adds the 190-pixel card width
and 32 pixels of clearance, with a 320-pixel minimum distance between levels.

The full tree scales to the available viewport, so deep trees can appear smaller
and remain zoomable. When a branch reaches the configured depth, expansion stops.
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
centering and zoom and adds the tree controls and `Déplier tous les nœuds`; it
does not repeat the call hierarchy. The context widget is collapsed by default and
can be expanded with `Développer`. Dragging the empty tree workspace pans the
tree, while clicking a service occurrence keeps its selection action. `Shift +
click` on a service occurrence or call arc opens its dedicated introspection
window. Hovering a service occurrence displays its level and cycle state.

The service occurrence tooltip also displays its IN and OUT ports with the
associated Java methods. Hovering an arc displays its order, source, target,
protocol, indexed relation label and message type when available.

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
trigger, service route, arc and step counts, effects, protocol, confidence and
topology status. The tooltip is dismissed when the pointer or keyboard focus
leaves the entry.

In the selected flow graph, each arc has a transparent hover target. Hovering
an arc or its order label highlights that arc and its label without changing
the persisted graph data.

## Explorer workspace and ports

The exported Explorer presents the graph in a shared workspace: a compact
control surface, a searchable graph context, summary counters, and a separate
legend and inspector. The visual treatment adapts to light and dark themes and
constrained viewports without changing graph data, selection state, or the
meaning of the existing controls. Secondary widgets use the same surface,
spacing, status, and interactive-row treatment for details, filters, code
flows, references, and indexing issues.

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
