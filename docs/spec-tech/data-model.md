# Data model

Parent: [Technical specification](../SPEC-TECH.md).


### Core persisted facts

`MessageEndpoint` is the primary extracted fact. It records role, system,
topic, dynamic status, source (`code` or `manifest`), framework, location,
snippet, module, qualified name and optional Kafka message type. Its identifier
is stable for a source location:

```text
sha256(role | topic | path | start_line:end_line)[:16]
```

`ArchitectureRelation` records an evidenced link between source and target
objects. It includes origin (`code`, `manifest` or `derived`), confidence,
module and source location. It is the persisted source of truth for the
architecture snapshot, including conservative resolved inter-service REST and
Kafka topology relations. `ArchitectureSnapshot` derives its topology edges
from those persisted relations; adapters may use indexed endpoints only to add
route, topic, and source presentation details and do not re-resolve targets or
rescan source.

`GraphFact` is the separate enrichment layer for facts supplied by a user, or
by an agent operating through the companion SystemLens skill, via MCP. It
supports typed nodes and edges, origin, namespace, status, confidence,
pass/revision metadata, optional relative evidence and a note. The
`graph_facts` table is not cleared by indexing. `import_graph_facts` validates
and upserts a manifest by `(namespace, fact_type, manifest_id)`; complete
snapshots can remove stale facts only inside their namespace. Source-derived
relations remain owned by the indexer and cannot be removed through MCP.
`architecture_graph` merges both layers using the generic dependency node/edge
shape, preserving API and MongoDB associations.

`AnalysisProfile` carries persisted extraction choices with the loaded
inventory, currently the `default` or `strategy1` topic convention. CLI, MCP,
export, graph, and audit adapters consume this profile. A workspace federation
retains source profiles and rejects a mixture of incompatible topic strategies.

### Materialised contracts

MongoDB persistence-class metadata is extracted at index time from Java
`@Document` declarations, entity generic types of Mongo repositories, and
unambiguous `Type.class` arguments of `MongoTemplate` operations. Repository
entities without `@Document` use Spring Data's lower-camel simple-name default;
ambiguous simple names are not resolved. The immutable snapshot records the
collection, qualified class name, source location, and declared fields so HTML
exports never reopen Java sources to build this view.

The HTML snapshot resolves these classes from the collection-owning module and
its transitive build dependencies. A unique collection-wide fallback covers
snapshots without dependency metadata while preserving ambiguity when several
modules declare the same collection name.

Persistence-class extraction was introduced in schema version 21 and remains
part of every later schema version. Any snapshot from an older schema version
is rejected on read and must be regenerated, preventing a valid-looking HTML
export from silently presenting the empty pre-extractor inventory.

For each MongoDB root class, indexing persists the recursive closure of uniquely
resolved project field types. Nested definitions retain source locations and
declared fields but are marked as non-root so collection inventories list only
actual persistence roots while inspectors can navigate the complete closure.

### Diagnostics and portability

`ExtractionDiagnostic` is a safe, persisted extraction outcome with its file
path, extractor, category, severity and a non-source-code detail. The initial
implementation records Tree-sitter Java parse failures; `analyze
indexing-issues` exposes them alongside unresolved architecture facts.

MongoDB extraction keeps structurally valid declarations and invocations from
a partially parsed Java file while ignoring subtrees that contain an error or
missing token. The file-level diagnostic remains visible so partial coverage is
never presented as a complete parse.

`CodeFlow` is a persisted, ordered potential path through one Java method or a
chain of Java methods resolved by the selected method-call engine. Its
first `CodeFlowStep` is an indexed HTTP/Kafka entry point or a source-evidenced
`@Scheduled(cron = "...")` trigger; later steps are HTTP calls, Kafka
publications, or MongoDB reads/writes located within the
same Tree-sitter `method_declaration`. Steps retain relative evidence paths and
line ranges. Flow identity uses the module, relative path, qualified method,
and trigger semantics rather than line numbers, so ordinary line movement does
not replace the logical flow. The materializer runs during indexing and never during export or
query. It is linear in the indexed endpoint and Mongo-operation inventory plus
the traversed AST nodes for Java files that contain eligible endpoints. Maven
`target/` and Gradle `build/` output trees are excluded from the inventory, so
copied resources and generated classes cannot duplicate source facts. A
same-method relation remains `potential` with medium confidence because static
lexical order does not prove branch execution. Method-call facts join
persisted AST method facts to form interprocedural flows; `method_call` steps keep
the call-site line. For CodeQL, reachability is computed from indexed output
methods backwards through their callers until indexed input methods are reached.
The transitive query has no business-level hop or global path limit; the
configured complete-pass deadline remains the operational safeguard. The legacy
`analysis.codeql_max_hops` remains for bounded route reconstruction; it does
not authorize inferred call edges.
By default, SystemLens creates one
temporary CodeQL database for the whole indexed repository, with CodeQL's
`--build-mode=none` source-only mode, then deletes it. The resulting calls are
partitioned by caller project only for progress checkpoints. With HTML progress,
global flows are materialized once, reused for final persistence, and filtered
by reported call-site evidence for partial views. Direct answers without an
intermediate witness are withheld until the final checkpoint. The
complete global call list, including cross-project references, is used for
method-flow materialization.
The call facts are kept in memory, their paths are remapped to root-relative
evidence, and all repository results are aggregated before method-flow
materialization. A missing or untrusted callee
path may join one unique qualified indexed method, but is explicitly
low-confidence and retains signature-join provenance; ambiguous names remain
unresolved. An
explicit CLI database remains a global-database override. When the CodeQL executable is
absent, it records no interprocedural paths and reports that limitation without
discarding AST-only flows. When CodeQL represents a lambda as a synthetic
anonymous callable, SystemLens attributes its call site to the narrowest
persisted Java method enclosing that line in the same file. Dynamic dispatch,
reflection, and runtime-only routing remain outside this deterministic layer.
In buildless mode, a transient AST symbol index covers source-declared
qualified types, imports and transitive `extends`/`implements` relations,
including source files without endpoint facts. It may bridge an
abstract/interface method to one unique compatible implementation and recover
receiver-typed helper calls by arity/signature. Duplicate types, ambiguous
overloads, unknown receivers and unsupported generic/vararg substitutions stay
unresolved. Synthetic edges are always `possible`/`low` and never replace
exact CodeQL evidence.
For a virtual call, CodeQL's unique `exactVirtualMethod` target is retained at
medium confidence. If no unique target can be proven, its `viableCallable`
candidates are retained individually at low confidence; SystemLens does not
select an implementation on Spring bean metadata alone. The analysis profile,
including the selected method-call engine, availability, activation and its bounds, participates in the
code-flow signature so switching profile recalculates unchanged repositories.

The flow join groups AST and interprocedural candidates with deterministic
in-memory keys before Kafka continuations are expanded. Parallel candidate
routes are grouped by source endpoint, target endpoint, and status;
the representative with the strongest confidence, then the shortest route, is
persisted. The indexing deduplication uses plain deterministic collections; HTML
layout remains a rendering concern handled by the browser graph stack. Each exported code flow
also carries a NetworkX-derived service subgraph and deterministic component
order; the Flux view uses that snapshot instead of reconstructing the call
sequence from display labels.
Kafka publications are expanded as fan-out branches for a concrete topic within
an existing input-triggered flow. A publication without an input trigger does
not become a flow root. The exception is a publisher explicitly annotated with
a cron expression: it creates one `CodeFlow` per matching consumer whose first
step is `cron_entry`, followed by the publication and consumer entry. Known
message types must match when both endpoints provide one. Missing type evidence
does not prevent the join, while conflicting known types prevent it.
The exported flow-graph projection is always a rooted arborescence: its root is
the first persisted endpoint, or the unique proven producer immediately before
a Kafka entry. For Kafka fan-in, no producer is selected arbitrarily. A
breadth-first traversal keeps each reachable service under its first parent,
omitting only disconnected alternatives and back-edges. This is a presentation
projection of the persisted evidence; cycle `CodeFlow` records remain intact.
Within that subgraph, parallel evidence rows are keyed by directed service
pair, protocol and resource label. Duplicate rows select one deterministic
endpoint pair, preferring a pair that is consecutive in the persisted flow and
then the lexicographically smallest pair. The browser receives only these
canonical service arcs; it does not append a second set reconstructed from
port links.
Before serialization, HTML export groups flows by their complete rendered
flow-graph signature (nodes, directed arcs, protocol and resource labels).
Equivalent CodeQL or continuation routes produce one visible representative,
chosen by status, confidence, route length and stable source ordering;
`equivalent_count` retains the number of persisted variants. Index storage is
not modified by this presentation deduplication.

Each persisted `CodeFlow` also carries a `reconciliation` status. `complete`
means every endpoint step exists in the same snapshot and every cross-service
effect/continuation has a matching persisted topology edge; `partial` means
the code evidence remains valid but at least one endpoint or topology edge is
missing, unresolved, dynamic, or ambiguous. Same-service input-to-output
evidence can be complete without an inter-service edge. The status is derived
during indexing from endpoint identifiers and never from route-label matching.
If multiple topology edges match the same flow step, reconciliation remains
`partial` because the evidence is ambiguous. When several static call routes
collapse to one canonical flow, `alternative_count` records the number of
distinct evidence-bearing routes represented by that flow for diagnostics and
UI disclosure.

Equivalent AST and CodeQL observations of the same ordered route count once;
genuinely different intermediate routes remain distinct.

When explicitly requested through `systemlens index --codeql-progress-html FILE`,
the indexing service emits one in-memory checkpoint after each completed
CodeQL project. The delivery adapter renders that checkpoint to `FILE` by
atomic replacement and labels it as provisional with its completed-project
count. These progress documents are deliberately outside the SQLite snapshot
contract: they are an opt-in observability aid, may omit later method-call
facts, and are never read by normal export, query, MCP, or web workflows.

Kafka flow continuations join only concrete, statically resolved Kafka endpoint
identifiers. Known message types must match when both endpoints provide one;
missing type evidence remains compatible. A continuation is not materialized when the producer has a later
external effect, because a linear composed flow would otherwise omit that
evidence. A composed flow can follow up to four Kafka producer-to-consumer
hops, using only original persisted message-entry flows as consumers and never
revisiting the same consumer flow. This makes cyclic topics finite while
retaining the complete ordered evidence for each bounded candidate.

The HTML graph model joins endpoint identifiers and statically inferred message
types to the persisted
`integration_methods` projection. Microservice cards remain simple rectangles
and receive an export-time count of persisted internal flows for that service.
The selected-service widget renders every indexed HTTP/Kafka endpoint as an
input/output port inventory, including ports without a resolved target or an
associated code flow. It also renders resolved input-to-output flow evidence.
Rendering does not read source files. At export time, every HTTP/Kafka endpoint receives a deterministic
identity global to the complete export: `I<n>` for an input and `O<n>` for an
output, ordered by a stable topological service order, then relative evidence
path, source line, and endpoint ID. A cyclic service component falls back to
the stable service order without inventing a direction. The projection derives
input-to-output references only from persisted code flows whose endpoint steps
belong to the same service: each input receives the labels of its locally
reached outputs (`I4 → O1, O3`). This prevents an external caller from being
presented as an output of the service it invokes. The renderer adds those
presentation-only labels to graph details and serialized code-flow endpoint
steps. Fixed-size graph cards render
those labels as side anchors: inputs on the left and outputs on the right. An
SVG overlay projects only persisted REST/Kafka edges with an output source and
an input target between these anchors; it redraws after camera changes and
uses deterministic opposing lanes for a direct cycle. The same overlay draws
dashed, directed local links from each input to the outputs associated with it
by a persisted code flow. It neither alters
persisted endpoint identity nor turns a CodeQL fact into a linear route.

The SQLite store is standard-library-only: it does not load native vector
extensions or persist/query vector representations. The retained findings
search compatibility path uses deterministic lexical matching.

Schema version 31 adds persisted `code_flows.alternative_count` route metadata
to the schema 30 `code_flows.reconciliation` status and
migrates older snapshots with `unknown`. Schema version 29 adds the
`asyncapi_contracts` table for validated AsyncAPI
documents owned by a module and rendered from the persisted snapshot. When an
export contains at least one such document, it embeds the Apache-2.0 licensed
AsyncAPI web component (version 3.1.8) and its distributed stylesheet; exports
without AsyncAPI facts do not carry this payload. Schema
version 28 adds the `integration_methods` table for AST method evidence
and input/output endpoint identifiers. Schema version 27 added the
`code_flows` table and its module/path indexes. The
migration is additive and occurs when the writable store opens, before the
index transaction. Stored steps are JSON projections of immutable domain
facts; source paths remain relative to the indexed root.

For an OpenAPI-derived REST input whose checked-in Java implementation only
implements a generated interface, the integration-method projection may attach
the input to exactly one method in the same module when the contract's exact
`operationId` equals the Java method name and that declaration carries
`@Override` on a class carrying `@RestController`. The endpoint keeps its
OpenAPI file and operation line as source evidence. Zero or multiple candidates
remain unmatched; this projection never selects a method merely from an HTTP
route or a class name.

The index persists source evidence only as paths relative to the indexed
project root. HTML export receives a local `--root-path` and joins it to these
relative paths when building VS Code URIs. This keeps an index portable across
machines and avoids persisting WSL or other host-specific path context.

Kafka DTO definitions and OpenAPI/Swagger document contents are materialized at
index time. Each OpenAPI/Swagger source file is attributed and materialized
once by the indexed Maven or Gradle module that directly encloses the file;
other modules may reference it but do not list it again. This attribution is
resolved by matching the repository-relative evidence path's segments against
the enclosing module's own directory segments (not merely its last path
component), so it stays correct for modules nested two or more levels below
the repository root and for a publishing module (a Strategy1 declaration)
whose contract physically lives in a different, shared module: the export
looks up the parsed spec and materializes the contract exactly once, keyed by
the module that truly encloses the file.

A DTO definition retains its qualified name, owning module, module-relative
Java source path, declared fields, enum values, and conservative nested-type
references. `indexing/dto_inventory.py` resolves a simple nested type only through an
explicit import, the containing package, or a globally unique simple name; an
ambiguous name remains unresolved. The HTML export uses those stored facts and
only derives its VS Code URI at render time; it does not reopen a Java or
OpenAPI source file.

For Strategy1 OpenAPI publication, `xxx.rest` selects both same-named contract
files anywhere in the repository and all YAML or JSON candidates under a
`model-xxx/src/main/resources/openapi/` module. Each candidate is validated as
an OpenAPI document before endpoint facts are persisted; endpoints remain
attributed to the module containing the declaration while the contract source
path stays evidence.

With Strategy1, software-layer classification additionally maps project
group `PORTAIL` to API, `CYCLE-DE-VIE` to Orchestration, `DOMAIN-*` to
Domain, and the documented layer-name prefixes/suffixes to their matching
layers. These project groups are parent directories containing projects;
Kubernetes namespaces are not used for this classification. These mappings
are disabled for the default profile.

Module discovery also inventories every valid YAML or JSON OpenAPI document
under a module's own `src/main/resources/openapi/` directory, regardless of
its filename. This list is the physical contract ownership used for persistence;
a Strategy1 declaration only reattributes the published endpoint facts to its
declaring module.

AsyncAPI documents are materialized from module source trees after build-module
discovery. Build-output paths (`target/` and `build/`) are excluded at inventory,
discovery, and scanner boundaries, so generated artifacts never enter the
snapshot even when a scanner is invoked outside the normal index pipeline.
