# XLSX diagnostic export

Parent: [Functional specification](../SPEC-FONC.md).

`systemlens export microservices --xlsx FILE` produces a workbook from the
persisted architecture snapshot. The command does not reparse source files or
resolve missing dynamic targets during export.

The workbook contains a compact `Synthèse` sheet with one row per microservice.
It includes `Statut`, port counts, flow counts, and `Alertes` columns to identify
likely indexing gaps. The detail sheets use one row per indexed fact, with a
frozen header and filters.

- `Ports` lists exposed and consumed inputs as well as called and produced
  outputs. Its `Direction` column distinguishes `IN` and `OUT`.
- `Flux internes` lists persisted code flows that have both an input endpoint
  and an output endpoint, one flow-output pair per row.
- `Légende` explains the columns and unresolved values.

Rows are colored as follows:

- green: no incomplete indexing signal was found;
- orange: no internal flow, unresolved class or Kafka type, or dynamic Kafka topic;
- red: no indexed port exists for the microservice.

The absence of an internal flow is an investigation signal, not proof of an
indexing error. A service may legitimately be only an input or only an output
boundary. Port-specific alerts include the direction, transport, resource, and
relative source location so the missing fact can be checked directly.
REST payload types are not flagged when absent: the current endpoint snapshot
does not yet distinguish request and response schemas for every REST contract.

Each port block puts the transport, Java class, inferred type, and REST route or
Kafka topic on separate lines. The export keeps only the Java class name, not
its package or method name, to keep the summary readable. Flow blocks use the
same layout for input and output classes and resources. A `?` means that the
corresponding association or type was not resolved statically.
- `Légende` explains the columns and unresolved values.

The export includes only deployable microservices selected by the same
projection as the microservice graph. It preserves unresolved evidence as
blank fields; it does not infer a class, type, topic, or relationship.
