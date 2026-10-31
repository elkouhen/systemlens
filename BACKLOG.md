# Backlog: Java indexing and call-graph discovery corrections

Status: delivered on 2026-10-31; see section 10 for the delivery record.
Owner: SystemLens implementer. Reviewers: product owner and architecture
maintainer.
Review date: 2026-10-31, on commit `741670d`.

This backlog records the defects and coverage gaps found by a review of the
Java indexing pass and the call-graph discovery engines. The review covered
`indexing/java_symbols.py`, `indexing/code_flows.py`,
`indexing/integration_methods.py`, `indexing/service.py`,
`callgraph/`, `engines/codeql.py`, and `discovery/java/parser.py`.

The previous root backlog (asynchronous write and synchronous read
consistency extraction) was removed from this file on 2026-10-31. Its content
remains in Git history before that date, and its linked plan stays current:
[`docs/plans/async-write-read-consistency.md`](docs/plans/async-write-read-consistency.md).

## 1. Index and scope

| Document | Purpose |
|---|---|
| [Indexing contract](docs/spec-tech/indexing.md) | Authoritative call-graph and symbol-pass rules to update with each task. |
| [Algorithms](docs/spec-tech/algorithms.md) | Cross-cutting overview of the stages touched here. |
| [ADR-61](docs/ADR.md#adr-61-make-the-call-graph-engine-a-pluggable-choice) | Engine seam that every task must preserve. |
| [Archived consistency backlog](BACKLOG.consistency-diagnostics.md) | Historical rendering tasks. |
| [Earlier backlog](BACKLOG.previous.md) | Historical index of data-operation work. |

## 2. Review baseline

The baseline was established before any change:

- `uv run pytest tests/test_source_engine.py tests/test_call_graph_engines.py
  tests/test_codeql_inheritance.py tests/test_code_flows.py tests/test_codeql.py`:
  160 passed, 2 deselected.
- `uv run ruff check src tests` and `uv run mypy`: no findings.
- CodeQL was not executed. Findings about the CodeQL adapter rest on code
  reading and on fixtures that pass engine rows directly.

Resolution of the source engine on a copy of the lab application
(`../systemlens-observability-lab/apps/supermarket-demo`, 47 Java files,
73 methods) with `JavaSymbols.fallback_calls(closed_world=True)`:

| Invocation sites | Resolved | External receiver | Untyped receiver | No target on a source type |
|---|---|---|---|---|
| 231 | 54 | 74 | 70 | 33 |

External receivers are expected. Untyped receivers are mostly external
builders and static utilities. The 33 sites without a target on a source type
are record accessors (`result.orderId()`), Spring Data methods inherited from
`JpaRepository` (`repository.save(...)`), and exception getters inherited
from `Throwable`.

## 3. Confirmed defects

Each defect below was reproduced with a minimal fixture against the current
code. The reproduction fixtures should become the regression tests of the
corresponding task.

### D1. Scheduler flows to one publication site collapse into one flow

`_deduplicate_code_flows.flow_identity` builds its key from steps that carry
an `endpoint_id`. A `cron_entry` step has none, so two `@Scheduled` methods
calling the same publisher share the key of the publication step. With
`Scheduler.tickA` and `Scheduler.tickB` both calling `Publisher.publish()`,
only the `tickB` flow survives `materialize_call_graph_flows`.
Location: `src/systemlens/indexing/code_flows.py`, `flow_identity`.

### D2. Mongo steps take the module of the last discovered module

`materialize_code_flows` calls `_mongo_step(..., module_identity(module))`
where `module` is the leftover variable of the earlier `for module in modules`
loop, not the trigger's module. With modules `orders` and `zzz-other`, a
Mongo write in `orders` gets `resource_identity="mongo:zzz-other:orders"`.
The JPA branch of the same expression correctly uses `trigger.module`. The
scheduled branch has the same defect. This breaks resource matching in the
consistency diagnostics.
Location: `src/systemlens/indexing/code_flows.py`, both `_mongo_step` calls
inside `materialize_code_flows`.

### D3. Sibling overrides leak through a template method in another module

`dispatch_matches_entry` restricts dispatch from an inherited body to the
entry subtype only when `(current_owner, target_module)` is an ancestor of
the target. When the abstract base lives in a helper module (`core`) and the
consumers in `app`, the ancestor key carries module `core`, the check fails,
and the rule silently does not apply. Fixture: `Base.handle()` calling
abstract `process()`, `ConsumerA` and `ConsumerB` overriding it. Same module
yields 2 flows; cross-module yields 4, including `ConsumerA -> b.out`.
The existing test `test_inherited_template_dispatch_stays_on_entry_subtype`
covers only the single-module layout.
Location: `src/systemlens/indexing/code_flows.py`, `dispatch_matches_entry`.

### D4. `codeql_edge_confidence="exact"` drops every reachability relation

The reachability loop skips relations whose confidence is not `"exact"`,
but the CodeQL query reports `medium` (exact-only route) or `low`. A
`medium` relation therefore disappears under the `exact` setting. The
existing test covers only a `low` relation.
Location: `src/systemlens/indexing/code_flows.py`, reachability block of
`materialize_call_graph_flows`.

### D5. Anonymous class methods are indexed as methods of the enclosing type

`_qualified_method_owner` and `JavaSymbols.owner` walk up to the nearest
named type, so `new Runnable() { public void run() {...} }` inside
`Outer.start()` becomes `p.Outer.run`. Consequences on the fixture:

- `o.run()` resolves to two `possible` targets, the real `Outer.run` and the
  anonymous body, and loses its `exact` status.
- The anonymous body's call `worker.work()` is attributed to `p.Outer.run`
  and is disconnected from `Outer.start()`, so a flow through an executor
  or callback is lost.

Lambda bodies do not have this problem because a lambda is not a
`method_declaration`.
Location: `src/systemlens/indexing/integration_methods.py` and
`src/systemlens/indexing/java_symbols.py`.

### D6. A dispatch pruned by caller-module preference is reported `exact`

`possible_targets` keeps only the caller module's implementations of an
interface when one exists. In closed world (`source` engine) a single
remaining implementation is then marked `exact`. Fixture: `CoreAdapter` in
`core` and `AppAdapter` in `app` both implement `Port`; a call from `core`
yields `CoreAdapter.send` as `exact`. The pruning is a heuristic, not a
proof, so the persisted confidence overstates the evidence.
Location: `src/systemlens/indexing/java_symbols.py`, `possible_targets`
and the `dispatch` computation in `fallback_calls`.

## 4. Confirmed coverage gaps of the source engine

These are false negatives reproduced on fixtures. They do not persist wrong
facts, but they hide routes that the `source` engine should find.

### G1. Receivers declared outside `local_variable_declaration`

`variable_type` scans only parameters, local declarations, and fields. The
following declarations are ignored, so calls on them resolve to nothing:

- the variable of an enhanced `for (Order o : orders)`;
- a typed lambda parameter `(Order x) -> x.fail()`;
- a `catch (FooException e)` parameter;
- a try-with-resources resource.

### G2. Static method imports

`import static p.Util.helper;` is skipped by the unit import scan, so
`helper()` is looked up on the enclosing type and finds nothing.

### G3. Record components

A `record` component is an accessor method at runtime but not a
`method_declaration` in the AST, so `pair.order().process()` cannot type
its receiver. This is the largest group of unresolved sites on the lab.

## 5. Risks found by reading, not reproduced

- **Project-specific rule in the core.** The
  `AbstractKafkaMessageProcessor.consumeMessage` / `.processMessage`
  rule is hard-coded twice: `src/systemlens/indexing/code_flows.py`
  (`dispatch_matches_entry`) and `src/systemlens/application/code_flows.py`
  (`_call_graph_edge_allowed`). It names a lab application class.
- **Inconsistent module identity for placeholders.** `_build_call_graph`
  and the persisted CodeQL method projection in `indexing/service.py` derive
  the module from the first path segment, while indexed methods use
  `module_identity` (Maven artifact identity). A placeholder therefore never
  matches an application module and bypasses
  `within_application_boundary`.
- **Over-broad CodeQL source exclusion.** `_prepare_source_only_root`
  excludes any path containing a directory named `target` or `out`, so a
  Java package such as `com/acme/target/` never enters the CodeQL database.
  Correction found during delivery: the file inventory excluded such a
  `target` package too, so J11 fixed both.
- **Dead and quadratic code in `JavaSymbols`.** `_targets` is written and
  never read. `implementations()` scans the whole `dispatch` dictionary for
  each generic contract.
- **Repeated parsing.** `parse_java` caches 512 trees, and one flow rebuild
  parses the inventory in `materialize_integration_methods`,
  `scheduled_trigger_endpoints`, `JavaSymbols`, `materialize_code_flows`, and
  `trace_scalar_key_identity`. Repositories above the cache size parse each
  file several times.

## 6. Invariants for every task

- Keep the engine seam of ADR-61: the indexing core never imports an engine
  module, and `JavaSymbols` stays the shared pass for both engines.
- Never turn an ambiguity into a chosen target. A heuristic that prunes
  candidates lowers confidence; it does not raise it.
- Persist only repository-relative paths. Keep SQLite migrations additive.
- Bump `CODE_FLOW_SIGNATURE` in `indexing/code_flows.py` whenever a task
  changes persisted flows or call edges, so existing indexes rebuild.
- Update `docs/spec-tech/indexing.md` in the same change, and
  `docs/ADR.md` when a rule becomes a durable decision.
- Check the companion skill (`../systemlens-skill/`) when a task changes the
  documented agent workflow.

## 7. Delivery order and dependencies

| ID | Deliverable | Priority | Prerequisites |
|---|---|---|---|
| J1 | Scheduler anchors in flow deduplication (D1) | P0 | None |
| J2 | Trigger module for Mongo steps (D2) | P0 | None |
| J3 | Cross-module template dispatch (D3) | P0 | None |
| J4 | Reachability under `exact` edge confidence (D4) | P1 | None |
| J5 | Anonymous class bodies attributed to the enclosing method (D5) | P1 | None |
| J6 | Pruned dispatch stays `possible` (D6) | P1 | None |
| J7 | Enhanced-for, catch, resource, and typed lambda receivers (G1) | P1 | None |
| J8 | Static method imports (G2) | P2 | J7 |
| J9 | Remove the lab-specific dispatch rule from the core | P2 | J3 |
| J10 | One module identity for placeholders and projections | P2 | None |
| J11 | Module-rooted `target`/`out` exclusion in the CodeQL projection | P2 | None |
| J12 | Record components as accessors (G3) | P3 | J7 |
| J13 | `JavaSymbols` cleanup and dispatch index | P3 | J6 |
| J14 | Parse each Java file once per flow rebuild | P3 | None |
| J15 | Regression matrix on the lab application | P1 | J1-J3 |

J1 to J3 fix persisted facts and ship first, each as its own commit. J4 to
J7 change the confidence or coverage visible in exports. J15 records the
measured effect of each increment on the lab application.

## 8. Work packages and acceptance criteria

### J1. Scheduler anchors in flow deduplication

Make `flow_identity` in `_deduplicate_code_flows` use the same anchor as
`record_flow`: an endpoint ID when present, otherwise
`kind:path:start_line` for a `cron_entry` step. Keep the existing behavior
for flows without any anchor (key suffixed with the flow ID).

Acceptance:

- Two `@Scheduled` methods calling one publisher produce two persisted
  flows, one per scheduler, each with `alternative_count=1`.
- A scheduled method reached both as a same-method AST flow and as an
  interprocedural flow still yields one representative preferring
  `method_call` evidence.
- `CODE_FLOW_SIGNATURE` is bumped; `docs/spec-tech/indexing.md` states that
  deduplication anchors on the trigger, including transient scheduler
  triggers.

### J2. Trigger module for Mongo steps

Replace both `module_identity(module)` arguments of `_mongo_step` in
`materialize_code_flows` with the trigger's module (`trigger.module`) and
the scheduled flow's `owner_module`. Consider a keyword-only `module`
parameter on `_mongo_step` and `_jpa_step` to make the call sites symmetric.

Acceptance:

- A fixture with two discovered modules, where only the first owns a Mongo
  write, persists `resource_identity="mongo:<first module>:<collection>"`.
- The scheduled branch of the same function is covered by the same test.
- `tests/test_flow_consistency.py` still passes; resource matching across
  modules is unchanged for JPA.

### J3. Cross-module template dispatch

In `dispatch_matches_entry`, look up the module of `current` from the
persisted method (`current.module`) when building the ancestor key, and
compare ancestor owners by qualified name when modules differ. Reuse
`JavaSymbols.ancestors` with the module of the type that declares the
inherited body, which `JavaSymbols.path_modules` already records.

Acceptance:

- A cross-module variant of
  `test_inherited_template_dispatch_stays_on_entry_subtype` with the base in
  a helper module (`starts_application=False`) yields exactly one flow per
  consumer, each ending on its own output.
- The single-module test still passes.
- `docs/spec-tech/indexing.md` states that the entry-subtype restriction
  applies across build modules.

### J4. Reachability under `exact` edge confidence

Filter reachability relations with `relation.confidence != "medium"` when
`codeql_edge_confidence == "exact"`, or map the setting to the set of
accepted relation confidences in one place. Document that `medium` means an
exact-only route and `low` a route through possible dispatch.

Acceptance:

- A `medium` relation produces a direct flow under `exact`; a `low` relation
  is still dropped. Both cases are in `tests/test_code_flows.py`.
- The `engines/codeql.py` query docstring and `docs/spec-tech/indexing.md`
  name the two confidence values.

### J5. Anonymous class bodies attributed to the enclosing method

Treat a `method_declaration` whose nearest enclosing `class_body` belongs to
an `object_creation_expression` as part of the enclosing method, not as a
method of the enclosing named type. Two options to evaluate:

1. Skip anonymous methods in `materialize_integration_methods` and let
   `fallback_calls` walk their bodies as part of the enclosing method, as it
   already does for lambda bodies.
2. Index them under a synthetic owner (`Outer$1`) and add an `exact` edge
   from the enclosing method to the anonymous body.

Option 1 matches how CodeQL `<anonymous` callers are already joined in
`locate_caller`. Option 2 keeps a visible hop. Decide and record the choice
in `docs/ADR.md`.

Acceptance:

- `o.run()` on an `Outer` field resolves to the one declared `Outer.run`
  as `exact`.
- A call inside an anonymous `Runnable` created in `Outer.start()` appears
  on a route starting at `Outer.start()`.
- Integration endpoints detected inside an anonymous body keep their owning
  method.

### J6. Pruned dispatch stays `possible`

In `possible_targets`, remember whether the caller-module preference removed
candidates. In `fallback_calls`, mark the dispatch `exact` only when the
single target was unique before pruning, or when it is declared concretely
on the receiver type or one of its ancestors.

Acceptance:

- Fixture with `CoreAdapter` and `AppAdapter` implementing `Port`: a call
  from `core` yields `CoreAdapter.send` as `possible` in closed world.
- A unique implementation in the whole repository stays `exact` in closed
  world (existing `test_source_engine.py` assertions unchanged).
- `docs/spec-tech/indexing.md` states that module preference never raises
  confidence.

### J7. Enhanced-for, catch, resource, and typed lambda receivers

Extend `variable_type` to declarations found while walking enclosing
scopes: `enhanced_for_statement` (`type` and `name` fields),
`catch_formal_parameter`, `resource` in a try-with-resources, and
`lambda_expression` formal parameters with an explicit type. Keep untyped
lambda parameters unresolved.

Acceptance:

- Fixture with the four shapes yields `exact` calls to the declared source
  methods, with the correct call lines.
- An untyped lambda parameter still produces no edge.
- The lab measurement in section 2 is rerun and the "untyped receiver" count
  decreases; the new count is recorded in J15.

### J8. Static method imports

Record `import static p.Util.helper;` and `import static p.Util.*;` in
`_Unit`. For an unqualified invocation that the enclosing type and its
ancestors do not declare, resolve the name through single static imports,
then through static wildcard imports when exactly one source type declares
that name with the arity.

Acceptance:

- A fixture with a single static import yields an `exact` edge to
  `p.Util.helper`.
- Two wildcard static imports declaring the same name produce no edge.

### J9. Remove the lab-specific dispatch rule from the core

Delete the `AbstractKafkaMessageProcessor` clauses from
`dispatch_matches_entry` and `_call_graph_edge_allowed`. After J3, the
generic entry-subtype rule covers the lab case. If a convention is still
needed, move it behind the Strategy1 pack with a documented trigger.

Acceptance:

- `grep AbstractKafkaMessageProcessor src/` returns nothing, or only inside
  `systemlens.conventions.strategy1`.
- `uv run python scripts/check_companion_contracts.py` produces the same
  selectable flows on the lab application as before the change.

### J10. One module identity for placeholders and projections

Compute the module of a placeholder method and of a persisted CodeQL
projection method with `_path_module(repo_root, path, modules)` from
`indexing/integration_methods.py`, not from the first path segment. Pass the
discovered modules to `_build_call_graph` for that purpose.

Acceptance:

- A placeholder in another application module is rejected by
  `within_application_boundary`; a test uses an engine call whose callee
  file is outside the indexed method set.
- Existing indexes load unchanged; the signature bump triggers a rebuild.

### J11. Module-rooted `target`/`out` exclusion in the CodeQL projection

In `_prepare_source_only_root`, exclude `target` and `out` only when they
are direct children of the repository root or of a discovered module root,
consistent with `is_build_output`. Keep the `target/generated-sources`
inclusion rule.

Acceptance:

- A file `app/src/main/java/com/acme/target/Foo.java` is copied into the
  projection; `app/target/classes/Foo.java` is not.
- `tests/test_codeql.py` covers both paths without running CodeQL.

### J12. Record components as accessors

In `JavaSymbols`, index each `record_declaration` component as a concrete
arity-0 method returning the component type, unless the record declares an
explicit accessor with that name. In `receiver_type`, use it for typing a
chained receiver.

Acceptance:

- `pair.order().process()` yields an `exact` edge to `Order.process`.
- An explicit accessor declaration takes precedence over the synthetic one.

### J13. `JavaSymbols` cleanup and dispatch index

Remove the unused `_targets` cache. Add a secondary index keyed by
`(owner, module, name, arity)` so `implementations()` for a generic contract
no longer scans the whole `dispatch` dictionary. Document the new cost in
`docs/spec-tech/indexing.md`.

Acceptance:

- All tests in `tests/test_codeql_inheritance.py` and
  `tests/test_source_engine.py` pass unchanged.
- The documented complexity of generic contract lookup is linear in the
  candidates of that name and arity.

### J14. Parse each Java file once per flow rebuild

Size the `parse_java` cache from the eligible inventory at the start of an
index pass, or pass a shared parsed-unit map from
`materialize_integration_methods` to `scheduled_trigger_endpoints`,
`JavaSymbols`, and `materialize_code_flows`. Keep `clear_caches` semantics.

Acceptance:

- `test_symbols_parse_each_indexed_file_once_and_respect_perimeter` is
  extended to count parses across the whole flow stage; the count equals
  the number of eligible Java files.
- Memory stays bounded by the inventory; the limit is documented.

### J15. Regression matrix on the lab application

Record, before and after J1 to J7, the counts of resolved call sites,
persisted call edges by confidence, flows by status, and selectable root
flows on a copy of the lab application, using the `source` engine. Use
`scripts/check_companion_contracts.py` for the end-to-end run and a small
script for the symbol-pass counts.

Acceptance:

- The matrix is appended to this file under a dated heading.
- Each increment states which counts it changed and why; an unexplained
  change blocks delivery of that increment.

## 9. Validation

For each task:

```bash
uv run ruff check src tests
uv run pytest tests/test_source_engine.py tests/test_code_flows.py \
  tests/test_codeql_inheritance.py tests/test_call_graph_engines.py
```

Before merging J1 to J3, J5, J6, J10, and J11, which change persisted facts:

```bash
uv run mypy
uv run pytest
uv run python scripts/check_companion_contracts.py
```

CodeQL-dependent assertions (J4, J10, J11) must be stated as unverified
when no local CodeQL executable is available.

## 10. Delivery record

All tasks J1 to J15 were delivered on `main` on 2026-10-31. Each fix has a
regression test that fails on the previous code. `CODE_FLOW_SIGNATURE` moved
from `code-flow-v39-source-engine-closed-world` to
`code-flow-v45-record-accessors`, so existing indexes rebuild their flows.

| Task | Commit | Outcome |
|---|---|---|
| J1, J2, J3, J4 | `90bea87` | Scheduler anchors, trigger module, cross-module template dispatch, `medium` reachability under `exact`. |
| J5, J6 | `a64ba65` | Anonymous bodies belong to the creating method (ADR-62); pruned dispatch stays `possible`. |
| J7, J8 | `35524cc` | Scoped declarations and static imports type receivers. |
| J9 | `397228a` | `entry_dispatch_allowed` replaces the lab class name in both modules. |
| J10 | `5ad5d1c` | Placeholders and the engine projection use the discovered module identity. |
| J11 | `8e1dda7` | `target` and `out` are build directories only outside `src`. |
| J12 | `6158f26` | Record components act as implicit accessors. |
| J13 | `e791409` | Dispatch signatures are indexed by owner, module, name and arity. |
| J14 | `e02637a` | The parse cache follows the Java inventory, up to 20,000 files. |
| J15 | this section | Regression matrix below. |

J5 was implemented with option 1: anonymous methods are not indexed. CodeQL
already reports their callers as `<anonymous class>.name`, and the join
attaches them to the smallest enclosing indexed method, so both engines
agree without a synthetic owner.

### Regression matrix on the observability lab, 2026-10-31

Measured on a fresh copy of `apps/supermarket-demo` (47 Java files) with
`index --full`, before (`741670d`) and after (`e02637a`) the corrections.
The CodeQL run used a database created with `--build-mode=none`.

| Measure | Source before | Source after | CodeQL before | CodeQL after |
|---|---|---|---|---|
| Integration methods | 73 | 73 | 73 | 73 |
| Call-graph methods | 73 | 73 | 123 | 123 |
| Exact engine edges | 53 | 53 | 57 | 57 |
| Possible engine edges | 0 | 0 | 17 | 17 |
| Possible inferred edges | 6 | 6 | 20 | 20 |
| Flows, potential and medium | 12 | 12 | 12 | 12 |
| Flows, potential and low | 11 | 11 | 11 | 11 |
| Flows with a scheduler trigger | 1 | 1 | 1 | 1 |

The persisted flows are identical before and after, flow by flow, for both
engines. The lab has a single scheduler, no anonymous class and no static
import. Its template body, `AbstractKafkaMessageProcessor.consumeMessage`,
lives in the `kafka-processing` helper module; the removed lab-specific rule
already kept it on the entry consumer, and J3 with J9 now do so generically
with the same result.

Symbol-pass resolution of invocation sites on the same copy:

| Category | Before | After |
|---|---|---|
| Resolved | 54 | 54 |
| External receiver | 74 | 75 |
| Untyped receiver | 70 | 65 |
| No target on a source type | 33 | 37 |

J7 and J12 typed five more receivers. The resolved count and the 59 symbol
calls are unchanged: the new receivers' targets are external types or record
accessors without a method declaration, so no new edge appears.

### Companion check

`scripts/check_companion_contracts.py` failed before this delivery, on the
baseline commit too. Two expectations were stale, not the indexer:

- Since `9dd6a7e`, the export lists access sites per access flow and
  groups them per root in `flow_data_access_flow_ids_by_root`. The check
  now accepts that grouping (`127b5fb`).
- Since `044bc07` (ADR-59), a read that precedes the Kafka publication in
  the fork handler yields no diagnostic. In the lab reservation handler,
  `products.findById` precedes `stockDepleted.publish`, so the order call
  graph has none. The check now rejects a `potential_risk` on that root
  instead of requiring `insufficient_evidence` (`ad76114`).

Both causes were located by bisection. The check now passes with CodeQL.

