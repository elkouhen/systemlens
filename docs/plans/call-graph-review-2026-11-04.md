# Review: weaknesses of the call-graph construction (2026-11-04)

Status: lots 1, 2, 4, 5 and 6 validated on 2026-11-04 by the product
owner, who asked to fix them. Lot 3 waits for the validation of ADR-81,
proposed for it. Section 5 records each finding's state. Baseline: commit
`9006b7b`.
Owner: SystemLens implementer. Reviewer: product owner.

Three independent reviews covered the `source` engine's call resolution,
the `codeql` engine's queries and join, and a differential comparison of
both engines on train-ticket. Each finding was proved by a probe on a
minimal fixture or on a train-ticket copy, unless marked `measured`. The
coordinator re-ran the most severe findings. Shapes already listed in
[the coverage plan](call-graph-coverage.md) and findings of
[the indexing review](indexing-review-2026-11-03.md) are not repeated.

## 1. Summary

On train-ticket the two engines agree on every edge between indexed
methods except 194 call sites, and on all 406 flows. The corpus has no call
site with two concrete dispatch candidates, so it does not exercise the
weaknesses below: they come from fixtures that reproduce common Java and
Spring code. The most severe ones produce a false `exact` edge, which
raises a flow's confidence on a wrong route.

## 2. Root causes

| # | Weakness | Findings |
|---|---|---|
| R1 | The source engine's lexical scope model is incomplete: declaration position, anonymous and local classes, type parameters, field hiding | S1, S2, S5, S6, S7, S8, S11 |
| R2 | Overload selection does not follow Java's phases (no boxing first, literal suffixes, varargs) | S3, S4, S14, Q2 |
| R3 | `exact` assumes the known implementor set is complete, while generated, inherited, test or framework implementations exist | S9, S10, S12, Q6, Q8 |
| R4 | The CodeQL input is trusted without checking it matches the indexed sources | Q1, Q5 |
| R5 | Timeout handling drops results already obtained | Q3, Q4 |
| R6 | The tools around the engines use other projections or node sets than the index | D1, D2, D3, D4, Q10 |
| R7 | Linear scans per call site and repeated full work | S13, Q12, D7 |

## 3. Findings

Severity values: `false exact`, `false edge`, `lost edge`, `wrong label`,
`crash`, `staleness`, `performance`.

### 3.1 Source engine

All in `src/systemlens/indexing/java_symbols.py`.

| ID | Finding | Where | Severity |
|---|---|---|---|
| S1 | Two `var` locals referencing each other through a shadowed field recurse forever; one method aborts the whole symbol pass. Re-run by the coordinator. | `variable_type` `:746`, `_scoped_declaration` | crash |
| S2 | A `var` initializer is typed at the call site, not at its declaration, so a later local shadows the field it names. | `:746` | false exact |
| S3 | Overload choice prefers boxing over widening and subtyping (`m(long)`/`m(Integer)` with `m(1)`). Re-run by the coordinator. | `_argument_matches` `:77`; `_select_overload` `:552-575` | false exact |
| S4 | `10L` and `1.5f` literals are typed `int` and `double`. Re-run by the coordinator. | `_LITERAL_TYPES` `:30-35` | false exact |
| S5 | In an anonymous-class method, the outer method's parameters hide the inner method's own parameters and locals. | `variable_type` `:722-729` | false exact |
| S6 | Anonymous-class fields are ignored; an outer field of the same name wins. | `variable_type` `:754-777` | false exact |
| S7 | A local class name leaks to the whole enclosing type and shadows an import. | `resolve` `:452-457` | false exact |
| S8 | Class and method type parameters do not shadow a source type of the same simple name (`<Order>`). | `resolve` `:440-497`; signatures `:298` | false exact |
| S9 | An override whose parameters cannot be resolved (varargs, external wildcard import) is invisible to dispatch, so the base body is `exact`. | `:317`; `implementations` `:499-525` | false exact |
| S10 | Record accessors, Lombok getters, bodies inherited from an external class and `Object` methods are not counted as implementors, so the one written body is `exact`. Re-run by the coordinator. | closed-world rule `:1460-1466` | false exact |
| S11 | Field hiding is resolved by qualified-name length or alphabetical order, not by hierarchy distance. | `variable_type` `:754-757`; `_field_type` `:1031`; `_lombok_getter_type` `:950` | false exact |
| S12 | An interface `default` body stays a target although a superclass method wins in Java. Re-run by the coordinator. | `_index_inherited_implementations` `:380-411` | false edge |
| S13 | Calls on receivers without a source contract, such as every Spring Data repository call, scan all methods: 800 classes take 11 s. | `_candidate_targets` `:624-631`; `_declared_arities` `:1095-1103` | performance |
| S14 | Varargs calls with another arity, outer-class fields used from a member inner class, and a type present in both `src/main` and `src/test` of one module give no edge. | `:287`, field lookup | lost edge |

### 3.2 CodeQL engine

| ID | Finding | Where | Severity |
|---|---|---|---|
| Q1 | A database built from an older source revision is used silently; old call lines land in other methods as `exact` edges and create flows that do not exist. Status stays `complete`. Re-run by the coordinator. | `engines/codeql/engine.py:97-100`; `indexing/service.py:293-298`; `code_flows.py:734-757` | false exact |
| Q2 | In build mode `none`, an unresolved argument type makes CodeQL pick the first declared overload, reported `exact`; the other overload is lost. | `engines/codeql/queries.py:57-58` | false exact |
| Q3 | A timeout in the reachability, methods or payload query discards the calls already returned. | `call_graph_stage.py:686-692`, `:728-731`; `engine.py:22-31` | lost edge |
| Q4 | Partial-result recovery never recovers anything: CodeQL writes the result file only at the end. The spec promises recovered calls. | `engines/codeql/database.py:203-216` | lost edge |
| Q5 | A database built with another source root joins zero rows; the status is still `complete` and the symbol-pass edges look like CodeQL's. | `code_flows.py:702-707`, `:734-757` | staleness |
| Q6 | A user-built database includes `src/test`; a test implementation turns a production `exact` edge into `possible` and lowers the flow. The automatic database gives a different result on the same sources. | `queries.py:30-32`; `database.py:42-46` | false edge |
| Q7 | A generic receiver (`Gateway<String>.send`) loses its declared-method and witness rows, because the parameterized member is not `fromSource()`. | `queries.py:68`, `:72-74`, `:245` | lost edge |
| Q8 | A `@FeignClient` with a `fallback` class gets an `exact` edge to the fallback; the HTTP call becomes `possible` and its flow `low`, or vanishes with exact edges only. Both engines. Re-run by the coordinator. | `queries.py:57-58`; witness branch | false exact |
| Q9 | Overloads declared on one line drop the edge in both engines; the join key has no column. Known limit since K2, but the spec lists S6 as covered. | `code_flows.py:741-744` | lost edge |
| Q10 | `codeql_methods` stores each annotated or nested method twice, with CodeQL's raw name and start line. | `call_graph_stage.py:401-426` | wrong label |
| Q11 | `equals`/`hashCode` on an `Object` receiver fan out to every source override, including 39 in the caller's module on train-ticket. `measured`. | `queries.py:64-65` | false edge |
| Q12 | The automatic database is rebuilt on every index (97 s against 20 s prebuilt on train-ticket), and the reachability query no longer contributes a flow since ADR-78. `measured`. | `database.py:89-155`; `call_graph_stage.py:692` | performance |

### 3.3 Differential comparison and tools

| ID | Finding | Where | Severity |
|---|---|---|---|
| D1 | `analyze compare-engines` recomputes flows without the persisted data operations, so reads become writes: 352 flows reported instead of 406. Re-run by the coordinator. | `application/engine_comparison.py:61`; `data_operation_projection.py:92-94` | false edge |
| D2 | `analyze indexing-audit` flags 4754 edges as orphans on a CodeQL index, because it ignores `codeql_methods` nodes. | `application/indexing_audit.py:283` | wrong label |
| D3 | `compare-engines` mixes node universes, ignores confidence and inferred in its edge key, and folds 406 flows into 390 keys. | `engine_comparison.py:73-76` | wrong label |
| D4 | Spring Data derived-query calls are `inferred` with the source engine and duplicated with CodeQL: 192 sites. | `code_flows.py:893-895`, `:951`; `java_symbols.py:1445` | wrong label |
| D6 | Calls from an anonymous `UserDetails` body to the outer class are missed by the source engine, because `UserDetails` members are not known. 2 sites. | `java_symbols.py:1179-1182` | lost edge |
| D7 | A `.codeql` database inside the repository is hashed and inventoried: 1521 extra files on train-ticket. | default excludes | performance |

D5, the "dispatch fallback" reason text, is finding F14 of the indexing
review. D8, CodeQL edges outside the caller's dependencies, is task C6; it
now counts 137 edges, all from Lombok `Response.equals` and `hashCode`.

## 4. Proposed lots

| Lot | Findings | Why this order |
|---|---|---|
| 1 | Q1, Q5, S1, Q3 | Silent wrong edges from a mismatched database, a crash, and lost results. |
| 2 | S2, S3, S4, S5, S6, S7, S8, S11, S12, Q2, Q7 | False `exact` and false edges with local fixes in resolution or queries. |
| 3 | S9, S10, Q6, Q8, Q11 | When a single implementor is `exact`; needs a decision record first. |
| 4 | D1, D2, D3, D4, Q10 | Tools that report wrong totals or labels. |
| 5 | S14, D6, Q4, Q9 | Missed edges and the dead partial-recovery path. |
| 6 | S13, Q12, D7 | Performance. |

Lot 3 changes a visible rule: an edge stops being `exact` when the sources
cannot prove the implementor set is complete. Every fix leaves a
regression test built from its probe. The figures "408 flows" in ADR-70,
ADR-71 and the dispatch-scope plan are stale since commit `74789a1`: both
engines now give 406 flows on train-ticket.

## 5. Delivery record

| ID | Outcome |
|---|---|
