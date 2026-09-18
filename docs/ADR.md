# Architecture Decision Records — systemlens (`systemlens`)

## Reading guide

ADRs explain *why* durable constraints exist. They do not replace the current
functional or technical contract.

| Topic | ADRs |
|---|---|
| Local, conservative extraction | [ADR-1](#adr-1--local-static-architecture-analysis), [ADR-3](#adr-3--conservative-static-resolution) |
| Snapshot storage and compatibility | [ADR-2](#adr-2--sqlite-is-the-local-fact-store), [ADR-7](#adr-7--publish-each-index-as-an-atomic-sqlite-snapshot), [ADR-11](#adr-11--separate-module-identity-from-its-display-alias) |
| Delivery and graph projection | [ADR-8](#adr-8--persisted-relations-are-the-canonical-architecture-projection), [ADR-9](#adr-9--exports-never-enrich-a-snapshot-from-live-source-files), [ADR-23](#adr-23--mcp-control-in-two-phases-with-a-graph-enrichment-layer) |
| Optional or repository-specific behaviour | [ADR-5](#adr-5--strategy1-conventions-are-opt-in), [ADR-12](#adr-12--kubernetes-discovery-is-explicit-and-snapshot-based) |

## ADR-1 — Local static architecture analysis

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

## ADR-2 — SQLite is the local fact store

**Status:** Accepted.

**Context:** Incremental inventory, MCP queries and graph rendering require a
portable local state.

**Decision:** Persist files, endpoints, modules, dependencies and architecture
relations in `.systemlens/findings.db`. The filename is retained for compatibility
with existing installations.

**Consequences:** The database is private implementation state; clients use CLI
and MCP contracts rather than writing SQL. Legacy external-analyzer results are
cleared during the first AST-only index run.

## ADR-3 — Conservative static resolution

**Status:** Accepted.

**Context:** HTTP paths and Kafka topics are often composed from properties,
constants or runtime values.

**Decision:** Resolve only literals and supported local Spring expressions. Mark
the rest as dynamic and preserve source evidence.

**Consequences:** The inventory favours trustworthy partial results over
plausible but unsupported dependencies.

## ADR-4 — Workspace federation is read-only

**Status:** Accepted.

**Context:** A parent workspace may contain independently indexed services.

**Decision:** Load service indexes read-only and normalize them before graph and
audit queries.

**Consequences:** Federated exploration never rewrites another repository’s
index and reports incomplete or stale sources as warnings.

## ADR-5 — Strategy1 conventions are opt-in

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

## ADR-5a — Strategy1 conventions live behind one pack boundary

**Status:** Accepted.

**Context:** Strategy1 rules had grown across Kafka extraction, REST target
resolution, OpenAPI declaration invalidation, topic request/reply derivation,
and layer classification. Passing a `strategy1` boolean through generic modules
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

## ADR-6 — SystemLens is the public product and state namespace

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

## ADR-7 — Publish each index as an atomic SQLite snapshot

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

## ADR-8 — Persisted relations are the canonical architecture projection

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

## ADR-9 — Exports never enrich a snapshot from live source files

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

## ADR-10 — Persist the analysis profile with each snapshot

**Status:** Accepted.

**Context:** Strategy-dependent extraction facts were persisted, but delivery
adapters could silently select a different default while reading or refreshing
the same index.

**Decision:** Load the persisted topic strategy as an immutable
`AnalysisProfile` with every architecture inventory. CLI, MCP, graph, audit,
and export adapters consume that profile. A federation rejects source indexes
whose profiles are incompatible.

**Consequences:** MCP reindexing preserves the existing strategy, and a
Strategy1 convention cannot appear in a default inventory or disappear from a
Strategy1 one because of the delivery path.

## ADR-11 — Separate module identity from its display alias

**Status:** Accepted.

**Context:** Maven artifact IDs and Gradle project names are not unique within
all workspaces or across independently indexed federated repositories.

**Decision:** Persist a collision-safe module identity. It equals the build
name when unique and is qualified with the relative module path when a
collision exists. Endpoint, relation, dependency, and federation keys use the
identity; the build name remains a display alias. Direct service indexes are
namespaced at the federation boundary.

**Consequences:** Ambiguous aliases are not resolved implicitly, and two
services with the same display name coexist without data loss. Existing SQLite
indexes receive the additive `modules.identity` migration on their next
writable open.

## ADR-12 — Kubernetes discovery is explicit and snapshot-based

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

## ADR-23 — MCP control in two phases with a graph enrichment layer

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

## ADR-24 — Use module terminology for structural project grouping

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

## ADR-25 — Separate indexed DTO materialization from graph rendering

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

## ADR-26 — Organize implementation modules by architectural ownership

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

## ADR-27 — Persist potential code flows separately from topology and runtime truth

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

## ADR-27a — Use AST symbol facts to constrain buildless call-graph fallbacks

**Status:** Accepted.

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

## ADR-27b — Preserve stronger enrichment facts and label topology reconciliation

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
inter-service edge.

**Consequences:** Enrichment imports cannot silently degrade a confirmed fact,
while explicit complete-manifest deletion remains available for stale facts in
that namespace. Flow consumers can distinguish a valid partial code path from
a fully reconciled topology path without discarding either one.

## ADR-28 — Render selected call-graph arcs with orthogonal port routes

**Status:** Accepted.

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

## ADR-29 — Ask CodeQL directly for bounded input-to-output reachability

**Status:** Accepted.

**Context:** Reconstructing every interprocedural flow by starting at each
 indexed input method and traversing Python-side call edges can miss a flow
 when dispatch resolution or an intermediate join is incomplete, even though
 CodeQL can answer the bounded reachability question directly.

**Decision:** Run a second bounded CodeQL query, scoped to the indexed input
 and output methods by relative path and start line. Persist exact reachability
 with medium confidence and dispatch-expanded reachability with low confidence.
Keep the existing Python traversal to provide intermediate steps and to retain
 its existing conservative behavior; add a two-endpoint direct flow only when
 that traversal did not already materialize the same input/output pair.

**Consequences:** CodeQL can recover connections that the Python-side join does
 not reconstruct, while the persisted evidence remains tied to indexed source
 methods and retains confidence. The query cost is bounded by the configured
 hop limit but still grows with the number of indexed input/output anchors and
 the reachable call graph. Reflection, runtime routing, and calls absent from
 the CodeQL database remain explicit blind spots.
