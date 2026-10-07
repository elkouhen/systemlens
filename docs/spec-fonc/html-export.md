# HTML export behaviour

Parent: [Functional specification](../SPEC-FONC.md).

The consolidated presentation and interaction contract is maintained in
[UX/UI rules](../UX-UI.md). This page owns export states, inspectors, flow
catalogue behavior, and evidence semantics; it does not duplicate the global
visual language or camera contract.


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

The architecture navigation exposes four top-level areas: Architecture, Flux
de code, Contrats, and Diagnostics. Architecture provides separate views for
the graph, the microservice list, the topic catalogue, and the collection
catalogue, plus the HTTP route list. Contrats provides separate views for
OpenAPI, AsyncAPI, indexed DTOs, JPA entities, and Mongo persistence classes.
Routes are grouped by
provider microservice and route path, and each entry displays its HTTP verb,
resource path, and count of unique calling microservices found. Selecting a
route opens the common inspector component with its provider, client count,
calling microservices, source evidence, and REST DTOs. Provider and client
microservices are interactive: they open the corresponding model inspector and
keep the route inspector available through the common back action. A
microservice’s called routes use the same route links and open the target
provider route in that inspector.

When a selected HTTP route has REST DTO evidence on its provider microservice,
the route details list the DTO names, roles, and source locations. Topic
details list associated Kafka DTOs, and Mongo collection details list their
associated persistence classes.

Selecting a microservice, topic, or Mongo collection from its Architecture
catalogue switches the graph to the selected node and opens the graphical
inspector component with that node's description widget. The inspector can be
closed without clearing the graph focus. Relations to other graph nodes are
interactive and reopen the same inspector on the target model element, so the
architecture catalogue supports multi-step navigation through the model. The
inspector displays the visited path, makes previous architecture elements
selectable in the breadcrumb, and provides a back action to return to the
previous model element without closing the component.

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

The exported `call_graphs` map describes service-level propagation for one flow
or flow group. Each graph contains participating nodes, deterministic order,
traversal levels, a compact call tree, complete directed edges with display
orders and endpoint identifiers, and service triggers. The first traversal
level is the root set. Selecting a call graph keeps the root, arc order and
trigger metadata visible; the export does not merge distinct flows into one
undifferentiated edge.

When a single code flow is selected, the graph displays the selected directed
call graph as a hierarchy of call occurrences. A microservice is therefore
rendered once per occurrence in the expanded tree, so the same service may
appear in several branches.

The tree mode exposes a maximum depth of 3, 5 or 8 levels and an orientation
of left-to-right or top-to-bottom. The default is depth 3 and left-to-right.
When a branch reaches the configured depth, expansion stops. When a branch
reaches an input endpoint already displayed on the current branch, the
occurrence is rendered as a cycle endpoint and is not expanded further. A
repeated service using a different input port is therefore not marked as a
cycle solely because its name is repeated. The existing network
projection remains available when the tree depth is increased.
At the depth boundary, the node displays the number of hidden child calls;
clicking that `+N appels` badge reveals one additional level for that branch
without changing the current tree viewport. Every visible node with
descendants displays a `− replier` badge that collapses that branch and
preserves the viewport. A collapsed node displays a `+N appels` badge to
reopen its descendants.
The depth selector can still be increased to reveal more levels globally.
The tree is the only call-graph rendering mode. The upper graph toolbar shows
`Centrer`, zoom, fit, selection reset, layout, and node-rendering actions in
the architecture view. In the call-graph view it keeps centering and zoom and
adds the tree controls and `Déplier tous les nœuds`; it does not repeat the
call hierarchy. The context widget is collapsed by default and can be
expanded with `Développer`. Dragging the empty tree
workspace pans the tree, while clicking a service occurrence keeps its
selection action. Hovering a service occurrence displays its level and cycle
state. The service occurrence tooltip also displays its IN and OUT ports with
the associated Java methods. Hovering an arc displays its order, source,
target, protocol, indexed relation label and message type when available.

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

The exported Explorer presents the graph as a layered workspace: a compact
control surface, a searchable graph context, summary counters, and a separate
legend and inspector. The visual treatment adapts to light and dark themes and
constrained viewports without changing graph data, selection state, or the
meaning of the existing controls. Secondary widgets use the same surface,
spacing, status, and interactive-row treatment for details, filters, code
flows, references, and indexing issues.

Every HTTP/Kafka input and output receives a deterministic identifier that is
global to the exported graph: `I1`, `I2`, … for inputs and `O1`, `O2`, … for
outputs. When a persisted code flow proves that an input reaches an output in
the same service, the exported input label includes those local outputs, for
example `I4 → O3, O5`. The graph anchor shows its compact global identifier
(`I4`); its tooltip and the inspector retain the complete label. Inputs and
outputs are centred and distributed independently on their respective sides
of a card rather than sharing one positional index. A selected service lists
only its triggered inputs, while the Flux widget uses the same labels; method
and Data steps remain ordered but unlabelled. When a selected flow port is
backed by Kafka, its tooltip explicitly shows `Topic en entrée` for a consumed
topic or `Topic en sortie` for a published topic. The Flux entry also lists all
input and output topics carried by the persisted flow; HTTP ports keep their
method and route presentation.
