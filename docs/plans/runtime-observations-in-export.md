# Runtime observations in the HTML export

Status: delivered, and validated on the lab's Elastic 9.5 cluster.
Owner: SystemLens implementer. Reviewers: product owner and architecture
maintainer. This plan adds persisted Elastic APM observations (ADR-60) to
the `systemlens export microservices` HTML graph.
The [runtime observations specification](../spec-tech/runtime-observations.md)
owns collection and reconciliation. [UX-UI](../UX-UI.md) owns presentation.

## 1. Goal

Let a reader of the architecture graph see, without leaving it, which
indexed integrations carried traffic during the collection window, which
did not, and which runtime calls the index does not explain. Static facts
stay the primary view. Runtime data is a labelled overlay with its window,
its source, and its limits always visible.

## 2. Current state and gaps

`systemlens observe report` reconciles observations in the terminal only.
The export does not read `runtime_observations`.

Three gaps block a faithful overlay:

- Reconciliation rows name a service and a target but not the matched
  static endpoint IDs or REST edge. The export cannot attach counts to a
  port, an arc, or a card without guessing again.
- The `--service-map` entries are CLI arguments. They are not persisted, so
  an export cannot reproduce the mapping that the report used.
- The view model has no runtime block, and the UI has no overlay state.

## 3. Scope and invariants

- The export reads persisted observations only. It never contacts
  Elasticsearch and needs no network access.
- Observations never create, confirm, or remove a static node or edge.
  The first increment draws no new graph edge for `observed_only` traffic.
  It lists such traffic in a dedicated panel instead.
- An index without observations exports the same embedded snapshot as
  before. No runtime control or data appears in that case.
- Counts are lower bounds that depend on sampling and the APM version. The
  UI states this wherever it shows a count.
- `static_only` means "no traffic in the window", never "dead code". A
  service without telemetry is shown differently from a silent integration.
- The cluster origin may appear in the export. API keys and URLs with
  credentials never do; they are not persisted in the first place.

Out of scope: latency percentiles (only sums are collected), time series,
live refresh, trace links to Kibana, and the deferred HTML concerns listed
in `AGENTS.md` (CDN, ARIA, keyboard navigation).

## 4. Delivery order

| ID | Deliverable | Prerequisites |
|---|---|---|
| R0 | Decisions: service-map persistence, default visibility, edge policy | None |
| R1 | Reconciliation rows carry static evidence keys | R0 |
| R2 | Runtime block in the export payload | R1 |
| R3 | Overlay, badges, details, and runtime gaps panel | R2 |
| R4 | Documentation, ADR, and companion skill | R0, updated per increment |
| R5 | Recorded validation | Each increment |

## 5. Work packages and acceptance criteria

### R0. Decide the open points

Record the decisions as an ADR-60 consequence or a new ADR. Recommended:

- Persist the service map as `runtime.service_map` in the project config
  (`APM name: indexed service`). `--service-map` overrides it per command.
  The map holds names only, never secrets.
- The overlay is off by default. A legend toggle appears only when the
  index holds observations.
- No `observed_only` edge is drawn in this plan. Drawing one later needs a
  distinct visual grammar and its own decision.

Acceptance:

- `observe report` and `export microservices` resolve services identically
  from the same config, verified by a test.
- Config validation rejects a map value that is not a string.

### R1. Attach static evidence to reconciliation rows

File: [reconciliation](../../src/systemlens/application/runtime_observations.py).

Add `endpoint_ids` to each `matched` row: the producer, consumer, or server
endpoints that justify the match. Add `edge` (`source`, `target` services)
to `matched` REST destination rows. Add `endpoint_ids` to `static_only`
items. Changes are additive to the JSON report.

Acceptance:

- A test covers each rule of the reconciliation table and asserts the
  exact endpoint IDs, including several endpoints with one normalized route.
- Existing `observe report --json` fields keep their names and values.

### R2. Add the runtime block to the export payload

Files: `_load_microservice_graph` in the [CLI](../../src/systemlens/delivery/cli.py),
the [HTML export](../../src/systemlens/render/html_export.py), and the
[view model](../../src/systemlens/render/graph_view_model.py).

Load observations from the `Store` with the other persisted facts, reconcile
them once, and pass the result as a `runtime_observations` option. The view
model emits one `runtime` block:

- `window`: origin, start, end, and collection time.
- `summary`: counts per status.
- `endpoints`: per endpoint ID, calls, failures, and status.
- `edges`: per REST service pair, calls and failures.
- `services`: per indexed service, `observed`, `silent`, or `no_telemetry`.
- `gaps`: `observed_only` and `external` rows with their hints, plus
  unresolved APM service names.

Render receives the reconciled data from the delivery adapter, like the
other persisted facts, and keeps the layering that ARCHITECTURE.md defines.

Acceptance:

- Without observations, the payload has no `runtime` key and every other
  key is identical to an export with observations.
- A view-model test on the lab-like inventory asserts the block contents.
- The block size is linear in observations plus indexed endpoints.

### R3. Present the overlay

Files: graph assets under `src/systemlens/render/assets/graph/`.

When the toggle is on:

- Arcs with traffic show call counts and a failure ratio.
- In a selected call tree, cards show their entry-point count. Arcs list
  emitted and received counts apart, since REST counts measure a pair.
- Silent integrations (`static_only`) are dimmed with a "no traffic in the
  window" marker. Services without telemetry get a separate neutral marker.
- Service details gain a runtime section listing its observed, silent, and
  unexplained integrations.
- A "Runtime gaps" panel lists `observed_only`, `external`, and unresolved
  services, with the dynamic-output hint when present.
- A header line shows the window, its age, and the origin.

Turning the toggle off restores the static view exactly. The overlay must
not change layout, camera, selection, or the selected call flow. The flow
toolbar mirrors the toggle, since the legend is hidden outside the graph.

Acceptance:

- A slow browser test toggles the overlay on the lab-like export, checks
  one counted arc, one silent port, and one gap entry, then checks that
  camera and selection are unchanged.
- The display stays readable in both themes and at a narrow viewport.

### R4. Update the documentation

Update in the same change as each increment: [UX-UI](../UX-UI.md) (the
tooltip rule currently forbids extra facts; add the runtime exception),
the [runtime specification](../spec-tech/runtime-observations.md) with an
"Export" section, the [CLI reference](../spec-fonc/cli.md) and `SPEC-FONC`,
`docs/README.md`, and ADR-60. Update `../systemlens-skill/` if the agent
workflow or a documented JSON shape changes.

### R5. Validate

- `uv run ruff check src tests`, `uv run mypy`, and `uv run pytest`.
- The slow browser test with Chrome available.
- A fresh copy of the observability lab indexed, fed by the fake
  Elasticsearch used for ADR-60, then exported and inspected.
- `scripts/check_companion_contracts.py` when CodeQL and `scc` are
  installed; report it as not run otherwise.
- Against the lab's Elastic 9.5 cluster: done on 2026-10-31. Field names
  and composite queries work. Two corrections followed: interval detection,
  because only `1m` exists, and the `unverified` status, because HTTP
  transactions carry no route there.

## 6. Risks

- Field names and `_doc_count` semantics are still unverified on a real
  cluster; the overlay could show plausible but wrong counts.
- An old collection against a newer index can mislead. The age line and a
  warning when the index is newer than the collection mitigate this.
- Route normalization merges overloaded routes; counts then attach to
  every matching endpoint and must say so in the tooltip.
