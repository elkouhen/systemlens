# Catalogue screen restructuring backlog

Implement the selected catalogue layout: a horizontal application header,
resource filters on the left, a table in the centre, and a persistent inspector
on the right. Visual acceptance uses the selected screenshot, not the existing
card-based implementation.

| Field | Value |
|---|---|
| Status | In progress; catalogue projection and workspace shell are implemented locally, acceptance remains open until the full screen contract passes |
| Owner | Implementing developer, with product owner reviewing visual fidelity |
| Audience | Developers and reviewers of the HTML export |
| Scope | Screen structure, resource browsing, inspection, export verification |
| Reference | [Selected catalogue screenshot](artifacts/resource-navigation/02-catalogue.png) and [HTML mockup](artifacts/resource-navigation/02-catalogue.html) |
| Tracking | This file only; no GitHub tickets or project boards |

## 1. Grounding and scope

The following observations are grounded in source revision `552badb`.
All target behaviour and dimensions below are planned acceptance requirements,
not claims that the implementation already satisfies them.

| Observed gap | Evidence | Required work |
|---|---|---|
| Catalogue remains nested inside the architecture toolbar | [HTML template](src/systemlens/render/assets/graph.html), `microservices-panel` | Separate the application shell and workspace screens |
| Repeated navigation bars, card grids and overlapping layout overrides differ from the mockup | [Styles](src/systemlens/render/assets/graph/50-obsidian.css), `catalogue-active` rules | Replace the catalogue layout and remove obsolete overrides |
| Catalogue rows come only from graph nodes | [Resource rendering](src/systemlens/render/assets/graph/40-details.js), `renderMicroservices` | Include routes, contracts and indexed data types |
| Preview references an undeclared `resourceCataloguePreview` binding in source | [Preview rendering](src/systemlens/render/assets/graph/40-details.js) and [controls](src/systemlens/render/assets/graph/20-controls.js) | Fix initialization and test HTML assembled from current source |
| Specifications require a separate inspection window and graph switching | [UX rules](docs/UX-UI.md) and [export behaviour](docs/spec-fonc/html-export.md) | Record the approved catalogue exception alongside implementation |

The selected image defines layout, hierarchy and component treatment. Its
counts, labels and sample relationships are illustrative; production content
must come from the persisted snapshot. Remove prototype banners and proposal
numbers from the application.

Preserve indexing, SQLite compatibility, evidence confidence and existing CLI
contracts. Do not infer owners from project namespaces or invent relationships.
Keep source evidence relative in persistence and resolve local links only at
export time. CDN changes, ARIA redesign and keyboard-navigation work remain
deferred under [AGENTS.md](AGENTS.md).

## 2. Target screens and design choice

> **Why & What: dedicated resource workspace**
>
> **What:** Introduce a dedicated catalogue screen with a shared inspector
> mounted in its right-hand region.
>
> **Why:** The selected layout lets users filter, compare and inspect resources
> without losing the result list. It does not resolve missing indexed evidence.
>
> **Alternative considered:** Extending the graph toolbar reuses existing
> components cheaply, but cannot reproduce the selected screen hierarchy.
>
> **Fallback:** Keep existing inspector renderers available during migration;
> do not mark the catalogue complete until the dedicated screen passes review.

| Screen or region | Target behaviour | Migration rule |
|---|---|---|
| Shared header | Brand, project context, Architecture, Flux de code, Ressources, Diagnostics, search and theme control | One horizontal navigation level; preserve the default Architecture landing screen |
| Architecture | Existing graph, layouts, zoom, selection and graph-specific controls | Hide these controls outside the graph screen |
| Flux de code | Flow catalogue followed by the selected call tree | Preserve flow filters and return behaviour |
| Ressources | Full-width catalogue below the shared header | Replace the enlarged toolbar and duplicate resource tabs |
| Catalogue left region | Resource types with counts, then microservice checkboxes | Use visible category rows rather than a type dropdown |
| Catalogue centre | Breadcrumb, category title, filter chips, HTTP method filter, result table and count | Use table rows rather than a grid of cards |
| Catalogue right region | Persistent shared inspector with resource identity, relations, contract and source | Selecting a row updates this region without switching screens |
| Diagnostics | Existing diagnostics list | Preserve content and return navigation |
| Contract and class details | Reachable through resource categories and inspector links | Preserve OpenAPI, AsyncAPI, DTO, JPA and Mongo inspection capabilities |

At the reference viewport of 1440 × 1000, use a header approximately 76 px
high, outer margins of 30 px, a left region near 218 px and a right region near
305 px. These targets come from the selected image; the centre fills the
remaining width. Avoid large unused bands between header, title and results.

## 3. Execution order

Each task includes its implementation and acceptance evidence. Keep tasks open
until their checks pass. Task identifiers are local backlog identifiers.

| Task | Priority | Depends on | Status |
|---|---|---|---|
| CAT-01: Screen and interaction contract | P0 | None | In progress |
| CAT-02: Application shell and screen lifecycle | P0 | CAT-01 | In progress |
| CAT-03: Resource catalogue projection | P0 | CAT-01 | In progress |
| CAT-04: Category and service filters | P0 | CAT-02, CAT-03 | In progress |
| CAT-05: Central resource table | P0 | CAT-04 | In progress |
| CAT-06: Persistent resource inspector | P0 | CAT-03, CAT-05 | In progress |
| CAT-07: Navigation continuity and legacy migration | P0 | CAT-02, CAT-06 | Open |
| CAT-08: Visual fidelity and constrained layouts | P0 | CAT-04, CAT-05, CAT-06, CAT-07 | In progress |
| CAT-09: Browser regression coverage | P0 | CAT-07, CAT-08 | In progress |
| CAT-10: Generated exports and delivery evidence | P0 | CAT-09 | In progress |

### CAT-01: Define the screen and interaction contract

Goal: give implementation and visual review the same target.

Files: `docs/UX-UI.md`, `docs/spec-fonc/html-export.md`,
`docs/SPEC-FONC.md`, and `docs/ADR.md` for the durable workspace decision.

- [ ] Describe the target screens in chapter 2 and the category taxonomy.
- [ ] Amend the conflicting rule that every catalogue selection opens a modal
  or returns to the graph. Scope the docked inspector rule to Ressources.
- [ ] Define selection, filter, empty-result, missing-evidence and return states.
- [ ] Preserve the graph inspector behaviour outside Ressources.

Acceptance: a reviewer can trace every region in the selected screenshot to
a specified behaviour. Modal-only and graph-switching rules no longer conflict
with the planned catalogue. Verify through a source-to-spec review.

### CAT-02: Separate the application shell from workspace screens

Goal: make Ressources an independent workspace under the shared header.

Files: `src/systemlens/render/assets/graph.html`,
`src/systemlens/render/assets/graph/20-controls.js`,
`src/systemlens/render/assets/graph/40-details.js`,
`src/systemlens/render/assets/graph/45-view-lifecycle.js`,
`src/systemlens/render/assets/graph/60-bootstrap.js`.

- [ ] Move the catalogue outside `architecture-toolbar` into a dedicated root.
- [ ] Create the shared horizontal header and four screen entry points.
- [ ] Replace body-class-only switching with explicit workspace activation.
- [ ] Hide graph canvases, overlays, legend and camera controls in Ressources.
- [ ] Initialize DOM references before registering handlers. Fix the missing
  preview binding without relying on stale code embedded in generated HTML.

Acceptance: opening Ressources shows only the catalogue workspace. Returning
to Architecture restores a working graph without duplicate handlers or errors.
Verify with a browser test switching among all four screens twice.

### CAT-03: Build a complete resource projection from the snapshot

Goal: include resources that are not graph nodes, especially HTTP routes.

Files: `src/systemlens/render/assets/graph/40-details.js`, proposed
`src/systemlens/render/assets/graph/42-resource-catalogue.js`,
`src/systemlens/render/html_export.py`, `docs/SPEC-TECH.md` and its owning
rendering section. Register the new module in the ordered asset list.

- [ ] Extract a catalogue adapter with stable resource IDs, kind, display name,
  associated services, evidence references and inspector targets.
- [ ] Include microservices, HTTP routes, topics, collections, OpenAPI/AsyncAPI
  contracts, DTOs, JPA entities and Mongo persistence classes.
- [ ] Reuse existing route grouping and contract/type lookup logic. Deduplicate
  routes by provider, method and path; preserve genuinely distinct providers.
- [ ] Distinguish ownership from association. Topic producers and consumers
  are associated services; project namespaces are not inferred owners.
- [ ] Index associations once per snapshot rather than rescanning all links
  for every visible row. Document construction and filtering complexity.

Acceptance: a snapshot with no graph node for a route still lists that route.
Identical paths from different services remain distinct. Unknown owners stay
unknown, client counts count unique indexed consumers, and source facts remain
unchanged. Cover these cases in catalogue and export tests.

### CAT-04: Implement category navigation and combined filters

Goal: reproduce the left-hand navigation and centre filter controls.

Files: `src/systemlens/render/assets/graph.html`, proposed
`src/systemlens/render/assets/graph/42-resource-catalogue.js`, and proposed
`src/systemlens/render/assets/graph/60-catalogue.css`.

- [ ] Render category rows with counts, including an all-resources category.
- [ ] Add service checkboxes with an explicit all-services state.
- [ ] Place resource search in the header; combine query, type and service
  filters using intersection. Multiple selected services use union.
- [ ] Show removable active filter chips and a reset action. For HTTP routes,
  add a method selector. Make every visible filter control functional.
- [ ] Calculate category counts using query and service filters before applying
  the selected category. Show the final result count beside the table.

Acceptance: selecting Routes HTTP, inventory-service and POST returns only
matching routes. Removing a chip restores matching rows. Reset restores the
full inventory. An unmatched query produces an explicit empty state.
Verify these sequences in browser tests.

### CAT-05: Render the central catalogue as a table

Goal: match the reference's compact, aligned resource rows.

Files: `src/systemlens/render/assets/graph.html`, proposed
`src/systemlens/render/assets/graph/42-resource-catalogue.js`, proposed
`src/systemlens/render/assets/graph/60-catalogue.css`.

- [ ] Replace `reference-item` card grids with a dedicated result table.
- [ ] For routes, render method badge and path, provider, and indexed client
  count. For other categories, use resource, service association and type-
  appropriate counts with explicit column labels.
- [ ] Use a stable category/name/service order and stable resource identity.
- [ ] Highlight the selected row in cyan. Clicking anywhere on a row selects
  it without changing the graph or navigating away.
- [ ] Retain selection while it remains in the results; clear selection and
  the inspector when filters remove it. Keep the inspector's empty state visible.

Acceptance: POST /api/reservations appears as a table row when present in
the snapshot. Its provider and client count agree with indexed evidence.
Filtering, clearing and reselecting cannot leave stale details on screen.
Verify row contents and selection state in the browser.

### CAT-06: Mount the shared inspector in the right-hand region

Goal: show usable resource details without hiding the catalogue.

Files: `src/systemlens/render/assets/graph/40-details.js`,
`src/systemlens/render/assets/graph/50-paths.js`, proposed
`src/systemlens/render/assets/graph/42-resource-catalogue.js`,
`src/systemlens/render/assets/graph.html`.

- [ ] Separate inspector content rendering from its modal container so the
  catalogue can mount the same content in a persistent right-hand region.
- [ ] Render identity once, followed by provider or owner, related resources,
  associated contracts, indexed source evidence and relevant type details.
- [ ] Preserve working source actions and specialized contract/class viewers.
- [ ] Support related-resource navigation and back history without discarding
  catalogue filters. Inspect related items outside the filter without secretly
  changing it; keep the originating row selected until the user selects another.
- [ ] Provide an explicit graph action for resources with graph targets.
  Do not manufacture graph nodes for routes or contracts.

Acceptance: selecting a route exposes its provider, indexed clients, contract
and source when available. Two successive selections replace all details.
Back restores the previous inspected resource. Missing evidence is explicit.
Verify these interactions and assert no page errors from newly generated HTML.

### CAT-07: Preserve navigation and retire duplicate resource screens

Goal: consolidate entry points without losing existing browsing capabilities.

Files: `src/systemlens/render/assets/graph/20-controls.js`,
`src/systemlens/render/assets/graph/40-details.js`,
`src/systemlens/render/assets/graph/45-view-lifecycle.js`,
`src/systemlens/render/assets/graph/60-bootstrap.js`,
`tests/test_browser_export.py`.

- [ ] Route Topics, Routes, Collections and contract/type entry points to the
  matching catalogue category, then remove duplicate top-level tabs.
- [ ] Preserve catalogue query, filters, selection and table scroll position
  across a visit to Architecture, Flux de code or Diagnostics.
- [ ] Keep graph state and catalogue state separate; restore graph camera and
  layout when switching back without a graph-focus action.
- [ ] Retain nested DTO, entity, OpenAPI and AsyncAPI inspection paths.

Acceptance: filter a catalogue, inspect an item, visit a flow, then return.
The filters, selected row and details are retained. Architecture and Diagnostics
remain usable. Existing interaction tests pass with selectors updated to the
new entry points rather than assertions removed.

### CAT-08: Match the selected image and handle constrained widths

Goal: make the implemented screen visually recognizable as the selected catalogue.

Files: proposed `src/systemlens/render/assets/graph/60-catalogue.css`,
`src/systemlens/render/assets/graph/50-obsidian.css`,
`src/systemlens/render/assets/graph.html`, `src/systemlens/render/html_export.py`.

- [ ] Apply the chapter 2 dimensions, continuous column borders, Obsidian
  surfaces, compact typography, badges and selected-row treatment.
- [ ] Remove superseded `catalogue-active` toolbar expansion, card-grid and
  duplicate grid-position rules. Keep general theme tokens reusable.
- [ ] Keep the results and inspector independently scrollable at desktop width.
- [ ] At 1024 px, reduce side-region widths while preserving readable rows.
  At 768 px, put filters above results and offer the inspector as an explicit
  closable detail surface that returns to the same filtered table.
- [ ] Preserve readable light-theme surfaces and the saved theme preference.

Acceptance: inspect screenshots at 1440 × 1000, 1024 × 768 and 768 × 1024.
At desktop width, all three regions remain visible after selection, with no
graph legend, stacked mode bars, card grid or large blank navigation bands.
The narrow layout has no clipped actions or page-wide horizontal overflow.

### CAT-09: Add browser regressions and visual comparison evidence

Goal: verify interaction and visual fidelity in HTML assembled from current assets.

Files: `tests/test_browser_export.py`, `tests/test_render.py`,
`tests/test_web.py`, `artifacts/resource-navigation/`.

- [ ] Generate test HTML from the current renderer; do not use a stale embedded
  script in a checked-in export as implementation evidence.
- [ ] Cover combined filters, empty results, selection replacement, related
  inspection/back, all resource categories, screen return and theme switching.
- [ ] Fail on catalogue page errors, including undeclared DOM bindings.
- [ ] Capture the actual route catalogue with POST /api/reservations selected.
  Compare it side by side with `02-catalogue.png` at the same viewport.
- [ ] Record the renderer revision, fixture, viewport and browser used. Label
  differences in real data separately from structural visual mismatches.

Acceptance: interaction checks and visual review both pass. Passing Python
tests alone is insufficient. Chrome unavailability leaves browser acceptance
open; it cannot be reported as successful validation.

### CAT-10: Synchronize exports and verify the delivered artifact

Goal: ensure the user opens the artifact containing the verified catalogue.

Files: `docs/models/simple-supermarket.html`,
`../systemlens-observability-lab/apps/supermarket-demo/architecture-ia.html`,
`src/systemlens/render/html_export.py`, and the delivery evidence in this file.

- [ ] Regenerate the laboratory POC HTML with current renderer assets and its
  persisted snapshot. Preserve graph data and avoid a source re-index for styling.
- [ ] Copy the generated POC export to `docs/models/simple-supermarket.html`
  following AGENTS.md; verify that the two files are identical.
- [ ] Check that exports contain no unresolved template markers. Verify the
  CLI and web paths include the new ordered CSS and JavaScript modules.
- [ ] Open the exact generated file, exercise the route catalogue and capture
  it. Record its path, revision and successful checks.
- [ ] If publication is requested, verify the deployment and the served page.
  A Git push alone is not evidence that the user-facing page has changed.

Acceptance: POC and documentation exports match; the inspected output comes
from current source; both route selection and navigation work without page
errors. Report local verification and deployed verification separately.

## 4. Validation commands and completion gate

Use the project environment. Run focused checks after each implementation task;
run the complete suite before final delivery because navigation contracts change.

```bash
uv sync --group dev
uv run ruff check src tests
uv run pytest tests/test_render.py tests/test_web.py
SYSTEMLENS_CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" uv run pytest -m slow tests/test_browser_export.py
uv run mypy
uv run pytest
uv run python scripts/check_companion_contracts.py
git diff --check
```

Use the installed Chrome path on other systems. The companion check applies
when the sibling repositories are present, as specified in AGENTS.md. Record
missing prerequisites or failures explicitly; do not substitute an unverified
environment or silently skip a required interaction check.

- [ ] Every task has passing acceptance evidence and updated authoritative docs.
- [ ] The selected screenshot and actual export have been visually compared.
- [ ] Routes, contracts and data types are present beyond graph-node resources.
- [ ] The inspector, source actions and return navigation work in the final file.
- [ ] Generated HTML and source modules agree; the reviewed change is complete.

## 5. Costs and remaining checks

The dedicated workspace requires separating navigation state and inspector
rendering from the graph toolbar. Reusing content renderers reduces duplicate
domain logic, but their modal assumptions must be tested in the docked host.
Large inventories also require bounded scrolling and reusable lookup maps.

The implementing developer must confirm snapshot coverage for each resource
category during CAT-03. Missing data stays visible as unavailable evidence;
extractor expansion requires separate scope. The product owner reviews the
visual comparison in CAT-09 against the selected image.

This backlog proposes the implementation and acceptance gates. It does not
declare any catalogue task delivered or any published export verified.
