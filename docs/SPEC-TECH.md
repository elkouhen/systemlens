# Technical specification — systemlens (`systemlens`)

## Reading guide

| When changing… | Read |
|---|---|
| An extractor or a derived relation | [Data model](#data-model), [Indexing](#indexing), and [Extractors](#extractors) |
| A CLI, MCP, or web delivery adapter | [Architecture](#architecture) and [Export snapshot contract](#export-snapshot-contract) |
| HTML graph placement | [Graph layout algorithms](#graph-layout-algorithms) |
| SQLite schema or index compatibility | [Persistence and compatibility](#persistence-and-compatibility) |

This document records implementation constraints. The observable CLI and MCP
contract lives in [SPEC-FONC.md](SPEC-FONC.md).

User-facing vocabulary distinguishes architecture `modules` (hierarchical
containers) from Maven/Gradle `projects` (build units). Compatibility-facing
Python names, SQLite tables, JSON fields such as `module`, and the canonical
architecture path field `cluster_path` retain their historical spelling; the
delivery layer translates those technical names before presenting them.

## Architecture

### Pipeline at a glance

The pipeline is deliberately local:

```text
repository files → file hashes → Tree-sitter Java AST extractors
                 → endpoints/modules/properties → SQLite facts and relations
                 → CLI, MCP, graph and audit views
```

When explicitly enabled with `--kubernetes`, indexing also invokes the local
`kubectl` CLI once to list Deployments and StatefulSets. It aggregates the
declared requests and limits of regular containers (init containers are
excluded) and attaches a workload only when its Kubernetes name exactly matches
the indexed project name. This optional step can contact the current Kubernetes
API context; it is never enabled by default.

### Ownership boundaries

`scanner/` (a package; see `docs/ARCHITECTURE.md`) owns Java/Spring extraction.
`discovery/java/parser.py` provides cached Tree-sitter parsing and syntax
helpers. `domain/module_inventory.py` owns the
build-inventory facts shared with persistence and projections, without
depending on discovery. The `module_types/` compatibility package retains the
former import path.
`discovery/build/` discovers build units; `indexing/relations.py` derives typed
architecture relations from modules, endpoints and build dependencies.
`indexing/service.py` orchestrates the incremental transaction, while
`indexing/file_inventory.py` owns file eligibility and conservative full-rescan
promotion and `indexing/materializers.py` owns persisted contract projections.
`indexing/dto_inventory.py` materializes the conservative Java
DTO closure referenced by Kafka endpoints before that closure is persisted.
`storage/sqlite.py` owns SQLite persistence; the `store/` compatibility package
retains the former Python import.

Within `render/`, `graph_view_model.py` owns the projection from persisted
facts to the browser data model. `html_export.py` only serializes that model
and assembles the standalone document from the HTML template and ordered
browser assets. Rendering code must not be imported by indexing or discovery
code.

`delivery/cli.py`, `delivery/mcp.py`, and the standard-library local HTTP server
in `delivery/web.py` are thin delivery layers over the domain modules. The CLI export and
`systemlens web` both use `application/architecture_projection.py` to select the same
deployable, exportable service topology before choosing their output format.
The web command serves only an in-memory landing page and the existing
`/architecture` HTML
projection: it loads the persisted architecture snapshot and renders it for
that request. If no local index exists, its explicit POST action creates the
default configuration when needed and indexes the repository before rendering
the snapshot.
The web layer has no filesystem-serving route and writes SQLite only for this
explicit initial-index action; it does not persist credentials. It binds to loopback by
default; changing the host is an explicit user choice.

The separate `simpleweb` executable is a dependency-free static server for an
explicit report directory. It uses `SimpleHTTPRequestHandler` with that
directory as its only document root, has no application or write routes, and
binds to loopback by default. It is intentionally separate from `systemlens
web`, whose only route is the in-memory architecture projection.

### Future adapters

S3 support requires a separate conservative Java extractor for explicit AWS SDK
v1/v2 operations and configured bucket names, with dynamic bucket expressions
preserved as unresolved evidence. Kafka, MongoDB, S3, and Kubernetes runtime
signals require source-specific adapters and conservative evidence handling.

Future Kubernetes correlation must first use the current exact workload/service
name match. Its only fallback is a normalized token-sequence containment check
between a Deployment or StatefulSet name and an indexed service name. The
fallback succeeds only for one candidate; it records the matching strategy and
leaves zero or multiple candidates unresolved. It must never use an arbitrary
substring search or change persisted source topology.

## Data model

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

`GraphFact` is the separate enrichment layer for facts supplied by an AI or
user through MCP. It supports typed nodes and edges, origin, namespace, status,
confidence, pass/revision metadata, optional relative evidence and a note. The
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
CodeQL-resolved chain of Java methods. Its
first `CodeFlowStep` is an indexed HTTP or Kafka entry point; later steps are
HTTP calls, Kafka publications, or MongoDB reads/writes located within the
same Tree-sitter `method_declaration`. Steps retain relative evidence paths and
line ranges. Flow identity uses the module, relative path, qualified method,
and trigger semantics rather than line numbers, so ordinary line movement does
not replace the logical flow. The materializer runs during indexing and never during export or
query. It is linear in the indexed endpoint and Mongo-operation inventory plus
the traversed AST nodes for Java files that contain eligible endpoints. Maven
`target/` and Gradle `build/` output trees are excluded from the inventory, so
copied resources and generated classes cannot duplicate source facts. A
same-method relation remains `potential` with medium confidence because static
lexical order does not prove branch execution. With an explicitly supplied
CodeQL Java database, resolved `MethodCall` facts join persisted AST method
facts to form bounded (12-hop) interprocedural flows; `method_call` steps keep
the call-site line. By default, SystemLens creates this database in a temporary
directory with CodeQL's `--build-mode=none` source-only mode, then deletes it;
an explicit CLI database remains an override. When the CodeQL executable is
absent, it records no interprocedural paths and reports that limitation without
discarding AST-only flows. When CodeQL represents a lambda as a synthetic
anonymous callable, SystemLens attributes its call site to the narrowest
persisted Java method enclosing that line in the same file. Dynamic dispatch,
reflection, and runtime-only routing remain outside this deterministic layer.
For a virtual call, CodeQL's unique `exactVirtualMethod` target is retained at
medium confidence. If no unique target can be proven, its `viableCallable`
candidates are retained individually at low confidence; SystemLens does not
select an implementation on Spring bean metadata alone.

Kafka flow continuations join only concrete, statically resolved Kafka endpoint
identifiers. A continuation is not materialized when the producer has a later
external effect, because a linear composed flow would otherwise omit that
evidence. A composed flow can follow up to four Kafka producer-to-consumer
hops, using only original persisted message-entry flows as consumers and never
revisiting the same consumer flow. This makes cyclic topics finite while
retaining the complete ordered evidence for each bounded candidate.

The HTML graph model joins endpoint identifiers to the persisted
`integration_methods` projection. Microservice cards remain simple rectangles
and receive an export-time count of persisted internal flows for that service.
The selected-service widget renders only resolved input-to-output flow evidence,
not a standalone input/output port inventory. Rendering does not read source
files.

The SQLite store is standard-library-only: it does not load native vector
extensions or persist/query vector representations. The retained findings
search compatibility path uses deterministic lexical matching.

Schema version 28 adds the `integration_methods` table for AST method evidence
and input/output endpoint identifiers. Schema version 27 added the
`code_flows` table and its module/path indexes. The
migration is additive and occurs when the writable store opens, before the
index transaction. Stored steps are JSON projections of immutable domain
facts; source paths remain relative to the indexed root.

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

## Export snapshot contract

The read-only `--graph FILE` export path accepts the versioned
`systemlens-ai-graph-v1` manifest described in [AI-GRAPH.md](AI-GRAPH.md).
The adapter validates node IDs, kinds, relation endpoints, relative evidence
paths and ambiguity status before projecting safe claims onto the existing
HTML graph model. It does not modify source-derived tables. The explicit
`import-facts`/`import_graph_facts` path validates the same manifest and writes
only the separate enrichment layer. Confirmed and proposed claims become
visual relations; ambiguous and unresolved claims are quality issues. This
keeps AI-generated convention analysis separate from persisted source evidence
while still making its reasoning inspectable.

HTML and JSON graph exports only consume the loaded architecture snapshot.
They do not reopen OpenAPI documents or recursively parse Java DTO sources at
render time. The export can show indexed Kafka payload-type identities and
OpenAPI evidence paths; richer DTO/OpenAPI content requires an explicit future
indexed contract rather than a live source read. This keeps an export
reproducible when repository files change after `systemlens index`.

The HTML renderer keeps its graph-detail section inside the left toolbar and
hides it while empty. Selecting a resource or itinerary applies a panel state
that hides the active tab panel, reveals the detail section, and resets the outer
toolbar scroll position. The same state hides summary and inventory counters,
while graph actions, tabs, and the active graph-layout status remain present.
The quick search is a separate Explorer-only toolbar section. A second
Explorer-only context section groups camera actions on one row, display actions
on another, then summary and inventory counters. Switching to a domain tab
hides that complete context. The reset action is disabled while there is no selection.
Shared CSS tokens define the height and inner radius of segmented controls and
the border, surface, and outer radius of widget containers. Navigation tabs and
graph mode selectors consume those tokens and use the same selected-state rule;
Explorer disclosure sections consume the container tokens. Semantic pills do
not consume the rectangular control radius.
The `--ui-*` palette is the only source for widget chrome: title, body, muted
and accent text; panel, nested-widget and control surfaces; borders, focus and
selection. Light and dark themes override those tokens rather than individual
components. Selectors may retain local colours only when they visualize graph
data or a semantic status such as warning, confidence, relation type, or
resource type.
Code-flow cards and their nested timeline steps consume the same semantic
widget, control, text and muted-text tokens. Only the potential-flow marker may
retain a status accent; source paths must wrap instead of widening the panel.
Selecting a reconciled code flow records its persisted ID only in transient
graph state. Node and edge reducers use the exact reconciled path sets to raise
their size and contrast, while HTML card overlays add flow-specific highlight
and dimming classes. This presentation state does not infer or persist any new
architecture relation. Flow selection does not activate the graph detail
panel: the Flux panel and its existing DOM stay mounted with unchanged geometry;
selection only updates the card marker and action label in place.
The code-flow camera fit projects only the reconciled path nodes, derives a
zoom ratio from their viewport span and the fixed card envelope, then shifts
their graph-space centre to the usable viewport centre. The usable region
excludes the toolbar on the side or below it when enough room exists; the fit
does not change persisted positions or rerun a layout. Its ratio is bounded by
the current camera ratio so flow selection may zoom out but never zoom in, and
the resize handler reapplies this flow fit instead of the global graph fit.
Clearing the selection reverses that state; selecting a navigation tab clears
details before displaying its ordinary panel. Path parsing
filters same-name candidates by the grammar before accepting an itinerary
endpoint: only a microservice can be first or last, while intermediate stops
can be microservices or Kafka topics. Direct resource search continues to
report multiple same-name resources as ambiguous.

The legacy `findings` table and model are retained only to open existing index
databases. `Store.clear_findings_once("ast_only_analysis_v1")` removes stale
external-analyzer data on the first AST-only index run.

## Indexing

`index_repo` performs these steps:

1. Clear parser and discovery caches for long-lived MCP processes.
2. Discover modules unless disabled.
3. Build the eligible-file hash inventory, respecting include/exclude rules,
   test-source exclusion and nested-build boundaries.
4. Compare hashes with the stored inventory and purge removed files. A changed
   or deleted Spring configuration file or Maven/Gradle descriptor promotes the
   delta to a full endpoint refresh because these files are dependencies of
   otherwise unchanged Java facts.
5. Force a full refresh when extractor/configuration/strategy signatures differ.
6. Run AST extractors for the changed files and atomically replace their
   endpoints.
7. Persist hashes, modules, dependencies and derived relations.

Steps 1–7 execute inside `Store.transaction()`. The writable connection uses
`BEGIN IMMEDIATE`, then commits the complete snapshot only after relation
materialization succeeds; any exception rolls back files, endpoints, modules,
dependencies, relations and index signatures together. Schema creation and
compatible migrations happen when a writable store opens, before an index
transaction. Read-only stores open SQLite in `mode=ro` and never migrate or
commit. A concurrent reader sees the last committed snapshot until the writer
commits the next one.

AST endpoint analysis uses no subprocess. For interprocedural flows, the local
CodeQL executable is used by default to create a temporary source-only Java
database, query it, and decode the result as CSV; `--codeql-database` reuses a
database supplied by the caller. The temporary query pack is locked through the
local CodeQL package manager before execution. No database path is persisted.
An absent CodeQL executable is reported and keeps AST-only results; a failing
available executable leaves the whole previous successful snapshot intact.

## Extractors

`infer_framework_endpoints` walks Java declarations, annotations and method
invocations to discover Spring MVC/WebFlux routes, Feign clients, RestTemplate,
WebClient, Spring Data REST and gateway routes. It resolves literals, known
Spring property expressions, and unique never-reassigned local string base
URLs conservatively. Multi-document Spring YAML is read document by document;
base-document values take precedence where no active-profile selection exists.
YAML parse failures, including unrendered Helm Go-template expressions, leave
that file without Spring-property facts and never abort the repository index.

REST graph construction first resolves an explicit target identity from an HTTP
host, `lb://` URI, configured client domain, or an opt-in Strategy1 convention.
For a URL expression that concatenates a local `@Value`-annotated field, or a
unique never-reassigned local string base URL, and a path, the extractor
resolves the value and retains its HTTP host as endpoint evidence while
persisting only the normalized route as the endpoint topic.
The normalized alias must match exactly one indexed service; prefix, suffix and
substring matching are not used. Route compatibility is evaluated only within
that service. A targetless or ambiguous call remains an endpoint fact and is
reported as unresolved rather than creating an internal edge.

`infer_kafka_endpoints` recognises Spring Kafka listeners and send sites,
KafkaTemplate/ProducerRecord usage and Spring Cloud Stream StreamBridge calls.
It preserves dynamic topic expressions and derives a payload type only from an
explicit listener parameter or client generic signature.

With `--topic-strategy strategy1`, every method whose name starts with
`envoyerMessageKafka` is an additional producer convention, including
`envoyerMessageKafkaRequest(topic, payload)` and
`envoyerMessageKafkaReply(topic, payload)`. A first argument shaped as
`kafkaProperties.getTopics().getXxx()` resolves to the normalized Strategy1
topic name; other values use the conservative topic resolver. The second
argument is used to derive the payload type from its method parameter, local
variable declaration or enclosing class field.

Strategy1 also enables the `getXxxServiceUrl()` REST target-name convention;
without it, SystemLens uses only an explicit URL or `lb://` service target.

The graph export exposes only the indexed Java payload-type identities linked
to Kafka endpoints. It does not resolve Java DTO fields or enums from source
roots at render time; recursive DTO inspection requires a future persisted
schema contract.

The graph export keeps an exact-name index of its visual nodes. Its client-side
itinerary algorithm performs directed breadth-first searches for each pair of
user-supplied stops and concatenates those shortest segments. It uses only
Kafka graph relations, independently of temporary display filters, so an
itinerary always alternates between microservices and Kafka topics. Kafka
links carry relation-specific published or consumed Java message types; the
selected-path detail uses only these adjacent links and explicitly reports
missing type information.

Display filtering first maps persisted node and edge vocabulary to visual
categories. `kafka_topic` and `message_channel` are messaging nodes;
`mongodb_collection` and `data_schema` are data nodes. Edge classification
uses the native kind, its relation label and endpoint kinds so that native
`rest`/`kafka`/`mongodb` links and enriched `mcp_*` links respond to the same
HTTP, Kafka and data-access selectors. Unrecognized kinds remain conservative
and independently selectable as `Other`; they are never silently discarded.

In symbol rendering, label decluttering is recomputed from projected viewport
coordinates on each coalesced render. Candidates are ordered deterministically
by selection/hover state, visible degree, semantic node category, complexity,
then name. A greedy screen-space collision pass accepts labels up to a limit
derived from viewport area and inverse camera ratio, rejects labels that would
cover another accepted label or symbol, and chooses the free side of the
symbol. Zooming in therefore increases the labeled share of currently visible
nodes without changing graph positions or camera state. Selected and hovered
labels bypass the density and collision constraints so their names remain
visible.
For `n` visible nodes, the current conservative collision pass is `O(n²)` and
uses `O(n)` temporary rectangles; exported architecture reports are expected
to remain within interactive inventory sizes.

Symbol styling reuses each node's `--card-accent`: a light accent-tinted
gradient and a high-contrast accent border mirror the card surface/border
rules, while the dark theme mixes the same accent into the slate surface.
Selection and hover add scale and glow without changing the 30×30 collision
envelope. The microservice hexagon uses nested, pixel-aligned polygons for its
border and surface; this avoids clipping a rectangular CSS border and keeps the
six edges visually even at the compact symbol size.

The manifest extractors add explicitly declared Kafka facts from Markdown and
JSON. Strategy1 is separate and opt-in because it embeds repository-specific
naming conventions.

`systemlens analyze indexing-issues --json` exposes unresolved facts as a structured
remediation review payload. Each endpoint-backed issue has a stable code,
severity, service, framework, topic/API, extracted message type and its source
path, line range and snippet. The command does not infer or apply a heuristic;
its evidence is intended for a human or an AI to assess a conservative rule.

## Graph layout algorithms

### Coordinate system and rendering

The HTML renderer keeps graph coordinates as the source of truth for layout.
Sigma's canvas and the HTML card/module overlays share the same full-window
workspace rectangle behind the floating navigation and details panel. Overlay positions are obtained from Sigma's public
`graphToViewport` conversion using the raw graph coordinates; renderer
internal matrices and full-window canvas coordinates are not mixed with the
workspace-local overlay coordinates.

The browser controller is maintained as ordered source modules under
`src/systemlens/render/assets/graph/`: core setup, graph rebuilding and camera
events, controls, layouts, details, path exploration, and bootstrap wiring.
The stylesheet follows the same ordered-module convention: base graph,
widgets, presentation theme, then the shared visual charter. The export
assembler concatenates both CSS and JavaScript modules into the standalone
document.
Mutable selection, view, layout, and camera state is held in one
`graphState` object. Overlay refreshes go through one animation-frame
scheduler (`requestGraphRender`) so canvas and HTML overlays observe one
coalesced render cycle.

Architecture-module titles are interactive overlay controls. Module selection
is tracked independently from node selection in `graphState`; it
uses the visible canonical `cluster_path` values to derive a hierarchy of path
prefixes. A module descriptor contains its canonical key, parent path, direct
child paths, and the visible node identifiers whose module path is an exact
match. This exact-match rule keeps direct membership distinct from descendant
membership and prevents filtered or guessed nodes from entering the details
list. Selecting a listed resource returns to the ordinary node-detail flow;
following a resource's module action applies the deterministic module layout
before selecting the corresponding descriptor.

Persisted `runtime_namespaces` and `fact_namespaces` remain available in the
embedded snapshot for backward compatibility and evidence processing. The
details renderer does not expose them as architectural metadata and does not
prefix Kubernetes workload names with their runtime namespace; module
navigation is sourced exclusively from the canonical `cluster_path` value.

### Camera interactions

The shared card size remains stable during navigation. Camera fitting starts
from Sigma's native complete overview. In the plain graph, `All nodes` uses
that state unchanged. In compound module and layer views, it measures the
projected card and sibling-module envelopes and applies the minimum
collision-free zoom. The default `Readable distance` mode also zooms in by at
least 1.6x. The historical 4x spacing guard remains limited to the plain graph;
compound views are not capped because a narrow viewport may require greater
separation. Graphs with at most 12 nodes in the plain graph instead derive an
overview ratio from their projected center span and the
available viewport after subtracting the fixed card width and height. This
keeps the complete card envelopes visible and relies on the following
collision pass for separation.
For each overlapping pair, the required factor is the smaller of its
horizontal and vertical separation factors: reaching the card clearance on
either axis is sufficient. Compound views use the maximum factor across card
pairs and module-envelope pairs. For module envelopes, the required factor is
derived from the gap between projected min/max center intervals on both axes,
including fixed card size, module header/padding and a positive sibling gap.
The factors are collected through the existing spatial grid and sorted, adding
O(p log p) work for p nearby card pairs plus O(m²) comparisons for m rendered
modules. Peripheral nodes may therefore leave the viewport. Panning and
zooming in never move nodes; zooming out is clamped to the collision-free ratio
in compound views.

The renderer keeps one graph and HTML overlay implementation for both node
rendering modes. Card mode uses the fixed 110×70 envelope. Symbol mode applies
a 30×30 overlay marker and reveals its overflowing adjacent name only on hover;
its fit calculation uses a 34×34 marker envelope, so label length does not force
the camera away from the graph. Switching modes redraws overlays and the Sigma
node reducer without rebuilding or re-parsing the persisted graph snapshot. It
does not invoke camera fitting or collision placement, so the camera state and
graph coordinates remain unchanged. Compound module and layer fits therefore
reserve the larger 110×70 card envelope even when Symbols is active; switching
back to Cards cannot introduce an overlap.

Relations remain rendered by Sigma independently of the HTML card overlays.
In symbol mode, Sigma's underlying node marker is reduced beneath the HTML
shape so that the card-oriented canvas glyph does not remain visible.
After the combined ForceAtlas2/Noverlap layout and readable camera fit, the
plain graph alone resolves residual collisions in projected screen space. The
solver uses 110×70 card envelopes or 30×30 symbol envelopes, converts the
adjusted centers back to graph coordinates, and leaves compound layouts
untouched.
Camera updates during pan and zoom are coalesced to the next animation frame.
Fit operations reset Sigma's normalized camera state synchronously before
calculating the selected mode. This makes overlapping fit requests idempotent
and prevents zero-duration camera animations from racing on repeated clicks.
The details section participates in the navigation panel's vertical flow but
does not participate in the Sigma workspace rectangle. The HTML layer, module,
and node overlays retain the same full-height coordinate surface as the canvas.
Opening or resizing details only changes the navigation panel's internal scroll
extent and therefore cannot introduce a clipping seam or reset the camera.
The desktop workspace starts at the window's left edge. The toolbar overlays
its upper-left portion, while nodes and relations remain rendered in the usable
space below the toolbar.
The Sigma canvas and the HTML card/module overlays are therefore recomputed
from one camera state per frame, preventing partially rebuilt containers from
appearing while the user drags the module view.

Wheel zoom is handled once for both the Sigma canvas and the HTML overlays;
the native Sigma wheel handler is disabled so hovering a card cannot change
the zoom behavior. Each wheel event applies a bounded exponential camera-ratio
step and respects the collision-free maximum ratio in compound views.

### Layout engines and architecture-module placement

The ELK layer layout loads independently from the ForceAtlas2 and Noverlap
modules used by the graph layouts, so unrelated dynamic imports cannot keep
the layer view in a pending state.

Force-based placement uses Sigma's node radius to preserve the graph structure;
it does not mutate positions after the camera fit to repair HTML-card overlap.

The module packer places microservices in a first sub-layer and
resources in a second sub-layer on separated grids, then
packs module rectangles with positive margins that include the complete
projected card/title envelope, not only the node-grid dimensions. Its graph-space
gaps are expressed in the same graph-coordinate scale as the rest of the
layout, while remaining large enough for the shared 110×70 card envelope. The
sub-layer assignment remains available as layout state and export diagnostics,
but the overlay does not render sub-layer titles.

Each layout starts with the shared camera-fit operation in the selected mode.
The `All nodes` and `Readable distance` actions select and immediately apply
their mode; the selection is reapplied after a viewport resize. This prevents
view switches from retaining a stale camera scale while allowing the readable
mode to favor card separation over a complete overview. In module and layer
views, cards and sibling module rectangles remain disjoint; parent rectangles
may overlap only by containing their children. Their fixed screen-space
dimensions are preserved during navigation.

Node positions remain in graph coordinates. Container geometry is rebuilt in
viewport coordinates from projected node centers after every camera update.

The module view uses this deterministic packing as its source of truth; it does
not wait for a compound force layout that could block the browser. When project
or other parent groups are enabled, their bounds are the union of the already
projected child-module bounds plus title/padding margins; node-grid gaps are
calibrated with the shared 110×70 card envelope and a dedicated vertical
separation between the two sub-layers. This explicit hierarchy prevents a
parent from being smaller than a nested module after zooming. Project groups
carry their owning module and full module path. The historical
`project_namespace` and `project_namespace_path` fields remain read-compatible
aliases for `cluster_path`; they do not represent Kubernetes namespaces.

Structural project groups contain only
their owning projects; resource nodes resolve their module
from incoming producer edges before consulting resource metadata; this keeps
topics and collections with their producing microservice.

When several services write the same resource, ownership is selected by the
lowest service layer in the canonical order; ties are resolved by service name
for deterministic exports.

### Layer placement

The layered view reuses this packer with an additional grouping key: modules
are first grouped by the canonical internal software-layer order (`api`,
`application`, `orchestration`, `infrastructure`, `domain`, `persistence`),
producing top-to-bottom layer bands. External microservices use the dedicated
`external` layer at the bottom and are not part of the internal dependency
order.

The same canonical resolver is used for layer placement, module boxes, and
band bounds, so a module cannot be placed in a band different from its
nodes. Each module uses the same two-part grid as the module view:
microservices first, then resources.

Layer-band bounds group every rendered resource with
`layeredLayerForNode`; they do not reassign non-microservice resources to the
visually nearest service layer. The module envelope and its owning band thus
consume exactly the same layer identity.

ELK may provide the initial compound layout, but the deterministic layer-aware
pack is the final collision guard and remains valid when ELK fails. The layered packer
checks each module envelope after placement. Modules that exceed the vertical
safety envelope are widened by adding columns, then the row width and all
positions are recomputed. This trades height for diagram width to preserve
layer separation.

Layer bands reserve a left graph-space gutter for their titles, so the title
overlay cannot cover the first architecture module.

### Browser verification

The layer-band calculation is implemented in the embedded `layer_geometry.js` module
and is covered by renderer geometry unit tests for ordering, containment, and
shared bounds. Browser integration tests additionally capture a PNG and JSON
geometry snapshot after each significant browser action (load, view change,
filter, selection, zoom, pan, and resize); the PNG pixels are inspected for
actual rendered content in the graph region, not only the DOM. The tests also
verify node-center visibility, fixed card dimensions, and pan/zoom
synchronization. Snapshots are written under `output/playwright/` for visual
inspection when a regression occurs.
The browser launcher tries Playwright Chromium first, then Firefox and WebKit;
an integration test is skipped only when all three engines fail to launch.

## Persistence and compatibility

SQLite schema migration is additive where possible. `files` stores hash state,
`endpoints` stores source facts, and normalized tables store modules,
dependencies and relations. Each module has a collision-safe identity used by
endpoints and relations; its artifact/project name remains a display alias.
The database filename remains `findings.db` for backward compatibility; new
AST-only behavior must not infer that it contains security findings.

SystemLens stores this database under `.systemlens/`. It intentionally does not
load the former `.cccr/`, `.archlens/`, or `.codeatlas/` state directory: the
product rename requires a fresh `systemlens init` and `systemlens index` so the
configuration and index namespace remain unambiguous.

The endpoint-inventory signature in `meta` is bumped whenever extractor
behaviour changes. This forces a complete refresh before new facts are served.

## Verification

Unit tests use fixture repositories with real Java source and assert source
locations, roles, dynamic flags and derived relations. Static checks are Ruff
and mypy. The project does not require an external scanner in development or
at runtime.
