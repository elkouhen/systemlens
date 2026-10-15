# Export snapshot contract

Parent: [Technical specification](../SPEC-TECH.md).

## Enrichment and persisted data

The read-only `--graph FILE` export path accepts the versioned
`systemlens-ai-graph-v1` manifest described in
[AI graph manifest](../spec-fonc/ai-graph-manifest.md).

The adapter validates node IDs, kinds, relation endpoints, relative evidence
paths and ambiguity status before projecting safe claims onto the existing
HTML graph model. It does not modify source-derived tables. The explicit
`import-facts`/`import_graph_facts` path validates the same manifest and writes
only the separate enrichment layer. Confirmed and proposed claims become
visual relations; ambiguous and unresolved claims are quality issues. This
keeps AI-generated convention analysis separate from persisted source evidence
while still making its reasoning inspectable.

`export facts FILE --namespace NAME` serializes one persisted enrichment
namespace back to the same manifest format. It preserves the stored fact ID in
the optional `storage_id` field, so imported facts remain stable across an
export and re-import. Edges whose endpoints are source-derived receive
reference nodes in the manifest; this keeps the edge importable without
claiming that the reference node was extracted from the source. The command
does not export source-derived facts as enrichment facts.

`flow_data_access` facts in the enrichment namespace stay separate from
topology nodes. The HTML exporter joins them to an indexed flow by ID and
includes the resulting accesses in the flow search projection. It drops stale
flow references from the projection without deleting the persisted fact.

The HTML snapshot stores access sites under their source internal flow ID in
`flow_data_accesses`. It stores only reachable access-flow IDs under each
selectable root in `flow_data_access_flow_ids_by_root`. Browser search and
call-tree badges resolve these references when needed, so shared downstream
accesses are serialized once rather than repeated for every root.

`flow_data_access_call_paths` stores the ordered source-backed method-call
witnesses for each access flow that has them. The browser compares the complete
access path with each occurrence path before treating an association as
route-supported. An older snapshot without call-path witnesses keeps the
association uncertain unless the exact flow ID matches.
`flow_data_access_first_calls` remains in the export for consumers of the
previous snapshot shape; the browser does not use it for association.

An internal flow that contains an external call remains selectable even when
its final step accesses data. Pure data-access flows remain supporting evidence.
For each selected root, sibling accesses remain reachable even when their
method-call routes differ. The browser marks handler-only associations as
uncertain instead of discarding them.

The exporter scans each root's reachable flows and serializes each access flow
once. It does not reopen source files.

With `--no-call-graph-annotations`, HTML serialization omits this projection,
consistency diagnostics, and data-access steps from selectable and supporting
flows. It still builds call topology from the complete persisted flows and
keeps their IDs stable. The exporter skips access aggregation and diagnostic
projection in this mode; indexing and JSON output keep their full facts.

The call-graphs-only browser bootstrap selects the first available flow after
its event handlers are registered. It skips the architecture layout when this
focused view opens. If no selectable flow exists, the flow list remains empty.

The HTML flow picker first removes flows reached from another indexed root.
It then removes root graphs whose services and exact endpoint-backed arcs are
proper subsets of another root graph. The persisted flows are unchanged;
omitted roots remain in `supporting_code_flows`. Equal graphs retain their
separate flow entries; identical root-specific shapes share a stored graph.

The containment lookup uses the rarest indexed arc, or a service for graphs
without arcs, to limit pairwise comparisons. A larger graph can share topology
with an omitted root without
sharing its causal trigger; diagnostics remain tied to selected roots.

HTML and JSON graph exports only consume the loaded architecture snapshot.
They do not reopen OpenAPI documents or recursively parse Java DTO sources at
render time. The export can show indexed Kafka payload-type identities and
OpenAPI evidence paths; richer DTO/OpenAPI content requires an explicit future
indexed contract rather than a live source read. This keeps an export
reproducible when repository files change after `systemlens index`.

Persisted JPA entity declarations are serialized on their owning service node
as qualified names and root-relative source locations. The HTML projection also
creates one `jpa_entity` node per declaration and a `maps` relation from its
owning service; this is a mapping relation, not a database access edge.
When `.systemlens/flow-descriptions.json` is present, the HTML export loads its
descriptions by flow ID as a separate enrichment layer. Missing descriptions do
not alter the persisted flow facts or prevent the export.

## Browser state and graph projection

The resource-catalogue preview derives direct neighbors from persisted
`links`. Non-graph entries use only indexed ownership, OpenAPI route lists,
AsyncAPI channel addresses, and Kafka DTO topic names to offer navigation
targets. It deduplicates targets and prefers topology links over enrichment
links for the same target.
Selection history and filter resets stay in browser state; neither changes
the exported snapshot.

The catalogue merges DTO descriptions and Kafka DTO definitions by their
indexed identity, so one entry retains both fields and topic associations.
The preview reads entity and DTO attributes from indexed descriptions or node
fields. Collection fields come from its indexed persistence classes and keep
their class grouping. No source files are parsed in the browser.

DTO attribute links resolve only `dto_references` IDs already present in the
catalogue. The preview uses the same browser selection history as related
resource links, including when a nested DTO references another nested DTO.
Fields without a resolved, non-self target retain plain type labels.

The HTML renderer keeps its graph-detail section inside the left toolbar and hides
it while empty. Selecting a resource or itinerary applies a panel state that hides
the active tab panel, reveals the detail section, and resets the outer toolbar
scroll position. The same state hides summary and inventory counters, while graph
actions, tabs, and the active graph-layout status remain present. The quick search
is a separate Explorer-only toolbar section. A second Explorer-only context
section groups camera actions on one row, display actions on another, then summary
and inventory counters. Switching to a domain tab hides that complete context.

The reset action is disabled while there is no selection. Shared CSS tokens define
the height and inner radius of segmented controls and the border, surface, and
outer radius of widget containers. Navigation tabs and graph mode selectors
consume those tokens and use the same selected-state rule; Explorer disclosure
sections consume the container tokens. Semantic pills do not consume the
rectangular control radius. The `--ui-*` palette is the only source for widget
chrome: title, body, muted and accent text; panel, nested-widget and control
surfaces; borders, focus and selection.

Light and dark themes override those tokens rather than individual components.
Selectors may retain local colours only when they visualize graph data or a
semantic status such as warning, confidence, relation type, or resource type.
Compact code-flow cards consume the same semantic widget, control, text and
muted-text tokens. Only the potential-flow marker may retain a status accent;
source paths must wrap instead of widening the panel. Selecting a reconciled code
flow records its persisted ID only in transient graph state.

Node and edge reducers, HTML-card overlays, and port overlays use the exact
endpoint and service sets derived from the flow evidence; selected nodes retain
flow-specific highlighting. The browser keeps an explicit transient rendering mode
for the architecture projection, an empty Flux de code tab, and the selected
call-graph projection. The selected flow ID alone MUST NOT determine which
projection is rebuilt. The flow graph builds directed candidate arcs from
persisted flow OUT ports, starts at trigger flows with no incoming flow arc, and
follows those flows breadth-first.

It adds reachable microservice output-to-input arcs and continues with the flow
triggered by each target IN port, with expanded OUT-port and endpoint-pair guards
for cycles and duplicates. Fan-in and fan-out remain visible, as do reachable
modules without a matching arc. The graph records only HTTP, message, and cron
trigger events under their owning microservices. Topic names remain in the
selected ports and tooltips but topic nodes are omitted. If the candidate graph is
cyclic and has no zero-indegree module, all flow modules are used as deterministic
roots while preserving arc direction.

The snapshot also exposes the breadth-first traversal levels and call tree that
assign arc numbers before they are copied to the rendered call graph.
Service-to-service interaction-graph edges use the complete projected edge set
during microservice placement. The SVG overlay then draws exactly one straight
segment between the actual output and input port anchors. `OUT` arcs leave through
the right edge of their port anchor and `IN` arcs arrive at the left edge of their
port anchor. The focused flow graph does not add obstacle-routing bends or curves;
card separation is handled by the placement pass.

Arc labels use the same protocol-specific color as their associated paths,
including the theme-specific dark-mode colors, and are subdued until hover or
analysis selection. In the focused flow graph view, endpoint arcs are represented
by the projected service edge and are not drawn a second time in the port overlay.

While a code flow is selected, port anchors are interactive analysis controls:
clicking an input or output endpoint, or directly on an arc, records a transient
endpoint focus and highlights every associated service arc, local port relation,
and arc label; clicking the same port or arc clears the focus, while selecting
another port or arc moves it. When navigation selects an arc, consecutive arcs
with the same source service share the selected visibility, emphasis, and
foreground ordering. The navigation cursor remains anchored to one arc so `N` and
`P` continue to move through individual projected edges.

The call tree uses parameter types stored with integration methods and projected
onto input ports. Flow-only occurrences can use the matching persisted method
declaration. Missing signatures remain absent; export does not parse Java source.

For a Strategy1 Kafka input, the graph view model adds `call_tree_signature` to
the port only when the persisted call graph has one direct `consume` callee with
declared parameter types. The selected call tree uses this display signature;
other port details continue to use the listener method.

The analysis banner derives a compact timeline from persisted step endpoint IDs
and the exported port inventory. It displays port labels, protocol, resource/topic
names, and message types. Non-integration method-call steps are omitted from the
compact timeline; full endpoint evidence remains in the DOM tooltip and the
persisted flow details. It must not infer a message type when the indexed port
does not provide one.

The export also includes `all_flows_call_graph`, built by
scanning every persisted flow before adding module nodes, valid output-to-input
service arcs, and trigger events.

## Indexes and reconciliation

The exporter builds shared endpoint, flow and edge indexes once and reuses them
for each flow graph. The shared index also precomputes flow transitions grouped by
output endpoint, default roots, and trigger metadata. Each root still gets its own
traversal because its reachable graph, edge discovery order, and call tree can
differ; the optimization removes repeated global scans without collapsing distinct
persisted flow graphs. Each selected graph carries causal transitions rooted at
its selected flow. The browser builds local occurrences from those transitions,
stopping at cycles, depth limits, and targets without an indexed consumer flow.

It does not rebuild the call tree from service-level topology when causal evidence
is absent, so unrelated arcs cannot become branches of the selected flow. The port
gesture is isolated from the card drag and node-selection handlers, so analysis
never changes the graph view or camera and does not persist data. Every visible
SVG arc has a transparent, wider hit-area path layered above it; the hit area
receives selection and tooltip events without changing the visual stroke width or
routing geometry. The Architecture overlay draws non-Kafka hit areas first and
Kafka hit areas last.

This stable, linear-time partition keeps topic inspection reachable when several
relations share a path; it needs linear temporary space per render. The export
indexes displayed nodes and visual edges by persisted endpoint ID once;
reconciliation requires exactly one endpoint-backed candidate and never selects a
relation from a route label, topic name, or first matching graph edge. A selected
local input-to-output relation is highlighted in the card overlay and is
reconciled independently from Sigma edges.

During Kafka-flow reconciliation, each publication starts from the service reached
by the preceding step; a composed `topic → consumer → topic` chain therefore
preserves its concrete topic-read and topic-write edges across microservices. Flow
selection does not activate the graph detail panel: the Flux panel and its
existing DOM stay mounted with unchanged geometry; selection only updates the card
marker and action label in place. The code-flow camera fit projects only the
reconciled path nodes, derives a zoom ratio from their viewport span and the fixed
card envelope, then shifts their graph-space centre to the usable viewport centre.

The usable region excludes the toolbar on the side or below it when enough room
exists; the fit does not change persisted positions or rerun a layout. Its ratio
is bounded by the current camera ratio so flow selection may zoom out but never
zoom in, and the resize handler reapplies this flow fit instead of the global
graph fit. Clearing the selection reverses that state; selecting a navigation tab
clears details before displaying its ordinary panel. URL fragments are ignored by
the Explorer and never restore or persist path selections, so they cannot alter
the rendering algorithm.

Path parsing filters same-name candidates by the grammar before accepting an
itinerary endpoint: only a microservice can be first or last, while intermediate
stops can be microservices or Kafka topics. Direct resource search continues to
report multiple same-name resources as ambiguous.

The legacy `findings` table and model are retained only to open existing index
databases. `Store.clear_findings_once("ast_only_analysis_v1")` removes stale
external-analyzer data on the first AST-only index run.
