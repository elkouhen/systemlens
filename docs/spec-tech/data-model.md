# Data model

Parent: [Technical specification](../SPEC-TECH.md).


### Core persisted facts

`MessageEndpoint` is the primary extracted fact. It records role, system,
topic, dynamic status, source (`code` or `manifest`), framework, location,
snippet, module, qualified name, optional Kafka message type, and an optional
source display label when the source key differs from the declared topic value.
Its identifier
is stable for a source location:

```text
sha256(role | topic | path | start_line:end_line)[:16]
```

If two extracted endpoints receive the same base identifier, indexing preserves
the first identifier and assigns later endpoints a deterministic content-derived
suffix before persistence. This keeps source evidence distinct when two
interactions share the same file and line span.

The persisted `topic` remains the identity used for matching and graph
relations. `topic_display`, when present, is the source spelling shown by UI
adapters; it never changes topic identity or relation resolution.

`ArchitectureRelation` records an evidenced link between source and target
objects. It includes origin (`code`, `manifest` or `derived`), confidence,
module and source location. It is the persisted source of truth for the
architecture snapshot, including conservative resolved inter-service REST and
Kafka topology relations. `ArchitectureSnapshot` derives its topology edges
from those persisted relations; adapters may use indexed endpoints only to add
route, topic, and source presentation details and do not re-resolve targets or
rescan source.
When two distinct evidences share the same base relation identifier, indexing
retains both by assigning the later relation a deterministic content-derived
suffix.

`JpaEntity` records a qualified Java class name and root-relative source line
for a class annotated with an imported or qualified JPA `Entity` type. The module snapshot
stores these declarations, and one high-confidence `maps` relation links each
owning module to an entity. The fact identifies a Java mapping declaration;
it does not identify a physical table, database engine, or read/write call.

`JpaMethod` records a supported call on a field typed as a source-declared
Spring Data JPA repository. It retains the raw operation, qualified entity,
receiver, module-relative source path, line, and owner method. The module JSON
column is additive; older indexes load an empty operation list until reindex.
Repository and entity resolution must each be unique. A `save` call means a
create-or-update attempt, not a committed insert or update.

`JpaDto` records a Java DTO related to one or more indexed JPA entities or used
in a REST controller signature when the module has no OpenAPI contract. It
stores the qualified name, root-relative source location, detected roles and
conservatively resolved entity names. It does not infer a mapper or a runtime
serialization path.

`GraphFact` is the separate enrichment layer for facts supplied by a user or
by an agent operating through the companion SystemLens skill. It
supports typed nodes and edges, origin, namespace, status, confidence,
module, pass/revision metadata, optional relative evidence and a note. The
`graph_facts` table is not cleared by indexing. `systemlens import-facts` validates
and upserts a manifest by `(namespace, fact_type, manifest_id)`; complete
snapshots can remove stale facts only inside their namespace. Source-derived
relations remain owned by the indexer and cannot be removed through fact import.
Manifest storage IDs must be unique across nodes and edges in one import. A
duplicate storage ID is rejected before any fact is persisted, so one manifest
entry cannot overwrite another fact through SQLite conflict handling.
CLI graph exports merge both layers using the generic dependency node/edge
shape, preserving API and MongoDB associations.

The same `systemlens-ai-graph-v1` manifest may contain optional `endpoints` and
ordered `flows` for direct source analysis. The importer stores manifest
endpoints with `source=manifest` and persists the ordered `CodeFlow` records
only when the source snapshot has no indexed endpoints or modules. This keeps
direct flow projections separate from deterministic source facts while making
the selected-flow HTML view available without running source indexing.

A `flow_data_access` node fact attaches a reviewed data operation to an
existing `CodeFlow` ID. Its name is the resource, its metadata contains
`flow_id`, `action`, and optional `branch`, and its relative evidence identifies
the source line. Import rejects unknown flow IDs atomically. These facts use
the existing `graph_facts` namespace lifecycle and survive source indexing.
The HTML exporter joins them to reachable call flows by ID and keeps them out
of the topology node set. Stale IDs produce no annotation. Imported facts do
not become `CodeFlowStep` records or consistency-diagnostic evidence.

Direct-flow step import preserves the optional `action` field. A step without
`action` remains compatible with older manifests and loads with a null action.
Direct-flow steps may also carry the optional anomaly evidence fields
`phase`, `transaction_id`, `concurrency`, `fields`, `value_dependencies`,
`guard`, `guard_scope`, `isolation`, and `freshness_required`. Value dependency
IDs use `flow_id:step_order`. Concurrency values are validated against the
detector's supported vocabulary. These optional fields are stored in the
existing JSON flow snapshot; older rows load with empty or null defaults.

The `export facts` command serializes a `graph_facts` namespace as the
`systemlens-ai-graph-v1` manifest. It preserves the stored ID, evidence,
status, confidence, pass and revision metadata, and arbitrary metadata. A
reference node is emitted when an enrichment edge targets an object that has no
node fact in the same namespace. This export is round-trippable through
`systemlens import-facts`; source-derived facts remain outside this contract.

`AnalysisProfile` carries persisted extraction choices with the loaded
inventory, currently the `default` or `strategy1` topic convention. CLI,
export, graph, and audit adapters consume this profile. A workspace federation
retains source profiles and rejects a mixture of incompatible topic strategies.

### Materialised contracts

The HTML graph exposes every indexed data resource through the common
`ResourceDescription` shape. The shape is presentation-neutral and applies to
JPA entities, Mongo persistence classes, and indexed DTOs:

```json
{
  "id": "stable resource identifier",
  "identity": "qualified identity",
  "name": "display name",
  "qualified_name": "qualified identity or null",
  "kind": "jpa_entity | mongo_persistence_class | dto",
  "technology": "JPA | MongoDB | Kafka | Java",
  "owner": "owning service or null",
  "module": "owning build module or null",
  "attributes": [{"name": "field", "type": "String"}],
  "source": {"path": "relative/path.java", "line": 1},
  "navigation": {"inspect": "resource identifier", "open_source": "VS Code URI"},
  "usage": {
    "consumers": ["resource identifiers"],
    "producers": ["resource identifiers"],
    "relations": [{"source": "id", "target": "id", "kind": "maps", "label": "maps"}]
  }
}
```

`id` and `identity` identify the resource; `name` is display-only. `kind` and
`technology` describe what was indexed without inferring a storage engine or a
runtime mapping. `attributes` contains only fields extracted from the indexed
source or persisted contract. `source.path` remains relative to the indexed
project root. `navigation.inspect` selects the resource inspector, while
`navigation.open_source` is an explicit source action. `usage` contains only
persisted or deterministically derived relationships.

The HTML resource widget consumes this common shape. A resource click displays
its description and usages in the main details panel. The class name is a
separate inspect action that opens the attribute view, so resource description
and class structure remain distinct.

### HTML graph object descriptions

The HTML graph keeps service and topic descriptions separate from the
`ResourceDescription` contract because they represent runtime architecture
objects rather than data classes. Their stable fields are:

```json
{
  "id": "microservice:inventory-service",
  "name": "inventory-service",
  "kind": "microservice",
  "layer": "application",
  "layer_label": "Application",
  "architecture_layer": "application",
  "project_namespace": "root",
  "project_namespace_path": ".",
  "build_system": "maven",
  "resources": ["GET /api/products/{productId}"],
  "ports": [],
  "vscode_uri": "VS Code URI"
}
```

`MicroserviceDescription` identifies one deployable application and groups its
runtime layer, project namespace, build system, served resources and integration
ports. `ports` is the source of truth for HTTP, Kafka and database boundary
details. `resources` contains served API resources and is not a dependency
edge. `vscode_uri` points to the service directory when source navigation is
available.

```json
{
  "id": "kafka_topic:supermarket.order.placed",
  "name": "supermarket.order.placed",
  "kind": "kafka_topic",
  "owner_service": "order-service",
  "published_message_types": ["OrderPlaced"],
  "consumed_message_types": ["OrderPlaced"],
  "message_type_status": "known",
  "architecture_layer": "application",
  "architecture_namespace": "root",
  "architecture_namespace_path": ".",
  "namespace_source": "writer"
}
```

`TopicDescription` identifies one normalized topic and records its owning
service, message types and resolution status. Producer and consumer services
are represented by the persisted graph links attached to the topic node, not
by guessed lists in the topic description. A known message type is retained
only when the index provides type evidence; unresolved or dynamic topics keep
their status instead of receiving an inferred name.

The three contracts serve different navigation purposes. A microservice click
opens service architecture and integration details. A topic click opens its
producers, consumers and message DTO references. A data resource click opens
its owner, usages and class or schema inspection action. This separation keeps
runtime objects, persisted data classes and message contracts distinct while
giving them the same identity, source and navigation vocabulary.

### Call graph descriptions

The HTML export stores call graphs under `call_graphs`, keyed by a stable graph
identifier. A `CallGraphDescription` describes service-level propagation for
one selected flow or flow group. It does not replace the persisted method call
facts or the individual `CodeFlow` records.
The exporter removes a root graph when its service set and exact directed arc
set are proper subsets of another root graph. Arc identity includes source and
target services, relation kind, label, and endpoint IDs. Equal graphs keep
their separate selectable flows. Identical root-specific shapes share one
stored graph by digest. The trigger catalogue is shared by all roots, so the
digest omits it. Building
each root's graph costs work proportional to its reachable flows. Containment
lookup uses an inverted arc index, or a service index for graphs without arcs.

```json
{
  "nodes": ["order-service", "inventory-service", "restock-service"],
  "node_order": ["order-service", "inventory-service", "restock-service"],
  "traversal_levels": [
    ["order-service"],
    ["inventory-service"],
    ["restock-service"]
  ],
  "call_tree": {
    "levels": [["order-service"], ["inventory-service"]],
    "edges": [{"source": "order-service", "target": "inventory-service", "order": 1}]
  },
  "call_trees_by_flow": {
    "flow-id": {
      "root_flow_ids": ["flow-id"],
      "reachable_flow_ids": ["flow-id"],
      "transitions": {
        "flow-id": [{
          "edge": {
            "source": "order-service",
            "target": "inventory-service",
            "kind": "rest",
            "label": "POST /api/reservations",
            "order": 1,
            "endpoint_ids": ["out-endpoint-id", "in-endpoint-id"]
          },
          "target_flow_ids": ["inventory-flow-id"]
        }]
      }
    }
  },
  "edges": [{
    "source": "order-service",
    "target": "inventory-service",
    "kind": "rest",
    "label": "POST /api/reservations",
    "order": 1,
    "endpoint_ids": ["out-endpoint-id", "in-endpoint-id"]
  }],
  "triggers": {
    "order-service": [{
      "flow_id": "flow-id",
      "kind": "http_entry",
      "name": "POST /api/orders",
      "endpoint_id": "in-endpoint-id"
    }]
  }
}
```

`nodes` is the unique set of participating microservices. `node_order` is the
deterministic display order. `traversal_levels` groups services by directed
reachability; its first level is the graph root set. `call_tree` is the
compact tree projection used for service-level layout and may omit an arc when
the full graph contains a fan-out, fan-in or cycle. `call_trees_by_flow` stores
one causal transition index for each exported flow root, so equivalent service
graphs do not make different events share a root. `root_flow_ids` identifies
the event roots, `reachable_flow_ids` bounds the traversal, and each transition
stores its integration edge plus the matching target flow IDs. An empty target
flow list represents a reachable target with no indexed consumer flow; the
browser renders it as an unknown leaf. The browser creates local occurrences
from this index and applies its cycle and depth rules. `edges` is the complete
service-level graph. Each edge is directed from caller or publisher to callee
or consumer, and `order` is the one-based display order within that graph.
`endpoint_ids` preserves the concrete input and output endpoint evidence used
to render ports and arc labels.

`triggers` maps each participating service to the flow entries that can start
the propagation. A trigger kind is one of `http_entry`, `message_entry` or
`cron_entry`. The `flow_id` links to `code_flows` or `supporting_code_flows`;
`endpoint_id` may be null for a scheduled trigger without an endpoint fact.
The graph description retains cycles and fan-out without merging distinct
flows. A graph with no zero-indegree service uses all participating services as
the deterministic root set.

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
Indexing also records `dynamic_endpoint` diagnostics for unresolved endpoint
expressions. One row groups entries by relative source path and extractor, and
its detail contains only roles, source lines and bounded reason labels. It does
not persist the unresolved source expression or a resolved configuration value.

MongoDB extraction keeps structurally valid declarations and invocations from
a partially parsed Java file while ignoring subtrees that contain an error or
missing token. The file-level diagnostic remains visible so partial coverage is
never presented as a complete parse.

`CodeFlow` is a persisted, ordered potential path through one Java method or a
chain of Java methods resolved by the selected method-call engine. Its
first `CodeFlowStep` is an indexed HTTP/Kafka entry point or a source-evidenced
`@Scheduled(cron = "...")` trigger; later steps are HTTP calls, Kafka
publications, or MongoDB/JPA reads and writes in the entry method or a reachable
method. Steps retain relative evidence paths and
line ranges. Flow identity uses the module, relative path, qualified method,
and trigger semantics rather than line numbers, so ordinary line movement does
not replace the logical flow. If overloaded or generated declarations still
produce the same identity, indexing keeps the first historical ID and adds a
stable route discriminator to later IDs before persistence. The materializer runs during indexing, while
`systemlens flows calculate` rebuilds endpoint and data paths from the
stored integration-method and CodeQL-edge snapshots with the independent
enrichment topology; neither export nor query runs it. The same-method pass
scans the endpoint and data-operation inventory plus eligible Java AST nodes.
The directed method-graph pass visits reachable methods and edges per entry
within the configured hop bound. Maven
`target/` and Gradle `build/` output trees are excluded from the inventory, so
copied resources and generated classes cannot duplicate source facts. A
same-method relation remains `potential` with medium confidence because static
lexical order does not prove branch execution. A method with multiple indexed
Kafka inputs and a `join` call gets an explanatory join reason; no extra
dependency or unconditional path is inferred. Method-call facts join
persisted AST method facts to form interprocedural flows; `method_call` steps keep
the call-site line. For CodeQL, reachability is computed from indexed output
methods backwards through their callers until indexed input methods are reached.

Same-method MongoDB steps retain the raw operation and an optional `action`:
`read`, `insert`, `update`, `delete`, `create_or_update`, or `read_update`.
The shared classifier in `src/systemlens/domain/mongo_operations.py` derives
read and write facets for relations and exports. `read_update` creates both
facets but keeps the existing broad `data_write` step kind. `bulkOps` is a
builder; it and unsupported operations create no flow or relation effect.
Older step JSON loads with `action=null`. This action is an API-level attempt,
not evidence of a committed database change. `flows calculate` still rebuilds
paths from persisted module inventory, method, and call-edge facts. A MongoDB
call joins one method only when module, relative source path, owner name, and
line range identify it uniquely. A bounded directed traversal from each
indexed entry emits a separate potential path to each reachable operation,
including calls in helper methods. An operation reached on a sibling branch
does not become a step of an unrelated endpoint-to-endpoint path. A possible
dispatch lowers confidence; neither branch execution nor transaction success
is inferred. The current flow hop bound also bounds these data paths. Without
a source-backed call edge, only same-method data operations are available.

JPA data steps use `jpa:<module>:<qualified entity>` as their logical resource;
the action is `read`, `delete`, or `create_or_update`. An HTTP entry labels a
reached blocking JPA access synchronous, and a Kafka entry labels it
asynchronous relative to its producer. These labels describe the entry path,
not transaction completion. A repository call in an ambiguous method or an
unsupported API creates no step. The operation inventory is persisted and
`flows calculate` joins it to the stored method and call-edge snapshot.

Without CodeQL, the source symbol index can add receiver-typed calls only when
one source method matches the receiver and signature. These paths have low
confidence and source-located `method_call` steps. Ambiguous types or overloads
produce no path. The pass parses eligible Java sources once per flow rebuild
and traverses their method invocations; it adds no runtime or cross-service calls.
For a uniquely inherited `final` method, the source symbol pass retains its
body as a possible target when generic parameter substitution is unresolved.
The inferred edge has low confidence and remains in the persisted call graph;
ambiguous inherited declarations still produce no edge.

The transitive query has no business-level hop or global path limit; the
configured complete-pass deadline remains the operational safeguard. The legacy
`analysis.codeql_max_hops` remains for bounded route reconstruction; it does
not authorize inferred call edges.
By default, SystemLens creates one
temporary CodeQL database for the whole indexed repository, with CodeQL's
`--build-mode=none` source-only mode, then deletes it. The resulting calls are
partitioned by caller project only for progress checkpoints. With HTML progress,
global flows are materialized once, reused for final persistence, and filtered
by reported call-site evidence for partial views. With an explicit global
database, the call query is scoped to each source-owning project while
retaining cross-project callees. Direct answers without an intermediate
witness are withheld until the final checkpoint. The complete global call
list, including cross-project references, is used for final method-flow
materialization. If the scoped queries produce no calls because the supplied
database uses a different source-root spelling, indexing retries one unscoped
query before materializing the checkpoints.
The call facts are aggregated before method-flow materialization and persisted
as root-relative `codeql_call_edges`. A missing or untrusted callee
path may join one unique qualified indexed method, but is explicitly
low-confidence and retains signature-join provenance; ambiguous names remain
unresolved. An
explicit CLI database remains a global-database override. When the CodeQL executable is
absent, indexing retains only same-method and source-symbol flows and reports
that limitation. When CodeQL represents a lambda as a synthetic
anonymous callable, SystemLens attributes its call site to the narrowest
persisted Java method enclosing that line in the same file. Dynamic dispatch,
reflection, and runtime-only routing remain outside this deterministic layer.
In buildless mode, a transient AST symbol index covers source-declared
qualified types, imports and transitive `extends`/`implements` relations,
including source files without endpoint facts. It may bridge an
abstract/interface method to one unique compatible implementation and recover
receiver-typed helper calls by arity/signature. Duplicate types in the caller's
module, ambiguous overloads, unknown receivers and unsupported generic/vararg
substitutions stay unresolved. When duplicate qualified types exist in other
modules, resolution first prefers the caller's module. Synthetic edges are
always `possible`/`low` and never replace exact CodeQL evidence.
For a virtual call, CodeQL's unique `exactVirtualMethod` target is retained at
medium confidence. If no unique target can be proven, its `viableCallable`
candidates are retained individually at low confidence; SystemLens does not
select an implementation on Spring bean metadata alone. The analysis profile,
including the selected method-call engine, availability, activation and its bounds, participates in the
code-flow signature so switching profile recalculates unchanged repositories.

The flow join groups AST and interprocedural candidates with deterministic
in-memory keys. Parallel candidate routes are grouped by their source endpoint
identities, module, input flow, input Java type, output flow, output Java type,
and status;
the representative with the strongest confidence, then the shortest route, is
persisted. The indexing deduplication uses plain deterministic collections; HTML
layout remains a rendering concern handled by the browser graph stack. Each exported code flow
also carries a NetworkX-derived service subgraph and deterministic component
order; the Flux view uses that snapshot instead of reconstructing the call
sequence from display labels.
Kafka publications are not expanded into cross-service persisted CodeFlow
candidates. The flow views can nevertheless follow a publication to every
opposite-role endpoint with the same protocol and concrete topic/route, even
when the endpoint's Java type is unknown or differs. A publication without an
input trigger does not become a flow root. A
publisher explicitly annotated with a cron expression still creates a local
source flow whose first step is `cron_entry`, followed by its publication.
Known message types must match when both endpoints provide one. Missing type
evidence does not prevent the topology relation, while conflicting known types
prevent it.
The exported flow-graph projection builds directed candidate arcs from the
flow-associated OUT ports, identifies trigger flows with no incoming flow
arc as roots, and traverses those flows in breadth-first order. A trigger is only
an `http_entry`, `message_entry`, or `cron_entry` first step; a publication or
HTTP call is an internal output. For each visited module, the traversal follows
only its flow-associated OUT ports and valid output-to-input topology arcs,
then visits the flow whose trigger is the target IN port.
Expanded OUT ports and endpoint pairs prevent duplicate work and terminate
cycles without suppressing a module reached again through another branch.
Fan-in, fan-out, and reachable modules without a matching arc remain visible.
If the candidate graph is cyclic and has no zero-indegree module, all flow
modules are used as deterministic roots while preserving arc direction.
The snapshot retains the traversal levels and call tree used to assign arc
orders before copying those orders onto the final call graph.
The graph records external trigger events under their owning microservices.
The browser receives these canonical service arcs with their endpoint identities
and does not reconstruct a second set from display labels.
The `flows list` query derives a read-only `root` boolean from the same
OUT-to-IN topology: a flow is a root when no persisted flow reaches its input
endpoint. `flows show` uses the persisted endpoint snapshot to connect an OUT
endpoint to downstream matching IN endpoints and emits that reachable forest
as `tree`; cycles and repeated flow IDs are emitted only once.
The CLI flow list exposes every persisted integration flow. HTML export lists
only uncovered call roots in `code_flows`. It selects zero-indegree trigger
flows first, then one stable representative for each component not reached
from them, including isolated cycles. A flow reached by a selected root stays
in `supporting_code_flows` so the browser can render its call-tree occurrence.
An unmatched or partially reconciled flow stays selectable when no indexed
root covers it. Per-service HTML flow counts include selectable roots only.

Data-access terminal paths stay in the compatible SQLite flow snapshot as
evidence, but do not become selectable call flows. Export builds reachability
over the full snapshot and stores each access once under its internal flow ID.
`flow_data_access_flow_ids_by_root` lists the access-bearing flows reachable
from each selectable root. Accesses at the same source site remain separate
when they belong to different internal flows.

The browser associates an access with a call-tree service occurrence when its
source flow ID matches that occurrence, or when the service, input endpoint,
and handler method match. A leaf without a call-flow continuation uses its
exact input endpoint. Cron flows without an endpoint use the same trigger
kind, name, and handler method. An unmatched access gets no service badge.

The exporter stores the first source-backed method call once per access flow
when that call exists. The browser marks an access as route-supported when its
flow ID is on the occurrence or its first method call matches an occurrence
flow. Other same-handler accesses remain possible but add `?` after the entity
name; this marker describes association uncertainty, not an unknown CRUD action.
Unknown leaves retain accesses reached through their exact input endpoint.

The call tree groups access and diagnostic annotations by full resource ID at
each matched occurrence, then displays one badge with the simple entity name.
It unions certain operation letters in CRUD order: `insert` or `create` maps
to `C`, `read` to `R`, `update` to `U`, and `delete` to `D`.
`create_or_update` contributes `C/U` as a separate alternative; `read_update`
contributes both `R` and `U`. An unknown write contributes no operation letter;
if no known letter remains, the badge shows `?`. A diagnostic makes the
grouped badge selectable.

The architecture graph and left widget receive no data-access badges. This
projection does not infer a linear order or change persisted flow IDs.

An imported access with a simple entity name uses a source resource ID only
when exactly one resource with that name exists in the same module. Otherwise
the exporter keeps the imported name and its unresolved identity separate.
The browser qualifies duplicate short names with their package, module, or
an uncertain-identity marker.

An async write is a database write in a Kafka consumer handler reached after a
message publication. A sync read is a database read on an HTTP entry path.
Reads in Kafka consumers and writes on other trigger types have no branch
classification. Async-write/sync-read diagnostics pair these classified sites
and matching resources across handlers reachable from the same persisted trigger.
Matching causal IDs support the pair; conflicting IDs or different key
expressions remain `insufficient_evidence`. A missing branch label is unknown
and does not classify an access as synchronous. A visibility guarantee requires
an explicit `visible` completion and the same non-empty expected version on
both accesses. Each exported diagnostic retains its owning write and read flow
IDs, operation endpoint IDs, and the endpoint paths from the trigger to each
operation. Export also includes each operation's source path, line, module,
action, and trigger endpoint as `write_site` or `read_site`. The renderer uses
the endpoint paths to highlight the causal sequence. It places selectable
diagnostic badges on call-tree service occurrences only when the operation's
module and trigger endpoint match that occurrence. The detector groups sync
reads by resource and compares an async write with reads of the same resource
or reads whose resource is unknown. A write with an unknown resource remains
eligible for every read. Inverted flow-to-root sets find shared roots without
scanning every root for each pair.

Advanced diagnostics require exact resource/key identity and positive
`concurrency` evidence (`parallel`, `independent_handlers`, or `reentrant`).
An async branch label by itself does not prove overlap. This gate applies to
`potential_concurrent_read_write` and `potential_concurrent_write` candidates.
The classifier also recognizes a read-modify-write pair only when both writes
depend on reads in distinct named transactions, and recognizes dirty reads
only when the writer is explicitly uncommitted and the reader explicitly uses
read-uncommitted isolation. A freshness requirement can refine a concurrent
read/write interaction to `potential_read_write_anomaly`. Matching guard kind
and exact `resource:key` scope produce `conflict_protection_identified`.

The renderer includes action, phase, transaction, fields, guards, causal/order
evidence, limitations, and paired read source locations in service tooltips.
Advanced source-indexed facts remain unavailable unless supplied in enriched
flow records; missing metadata creates no concurrency claim. These findings
are static candidates, not observed incidents.

For W writes, R reads, and P candidate pairs, indexing costs O(W + R + total
reachability entries), followed by O(P) overlap checks. Root intersection cost
depends on the number of roots containing each candidate's flows. P can still
reach W × R when resources are shared or unknown; output size also grows with
the number of common roots.

When a read precedes a write in one ordered flow, or its call site precedes
the publication in the same entry source file, the pair is insufficient
evidence for a stale read after that write. This source-order guard does not
prove runtime execution order across branches.
The HTML export projects data sites onto every reachable call root using
persisted endpoint arcs and flows sharing the exact trigger endpoint. This
projection does not merge sibling paths into one execution sequence. Its work
is bounded by the number of exported flow roots times reachable flow edges
and access steps; it does not reopen Java source files.

Each persisted `CodeFlow` also carries a `reconciliation` status. `complete`
means every endpoint step exists in the same snapshot and every cross-service
effect has a matching persisted topology edge; `partial` means
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

After each completed CodeQL project, the indexing service persists a partial
`code_flows` checkpoint with `code_flow_snapshot_status=partial`. The optional
`--codeql-progress-html FILE` delivery adapter renders the same checkpoint to
`FILE` by atomic replacement and labels it as provisional with its
completed-project count. Each checkpoint also carries the source-owning Maven
module and the Java methods associated with its indexed IN and OUT integration
points. Partial checkpoints may omit later method-call facts; the final
reconciliation replaces them and marks the snapshot complete.
The join emits periodic progress logs while traversing input methods and
call-graph transitions. After each join batch, the current provisional
`code_flows` snapshot is committed, allowing exports to retain the arcs found
before an interruption. These snapshots remain partial until final
reconciliation.
The join offset, compatibility signature, and ordered input-method signature
are stored in `meta`; an explicit resume request can therefore skip completed
IN methods after a process crash. The resume path is valid only while source
and analysis-input signatures remain unchanged; generated presentation files
such as HTML exports are ignored. If the persisted input-method signature no
longer matches the current index, the stale cursor is discarded and the join
restarts from the beginning.

Kafka endpoint identifiers are reconciled as topology facts, not composed into
new persisted code-flow candidates. Known message types must match when both
endpoints provide one; missing type evidence remains compatible. The CodeQL
call graph remains limited to source-backed Java method calls, so a Kafka topic
does not create an internal method edge.

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

Schema version 32 adds the persisted `codeql_call_edges` source-backed method
call graph to schema version 31, which added `code_flows.alternative_count`
route metadata to the schema 30 `code_flows.reconciliation` status and
migrates older snapshots with `unknown`. Schema version 29 adds the
`asyncapi_contracts` table for validated AsyncAPI
documents owned by a module and rendered from the persisted snapshot. When an
export contains at least one such document, it embeds the Apache-2.0 licensed
AsyncAPI web component (version 3.1.8) and its distributed stylesheet; exports
without AsyncAPI facts do not carry this payload. The HTML renderer stores the
viewer assets as deterministic gzip data encoded in base64. The browser loads
them once, on the first contract inspection, using `DecompressionStream`.
This reduces file size and defers viewer execution, but does not bound graph
memory usage. The persisted graph JSON remains unchanged. Schema
version 28 adds the `integration_methods` table for AST method evidence
and input/output endpoint identifiers. Schema version 27 added the
`code_flows` table and its module/path indexes. The
migration is additive and occurs when the writable store opens, before the
index transaction. Stored steps are JSON projections of immutable domain
facts; source paths remain relative to the indexed root. Integration-method IDs
are stable hashes of the module, relative path, qualified method and parameter
signature. If malformed or generated Java repeats that signature, later
declarations receive a deterministic source-coordinate suffix so the snapshot
remains insertable.

Code-flow IDs are unique within every persisted snapshot, including provisional
CodeQL checkpoints and `flows calculate` results. A collision preserves the
first stable ID and assigns later candidates a deterministic content-derived
suffix before SQLite persistence.

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
