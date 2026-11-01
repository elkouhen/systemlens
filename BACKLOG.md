# Backlog: Explorer UX improvements

Status: proposed on 2026-11-01 from the UX review of the generated HTML
export; awaiting the product owner's validation of the lots before any
task starts (see `docs/process/delivery-practices.md`). Owner: SystemLens
implementer. Reviewers: product owner and UX maintainer. Review baseline:
commit `fdac92e`, live model `docs/models/simple-supermarket.html`,
viewport 1440 by 900, Obsidian theme.

This backlog carries one work stream, **Stream U**, which reworks how the
Explorer exposes its commands, its inspection path, and its empty states.
The review that motivates it is published as a page with before and after
captures; a local copy lives in `output/ux-review/revue-ux-explorer.html`
(git-ignored) with its captures under `output/ux-review/img/`.

The previous root backlog (data operations and runtime observations,
delivered on 2026-10-31) was removed from this file on 2026-11-01. Its
content and delivery record remain in Git history up to commit `fdac92e`.
Its open items are restated in section 8 so they are not lost.

## 1. Index and scope

| Document | Purpose |
|---|---|
| [UX/UI rules](docs/UX-UI.md) | Normative contract of the export; every task changes it first. |
| [Functional specification](docs/SPEC-FONC.md) | Observable behavior of the export and the CLI. |
| [Technical specification](docs/SPEC-TECH.md) | Data carried by the export and layout algorithms. |
| [Architecture decisions](docs/ADR.md) | Record for U3, which changes a contract rule. |
| [Product requirements](docs/PRD.md) | Personas and the "graph is the workspace" intent. |
| [Archived consistency backlog](BACKLOG.consistency-diagnostics.md) | Historical diagnostic rendering tasks. |
| [Earlier backlog](BACKLOG.previous.md) | Historical index of data-operation work. |

The deferred concerns of `AGENTS.md` still apply: no task below may add
CDN dependencies, ARIA semantics, or keyboard navigation work. A task that
touches a control keeps its current markup role and labels; it moves or
restyles the control without widening that scope.

The engineering invariants of `AGENTS.md` apply to every task: exports
read the persisted snapshot only, no local path is disclosed beyond the
existing `--root-path` resolution, and no task re-parses source files to
fill a new widget.

## 2. Baseline

Verified on the live model at the review date.

| Fact | Where | Consequence |
|---|---|---|
| Camera and rendering controls (`Centrer`, zoom, `Cartes`/`Symboles`, `Réinitialiser`) render inside the left panel, between the search block and the filters. | `.graph-mode-context-actions` in `src/systemlens/render/assets/graph.html` | The controls sit about 500 px from the surface they act on. |
| `Tout le graphe` and `Vue lisible` are specified in the camera section of `docs/UX-UI.md` but the Explorer exposes no such buttons. | `docs/UX-UI.md`, section "Camera and graph interaction" | The contract and the implementation disagree; U1 resolves it. |
| Opening the inspector requires `Shift + click`; a plain click does nothing and nothing on screen says so. | `docs/UX-UI.md`, section "Navigation, selection, and inspection"; `src/systemlens/render/assets/graph/50-paths.js` | First-time users cannot find the primary inspection path. |
| The inspector is a centered modal with a blurred backdrop. | `#inspector-modal` in `graph.html`, `src/systemlens/render/assets/graph/40-ergonomics.css` | The graph context is lost while inspecting. |
| The inspector of `inventory-service` lists `GET /api/products/{productId}` twice: once from the Java controller (`endpoint_id b6f1d065232ad37e`) and once from the OpenAPI contract (`a9cbdc7ef35fd27c`). | `http_routes` of the node in the export JSON; rendered by `appendActionList("Routes exposées", …)` in `50-paths.js` | Two evidence sources of one route render as two routes. |
| The `Flux de code` view shows an empty canvas and five disabled controls before a flow is selected. | `#flow-mode-context` in `graph.html`; `src/systemlens/render/assets/graph/55-code-flows.js` | The only view without an explanatory empty state. |
| Changing the call-tree depth does not refit the tree; at depth 4 the last occurrence is outside the viewport. | depth control in `55-code-flows.js` | `Déplier tout le graphe` refits, the depth control does not. |
| The resource catalogue spans the full viewport width; every row carries an `Inspecter` button; `0 client` is repeated per route; the kind is the first segment of the Contexte text. | `src/systemlens/render/assets/graph/42-resource-catalogue.js` | Scanning cost grows with the inventory. |
| `Index : complet` is a standalone button in the panel; no visible count of resources per type exists; the index date is persisted (`files.indexed_at`) but not exported. | `#inventory-status` in `graph.html`; `src/systemlens/storage/sqlite.py` | The export cannot show when it was indexed. |
| The left panel keeps its width below 900 px; the only breakpoint is at 700 px. | `@media (max-width: 700px)` in `40-ergonomics.css` | At 820 px the graph keeps about 400 px. |
| `Layer` is the only English heading of the inspector; the search placeholder cites `orders -> payments`, two names absent from the project; the theme toggle reads `Clair` or `Obsidian`. | `50-paths.js` lines 891 and 921; `graph.html` line 69; `00-core.js` line 76 | Small wording defects visible on every export. |

## 3. Invariants for Stream U

1. A visual rule changes in `docs/UX-UI.md` first, then in the assets,
   then in `tests/test_browser_export.py`, in the same commit.
2. No task changes persisted facts, identifiers, or the export JSON
   contract except U7, which adds one optional field.
3. Every moved control keeps its `id`, its label, and its handler, so the
   existing browser tests keep locating it.
4. Light and Obsidian themes receive the same change; a task is not done
   until both are captured.
5. Nothing below 700 px regresses: the existing bounded scroll region and
   legend placement rules stay valid.

## 4. Stream U: Explorer UX

Tasks are ordered by delivery lot (section 6). Each task names its files,
its acceptance criteria, and the browser test to add or extend.

### U1. Floating graph toolbar

Move the camera and rendering controls out of the left panel into one
floating bar anchored at the top of the graph area, shared by the
Architecture graph and the selected call tree.

- Files: `graph.html`, `20-controls.js`, `30-layouts.js`,
  `40-ergonomics.css`, `50-obsidian.css`, `docs/UX-UI.md` ("Workspace
  shell", "Camera and graph interaction").
- Content, left to right: `Tout le graphe`, `Vue lisible`, `Centrer`,
  zoom group, `Cartes`/`Symboles`, `Légende`, `Réinitialiser`.
- The bar reserves a top band; the initial fit and `Tout le graphe`
  exclude that band so the highest node is never under the bar.
- The left panel keeps the search block and one `Filtres et affichage`
  group that wraps `Visibilité`, `Ressources masquées`, and
  `Plus d’options`.
- Acceptance: the two missing buttons exist and behave as specified; the
  existing camera tests pass unchanged; a new test asserts that no node
  card intersects the bar after `Tout le graphe`.

### U2. Discoverable inspection

Add a persistent interaction hint at the bottom of the graph area and make
the selection feedback of a plain click visible.

- Files: `graph.html`, `40-details.js`, `40-ergonomics.css`,
  `docs/UX-UI.md` ("Navigation, selection, and inspection").
- Hint text: `clic` selects and emphasizes relations, `⇧ clic` inspects,
  wheel zooms, drag pans. It hides below 700 px.
- A plain click on a node selects it and applies the existing emphasis of
  incident relations; it still opens nothing. If the product owner prefers
  a click to open a docked summary instead, record the decision in the
  ADR of U3 before implementing.
- Acceptance: a browser test clicks a card and asserts the emphasis class
  on its incident arcs and the absence of the inspector; the hint is
  present in both themes.

### U3. Docked inspector

Replace the centered modal by a panel docked to the right edge, the graph
staying visible and shifted, with a modal fallback under 900 px.

- Files: `graph.html`, `50-paths.js`, `40-ergonomics.css`,
  `50-obsidian.css`, `docs/UX-UI.md` ("Widget inventory", "Navigation,
  selection, and inspection"), `docs/ADR.md` (new ADR).
- Header keeps title, breadcrumb, back, and close. A four-tile strip
  summarizes relations, proven, inferred, and without evidence level,
  using the existing badge counts. Secondary actions (`Centrer dans le
  graphe`, `Flux de code (N)`, VS Code) form one row under the strip.
- Relation sections become disclosure groups with a count; the first three
  open by default; an empty group stays listed and disabled.
- The inspected node receives a halo in the graph while the panel is open.
- `Routes exposées` merges entries with the same method and path and lists
  each evidence source (Java, OpenAPI) under the merged row, so the
  duplicate `GET /api/products/{productId}` renders once. The persisted
  endpoints are unchanged.
- Acceptance: existing inspector tests pass with updated selectors; new
  tests cover the merged route row, the halo, and the modal fallback at
  820 px.

### U4. Empty state of the Flux de code view

Show an explanatory card on the canvas when no flow is selected, and hide
the tree controls until a flow is open.

- Files: `graph.html`, `55-code-flows.js`, `40-ergonomics.css`,
  `docs/UX-UI.md` ("Shared states", "Selected call tree").
- The card states that flows are potential paths, explains that selecting
  an entry point unfolds HTTP and Kafka calls with their evidence level,
  and lists the flows as shortcuts with method badge, path, service chain,
  step count, and the count of steps with insufficient evidence.
- Choosing a shortcut selects the same flow as the panel list.
- Acceptance: a browser test opens the view, asserts the card and the
  hidden controls, selects a shortcut, and asserts the tree.

### U5. Call-tree context on the canvas

Move the flow summary, the tree controls, and the access key out of the
left panel onto the canvas, and refit on depth changes.

- Files: `graph.html`, `55-code-flows.js`, `40-ergonomics.css`,
  `docs/UX-UI.md` ("Selected call tree").
- Top bar: trigger and owner service, node and arc counts, depth, and
  three coloured counts (risks, insufficient evidence, guarantees); then
  the controls that are currently enabled. Disabled actions are hidden
  rather than greyed; `Sélection` appears once an occurrence is selected.
- The C/R/U/D key becomes a collapsible docked at the bottom left of the
  canvas, open by default, remembered per viewer.
- Changing the depth with `−` or `+` refits the tree like
  `Déplier tout le graphe`; `+N appels` keeps the current rule and does
  not move the viewport.
- The left panel keeps only the flow list, with the selected flow first.
- Acceptance: the existing call-tree tests pass; a new test increases the
  depth to 4 and asserts every occurrence card is inside the viewport.

### U6. Denser resource catalogue

Bound the catalogue width, label the kind, and reveal the action on hover.

- Files: `42-resource-catalogue.js`, `40-ergonomics.css`,
  `docs/UX-UI.md` ("Workspace shell", catalogue paragraphs).
- Layout bounded to 1180 px and centered; the mode tabs keep their panel
  width; the collapse control stays in the panel header.
- Each row shows a kind tag coloured like the graph outlines, the HTTP
  verb as a badge, the owner, and a relation summary; `0 client` renders
  as a muted `aucun client`. `Aperçu seul` keeps its muted label.
- `Inspecter` appears on row hover and on the selected row; the row itself
  keeps opening the docked preview.
- Default order: kind, then owner, then name.
- Acceptance: the three catalogue tests pass; a new test asserts the tag
  text per kind and the hover-only action.

### U7. Index status and type counts

Show the index status with its date under the panel header and a strip of
per-type counts under the graph toolbar.

- Files: `src/systemlens/render/html_export.py` (export the latest
  `files.indexed_at` as an optional `indexed_at` field), `graph.html`,
  `10-rebuild.js`, `docs/SPEC-TECH.md` (export contract), `docs/UX-UI.md`.
- Status line: `Index complet` or `Index : N faits à vérifier`, with the
  relation count and `indexé le <date>`; the date is omitted, not
  invented, when the field is absent from an older export.
- Count strip: one chip per visible type (microservices, topics,
  collections, entities, others) computed from the rendered graph;
  clicking a chip toggles the matching visibility filter.
- Acceptance: a unit test in `tests/test_render.py` for the new field and
  an older snapshot without it; a browser test asserts chip counts after
  hiding a microservice.

### U8. Narrow viewports and wording

- Collapse the left panel automatically below 900 px and reopen it as a
  drawer over the graph; the 700 px rules stay as they are.
- Rename the `Layer` heading to `Couche`.
- Generate the search placeholder from two indexed services of the export
  when at least two exist, else keep a neutral example.
- Theme toggle reads `Sombre` and `Clair`; `Obsidian` stays the palette
  name in the documentation.
- The `Diagnostics` category with zero entries shows `aucun diagnostic`.
- Files: `40-ergonomics.css`, `50-paths.js`, `00-core.js`, `graph.html`,
  `42-resource-catalogue.js`, `docs/UX-UI.md`, `docs/spec-fonc/html-export.md`.
- Acceptance: a browser test at 820 px asserts the collapsed panel and the
  drawer; string assertions cover the four wording changes.

## 5. Validation

For each task:

```bash
uv run ruff check src tests
uv run pytest tests/test_render.py tests/test_modules.py
SYSTEMLENS_CHROME_BIN=/path/to/chrome uv run pytest -m slow tests/test_browser_export.py
```

Before closing each lot:

```bash
uv run mypy
uv run pytest
uv run python scripts/check_companion_contracts.py
```

After U7 and at the end of the stream, regenerate the lab export from
`../systemlens-observability-lab/apps/supermarket-demo/architecture-ia.html`
and copy it to `docs/models/simple-supermarket.html`, as `AGENTS.md`
requires. Capture the Explorer, the inspector, the empty flow view, a
depth-4 call tree, and the catalogue in both themes and attach them to the
delivery record. State the validation as unverified when Chrome or the lab
checkout is unavailable.

## 6. Delivery lots

| Lot | Tasks | Why this order |
|---|---|---|
| 1 | U2, U4, U7 | Surface additions without a contract change. |
| 2 | U1, U5 | Share one floating-bar component and the refit rule. |
| 3 | U3 | Changes the modal rule of the contract; needs an ADR. |
| 4 | U6, U8 | Polishing once the shell is stable. |

## 7. Delivery record

Each task is recorded here with its commit, the validation that ran, and
any deviation from the work package.

| ID | Outcome |
|---|---|
| U1 | Open. |
| U2 | Delivered on 2026-11-01 (commit "Make inspection discoverable with a hint and plain-click selection"). A plain click selects the node (details widget, emphasized relations, dimmed unrelated cards) and a second click or a stage click clears it; `Shift + click` still opens the inspector; a permanent hint names the four gestures and hides with the graph, in the call tree, and below 721 px. PDR-1 amended. Verified by `test_plain_click_selects_a_node_and_the_hint_names_the_gestures`; browser suite 41 passed with Chrome; default suite, ruff, mypy clean. Deviation: none. |
| U3 | Open. |
| U4 | Delivered on 2026-11-01 (commits `5f9eeda` and "Close U4"). While the catalogue is open and no flow is selected, the graph area shows a card that explains the view and lists up to eight flows as shortcuts (trigger kind or verb, name, service chain, steps, diagnostics to verify); a footer counts the rest; a shortcut opens the same flow as the list. Verified by `test_flow_catalogue_without_selection_shows_shortcuts_on_the_canvas`; browser suite 42 passed twice with Chrome; default suite, ruff, mypy clean. Deviation: the browser suite had become timing-sensitive on this machine (cards moved by the layout pass while a test clicked, the call tree rendered a frame after the click, the zoom took more than 80 ms); the tests now wait for a settled layout and for the rendered tree instead of fixed delays. |
| U5 | Open. |
| U6 | Open. |
| U7 | Delivered on 2026-11-01. `Store.latest_indexed_at` feeds an optional `indexed_at` view-model key; a status line under the header shows the index status, the relation count, and `indexé le <date>` when known; type chips at the top right of the graph count nodes per type filter and toggle it. Verified by `test_export_carries_the_index_time_only_when_known`, two storage tests, and `test_index_status_line_and_type_chips_follow_the_filters`; browser suite 43 passed with Chrome; default suite, ruff, mypy clean. Deviations: the status keeps the contract wording `Index : complet` rather than `Index complet`; chips follow the five existing node-type filters (microservices, external services, topics, collections, other resources) so a chip never hides a type the filters cannot express; the now-empty graph-context section is hidden. |
| U8 | Open. |

## 8. Items carried over from the previous backlog

These items were left open on 2026-10-31; see the delivery record in Git
history before `fdac92e` for their context. Their status follows the
vocabulary of `docs/process/delivery-practices.md`.

| Item | Status |
|---|---|
| Promote the read-modify-write and dual-write review aids out of `export microservices --review-aids`. | `blocked: no second Spring application in the workspace for the precision measurement` |
| Field validation of runtime percentiles, the Prometheus source, and completeness facts (R1, R4, R5). | `blocked: lab Elastic cluster and Prometheus server unreachable from the delivery machine` |
| Regenerate `docs/models/simple-supermarket.html` with the hotspot group and the badge facts. | `blocked: needs a lab reindex plus a runtime collection` |
| Make the lab reachable from a scheduled CI job so the three items above can leave `blocked`. | `open`; depends on the lab repository's compose setup |
| Regenerate the live model in the `pages.yml` pipeline, as `supermarket.html` already is. | `open`; depends on the previous item |
