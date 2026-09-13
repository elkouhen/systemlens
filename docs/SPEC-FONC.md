# Functional specification — systemlens (`systemlens`)

`systemlens` builds a local architecture inventory from Java/Spring source ASTs
and, when the local CodeQL CLI is available, a temporary local Java call graph.
The inventory commands operate on the current repository unless an explicit
workspace root is accepted. Source code never leaves the local machine.

## Reading guide

| If you are… | Start with |
|---|---|
| Setting up or operating SystemLens | [Configuration](#configuration) and [CLI](#cli) |
| Changing extraction behaviour | [Extraction contract](#extraction-contract) and [Boundaries](#boundaries) |
| Changing the HTML export | [HTML export behaviour](#html-export-behaviour), then its rendering rules |
| Integrating an agent | [MCP](#mcp) |

The keywords **MUST** and **MUST NOT** identify compatibility requirements.

## Configuration

`systemlens init` creates `.systemlens/config.yml`:

```yaml
include: ["**/*"]
exclude: [".git/**", ".venv/**", "node_modules/**", ".systemlens/**"]
min_severity: INFO
root_path: .
analysis:
  topic_strategy: default
  codeql: true
  codeql_max_hops: 12
  codeql_max_paths: 10000
  disabled_extractors: []
```

This is a breaking rename from `cccr`, `archlens`, and `codeatlas`: SystemLens
does not read an existing `.cccr/`, `.archlens/`, or `.codeatlas/` directory.
Run `systemlens init` and
`systemlens index` to create a new local inventory in `.systemlens/`.

`include` and `exclude` control source inventory. Maven/Gradle test source
sets (`src/test`, `src/componentTest`, and names ending in `Test`) are always
excluded. The `min_severity` setting remains accepted for database compatibility
but does not alter AST endpoint extraction.
`analysis` is the versionable source of truth for the topic convention, CodeQL
use, and disabled extractors. `root_path` is local export configuration only:
it resolves relative source evidence into VS Code links and is never persisted
in the architecture snapshot.

## CLI

| Command | Behaviour |
|---|---|
| `systemlens init` | Creates `.systemlens/config.yml`; it never overwrites an existing file. |
| `systemlens doctor [--json]` | Read-only check of configuration, local AST readiness and index state. |
| `systemlens version` | Prints the installed `systemlens` package version. |
| `systemlens index [MANIFEST]... [--full] [--topic-strategy default\|strategy1] [--manifest FILE]... [--kubernetes] [--kubernetes-namespace NAME] [--codeql-database DIR] [--disable TYPE]...` | Incrementally extracts and persists architecture facts. When the local CodeQL CLI is available, it creates a temporary source-only Java database and extends code flows across resolved method calls. `--codeql-database` reuses an already-built Java database instead. `--kubernetes` queries the active `kubectl` context for Deployments and StatefulSets; `--kubernetes-namespace` restricts it to one runtime namespace. `--disable` can independently disable the `properties`, `module-architecture`, or `module-tree-sitter` extractor and may be repeated. |
| `systemlens import-facts FILE [--namespace NAME] [--complete]` | Validates and transactionally upserts an AI fact manifest into the separate enrichment layer. `--complete` removes stale facts only within the selected namespace. |
| `systemlens microservices`, `topics`, `apis`, `dtos`, `mongodb`, `projects` | Browse the indexed catalog; `microservices`, `topics` and `mongodb` list the corresponding architecture objects directly, each with a `kind` and `name`, and support the documented list/show/neighbors actions and JSON output where applicable. |
| `systemlens flows [list] [--root DIR] [--json]` | Lists persisted potential code flows from an entry point to a source-evidenced external effect, within one method or across CodeQL-resolved method calls. |
| `systemlens flows show ID_OR_QUERY [--root DIR] [--json]` | Shows the ordered steps and source evidence of one unambiguously selected potential code flow. |
| `systemlens microservices topics\|apis\|mongodb\|properties\|openapi NAME [--root DIR] [--json]` | Follow one linked object kind from a single named microservice. |
| `systemlens microservices implementation KIND ID [--root DIR] [--json]` | Jump to the source implementation of one identified integration. |
| `systemlens projects integrations PROJECT [--json]` | Lists the integrations owned by one Maven/Gradle project. |
| `systemlens projects graph [--json]` | Prints the Maven/Gradle build-dependency graph between projects. |
| `systemlens analyze audit [--workspace DIR]` | Reports static architecture risks; `--workspace` analyzes a parent workspace of independently indexed services instead of the current repository. |
| `systemlens analyze coverage [--root DIR] [--json]` | Reports inventory coverage and unresolved integrations. |
| `systemlens analyze indexing-issues [--root DIR] [--json]` | Lists unresolved indexing facts. JSON includes source evidence suitable for reviewing proposed heuristics. |
| `systemlens analyze microservices calls\|dependencies\|external-apis\|orphan-integrations [NAME] [--root DIR] [--json]` | Lists a service's outgoing calls, dependencies, external APIs, or integrations with no resolved caller/callee, depending on the subcommand. `external-apis` and `orphan-integrations` accept an optional `NAME` to scope the result to one service. |
| `systemlens analyze microservices impact NAME [--root DIR] [--json]` | Lists direct and transitive impact paths. |
| `systemlens analyze microservices path FROM TO [--root DIR] [--json] [--max-depth N] [--limit N]` | Lists bounded paths between services. |
| `systemlens analyze request-reply [--root DIR] [--json]` | Lists Strategy1 Topic request/reply candidates. |
| `systemlens export microservices (--html FILE | --c4 DIRECTORY | --json) [--graph FILE] [--workspace DIRECTORY] [--root-path DIRECTORY]` | Exports the deployable microservice, API, Data, and Topic topology. Non-deployable indexed projects (libraries and aggregators without an application entry point) are excluded from this view and remain available to `export projects` and `export layers`. Persisted MCP graph facts are included in the HTML export. `--graph FILE` reads a validated `systemlens-ai-graph-v1` manifest; `--workspace` federates separately indexed services below one parent directory; `--root-path` provides the local source root for HTML source links. |
| `systemlens export projects --html FILE` | Exports the Maven/Gradle build-dependency view. |
| `systemlens export layers --html FILE` | Exports a dedicated software-layer view. With the persisted Strategy1 profile, the project groups `PORTAIL` and `CYCLE-DE-VIE` are rendered in API/contracts and Orchestration, `DOMAIN-*` projects in Domain, and the documented layer-name prefixes/suffixes in their matching layers; shared libraries and other non-deployable projects are omitted, and without Strategy1 the repository-specific conventions are disabled. |
| `systemlens export modules --html FILE` | Exports the structural hierarchy where a module can contain child modules and indexed projects. Membership comes from project directory paths, never from Kubernetes namespaces. The legacy `export clusters` and `export namespaces` spellings remain hidden compatibility aliases. |
| `systemlens export request-reply --html FILE` | Exports Strategy1 Topic request/reply candidates. |
| `systemlens web [--host HOST] [--port PORT]` | Starts the local Python web application at `http://127.0.0.1:8765/` by default. Its home page links to Architecture. Architecture renders the persisted snapshot for each request, excluding test-fixture microservices and every relation attached to them; when no index exists, it offers an explicit local button that creates the default configuration when needed and indexes the repository. The default loopback host prevents network exposure unless the user explicitly changes `--host`. |
| `simpleweb [DIRECTORY] [--host HOST] [--port PORT]` | Serves static files from `DIRECTORY`, or from the current directory when omitted, for opening generated HTML files that load adjacent JSON. It binds to `http://127.0.0.1:8000/` by default, has no write routes, and does not create or modify files. The directory must exist. |
| `systemlens mcp` | Starts the stdio MCP server. |

`systemlens index` reports its file delta, AST analysis stage, persisted endpoint
count and materialized relations. It then prints a next-step hint towards the
interactive microservice HTML export. Its result line is:

```text
scanned=<N> skipped=<N> +integrations=<N> -integrations=<N>
```

The first AST-only run removes stale results from the retired analyzer.

`--topic-strategy strategy1` is opt-in. The selected strategy is persisted with
the index and reused by incremental MCP reindexing and all derived views.
`--disable` accepts `properties`,
`module-architecture`, and `module-tree-sitter`.

## Extraction contract

### Core extraction rules

An endpoint has a role (`serve`/`call` for REST, `produce`/`consume` for a topic),
a system, a topic (`METHOD /path` for REST), source location, framework and
optional module, qualified name and Java message type. A value that cannot be
resolved statically is flagged `topic_dynamic=true`; it is never fabricated.

The Java AST extractor covers Spring MVC/WebFlux, Feign, RestTemplate,
WebClient, Spring Cloud Gateway, Spring Data REST, Spring Kafka and Spring
Cloud Stream. Markdown and JSON Kafka manifests are supported as explicit
sources and are labelled `source=manifest`.

For REST clients, a literal URL or a unique, never-reassigned local string
base URL is normalized to its route and retains its HTTP host as target
evidence. Spring application names are read from multi-document YAML files;
profile-specific values do not override the base document without an explicit
active-profile selection. A mutable or otherwise unresolved URL remains a
dynamic, unresolved port rather than a guessed service link.

Indexing materializes AST method facts that associate each Java method with its
HTTP/message entry endpoints and HTTP/message output endpoints. It then
materializes conservative same-method code flows and, when the local CodeQL CLI
is available, creates a temporary source-only Java database to follow
CodeQL-resolved static method calls from an indexed entry method to an indexed
output method. `--codeql-database DIR` reuses an existing database instead.
The temporary database and the supplied database path are never persisted. If
CodeQL is unavailable, indexing reports that interprocedural flows were skipped
and retains AST-only flows. A uniquely resolved dispatch yields a `potential`
flow with medium confidence. When CodeQL identifies several compatible virtual
method implementations, SystemLens retains each candidate as a `potential`
flow with low confidence instead of choosing one. Unresolved dispatch,
reflection, dynamic routing, and runtime-only routing are not added.

For Kafka, SystemLens can continue a potential flow from a concrete,
statically resolved producer topic to a persisted concrete consumer entry. It
does not join dynamic topics and does not compose a producer whose later
external effect would be hidden by a linear rendering. Continuations are
bounded to four asynchronous hops and never revisit the same consumer flow.

A REST call forms an internal architecture relation only when its target
service is identified by an exact normalized explicit alias, such as an HTTP
host, an `lb://` service name, a configured client domain, or an HTTP host
resolved from a local field annotated `@Value("${…}")`. A matching HTTP
method and route only refines a resource within that already identified
service; it never identifies a service by itself. Calls without a unique target
remain indexed as unresolved evidence and are reported by coverage and indexing
issues rather than being linked to a coincidentally similar route.

### HTML export behaviour

Microservice cards in the Explorer view remain compact rectangles. A card with
persisted internal code flows displays their count, so services with discovered
flows can be identified directly on the graph. The inspector does not display
an inventory of input and output ports. When a persisted code flow links an
input endpoint to an output endpoint of the same service, it displays only that
source-evidenced potential internal flow and any CodeQL-resolved intermediate
method calls. It does not imply that every input reaches every output.
When a selected flow displays a numbered input or output label on a service,
hovering that label separates the step type, its trigger or effect, and the
associated Java method in a high-contrast tooltip with distinct lines.
The Flux widget uses the same numbering as those graph labels: only HTTP and
Kafka ports are numbered; method and Data steps remain ordered but unnumbered.
Its primary card title is the input trigger, while the Java method remains
visible as source evidence. If SystemLens cannot reconcile every displayed
step to a persisted topology edge, the card explicitly marks the graph view as
having partial edges rather than presenting it as a verified path.
Detected CodeQL call cycles and concrete Kafka topic cycles are retained as
potential `cycle` flows. They are visually distinguished and listed before
non-cyclic flows; within each category, longer flows appear first.
The Flux tab provides a `Cycles only` control with the detected-cycle count to
isolate them immediately.
Selecting a microservice also lists every persisted code flow owned by or
traversing one of its indexed ports. Each compact entry identifies its trigger,
length, cycle status and Java method, and can display that flow in Explorer.
Those entries provide separate actions to open the Flux tab, highlight the
flow in Explorer, or open its persisted Java method evidence in VS Code when
the export has a resolvable source root.

The HTML microservice export provides an inspector for each statically typed
Topic message. It shows the indexed payload-type identity, message topic, and
producer and consumer services. When the matching Java type is indexed, its
inspector also shows its source, declared fields, enum values, and conservative
recursive project-type navigation.

The Flux tab presents each persisted potential code flow as a compact ordered
timeline. User-facing step and confidence labels are localized, source paths
wrap within the panel, and the card, nested steps, metadata and action use the
shared light/dark semantic palette. A flow that can be reconciled with the
displayed topology offers an action to highlight that path in Explorer. The
selected flow keeps every participating node at full opacity with a visible
halo, emphasizes its edges, gently subdues unrelated edges while leaving
unrelated node cards opaque and unchanged. Selecting it keeps the Flux tab and
its card geometry unchanged; the selected card is marked in place instead of
replacing the left widget with the graph detail view. The camera frames the selected flow inside the visible
workspace beside the toolbar on wide screens and below it when that is the
larger available region, while preserving margins for fixed-size cards. This
fit never zooms in beyond the current readable view, is reapplied after a
viewport resize, and cannot make the toolbar scroll horizontally.
Clearing or replacing the selection
removes this flow-specific emphasis.

The architecture vocabulary is extensible: `Data` represents a persisted Data
resource or contract, while `Topic` represents a messaging channel. MongoDB
collections, SQL tables, Redis keyspaces, object-store datasets, Kafka,
RabbitMQ, SQS, and webhook streams are technology-specific evidence, not the
primary architecture category.

The export uses a responsive workspace layout with eight navigation tabs:
Explorer, Resources, OpenAPI, Topics, Data, Build, Quality, and Flux.
`Resources` is a filterable inventory of every persisted graph node; selecting
an item opens it in Explorer and focuses its graph card. `Topics` and `Data`
are data-schema reference views: Java classes defining exchanged event data
and persisted data respectively. They retain the generic `Topic` and `Data`
architecture categories rather than implying a single storage or messaging
technology. It includes
compact architecture counters, contextual details integrated into the left
panel, and a full-size resource inspector. On narrow viewports the left panel
uses one bounded, scrollable region so the graph remains visible while users
inspect controls or resource details. At intermediate widths up to 1024 px it
is limited to 320 px and 82% of the viewport height. Its branded header remains
visible while the panel content scrolls, and its navigation tabs use one
balanced four-column, two-row grid immediately after the branded header, so
all eight destinations remain visible without horizontal scrolling. The extended search
hint is hidden at those widths while its label and example placeholder remain
visible.

The panel typography uses a stable functional scale: 10 px uppercase kickers,
11 px compact controls, 12 px body copy, 13 px section prompts, 16 px view
titles, and 19 px selected-resource or itinerary titles. A component's size is
derived from its role in that hierarchy rather than from its individual tab.
Interactive widgets use one visual grammar across the panel: segmented tabs
and graph selectors share the same control height, inner radius, border,
surface, and blue selected state; collapsible Explorer sections use the same
bordered surface and outer radius. Pills remain reserved for filters, counters,
and status metadata so their shape continues to communicate a distinct role.
All widget families also consume one semantic palette for headings, body text,
muted metadata, accent text, panel surfaces, nested surfaces, controls, borders,
focus, and selected states. Large headings use the shared heading colour in
every tab and inspector; in the dark theme that colour is a restrained lavender
rather than white. Technology, relation, severity, and confidence colours are
excluded because they encode architecture data or status rather than chrome.

The export opens with a dark blue presentation (or follows the browser's light
preference), and provides a theme toggle in the graph
toolbar. The selected theme is stored only in browser local storage and does
not affect persisted inventory facts or exported architecture data.

Its initial view places resource and itinerary search inside Explorer,
immediately below the navigation tabs and before camera and rendering controls.
Graph-specific context follows the search: its first row contains zoom, fit
and reset actions; its second row contains the graph-view and node-rendering
selectors; architecture counters and inventory status follow. This whole context appears only in
Explorer, so domain tabs start directly with their own content. A dedicated,
compact `Displayed nodes and edges` control lets users independently select
API, Topic, Data-access and other edge categories, and internal services,
external services, messaging resources, Data resources and other node
categories. This filtering applies equally to native and MCP-enriched graph
vocabularies. Placement strategies remain available as advanced controls.
The toolbar does not expose a separate path-history section. Paths remain
available directly from search and the advanced route tools in `Explore`.

The graph offers three primary views—graph, layers, and modules—while
grouped and non-overlapping placement strategies remain secondary options. The layers view
uses ELK.js compound nodes to arrange resources in a deterministic hierarchy:
software layers are stacked vertically, modules are nested inside their
layer, and projects/resources are placed inside each module without
overlap. The internal canonical order is `api`, `application`, `orchestration`,
`infrastructure`, `domain`, then `persistence`;
`persistence` is always the lowest layer. In Strategy1, the `CYCLE-DE-VIE`
project group is rendered in the Orchestration layer.

External microservices are rendered in a dedicated `External services` layer
at the bottom, after the internal layer order.

Shared libraries and other non-deployable projects are not rendered as layers.

The three primary views are presented as a permanently visible segmented
selector with direct `Graph`, `Layers`, and `Modules` choices; changing
views MUST NOT require cycling through intermediate views. Placement strategies
remain secondary controls. On desktop, the navigation panel uses a compact
340 px maximum width, 10 px outer margins, and dense internal spacing. The
graph viewport spans the full window behind the floating navigation panel, so
the area below the panel continues to render both nodes and edges. Zoom
actions are grouped as a compact `−` / `+`
control, and the adjacent segmented fit control exposes two explicit modes:
`All`, which frames every visible node in the plain graph and uses the widest
collision-free scale in the module and layer views, and `Readable`, which keeps
the same center but zooms in until cards have useful reading separation. The
selected mode is reapplied after a view or window-size change, and `Readable
distance` is the initial mode. Reapplying either selected mode MUST be
idempotent: repeated or rapid clicks MUST produce the same camera framing and
MUST NOT compound the previous zoom.

Users can pan and zoom to explore the remaining graph. In the layers and
modules views, relations are visually subdued. In every view, microservice,
Topic, message channel,
Data resource, data schema, and equivalent resource cards share the same
rendered width, height, and scale. Their semantic differences are conveyed by
compact icons aligned with the name, plus border and color. The secondary kind
label uses the full inner card width instead of reserving a permanent icon
column.

Users can switch node rendering between `Cards` and `Symbols` without changing
the active graph, filters, layout, selection, node positions, or current camera
framing. The switch redraws node representations in place and MUST NOT reapply
either fit mode or rerun collision placement. `Cards` remains the default.
In `Symbols`, microservices use compact hexagons, Topics and message
channels use circles, and Data resources and data schemas use small
squares. Symbols use the same visual rule as cards: a light surface tinted by
the resource accent, a defined accent border and a restrained depth shadow;
the dark theme uses an accent-tinted slate surface rather than a saturated
solid fill. Node names use adaptive, collision-aware labeling: the viewport
shows a bounded sample prioritizing connected and semantically important
nodes, and progressively admits more labels as the user zooms in. A selected
or hovered node's name is always visible and MAY extend beyond the symbol
envelope. `Readable` fitting uses the compact symbol envelope in the plain
graph. Module and layer views retain full module-envelope clearance so
switching rendering mode never changes their camera framing.
The hovered symbol and its label are rendered above every non-hovered symbol,
so another geometric marker cannot obscure the visible resource name.
In the plain graph's `Readable` fit, a final screen-space collision pass keeps
the active card or symbol envelopes from overlapping. This pass does not run in
the layers or modules views, whose deterministic placement remains unchanged.

During pan and zoom, the graph and its module overlays remain synchronized so
cards and their containing rectangles move together without transient partial
redraws.

The details panel is integrated into the left navigation panel and is hidden
until a resource, architecture module, build project, or itinerary is selected. Opening it MUST
NOT resize or crop the full-window graph workspace; only the floating panel's
contents scroll vertically. The detail view temporarily
replaces the active tab content while preserving the global toolbar,
primary view controls and navigation tabs. The selection action is hidden when
nothing is selected and becomes an explicit `Fermer` action while details are
shown. Architecture
counters are temporarily hidden to give the details usable vertical space;
the current graph-view status remains visible so direct module navigation has
an immediate confirmation. Clearing the selection restores the Explorer
controls and counters. Buttons inside details use the full available width for
resource and module names rather than inheriting the compact square dimensions
of toolbar icon buttons. Node selection MUST NOT
move, zoom, refit, or otherwise alter the camera in any primary view. Selection
may update emphasis and details, but every node and module retains its current
screen position.

In the layers and modules views, each module exposes a full-width clickable
header. Selecting that header highlights the module and opens its member list;
the module body remains available for graph panning.

Module details MUST support direct hierarchy navigation: the parent module,
direct child modules, and resources assigned directly to the selected module
are actionable when present. Selecting a listed resource opens its ordinary
resource details. Conversely, every resource detail exposes its canonical
module as an action; following it switches directly to the Modules view and
selects that module. Nested module membership is derived from canonical
slash-separated paths. A parent module does not claim resources that
are assigned only to one of its descendants.

Resource details MUST NOT present Kubernetes runtime namespaces or enrichment
manifest namespaces as architectural grouping information. Kubernetes workload
entries identify the workload by kind and name without displaying a namespace
prefix. Namespace fields MAY remain in the embedded snapshot for compatibility
and source evidence, but the report's navigable structure uses modules only.

Panning MUST also work when the drag starts on a node card; a simple click on
the same card MUST continue to select the node.

Double-clicking MUST NOT change the camera zoom accidentally after a pan;
zoom remains available through the wheel and the explicit zoom controls.

Releasing a pan MUST stop the camera immediately; no inertial continuation is
allowed.

Automatic collision protection MUST NOT zoom the camera during a pan; it may
only constrain an explicit zoom-out operation.

In the module and layer views, zooming out remains available until fixed-size
cards would make sibling module envelopes overlap. The camera is then clamped
to the collision-free ratio calculated for the current viewport; panning and
zooming in remain available.

### Placement and interaction model

For the graph export, a module is a structural group that can contain child
modules and projects. Module membership comes from project directory
paths and MUST NOT be inferred from Kubernetes namespaces. Projects located
directly at the indexed repository root are assigned to the synthetic `root`
module. The legacy internal `project_namespace*` fields remain compatibility
aliases for the canonical `cluster_path`; they do not denote Kubernetes
namespaces.

The module layout is independent of the layer order and uses deterministic
two-level grid packing without ELK or fCoSE. Resources are placed locally
inside each module, then modules are placed in an outer grid with fixed graph
coordinate margins. After projection, the camera fit measures card and module
envelopes in screen coordinates and zooms to the smallest scale at which every
sibling rectangle is disjoint. The projected-envelope check is the
authoritative collision guard.

Node identifiers and module names are sorted only to make the result
reproducible; there is no semantic order between modules. Neither resources
nor module rectangles may overlap. If fCoSE is unavailable, the same
deterministic grid is used without the local fCoSE ordering.

ELK is used only for the architectural layer layout, while Sigma.js provides
the interactive rendering for both views. Architecture relations remain
visible even when they are not used as placement edges.

It also provides dedicated OpenAPI, Topics, Data, and Build
views, which keep their domain inventories separate.

Changing a relation-type filter rebuilds and relayouts the graph from only the
selected dependency types; excluded relations do not influence the resulting
graph layout.

### Layered-view rendering rules

The HTML architecture view MUST preserve these visual invariants:

- Each software layer is a bounded horizontal band whose width and height are
  calculated from its visible content. Layers MUST NOT be infinite full-width
  backgrounds.
- All visible layer bands MUST share the same left and right bounds. The first
  band starts immediately above its highest visible module content, and the
  last band ends immediately below its lowest visible module content.
- Each layer band MUST reserve a visible left gutter for its title. The title
  MUST NOT overlap a module; widening the band is
  preferred to moving or shrinking module content.
- The layer-band geometry MUST be calculated from one shared rectangle model:
  all bands use the same left/right bounds, and the title gutter is included
  before the first module envelope.
- Layers MUST be stacked vertically in the canonical order above, with the
  Persistence layer at the bottom.
- Each visible structural module MUST be represented by a bounded rectangle
  fully contained inside its owning layer, including its header and padding.
- A module MAY use several rows. The default placement uses at most five
  boxes per row; additional boxes wrap onto subsequent rows.
- Microservices, Topics, message channels, Data resources, data
  schemas and other rendered resources MUST NOT overlap. Placement MUST keep a
  positive horizontal and vertical gap greater than the projected card size.
- Microservice and resource cards MUST use one shared rendered width, height,
  and scale in every view. Type-specific styling MUST NOT change card geometry.
- Layer and module bounds MUST be recomputed after filtering, zooming,
  camera updates and layout changes so containers continue to contain their
  visible children.
- Selecting a layer or module MUST rebuild the visible graph without
  turning remaining cards white, losing isolated services, or leaving stale
  containers on screen.
- In the layers and modules views, selecting a module
  title MUST highlight that module and display its name and sorted list of
  currently visible elements in the details panel. Each listed element MUST
  open its ordinary node details.
- Changing node-type or relation filters MUST remain valid when no
  microservice layer is visible or when the filtered graph is empty; the
  renderer MUST clear stale layer and module containers without producing
  invalid coordinates.
- Changing a node-type filter MUST refresh the main graph renderer and its
  overlays immediately and MUST reapply the active graph layout to the
  filtered network.
- The layered view extends the module packing: each canonical
  software layer is a separate horizontal band ordered from top to bottom,
  modules are packed inside that band, and each module uses a first
  microservice sub-layer followed by a resource sub-layer. ELK compound-node placement is
  used as a seed when available, while the deterministic layer-aware packing
  is the final collision guard. If ELK is unavailable or fails, the fallback
  MUST retain the same layer order, module containment and non-overlap
  guarantees.
- If a module becomes too tall and risks crossing a neighbouring
  layer, the renderer MUST add columns to that module and recompute the
  layout. The additional horizontal space MUST expand the diagram rather than
  overlap another layer or module.
Every layout switch MUST refit the camera to the resulting graph using the
selected fit mode. In a compound view, both modes MUST honor the collision-free
camera limit; this may leave peripheral nodes outside the viewport, and users
can pan to reach them. `Readable distance` MAY zoom in further.
For graphs of at most 12 nodes, the readable mode MUST retain the complete
overview instead of applying its normal zoom and making the small diagram
appear empty. It adds only the zoom-out required to keep the full fixed-size
card envelopes inside the graph viewport; the screen-space collision pass
handles card separation.

The details panel MUST display the resolved software layer and the module
path once, in its `Architecture` section, for microservices and resources
(Topics, Data resources, and enriched resources). Architecture fields already
shown there MUST NOT be repeated as header badges or raw metadata. The module
path MUST be the slash-separated path of grouping
directories, such as `group1/group2`, without a structural-group prefix.
For a resource modified in writing, the path MUST be inherited from its
producing or owning microservice.
If several microservices modify the same resource in writing, the renderer
MUST associate the resource with the microservice belonging to the lowest
software layer in the canonical visual order.

### Module rendering rules

The module view MUST preserve these visual invariants:

- Module membership MUST use the same resolver for placement and for the
  visible module rectangle.
- A Topic, message channel, Data resource, or other resource MUST be
  assigned first to the module of its producing microservice, using the
  incoming source relation. A consumer module MUST NOT move the resource
  into its module. Resources without an identifiable producer remain in
  `ROOT`.
- Resources inside one module MUST be placed on a grid with a positive
  horizontal and vertical gap greater than the projected card size.
- Within each module, microservices MUST occupy the first
  sub-layer and Topics, Data, and other resources MUST occupy a second
  sub-layer below them. Empty sub-layers are omitted. This ordering is conveyed
  by placement only; the renderer MUST NOT add visible `Microservices` or
  `Resources` sub-layer labels inside the module.
- Module rectangles MUST be packed with a positive gap based on their full
  rendered envelope, including card, title, and padding margins, and MUST NOT
  overlap each other.
- When a second grouping level is displayed, each parent module MUST be the
  union of its visible child module rectangles plus its own title/padding
  margin. Parent bounds MUST contain the complete child boxes; the
  non-overlap rule applies between sibling modules, not between a parent and
  its descendants.
- A project-group parent MUST remain attached to the module of its owning
  projects and MUST contain only those owning projects. Resources MUST remain
  in the module of their producing microservice; relation targets
  MUST NOT be added as children of the structural parent or enlarge it across
  unrelated modules.
- Module bounds MUST be calculated from graph-coordinate bounds and projected
  after camera changes. Parent bounds MUST be recomputed from the projected
  child bounds, so zooming cannot make a child escape its parent or make the
  hierarchy drift.
- The layout MUST NOT depend on the software-layer order. Narrow viewports MAY
  use additional rows to keep modules inside the visible graph area.

Microservices with no indexed inter-service relation remain visible in a
separate isolated area of the graph, so their absence of dependencies is not
confused with an absent service.

Microservices, Topics, and Data resources are marked by their
relative connectivity. In the HTML graph, the shape identifies the resource
type. Microservices, Topics, and Data resources use a coloured
outline around a neutral interior; the fill never carries connectivity or risk
meaning.

The relation count includes their indexed API, Topic, and Data dependencies,
while low/medium/high relative tiers are calculated separately for each
resource type.

For a microservice, the count is its distinct direct
API clients and targets, Topic producer/consumer relations, and Data
relations; multiple HTTP routes between the same client and target
are counted once.

The HTML complexity badge exposes the HTTP, topic, and
Data breakdown as a tooltip, along with the resource rank and its soft
tercile bounds. The lowest third is blue, the middle third orange, and the
highest third red; the terciles are recalculated separately for each resource
type in every export. The graph label of each coloured resource also displays
its relative connectivity through its coloured outline; the exact details are
available after selecting the node.

The Explore search suggests indexed resource names and accepts either one
exact, unambiguous graph-node name or a topic itinerary written with `->`.

An itinerary starts and ends with a microservice and follows only directed topic
relations through Topics; it never traverses APIs or Data dependencies.

When a microservice and another resource have the same display name, a direct
resource search remains ambiguous, while an itinerary endpoint resolves the
unique microservice candidate required by the itinerary grammar.

Invalid, ambiguous, repeated, or unreachable stops leave the current graph
unchanged and produce an actionable message. The itinerary detail is an
ordered, clickable list of node names and types. A Topic lists its associated
DTO names in parentheses. Selecting any path stop reveals its ordinary detail
view, including the indexed Topic source links where present.

Every graph resource detail starts with one relation count when every indexed
relation is visible. When filters hide relations, it instead distinguishes the
indexed and visible counts. A `Relations` section follows. For a microservice,
that section separates
consumed and published API and Topic resources, plus Data resources. Each
microservice resolved to an indexed Maven or Gradle project also provides a
visible action that opens the module root directory in VS Code. Topics list
their applicable DTOs. A collapsed `Sources` section lists the indexed OpenAPI
and Topic files that provide the evidence, avoiding repetition in every
topic.

At constrained viewport sizes, the empty context panel is hidden. Once it
contains selected-node or path details, it replaces the Explorer controls and
starts at the top of the panel content. Its normal controls remain interactive
inside the single left-panel scroll region.

Indexing persists source evidence only as paths relative to the project root.
HTML export joins those paths to `--root-path` (the current directory by
default) when building VS Code links. No WSL distribution or absolute local
source path is stored in the index.

When several modules reference one OpenAPI/Swagger file, it is listed once for
the module that directly contains the source file.

MongoDB collection details list the services using the collection once, followed
by indexed Java persistence classes resolved from
`@Document`, Mongo repository entity generics, or unambiguous `Type.class`
arguments passed to `MongoTemplate`. They include their qualified name, source
location, collection, owning microservice, module, and declared fields without
repeating the collection in the inspector summary. The Persistence view provides the same inventory
with filtering by class, package, collection, or service and opens a dedicated
inspector. Fields whose type resolves uniquely to another indexed project class
are navigable recursively; the inspector provides a return action to the
containing class. External and ambiguous field types remain plain text.

Persistence classes declared in a dependent build project are attached to the
owning service collection through the indexed project-dependency graph. When
dependency metadata is unavailable, a workspace-wide class is used only if it
is the unique candidate for that collection name; ambiguous candidates remain
unassociated rather than being guessed.

The Topic detail lists resolved DTOs once. It lists message types only
when no matching DTO has been resolved, avoiding duplicate published and
consumed type lists when they describe the same contract.

Indexing issues that have a source endpoint expose a VS Code link to the
associated file and line. The HTML export provides dedicated OpenAPI, Topics,
Data, and Build views. OpenAPI and Topics both support
filtering their complete list (OpenAPI by path or service, DTOs by simple name
or package); Persistence filters by class, package, collection, or service. A
persistent inventory status reports whether unresolved indexing facts exist and
opens their review view.

`--topic-strategy strategy1` adds opt-in convention extraction for selected
`getTopics()` accessors and `envoyerMessageKafka*(kafkaProperties.getTopics().getXxx(), payload)` calls
(including `envoyerMessageKafkaRequest` and `envoyerMessageKafkaReply`),
`${kafka.topics.*.name}` expressions and configured REST client constants. It
also enables the `getXxxServiceUrl()` REST target-name convention.

For a `src/main/resources/openapi/xxx.rest` publication declaration, it
searches the entire indexed repository for same-named `xxx.yaml`,
`xxx.yml`, or `xxx.json` OpenAPI contracts, including contracts in a sibling
shared project without an `openapi-generator` Maven configuration. It also
searches every YAML or JSON document below
`model-xxx/src/main/resources/openapi/`, so that module may contain several
contracts with distinct names. Only a valid OpenAPI document is attached, and
its resulting endpoints remain attributed to the module that owns the `.rest`
declaration.

Independently of Strategy1, each build project inventories every valid YAML or
JSON OpenAPI document under its own `src/main/resources/openapi/` directory;
contract file names do not need to follow an `openapi.*` or `swagger.*`
pattern.

With Strategy1, it may also derive a high-confidence request/reply pair when
both sides follow the `retour_<request-topic>` convention.

## Incrementality and freshness

The index stores SHA-256 values for eligible files. A normal run parses added or
changed files and purges facts for deleted files. A full refresh is forced when
the endpoint extractor signature, analysis configuration signature, selected
topic strategy, Spring configuration file, or Maven/Gradle build descriptor
changes. Spring properties and build descriptors can affect facts attributed to
otherwise unchanged Java source files. Explicit manifests are included even when
otherwise excluded.

The index is `.systemlens/findings.db` for compatibility with prior releases. It is a
local implementation detail, not a contract for direct SQL writes.

## MCP

The MCP server exposes a deliberately small control surface for the
index-then-enrich workflow. It no longer mirrors every read-only CLI command:

| Tool | Purpose |
|---|---|
| `index_repository` | Index or refresh the current repository; preserves graph enrichment facts. |
| `graph_fact_exists` | Check a semantic node/edge fact before proposing it. |
| `add_graph_fact` | Add an AI/user node or edge assertion with confidence and optional relative evidence; rejects semantic duplicates. |
| `import_graph_facts` | Validate and atomically upsert a `systemlens-ai-graph-v1` manifest into one enrichment namespace, optionally removing stale facts for a complete snapshot. |
| `remove_graph_fact` | Remove an assertion previously added through MCP; never removes extracted source facts. |
| `list_graph_facts` | List the persisted enrichment layer. |
| `architecture_graph` | Return the complete generic dependency graph (services, APIs, Topics, Data resources and external resources) merged with persisted enrichment facts. |

Only `index_repository` creates or refreshes source-derived facts. Enrichment
facts are stored separately in `graph_facts`, survive reindexing, and are never
treated as source evidence.

Nodes require `fact_type=node`, `kind` and `name`;
edges require source/target kinds and names plus `relation`. Evidence paths are
relative to the indexed repository and may not escape it.

`add_graph_fact` remains an additive single-fact API and rejects duplicates.
For iterative analysis, use `import_graph_facts`: it reconciles by the
manifest namespace and stable node/edge id, replacing the complete stored
value for an existing AI fact. A partial manifest never removes facts; a
manifest with `mode=complete` (or an explicit `complete=true`) removes stale
facts only from that namespace. Source-derived facts are stored separately and
are never overwritten. The import is transactional and returns inserted,
updated and removed counts.

For generic middleware, use `kind=data_schema` or `kind=message_channel`, set
`technology` to the concrete implementation, and put provider-specific facts
such as database/schema/table, exchange/queue or partition in `metadata`.

## Boundaries

The inventory is static. Reflection, arbitrary string construction, runtime
routing and undeclared external contracts can remain unresolved. Consumers must
use `topic_dynamic`, confidence and source evidence when interpreting the graph.
