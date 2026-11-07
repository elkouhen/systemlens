# Backlog

Status: reviewed item by item by the product owner on 2026-11-07, who
kept the twelve items below, all `open` and all P1, and dropped the
others (Appendix B). Owner: SystemLens implementer. Reviewer: product
owner. Baseline: commit `4a12ad5`.

This backlog replaces the ten files of `docs/plans/`, the 2026-11-06
review backlog, `BACKLOG.previous.md` and
`BACKLOG.consistency-diagnostics.md`. Those files remain in Git at
`4a12ad5`. An item names its origin as `<file>: <old ID>`, so its full
history stays one `git show 4a12ad5:<file>` away.

Items from the 2026-11-03 and 2026-11-04 reviews were checked against
the Git log and the code at `4a12ad5`: no later commit names them, and
the cited code was not rewritten. Their line numbers date from the review
baseline named in the item and may have drifted. A measure dated before
2026-11-07 was not repeated.

Statuses follow
[`docs/process/delivery-practices.md`](docs/process/delivery-practices.md).
A decision record named under "Depends on" is validated before any code.
Item IDs keep their numbering from the reviewed draft; the gaps are the
dropped items.

Reference projects: train-ticket at `~/examples/train-ticket`, the lab
application `apps/supermarket-demo`, and
`sample-spring-microservices-new` at `d7bf01c` ("the sample").

## Summary

| ID | Title | Priority | Status |
|---|---|---|---|
| [BL-01](#bl-01) | Keep a dispatch `exact` only when its implementor set is complete | P1 | `open` |
| [BL-02](#bl-02) | Use the Feign client name as the HTTP peer | P1 | `open` |
| [BL-04](#bl-04) | Attribute inherited request mappings to the implementing type | P1 | `open` |
| [BL-06](#bl-06) | Apply gateway path filters before route matching | P1 | `open` |
| [BL-08](#bl-08) | Scope a RestClient `baseUrl` to its own client | P1 | `open` |
| [BL-09](#bl-09) | Type Kafka payload variables in their own scope | P1 | `open` |
| [BL-11](#bl-11) | Persist one flow set whatever the engine or command | P1 | `open` |
| [BL-12](#bl-12) | Reconcile an OUT followed by another OUT | P1 | `open` |
| [BL-14](#bl-14) | Resolve calls on untyped lambda parameters | P1 | `open` |
| [BL-16](#bl-16) | Use one zoom step on both graph surfaces | P1 | `open` |
| [BL-17](#bl-17) | Keep the camera on window resize | P1 | `open` |
| [BL-18](#bl-18) | Pan with a trackpad scroll and zoom with a pinch | P1 | `open` |

## P1: false facts and graph defects

Each item makes the index assert a wrong route, edge or confidence, or
hides a fact a reader of the graph expects, on a reference project or a
common idiom; or it breaks navigation of the graph itself. None needs the
lab.

<a id="bl-01"></a>
### BL-01. Keep a dispatch `exact` only when its implementor set is complete

- **Problem:** both engines mark a call `exact` on the one written body
  when other implementors exist without a source body.
- **Impact:** a false `exact` edge raises a flow's confidence on a wrong
  route; a `@FeignClient` call reaches its fallback class instead of HTTP.
- **Cause:** at `9006b7b`. S9: an override whose parameters cannot be
  resolved is invisible to dispatch (`java_symbols.py:317`,
  `implementations` `:499-525`). S10: record accessors, Lombok getters,
  bodies inherited from an external class and `Object` methods are not
  implementors (closed-world rule `:1460-1466`). Q6: a user-built CodeQL
  database includes `src/test` (`queries.py:30-32`). Q8: the Feign
  `fallback` class becomes the exact target. Q11: `equals`/`hashCode` on
  an `Object` receiver fan out to every override (39 in one module on
  train-ticket).
- **Acceptance:** one regression test per finding, each failing at
  `4a12ad5`; the train-ticket change in edges and flow confidences is
  recorded, and any lost flow is explained.
- **Depends on:** ADR-81 (proposed).
- **Origin:** `docs/plans/call-graph-review-2026-11-04.md`: lot 3 (S9,
  S10, Q6, Q8, Q11).
- **Status:** `open`.
- **Why P1:** false `exact` edges; Feign fallbacks are a common Spring
  idiom.

<a id="bl-02"></a>
### BL-02. Use the Feign client name as the HTTP peer

- **Problem:** the `name` or `value` of `@FeignClient` never names the
  target service.
- **Impact:** on the sample, 4 of 4 Feign calls stay external, and the
  cross-service chain of flows breaks.
- **Cause:** `scanner/rest_mvc.py`, `_ast_feign_base` resolves only `url`
  and `path` ("without treating `name` as a route").
- **Acceptance:** on a fixture and on the sample, a Feign call names the
  service whose `spring.application.name` equals the client name; an
  unknown name stays external; the sample's chained flows join.
- **Depends on:** none.
- **Origin:** `docs/plans/indexing-case-analysis.md`: P3, F6.
- **Status:** `delivered`.
- **Delivery:** `tests/test_rest_peer_regressions.py::test_bl02_*` (six
  tests; five fail at `43c91f4`). On the sample, Feign calls with a target
  went from 0 to 4 of 4, `calls_service` relations from 2 to 6, rest graph
  edges from 2 to 6, flows reconciled `complete` from 0 to 4 of 4, and
  joined flow pairs from 0 to 1
  (`OrganizationController.findByIdWithDepartmentsAndEmployees` to
  `DepartmentController.findByOrganizationWithEmployees`). Feign joins the
  route-declaring clients, so a dynamic Feign route is never projected onto
  every host route. commit: pending.
- **Why P1:** Feign is the most common inter-service client in Spring.

<a id="bl-04"></a>
### BL-04. Attribute inherited request mappings to the implementing type

- **Problem:** mappings declared on an interface or a base class belong
  to the declaring file.
- **Impact:** the implementing controller serves nothing, a base-class
  route has no prefix, and a Feign client extending the interface emits
  no call.
- **Cause:** `rest_mvc.py:454-496`, `:554-576` (at `224dcfd`).
- **Acceptance:** fixtures for the three shapes give the route on the
  implementing controller with its prefix, and the call on the Feign
  client.
- **Depends on:** none.
- **Origin:** `docs/plans/indexing-review-2026-11-03.md`: H8.
- **Status:** `delivered`.
- **Delivery:** `tests/test_rest_peer_regressions.py::test_bl04_*` (seven
  tests: the three shapes, the inherited type-level prefix, a declaring
  type without inheritor, and two incremental cases; all but the
  no-inheritor case fail at `43c91f4`). New `scanner/rest_inheritance.py`
  index; an inherited route without override sits on the inheritor's type
  declaration, so no code flow starts from it. Neither the sample nor
  train-ticket has the shape: their REST endpoints are unchanged (0
  inheritance links; the index costs 0.16 s on train-ticket).
  commit: pending.
- **Why P1:** contract-first and shared-API interfaces are common.

<a id="bl-06"></a>
### BL-06. Apply gateway path filters before route matching

- **Problem:** only `StripPrefix` is applied, and YAML gateway routes lose
  facts.
- **Impact:** on the sample, `/organization/**` matched `/{id}/...` by
  coincidence; `uri: http://...` routes are dropped.
- **Cause:** `RewritePath`, `PrefixPath` and `SetPath` are ignored; long-form
  `StripPrefix` and Java DSL `stripPrefix` are ignored; a quoted `uri`
  gets line 1 (`rest_mvc.py:1650-1666`, `:1715-1755` at `224dcfd`).
- **Acceptance:** fixtures for each filter match the rewritten path; an
  `http://` YAML route is kept with its line; the sample's gateway
  matches only rewritten routes.
- **Depends on:** none.
- **Origin:** `docs/plans/indexing-case-analysis.md`: C8, P7;
  `docs/plans/indexing-review-2026-11-03.md`: H13.
- **Status:** `delivered`.
- **Delivery:** `tests/test_rest_peer_regressions.py::test_bl06_*` (seven
  tests, all failing at `43c91f4`). On the sample, the six gateway call
  topics went from `ANY /employee/**`, `ANY /department/**` and
  `ANY /organization/**` to `ANY /**` after `RewritePath`; the two
  `http://localhost` api-docs routes are now kept (`ANY /**/v3/api-docs`,
  lines 42 and 43). Gateway `calls_service` relations went from 2 (to
  `organization-service`, by coincidence) to 6 (to each of the three
  services, from both gateway files). A rewrite that depends on a pattern
  segment makes the call `<dynamic>` with no relation. The gateway files
  still belong to `config-service`, which serves them. commit: pending.
- **Why P1:** wrong gateway-to-service relations on a reference project.

<a id="bl-08"></a>
### BL-08. Scope a RestClient `baseUrl` to its own client

- **Problem:** a file-wide unique RestClient `baseUrl` host is applied to
  every client of the file, and its path is dropped.
- **Impact:** calls of other clients point to the wrong service.
- **Cause:** `rest_mvc.py:1445-1456` (at `224dcfd`).
- **Acceptance:** a fixture with two clients in one file gives each call
  its own host and base path.
- **Depends on:** none.
- **Origin:** `docs/plans/indexing-review-2026-11-03.md`: H9.
- **Status:** `delivered`.
- **Delivery:** `tests/test_rest_peer_regressions.py::test_bl08_*` (three
  tests; the two acceptance fixtures fail at `43c91f4`). Two clients in
  one file now each get their own host and base path; a client bound to
  several bases, or a URI with an unresolved leading part, takes no base.
  Neither the sample nor train-ticket uses RestClient: no measured change.
  commit: pending.
- **Why P1:** a false HTTP relation; local fix.

<a id="bl-09"></a>
### BL-09. Type Kafka payload variables in their own scope

- **Problem:** payload typing misses `instanceof` and `switch` pattern
  variables, try-with-resources resources and anonymous-class fields,
  and takes an outer field of the same name instead.
- **Impact:** a wrong message type blocks or falsifies producer-consumer
  pairing.
- **Cause:** `scanner/kafka_payload_types.py:454` (at `224dcfd`).
- **Acceptance:** one fixture per shape gives the declared type or
  unknown, never the outer field's type.
- **Depends on:** none.
- **Origin:** `docs/plans/indexing-review-2026-11-03.md`: K5.
- **Status:** `open`.
- **Why P1:** a false fact that ADR-76 pairing trusts.

<a id="bl-11"></a>
### BL-11. Persist one flow set whatever the engine or command

- **Problem:** the engines and `flows calculate` persist different flow
  sets and identifiers for the same inputs.
- **Impact:** F4: a method with several effects gives different flows per
  path. F5: a scheduled flow is persisted twice, because the syntax tree
  and the transient trigger label it differently. F12: `flows calculate`
  ignores the index configuration (edge-confidence setting, repository
  root for key tracing, `after_commit` phase), and `--module` replaces
  syntax-tree flows instead of rebuilding them.
- **Cause:** at `224dcfd`: `call_graph_stage.py:685-691`,
  `application/flow_calculation.py:77-110`, `code_flows.py:342`,
  `integration_methods.py:181`. `b10cb94` shared the persisted-input
  loader (D1) but not the configuration.
- **Acceptance:** on a fixture with a scheduler and a multi-effect method,
  `index` with each engine and `flows calculate` persist identical flow
  identifiers; a test asserts that `flows calculate` applies the
  configured edge confidence.
- **Depends on:** none.
- **Origin:** `docs/plans/indexing-review-2026-11-03.md`: F4, F5, F12.
- **Status:** `open`.
- **Why P1:** duplicate and divergent flows are visible in every export.

<a id="bl-12"></a>
### BL-12. Reconcile an OUT followed by another OUT

- **Problem:** an OUT followed by another OUT of the same module is never
  reconciled.
- **Impact:** an `IN → OUT1 → OUT2` flow is `complete` when OUT1 has no
  consumer.
- **Cause:** `indexing/code_flows.py:1758` (at `224dcfd`).
- **Acceptance:** a regression test gives that flow the status the spec
  defines for an unmatched OUT.
- **Depends on:** none.
- **Origin:** `docs/plans/indexing-review-2026-11-03.md`: F2.
- **Status:** `open`.
- **Why P1:** a false `complete` status.

<a id="bl-14"></a>
### BL-14. Resolve calls on untyped lambda parameters

- **Problem:** a call on a lambda parameter without a written type gives
  no edge in the `source` engine.
- **Impact:** on the sample, 8 edges are found by CodeQL only.
- **Cause:** the symbol pass does not infer the parameter type from the
  functional interface the lambda targets.
- **Acceptance:** a fixture of the sample's shape gives the edge; the
  sample's `compare-engines` gap closes for those 8 sites.
- **Depends on:** none.
- **Origin:** `docs/plans/indexing-case-analysis.md`: F2.
- **Status:** `open`.
- **Why P1:** lost edges of the default engine on a reference project.

<a id="bl-16"></a>
### BL-16. Use one zoom step on both graph surfaces

- **Problem:** one wheel notch zooms the call tree by 1.06 and the
  Architecture graph by 1.2.
- **Impact:** the two surfaces react differently to the same gesture.
- **Cause:** wheel handler in `55-code-flows.js`.
- **Acceptance:** a browser test scales both by the same factor, within
  0.2 percent, around the pointer; `docs/UX-UI.md` ("Selected call
  tree") states it.
- **Depends on:** none.
- **Origin:** `docs/plans/graph-viewport-navigation.md`: V4.
- **Status:** `open`.
- **Why P1:** a reported navigation defect; no contract change.

<a id="bl-17"></a>
### BL-17. Keep the camera on window resize

- **Problem:** a resize that changes the toolbar height refits the graph.
- **Impact:** the user's pan and zoom are lost; the contract protects
  them only for the details panel.
- **Cause:** `refreshToolbarReservation` in `60-bootstrap.js`.
- **Acceptance:** after a pan, a resize from 1440 to 1300 pixels keeps the
  card under the workspace centre within two pixels; an untouched graph
  still refits.
- **Depends on:** the rule in `docs/UX-UI.md` ("Camera and graph
  interaction"), validated first.
- **Origin:** `docs/plans/graph-viewport-navigation.md`: V6.
- **Status:** `open`.
- **Why P1:** a reported navigation defect.

<a id="bl-18"></a>
### BL-18. Pan with a trackpad scroll and zoom with a pinch

- **Problem:** a horizontal wheel delta does nothing, and a vertical
  two-finger scroll zooms instead of panning.
- **Impact:** on a Mac trackpad the graph cannot be panned without a
  click and drag.
- **Cause:** `handleGraphWheel` in `10-rebuild.js`; `55-code-flows.js`.
- **Acceptance:** a browser test sends a horizontal wheel and a pinch
  (`ctrlKey`) wheel and asserts a pan and a zoom; a manual check on a
  Mac trackpad is recorded, else the item is `blocked: no trackpad
  check`.
- **Depends on:** a PDR entry for two-finger scroll. Proposed rule: a
  pinch or a whole-line delta zooms, any other pixel delta pans. It must
  name the fallback when mouse and trackpad cannot be told apart.
- **Origin:** `docs/plans/graph-viewport-navigation.md`: V3.
- **Status:** `open`.
- **Why P1:** the product owner reported that viewport navigation does
  not work; last of P1 because it needs a decision and hardware.

## Appendix A. Dropped as delivered or superseded

| Origin | Items | Evidence |
|---|---|---|
| 2026-11-06 review backlog | J1 to J6, P1 to P9, A1 to A6, C1 to C5 | `e419154`, `1d88e4d`, `6bca925`, `e424c6c`, `d1ecf2a`, `8458ee1`; each with its regression test in that file's section 5 |
| `call-graph-review-2026-11-04.md` | Q1, Q5; S1, S2; Q3, Q4; S3, S4; S5, S6, S11, S14; S7, S8; S12; Q2, Q7; D1 to D4; Q10; D6; Q9 (CodeQL join); S13; Q12; D7 | `32571f6`, `ac8183b`, `ea7d439`, `6f1648f`, `b844bd9`, `b81f7ab`, `df27f26`, `55fa1cb`, `4dd1cb8`, `b10cb94`, `1fcb3e6`, `ca0eafa`, `569ba9e`, `074b0e9`, `967b760`, `cd37c2d`; tests in `tests/test_codeql_review_regressions.py` and `tests/test_source_engine.py` |
| `call-graph-review-2026-11-04.md` | D5 (= F14), D8 (= C6) | `b10cb94`; `6bca925` (J2) |
| `indexing-review-2026-11-03.md` | I10, I1, I14, H1, H2, H4, H5, K1, K2, K3, K4, K6, K7, F1, F7, F8, I2, I3, I4, I6, I7, I9 | `68517b0`, `d934618`, `0203dc5`, `b227619`, `9b7c824`, `53c02fe`, `0188929`, `915706c`, `9c92a43`, `a9ee304`, `8af758e`, `9d495ce`, `74789a1` |
| `indexing-review-2026-11-03.md` | F14 (flow reason names its weakest hop) | `b10cb94` |
| `call-graph-coverage.md` | K0, K1, K2, K7; shapes H9 and all `covered` rows | `afac00e`, `3965e74`, `8458ee1`; the shape table moved to `docs/spec-tech/indexing.md` |
| `call-graph-external-dispatch-scope.md` | C0 to C6 | `2e42b93` (ADR-71), `6bca925` (C6 with J2), `8b24861`; C4 checked, no defect |
| `call-graph-external-dispatch-scope.md` | C4 note: a consumer of `codeql_call_graph.edges` must deduplicate | Superseded by A4 (`6bca925`): the witness adds no second edge |
| `graph-viewport-navigation.md` | V7, V8 | `5e8528e`, `58d6c28` |
| `resource-inspector-uniformisation.md` | R1 to R6, PDR-10 amendments | `327e607`, `680f02c`, `8c3b754`, `3e82ebf`, `41e78c1`; regeneration dropped (Appendix B, BL-42) |
| `runtime-observations-in-export.md` | R0 to R5 | `0fbef73`, `7995fa9`, `d61554a`; validated on the lab Elastic 9.5 cluster on 2026-10-31 |
| `data-operations-in-internal-flows.md` | 4.1 to 4.7, section 5 (JDBC) | ADR-64; `525073d`, `6c22d4a`, `85a3783`; section 6 dropped (Appendix B, BL-44) |
| `BACKLOG.previous.md` | Persistence, JPA, JDBC rows | Same as the data-operations row; lost-update row dropped (Appendix B, BL-44) |
| `async-write-read-consistency.md` | Sections 3.1 to 3.4 | `044bc07` (ADR-59), `cef857b`, `d0562a4`, `61341f3`; section 4 dropped (Appendix B, BL-38) |
| `indexing-case-analysis.md` | `verified` and `specified` rows; KP14, KP16 | No task: KP14 and KP16 are unchanged by decision (ADR-76); a `specified` row becomes an item only when a measure finds a defect |
| `call-graph-coverage.md` | ADR-73's typed-engine part | Delivered by the 2026-11-06 amendment of ADR-71 (J2, `6bca925`); the rest was BL-20, dropped (Appendix B) |

## Appendix B. Dropped by the product owner on 2026-11-07

The product owner dropped these items during the item-by-item review.
Each came from one of the deleted plans or backlogs, which remain in Git
at `4a12ad5` with the findings' full context.

| ID | Title |
|---|---|
| BL-03 | Keep the host of a local base URL with a dynamic part |
| BL-05 | Give gateway and config-server files to the service they configure |
| BL-07 | Scope `@Value` lookups to the module and the incremental delta |
| BL-10 | Read Spring Cloud Stream destinations |
| BL-13 | Dispatch calls on external classes to source overrides |
| BL-15 | Never toggle a toolbar chip at the end of a drag |
| BL-19 | Keep dispatch `possible` when an implicit implementation exists |
| BL-20 | Scope source-type dispatch and duplicate types by module |
| BL-21 | Resolve generic implementations and pattern variables |
| BL-22 | Add Spring-mediated call edges and entry points |
| BL-23 | Count flow alternatives after deduplication |
| BL-24 | Report an OUT dropped by the hop limit |
| BL-25 | Keep two data operations of one name on one line |
| BL-26 | Keep flow identifiers stable on cosmetic edits |
| BL-27 | Cover the missing HTTP route and client forms |
| BL-28 | Cover the missing Kafka producer and consumer forms |
| BL-29 | Fail the suite when the live model is stale |
| BL-30 | Regenerate the live model in the Pages pipeline |
| BL-31 | Make the lab reachable from a scheduled CI job |
| BL-32 | Bound indexing time on large repositories |
| BL-33 | Keep same-line overloads apart in the source join |
| BL-34 | Check the join resume after a module-checkpoint interruption |
| BL-35 | Report a locked index cleanly after the index transaction |
| BL-36 | Decide whether an explicit engine choice may fall back |
| BL-37 | Inspect several consistency diagnostics in one graph |
| BL-38 | Derive consistency evidence from the sources |
| BL-39 | Decide the scope of the absent indexing cases |
| BL-40 | Show observed activity beside the static architecture |
| BL-41 | Give runtime hotspots a Kubernetes capacity context |
| BL-42 | Regenerate the published live model |
| BL-43 | Validate runtime percentiles, Prometheus and completeness in the field |
| BL-44 | Promote the read-modify-write and dual-write review aids |
