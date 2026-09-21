# HTML export behaviour

Parent: [Functional specification](../SPEC-FONC.md).


Microservice cards in the Explorer view remain compact rectangles. A card with
persisted internal code flows displays their count, so services with discovered
flows can be identified directly on the graph. The inspector does not display
an inventory of input and output ports. When a persisted code flow links an
input endpoint to an output endpoint of the same service, it displays only that
source-evidenced potential internal flow and any CodeQL-resolved intermediate
method calls. It does not imply that every input reaches every output.
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

