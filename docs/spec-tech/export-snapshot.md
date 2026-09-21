# Export snapshot contract

Parent: [Technical specification](../SPEC-TECH.md).


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
Compact code-flow cards consume the same semantic widget, control, text and
muted-text tokens. Only the potential-flow marker may retain a status accent;
source paths must wrap instead of widening the panel.
Selecting a reconciled code flow records its persisted ID only in transient
graph state. Node and edge reducers, HTML-card overlays, and port overlays use
the exact reconciled path sets to hide every unrelated node and edge; selected
nodes retain flow-specific highlighting. The selected flow graph layout uses
its persisted directed service edges to compute graph levels and vertical
sibling offsets; cyclic or edge-less flows fall back to the deterministic
sequence order. This presentation state does not infer or persist any new
architecture relation. A selected flow graph projects only
the microservices on its reconciled path; topic names remain in the selected
ports and tooltips but topic nodes are omitted. Its nodes use a compact
horizontal sequence when no branches are available; otherwise its tree/DAG
levels remain readable. Service-to-service
interaction-graph edges
use orthogonal straight-segment routes in the SVG overlay between their actual
output and input port anchors. ELK.js supplies the positioned graph and the
browser libavoid WASM router receives fixed node rectangles plus explicit port
sides, then returns absolute source, bend, and target points. The router
includes every visible microservice card as an obstacle, not only the source
and target cards of the routed edges; route validation excludes the source and
target cards themselves. It nudges parallel routes. OUT arcs always leave
through the right edge of their port anchor and
IN arcs always arrive at the left edge of their port anchor, regardless of the
relative position of the endpoint cards. Route validation also requires the
first segment to leave the OUT side and the final segment to approach the IN
side; a route that immediately turns through its source or target card is
rejected. Returned
Libavoid points are never clamped after routing: routes that leave the safe
viewport or intersect an expanded obstacle are rejected and use the existing
geometry fallback. A returned Libavoid route whose Manhattan length is
disproportionate to the direct distance is also rejected, preventing
full-viewport U-shaped detours; the bounded orthogonal fallback is preferred
when it is short and obstacle-free. Arc labels use the same protocol-specific
color as their associated paths, including the theme-specific dark-mode colors,
and are subdued until hover or analysis selection. A two-point route remains
direct when the segment is clear; a rectangular detour is created
only when an obstacle blocks that direct segment. In the focused
flow graph view, endpoint arcs are represented by the
projected service edge and are not drawn a second time in the port overlay.
While a code flow is selected, port anchors are interactive analysis controls:
clicking an input or output endpoint, or directly on an arc, records a
transient endpoint focus and highlights every associated service arc, local
port relation, and arc label; clicking the same port or arc clears the focus,
while selecting another port or arc moves it.
The analysis banner derives a compact timeline from persisted step endpoint
IDs and the exported port inventory. It displays port labels, protocol,
resource/topic names, and message types. Non-integration method-call steps are
omitted from the compact timeline; full endpoint evidence remains in the DOM
tooltip and the persisted flow details. It must not infer a message
type when the indexed port does not provide one.
The port gesture is isolated from the card drag and node-selection handlers, so
analysis never changes the graph view or camera and does not persist data.
Every visible SVG arc has a transparent, wider hit-area path layered above it;
the hit area receives selection and tooltip events without changing the visual
stroke width or routing geometry.
The export indexes displayed nodes and visual edges by
persisted endpoint ID once; reconciliation requires exactly one endpoint-backed
candidate and never selects a relation from a route label, topic name, or first
matching graph edge. A selected local input-to-output relation is highlighted
in the card overlay and is reconciled independently from Sigma edges. During Kafka-flow reconciliation, each publication starts
from the service reached by the preceding step; a composed
`topic → consumer → topic` chain therefore preserves its concrete topic-read
and topic-write edges across microservices. Flow selection does not activate the graph detail
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
details before displaying its ordinary panel. URL fragments are ignored by the
Explorer and never restore or persist path selections, so they cannot alter the
rendering algorithm. Path parsing filters same-name candidates by the grammar before accepting an itinerary
endpoint: only a microservice can be first or last, while intermediate stops
can be microservices or Kafka topics. Direct resource search continues to
report multiple same-name resources as ambiguous.

The legacy `findings` table and model are retained only to open existing index
databases. `Store.clear_findings_once("ast_only_analysis_v1")` removes stale
external-analyzer data on the first AST-only index run.
