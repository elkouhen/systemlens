# Architecture decision records: SystemLens (`systemlens`)

## Reading guide

ADRs explain *why* durable constraints exist. They do not replace the current
functional or technical contract.

| Topic | ADRs |
|---|---|
| Local, conservative extraction | [ADR-1](#adr-1-local-static-architecture-analysis), [ADR-3](#adr-3-conservative-static-resolution) |
| Snapshot storage and compatibility | [ADR-2](#adr-2-sqlite-is-the-local-fact-store), [ADR-7](#adr-7-publish-each-index-as-an-atomic-sqlite-snapshot), [ADR-11](#adr-11-separate-module-identity-from-its-display-alias), [ADR-81](#adr-81-keep-a-dispatch-exact-only-when-its-implementor-set-is-complete) (proposed), [ADR-80](#adr-80-follow-the-build-tools-project-model-for-module-ownership) |
| Delivery and graph projection | [ADR-8](#adr-8-persisted-relations-are-the-canonical-architecture-projection), [ADR-9](#adr-9-exports-never-enrich-a-snapshot-from-live-source-files), [ADR-23](#adr-23-mcp-control-in-two-phases-with-a-graph-enrichment-layer) |
| Optional or repository-specific behaviour | [ADR-5](#adr-5-strategy1-conventions-are-opt-in), [ADR-12](#adr-12-kubernetes-discovery-is-explicit-and-snapshot-based) |
| Product namespace and module vocabulary | [ADR-6](#adr-6-systemlens-is-the-public-product-and-state-namespace), [ADR-24](#adr-24-use-module-terminology-for-structural-project-grouping) |
| Indexed contracts and implementation boundaries | [ADR-25](#adr-25-separate-indexed-dto-materialization-from-graph-rendering), [ADR-26](#adr-26-organize-implementation-modules-by-architectural-ownership) |
| Potential code flows and call-graph resolution | [ADR-27](#adr-27-persist-potential-code-flows-separately-from-topology-and-runtime-truth), [ADR-27b](#adr-27b-preserve-stronger-enrichment-facts-and-label-topology-reconciliation), [ADR-29](#adr-29-ask-codeql-directly-for-input-to-output-reachability), [ADR-30](#adr-30-resolve-fallback-dispatch-by-qualified-symbols-and-union-partial-evidence), [ADR-38](#adr-38-persist-the-source-backed-codeql-call-graph), [ADR-43](#adr-43-preserve-receiver-context-in-inherited-dispatch), [ADR-78](#adr-78-hold-a-codeql-reachability-flow-to-the-joins-rules) |
| Graph rendering and integration evidence | [ADR-28](#adr-28-render-selected-call-graph-arcs-with-orthogonal-port-routes), [ADR-31](#adr-31-resolve-declarative-http-clients-and-bounded-url-helpers-conservatively), [ADR-77](#adr-77-match-rest-routes-by-spring-mvc-specificity), [ADR-32](#adr-32-require-topic-and-payload-type-for-an-asserted-kafka-service-arc), [ADR-33](#adr-33-keep-topics-in-selected-call-graph-views), [ADR-35](#adr-35-use-shared-kafka-topics-before-payload-type-resolution), [ADR-76](#adr-76-type-kafka-payloads-with-one-shared-scanner-rule), [ADR-79](#adr-79-treat-generic-payload-carriers-as-unknown-kafka-types), [ADR-50](#adr-50-present-data-access-as-call-flow-annotations), [ADR-51](#adr-51-scope-data-access-annotations-to-selected-call-graphs), [ADR-52](#adr-52-classify-async-writes-by-message-consumption), [ADR-53](#adr-53-list-only-uncovered-call-roots-in-html), [ADR-54](#adr-54-attach-imported-data-access-to-indexed-call-flows), [ADR-59](#adr-59-pair-async-writes-and-sync-reads-along-call-graph-branches) |
| Runtime observations | [ADR-60](#adr-60-compare-static-topology-with-aggregated-elastic-apm-metrics) |
| HTML widget readability | [ADR-63](#adr-63-reserve-widget-footprints-and-set-a-10-pixel-type-floor), [ADR-68](#adr-68-dock-the-node-inspector-beside-the-graph) |
| Call-graph engines | [ADR-61](#adr-61-make-the-call-graph-engine-a-pluggable-choice), [ADR-62](#adr-62-attribute-anonymous-class-bodies-to-the-creating-method), [ADR-66](#adr-66-make-the-source-engine-the-default), [ADR-67](#adr-67-split-codeql-and-the-call-graph-stage-into-dedicated-modules), [ADR-70](#adr-70-give-the-source-engine-codeqls-interface-witness-and-dispatch-gaps), [ADR-71](#adr-71-scope-external-interface-dispatch-to-the-callers-visible-modules), [ADR-75](#adr-75-make-constructors-and-initializers-part-of-the-call-graph); proposed: [ADR-72](#adr-72-keep-dispatch-possible-when-an-implicit-implementation-exists), [ADR-73](#adr-73-scope-every-dispatch-to-the-callers-visible-modules), [ADR-74](#adr-74-dispatch-calls-on-external-classes-to-source-overrides), [ADR-83](#adr-83-check-a-supplied-codeql-database-against-the-indexed-sources), [ADR-84](#adr-84-reuse-the-automatic-codeql-database-and-drop-reachability-from-the-index) |
| Index timeout and partial snapshots | [ADR-34](#adr-34-commit-a-partial-snapshot-after-a-codeql-timeout) |
| Progressive CodeQL persistence | [ADR-37](#adr-37-publish-codeql-flow-checkpoints-during-indexing) |
| Offline source generation | [ADR-36](#adr-36-run-maven-source-generation-offline) |

## ADR-1: Local static architecture analysis

**Status:** Accepted.

**Context:** Architecture facts need to be reproducible offline and traceable
to source locations without a separate analysis runtime.

**Decision:** Parse Java source locally with Tree-sitter and derive static Spring
REST, Kafka and module facts from AST nodes and deterministic local
configuration. Use the locally installed CodeQL CLI by default to create a
temporary source-only Java call graph for bounded interprocedural flow facts.
For `build-mode=none`, stage only Java source files in a temporary projection;
do not expose Maven or Gradle descriptors to CodeQL, because its Java no-build
extractor may otherwise invoke those tools for dependency discovery and access
remote registries.

**Consequences:** The project has no remote analyzer or source-code search
dependency. CodeQL is an optional local prerequisite: when absent, the index
keeps AST-only results and reports the reduced interprocedural coverage. Dynamic
values are surfaced as unresolved facts instead of being guessed. Offline
CodeQL staging may reduce resolution of external types, which is an explicit
accuracy trade-off for deterministic, network-independent indexing.

## ADR-2: SQLite is the local fact store

**Status:** Accepted.

**Context:** Incremental inventory, MCP queries and graph rendering require a
portable local state.

**Decision:** Persist files, endpoints, modules, dependencies and architecture
relations in `.systemlens/findings.db`. The filename is retained for compatibility
with existing installations.

**Consequences:** The database is private implementation state; clients use CLI
and MCP contracts rather than writing SQL. Legacy external-analyzer results are
cleared during the first AST-only index run.

## ADR-3: Conservative static resolution

**Status:** Accepted.

**Context:** HTTP paths and Kafka topics are often composed from properties,
constants or runtime values.

**Decision:** Resolve only literals and supported local Spring expressions. Mark
the rest as dynamic and preserve source evidence.

**Consequences:** The inventory favours trustworthy partial results over
plausible but unsupported dependencies.

## ADR-4: Workspace federation is read-only

**Status:** Accepted.

**Context:** A parent workspace may contain independently indexed services.

**Decision:** Load service indexes read-only and normalize them before graph and
audit queries.

**Consequences:** Federated exploration never rewrites another repository’s
index and reports incomplete or stale sources as warnings.

## ADR-5: Strategy1 conventions are opt-in

**Status:** Accepted.

**Context:** Some repositories encode Kafka and REST dependencies through
project-specific naming conventions.

**Decision:** Keep these in `--strategy strategy1`, separate from the
default AST extractor. Strategy1 also enables the repository-specific layer
conventions: project namespace `PORTAIL` identifies API modules, project
namespace `CYCLE-DE-VIE` identifies Orchestration modules, and the `DOMAIN-*`
module prefix identifies Domain modules. A project namespace is a parent
directory containing projects, not a Kubernetes namespace. Other layer-name
prefixes and suffixes (`api-*`, `infra-*`, `shared-*`, `repository-*`, and
related forms) are also Strategy1-only conventions.

**Consequences:** Default indexing remains framework-oriented and portable;
Strategy1 facts are explicitly identified as convention-derived.

## ADR-5a: Strategy1 conventions live behind one pack boundary

**Status:** Accepted.

**Context:** Strategy1 rules had grown across Kafka extraction, REST target
resolution, OpenAPI declaration invalidation, and layer classification. Topic
request/reply pairing was retired because a naming convention does not prove a
runtime exchange. Passing a `strategy1` boolean through generic modules
made it easy for repository-specific rules to leak into portable analysis.

**Decision:** Place Strategy1 rules in `systemlens.conventions.strategy1` and
make generic indexing, relation, graph, and rendering modules call that pack's
explicit operations. Scanner modules retain compatibility façades for their
historical public imports, but the indexing pipeline uses the convention pack.
The pack owns convention normalization and matching; the core retains CLI
selection, persisted profile metadata, SQLite facts, and generic graph/export
mechanics.

**Consequences:** The default profile does not invoke Strategy1 matching.
Future repository convention packs get a clear extension boundary without a
schema fork, while existing third-party scanner imports remain usable.

## ADR-6: SystemLens is the public product and state namespace

**Status:** Accepted.

**Context:** The former product names and commands (`cccr`, `archlens`, and
`codeatlas`) were
not suitable for users, while the tool's purpose is to make a local
architecture view easier to read.

**Decision:** Rename the distribution, Python package, CLI command, MCP server
name, generated-export labels and state directory to `SystemLens` / `systemlens`.
The project state is now stored in `.systemlens/`.

**Consequences:** This is a breaking rename. Existing `.cccr/`, `.archlens/`,
and `.codeatlas/` configuration and index data are not read by SystemLens: run
`systemlens init` and `systemlens index` in each repository to create a fresh
`.systemlens/` inventory. Trace environment variables use the `SYSTEMLENS_`
prefix.

## ADR-7: Publish each index as an atomic SQLite snapshot

**Status:** Accepted.

**Context:** An index refresh replaces file hashes, endpoints, modules,
dependencies, relation facts and their signatures. Publishing only part of
that sequence would make all read adapters report an incoherent architecture.

**Decision:** `index_repo` executes its complete refresh inside one explicit
`Store.transaction()` using SQLite `BEGIN IMMEDIATE`. The store commits only
after the complete relation projection is materialized, and rolls back on any
exception. Schema setup remains a separate opening-time concern; read-only
connections never mutate the database.

**Consequences:** Concurrent readers observe the previous complete snapshot
while a refresh is in progress, then the next complete snapshot after commit.
An index holds the single-writer SQLite lock for its run; this favours a
trustworthy local inventory over concurrent writers.

## ADR-8: Persisted relations are the canonical architecture projection

**Status:** Accepted.

**Context:** Endpoints, request-time graph edges, dependency dictionaries and
renderer-specific links previously represented overlapping architecture facts
with independently implemented rules.

**Decision:** `architecture_relations` is the canonical persisted relation
projection. Indexing materializes evidenced endpoint, module, data-store and
resolved inter-service topology relations into it. Read adapters receive the
immutable `ArchitectureSnapshot`; the legacy `ArchitectureCatalog` name is a
compatibility alias for that projection.

**Consequences:** Relation identity, provenance and confidence are stable
across local catalog and coverage adapters. Graph-shaped renderers can still
adapt endpoint evidence for route and topic labels, but must not independently
infer service topology or rescan repository sources.

## ADR-9: Exports never enrich a snapshot from live source files

**Status:** Accepted.

**Context:** HTML export previously reopened OpenAPI documents and recursively
parsed Java DTO files, allowing a single export to mix indexed topology with a
later working-tree revision.

**Decision:** Renderers only consume loaded snapshot facts. They display
indexed Kafka payload identities and OpenAPI evidence paths, but do not parse
contract or Java source files. A later detailed-contract feature must add an
explicit indexed data model first.

**Consequences:** Exports are reproducible and work without source trees. DTO
field and enum inspection is deliberately unavailable until its facts are
persisted at index time.

## ADR-10: Persist the analysis profile with each snapshot

**Status:** Accepted.

**Context:** Strategy-dependent extraction facts were persisted, but delivery
adapters could silently select a different default while reading or refreshing
the same index.

**Decision:** Load the persisted topic strategy as an immutable
`AnalysisProfile` with every architecture inventory. CLI, MCP, graph, audit,
and export adapters consume that profile. A federation rejects source indexes
whose profiles are incompatible.

**Consequences:** Every reader of a snapshot uses its persisted profile, so a
Strategy1 convention cannot appear in a default inventory or disappear from a
Strategy1 one because of the delivery path. Reindexing is different: each
pass applies `--strategy`, or else `analysis.strategy` from `config.yml`,
never the persisted profile. The configuration file is the durable choice.

## ADR-11: Separate module identity from its display alias

**Status:** Accepted.

**Context:** Maven artifact IDs and Gradle project names are not unique within
all workspaces or across independently indexed federated repositories.

**Decision:** Persist a collision-safe module identity. It equals the build
name when unique and is qualified with the relative module path when a
collision exists. The repository root uses the explicit `racine` segment rather
than the ambiguous `.` path marker. Endpoint, relation, dependency, and
federation keys use the identity; the build name remains a display alias.
Direct service indexes are namespaced at the federation boundary.

**Consequences:** Ambiguous aliases are not resolved implicitly, and two
services with the same display name coexist without data loss. Existing SQLite
indexes receive the additive `modules.identity` migration on their next
writable open.

## ADR-12: Kubernetes discovery is explicit and snapshot-based

**Status:** Accepted.

**Context:** CPU and memory dimensions are runtime deployment facts, but a
normal source index must remain usable offline and without cluster credentials.

**Decision:** `systemlens index --kubernetes` invokes the local `kubectl` CLI
against its active context and records Deployments and StatefulSets. It attaches
a workload only when its Kubernetes name exactly matches an indexed project
name, and aggregates requests and limits across regular containers. Init
containers are excluded because their scheduling resources are not steady-state
service capacity.

**Consequences:** Kubernetes access is opt-in and may fail if `kubectl`,
credentials, or API connectivity are unavailable. The resulting dimensions are
persisted in the SQLite snapshot, so catalog and HTML export do not re-query a
cluster after indexing.

## ADR-23: MCP control in two phases with a graph enrichment layer

**Status:** Accepted.

**Context:** An AI can identify repository conventions that deterministic
extractors cannot prove, but those claims must not overwrite source evidence.

**Decision:** The MCP exposes `index_repository`, followed by dedicated
operations to add, inspect, and remove graph facts. Added facts are persisted
in `graph_facts`, separately from relations derived from source code.

**Consequences:** This separation lets an AI complete conventions that cannot
be extracted deterministically, while ensuring reindexing preserves enrichment
and MCP deletion never destroys source evidence. `architecture_graph` merges
both layers for reading. Added evidence paths remain relative to the repository.

## ADR-24: Use module terminology for structural project grouping

**Status:** Accepted.

**Context:** The interactive architecture export called its structural
directory hierarchy a namespace view. That wording confused project grouping
with Kubernetes namespaces and enrichment-fact namespaces, even though neither
defines the hierarchy.

**Decision:** The interactive export calls this hierarchy `Modules`. An
architecture module is a structural group that may contain child modules and
projects. Maven/Gradle build units are called `Projects` in user-facing text.
Membership comes from project directory paths; Kubernetes namespaces remain
runtime metadata only. Existing `project_namespace*` export fields remain
compatibility aliases for the canonical `cluster_path` field.

**Consequences:** Users select `Graph`, `Layers`, or `Modules` without implying
a Kubernetes grouping or confusing build projects with structural containers.
Internal identifiers such as `cluster_path`, persisted `module` fields, and
Python model names remain unchanged for index and JSON compatibility. The CLI
uses `projects` for the Maven/Gradle catalog and build export, and `export
modules` for the structural hierarchy; legacy unambiguous spellings remain
hidden aliases.

## ADR-25: Separate indexed DTO materialization from graph rendering

**Status:** Accepted.

**Context:** Kafka DTO discovery was implemented as a private HTML-renderer
helper that `indexer.py` imported directly. The same renderer also combined
source extraction, graph-model projection, serialization, and standalone HTML
assembly. This reversed the intended dependency direction and made indexing
depend on an output adapter.

**Decision:** `indexing/dto_inventory.py` owns the conservative Java DTO closure created
during indexing and exposes one public materialization function. The persisted
snapshot remains the only DTO input to exports. Within `render/`,
`graph_view_model.py` projects snapshot facts into browser data, while
`html_export.py` only serializes and assembles the standalone document. Graph
CSS and JavaScript remain source modules with deterministic numeric ordering.

**Consequences:** Indexing no longer imports rendering code, DTO resolution can
be tested independently, and HTML assembly stays small. Adding a browser field
requires changing the view-model projection, while changing Java DTO discovery
requires changing the inventory module and its focused tests. Asset ordering is
an explicit build-time convention rather than an implicit monolithic file.

## ADR-26: Organize implementation modules by architectural ownership

**Status:** Accepted.

**Context:** Most implementation modules historically lived directly under
`src/systemlens/`. Their imports formed a valid acyclic graph, but the
filesystem did not communicate the documented delivery, application, domain,
discovery, indexing, infrastructure, rendering, and persistence boundaries.
This also allowed persistence to depend accidentally on discovery-owned types.

**Decision:** Keep only `__init__.py` as a file at the `systemlens` package
root. Place implementations in ownership packages: `application/`, `domain/`,
`discovery/`, `indexing/`, `infrastructure/`, `delivery/`, `render/`,
`scanner/`, and `storage/`. Console entry points target `delivery/` directly.
Compatibility-only root-level packages are removed; all code imports the owning
implementation package directly. Domain modules may not import outer layers;
an architecture test enforces both dependency direction and the
absence of flat root implementations.

**Consequences:** Directory structure now exposes ownership before a file is
opened, and domain facts no longer depend on Java, build, Kubernetes, or SQLite
adapters. Moving a module requires updating internal imports and documentation,
while compatibility packages keep existing Python and console consumers
working. Large files may still be split further inside their owning package
without changing the top-level layer model.

## ADR-27: Persist potential code flows separately from topology and runtime truth

**Status:** Accepted.

**Context:** Topology paths connect services through known integrations but do
not prove that consuming one input causes every output exposed by the same
service. Runtime traces are not guaranteed to be available, while source code
can still establish bounded local relationships.

**Decision:** During indexing, materialize AST method facts for Java methods
that contain indexed HTTP/message inputs or outputs, and retain same-method
flows. When the local CodeQL CLI is available, create a temporary source-only
Java database with `--build-mode=none`, then join these facts with
CodeQL-resolved static method calls to materialize bounded interprocedural
flows. `--codeql-database` remains an override for an existing database.

Preserve call-site evidence and label every flow `potential` with medium
confidence when dispatch is unique; retain multiple CodeQL virtual-dispatch
candidates at low confidence instead of choosing an implementation. Store flows
separately from topology and runtime facts. The
temporary database and supplied database path are never persisted; an absent
CodeQL CLI is reported and leaves AST-only flows available.

The temporary query pack pins its Java library version and resolves only the
locally provisioned package cache. The analysis profile records CodeQL
availability, activation and explicit depth/transition bounds so a profile
change invalidates the persisted code-flow snapshot. Spring bean annotations
are not used to choose one virtual-dispatch implementation: qualifiers,
profiles and runtime factory conditions remain ambiguity rather than guessed
execution evidence.

Concrete Kafka publications may continue into persisted concrete Kafka entry
flows. The join is bounded to four asynchronous hops and prevents a consumer
flow from recurring in one candidate, so cyclic topics cannot grow the result
without bound. Dynamic topics and producer flows with a later external effect
are not composed because the static evidence cannot support a complete linear
causal path in those cases.

**Consequences:** Users can inspect useful causal candidates without the broad
consumer-to-all-publications assumption. Conditional execution, unresolved
dispatch, reflection, and runtime-only routing remain explicit blind spots.
Indexing performs AST traversal and a bounded CodeQL call-graph join when the
local prerequisite is available, while exports and queries continue to consume
only persisted snapshots.

## ADR-27a: Use AST symbol facts to constrain buildless call-graph fallbacks

**Status:** Superseded by
[ADR-30](#adr-30-resolve-fallback-dispatch-by-qualified-symbols-and-union-partial-evidence).

**Context:** A source-only CodeQL database can lack enough dependency/type
information to resolve a Java interface or bean call. The existing fallback
must remain conservative, but matching only a same-module method name can
confuse overloads or reinterpret an external `send` call as a local method.

**Decision:** During flow materialization, build a transient AST symbol index
from the already indexed Java sources. For unresolved fallback calls, use the
invocation arity and the source-declared `implements`/`extends` hierarchy to
narrow endpoint-bearing output candidates. Do not persist this auxiliary index
or promote its candidates above `possible`/low confidence. Treat methods that
already contain an indexed output endpoint as terminal for this AST fallback;
the full CodeQL graph remains authoritative for additional calls in those
methods.

**Consequences:** Existing SQLite snapshots remain compatible and endpoint
semantics are unchanged. Interface adapters and overloaded methods receive
better candidate selection in buildless analysis, at the cost of retaining
ambiguity when the receiver type or source hierarchy is unavailable. The
fallback remains intentionally weaker than CodeQL and does not resolve runtime
Spring bean selection.

## ADR-27b: Preserve stronger enrichment facts and label topology reconciliation

**Status:** Accepted.

**Context:** Iterative AI graph imports reuse semantic fact identities. A later
pass may know less than an earlier pass and must not replace a high-confidence
assertion with an imprecise one. Separately, a persisted code flow can remain
valid source evidence while one of its endpoint or topology links is absent
from the current architecture snapshot.

**Decision:** Merge repeated `GraphFact` values monotonically by confidence and
status. Equal-quality observations refresh the stored value; lower-quality
observations may fill missing optional fields but cannot replace stronger
semantic fields or provenance. Persist a `CodeFlow.reconciliation` status
derived only from endpoint identifiers and the current topology edges:

`complete` for fully matched evidence and `partial` when code evidence remains
but an endpoint or cross-service edge is missing, unresolved, dynamic, or
ambiguous. Same-service input/output evidence does not require an
inter-service edge. Multiple matching topology edges are ambiguous and remain
`partial`. A canonical deduplicated flow also persists `alternative_count` so
the UI can disclose how many static routes it represents.

**Consequences:** Enrichment imports cannot silently degrade a confirmed fact,
while explicit complete-manifest deletion remains available for stale facts in
that namespace. Flow consumers can distinguish a valid partial code path from
a fully reconciled topology path without discarding either one.

## ADR-28: Render selected call-graph arcs with orthogonal port routes

**Status:** Accepted; the libavoid router was removed by ADR-69, and the
local orthogonal router is the only one.

**Context:** The focused call-graph view needs to distinguish several
service-to-service dependencies that share the same endpoints and must keep
their lines out of the source and target cards.

**Decision:** Render selected service-to-service call-graph edges in the SVG
overlay as orthogonal straight segments between their actual output and input
port anchors. ELK.js keeps node positions fixed while the browser
`@mr_mint/elkjs-libavoid` WASM module routes around card rectangles, using
explicit port sides and nudging for parallel edges. Exclude the source and
target card rectangles with padding. In the focused call-graph view, persisted
endpoint-to-endpoint dependencies are represented by the projected service
edge, avoiding a duplicate raw port path.

**Consequences:** Arcs remain attached to their port badges, avoid the two
endpoint cards, and remain readable when several dependencies share a route.
The routes are presentation-only and are recomputed after camera or layout
changes. If the CDN or WASM runtime is unavailable, the existing local
orthogonal router preserves a usable export.

## ADR-29: Ask CodeQL directly for input-to-output reachability

**Status:** Accepted. Amended by ADR-78 and ADR-84.

**Context:** Reconstructing every interprocedural flow by starting at each
 indexed input method and traversing Python-side call edges can miss a flow
 when dispatch resolution or an intermediate join is incomplete, even though
 CodeQL can answer the input-to-output reachability question directly.

**Decision:** Run a second CodeQL query, scoped to the indexed input
 and output methods by relative path and start line. Traverse from outputs to
 callers until indexed inputs are reached, without a business-level hop or
 global path limit. Persist exact reachability with medium confidence and
 dispatch-expanded reachability with low confidence. Keep a finite graph search
 only to recover one representative intermediate route for each proven pair;
 add a two-endpoint direct flow when no such route is available.

**Consequences:** CodeQL can recover connections that the Python-side join does
 not reconstruct, while the persisted evidence remains tied to indexed source
 methods and retains confidence. Runtime cost grows with the number of indexed
 anchors and the reachable call graph, so the subprocess timeout remains the
 operational guardrail. Reflection, runtime routing, and calls absent from the
 CodeQL database remain explicit blind spots.

## ADR-30: Resolve fallback dispatch by qualified symbols and union partial evidence

**Status:** Accepted. Supersedes the endpoint-only fallback in ADR-27a. Amended by ADR-78.

**Context:** Large repositories contain unrelated homonymous methods and
abstract contracts implemented in other build modules. Name-only bridges can
invent dependencies, while same-module/output-only restrictions lose helper
chains. A nonempty CodeQL reachability result is not proof that AST fallback
coverage is redundant.

**Decision:** Build a transient qualified Java symbol index within the indexed
source perimeter. Require a known receiver/contract, a compatible parameter
signature and unique source implementation through transitive inheritance for
synthetic calls; retain these as low confidence. Include ordinary intermediate
methods, but preserve ambiguity instead of selecting a bean or homonymous
method. Union bounded fallback paths with output-anchored direct CodeQL pairs.
For duplicate pairs, retain the direct proof and only attach a CodeQL witness
of compatible confidence. Cache predecessor trees by source/confidence and
materialize global CodeQL flows once for HTML progress and final persistence.

**Consequences:** Source-backed cross-module inheritance works without changing
SQLite or endpoint contracts. Previously invented name-only flows disappear.
Unsupported generic substitution, varargs and unknown types remain fallback blind
spots. Multiple concrete implementations are retained as separate low-confidence
possible candidates rather than resolved to one runtime target. Symbol/hierarchy
indexing keeps duplicate qualified types separate by module, and per-source
predecessor trees consume memory proportional to their relations;

When the caller module provides compatible implementations, it is preferred;
cross-module candidates remain available only when that local set is empty.
the QL closures remain potentially large but are explicitly output-anchored.
Subprocess deadlines cover progress reads as well as execution and terminate
the POSIX process group. The code-flow signature changes to rebuild old flows.

## ADR-31: Resolve declarative HTTP clients and bounded URL helpers conservatively

**Status:** Accepted.

**Context:** Representative Spring repositories use `@HttpExchange` contracts
and small private helpers to construct service URLs. Treating these as dynamic
loses explicit calls, while evaluating arbitrary Java would invent targets.

**Decision:** Extract method-level HTTP exchange annotations and retain an
explicit `@ClientRegistrationId` as target evidence. Resolve only source-local
String expressions that are literal, uniquely initialized and never reassigned,
or a uniquely named private/static String helper with one unconditional return
and statically resolvable arguments. Reject overload ambiguity, mutation,
conditionals, recursion and foreign receivers. Reuse the existing target
identity matching, so unresolved or multiply matched aliases remain endpoint
facts without topology edges.

**Consequences:** Declarative clients and common service-name helpers become
visible in endpoint and flow analysis without broadening inference to runtime
configuration. The bounded evaluator adds a small AST traversal cost per URL
expression. Reflection, service discovery, mutable hosts and arbitrary helper
logic remain explicit unresolved cases.

## ADR-32: Require topic and payload type for an asserted Kafka service arc

**Status:** Accepted.

**Context:** Sharing a Kafka topic is insufficient proof that two services
exchange the same logical message. A topic may carry several payload types,
and an endpoint may have no reliable type evidence.

**Decision:** Build an asserted producer-to-consumer service arc only when both
endpoints have a concrete identical topic and a known identical Java message
type. Keep topic-level endpoints, topic nodes, and warnings for missing or
mismatched types, but do not project them as an `A → B` relation. Potential
Kafka flow continuations use the same pairwise condition.

**Consequences:** The graph contains fewer but stronger Kafka arcs. Topic-level
evidence remains visible for investigation, while unknown, dynamic, and
contradictory payloads remain explicitly unresolved.

## ADR-33: Keep topics in selected call-graph views

**Status:** Accepted.

**Context:** A selected code flow previously projected only microservices and
service-level endpoint dependencies. That hid the topic that explains a Kafka
handoff and made a valid producer-to-consumer arc difficult to interpret.

**Decision:** When a selected message flow has one concrete producer for its
entry topic, include the producer and consumer in the focused path and retain
the topic only as endpoint and tooltip evidence. Keep the corresponding direct
persisted service edge and arrange the selected microservices in a compact
horizontal lane. Do not add a producer when the topic has multiple ambiguous
producers.

**Consequences:** Call-graph screenshots and exports show only microservice
nodes while retaining the asserted direct service arc and its topic evidence in
badges and tooltips. Ambiguous topic ownership remains unresolved and is not
turned into a guessed path.

## ADR-34: Commit a partial snapshot after a CodeQL timeout

**Status:** Accepted.

**Context:** CodeQL is an optional, potentially long-running enrichment stage
of indexing. Treating its timeout as a fatal index error rolls back facts that
were already extracted by the AST and prevents users from inspecting the
available architecture in the HTML graph.

**Decision:** Treat `analysis.codeql_timeout_seconds` expiration as a soft
boundary for the complete CodeQL pass. Propagate the remaining wall-clock
budget to source generation, database creation, queries and decoders. Terminate
and reap the complete timed-out CodeQL process group, retain AST facts and any
CodeQL calls recovered from a completed partial result, run relation and flow
post-processing, reconcile the resulting facts, and commit the partial
snapshot. Do not mark the code-flow signature current after a timeout, so the
next index retries CodeQL. Other CodeQL errors remain fatal and preserve the
previous committed snapshot through the transaction boundary.

**Consequences:** A completed index may expose a graph with reduced
interprocedural coverage, and the CLI explicitly reports that the graph is
partial. HTML export remains available and deterministic from the committed
snapshot. Repeated indexes may retry CodeQL until it completes successfully;
the timeout does not weaken extraction confidence or invent missing calls.

## ADR-35: Use shared Kafka topics before payload type resolution

**Status:** Accepted.

**Context:** A Kafka producer and consumer can share a concrete topic even when
the indexed source does not expose a usable Java payload type. Requiring both
types to match hides an evidenced service connection and removes the services
from the corresponding call graph.

**Decision:** Create a producer-to-consumer service relation when two distinct
microservices use the same concrete Kafka topic. Missing payload types remain
compatible and lower the relation and flow confidence to medium. Two known and
different payload types remain incompatible, because the indexed evidence
indicates a possible contract conflict rather than a proven exchange.
Apply the same compatibility rule to persisted Kafka flow continuations.
Dynamic topics remain unresolved and never create a pairing.

**Consequences:** Interaction graphs include more topic-backed service connections
without inventing a payload type. HTML and catalog views expose the type status
as unknown or partial, while contract mismatch warnings remain visible. Topic
fan-out can include consumers with missing type evidence, so analysts must use
the confidence and type status before treating the relation as a confirmed
payload contract.

## ADR-36: Run Maven source generation offline

**Status:** Accepted.

**Context:** CodeQL can request Maven source generation before building its
source-only database. Allowing Maven to resolve plugins or dependencies from a
registry violates the local and reproducible indexing boundary.

**Decision:** Invoke Maven source generation with batch, non-interactive and
offline options. Maven may use only the local repository cache. A missing
plugin or dependency is an explicit source-generation failure; SystemLens does
not retry by contacting a remote registry. Gradle source generation keeps its
existing local wrapper invocation.

**Consequences:** CodeQL indexing remains network-independent when source
generation is enabled. A cold local Maven cache can prevent generated sources
from being produced, so the index reports the failure instead of silently
falling back to a network access or an incomplete generated-source result.

## ADR-37: Publish CodeQL flow checkpoints during indexing

**Status:** Accepted.

**Context:** A complete CodeQL pass can take long enough that users need to
inspect the flows already resolved while indexing continues. Keeping every
mutation inside one transaction hides those usable intermediate results.

**Decision:** After each completed CodeQL analysis project, commit the current
architecture facts and provisional `code_flows` as an explicit partial
checkpoint. Mark the checkpoint with
`code_flow_snapshot_status=partial`, keep the code-flow signature unset, and
emit a progress line with the checkpoint count and provisional flow count. The
final reconciliation replaces the provisional flows and marks the snapshot
`complete`.

**Consequences:** Read-only clients can inspect progressively enriched flows
while CodeQL continues, but a checkpoint is not a complete architecture
snapshot. A failed run after a checkpoint leaves the last explicitly partial
snapshot available for inspection; the missing code-flow signature causes the
next index to retry the interprocedural stage.

## ADR-38: Persist the source-backed CodeQL call graph

**Status:** Accepted.

**Context:** `CodeFlow` records describe only projections from indexed inputs to
indexed outputs. They do not preserve isolated methods, internal call edges, or
the complete set of source-backed methods needed to inspect the CodeQL graph.

**Decision:** Persist every indexed Java method through `integration_methods` and
persist every normalized CodeQL caller-to-callee edge in `codeql_call_edges`.
Store the relative call-site path, line, dispatch confidence, and whether the
edge came from a source-declared fallback. Compute weak connected components
from all method IDs, including isolated methods. Keep `CodeFlow` as a bounded
IN-to-OUT projection over this graph. For CodeFlow materialization, this
supersedes the Kafka continuation decisions recorded in ADR-27, ADR-32 and
ADR-35.

**Consequences:** Read adapters can inspect the call graph after indexing, while
existing flow and topology contracts remain compatible. A schema migration adds
the edge table. Ambiguous overload resolution remains unresolved instead of
selecting an arbitrary method.

## ADR-39: Fuse matching Kafka flow fragments in the HTML projection

**Status:** Accepted.

**Context:** Indexing can produce one persisted fragment for a scheduled or
explicit Kafka publication and another for the consumer that starts at the
same topic. Showing both as independent Flux cards duplicates one rooted
interaction, especially when the consumer continues to another topic.

**Decision:** During HTML serialization, group a publication fragment and a
consumer fragment only when their rendered root, concrete topic, and known
message type agree. Build one rooted union of their already-proven service
branches and retain one deterministic representative. Do not enumerate
producer/consumer route combinations, and do not alter persisted `CodeFlow`
records or architecture facts.

**Consequences:** The HTML flow picker represents one root interaction for a
matching Kafka boundary while preserving fan-out branches. A missing message
type does not fuse with a known type, and fan-in remains conservative because
the consumer fragment must already have one rendered root. `equivalent_count`
reports how many persisted fragments the visible card represents.

## ADR-40: Preserve complete service arcs in selected flow graphs

**Status:** Accepted.

**Context:** A rendered flow graph that aggregates every arc touching an
endpoint can include unrelated topology and cannot establish where traversal
starts. The persisted flow and topology snapshots contain enough evidence to
follow internal outputs from source modules while retaining branches and
cycles.

**Decision:** Build directed candidate arcs from flow-associated `call` or
`produce` OUT ports to `serve` or `consume` IN ports. Treat trigger flows with
no incoming flow arc as roots. After adding an arc, continue with the flow
whose trigger is the target IN port and visit flows in breadth-first order.
Preserve fan-in, fan-out, and cycles, and deduplicate by expanded OUT port
plus protocol, topic, and endpoint pair. Record only external trigger steps
under their owning microservice.
Preserve endpoint identities on each rendered service arc.

**Consequences:** The graph represents reachable source-backed service
interaction evidence rather than an arbitrary aggregation or presentation
tree. A cycle can prevent a topological order, so the exporter uses a
deterministic node order for cyclic graphs. If no trigger flow has zero
indegree, all trigger flows are used as deterministic roots while preserving
arc direction; missing topology arcs do not create guessed service relations.

## ADR-41: Keep repository conventions outside the domain graph

**Status:** Accepted.

**Context:** The domain graph previously imported Strategy1 REST conventions
directly. This made the domain depend on a repository-specific adapter and
made the integration vocabulary ambiguous by using `topic` for both Kafka
topics and HTTP routes.

**Decision:** The domain graph accepts an injected `RestTargetPolicy`. Strategy1
constructs that policy in `conventions.strategy1.graph` and application or
indexing services pass it at the boundary. `MessageEndpoint` retains its
storage-compatible `topic` field, but exposes `kafka_topic` and `route` typed by
the integration system. Application endpoint summaries identify the resource
with `resource_kind` and expose the corresponding explicit field.

**Consequences:** The domain no longer imports Strategy1 and the architecture
boundary test rejects convention imports from the domain. Existing persisted
indexes and the legacy `topic` field remain compatible. New code must not use
`topic` as the vocabulary for an HTTP route, and callers must inject a policy
when they need repository-specific target resolution.

## ADR-42: Inventory JPA entities without inferring a database

**Status:** Accepted.

**Context:** A service can declare JPA entities while the existing data inventory
lists only MongoDB collections. An empty database summary then hides source
evidence of relational persistence, but `@Entity` alone does not establish a
physical table or a selected database engine.

**Decision:** Persist each imported or qualified JPA `@Entity` declaration with its qualified
class name and root-relative source location. Add a module-to-entity `maps`
relation and expose the declarations in the service summary. Do not derive
tables, JDBC engines, or read/write operations from this annotation.

**Consequences:** Existing indexes gain an additive module column and continue
to load with an empty JPA inventory until reindexed. Users can see declared
entities in the CLI. Runtime database identity and repository operations remain
outside this static fact.

## ADR-43: Preserve receiver context in inherited dispatch

**Status:** Accepted.

**Context:** Source-only Java resolution can see a base method, several
overrides, and a concrete entry subtype at the same time. Selecting one
override from the method name alone creates cross-subtype flows, while dropping
the base body loses valid calls from a base-typed receiver.

**Decision:** Keep the declared base body and known overrides as possible targets
when the runtime receiver is unknown. When a flow starts in a concrete subtype,
restrict inherited-body dispatch to that subtype and its ancestors. Resolve
`super.method()` against the direct parent declaration. Preserve known generic
signature positions when expanding partially resolved contracts. Keep all such
fallback edges at possible dispatch and low flow confidence.

**Consequences:** Sibling implementations no longer enter one concrete flow,
while base-typed fields retain explicit alternatives. Runtime bean selection,
generic substitution that cannot be proven, and reflection remain unresolved.

## ADR-44: The resource catalogue is a snapshot projection

**Status:** Accepted.

**Context:** The HTML export needs one browsing surface for resources that are
represented by graph nodes and resources that remain attached to service
metadata, such as HTTP routes and contracts.

**Decision:** Build the resource catalogue from the persisted architecture
snapshot during export. Give each entry a stable identity, evidence reference,
kind and association metadata. Keep the catalogue filters and docked inspector
in the browser layer. Do not re-parse source files, infer owners or create
graph nodes for resources that have no persisted graph node.

**Consequences:** The catalogue remains deterministic and offline, but it can
only show resources present in the snapshot. Extractor changes are required
when a missing resource is a data-quality problem. Existing graph and modal
inspection paths remain available outside the catalogue workspace.

## ADR-45: Diagnose asynchronous writes and synchronous reads as pair evidence

**Status:** Accepted.

**Context:** A selected flow can publish an update asynchronously while a
causally related continuation reads the affected data synchronously. Linear
flow order cannot prove that the consumer write has become visible before the
read, and a single flow can contain several independent read/write pairs.

**Decision:** Keep optional branch-aware evidence on `CodeFlowStep` and derive
one deterministic diagnostic per distinct asynchronous-write/synchronous-read
pair. Require a shared causal identity or a persisted common trigger plus
resource and key/query overlap. Recognize a completion guarantee only when it
applies to the expected operation/version; acknowledgements and fixed delays
remain potential risks. Unknown evidence is classified as insufficient rather
than safe. Export the diagnostics from the persisted snapshot and render them
only in Flux.

**Consequences:** Existing indexes and flow consumers remain compatible because
the metadata is additive and nullable. The detector is conservative and does
not infer missing data-access or causal facts; extractor work is still required
to populate those fields for real applications. Multiple diagnostics remain
independently inspectable without changing topology or runtime claims.

## ADR-46: Classify MongoDB API attempts separately from execution paths

**Status:** Accepted.

**Context:** The MongoDB inventory retains raw API names, while flow and
architecture projections used separate read/write lists. Those lists treated
deletion as reading and an unexecuted `bulkOps` builder as writing.

**Decision:** Use one MongoDB API classifier for read, insert, update, delete,
create-or-update, and read-and-update attempts. Derive broad read/write facets
for existing relations. Keep the specific action optional on persisted flow
steps so older indexes load unchanged. A builder or collection accessor does
not create a read/write effect. Source receiver typing gates extraction; an
API name alone does not identify MongoDB access.

**Consequences:** Same-method MongoDB steps can distinguish insertion from
deletion while retaining existing broad flow kinds. `read_update` has both
relation facets but currently uses the broad `data_write` step kind. Old
snapshots have no action until reindexed. The classifier does not prove that
the call ran or that a database transaction committed. Reaching operations
through helper methods still requires the separate persisted-fact join.

## ADR-47: End MongoDB data flows at source-backed operation sites

**Status:** Accepted.

**Context:** HTTP and Kafka entries can reach MongoDB calls through application
services and persistence adapters. Endpoint-to-endpoint flow reconstruction
does not include operations on a sibling branch of the call graph.

**Decision:** Join each MongoDB inventory call to a unique persisted method by
module, relative path, owner name, and source line. Traverse directed call
edges from each indexed entry and emit a separate potential path ending at the
operation. Retain call-site evidence and lower confidence for possible dispatch.
Keep data paths separate from paths ending at an external endpoint.

**Consequences:** Existing indexes can be recalculated without rereading Java
sources. Calls with ambiguous owners or missing call edges remain absent.
The method graph does not prove branch execution, operation success, or exact
create-versus-update behavior for `save`. Data paths add traversal and export
work proportional to reachable operation sites within the configured hop bound.

## ADR-48: Resolve JPA accesses and project sibling data paths onto call roots

**Status:** Accepted.

**Context:** The call view can show a MongoDB terminal path while a selected
HTTP-to-Kafka path omits data accesses reachable from the same entry. JPA entity
declarations alone do not identify repository read or write calls.

**Decision:** Persist supported Spring Data JPA calls only when a typed
repository and one indexed entity resolve from source. Classify calls as read,
delete, or create-or-update attempts. Emit a separate potential path from each
reachable HTTP or Kafka entry to the call site. At export, show the union of
data sites reachable through persisted endpoint arcs or an identical trigger
endpoint on the selected call root. Keep the paths separate and use the
existing pairwise consistency detector only with its required evidence.

**Consequences:** Call inspection can reveal JPA and MongoDB accesses without
implying that every branch ran. Older SQLite indexes acquire an empty JPA
operation column through an additive migration and need reindexing for new
facts. Unsupported repository calls and ambiguous types remain absent.
Entity identity does not establish a physical table or record key. Static
reachability alone cannot prove that an asynchronous write became visible
before a synchronous read.

## ADR-49: Preserve a unique final generic delegation as a possible call

**Status:** Accepted.

**Context:** A Kafka listener can delegate to a `final` method inherited from
a generic base class. The source symbol join previously required every formal
parameter type to resolve, so this source-backed call was absent even when the
body could not be overridden.

**Decision:** When the declared receiver has one compatible inherited `final`
method with a source body, retain that method as a possible call target despite
an unresolved generic parameter. Keep all ordinary overload, receiver, and
source-location checks. Persist the edge with inferred, possible confidence.

**Consequences:** Kafka-to-persistence paths through this delegation can be
reconstructed without inventing a runtime target. The method body can still
contain conditional effects, so downstream data paths remain potential. A
non-final or ambiguous generic method remains unresolved.

## ADR-50: Present data access as call-flow annotations

**Status:** Accepted.

**Context:** Persisting a terminal path for every reachable database call
increased the apparent number of flows, although those paths are evidence
about a service rather than additional service-to-service calls.

**Decision:** Keep source-backed data paths in the compatible SQLite snapshot
for reachability and consistency analysis. CLI flow listing and HTML call-flow
navigation expose only integration-ending paths and cycles. HTML export
projects data operations onto the owning microservice and onto reachable call
roots. A call-tree occurrence shows only operations whose trigger endpoint
matches its input endpoint. Tooltips label operations as potential.

**Consequences:** Existing indexes remain readable and no source parse occurs
during export. A data-only trigger has no selectable call-flow card; its
service-level tooltip still shows indexed operations. Reachability remains
conservative and branch execution is unproven. The SQLite snapshot still
contains the internal terminal paths, so storage size is unchanged.

## ADR-51: Scope data-access annotations to selected call graphs

**Status:** Accepted.

**Context:** Service-wide data access lists in the architecture graph lack a
selected call path and can be mistaken for operations performed by every
entry. The left widget also competes with the call graph for attention.

**Decision:** Show data-access annotations only in microservice tooltips of a
selected call graph. Match a call-tree occurrence to access evidence through
its exact input endpoint. Keep the architecture graph and left flow widget
free of data-access annotations and consistency cards. Attach each
diagnostic's source site to the exported pair, then put a selectable badge
on matching service occurrences in the call graph. This supersedes ADR-50's service-wide
architecture tooltip projection; its SQLite evidence policy still applies.

**Consequences:** The HTML payload omits the redundant service-wide access
projection. Accesses without a reachable integration call flow remain stored
but cannot be displayed in a call graph. A selected call graph shows only
accesses supported by its persisted trigger and topology.

## ADR-52: Classify async writes by message consumption

**Status:** Accepted.

**Context:** Marking every database operation in a Kafka consumer as async
made reads appear to have asynchronous consistency semantics. MongoDB access
facts also lacked branch metadata, so consumer writes could not participate in
the same diagnostic as JPA writes.

**Decision:** Classify a database write as async when it belongs to a Kafka
consumer handler. Classify a database read as sync when it belongs to an HTTP
entry path. Leave reads in message consumers and operations under other trigger
types unclassified. Apply this rule to JPA and MongoDB operations.

**Consequences:** Consistency diagnostics compare writes performed after
message consumption with reads on synchronous HTTP paths. The static model
does not prove that the write was caused by a particular publication or that
the consumer completed successfully; persisted reachability remains required
to establish the candidate path.

## ADR-53: List only uncovered call roots in HTML

**Status:** Accepted.

**Context:** Listing every persisted integration flow separately repeats a
downstream Kafka or HTTP segment that already appears in a larger call tree.
The repeated cards make a source-backed path look like several independent
user journeys.

**Decision:** In HTML exports, select call flows with no incoming persisted
flow arc first. Traverse their call trees, then select one stable flow for each
uncovered component or cycle. Keep reached flows as supporting records for
browser traversal, source evidence, and per-service annotations. Keep the CLI
flow list and SQLite snapshot complete.

**Consequences:** The HTML picker shows fewer root journeys while preserving
fan-in, cycles, and nested call occurrences. Access and consistency projections
run only for selectable roots. A flow with no proven upstream arc remains
selectable; partial topology cannot hide it by inference.

## ADR-54: Attach imported data access to indexed call flows

**Status:** Accepted.

**Context:** Source indexing can miss a data operation that a reviewer can
locate, while direct flow import cannot run on an indexed source snapshot.
Replacing an indexed flow with an agent-produced path would weaken its source
evidence and disturb stable call selection.

**Decision:** Import a `flow_data_access` node fact in the existing namespace
layer. Require a known flow ID, a CRUD action, confidence, and relative source
evidence. Join the fact to its selected call graph at export time. Keep it out
of topology nodes and consistency diagnostics.

**Consequences:** Reviewers can annotate indexed calls without changing the
source snapshot or SQLite schema. The annotation persists across reindexing.
A changed flow ID requires reconciliation before the annotation reappears.
The import and HTML projection add work proportional to imported facts and the
reachable flows of each selected root.

## ADR-55: Give browser navigation and initial rendering explicit owners

**Status:** Accepted.

**Context:** The standalone HTML export concatenates its browser files into one
script. A graph rebuild ran while later widgets were still loading, and the
bootstrap rebuilt it again. The details widget also owned toolbar navigation,
while some catalogue inputs registered the same render handler in two files.

**Decision:** Keep the single-script export. Put tab and panel transitions in
`07-navigation.js`, and keep flow-selection transitions in
`06-view-lifecycle.js`. Register widget filters in their widget files. Start
the first graph projection from bootstrap after all browser files initialize,
then apply the initial layout after sizing the workspace.

**Consequences:** The first projection reads initialized widget state, and
one catalogue filter event invokes one renderer. Browser files still share
lexical state and depend on the assembler's declared load order. Moving to
independent JavaScript modules remains a separate change.

## ADR-56: Show CRUD evidence and association uncertainty on call occurrences

**Status:** Accepted.

**Context:** A handler can reach both an external call and a database access
through separate internal paths. Their shared entry point does not prove that
both effects occur on every execution. A `save` call can create or update, and
resource names imported without a qualified ID can remain ambiguous.

**Decision:** Show one CRUD badge per resource ID on each call-graph service
occurrence. Keep sibling accesses reachable through the same handler, but mark
the badge with `?` when the flow ID or first source-backed method call does not
support a more specific association. Display `create_or_update` as `C/U` rather
than two certain operations. Resolve an imported simple name to a source ID
only when one candidate exists in the owning module. This supersedes ADR-51's
tooltip placement while retaining its occurrence-level scope.

**Consequences:** The selected graph retains possible accesses without
claiming a proved branch execution. Distinct unresolved resources remain
separate, with qualified duplicate names in the badge. The HTML snapshot
stores one optional first-call witness per access flow; the SQLite schema and
persisted facts do not change.

## ADR-57: Compare complete method-call paths for data-access badges

**Status:** Accepted.

**Context:** Two access paths can share the first method call and then diverge.
ADR-56's first-call witness marks both routes as supported, even when only one
route reaches the data operation.

**Decision:** Export each access flow's ordered method-call path. A call-tree
occurrence supports the association when its flow ID matches the access flow,
or when the access path is a non-empty prefix of an occurrence flow's path.
Otherwise, the badge keeps the handler-level `?` marker. This supersedes
ADR-56's first-call witness rule.

**Consequences:** Divergent routes retain visible potential accesses without
claiming a route match. The HTML snapshot is larger for access flows with
several calls; the first-call field remains for older consumers. SQLite facts
and schema remain unchanged. Identical call paths still cannot prove which
conditional branch ran.

## ADR-58: Stop internal call paths at application boundaries

**Status:** Accepted.

**Context:** The global CodeQL method graph can connect an entry in one
application module to a method in another application module. Assigning a
reached data operation to the entry module then displays the other service's
database access as its own. Cross-module helper calls remain useful.

**Decision:** During flow reconstruction, stop a path before a different
module that starts its own application. Keep calls into modules without an
application entry point. Apply the boundary to directed traversal and direct
CodeQL input-to-output pairs. This narrows ADR-30's cross-module fallback
while preserving its helper-module resolution.

**Consequences:** Internal flows no longer claim data or output effects from
another application through a Java method path. Interservice HTTP and Kafka
edges still connect separate internal flows. Existing indexes rebuild flows
through a code-flow signature change; SQLite schema and source facts remain
compatible. An application module deliberately embedded as a library loses
these internal paths. Module metadata must be present to classify the boundary.

## ADR-59: Pair async writes and sync reads along call-graph branches

**Status:** Accepted. Amends ADR-52.

**Context:** ADR-52 labels writes and reads by their own flow trigger. With
source-indexed flows, a cross-flow pair also needed causal IDs and an explicit
`request_before_reads` relation that only manifests supply. On real indexes the
detector therefore reported nothing, even for a handler that publishes a Kafka
event and then reads the record its consumer writes.

**Decision:** Within one selected call graph, the indexed path from the root is
the causal evidence. Find the handler where the paths to the write and the read
diverge. The write branch must cross a Kafka hop; the read branch must use only
REST hops or none. Require one canonical resource identity, without distinct
proven key identities.

Compare the two branch departures in the fork handler. They are direct sites of
the same method, so their source lines give a program-order hint. Report a
possible risk when the publication precedes the read. Report nothing when the
read precedes it, and insufficient evidence when no comparison is possible.

**Consequences:** Source-only indexes can show stale-read candidates on the
call graph that causes them. The order hint does not exclude conditional or
mutually exclusive branches, loops, or callbacks, so the finding stays a
potential risk with that limitation. An unproven record key lowers confidence
and severity instead of hiding the pair. Steps with explicit causal or order
facts keep the manifest rules. Exports without endpoint systems keep the
previous behavior; the SQLite schema is unchanged.

## ADR-60: Compare static topology with aggregated Elastic APM metrics

**Status:** Accepted.

**Context:** Static indexing cannot show which integrations run, nor which
runtime integrations the source index misses. At 50 services and hundreds of
topics, raw APM traces are too large to download. Elastic APM Server already
stores pre-aggregated service-destination and transaction metrics.

**Decision:** An opt-in command queries only those metric data streams through
paginated composite aggregations computed by Elasticsearch. It never downloads
trace documents. The result is persisted as runtime observations, separate
from source facts and enrichment facts, with its window and origin.

Reconciliation runs on persisted data. It maps an APM service name to an
indexed service by explicit mapping, exact name, then a unique token-bounded
inclusion; anything else stays unresolved. Observations become `matched`,
`observed_only`, `external`, or `not_compared`; indexed integrations of an
observed service without traffic become `static_only`.

**Consequences:** Analysts see unindexed runtime integrations and unexercised
static ones without a trace export. Transfer size depends on service and
destination cardinality, not on traffic. Counts depend on the APM version and
sampling, so missing traffic never proves absence. The command contacts a
network service; credentials come from an environment variable and are never
persisted. The new table is additive and does not change `SCHEMA_VERSION`.

**Field validation:** Against the lab's Elastic 9.5 cluster, only the `1m`
rollup exists, and request transactions named only `POST` appear: they are
Micrometer's OTLP metric exports, recorded as root HTTP client spans, which
aggregated metrics cannot tell apart from unrouted requests. Collection
therefore detects the available interval, and an indexed integration is
`static_only` only when the service's telemetry proves it would name that
traffic; otherwise it is `unverified`. REST orders sent to the lab were then
matched end to end: route, service-pair destination, and downstream route.

**Export decision:** The microservices HTML export embeds the reconciled
observations as a separate overlay, offered only when the index holds
observations and on at load: an export only carries it when the user
collected traffic, and an overlay hidden behind a toggle went unnoticed in
the lab review. It annotates existing arcs and service cards and
draws no edge for `observed_only` traffic, which a gaps panel lists instead.
The service map is persisted as `runtime.service_map` in the project config,
so `observe report` and the export resolve services identically.

**Export consequences:** Exports read observations from the index and never
contact Elasticsearch. Without observations, the embedded snapshot is
unchanged. A REST count measures a service pair, not a call site. The
selected call tree therefore labels each card with the count of its own
entry point, and shows an arc's emitted and received counts separately,
never added together. Service cards state entry-point coverage, not raw
volume: in the lab, health probes made up most inbound transactions.

**Error groups:** Collection also aggregates `logs-apm.error-*` per service
and grouping key, keeping exception type, culprit, transaction name, count,
and last occurrence. Messages are never read or persisted, because they can
carry business data or secrets. Errors attach to the indexed entry point of
their transaction and never change a reconciliation status. Errors caught by
the application leave no error document and show only as failures.

## ADR-61: Make the call-graph engine a pluggable choice

**Status:** Accepted. Amends ADR-29 and ADR-38.

**Context:** Interprocedural flows came from CodeQL, with a tree-sitter
symbol pass filling missing edges. The indexing core imported the CodeQL
module directly. CodeQL needs its CLI, a query pack, minutes of analysis,
and a license that may not cover proprietary code. Without it, the symbol
pass already built flows, but only as an implicit fallback named `none`.

**Decision:** The core depends on an engine-neutral seam, `callgraph/`: call
types, a `CallGraphEngine` protocol whose session reports calls, methods,
reachability, and Kafka payload types, and a registry that loads engines by
name. `codeql` lives in `engines/codeql/` and is imported lazily. `source`
is a first-class engine: it reports no call itself, and the shared
tree-sitter symbol pass becomes the whole call graph. `none` builds no
interprocedural path. An unavailable engine is announced, then replaced by
`source`. As the whole call graph, the source pass runs in a closed world
like CodeQL's `exactVirtualMethod`: a call is `exact` when one concrete
source body can receive it, including the unique implementation of an
interface. When it only fills CodeQL's gaps, interface dispatch to a subtype
stays `possible`. `analyze compare-engines` measures two engines on one index.
The source engine also types `var` initializers, static calls on a type name,
method references with a single declared arity, field accesses, and
same-arity overloads from literal or typed arguments, including arguments
assignable to a source ancestor; an ancestor's concrete body is `exact`.
An override whose parameter type comes from an external wildcard import
matches the single ancestor contract with the same written simple names,
following Java's erased-signature override rule. A call to an abstract
source declaration that no source type implements, such as a Spring Data
query method, reaches that declaration as a `possible` target.

**Consequences:** Users can choose the engine per project or per run, and
SystemLens works fully without CodeQL. Configurations with `codeql: false`
keep their previous behavior through `source`. Persisted identifiers keep
their `codeql` names (tables, meta keys, the export's `codeql_call_graph`
key, `codeql_*` settings) so existing indexes and exports stay compatible.
Flow reasons and progress messages no longer name CodeQL. A source flow
whose calls are all exact now reaches `medium` confidence instead of `low`.
On the observability lab, the source engine finds every flow CodeQL-free, but
reports each data-access site in its own flow: grouped data accesses and the
ADR-59 async-write/sync-read diagnostic still need `codeql`. The companion
check validates whichever engine is installed.

## ADR-62: Attribute anonymous class bodies to the creating method

**Status:** Accepted. Amends ADR-61.

**Context:** The method inventory named every `method_declaration` after its
nearest named type. A method of `new Runnable() { public void run() {} }`
inside `Outer.start()` became `Outer.run`. A real `Outer.run` then had two
candidate bodies, so `o.run()` lost its `exact` dispatch. The anonymous body
was also disconnected from `start()`, which hid routes through callbacks.

**Decision:** An anonymous class method is not indexed. Its body belongs to
the named method that creates the instance, as a lambda body already does.
Endpoints, data accesses, and calls found in it are attributed to that
method. Inside the body, a call on `this`, `super`, `this.field`, or without
a receiver denotes the anonymous instance, whose supertype is often
external; such a call stays unresolved. CodeQL already reports these callers
as `<anonymous class>.name`, and the join attaches them to the smallest
enclosing indexed method, which is now the same creating method.

**Consequences:** Both engines agree on the caller of a callback body, and
named methods keep exact dispatch. A route through the anonymous instance's
own members is not reconstructed. An anonymous class created in a field
initializer has no creating method, so its calls are not indexed. Persisted
method identities of former anonymous rows disappear; the code-flow
signature forces existing indexes to rebuild.

## ADR-63: Reserve widget footprints and set a 10-pixel type floor

**Status:** Accepted.

**Context:** A widget review found an open legend covering graph cards, badge
text at 8 pixels, and a call-tree context widget whose content was never
displayed. The legend overlaid the graph because the workspace ignored it.
`Déplier tout le graphe` kept a camera that no longer framed the larger tree.

**Decision:** Widget text never goes below 10 pixels and uses the `--type-*`
tokens. An open legend reserves its footprint through the workspace variables,
as the left panel already does: a right column when 560 pixels of graph width
remain, a bottom band otherwise, nothing on compact viewports. A global tree
expansion refits like `Tout voir`; a branch expansion keeps the camera.

**Consequences:** Opening the legend refits the graph once and shrinks its
workspace. Fixed-size cards and badges take slightly more room, so long
resource names wrap earlier inside their 110×70 envelope. The expand-all
action no longer preserves the viewport, and its browser test asserts that
every occurrence is inside the workspace instead.

## ADR-64: Persist typed data operations and gate the review aids

**Status:** Accepted.

**Context:** Data operations lived inside the module inventory JSON and were
re-derived per flow, so JPA coverage stopped at `JpaRepository` CRUD calls
and nothing recorded transaction scope or conflict controls. The
lost-update and dual-write review aids of the data-operations plan need
those facts, but their precision is unmeasured.

**Decision:** Persist one `data_operations` row per supported invocation,
keyed by module, relative path, owner method, line, technology, and raw
operation, with action, facets, resource identity, key, confidence, reason,
transaction scope, guard, and value dependencies. Flow steps reference the
row by `operation_id`; an unknown action yields a `data_access` step. An
older index projects its module JSON until reindexed. Extraction resolves
receivers by declared type, accepts the Spring Data base interfaces through
module-local inheritance, and classifies derived queries, literal `@Query`,
`EntityManager`, and literal single-statement JDBC; everything else stays
unknown with a reason. Guards are recorded on explicit evidence only;
`@Transactional` is scope, never a guard. The two review aids run only
behind `export microservices --review-aids` until a precision, abstention,
and reviewer-time record on the lab and one more Spring application exists.

**Consequences:** Schema version 39 and a new flow signature trigger one
rebuild. Coverage output makes a JDBC-heavy module with dynamic SQL visibly
partial. The aids on the lab produce one read-modify-write abstention and
four dual-write pairs for one publication; the record in the backlog keeps
the gate closed, and promoting the aids is a separate decision.

## ADR-65: Abstract runtime sources behind one seam with percentiles and history

**Status:** Accepted. Extends ADR-60.

**Context:** ADR-60 compared the index with Elastic APM call sums only. A
product review asked for latency percentiles, a ranked hotspot list, an
explicit statement of completeness, and a second collection source based on
OpenTelemetry metrics, without coupling reconciliation to one backend.

**Decision:** Every backend implements a `RuntimeSource` seam that returns
observations, error groups, and one collection record. The Elastic
collector folds transactions per service, type, and name, with a failure
filter and backend percentiles of `transaction.duration.histogram`; a
Prometheus collector maps the OpenTelemetry HTTP and messaging conventions
to the same rows through instant queries only. Percentiles are persisted as
nullable columns and stated as backend approximations; a destination keeps
an average. Each collection is persisted with its interval, page counts,
overflow services, truncation flag, and notes; the last three per source
are kept, and a comparison needs the same source and interval. Hotspots are
a ranking of reconciled rows, listed apart when they cannot be attributed
to an indexed entry point.

**Consequences:** A new backend changes no reconciliation or overlay code.
Observation IDs now include the collection, so rows of several windows
coexist; rows written before history are reported until the next
collection and prune then. The schema stays additive: new columns are
nullable and the collection table is created on open. A missing
percentile is reported as absent, never as zero, and an overflowed service
is marked incomplete rather than dropped. The overlay draws no edge for a
hotspot; a row selects through the existing route path. The lab's Elastic
cluster and a Prometheus server were not reachable during delivery, so
both collectors are validated against fakes until a lab run is recorded.

## ADR-66: Make the source engine the default

**Status:** Accepted. Amends ADR-61.

**Context:** ADR-61 made the call-graph engine pluggable but kept `codeql`
as the default of `systemlens init` and of a configuration without an
engine. CodeQL needs its CLI, a query pack and a licence that may not cover
proprietary code; a first index therefore depended on an external tool, and
the fallback to `source` happened silently at run time.

**Decision:** The tree-sitter `source` engine is the default engine. `Config`
defaults to `call_graph_engine: source` and `codeql: false`; `systemlens
init` writes those values. CodeQL is opt-in through
`analysis.call_graph_engine: codeql` or `--call-graph-engine codeql`. A
configuration written before this decision with an explicit `codeql: true`
and no engine key keeps selecting CodeQL.

**Consequences:** A first index runs without any external tool and with the
closed-world dispatch rules of ADR-61. Existing configurations are unchanged:
`init` wrote both keys explicitly. The companion skill and the laboratory
check still pass `--call-graph-engine codeql` explicitly where CodeQL is
installed. Persisted `codeql_*` names and settings are unchanged.

## ADR-67: Split CodeQL and the call-graph stage into dedicated modules

**Status:** Accepted. Completes ADR-61.

**Context:** After ADR-61 and ADR-66, CodeQL was an optional engine, but its
code still lived in one 1,200-line module mixing QL text, CLI invocation,
database lifecycle, row parsing and the engine adapter. The index service
held the whole interprocedural stage inline, so a change to the join or to
flow materialization meant editing the file that also owns the file
inventory and the transaction. Two CodeQL-specific delivery helpers (the
provisional progress export and the direct database query) sat among the
Typer commands.

**Decision:** CodeQL becomes the `engines/codeql/` package: `queries.py`
(QL text), `database.py` (CLI lookup, database lifecycle, one shared query
runner), `extraction.py` (rows to call types), `engine.py` (the protocol
adapter) and `errors.py`. The package facade re-exports the previous public
names, and the registry entry `systemlens.engines.codeql` is unchanged.
Submodules import each other by their full module path, never through the
facade, so the import graph stays acyclic.

The interprocedural stage of an index moves to
`indexing/call_graph_stage.py`, which exposes `run_call_graph_stage` and the
progress contract. `indexing/service.py` keeps the file inventory, the
extraction pass and the transaction, and re-exports the progress types that
delivery adapters already import. The two delivery helpers move to
`delivery/call_graph.py`, which reaches the engine through the `callgraph`
registry only.

**Consequences:** Persisted names (`codeql_call_edges`, `codeql_methods`,
`codeql_*` meta keys) and CLI flags are unchanged, so existing indexes and
scripts keep working. Tests patch the submodule that owns a behavior
(`engines.codeql.database`, `engines.codeql.extraction`,
`indexing.call_graph_stage`, `delivery.call_graph`) instead of one flat
module. The core-never-imports-CodeQL check of ADR-61 still holds, and the
acyclic import test now also guards the package facade.

## ADR-68: Dock the node inspector beside the graph

**Status:** Accepted on 2026-11-01 with backlog task U3. Amends PDR-2.
Refined by PDR-9 for groups a node kind does not define. Amended on
2026-11-01 by PDR-10: DTO and persistence-class (Mongo class, JPA entity
field) views dock like a node; only contract views keep the modal.

**Context:** The inspector opened every view as a centered modal over a
blurred graph. Inspecting a node therefore hid the node, its neighbours and
the relations the inspector was describing. Its header stacked one badge per
evidence level, and every relation section stayed expanded. A route served
by a Java controller and declared in an OpenAPI file appeared twice, once per
persisted endpoint.

**Decision:** From 901 pixels of width, the inspector of a node, an HTTP
route or an HTTP call docks to the right edge as a full-height panel. The
graph stays interactive and its right workspace band reserves the panel
width, so cards never sit under it. Contract, schema, DTO, entity and
runtime views keep the centered modal: they need the full width. At 900
pixels or less every view uses the modal.

Amendment (2026-11-01, PDR-10): DTO, Mongo persistence-class, and JPA
entity field views also dock, with the node inspector skeleton. They list
relations more than wide content. Contract viewers keep the modal.

The node view, docked or modal, shows a four-tile strip (relations, proven,
inferred, without evidence level) computed from the same counts as the
details badges, then one row of secondary actions. Relation groups become
disclosure groups with an item count; the first three open, and a group
that the widget defines but leaves empty stays listed and disabled. The
inspected node carries a halo in the graph while the panel shows it.

`Routes exposées` groups served routes by method and path in the export
only. The merged row lists each evidence source (Java, OpenAPI) with its
location. Persisted endpoints and their identifiers are unchanged.

**Consequences:** Inspecting no longer hides the graph, and the panel can
follow Shift-clicks on other nodes without closing. The inline details
widget keeps its badges; only the inspector clone is restructured, so the
inline contract is unchanged. A plain click still opens nothing (PDR-1).
Tests select the modal fallback by viewport width, not by inspector kind.

## ADR-69: Render the graph with Sigma 4 and drop libavoid

**Status:** Accepted on 2026-11-01. The product owner asked for both changes,
which lifts the deferral of CDN dependency work for these two libraries only.

**Context:** The export loaded Sigma 2.4.0 and graphology 0.25.4; Sigma 4.0.4
is current. Sigma 2.4 ignores its documented camera settings, so the export
cancels its gestures by event interception alone. The export also imported
`@mr_mint/elkjs-libavoid` from esm.sh without a version on every page load.
Its router was never called: the obstacle list it routed had no edges since
the selected call graph became a call tree.

**Decision:** Load Sigma 4.0.4 from jsDelivr, the first CDN that serves it,
and graphology 0.26.0 from cdnjs. Declare one node primitive, a filled square
under each HTML card, and one edge primitive, a straight path with an arrow
head. Styles read the graph attributes; the existing reducers stay as the
selection and filter rules and return `visibility: "hidden"` instead of
`hidden`. Sizes keep the screen-pixel reference of Sigma 2. Sigma's camera
panning and zooming stay enabled, because Sigma 4 also gates
`camera.setState` on them. The shared controller cancels Sigma's own wheel
and drag at the mouse captor, and node drag and camera rotation are off.
Remove libavoid, its WASM loader, and its dead obstacle code.

**Consequences:** The export needs WebGL 2. Sigma receives every press and
release, so a click on the empty stage clears the selection again; the pan
controller no longer captures the pointer. Rendering in cards and symbols
modes is pixel-identical on the supermarket lab export in Chrome. Arcs of the
selected call graph keep the local orthogonal router of ADR-28. Swagger UI
and the layout libraries keep their versions.

## ADR-70: Give the source engine CodeQL's interface witness and dispatch gaps

**Status:** Accepted on 2026-11-03. Amends ADR-61. Amended on 2026-11-06
by lot 3 of the review backlog (finding A4): the witness expansion adds no
edge where a declared edge already reaches the implementation.

**Context:** On the train-ticket model the `source` engine finds the same
flows as `codeql` (406 against 408) but persists 2052 call edges against
8264, and the exported call tree of an entry reaches one method fewer on
219 of 260 entries. Measured by qualified name, the gap has three parts:
calls whose caller or callee is outside the integration-method universe,
which the export already filters for both engines; 360 calls to an
interface method that the source pass resolves straight to its unique
implementation, so the caller-to-contract hop CodeQL keeps as a witness
disappears from the tree; and 63 calls between indexed methods that the
pass leaves unresolved: `Runnable.run` and `Callable.call` on receivers
typed by an external interface whose implementations are in the sources,
`hashCode`, `toString` and `equals` redefined by a source type, and a few
named inner classes, chained getters and static helpers.

**Decision:** The source pass emits the same shapes as the CodeQL query of
`engines/codeql/queries.py`. When the static receiver type is a source
interface and the exact target is a concrete implementation, the pass also
emits a `possible` witness edge to the interface declaration; an interface
method that is itself the exact target stays a single exact edge. A
receiver typed by an interface that is not in the sources dispatches to the
source implementations of that method name and arity, `exact` when one
concrete body exists in the closed world and `possible` otherwise. A call to
`hashCode`, `toString` or `equals` on a source type reaches the body that
type or its source ancestors declare. Named inner classes, chained record
and bean accessors and static helpers resolve under the existing receiver
rules. Ambiguous same-arity overloads stay unresolved: no rule invents a
target.

**Consequences:** The exported call tree shows the contract hop with the
source engine, so a `source` export and a `codeql` export of one project
read the same way. The witness targets an abstract method, and its
synthetic dispatch reaches only the site's exact implementation, so it adds
no route and changes no flow confidence. Persisted names and
the `codeql_*` keys are unchanged. Measured on train-ticket, the source
engine persists 3008 edges and finds 407 flows; the export holds 1457 edges
between integration methods against 877, the same 1170 call sites as
CodeQL, and no entry where CodeQL reaches more methods, against 219 of 260.
`analyze compare-engines` still reports one CodeQL-only flow, a `cycle`
through `TSOStr.write`: it needs dispatch from `java.io.OutputStream` to a
subclass of the servlet API's `ServletOutputStream`, a library hierarchy
the sources do not contain. Dispatch on an external interface stays
`possible` when an anonymous class or a lambda can implement it.

Amendment of 2026-11-06 (A4): the witness hop is the declared `possible`
edge to the interface method. Its expansion to the site's exact
implementation repeated the exact edge as a `possible`, inferred one, and
the flow search explored every method below it twice. The join now drops
an expanded edge when a declared edge at the same site already reaches
the same callee. The persisted graph keeps two edges at such a site, the
exact one and the witness; flows and their confidence are unchanged.

## ADR-71: Scope external-interface dispatch to the caller's visible modules

**Status:** Accepted on 2026-11-03. Amends ADR-70. Amended on 2026-11-06
by lot 3 of the review backlog (finding J2, task C6 of the dispatch-scope
plan): the scope also bounds the edges of a typed engine. The amendment's
refinement was confirmed by the product owner on 2026-11-07.

**Context:** ADR-70 lets a call on an interface outside the sources, such
as `ExecutorService`, `Callable` or `UserDetails`, dispatch to the source
classes that implement it. The candidates come from every indexed module,
because the symbol pass does not receive the module dependencies. On
train-ticket this adds nine edges from a caller to a module that is not
among its transitive dependencies. One of them is `exact`:
`TravelServiceImpl.queryInParallel` reaches `TEServ.submit`, a monitoring
library under `old-docs/`, and produces a false `cycle` flow. The pool is
a JDK class, so the single source body proved nothing.

**Decision:** A call on an external interface dispatches only to source
implementations declared in the caller's module or in its transitive
module dependencies. A library therefore never dispatches into a module
that depends on it: the persisted graph has no classpath context, and a
flow entering the library from one service would continue into another
service. This dispatch is always `possible`, even with one source body,
because a library can supply the implementation. When no module
dependency is known for the caller, only its own module is visible.

**Consequences:** The nine edges and the false cycle flow disappear on
train-ticket. Calls through `ExecutorService`, `Callable` and similar
interfaces no longer raise a flow's confidence. Edges to source interfaces,
witnesses, and the other ADR-70 shapes are unchanged.

Measured on 2026-11-03 with fresh `source` indexes of copies. Train-ticket
goes from 3008 to 2999 edges (exact 1874 to 1844, possible 1134 to 1155)
and from 407 to 406 flows. The 406 flows are all `potential` and `medium`,
the same set as before ADR-70; only the `queryInfoInparallel` cycle goes.
No edge leaves its caller's visible modules. Besides the nine dropped
edges, 29 exact edges inside `micro-service-monitoring-core` become
`possible`. The lab keeps 87 edges and 23 flows.

CodeQL parity: the `codeql` engine on train-ticket reports 408 flows and
8264 edges, 16 of them outside the caller's dependencies. Both engines
report the same 406 `potential` flows; CodeQL adds two `cycle` flows,
`queryInfoInparallel` and `imageCode`, which C6 of the plan addresses. The
[dispatch-scope plan](plans/call-graph-external-dispatch-scope.md) records
the measurement.

Amendment of 2026-11-06 (J2, C6): a typed engine (`codeql`, `joern`)
resolves dispatch over every projected type, without the module
dependencies. When the index knows module dependencies, the join drops an
engine edge between two modules the build graph does not relate: the
callee module is neither on the caller's module path (the caller's module
and its transitive dependencies) nor a module that depends on the
caller's. The dispatch the join adds for a typed engine, and the symbol
pass that fills its gaps, keep the same scope. The `source` engine is
unchanged.

A module that depends on the caller's stays reachable because it can
implement the caller's interfaces, as an adapter implements a port; the
observability lab's library `kafka-processing` dispatches that way, and
its 23 flows need it. An index without any known module dependency keeps
every engine edge: there is no build graph to judge it.

The product owner confirmed this refinement on 2026-11-07, which closes
task C6 of the dispatch-scope plan.

## ADR-72: Keep dispatch possible when an implicit implementation exists

**Status:** Proposed on 2026-11-03 for task K3 of the
[call-graph coverage plan](plans/call-graph-coverage.md). Not validated.
Amends ADR-70.

**Context:** ADR-70 keeps a call on an external interface `possible` when
an anonymous class or a lambda can implement it. A call on a source type
does not get that prudence. A field typed by a source interface `Fn`, with
one named implementation `FnImpl` and a lambda `() -> {}` assigned to an
`Fn` elsewhere, yields an exact edge to `FnImpl.apply`. A variable holding
`new Base() { void hook() {} }` yields an exact edge to `Base.hook`. Both
edges claim a proof the sources do not give: ADR-62 attributes anonymous
and lambda bodies to the creating method, so no indexed method stands for
the runtime body.

**Decision:** A call whose receiver type is a source type stays
`possible` when the sources contain an anonymous class extending or
implementing that type, or its ancestors, or a lambda or method reference
whose target type is that type. The lambda's target type is known only
when it is assigned, returned or passed to a declared source type;
otherwise it does not count. The named implementations stay the targets:
no edge is added to the creating method.

**Consequences:** Some exact edges become `possible`, and some flows lose
their exact confidence. No edge disappears. The measured change on
train-ticket is recorded in the plan before delivery.

## ADR-73: Scope every dispatch to the caller's visible modules

**Status:** Proposed on 2026-11-03 for task K4 of the
[call-graph coverage plan](plans/call-graph-coverage.md). Not validated.
Amends ADR-71; would also deliver task C6 of the
[dispatch-scope plan](plans/call-graph-external-dispatch-scope.md).

**Context:** ADR-71 limits dispatch on external interfaces to the
caller's module and its transitive dependencies. Dispatch on a source
type is not limited: a call in a library on a class it declares still
reaches an override in a service that depends on the library. The
`codeql` engine has the same defect for both kinds: on train-ticket it
persists 16 edges outside the caller's dependencies and two false
`cycle` flows. When one qualified type is declared in two modules, the
call stays `possible` even when only one module is visible.

**Decision:** Every call edge, from either engine, targets a method
declared in the caller's module or in its transitive module dependencies.
The `source` engine applies the rule while selecting candidates; the
join of `code_flows.py` drops a `codeql` edge that breaks it. When one
qualified type has a single visible declaration, it is the receiver
type. A module with no known dependency sees only itself.

**Consequences:** A library never calls into a module that uses it in
the persisted graph, for either engine. On train-ticket the `codeql`
engine is expected to lose its 16 out-of-scope edges and the two false
cycles; the measured flow count is recorded before delivery. A real callback from a
library into an application, through an interface it declares, is no
longer shown: the graph has no classpath context to tell which
application loaded the library.

## ADR-74: Dispatch calls on external classes to source overrides

**Status:** Proposed on 2026-11-03 for task K6 of the
[call-graph coverage plan](plans/call-graph-coverage.md). Not validated.
Extends ADR-70 and ADR-71.

**Context:** ADR-70 dispatches a call on an external interface to its
source implementations. A call on an external class does not dispatch:
`Thread t; t.run()` never reaches `MyRunner.run` when `MyRunner extends
Thread`. ADR-70 names this gap for the remaining CodeQL-only flow through
`TSOStr.write`, a subclass of the servlet API's `ServletOutputStream`.

**Decision:** A call on an external class dispatches to the most
specific source override of the method name and arity in each source
subtype, in the caller's visible modules. The edge is always `possible`,
because the external class has its own body and other library
subclasses. Overloads the arguments cannot separate stay unresolved.

**Consequences:** Framework subclasses, such as threads, filters or
output streams, become reachable through their library supertype.
Edges are added, never made exact.

## ADR-75: Make constructors and initializers part of the call graph

**Status:** Accepted on 2026-11-07 by the product owner. Proposed on
2026-11-03 for task K7 of the
[call-graph coverage plan](plans/call-graph-coverage.md); delivered with
lot 6 of the review backlog (finding J5) for the `source`, `codeql` and
`joern` engines.

**Context:** The call graph has one node per method declaration.
Constructors, instance and static initializers, and field initializers
are not nodes, so the calls they make have no caller, and `new X(...)`
reaches nothing. The CodeQL query already reads calls whose enclosing
callable is a constructor, but it only reports `Method` targets.

**Decision:** A constructor becomes a node with the qualified name
`Owner.<init>` and its parameter types; each type with initializer code
gets one `Owner.<clinit>` node for static code and its field and instance
initializers are attributed to every constructor of the type. A
`new X(...)` call targets the constructor its arguments select, exact
when one matches; `this(...)` and `super(...)` are exact calls. A
constructor is never an IN or OUT port and never starts a flow.

**Consequences:** New rows enter `integration_methods` with names that
cannot collide with Java method names, so existing identifiers and the
schema keep their shape. Existing indexes need a reindex to show the new
nodes; the code-flow signature changes to trigger it. The export and the
UX contract need a rule for constructor nodes, decided with this record.

Delivery notes of 2026-11-07, within the decision:

- Every written constructor is a node. An implicit constructor gets a node
  only when the type's instance initializer code makes a call, and
  `<clinit>` only when its static initializer code does: other implicit
  bodies would be nodes without an outgoing edge. Both implicit nodes span
  the type's header, from its first annotation to its opening brace, where
  the typed engines locate them. A record's implicit canonical constructor
  and its compact constructor are not nodes.
- Instance initializer code is attributed to every constructor that does
  not start with `this(...)`, as Java runs it (JLS 12.5): a delegating
  constructor reaches it through its `this(...)` edge. The engines agree:
  CodeQL calls `<obinit>` from those constructors, Joern inlines the
  field initializers there.
- The implicit `super()` call is not an edge, nor is the creation of an
  anonymous class or a lambda (ADR-62). An engine's constructor that the
  index has no node for, such as a default constructor, joins nothing and
  is no placeholder.
- Joern leaves initializer blocks out of its graph; the symbol pass adds
  their calls beside it, as it fills the other gaps of a typed engine.
- Export and UX rule: a constructor or initializer node is a node of the
  exported call graph (`codeql_call_graph`), spelled `Owner.<init>` or
  `Owner.<clinit>`, and a step of the flows that pass through it. It owns
  no port, so no service occurrence or flow entry shows it as its method.
- The product owner confirmed this export rule and the narrower scope
  of the notes above on 2026-11-07.

Measured on 2026-11-07 on train-ticket, before and after, with
`analyze compare-engines` on fresh copies (CodeQL 2.27.3, Joern 4.0.674):

| Engine | Engine calls | Flows | Edges (indexed methods) | Run time |
|---|---|---|---|---|
| `source` | 0 | 406 (all medium) | 1524 to 1712 | 0.8 s |
| `codeql` | 6279 to 6932 | 406 (all medium) | 1524 to 1712 | 90 s, database built |
| `joern` | 1541 to 1753 | 406 (395 medium, 11 low) | 1524 to 1712 | 37 s, graph built |

The index gains 133 `<init>` and 96 `<clinit>` nodes. The 188 new edges
are all `exact`: 160 reach a constructor, 32 leave one; no `<clinit>`
calls a source method. The three engines share every keyed edge, and no
flow passes through a constructor, so the flows do not change.

## ADR-76: Type Kafka payloads with one shared scanner rule

**Status:** Accepted on 2026-11-03, at the product owner's request.

**Context:** A producer's `message_type` came from two local readers:
`KafkaTemplate.send` typed its last argument, the Strategy1
`envoyerMessageKafka*` methods their second. Both lost common shapes
(`this.field`, inherited fields, mapper calls, getters, casts, lambdas),
and both stored types that name no DTO, such as `T`, `Object` or a JSON
`String`. Such a type blocked the pairing with a consumer typed
`OrderEvent`, as did `p.OrderEvent` against `OrderEvent`.

The call-graph symbol pass (`indexing/java_symbols.py`) already types
expressions, but it erases generic arguments and `scanner/` must not
import `indexing/`. Completing types in the indexing stage, as CodeQL does
for Strategy1, would also rebuild relations and DTO definitions after
flows, and it would add a second rule beside the scanner's own.

**Decision:** One module, `scanner/kafka_payload_types.py`, types the value
expression of both send paths at scan time. It reads written types, so
`Message<T>`, `KafkaTemplate<K, V>` and collection element types stay
visible, and it resolves receivers across repository Java files through
imports and packages. When the value stays unknown, the value type of the
receiving `KafkaTemplate<K, V>` is the fallback.

`domain/message_types.py` decides what is known: `Object`, `String`,
`byte[]`, wildcards, raw containers and declared type variables are
unknown, on both sides of a topic and for CodeQL evidence. Pairing compares
keys without package qualifiers and with generic arguments. The stored
type keeps its source spelling. Two different known DTO names still
prevent the edge.

**Consequences:** More producers get a type, and fewer edges are blocked
by a type that says nothing. No schema change: existing indexes compare
correctly at once, and a reindex refreshes the stored types. A consumer
typed `String` becomes unknown. `List<OrderEvent>` stays distinct from
`OrderEvent`, which can block a batch listener; that case is not
addressed here.

The scanner holds a second, smaller typing rule beside the symbol pass.
An incremental index rescans changed files only, so a type taken from an
unchanged file refreshes at the next full index. A call is typed only
when one source declaration matches its name and arity; generic
substitution beyond collection elements stays unknown.

## ADR-77: Match REST routes by Spring MVC specificity

**Status:** Accepted on 2026-11-03 by the product owner.

**Context:** A `{var}` segment matched literal segments both ways, so a call
to `GET /items/count` reached `/items/count` and its sibling `/items/{id}`;
a templated call reached every literal sibling. A `{id:\d+}` constraint was
ignored, so `/orders/abc` reached it (review finding H4).

**Decision:** Route matching inside an identified target service follows
Spring MVC: the most specific compatible route wins. A literal segment beats
a template, a route without `**` beats one with it, and an explicit HTTP
method beats `ANY`. A `{name:regex}` constraint must match a literal
segment; a constraint Python cannot compile is treated as unconstrained.

A templated call, such as `/items/{path}`, keeps the route whose templates
align with its own (`/items/{id}`). When every candidate would need a guess
about the call's unknown value, no single best match exists and every
candidate is kept. A call with `ANY` is ranked per served method, and a
gateway call ending in `**` keeps every compatible route.

**Consequences:** Fewer false route edges, and flows enter the route Spring
would dispatch to. When several candidates are kept, the route ambiguity is
visible as several edges; the service relation keeps its confidence, because
the host, not the route, identifies the service. A templated call aligned
with a template route no longer reaches a literal sibling that the runtime
value might hit.

## ADR-78: Hold a CodeQL reachability flow to the join's rules

**Status:** Accepted on 2026-11-03 with lot 2 of the
[indexing review](plans/indexing-review-2026-11-03.md) (finding F1).
Amends ADR-29 and ADR-30.

**Context:** ADR-29 turns each CodeQL reachability row into a flow, with a
two-endpoint direct flow when no intermediate route is found, and ADR-30
lets that direct proof replace the join's flows of the same pair. The row
is checked only against the target's module. A route through another
application, a dispatch the entry's receiver cannot take, or a route past
`codeql_max_hops` therefore became a flow, and it erased the join's own
routes for that pair.

**Decision:** A reachability row becomes a flow only through a
representative route of the indexed call graph that passes every rule of
the main join: the application boundary on each hop, the entry's dispatch
(`dispatch_matches_entry`), and `codeql_max_hops`. Synthetic edges and,
for a `medium` row, `possible` edges stay excluded from the route. A row
with no such route creates no flow; the two-endpoint direct flow is gone.
A pair the join already connects keeps the join's flows, which the row
never replaces.

**Consequences:** No flow is stronger than the evidence the join can check.
CodeQL reachability no longer recovers a pair whose calls are missing from
the persisted call graph; that pair stays absent rather than unchecked. On
train-ticket the CodeQL index keeps its 408 flows and 397 anchor pairs;
the 170 call flows keep their routes and confidence and take back the
join's IDs and reasons, which the direct flows had replaced. The
code-flow signature changes, so the next index rebuilds the flows.

## ADR-79: Treat generic payload carriers as unknown Kafka types

**Status:** Accepted on 2026-11-03 with lot 2 of the indexing review (K7).
Amends the unknown-type list of ADR-76.

**Context:** ADR-76 makes `Object`, `String`, `byte[]`, wildcards, raw
containers and type variables unknown. A producer typed `JsonNode`,
`GenericRecord`, `ByteBuffer` or `Map<String, Object>` still counted as a
known DTO. Its name differs from the consumer's `OrderEvent`, so the
pairing was blocked by a type that says nothing about the payload.

**Decision:** `domain/message_types.py` holds one list, `CARRIER_TYPES`, of
generic carriers: JSON trees (`JsonNode`, `ObjectNode`, `ArrayNode`,
`JsonElement`, `JsonObject`, `JSONObject`), Avro records (`GenericRecord`,
`GenericContainer`, `IndexedRecord`, `SpecificRecord`,
`SpecificRecordBase`), Protobuf base messages (`AbstractMessage`,
`DynamicMessage`, `GeneratedMessage`, `GeneratedMessageV3`), `Bytes`,
`ByteBuffer`, `Serializable` and the `Map` family with `Properties`.

A type is a carrier when its erased simple name is in the list, so the
package and the generic arguments do not matter: `Map<String, Order>` is a
carrier. A carrier is unknown on both sides of a topic, like `Object`.

**Consequences:** Such a producer or consumer pairs with any typed peer on
the same topic, with the medium confidence of an unknown type. A carrier
type is no longer persisted as `message_type`. No schema change: the
export compares stored types with the same rule, and a reindex refreshes
the persisted relations. A generated Avro class keeps its own name and
stays a known DTO. A project DTO that reuses a carrier name, such as a
class named `Bytes`, is unknown too; that loss is accepted over a false
mismatch.

## ADR-80: Follow the build tool's project model for module ownership

**Status:** Accepted on 2026-11-04 at the product owner's request (lot 3)
of the [indexing review](plans/indexing-review-2026-11-03.md) (findings I2,
I3, I4, I6, I7, I9). Applies ADR-11's `racine` segment to Gradle too.

**Context:** Module inventory, file ownership and module dependencies each
had their own rules. Gradle files belonged to the enclosing Spring Boot
project, so a root application owned every subproject and a library owned
nothing. Inventory stopped at five directory levels while Maven ownership
did not. Every build file was a module, declared or not. Dependencies
matched on `artifactId` alone and counted test scopes. An identity change
left unchanged files with the old identity.

**Decision:** One build layout per index drives inventory, ownership,
identities and the excluded perimeter.

- A file belongs to the deepest Maven or Gradle project that contains it,
  application or library. When one directory has both build files, Maven
  wins. There is no depth limit. The walk skips hidden directories,
  `node_modules` and build outputs.
- An aggregator POM with `<modules>` declares its build: below it, only the
  declared POMs are projects, recursively, profiles included. A
  `settings.gradle(.kts)` with literal `include` calls does the same, with
  literal `projectDir` remaps. A declared Gradle project is named after its
  project path. A module entry that does not resolve, or a computed
  include, declares nothing, and every build file below stays a project.
- `buildSrc` and `includeBuild` directories are never projects. With test,
  mock and archetype modules, they are excluded with everything below them.
  An undeclared build directory is excluded unless it contains a project.
- Identities are collision-safe across Maven and Gradle projects of the
  inventory; a qualified root uses `racine`.
- A Maven dependency names a local module only when its `groupId` matches
  the module's declared or inherited one, when both are known. Maven `test`
  and Gradle test configurations (`test*`, `androidTest*`, `testFixtures*`,
  `<sourceSet>Test*`) give no edge. `provided`, `runtime`, `compileOnly`
  and `runtimeOnly` stay edges: they are build or runtime dependencies.
- An incremental index rescans every file below a module whose identity
  changed, appeared or disappeared; a change at the root forces `--full`.

**Consequences:** Every endpoint, relation and flow names a module of the
inventory. Gradle library endpoints now carry their module, so their
relations exist. Stray copies such as train-ticket's
`old-docs/Lib/ms-monitoring-core` leave the inventory, and their sources
are no longer indexed. A repository whose aggregator omits a real service
loses that service until the build declares it. The removed five-level
test is replaced by a regression test for deep modules.

## ADR-81: Keep a dispatch exact only when its implementor set is complete

**Status:** Proposed on 2026-11-04 for lot 3 of the
[call-graph review](plans/call-graph-review-2026-11-04.md). Not validated.
Amends ADR-66 and ADR-70.

**Context:** In a closed world, the `source` engine marks a call `exact`
when one source body implements the called method. The review found
implementors that the sources declare without writing a body: record
accessors, Lombok-generated getters, bodies inherited from an external
class, and `Object`'s own `toString`, `equals` and `hashCode`. The one
written body then becomes a false `exact` target. CodeQL has the same
effect with test implementations in a user-built database, and both
engines mark the `fallback` class of a `@FeignClient` as the exact target
of a call that really goes over HTTP. Calls to `equals` or `hashCode` on
an `Object` receiver fan out to every override in the sources.

**Decision:**
1. A call is `exact` only when every implementor of the static receiver
   type has a known body, and exactly one body applies. A record accessor
   or a Lombok-generated member is a known implementor without an indexed
   body; a body inherited from an external class or from `Object` is an
   unknown implementor. Either one keeps every source target `possible`.
2. Test sources are never part of the world: both engines ignore
   implementations declared under test source roots.
3. A source class named by `@FeignClient(fallback = ...)` or
   `fallbackFactory` is a `possible` target; the interface method, which
   carries the HTTP call, stays the primary target of the call.
4. A call to an `Object` method on a receiver typed `Object` or by a type
   variable produces no edge: its runtime type is unknown.

**Consequences:** Some `exact` edges become `possible`, and flows built
on them lose confidence. No edge is invented. On train-ticket the measured
change is recorded before delivery; a flow lost entirely must be explained.

## ADR-83: Check a supplied CodeQL database against the indexed sources

**Status:** Accepted on 2026-11-04 with lot 1 of the
[call-graph review](plans/call-graph-review-2026-11-04.md) (findings Q1
and Q5), whose fixes the product owner validated.

**Context:** `--codeql-database` trusted its database. One built from an
older revision placed old call lines in other methods as `exact` edges,
and one built from another source root joined no row while the status
still said `complete`. Both looked like a valid CodeQL index.

**Decision:** Before any query, compare each indexed Java file with its
copy in the database's source archive. One differing file, or no indexed
file in the archive, fails the index with an error that names the cause.
A file CodeQL did not archive is accepted. A call result whose rows name
no indexed file fails the same way. The hash of `codeql-database.yml`
enters the code-flow signature. Refusing is preferred to falling back to
the `source` engine: the user asked for CodeQL, and a silent fallback
would change the engine behind the same command.

**Consequences:** A stale or misplaced database can no longer create
flows. The index keeps its previous snapshot, and the user recreates the
database. A file added after the database was created is not detected;
its calls are missing, which loses edges but invents none. The check
reads the archive once; on train-ticket it compares 409 of 411 indexed
Java files.

## ADR-84: Reuse the automatic CodeQL database and drop reachability from the index

**Status:** Accepted on 2026-11-04 with lot 6 of the
[call-graph review](plans/call-graph-review-2026-11-04.md) (finding Q12),
whose fix the product owner validated. Amends ADR-29.

**Context:** The automatic database lived in a temporary directory and
was created on every index: 117 s on train-ticket against 25 s with a
prebuilt database. ADR-29's reachability query cost about 3 s more, yet
since ADR-78 a reachability row becomes a flow only through a route the
join already checks. Without it, train-ticket and the lab keep the same
406 and 23 flows and the same edges.

**Decision:** Cache the automatic database in
`.systemlens/codeql/java-database`, with a fingerprint of the projected
Java sources (paths and contents) and of the CodeQL executable. A run
with the same fingerprint reuses it; any change creates it again and
replaces the cache. The index stops running the reachability query; the
query and ADR-78's rules stay available to other callers.

**Consequences:** An index of unchanged sources takes 24 s instead of
117 s on train-ticket. The cache takes disk space (65 MB there) under
`.systemlens/`, which the inventory already skips. A change to one Java
file still recreates the whole database. Reachability no longer appears
in the index's progress lines.

## ADR-85: Let each engine own its options and add Joern

**Status:** Accepted on 2026-11-06. Amends ADR-61 and ADR-66. Amended
on 2026-11-07: the in-house engine moves to `engines/custom/`, and Joern
reports Kafka payload evidence for Strategy1.

**Context:** ADR-61 put the engine behind a seam, but the rest of the
application still speaks CodeQL. The CLI exposes `--codeql-database`,
`--codeql-progress`, `--refresh-codeql-view`, `--resume-codeql-join` and
`--codeql-edge-confidence`. The configuration holds `codeql_*` settings,
some of which bound every engine, such as the timeout and the hop limit.

The registry and the `init` profiles list engine names by hand. Adding a
third engine would mean editing the CLI, the configuration, the indexing
stage and the registry. The user asked for Joern as a third engine, with
an application that does not depend on which engine is chosen.

**Decision:** An engine package declares its name, label, capabilities,
availability check, configuration section, CLI options and `init`
profile. The registry discovers engines from that declaration; the core
lists no engine name. Outside `engines/` and the registry, code names
only engine-neutral concepts: `call_graph_*` or `engine_*`.

Settings that bound any engine become generic `analysis.engine_*` keys:
timeout, threads, RAM, hop limit and edge confidence. Settings owned by
one engine move under `analysis.engines.<name>`, for example
`analysis.engines.codeql.database`. CLI options follow the same split.
The old `codeql_*` keys and `--codeql-*` flags are removed, not aliased.

Joern lives in `engines/joern/`. It builds a code property graph from
the Java sources with `joern-parse` (`javasrc2cpg`, no build needed). A
Joern script exports methods and calls with their file, line and
resolved target as JSON, which the adapter maps to `MethodCall` and
`SourceMethod`.

A call Joern cannot resolve stays `possible` or is dropped; the adapter
never guesses a target. The Joern version enters `input_fingerprint`.
Joern reports no reachability, no Kafka payload type and no module
refresh; the shared source passes keep those duties.

**Consequences:** A new engine is one package and one registry entry,
with no change to the CLI, the configuration loader or the indexing
stage. A test fails when a named engine appears outside its package.

Existing `config.yaml` files and scripts that use `codeql_*` keys or
`--codeql-*` flags break and must be rewritten. Persisted names stay as
they are (SQLite tables, `codeql_*` meta keys, the export's
`codeql_call_graph` key), so existing indexes still load.

Joern needs a JDK and adds a JVM start-up and a graph build to each
index; on macOS its launchers also need `greadlink` (coreutils). On
train-ticket (406 flows), `analyze compare-engines` measured on
2026-11-06, with Joern 4.0.674 and CodeQL 2.27.3:

| Engine | Engine calls | Flows | Edges (indexed methods) | Run time |
|---|---|---|---|---|
| `joern` | 6305 | 406 (395 medium, 11 low) | 1797 | 43 s, 4.4 s cached |
| `codeql` | 6279 | 406 (all medium) | 1811 | 207 s, 7.4 s cached |
| `source` | 0 | 406 (all medium) | 1811 | 0.7 s |

All three find the same 406 flows. `codeql` and `source` agree on every
edge between indexed methods. Joern shares 1517 keyed edges with each, adds
none, and misses 7, all in `ts-contacts-service`: Joern leaves those
`ContactsService` calls with an unresolved signature, so the adapter drops
them and 11 flows there fall to `low`. Joern is therefore not recommended
over `codeql` or `source` on this evidence.

Measured again on 2026-11-06 after lot 1 of the review backlog, which
runs `javasrc2cpg` with `--delombok-mode types-only`. The default mode
built the graph on delomboked text, so 108 Lombok files had methods
beyond their last line and 59 engine methods duplicated an indexed one:

| Engine | Engine calls | Flows | Edges (indexed methods) | Run time |
|---|---|---|---|---|
| `joern` | 1534 | 406 (395 medium, 11 low) | 1797 | 42 s, 4.0 s cached |
| `codeql` | 6279 | 406 (all medium) | 1811 | 7.2 s cached |
| `source` | 0 | 406 (all medium) | 1811 | 0.7 s |

No Joern method or call line now lies beyond its file, and no engine
method shares a path and name with an indexed method it does not match.
The engine calls fall because a call to a Lombok-generated accessor has
no source line and is dropped. Joern shares the same 1517 keyed edges,
adds none, and still misses the 7 `ContactsService` calls.

Measured again after lot 3 (J2, J3, J4, A4), with the CPG cached:

| Engine | Engine calls | Flows | Edges (indexed methods) | Run time |
|---|---|---|---|---|
| `joern` | 1541 | 406 (395 medium, 11 low) | 1524 | 3.7 s cached |
| `codeql` | 6279 | 406 (all medium) | 1524 | 6.6 s cached |
| `source` | 0 | 406 (all medium) | 1524 | 0.6 s |

The edge count falls for every engine because a witness no longer
repeats the exact edge (A4). The three engines share every keyed edge.
Joern now reaches the 7 `ContactsService` declarations as `possible`
(J3), and the join's dispatch reaches their implementation as
`possible`, inferred, where the other engines are `exact`: the 11
`ts-contacts-service` flows stay `low`.

Amendment of 2026-11-07 (validated request brief). The in-house engine
leaves `callgraph/source.py` for `engines/custom/`, beside `codeql/` and
`joern/`, with a `declaration.py` and an `engine.py`. Its name stays
`source`: the configuration value, the CLI flags and the SQLite schema
do not change. `callgraph/` keeps only the engine-neutral seam and the
registry, and the indexing stage falls back to the default engine through
the registry, so no core module imports a package under `engines/`.

The shared symbol pass (`indexing/java_symbols.py`) and the join in
`indexing/code_flows.py` stay in the core. They run for every engine, the
typed ones included; moving them into `engines/custom/` would make the
core depend on an engine package.

Each engine provides its share of Strategy1 (ADR-5) through
`payload_types()`, which the stage reads only under `--strategy
strategy1`. Joern now reports payload evidence, which replaces "Joern
reports no Kafka payload type" above. For each `envoyerMessageKafka*`
call, the export script records the second argument, its declaration as
written, and the local assignments of the calling method. The adapter
then follows CodeQL's query: the type argument of a `Message` or
`GenericMessage` (first) or `ProducerRecord` (second) wrapper; else the
types of the values assigned to the variable, through other variables;
else the argument's own type. The `source` engine has no data flow beyond
the scanner's AST rule (ADR-76) and reports no evidence. The stage applies
ADR-76 to every engine's evidence: a type that names no DTO stays unknown.

