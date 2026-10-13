# Data operations in internal flows

This plan proposes source-backed read, insert, update, and delete evidence on
potential internal flows. The first delivery covers supported MongoDB and
Spring Data JPA calls. Literal JDBC statements are a later extension. A flow
must retain an unknown action or resource when the code does not establish it.

Status: in progress. Owner: SystemLens implementer. Reviewers: product owner
and architecture maintainer. MongoDB classification, typed Spring Data JPA
CRUD extraction, additive module persistence, interprocedural data paths, and
call-root data projection are implemented. A dedicated operation table,
derived JPA queries, record-key tracing, and ordering evidence remain open. The
[product scope](docs/PRD.md), [functional specification](docs/SPEC-FONC.md),
and [technical specification](docs/SPEC-TECH.md) remain authoritative.

## 1. Goal and boundaries

An analyst should be able to select an HTTP or Kafka entry flow and see which
data operation a reachable Java method may perform, on which known resource,
with a source location and an explicit confidence level. A data operation must
also be visible when the path has no HTTP call or Kafka publication after it.

An action describes a source-level attempt. `insert` does not prove a row was
created, `delete` does not prove a row existed, and a potential call path does
not prove runtime execution. The first delivery does not infer transaction
commit, cross-service causality, physical JPA table names, or application-level
business effects. Unsupported persistence APIs remain outside coverage.

An operation identity contains the indexed project, source-relative path,
enclosing method identity, invocation location, technology, raw API operation,
action, optional resource identity, confidence, and unresolved reason. For
MongoDB, the resource is a collection. For JPA, it is an entity type; a table
name appears only with independent, unambiguous mapping evidence. No local
machine root or secret value enters persisted evidence.

## 2. Baseline evidence

The code facts below describe the baseline at commit `cef857b`, before the
work in section 4 began.

| Verified fact | Source | Consequence |
|---|---|---|
| MongoDB discovery retains operation name, source line, collection, and a simple owner method name. | [Mongo inventory](src/systemlens/domain/module_inventory.py), [discovery](src/systemlens/discovery/build/modules.py) | The raw action survives extraction, but overloaded owner methods need stronger identity. |
| The flow materializer maps MongoDB calls to `data_read` or `data_write` and retains the raw operation. It attaches MongoDB steps in the same method as an indexed trigger. | [Flow materializer](src/systemlens/indexing/code_flows.py) | Actions in reached helper methods are missing from the persisted flow. |
| Delete and multi-item insert methods are discovered but omitted from the separate write lists. The lists include the unexecuted `bulkOps` builder. | [Discovery](src/systemlens/discovery/build/modules.py), [relations](src/systemlens/indexing/relations.py), [LikeC4 export](src/systemlens/render/likec4_export.py) | Deletes and inserts may appear as reads; a batch builder may appear as a write. |
| JPA inventory records entity declarations, not read or write invocations. | [Data model](docs/spec-tech/data-model.md) | An entity declaration alone cannot prove an operation. |
| CodeQL methods and call edges are persisted, and `flows calculate` uses the stored snapshot. | [Persistence](docs/spec-tech/persistence.md), [indexing](docs/spec-tech/indexing.md) | Flow reconstruction can join stored operations without parsing Java at export time. |

The accepted [conservative extraction](docs/ADR.md) and
[snapshot export](docs/spec-tech/export-snapshot.md) rules apply throughout.
Any new public behavior requires an update to [extraction rules](docs/spec-fonc/extraction-rules.md).
[Spring Data MongoDB](https://docs.spring.io/spring-data/mongodb/reference/4.5/mongodb/template-crud-operations.html)
and [Spring Data JPA](https://docs.spring.io/spring-data/jpa/reference/jpa/entity-persistence.html)
document the conditional behavior of `save`. The [MongoDB bulk API](https://docs.spring.io/spring-data/data-mongo/docs/current/api/org/springframework/data/mongodb/core/BulkOperations.html)
executes a prepared batch through `execute()`.

## 3. Proposed representation

> **Why & What - Persist operation facts**
>
> **What:** Persist one typed data-operation fact per supported source invocation,
> separately from the paths that reach it.
>
> **Why:** One operation can appear in several potential flows, and a flow
> rebuild must use the stored snapshot. This does not establish whether the
> operation ran or committed.
>
> **Alternatives considered:** Store actions only on `CodeFlowStep`. That
> reuses existing JSON but duplicates evidence across flows and loses operations
> when no flow reaches them.
>
> **Fallback:** Keep the existing MongoDB inventory and report that operation
> coverage is unavailable on older indexes until they are reindexed.

The proposed action vocabulary is `read`, `insert`, `update`, `delete`,
`create_or_update`, `read_update`, and `unknown`. A separate derived read/write
facet preserves existing `data_read`, `data_write`, and `reads`/`writes`
consumers. An unknown action creates no asserted read or write relation.
`findAndModify` may carry both read and update facets; `save` remains
`create_or_update` because its outcome depends on entity state.

> **Why & What - Reach data terminals**
>
> **What:** Join persisted operation facts to source-backed method paths from
> an indexed HTTP, Kafka, or scheduled entry, even without another output port.
>
> **Why:** Endpoint-to-endpoint paths miss a request whose only external effect
> is a repository call. A reached method remains a potential path, not a
> statement of execution order across branches.
>
> **Alternatives considered:** Annotate existing endpoint-to-endpoint flows
> only. This is cheaper but hides data-only handlers.
>
> **Fallback:** Show same-method operations when CodeQL is unavailable and
> label the missing interprocedural coverage.

The graph answers whether an indexed entry can reach an invocation. It does
not establish that the invocation runs on every request. Each reached
operation therefore gets its own potential entry-to-data path, even when an
HTTP call or Kafka publication is also reachable. An operation annotates an
entry-to-output path only when its enclosing method belongs to that path's
source-backed method witness. Neither annotation asserts execution order
inside a method or across conditional branches.

The source fact owns the operation and its evidence. A flow step references
that fact and carries the existing broad `kind` plus an optional action.
Older step JSON loads with an unknown action. A new `data_access` step may
represent a reached invocation whose effect is unresolved; consumers must
handle that kind explicitly. Resource identities include technology so an
entity and a collection with the same name do not become one object.

## 4. Delivery tasks

### 4.1 Define action and evidence contracts

Files: add `src/systemlens/domain/data_operations.py`; extend
[flow step model](src/systemlens/domain/code_flows.py) and
[model tests](tests/test_models.py).

Work: define the action vocabulary, read/write facets, source location,
qualified method identity, resource identity, confidence, and unknown reason.
Specify whether a call returns data while writing it. Preserve the current
step `kind` contract and make new fields optional for older JSON.

Acceptance: `insert` differs from `create_or_update`; `findAndModify` retains
both facets; an unsupported call remains unknown. Verify with
`uv run pytest tests/test_models.py`.

### 4.2 Correct MongoDB classification at the source

Files: [Mongo discovery](src/systemlens/discovery/build/modules.py),
[flow materializer](src/systemlens/indexing/code_flows.py),
[relations](src/systemlens/indexing/relations.py),
[LikeC4 export](src/systemlens/render/likec4_export.py),
[graph view model](src/systemlens/render/graph_view_model.py), and
[module tests](tests/test_modules.py).

Work: replace the duplicated write lists with one classifier based on a
supported receiver type and operation. Classify `insert`/`insertAll`,
`delete`/`deleteById`/`deleteAll`, reads, updates, and ambiguous saves.
Treat `getCollection` and `watch` as non-CRUD operations. Treat `bulkOps` as a
builder; record a batch effect only when a supported `execute()` call is
resolved. Keep a source fact with unknown collection when the collection
expression is dynamic.

Acceptance: a delete is never reported as a read, and an unexecuted batch
builder creates no write. A name-matched variable of an unrelated Java type
creates no MongoDB fact. Verify with `uv run pytest tests/test_modules.py
tests/test_relations.py tests/test_code_flows.py`.

### 4.3 Persist facts and refresh them incrementally

Files: [SQLite store](src/systemlens/storage/sqlite.py),
[serialization](src/systemlens/storage/serialization.py),
[index orchestration](src/systemlens/indexing/service.py), and
[storage tests](tests/test_storage_uniqueness.py).

Work: add a source-path-indexed operation table and an additive migration
before the index transaction. Replace facts for changed paths, remove facts
for deleted paths, and bump the extraction and flow signatures. Retain
root-relative paths. Keep the old MongoDB module JSON readable while the new
facts become the source of truth for flow actions.

Acceptance: an old SQLite index opens, a reindex produces new facts, and a
deleted source removes its facts. Repeated indexing produces stable IDs.
Verify with `uv run pytest tests/test_storage_uniqueness.py
tests/test_code_flows.py`.

### 4.4 Extract Spring Data JPA operations

Files: add `src/systemlens/discovery/build/data_operations.py`; integrate it
through [module discovery](src/systemlens/discovery/build/modules.py) and
[index orchestration](src/systemlens/indexing/service.py). Add focused
`tests/test_data_operations.py`.

Work: resolve repository receiver types and entity generics before classifying
`find`, `exists`, `save`, `delete`, and supported derived query methods.
Inspect `@Modifying` plus literal `@Query` and direct `EntityManager` calls
under explicit rules. Preserve unknown targets for custom repositories,
dynamic queries, ambiguous overloads, and unmapped types. Do not infer a
physical table from `@Entity` alone.

Acceptance: one repository call to each supported action yields the correct
entity and source site. `save` stays create-or-update; an unrelated method
named `delete` produces no fact. Verify with
`uv run pytest tests/test_data_operations.py tests/test_modules.py`.

### 4.5 Attach operations to internal paths

Files: [flow materializer](src/systemlens/indexing/code_flows.py),
[flow calculation](src/systemlens/application/flow_calculation.py),
[flow tests](tests/test_code_flows.py), and
[consistency tests](tests/test_flow_consistency.py).

Work: join an operation to a method only when module, relative path,
qualified signature, and source range identify one persisted method. Keep
ambiguous matches unresolved. Traverse directed persisted call edges from
each indexed entry, including a zero-hop same-method path. Sort edges by
source coordinate and method ID; retain one shortest source-backed witness
for each entry and operation pair. Reuse the existing 12-hop flow bound,
report a truncated result when it is reached, and mark unavailable or partial
CodeQL coverage separately. Do not report an exact alternative count unless
the traversed alternatives were actually counted. An endpoint flow receives
an operation only when the operation's method occurs on that specific flow's
method witness. Calls and data operations within the same method remain
potentially co-occurring, with no asserted execution order.

Acceptance: a controller calling a helper that calls a repository exposes the
repository operation with its source site. An operation reached on a sibling
branch does not annotate an unrelated endpoint path. Two overloaded helpers
do not share facts. Cycles terminate and depth truncation is visible. A
CodeQL-free index retains same-method facts and labels any remaining
interprocedural coverage gap. Verify with
`uv run pytest tests/test_code_flows.py tests/test_flow_consistency.py`.

### 4.6 Expose and explain the persisted evidence

Files: [HTML export](src/systemlens/render/html_export.py),
[Flux view](src/systemlens/render/assets/graph/55-code-flows.js),
[CLI flow view](src/systemlens/application/code_flows.py), and
[render tests](tests/test_render.py).

Work: expose action, typed resource, confidence, source location, and unknown
reason from the persisted snapshot. Display read, insert, update, delete, and
create-or-update as distinct actions. Keep the broad read/write kind for
existing consumers. Show potential paths and partial coverage explicitly.
Do not read Java files during export or turn unknown resources into guesses.

Acceptance: CLI and HTML describe the same operation from an index after
source files are unavailable. An old index remains readable and shows unknown
action rather than a fabricated insert or delete. Verify with
`uv run pytest tests/test_render.py tests/test_code_flows.py`; run the slow
browser export test when Chrome is available.

### 4.7 Validate contracts and companion workflow

Files: update the owning sections linked from
[functional specification](docs/SPEC-FONC.md) and
[technical specification](docs/SPEC-TECH.md), plus
[product requirements](docs/PRD.md) if the supported scope changes. Record
any durable decision as a new entry in [ADR](docs/ADR.md). Update
[companion skill](../systemlens-skill/) in the same pass if its workflow or
examples consume the new facts.

Work: test positive, negative, and ambiguous cases across MongoDB and JPA,
including old SQLite indexes, incremental removal, CodeQL timeout, and
CodeQL-free operation. Review whether actions change the existing consistency
diagnostic and keep its uncertain cases uncertain.

Acceptance: focused tests pass, public JSON remains compatible, and the
companion contract check passes against a fresh laboratory index. Run
`uv run ruff check src tests`, `uv run mypy`, `uv run pytest`, and
`uv run python scripts/check_companion_contracts.py`.

## 5. Later JDBC extension

Add a separate extractor for `JdbcTemplate` and prepared-statement calls with
literal SQL `SELECT`, `INSERT`, `UPDATE`, and `DELETE` after the first delivery.
Use physical table identity only for an unambiguous parsed statement. Dynamic
SQL, stored procedures, ORM-generated SQL, and multiple-statement strings
remain unknown until independently supported.

Files: extend `src/systemlens/discovery/build/data_operations.py` and add
JDBC fixtures to `tests/test_data_operations.py`. Acceptance: literal single-
table statements expose the intended action and table; dynamic SQL stays
unknown. Verify with `uv run pytest tests/test_data_operations.py`. Its absence
must be visible in coverage output; section 4 does not cover every Java
persistence technology.

## 6. Follow-up: lost-update candidates

The indexed read and write facts can support a later review aid for concurrent
read-modify-write patterns. They do not currently prove that the written value
depends on the read value, that both operations target the same record, or that
concurrent transactions can overlap. Keep this analysis separate from the
operation classification and flow projection.

The first useful candidate requires all of the following source evidence:

- A read and a write or update in one method-level value-flow path.
- Evidence that the write value depends on the read result.
- The same resolved resource and record key for both operations.
- A known transaction boundary, or an explicit unknown transaction status.
- An inventory of recognized conflict controls applicable to the update, such
  as a version check, a scoped lock, or a conditional atomic update.

Do not treat `@Transactional` as conflict protection by itself. Do not report an
unprotected update when the database engine, isolation mode, key identity, or
protection mechanism is unknown. The result should say “potential
read-modify-write; protection not established” and link to both source sites.
An unresolved value dependency or record key must produce an abstention, not a
defect finding.

Acceptance criteria:

- Protected, unprotected, and ambiguous examples distinguish correctly across
  more than one Spring application.
- Each candidate includes both operation locations, the resolved resource and
  key when known, transaction evidence, recognized protection evidence, and
  explicit unknowns.
- A read and write to the same entity without value-flow evidence do not become
  a lost-update candidate.
- Precision, abstention rate, and reviewer time are recorded before promoting
  this review aid to a user-facing diagnostic.

The scope is larger than locating a read and a write. Java value flow, key
aliasing, transaction propagation, and database behavior can cross framework
and configuration boundaries. If these cannot be resolved with bounded
evidence, retain the operations as graph annotations and leave the concurrency
conclusion to the reviewer.

## 7. Residual risks

Static reachability can over-approximate runtime dispatch and cannot prove a
transaction committed. A repository method may wrap additional side effects.
CRUD names alone are insufficient evidence for a custom method, and a known
method may target an unresolved resource. Each emitted operation therefore
retains the supporting invocation, method path, and confidence.

The main implementation cost is maintaining one classifier across extraction,
relations, flow calculation, CLI, and export. The plan accepts that cost to
avoid conflicting read/write judgments. If typed resolution proves too weak
on a supported repository, retain unknown evidence and narrow coverage rather
than promoting a method-name guess to an architecture fact.

## 8. Research and API references

These papers guide the design; none validates SystemLens' proposed operation
classifier or its persisted call graph. Its precision and coverage require
project-specific fixtures and evaluation.

- Liang et al., [*Pointer Analysis for Database-Backed Applications*](https://doi.org/10.1145/3729307), PLDI 2025. DBridge models Java database APIs, including JPA and Spring Data JPA, and provides the DB-Micro benchmark. Its Java-to-database value-flow objective is broader than this plan's operation reachability.
- Reps, Horwitz, and Sagiv, [*Precise Interprocedural Dataflow Analysis via Graph Reachability*](https://doi.org/10.1145/199448.199462), POPL 1995. Valid interprocedural paths respect matching calls and returns. The persisted method-call graph here lacks the control-flow facts needed to claim that precision.
- Sui et al., [*On the Recall of Static Call Graph Construction in Practice*](https://doi.org/10.1145/3377811.3380441), ICSE 2020. Their Java evaluation motivates explicit missing-edge and partial-coverage reporting.
- Smaragdakis and Balatsouras, [*Pointer Analysis*](https://doi.org/10.1561/2500000014), Foundations and Trends in Programming Languages, 2015. Receiver and alias resolution trade precision against coverage and cost.
- Cherry et al., [*Static Analysis of Database Accesses in MongoDB Applications*](https://doi.org/10.1109/SANER2022.2022.00111), SANER 2022. Its MongoDB API extraction and false-positive evaluation concern JavaScript, so its rules do not directly classify Spring Data Java calls.

The [CodeQL Java call-graph guide](https://codeql.github.com/docs/codeql-language-guides/navigating-the-call-graph/)
defines static and possible virtual targets. [Spring Data JPA query methods](https://docs.spring.io/spring-data/jpa/reference/jpa/query-methods.html)
define derived deletes and modifying queries. These API contracts determine
classification rules alongside the research above.
