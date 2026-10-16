# Backlog: consistency diagnostics in the call graph

> Archived snapshot of the former backlog. Its task status is historical;
> use [the current backlog](BACKLOG.md) for active rendering work and the
> linked plans for the current status of deferred analysis.

Status: planned. Owner: SystemLens implementer. This backlog defines the
proposed “Cohérence des données” rendering mode for asynchronous writes and
synchronous reads. It covers multiple diagnostics in the same graph and the
inspection of each diagnostic's evidence. All tasks below await implementation.

## 1. Backlog index

| Document | Purpose | Status |
|---|---|---|
| [Current tasks](#4-implementation-tasks) | Render and inspect multiple consistency diagnostics in one call graph. | Planned |
| [Previous backlog](BACKLOG.previous.md) | Preserve the former root backlog and its links to data-operation work. | Historical copy |
| [Consistency analysis plan](docs/plans/async-write-read-consistency.md) | Track detector evidence, implemented scope, and deferred source inference. | Consult the plan |
| [Data operations plan](docs/plans/data-operations-in-internal-flows.md) | Track extraction, persistence, and data-operation follow-ups. | Consult the plan |

The historical copy preserves `BACKLOG.md` from `2e6c9cf^`, immediately before
its deletion in Git. Its status labels describe that snapshot. The linked
plans own the current status of their work; archiving does not close it.

## 2. Scope and rendering choice

The analyst should see where diagnostics concentrate, select one pair of
operations, and inspect why the detector classified it. A diagnostic identifies
an asynchronous write and a synchronous read within a graph context. Several
diagnostics can share either operation without duplicating its graph card.

> **Why & What - Overview with selected detail**
>
> **What:** Show aggregate badges and a diagnostic list, then highlight the
> selected diagnostic's paths and pair relation.
>
> **Why:** Shared operations remain readable when many pairs overlap. The
> analyst must select a diagnostic to see its complete relationship.
>
> **Alternative:** Draw every pair relation simultaneously. This exposes all
> relationships at once but adds crossing lines around shared operations.
>
> **Fallback:** Keep the list and evidence panel usable when a graph path
> cannot be resolved; identify the missing location explicitly.

This work consumes persisted diagnostics and preserves their classifications.
Automatic Kafka payload correlation, branch compatibility, and visibility
inference remain in the analysis plan. Runtime incident detection is outside
this rendering backlog. Existing deferred HTML concerns remain governed by
[AGENTS.md](AGENTS.md#deferred-html-accessibility-and-delivery-concerns).

## 3. Implementation context

| Area | Starting point |
|---|---|
| Diagnostic projection and source sites | [HTML export](src/systemlens/render/html_export.py) |
| Graph badges, tooltips, and focus | [Call graph renderer](src/systemlens/render/assets/graph/55-code-flows.js) |
| Selection state and view lifecycle | [State](src/systemlens/render/assets/graph/00-core.js), [lifecycle](src/systemlens/render/assets/graph/06-view-lifecycle.js) |
| Resource catalogue and inspection | [Catalogue](src/systemlens/render/assets/graph/42-resource-catalogue.js), [details](src/systemlens/render/assets/graph/40-details.js) |
| Shared panel and graph styling | [Widgets](src/systemlens/render/assets/graph/10-widgets.css), [presentation](src/systemlens/render/assets/graph/20-presentation.css) |
| Regression coverage | [Export tests](tests/test_render.py), [browser tests](tests/test_browser_export.py) |

These files are integration points to inspect, not requirements to duplicate
existing mechanisms. Keep the detector's persisted evidence separate from
temporary selection, filtering, and camera state.

## 4. Implementation tasks

Complete tasks in order. Each task includes its acceptance criteria. A checked
task means its behavior and relevant tests pass; record unavailable validation
explicitly instead of treating it as a pass.

### C01. Specify the mode and its state transitions

- [ ] Define entry from the call graph and from the diagnostics catalogue in
  [UX/UI](docs/UX-UI.md) and [HTML behavior](docs/spec-fonc/html-export.md).
- [ ] Specify overview, filtered list, selected diagnostic, and return to
  overview. Reuse the existing Architecture and Resources workspaces.
- [ ] Acceptance: the specification defines the active graph scope, selection
  reset on graph changes, and return behavior for every entry path.

### C02. Define diagnostic identity and variant grouping

- [ ] Extend the HTML projection only where needed to associate each
  diagnostic with its graph, write site, read site, and path variants.
- [ ] Assign display labels D1, D2, and so on deterministically within one
  exported graph. Filtering and sorting preserve those labels and IDs.
- [ ] Group only proven variants of the same operation pair, resource identity,
  causal scope, and classification. Keep distinct operations and incompatible
  evidence separate; unresolved identity must not cause speculative merging.
- [ ] Acceptance: export tests cover two reads sharing one write, two writes
  sharing one read, duplicate paths, and identical labels at different sites.
  Each group retains its member IDs, evidence, and variant count.

### C03. Add the diagnostic overview and filters

- [ ] Add a diagnostic list in the right panel, with a preferred width of
  500 px. Each row shows its label, resource, operation pair, and state.
- [ ] Add filters by resource, service, and state. A service matches either
  side of the pair. Show visible and total diagnostic counts for this graph.
- [ ] Count grouped diagnostics separately from path variants. Show separate
  totals for possible risks, insufficient evidence, and identified guarantees.
- [ ] Acceptance: filters combine consistently, clear independently, and never
  include diagnostics from another graph. “No diagnostics detected” and
  “No matching diagnostics” have distinct empty states.

### C04. Render shared-operation badges

- [ ] Display the number of associated diagnostics on each affected operation
  in overview mode. Reuse the existing service and method cards.
- [ ] Show counts by state for operations with mixed classifications. Keep
  orange for possible risk, gray for insufficient evidence, and green for an
  identified guarantee, with text labels alongside color.
- [ ] Clicking a badge filters the list to that operation's diagnostics.
  Provide a visible action to remove this operation filter.
- [ ] Acceptance: one write participating in three diagnostics has one card
  with the correct counts. Multiple paths do not inflate diagnostic counts.

### C05. Focus one diagnostic without losing graph context

- [ ] Highlight the selected write, read, and their supported paths. Attenuate
  other branches while keeping the graph context visible.
- [ ] Draw a dotted pair relation only for the selected diagnostic. Label it
  as a diagnostic relationship, separate from actual call or message arcs.
- [ ] Fit both operations and their common context when selecting a diagnostic.
  “All diagnostics” restores the previous overview camera and expansion state.
- [ ] Acceptance: switching D1 to D2 leaves no stale highlights. The display
  never implies runtime chronology from card position or path layout.

### C06. Explain evidence in the detail panel

- [ ] Show a classification-specific explanation, followed by resource and key
  identity, causal evidence, visibility evidence, and unresolved limitations.
- [ ] Show the service, emphasized Java method, parameter type, and operation
  for each side when available. Missing facts remain explicitly unknown.
- [ ] Link available publication, write, and read evidence to VS Code using
  paths resolved through the export root. Persisted paths remain relative.
- [ ] Acceptance: a shared topic or matching raw key text never appears as
  proof. A guarantee identifies its visibility evidence. Missing paths disable
  source navigation with an explanation. The 500 px panel fits a narrow viewport.

### C07. Connect catalogue navigation and diagnostic selection

- [ ] Opening a diagnostic from Resources selects its graph and detail panel.
  Provide previous and next actions within the current filtered list.
- [ ] Preserve filters while moving between list and detail. If a filter hides
  the selected diagnostic, clear selection and return to the filtered overview.
- [ ] Scope selection to the current graph and clear incompatible transient
  state when switching graphs or leaving the mode.
- [ ] Acceptance: list rows, graph badges, and catalogue entries select the
  same diagnostic. First and last entries disable the unavailable navigation
  direction. Returning to overview restores its list position and filters.

### C08. Add representative multi-diagnostic fixtures and tests

- [ ] Extend [synthetic examples](examples/README.md) with a graph containing
  shared writes, shared reads, independent pairs, mixed states, and variants.
- [ ] Add browser tests for filters, mixed-state badges, selected pair lines,
  evidence display, previous/next navigation, and camera restoration.
- [ ] Cover zero diagnostics, one diagnostic, missing evidence, graph changes,
  light and dark themes, constrained widths, and a densely annotated graph.
- [ ] Acceptance: assertions verify rendered behavior and selection state.
  Existing call graph navigation remains covered. The dense fixture keeps
  cards readable and the diagnostic list scrollable.

### C09. Complete documentation and validation

- [ ] Update [UX/UI](docs/UX-UI.md), [HTML behavior](docs/spec-fonc/html-export.md),
  and example instructions alongside the implementation. Update the technical
  data contract if the export projection changes.
- [ ] Run the focused export tests and browser suite required by
  [repository validation](AGENTS.md#validation). Run Python checks and companion
  validation when their corresponding contracts change.
- [ ] Acceptance: record the executed checks and any blocked prerequisites.
  Confirm that all earlier acceptance criteria have evidence before marking
  the rendering backlog complete.
