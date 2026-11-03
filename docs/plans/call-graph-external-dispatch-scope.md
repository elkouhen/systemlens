# Plan: scope external-interface dispatch in the source engine

Status: validated on 2026-11-03 by the product owner, who asked for every
task and kept the recommendation of C1: a library never dispatches into a
module that depends on it. ADR-71 records the decision. Section 6 records
each task's state. Owner: SystemLens implementer. Reviewers: product owner and engine
maintainer.

Baseline: commit `184f145` (ADR-70) against its parent `7d5f709`. Fresh
indexes of copies of `~/examples/train-ticket` and of the lab application
`apps/supermarket-demo`, built with `index --full`. No persisted index was
reused.

Parent rules: [ADR-70](../ADR.md#adr-70-give-the-source-engine-codeqls-interface-witness-and-dispatch-gaps),
[indexing specification](../spec-tech/indexing.md), invariant 4 of
[AGENTS.md](../../AGENTS.md) (conservative extraction).

## 1. Problem

The product owner reports a large regression in call-graph construction.
ADR-70 lets a call on an interface outside the sources, such as
`ExecutorService`, `Callable`, or Spring's `UserDetails`, dispatch to the
source classes that implement it. The candidate classes come from every
indexed module. The symbol pass does not know the dependencies between
Maven or Gradle modules, so it links modules that never share a classpath.

ADR-70 also marks this dispatch `exact` when one source body exists. An
external interface usually has library implementations the sources do not
contain, so a single source body proves nothing.

## 2. Baseline

Measured on 2026-11-03 with the `source` engine unless stated.

| Project and engine | Edges before | Edges after | Flows before | Flows after |
|---|---|---|---|---|
| Lab, `source` | 59 | 87 | 23 | 23 |
| Lab, `codeql` | 94 | 94 | 23 | 23 |
| train-ticket, `source` | 2052 | 3008 | 406 | 407 |

No edge of the parent commit disappears, no exact edge degrades, and the
indexing time is unchanged. The default suite (702 tests) and
`scripts/check_companion_contracts.py` pass on `184f145`.

On train-ticket, `184f145` adds nine edges whose callee module is not a
transitive dependency of the caller module:

| Caller module | Callee module | Edges | Confidence | Example |
|---|---|---|---|---|
| `ts-travel-service` | `micro-service-monitoring-core` | 1 | `exact` | `TravelServiceImpl.queryInParallel` to `TEServ.submit`, line 272 |
| `micro-service-monitoring-core` | `ts-travel-service` | 4 | `possible` | `ChainCallable.call` to `TravelServiceImpl.MyCallable.call` |
| `micro-service-monitoring-core` | `ts-wait-order-service` | 3 | `possible` | `ChainRunnable.run` to `PollThread.run` |
| `ts-common` | `ts-auth-service` | 1 | `possible` | `JWTUtil.getJWTAuthentication` to `User.getAuthorities` |

Consequences found by the same measurement:

| Fact | Where | Consequence |
|---|---|---|
| `executorService` comes from `Executors.newFixedThreadPool`, a JDK pool. `TEServ` is a monitoring library under `old-docs/`, and `ts-travel-service` does not depend on it. | `ts-travel-service/.../TravelServiceImpl.java:49` and `:272` | A false exact edge enters an unrelated module. |
| `TEServ.submit` delegates to `submit`, which resolves to itself. | `old-docs/Lib/ms-monitoring-core/.../TEServ.java:84` | The only new flow, `TravelController.queryInfoInparallel`, is a false `cycle`. |
| `ts-auth-service` depends on `ts-common`, not the reverse. | `module_dependencies` table | A library method calls into one of the services that use it. |
| `_external_dispatch` scans `self.types` across all modules, and the symbol pass receives no module dependencies. | `src/systemlens/indexing/java_symbols.py:1141` | Any source implementor anywhere becomes a candidate. |
| A single candidate gets `exact` unless the interface is `Runnable` or `Callable`, or an anonymous class implements it. | `java_symbols.py`, dispatch rule after the external lookup | `ExecutorService.submit` is exact on one source body. |

The lab has no such case, which is why the companion check stays green.

C0 measured the `codeql` engine on a train-ticket copy (CodeQL database in
build mode `none`, 408 flows, 8264 edges). It has the same defect: 16
edges leave the caller's transitive dependencies, including the exact
`TravelServiceImpl.queryInParallel` to `TEServ.submit` edge and two exact
edges from `ts-verification-code-service`. It also reports the false
`queryInfoInparallel` cycle and an `imageCode` cycle. ADR-70 copied a
CodeQL artefact; C6 proposes the same correction for that engine.

## 3. Invariants

1. The decision of C1 is recorded in `docs/ADR.md` before any code change.
   It amends ADR-70.
2. No rule invents a target. Removing a dispatch edge is preferred to
   keeping a guess, as invariant 4 of `AGENTS.md` requires.
3. Persisted names, the `codeql_*` keys, and the export JSON keep their
   shape. Schema changes, if any, are additive.
4. Every fixed defect leaves a regression test in the same commit.
5. Measurements come from fresh indexes of copies of the reference
   projects.

## 4. Tasks

### C0. Measure the CodeQL behaviour on train-ticket

Build a CodeQL database for a copy of train-ticket and index it with the
`codeql` engine. Count the edges whose callee module is not a transitive
dependency of the caller module.

- Files: none; the result goes into section 2 of this plan.
- Acceptance: the plan states whether CodeQL produces the nine edges, so
  C1 knows whether ADR-70's parity claim covers them.
- Outcome: measured on 2026-11-03; see the end of section 2.

### C1. Decide the dispatch scope

Decision first: record ADR-71 in `docs/ADR.md`, amending ADR-70. The
proposed rule has two parts.

1. A call on an external interface dispatches only to source
   implementations in the caller module or its transitive dependencies.
2. That dispatch is always `possible`, even with one source body, because
   a library can provide the implementation.

Open point: a library calling back into a module that depends on it, such
as `ChainCallable.call` reaching a service's `Callable`. The proposal drops
these edges, because a flow entering the library from one service would
continue into another service's code. The alternative keeps them as
`possible` and lets the flow join filter them by classpath, which it
cannot do today.

- Acceptance: the product owner validates ADR-71 and settles the open
  point.
- Outcome: [ADR-71](../ADR.md#adr-71-scope-external-interface-dispatch-to-the-callers-visible-modules)
  keeps both parts and drops library callbacks into dependent modules.

### C2. Give the symbol pass the module dependencies

Pass the discovered module dependencies to the source symbol pass and
filter the candidates of `_external_dispatch` by the visible modules.

- Files: `src/systemlens/indexing/java_symbols.py`,
  `src/systemlens/indexing/call_graph_stage.py`,
  `tests/test_source_engine.py`, `docs/spec-tech/indexing.md`.
- Regression test: two modules without a dependency each implement
  `ExecutorService`; a call in the first module reaches only its own
  implementation. A second case covers the open point of C1.
- Acceptance: on train-ticket, no edge links a caller to a module outside
  its transitive dependencies, unless C1 keeps callbacks; the
  `queryInfoInparallel` cycle flow is gone.
- Outcome: delivered; see section 6.

### C3. Keep external-interface dispatch possible

Apply part 2 of C1: one source body on an external interface gives a
`possible` edge.

- Files: `src/systemlens/indexing/java_symbols.py`,
  `tests/test_source_engine.py`, `docs/spec-tech/indexing.md`.
- Regression test: a single source implementor of `ExecutorService`
  yields a `possible` edge.
- Acceptance: the test fails on `184f145` and passes after the change.
  The train-ticket flow count and flow confidences are recorded; a drop
  below 406 flows is reported to the product owner before delivery.
- Outcome: delivered; see section 6.

### C4. Check the witness duplicate in the exported call tree

ADR-70 keeps an exact edge to the implementation, adds a `possible`
inferred edge to the same implementation, and adds a `possible` witness to
the interface method. On the lab, every interface call site now has these
three edges.

- Files: none unless the check finds a defect; then
  `src/systemlens/indexing/code_flows.py` and `tests/test_code_flows.py`.
- Acceptance: a fresh lab export shows one arc per call site and
  implementation in the call tree. If it shows two, a proposed fix is added
  to this plan for validation.
- Outcome: no defect; see section 6.

### C5. Update ADR-70's measured figures

Re-measure train-ticket after C2 and C3, and record the new edge, flow,
and CodeQL-parity figures in ADR-71.

- Files: `docs/ADR.md`, `docs/spec-tech/indexing.md`.
- Acceptance: the figures match a fresh index of a train-ticket copy.
- Outcome: delivered; see section 6.

### C6. Apply the same scope to the `codeql` engine

Status: `proposed`; it extends ADR-71 to an engine the product owner did
not include. CodeQL dispatches over the whole database without the JDK
hierarchy, so it reproduces the nine edges and adds seven more. The
proposed rule drops, in the join of `code_flows.py`, any engine edge whose
callee module is outside the caller's module and transitive dependencies.

- Files: `docs/ADR.md` (ADR-71 amendment first),
  `src/systemlens/indexing/code_flows.py`, `tests/test_code_flows.py`,
  `docs/spec-tech/indexing.md`.
- Acceptance: on a train-ticket copy indexed with `codeql`, no edge leaves
  the caller's dependencies and the `queryInfoInparallel` cycle is gone.

## 5. Delivery lots and validation

| Lot | Tasks | Why this order |
|---|---|---|
| 1 | C0, C1 | C1 changes an accepted ADR and needs the CodeQL fact first. |
| 2 | C2, C3, C5 | Implement the validated rule and record its measured effect. |
| 3 | C4 | Independent of the defect; a check before any change. |
| 4 | C6 | Waits for the product owner to extend ADR-71 to the `codeql` engine. |

Each task runs:

```bash
uv run ruff check src tests
uv run pytest tests/test_source_engine.py tests/test_code_flows.py
```

Each lot closes with `uv run mypy`, `uv run pytest`, and
`uv run python scripts/check_companion_contracts.py`, plus the train-ticket
measurement of section 2.

## 6. Delivery record

| ID | Outcome |
|---|---|
| C0 | Delivered on 2026-11-03; result in section 2. |
| C1 | Delivered on 2026-11-03 as ADR-71. |
| C2 | Delivered on 2026-11-03. The stage passes the discovered module dependencies to `JavaSymbols`, which keeps an external-interface body only when its module is the caller's or a transitive dependency. Tests: `test_an_external_interface_dispatches_only_into_visible_modules`, `test_a_library_never_dispatches_into_a_module_that_depends_on_it`. |
| C3 | Delivered on 2026-11-03. External-interface dispatch is always `possible`. Test: `test_a_single_source_body_of_an_external_interface_stays_possible`; the ADR-70 test now expects `possible` for `Disk.save`. |
| C4 | Checked on 2026-11-03: no defect. See the C4 note below. |
| C5 | Delivered on 2026-11-03 in ADR-71 and `docs/spec-tech/indexing.md`. |

Verification: the three new tests failed on `274251e`, each for the
expected reason, and pass after the change. `ruff`, `mypy`, the default
suite (705 passed) and `scripts/check_companion_contracts.py` pass.

Train-ticket, fresh `source` index of a copy:

| Measure | `184f145` | After C2 and C3 |
|---|---|---|
| Edges | 3008 | 2999 |
| Exact / possible edges | 1874 / 1134 | 1844 / 1155 |
| Edges outside the caller's dependencies | 9 | 0 |
| Flows | 407 | 406 |
| Flow status and confidence | 406 `potential` `medium`, 1 `cycle` `medium` | 406 `potential` `medium` |

The 406 flows are the same set as on `7d5f709` and as the `potential`
flows of the `codeql` index. Besides the nine dropped edges, 29 exact
edges inside `micro-service-monitoring-core` become `possible`. The lab,
indexed fresh at `274251e` and after the change, keeps the same 87 edges
and 23 flows.

C4 note: a fresh lab export renders the call tree from flow steps and
flow transitions. No flow repeats a call site in its `method_call` steps.
The exported `codeql_call_graph.edges` holds 14 site pairs, each an exact
edge and the inferred `possible` edge to the same implementation, but
`55-code-flows.js` reads only that object's `nodes`. The pair is visible
only in the raw JSON; a future consumer of those edges must deduplicate
by caller, callee and site.

Deviation: `JavaSymbols` filters on the module of the body's method, not
of the subtype that inherits it, matching ADR-71's "implementations
declared in" a visible module.
