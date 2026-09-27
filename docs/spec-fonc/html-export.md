# HTML export behaviour

Parent: [Functional specification](../SPEC-FONC.md).


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

The exported `call_graphs` map describes service-level propagation for one flow
or flow group. Each graph contains participating nodes, deterministic order,
traversal levels, a compact call tree, complete directed edges with display
orders and endpoint identifiers, and service triggers. The first traversal
level is the root set. Selecting a call graph keeps the root, arc order and
trigger metadata visible; the export does not merge distinct flows into one
undifferentiated edge.

Flow entries use a compact summary layout: the description is limited to two
lines and statistics are displayed inline. Status and protocol details remain
available in the opened flow view rather than expanding every list entry.
Clicking the entry still opens the complete flow view, so the compact layout
does not remove any indexed information.

In the side-by-side flow comparison, each arc has a transparent hover target.
Hovering an arc or its order label highlights only that arc and its label
inside the current comparison panel. The other compared graph remains
unchanged, and the interaction does not merge the graphs.

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
