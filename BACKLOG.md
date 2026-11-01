# Backlog: graph viewport navigation

Status: `proposed` on 2026-11-01. No task starts before the product owner
validates this backlog, reorders its lots if needed, and settles the
decision of V3. V7 was requested and validated directly by the product
owner, and is delivered. Owner: SystemLens implementer. Reviewers: product owner
and UX maintainer. Baseline: commit `556c0f7`, viewport 1440 by 900.

This backlog replaces the delivered Explorer UX stream U1 to U8. Its
content and delivery record remain in Git history at commit `556c0f7`.
The items it carried over from earlier backlogs are restated in section 7
so they stay visible.

## 1. Problem

The product owner reports that viewport navigation does not work.
Commit `556c0f7` fixed four pan and zoom defects in the export assets.
The pages people open were not regenerated after that commit, so they
still show the defects. Trackpad gestures and the call tree also behave
differently from the contract or from user expectations.

## 2. Baseline

Measured on 2026-11-01 with Playwright in headless Chrome, WebKit, and
Firefox. Each probe dragged the empty stage by 120 by 70 pixels and sent
one wheel notch of 100 pixels.

| Surface | Drag result | Wheel notch | Verdict |
|---|---|---|---|
| Fresh export of the lab, current assets, three engines | 120 by 70 | 1.197 around the pointer | Conforms |
| Live model `docs/models/simple-supermarket.html`, built 2026-10-31 | 96 by 63.9 | 1.062, pivot drifts | Old defects |
| Lab POC `architecture-ia.html`, built 2026-10-31 | not measured | not measured | Predates `556c0f7` |

Other facts found by the same probes and by reading the assets:

| Fact | Where | Consequence |
|---|---|---|
| A horizontal wheel delta does nothing, and a vertical two-finger trackpad scroll zooms instead of panning. | `handleGraphWheel` in `src/systemlens/render/assets/graph/10-rebuild.js` | On a Mac trackpad the graph cannot be panned without a click and drag. |
| One wheel notch zooms the call tree by 1.06 while the Architecture graph zooms by 1.2. | wheel handler in `55-code-flows.js` | The two graph surfaces react differently to the same gesture. |
| A drag that starts on a type chip of the toolbar toggles the chip's filter when released. | `.graph-type-chip` handler, `20-controls.js` | A missed pan hides a node type without warning. |
| A window resize that changes the toolbar height refits the graph and drops the user's pan and zoom. | `refreshToolbarReservation` in `60-bootstrap.js` | Navigation state is lost on resize; the contract protects it only for the details panel. |
| No test or check compares the checked-in live model with the current assets. | `tests/`, `.github/workflows/pages.yml` | An asset fix can ship while the public page keeps the defect. |

Not verified: real trackpad and pinch hardware. Headless browsers send
synthetic wheel events, so V3 needs a manual check on a Mac trackpad.

## 3. Invariants

1. A visual rule changes in `docs/UX-UI.md` first, then in the assets,
   then in `tests/test_browser_export.py`, in the same commit.
2. No task changes persisted facts, identifiers, or the export JSON.
3. Every fixed defect leaves a regression test in the same commit.
4. The deferred concerns of `AGENTS.md` still apply: no CDN, ARIA, or
   keyboard-navigation work.
5. Regenerated exports come from a copy of the lab application with a
   fresh index; the lab's own index is never reused.

## 4. Tasks

### V1. Regenerate the published exports

Rebuild the lab POC export with the current assets, then copy it to the
live model, as `AGENTS.md` requires.

- Files: `../systemlens-observability-lab/apps/supermarket-demo/architecture-ia.html`,
  `docs/models/simple-supermarket.html`.
- Precondition: the lab checkout has uncommitted changes to
  `architecture-ia.html` and `.systemlens/config.yml`. The product owner
  decides whether to keep or discard them before the file is overwritten.
- Acceptance: the drag and wheel probes of section 2 give 120 by 70 and
  about 1.2 around the pointer on the live model in Chrome and WebKit.

### V2. Detect a stale live model

Fail the default suite when the live model was built from older assets.

- Files: `tests/test_spec_conformance.py` or a new test next to it;
  optionally `src/systemlens/render/html_export.py` to embed an asset
  fingerprint in a `meta` tag.
- The test recomputes the fingerprint of the current assets and compares
  it with the one in `docs/models/simple-supermarket.html`.
- Acceptance: the test fails on the 2026-10-31 live model and passes
  after V1.

### V3. Trackpad pan and pinch zoom

Decision first: record in `docs/PDR.md` how a two-finger scroll behaves.
The proposed rule is that a wheel event with `ctrlKey` (pinch) or with a
whole-line delta zooms, and any other pixel-delta event pans by its
`deltaX` and `deltaY`. The alternative keeps every vertical wheel as zoom
and only adds horizontal pan.

- Files: `docs/PDR.md`, `docs/UX-UI.md` ("Camera and graph interaction"),
  `10-rebuild.js`, `55-code-flows.js`, `tests/test_browser_export.py`.
- Risk: a mouse wheel and a trackpad scroll both send pixel deltas in
  some browsers. The rule must name the fallback when the input device
  cannot be told apart.
- Acceptance: a browser test sends a horizontal wheel and a pinch wheel
  and asserts a pan and a zoom; a manual check on a Mac trackpad is
  recorded, else the task is `blocked: no trackpad check`.

### V4. One zoom step for both graph surfaces

Align the call-tree wheel step with the Architecture graph step.

- Files: `55-code-flows.js`, `docs/UX-UI.md` ("Selected call tree"),
  `tests/test_browser_export.py`.
- Acceptance: one wheel notch scales the call tree by the same factor as
  the Architecture graph, within 0.2 percent, around the pointer.

### V5. A drag never toggles a toolbar control

Treat a pointer gesture that moved more than three pixels as a drag on
the type chips, as the cards already do.

- Files: `20-controls.js`, `tests/test_browser_export.py`.
- Acceptance: a drag that starts on a chip leaves every filter unchanged.

### V6. Keep the camera on window resize

Refit only when the graph was never moved by the user; otherwise keep
the graph point at the workspace centre fixed.

- Files: `60-bootstrap.js`, `docs/UX-UI.md` ("Camera and graph
  interaction"), `tests/test_browser_export.py`.
- Acceptance: after a pan, a resize from 1440 to 1300 pixels keeps the
  card under the workspace centre within two pixels; an untouched graph
  still refits.

### V7. Keep the minimap on screen once the user navigates

The minimap hid whenever every node centre was back inside the graph area,
so it blinked during a pan and vanished on small graphs. PDR-6 records the
new rule: it stays visible after any user pan or zoom until the next fit.

- Files: `docs/PDR.md`, `docs/UX-UI.md`, `00-core.js`, `10-rebuild.js`,
  `20-controls.js`, `55-code-flows.js`, `60-bootstrap.js`,
  `tests/test_browser_export.py`.
- Acceptance: a browser test pans a nine-node graph back and forth and
  asserts the minimap never hides, then hides after `Tout le graphe`.
- Status: see the delivery record below.

## 5. Delivery lots and validation

| Lot | Tasks | Why this order |
|---|---|---|
| 1 | V1, V2 | Ships the existing fix to the pages people open, and keeps it shipped. |
| 2 | V4, V5 | Local defects without a contract decision. |
| 3 | V3, V6 | Change a contract rule; V3 needs its PDR entry first. |
| Fix | V7 | Requested directly by the product owner on 2026-11-01. |

Each task runs:

```bash
uv run ruff check src tests
uv run pytest tests/test_render.py tests/test_spec_conformance.py
uv run pytest -m slow tests/test_browser_export.py
```

Each lot closes with `uv run mypy`, `uv run pytest`, and
`uv run python scripts/check_companion_contracts.py`.

## 6. Delivery record

| ID | Outcome |
|---|---|
| V7 | Delivered on 2026-11-01. A user pan, wheel zoom, zoom control, or minimap move sets a flag per surface that keeps the minimap visible; every fit clears it. PDR-6 and `docs/UX-UI.md` record the rule. Verified by `test_minimap_stays_visible_once_the_user_navigates`, which fails on the previous assets; the browser suite (49 tests) and the other slow tests passed with Chrome; default suite, ruff, and mypy clean. A slow pan on a fresh lab export kept the minimap on screen at every step. Deviation: none. The published live model still needs V1 to show the fix. |

## 7. Items carried over from earlier backlogs

Their context lives in Git history before `fdac92e`. Statuses follow
`docs/process/delivery-practices.md`.

| Item | Status |
|---|---|
| Promote the read-modify-write and dual-write review aids out of `export microservices --review-aids`. | `blocked: no second Spring application in the workspace for the precision measurement` |
| Field validation of runtime percentiles, the Prometheus source, and completeness facts (R1, R4, R5). | `blocked: lab Elastic cluster and Prometheus server unreachable from the delivery machine` |
| Regenerate `docs/models/simple-supermarket.html` with the hotspot group and the badge facts. | `blocked: needs a lab reindex plus a runtime collection`; V1 regenerates the static part only |
| Make the lab reachable from a scheduled CI job so the three items above can leave `blocked`. | `open`; depends on the lab repository's compose setup |
| Regenerate the live model in the `pages.yml` pipeline, as `supermarket.html` already is. | `open`; would make V2 redundant once delivered |

## 8. Archived backlogs

| Document | Purpose |
|---|---|
| [Archived consistency backlog](BACKLOG.consistency-diagnostics.md) | Historical diagnostic rendering tasks. |
| [Earlier backlog](BACKLOG.previous.md) | Historical index of data-operation work. |
