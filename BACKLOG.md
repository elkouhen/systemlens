# Backlog: data operations and runtime observations

Status: open. Owner: SystemLens implementer. Reviewers: product owner and
architecture maintainer.
Review date: 2026-10-31, on commit `632af79` plus the uncommitted delivery of
the widget readability backlog.

This backlog carries two work streams selected after a product review of the
value axes on 2026-10-31:

- **Stream D** finishes the
  [data operations plan](docs/plans/data-operations-in-internal-flows.md).
  It adds the persisted operation table and the JPA, `EntityManager`, and
  JDBC coverage that remain open, then the transaction and conflict-control
  facts that two review aids need: dual write and lost update.
- **Stream R** extends the
  [runtime observations](docs/spec-tech/runtime-observations.md) beyond
  call sums: latency percentiles, ranked hotspots, completeness facts, and a
  second collection source based on OpenTelemetry metrics.

The previous root backlog (widget readability in the HTML export, delivered
on 2026-10-31) was removed from this file on 2026-10-31. Its content and
delivery record remain in Git history before that date.

## 1. Index and scope

| Document | Purpose |
|---|---|
| [Product requirements](docs/PRD.md) | Scope boundaries and the hotspot success measure. |
| [Data operations plan](docs/plans/data-operations-in-internal-flows.md) | Representation, open tasks 4.3 to 4.5, JDBC extension, lost-update gate. |
| [Feasibility of microservice diagnostics](docs/microservices-bad-practices.md) | Ratings of the dual-write and lost-update candidates. |
| [Algorithms](docs/spec-tech/algorithms.md) | Sections 7 and 8: access association and consistency detector. |
| [Data model](docs/spec-tech/data-model.md) | Flow steps, data access projection, consistency diagnostics. |
| [Extraction rules](docs/spec-fonc/extraction-rules.md) | Observable JPA and MongoDB extraction contract. |
| [Runtime observations](docs/spec-tech/runtime-observations.md) | Elastic APM collection, persistence, reconciliation, overlay. |
| [Runtime export plan](docs/plans/runtime-observations-in-export.md) | Delivered overlay and its lab validation. |
| [CLI reference](docs/spec-fonc/cli.md) | `observe`, `analyze`, and `flows` contracts. |
| [Archived consistency backlog](BACKLOG.consistency-diagnostics.md) | Historical diagnostic rendering tasks. |
| [Earlier backlog](BACKLOG.previous.md) | Historical index of data-operation work. |

The deferred concerns of `AGENTS.md` still apply: no task below may add CDN
dependencies, ARIA semantics, or keyboard navigation work. The engineering
invariants of `AGENTS.md` apply to every task: relative source paths only,
additive SQLite migrations before the index transaction, exports that read
the persisted snapshot only, conservative extraction, and no secret in a
persisted fact.

## 2. Baseline

Verified on the working tree at the review date.

### 2.1 Data operations

| Fact | Where | Consequence |
|---|---|---|
| MongoDB calls are classified into `read`, `insert`, `update`, `delete`, `create_or_update`, and `read_update` with read/write facets. | `src/systemlens/domain/mongo_operations.py` | The action vocabulary of plan task 4.1 exists. |
| JPA calls are accepted on a field typed by a source-declared interface that extends `JpaRepository`, for 14 CRUD method names. | `discover_jpa_methods` in `src/systemlens/discovery/build/modules.py` | `CrudRepository`, `ListCrudRepository`, `PagingAndSortingRepository`, constructor-injected repositories, derived queries, `@Query`, `@Modifying`, and `EntityManager` create no fact. |
| A key expression is kept only for `findById`, `existsById`, and `deleteById`; key identity is derived for exact literals and traced through unmodified scalar parameters. | `JpaMethod.key_expression`, `src/systemlens/indexing/java_value_origins.py` | Derived-query predicates and Mongo query objects carry no key identity. |
| Operations are persisted inside the module inventory JSON and as `code_flows` steps; there is no operation table. | `modules` columns and `_migrate_module_columns` in `src/systemlens/storage/sqlite.py` | Plan task 4.3 is open. A reached operation exists only where a flow materialised it. |
| Each reachable HTTP, Kafka, or scheduled entry gets a potential path to each supported Mongo or JPA call through the persisted call graph. | `src/systemlens/indexing/code_flows.py` | Plan task 4.5 is implemented for the supported calls. |
| The consistency detector accepts `transaction_id`, `isolation`, `guard`, `guard_scope`, `value_dependencies`, and `concurrency` on steps, but source extraction never produces them. | `CodeFlowStep` in `src/systemlens/domain/code_flows.py`, `src/systemlens/application/flow_consistency.py` | Lost-update and dirty-read refinements fire only on manifest facts. |
| No JDBC extraction exists. | plan section 5 | `JdbcTemplate` traffic is invisible, and coverage output does not say so. |
| The lab application uses Spring Data JPA on PostgreSQL and Spring Data MongoDB. | `../systemlens-observability-lab/apps/supermarket-demo` | Both technologies can be validated on a real repository. |

### 2.2 Runtime observations

| Fact | Where | Consequence |
|---|---|---|
| Collection reads `metrics-apm.service_destination.<interval>-*` and `metrics-apm.transaction.<interval>-*` through composite aggregations, plus `logs-apm.error-*` for error groups. | `src/systemlens/discovery/elastic_apm.py` | Only `calls`, `failures`, and `duration_sum_us` are measured. |
| `runtime_observations` has one row per kind, service, target, and target type; a collection replaces every row of its source. | `src/systemlens/storage/sqlite.py` | No percentile column, no collection history, no completeness record. |
| The `source` column exists on both runtime tables but only `elastic` writes it. | same | A second source can coexist without a schema change for identity. |
| Reconciliation and the export overlay are source-agnostic once rows exist; service mapping comes from `runtime.service_map`. | `src/systemlens/application/runtime_observations.py` | A new collector reuses the matching rules unchanged. |
| APM aggregation overflow buckets (`_other`) are not detected. | collector | A high-cardinality service silently loses targets. |
| The lab ships the OpenTelemetry Java agent and the Elastic OTel collector; its configuration mentions a Prometheus exporter and a PostgreSQL receiver. | `../systemlens-observability-lab` | A Prometheus-backed source can be validated there once a server is exposed. |

## 3. Invariants for both streams

- A data operation is a source-level attempt. `insert` does not prove a
  row was created, and a reachable operation does not prove execution.
- Every candidate review aid says "protection not established" or
  "recovery not established" and links both source sites. It never claims
  a defect.
- Runtime counts and percentiles are lower bounds and approximations
  that depend on sampling, aggregation, and the backend version. The UI
  and the CLI say so wherever they show one.
- Observations never create, confirm, or remove a static fact. A second
  runtime source follows the same rule.
- Older indexes stay readable. New columns are nullable, new tables are
  created on first write, and the absence of a fact is reported as unknown.

## 4. Stream D: data operations

Dependencies: D1 before D2 to D5; D6 before D7 and D8; D9 closes the
stream.

### D1. Persist operations in a dedicated table

Files: `src/systemlens/storage/sqlite.py`,
`src/systemlens/storage/serialization.py`,
`src/systemlens/indexing/service.py`, `tests/test_storage_uniqueness.py`.

Add a `data_operations` table keyed by a stable ID derived from module,
relative path, enclosing method, line, technology, and raw operation.
Columns: technology, raw operation, action, read/write facets, resource
identity, key expression, key identity, confidence, unresolved reason, and
the evidence range. Create it additively before the index transaction.
Replace the rows of changed paths and remove the rows of deleted paths
during incremental indexing. Flow materialisation reads this table instead
of the module JSON, which stays readable for older consumers.

Acceptance:

- An index created before this task opens, reindexes, and produces rows.
- Deleting a source file removes its rows on the next index run.
- Two consecutive index runs produce identical IDs.
- `flows calculate` produces the same steps from the table as from the
  module JSON on the lab application.

### D2. Widen repository resolution

Files: `discover_jpa_methods` and the Mongo repository resolution in
`src/systemlens/discovery/build/modules.py`, `tests/test_modules.py`.

Accept interfaces extending `CrudRepository`, `ListCrudRepository`,
`PagingAndSortingRepository`, `JpaRepository`, and `MongoRepository`, in
any generic order, and interfaces that extend such an interface declared in
the same module. Accept receivers that are constructor parameters assigned
to a final field, as well as fields. Keep an interface whose entity cannot be
resolved to exactly one indexed entity unresolved, with a reason.

Acceptance:

- A `CrudRepository<Order, UUID>` injected through a constructor yields the
  same facts as a `JpaRepository` field.
- A repository whose entity matches two indexed classes yields an
  unresolved fact, not a guessed entity.
- A class with a method named `save` on a non-repository field yields
  nothing.

### D3. Derived queries and literal `@Query`

Files: `src/systemlens/discovery/build/modules.py`, a new
`src/systemlens/domain/data_operations.py` for the classifier,
`tests/test_data_operations.py`.

Classify repository interface methods by the Spring Data prefix grammar:
`find`, `read`, `get`, `query`, `search`, `stream`, `count`, and `exists`
are reads; `delete` and `remove` are deletes. Record the key expression when
the predicate is a single `By<Property>` equality with one argument, and
the key identity when that argument is an exact literal or a traced scalar.
For a literal `@Query`, classify JPQL `select` as read, and `update` or
`delete` with `@Modifying` as update or delete. A native query, a
`Specification`, `Example`, or `Querydsl` call, and a non-literal query
string remain unknown with a reason.

Acceptance:

- `findByCustomerId(id)` is a read on the entity with the key expression
  `id`; `deleteByStatus(status)` is a delete without a key identity.
- A `@Modifying @Query("update Order o set ...")` is an update; the same
  query without `@Modifying` is unknown, not a read.
- A derived method name that the grammar cannot parse is unknown.

### D4. `EntityManager` operations

Files: `src/systemlens/discovery/build/modules.py`,
`tests/test_data_operations.py`.

On a field or constructor parameter typed `EntityManager`, classify
`persist` as insert, `merge` as create-or-update, `remove` as delete, and
`find` or `getReference` as read, with the entity taken from the argument
type or the class literal. `createQuery` with a literal JPQL string follows
the D3 rules. `createNativeQuery` is handed to D5. A `Query` object whose
string is built dynamically remains unknown.

Acceptance:

- `entityManager.find(Order.class, id)` is a read on `Order` with key
  expression `id`.
- `entityManager.createQuery(sql)` where `sql` is a parameter is unknown.

### D5. Literal JDBC statements

Files: a new `src/systemlens/discovery/build/jdbc_operations.py`,
`tests/test_data_operations.py`.

On `JdbcTemplate` and `NamedParameterJdbcTemplate` receivers, classify
`query*` and `queryForObject` as reads and `update`, `batchUpdate`, and
`execute` by the first keyword of a literal single-statement SQL string
(`SELECT`, `INSERT`, `UPDATE`, `DELETE`). The string may be a literal or a
`static final` constant of the same class. Use the resource identity
`jdbc:<module>:<table>` only when the statement names exactly one table.
Joins, subqueries, stored procedures, multiple statements, and concatenated
or formatted strings remain unknown, with a reason.

Acceptance:

- `jdbc.update("DELETE FROM orders WHERE id = ?", id)` is a delete on
  `orders` with key expression `id`.
- `jdbc.query("SELECT ... FROM orders o JOIN lines l ...")` is a read with
  an unknown resource.
- `jdbc.update(buildSql(), ...)` is unknown.

### D6. Transaction scope facts

Files: `src/systemlens/discovery/build/modules.py`, flow materialisation
in `src/systemlens/indexing/code_flows.py`, `tests/test_code_flows.py`,
`tests/test_flow_consistency.py`.

Attach `transaction_id`, propagation, `isolation`, and read-only flags to
an operation whose enclosing method or class carries `@Transactional`, or
that runs inside a `TransactionTemplate.execute` lambda. Use a stable ID
from module, path, and method. A method without an annotation inherits the
class annotation; a method called from a transactional method does not
inherit anything, because propagation is a runtime decision. `@Transactional`
is scope evidence only; it is never recorded as a guard.

Acceptance:

- Two operations in one `@Transactional` method share one
  `transaction_id`; a helper called from it has none.
- `isolation = READ_UNCOMMITTED` reaches the dirty-read rule of the
  detector unchanged, and the default isolation is recorded as unknown.

### D7. Conflict-control inventory

Files: `src/systemlens/discovery/build/modules.py`,
`src/systemlens/indexing/code_flows.py`, `tests/test_flow_consistency.py`.

Record a `guard` with its `guard_scope` when the evidence is explicit: a
`@Version` field on the resolved entity (`optimistic_version`, scope
resource), `@Lock(PESSIMISTIC_READ|WRITE)` on the repository method
(`pessimistic_lock`, scope operation), a Mongo `findAndModify` or an
update whose document uses only `$inc`, `$set` with a version predicate, or
`upsert` (`atomic_update`, scope operation), and a JDBC `UPDATE ... WHERE`
with a version column named in the statement (`conditional_update`). An
unknown mechanism is recorded as absent evidence, never as "unprotected".

Acceptance:

- An entity with `@Version` makes every write on it carry
  `optimistic_version`; removing the field removes the guard on reindex.
- A `$set` update without a predicate carries no guard.

### D8. Same-method read-modify-write and dual-write review aids

Files: `src/systemlens/application/flow_consistency.py`,
`src/systemlens/indexing/java_value_origins.py`,
`tests/test_flow_consistency.py`, renderer tests.

Derive `value_dependencies` when a write argument is the unmodified result
of a read in the same method, or a final local initialised from it. With D6
and D7, the existing lost-update refinement then runs on source facts for
same-method sequences. Add a dual-write candidate: a data write and a Kafka
publication reachable in one flow, with the transaction evidence of D6,
reported as "potential dual write; recovery not established". Recognise an
outbox pattern conservatively: a write to a resource named `*outbox*` with
no publication, or a `@TransactionalEventListener` on the publishing method,
downgrades the candidate to "recovery mechanism present, not verified".

Both aids are gated by the plan's section 6: before they become default
diagnostics, record precision, abstention rate, and reviewer time on the lab
and on one more Spring application in the delivery record.

Acceptance:

- `var o = repo.findById(id); o.setQty(o.getQty() - 1); repo.save(o);`
  inside `@Transactional` without `@Version` yields a lost-update candidate
  with both source sites and "protection not established".
- The same code with `@Version` on the entity yields a guarded pair.
- A read and a write without a value dependency yield no candidate.
- `repo.save(order); kafka.send("orders", event);` yields a dual-write
  candidate; adding an outbox write and removing the send yields none.

### D9. Coverage, surfaces, documentation, and skill

Files: `inventory_coverage` in `src/systemlens/application/architecture.py`,
`src/systemlens/application/code_flows.py`,
`src/systemlens/render/html_export.py`,
`src/systemlens/render/assets/graph/55-code-flows.js`, the owning
documents, `docs/ADR.md`, `../systemlens-skill/`.

Make `analyze coverage` list persistence technologies with their supported,
partial, and unsupported operation counts and the unresolved reasons, so an
analyst can see that a JDBC-heavy module is partially covered. Show the
`jdbc:` resource kind and the guard and transaction facts in `flows show`
and in the call-tree badge tooltip. Update extraction rules, the data model,
algorithms section 8, and the companion skill's manifest guidance for the
new fields. Record the table and the review-aid gate as an ADR.

Acceptance:

- `analyze coverage --json` on the lab reports JPA and MongoDB as supported
  and lists every unknown operation with its reason.
- CLI and HTML describe the same operation, guard, and transaction from an
  index after the source tree is removed.
- `scripts/check_companion_contracts.py` passes.

## 5. Stream R: runtime observations beyond sums

Dependencies: R1 and R4 are independent; R2 needs R1; R3 needs R1 and R4;
R5 needs R2 and R3; R6 closes the stream.

### R1. Latency percentiles per entry point

Files: `src/systemlens/discovery/elastic_apm.py`,
`src/systemlens/domain/runtime.py`, `src/systemlens/storage/sqlite.py`,
`tests/test_runtime_observations.py`.

Add a `percentiles` sub-aggregation on `transaction.duration.histogram`
for p50, p95, and p99 to the transaction query, folded per service and
transaction name. Persist them as nullable `duration_p50_us`,
`duration_p95_us`, and `duration_p99_us` columns added by an additive
migration. Service destination streams expose only a sum and a count, so a
destination row keeps an average and no percentile; the report says so.
Verify the field name and histogram type on the lab's Elastic 9.5 cluster
before merging, and record the outcome in section 7.

Acceptance:

- A fake cluster returning histogram percentiles yields rows with the
  three values; a cluster without the field yields rows with `NULL` and a
  warning in the report, not an error.
- An index collected before this task reports its rows without percentiles.
- The existing `observe report --json` fields keep their names and values.

### R2. Ranked hotspots in the report and the export

Files: `src/systemlens/application/runtime_observations.py`,
`src/systemlens/delivery/cli.py`, `src/systemlens/render/html_export.py`,
graph assets under `src/systemlens/render/assets/graph/`,
`docs/spec-fonc/cli.md`, `docs/spec-fonc/html-export.md`, `docs/UX-UI.md`.

Add `observe report --hotspots N --by p95|p99|failures|errors` listing the
N highest entry points, each with its indexed `endpoint_ids`, service,
window, calls, the ranking value, and a completeness note from R5 when
available. Entry points that are `not_compared` or `unverified` are listed
apart. In the export, add a "Hotspots" group to the runtime panel; selecting
a row selects the endpoint's card and arc, as an existing runtime gap row
does. No new graph edge and no change to layout or camera.

Acceptance:

- The ranking is deterministic for equal values (service, then target).
- A browser test selects a hotspot row and asserts the selected endpoint
  and an unchanged camera.
- The PRD success measure on hotspots is satisfied on the lab export: the
  analyst sees the ranked list, its completeness, and the linked evidence.

### R3. Collection history and window comparison

Files: `src/systemlens/storage/sqlite.py`,
`src/systemlens/application/runtime_observations.py`,
`tests/test_runtime_observations.py`.

Add a `runtime_collections` table (ID, source, origin, window, interval,
collected time, page counts, overflow flag from R5) and a `collection_id`
column on both observation tables. Keep the last three collections per
source and prune older ones in the same transaction. `observe report` and
the export use the latest collection by default; `observe report
--compare-previous` adds the delta of calls, failure ratio, and p95 per
matched row. The export shows the delta only when two collections of the
same source and interval exist.

Acceptance:

- A fourth collection removes the first one's rows atomically.
- A delta is computed only between collections with the same interval and
  source; otherwise the report states why no comparison is shown.
- An index with only the old schema reports its single collection.

### R4. Second runtime source: OpenTelemetry metrics through Prometheus

Files: a new `src/systemlens/discovery/prometheus_otel.py`, a
`RuntimeSource` protocol in
`src/systemlens/application/runtime_observations.py`,
`src/systemlens/delivery/cli.py`, `docs/spec-tech/runtime-observations.md`,
`docs/spec-fonc/cli.md`, `docs/spec-fonc/configuration.md`.

Extract the Elastic-specific collection behind a `RuntimeSource` interface
that returns observations, error groups, and a completeness record. Add
`systemlens observe prometheus --url URL --since 7d [--bearer-env NAME]
[--ca-cert FILE]` reading the HTTP query API only, with instant queries over
range vectors computed server side. Map OpenTelemetry HTTP semantic
conventions: `http.server.request.duration` by `service.name`,
`http.request.method`, and `http.route` to request transactions;
`http.client.request.duration` by `server.address` and `server.port` to REST
destinations; `messaging.client.*` by `messaging.destination.name` to Kafka
producers and consumers. Compute p50, p95, and p99 with `histogram_quantile`
server side. Accept the Prometheus naming of those metrics (underscores,
unit suffixes). Metrics without a route label or with another convention
become `not_compared` with the reason. Store `source = prometheus`. The
reconciliation rules and `runtime.service_map` apply unchanged.

The lab must expose a Prometheus server scraping the collector before this
task can be validated; that change belongs to the lab repository and is a
prerequisite, not part of this task.

Acceptance:

- A fake Prometheus API with the lab's service names yields the same
  reconciliation statuses as the Elastic collection for matched routes and
  destinations.
- A URL with credentials is rejected; the token is never persisted or
  logged.
- Observations of the two sources coexist in the tables and the report
  names the source it reconciles.

### R5. Completeness facts

Files: `src/systemlens/discovery/elastic_apm.py`,
`src/systemlens/discovery/prometheus_otel.py`,
`src/systemlens/application/runtime_observations.py`, report and overlay
text.

Detect Elastic APM overflow buckets (`_other` service, transaction, or
destination names) and record an overflow flag with the affected service on
the collection. Record the interval, the page count per query, and whether
any query hit the 100-page bound. For Prometheus, record the scrape
interval and the step. The report and the runtime panel header show one
completeness line: window, interval, source, and the overflow or truncation
state. A hotspot row from R2 carries "incomplete" when its service overflowed.

Acceptance:

- A fake cluster emitting an `_other` bucket marks the collection as
  overflowed and names the service; counts of that service are shown with
  the lower-bound wording.
- Without overflow or truncation, the completeness line says so
  explicitly.

### R6. Documentation, ADR, skill, and lab validation

Files: `docs/spec-tech/runtime-observations.md`, `docs/spec-fonc/cli.md`,
`docs/spec-fonc/html-export.md`, `docs/UX-UI.md`, `docs/PRD.md` scope
tables, `docs/ADR.md`, `../systemlens-skill/`.

Record the `RuntimeSource` seam, the percentile semantics, and the
history retention as a new ADR that references ADR-60. Move the Elastic
collection section of the runtime specification under a per-source
heading. Update the PRD delivered scope. Update the skill's rule that
static evidence is never described as runtime observation to cover
percentiles and hotspots. Validate against the lab's Elastic 9.5 cluster
and, when the lab exposes one, its Prometheus server, and record both in
section 7.

Acceptance:

- Every public field added by R1 to R5 appears in the owning document.
- `scripts/check_companion_contracts.py` passes.

## 6. Validation

For each task:

```bash
uv run ruff check src tests
uv run pytest tests/test_data_operations.py tests/test_code_flows.py \
  tests/test_flow_consistency.py tests/test_runtime_observations.py
```

For D1 and R3, which touch the SQLite schema, and before closing each
stream:

```bash
uv run mypy
uv run pytest
```

For R2 and any task that changes generated HTML interactions, when Chrome
is available:

```bash
SYSTEMLENS_CHROME_BIN=/path/to/chrome \
  uv run pytest -m slow tests/test_browser_export.py
```

Before closing D9 and R6, and whenever a task changes the documented agent
workflow:

```bash
uv run python scripts/check_companion_contracts.py
```

For R1, R4, and R5, run the collection against the lab cluster from a
fresh copy of the lab application and inspect the report. State the
validation as unverified when the cluster, Chrome, or CodeQL is unavailable.

## 7. Delivery record

No task is delivered yet. Record each task here with its commit, the
validation that ran, any deviation from the work package, and, for D8, the
measured precision, abstention rate, and reviewer time.

| ID | Outcome |
|---|---|
| D1 | Open. |
| D2 | Open. |
| D3 | Open. |
| D4 | Open. |
| D5 | Open. |
| D6 | Open. |
| D7 | Open. |
| D8 | Open. |
| D9 | Open. |
| R1 | Delivered. Transaction query folds per service, type, and name with a failure filter and p50/p95/p99 of `transaction.duration.histogram`; nullable percentile columns added additively; missing histogram yields `NULL` and a collection note. Validated with fakes (`uv run pytest tests/test_runtime_observations.py`); not run against the lab cluster, which was unreachable. |
| R2 | Delivered. `observe report --hotspots N --by p95|p99|failures|errors`, `runtime.hotspots` in the export, "Points chauds" inspector whose row selects the service card and route inspector. Browser test `test_runtime_hotspot_row_selects_its_endpoint_without_moving_the_camera` passed three times with Chrome. The PRD measure is verified on a synthetic export, not on the lab export. |
| R3 | Delivered. `runtime_collections` table, `collection_id` on both observation tables, last three collections per source pruned in the same transaction, `--compare-previous` deltas gated on same source and interval; legacy rows without collection reported until the next collection. |
| R4 | Delivered against a fake query API: `RuntimeSource` seam, `discovery/prometheus_otel.py`, `observe prometheus --url --since --bearer-env --ca-cert`, OTel HTTP and messaging mapping, credentials in the URL rejected, token never persisted. Deviation: the Prometheus scrape interval is read from `prometheus_target_interval_length_seconds` when exposed, otherwise recorded as unknown. Unverified on the lab: no Prometheus server was reachable. |
| R5 | Delivered. `_other` overflow buckets dropped and their services recorded; page counts per query; completeness line in the report, the legend, and the inspectors, explicit when none; hotspot rows of an overflowed service read "incomplet". Deviation: the 100-page bound still stops collection with an error (unchanged contract), so `truncated` is recorded but never set by the Elastic collector. |
| R6 | Delivered. ADR-65, runtime specification restructured per source, CLI, configuration, export, UX, and PRD updated; skill rule extended to percentiles and hotspots. Lab validation of both sources is unverified: neither the Elastic cluster nor a Prometheus server answered from the delivery machine. |
