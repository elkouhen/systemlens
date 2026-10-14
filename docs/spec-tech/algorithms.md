# Core SystemLens algorithms

SystemLens turns Java/Spring source into a persisted architecture snapshot,
then projects that snapshot into service graphs, call trees, and diagnostics.
The stages below follow that order. Each section explains the decision the
algorithm makes; the linked specifications own its detailed contract.

## 1. Incremental indexing

The indexer discovers build modules and inventories eligible source files.
It compares file hashes and analysis signatures with the stored snapshot to
decide which files need extraction. Changes to shared configuration or build
descriptors can expand the refresh to a module or the whole repository.

The indexer removes facts from deleted files within a transaction. CodeQL
stages can commit partial checkpoints before final reconciliation. A later
failure rolls back only changes since the last commit; an earlier checkpoint
remains available to readers. See [checkpoint persistence](persistence.md).

Exports read the persisted snapshot. The inventory still scans eligible files
on every index run, even when extraction is incremental. See
[indexing](indexing.md) and
[`file_inventory.py`](../../src/systemlens/indexing/file_inventory.py).

## 2. Static source extraction

Java AST extractors identify Spring HTTP routes and clients, Kafka listeners
and publishers, scheduled triggers, and MongoDB or JPA operations. Build and
configuration discovery assigns evidence to modules and resolves supported
properties, literals, and contracts. Source-derived endpoint and flow evidence
retains project-relative paths and source lines.

The extractors preserve unresolved expressions and ambiguous targets. For
example, a Kafka topic chosen by a statically resolved branch yields a fact
for each possible topic; a partly dynamic topic remains unresolved. See
[extractors](extractors.md) and the
[`scanner` package](../../src/systemlens/scanner).

## 3. Service topology resolution

The topology resolver connects an HTTP call to a served route after it
identifies one target service from source, contract, or configuration evidence.
It then checks the HTTP method and route within that service. A compatible
route name by itself does not establish the target service.

For Kafka, the resolver pairs producers and consumers on a concrete topic.
Conflicting known message types prevent a pair; a missing type lowers its
confidence. Dynamic topics do not create a producer to consumer relation.
Unresolved calls remain indexed endpoint facts. See [extraction and matching](extractors.md)
and [`build_graph`](../../src/systemlens/domain/graph.py).

## 4. Java method call graph

When available, CodeQL supplies source-located Java methods and directed
caller to callee edges. SystemLens anchors indexed entries and outputs to
those methods, then traverses reachable calls to find possible routes. An
AST symbol fallback adds only source-supported candidates and marks possible
dispatch with lower confidence.

The traversal keeps call direction, source evidence, ambiguity, and cycles.
Bounded fallback reconstruction can miss a deeper route even when a direct
CodeQL reachability query finds its entry and output. A call between Java
methods does not by itself establish a call between services. See
[call graph indexing](indexing.md) and
[`code_flows.py`](../../src/systemlens/indexing/code_flows.py).

## 5. Internal flow materialization

An internal flow (`CodeFlow`) is a persisted, ordered potential path starting
at an indexed HTTP or Kafka input, or a source-evidenced scheduled trigger.
Its steps can include Java method calls, HTTP calls, Kafka publications, and
MongoDB or JPA accesses. A flow retains relative source locations, confidence,
status, and topology reconciliation state.

Within one method, SystemLens can put several effects in the same flow. Across
methods, it can emit separate paths to reachable outputs or data operations.
A cycle can also end a recorded path without an output or database access.
The Java traversal can use shared helper modules, but stops at another
discovered application module. For example:

```text
POST /orders -> OrderController -> OrderService -> OrderRepository.save
```

This path suggests a database write; it does not prove branch execution or
transaction commit. `systemlens flows calculate` rebuilds flows from the
persisted endpoint, method, call edge, module, and topology facts without
reparsing Java. See the [flow model](data-model.md) and
[flow reconstruction](indexing.md).

## 6. Microservice call graph and tree

The export selects root flows with no upstream indexed flow, plus a stable
representative for each uncovered component. From each root, it finds the
associated HTTP calls or Kafka publications. For each resolved output to input
relation, it continues with flows triggered by that exact input endpoint in
the next service. Repeating this traversal retains fan out, convergence, and
cycles in the complete directed graph.

The selector then omits a root graph when its services and directed arcs are
contained in a strictly larger root graph. Arc identity includes the source
and target services, protocol, label, and endpoint IDs. Equal graphs retain
their separate flow entries. This filter changes which roots are selectable;
it does not delete the persisted internal flows.

For example, an HTTP flow in A reaches B; a flow in B publishes an event
consumed by C and D:

```text
POST /orders -> A
                  `- POST /reserve -> B
                                        |- event -> C
                                        `- event -> D
```

The displayed tree unfolds the selected flow's causal transitions. The same
service can appear on different branches. A repeated input endpoint or flow
on one branch marks a cycle; an input without an indexed consumer flow becomes
a leaf with unknown continuation. The display can also hide descendants at
the chosen depth or behind a collapsed branch. See the
[call graph contract](data-model.md#call-graph-descriptions),
[`call_graph.py`](../../src/systemlens/render/call_graph.py), and
[tree display rules](../spec-fonc/html-export.md).

## 7. Database access association

Data access paths support annotations on service occurrences in the call
tree. An access matches an occurrence through its flow ID or its service,
input endpoint, and handler method. A leaf without an indexed consumer flow
can still receive access evidence through its service and exact input
endpoint. A scheduled flow without an input port uses its trigger and handler
method. An unmatched access gets no service badge.

A badge has route support when its access flow ID belongs to the occurrence,
or its complete, nonempty Java call path is a prefix of an occurrence flow's
call path. Each matched call must have the same method name, source path,
and line. Sharing only the first call is insufficient when later calls diverge.

Associations supported only by the handler or input remain potential and add
`?` after the entity name, including accesses on unknown-continuation leaves.
Operations on the same resource are grouped for that occurrence. `C`, `R`,
`U`, and `D` mean create, read, update, and delete; `C/U` means create or update,
without asserting that both happened. See
[data access projection](data-model.md) and
[badge behavior](../spec-fonc/html-export.md).

## 8. Data consistency diagnostics

The current detector compares asynchronous writes and synchronous reads on
shared reachable triggers, then applies resource, key, causal, source-order,
and visibility evidence. Advanced checks require explicit resource/key and
concurrency facts. The detector reports potential risks, identified guards or
guarantees, and insufficient evidence; it does not claim runtime incidents.

The detailed model below retains the fuller event-order design, pseudocode,
and acceptance criteria from the detector design. Those sections
describe design goals where they call for a lifecycle ordering graph or runtime
evidence; the implementation remains limited to the rules stated above and in
the [data model specification](data-model.md).

### Design summary


The detector should mark two classes of potential anomalies on the call graph:

1. A synchronous read may run before an asynchronous write becomes visible.
2. Two writes to the same data may overlap without an identified ordering or conflict guard.

A lost update is a narrower second case. It requires two read-modify-write operations on the same record, plus evidence that each written value depends on a value read earlier. The graph can show a candidate and its evidence; static indexing cannot claim that a runtime race occurred.

`src/systemlens/application/flow_consistency.py` implements the classifications
below when the persisted flow includes the required evidence. Existing source
indexing still supplies only a subset: action, resource, and a trigger-derived
branch. It does not yet infer transaction scope, record keys, modified fields,
value dependencies, isolation, or scheduler concurrency. Such source-indexed
flows therefore abstain from the advanced detectors. Direct flow manifests can
carry reviewed evidence for those fields. The graph never treats missing facts
as proof of safety or concurrency.

> **Why & What - compare event order**
>
> **What:** Model operation start, commit, visibility, and completion as separate events related by an explicit partial order.
>
> **Why:** Line order, a shared trigger, or an asynchronous branch label cannot establish when another service's write becomes visible to a read. Operation intervals also distinguish overlapping writes from writes that merely start in one order and finish in another.
>
> **Alternatives considered:** Pair accesses by resource and source order. That costs less and can remain a low-confidence hint, but it cannot support a concurrency or visibility conclusion.
>
> **Fallback:** Keep the pair as an insufficient-evidence annotation when lifecycle or ordering facts are missing.

### Event model

The algorithms operate on events attached to source-backed call-flow occurrences. An occurrence identifies one place in a selected graph, even when the same service or source method appears more than once.

Each data event contains:

| Field | Meaning |
|---|---|
| `event_id` | Stable identity for this operation within its flow occurrence |
| `phase` | Lifecycle point: `start`, `apply`, `commit`, `visible`, or `complete` |
| `kind` | `async_write_request`, `data_read`, or `data_write` |
| `action` | `read`, `insert`, `update`, `delete`, `create_or_update`, `read_update`, or `unknown` |
| `resource` | Canonical collection or entity identity, with technology and owning service |
| `key` | Resolved record key, when statically known |
| `fields` | Read or written fields, when resolved |
| `branch` | `sync`, `async`, `parallel`, or `unknown`, with source evidence |
| `causal_id` | Message or request identity propagated to the operation, when known |
| `completion` | Whether the write is merely requested, completed, committed, or visible to the relevant read |
| `transaction_id` | Static transaction scope identity, when known |
| `guard` | Version check, conditional update, lock, or other conflict control with its scope |
| `evidence` | Relative source path, line, method identity, and extraction confidence |

The existing `CodeFlowStep` already has optional `branch`, `resource`, `key`, `causal_id`, `expected_version`, `completion`, `query_shape`, and `action` fields. These fields are useful inputs, but they do not currently encode field-level dependencies, transaction boundaries, or a complete event-order relation.

The optional flow-step contract also accepts `phase`, `transaction_id`,
`concurrency`, `fields`, `value_dependencies`, `guard`, `guard_scope`,
`isolation`, and `freshness_required`. A value dependency refers to the stable
event ID `flow_id:step_order`. `concurrency` is one of `parallel`,
`independent_handlers`, `reentrant`, `sequential`, or `unknown`; only the first
three are positive overlap evidence. A guard applies only when both accesses
name the same recognized guard and its `guard_scope` equals
`resource:key`. These fields are optional and old persisted flow snapshots
remain valid.

Resources compare equal only when the index can resolve them to the same data identity. Equal entity names in different services do not prove shared storage. Different key expressions mean unknown overlap unless alias or value-flow analysis proves they resolve to the same key. A query shape such as `point` narrows a search but does not identify a record by itself. Unknown resource or key information remains visible as uncertainty.

### Order and concurrency evidence

The detector builds a directed happens-before relation over lifecycle events. An edge `A → B` means the available source or protocol evidence requires A to precede B. A missing edge means the order is unknown; it does not mean the events are concurrent. A write's `start`, `commit`, and `visible` points remain separate because a request can return before a transaction commits, and a commit may not be visible through a replica or cache.

The relation may include:

- Control-flow order when the same execution must pass through both operations and the relevant branches are compatible.
- Synchronous call and return order when the call is known to block until the target returns.
- Message-send before corresponding handler invocation when the message identity or payload mapping is established.
- Explicit completion, join, acknowledgement, transaction synchronization, or visibility barriers when their semantics and scope are indexed.
- Program order within one transaction, while keeping commit and visibility as separate events.
- Operation start before operation completion, with a commit point when transaction evidence identifies it.

An asynchronous send does not establish that the receiving write committed before another service reads. A common root flow or a shared topic alone does not establish that two operations refer to the same business record. A transaction annotation alone does not identify isolation behavior or provide a conflict guard.

A call-graph arc proves a possible call relationship, not event order or thread overlap. Build order edges only from execution and framework evidence. The order graph for one expanded occurrence must be acyclic. If contradictory evidence creates a cycle, mark the affected pair `inconsistent_order_evidence` and abstain. Loops need bounded occurrence expansion or an explicit loop summary; a repeated static source site is not a second runtime execution by itself.

The relation must preserve alternative branches. It may say that an ordering is possible only when the source-backed branch predicates are compatible. If branch compatibility cannot be resolved, the detector records `unknown` and abstains from a definite ordering claim.

The current implementation does not build the full lifecycle DAG described
above. It accepts explicit per-access concurrency and lifecycle evidence and
uses it only for conservative candidate classifications. Cyclic flow records
are excluded from advanced concurrency pairing because they lack bounded
execution occurrences.

#### Evidence states

Every candidate pair receives one of these evidence states:

- **Confirmed relation:** identity and required order are supported by explicit source evidence.
- **Potential relation:** identity is supported, but order, completion, visibility, or concurrency remains unresolved.
- **Insufficient evidence:** a required resource, key, causal link, or coverage fact is missing.
- **Not applicable:** known resource or key evidence proves the pair does not overlap, or a complete order proves the suspected interleaving cannot occur.

“Confirmed relation” describes a static relationship, not a production incident. The user interface must preserve this distinction in labels and tooltips.

### Asynchronous write followed by a synchronous read

The first detector reports `potential_async_write_read` when a flow contains an asynchronous write request and a synchronous read that may observe the same data before the requested write becomes visible.

The required evidence is:

1. A source-backed asynchronous send or dispatch event.
2. A reachable receiver operation classified as a write or update, or an explicit write annotation linked to that send.
3. A reachable synchronous read from the same selected root or a causally linked root.
4. Matching resource identity and either a matching record key or a supported query-overlap result.
5. No established visibility barrier that orders write visibility before the read.

The send-to-write link needs more than a shared topic name. Prefer a propagated `causal_id`, message key, or source-backed payload field that maps to the data key. If the link comes only from a topic and reachable handlers, report a lower-confidence candidate with the missing correlation shown.

Algorithm:

```text
index writes and reads by canonical resource

for each async send S:
    receiver_writes = writes linked to S by causal or payload evidence
    for each write W in receiver_writes:
        for each read R with a potentially overlapping resource and key:
            if S and R cannot belong to the same causal scenario:
                continue
            if resource or key overlap is unknown:
                emit insufficient_evidence(S, W, R)
                continue
            if happens_before(R.start, S.start):
                continue
            if not happens_before(S.start, R.start):
                emit insufficient_evidence(S, W, R)
                continue
            if a complete happens-before path orders visible(W) before R:
                emit guarantee_identified(S, W, R)
                continue
            emit potential_async_write_read(S, W, R)
```

The event `visible(W)` is distinct from the source call that requested W. A broker acknowledgement, method return, or transaction annotation counts as a barrier only when the indexed framework semantics establish that it also guarantees the write is visible to R. If the read begins before the asynchronous send, this detector does not call it a read-after-request event. If their order is unknown, the detector reports insufficient evidence.

If the index cannot resolve which write a send triggers, it may display an unpaired asynchronous write and read as separate events. It must not create a high-confidence pair from resource-name similarity alone.

### Potentially concurrent writes

The second detector reports `potential_concurrent_write` for two writes with
exact resource/key identity and positive explicit concurrency evidence, absent
a matching recognized guard. It records disjoint fields as a limitation and
does not infer a lost update from them. Unknown fields remain explicit.

Evidence of possible overlap can include:

- Two asynchronous branches launched from one trigger.
- Independent message handlers that may process messages for the same key concurrently.
- Reentrant or repeated invocation of the same write site through distinct call-flow occurrences.
- Parallel calls whose completion is not joined before the next dependent step.

These conditions count only when represented by the explicit `concurrency`
field. The detector does not derive them from `branch=async`, a shared root, or
repeated graph paths.

A pair that appears twice in one static graph is not sufficient evidence. The detector must distinguish separate runtime opportunities from graph duplication. Unknown broker partitioning, scheduling, or transaction behavior stays unknown.

Two calls to the same handler count as distinct execution opportunities only
when the index has evidence that the framework can invoke that handler
concurrently. A single indexed listener or a repeated graph path does not prove
that two messages overlap. Partition keys, listener concurrency, executor
configuration, and keyed serialization can change this result.

Algorithm:

```text
index writes by canonical resource and resolved key

for each candidate pair (W1, W2):
    if their resource or key is proven disjoint:
        continue
    if they cannot execute in one relevant scenario:
        continue
    if complete(W1) happens before start(W2)
       or complete(W2) happens before start(W1):
        continue
    if an applicable guard serializes or rejects this conflicting update:
        emit conflict_protection_identified(W1, W2)
        continue
    if explicit parallel or independent-handler evidence exists:
        emit potential_concurrent_write(W1, W2)
    else:
        emit insufficient_evidence(W1, W2)
```

An atomic update can protect a particular operation from a lost update while leaving a multi-operation business invariant vulnerable. The detector records the guard and its target fields or predicate; it does not treat the word “atomic” as a universal guarantee.

### Concurrent read-write interactions

The third detector reports `potential_concurrent_read_write` when a read and a write may overlap on the same resolved resource and key. This is an interaction candidate, not automatically a defect: snapshot reads, atomic updates, or a freshness policy may make the overlap valid.

The detector requires exact resource and key identity plus explicit
`concurrency` evidence on at least one access. An async branch alone is not
concurrency proof. A `sequential` marker suppresses the candidate. This first
implementation uses explicit concurrency evidence as an overlap witness; it
does not reconstruct full start/commit/end intervals. The returned interaction
therefore remains a candidate unless freshness or dirty-read facts support a
narrower classification.

Algorithm:

```text
index reads and writes by canonical resource and resolved key

for each candidate pair (R, W):
    if resource or key is proven disjoint:
        continue
    if read and write cannot occur in one relevant scenario:
        continue
    operation_overlap = may_overlap(R.start, R.complete, W.start, W.complete)
    transaction_overlap = may_overlap(R.tx_begin, R.tx_end, W.tx_begin, W.tx_end)
    if operation_overlap == no and transaction_overlap == no:
        continue
    if operation_overlap != yes and transaction_overlap != yes:
        emit insufficient_evidence(R, W)
        continue
    record the write commit and read snapshot/observation order
    record any applicable lock, version check, or atomic predicate
    if the source establishes the required freshness or isolation invariant
       and the pair can violate it:
        emit potential_read_write_anomaly(R, W)
    else:
        emit potential_concurrent_read_write(R, W)
```

The detector must distinguish “the read and write may overlap” from “the read may return an obsolete value.” The latter also needs a freshness requirement and database semantics. For example, PostgreSQL repeatable-read transactions continue to use a transaction snapshot after a concurrent commit ([transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html)). That behavior is a problem only when the caller requires a newer value. If no freshness requirement is indexed, show the read/write interaction and leave the anomaly verdict unknown.

A read of uncommitted data is a separate, narrower classification. It requires evidence that the read can observe another transaction's uncommitted write and that the configured database permits that behavior. Never infer a dirty read from source call order or from overlapping transaction intervals alone.

### Lost-update refinement

The detector may refine a concurrent-write pair to `potential_lost_update`
only when it finds two read-modify-write sequences in distinct, named
transactions. Each write's `value_dependencies` must contain its paired read's
`flow_id:step_order`; the records and keys must match; and concurrency evidence
must be present. Field overlap must be known or remain an explicit limitation.
The paired read source locations are retained in diagnostic details. The
sequences are:

```text
R1(key) → W1(key, value derived from R1)
R2(key) → W2(key, value derived from R2)
```

The reads and writes must target the same resolved record. The writes must overlap on at least one resolved field, or field overlap must remain explicitly unknown. A value-flow analysis must connect each read result to its corresponding write value. Method adjacency or a shared entity name does not establish this dependency.

The algorithm checks whether the persisted order graph admits an interleaving consistent with both local program orders and separate transaction lifecycles:

```text
begin(T1) → R1 → apply(W1) → commit(T1)
begin(T2) → R2 → apply(W2) → commit(T2)
R1 → R2 → apply(W1) → commit(T1) → apply(W2) → commit(T2)
```

This is an example witness, not the only possible interleaving. The detector tests candidate constraints against the known partial order. If they create a cycle, that interleaving is impossible under the modeled evidence. If they remain acyclic, the interleaving is possible within the model and supports a potential lost-update finding. It still does not prove that the runtime scheduler selected it. The detector reports this refinement only when both transaction occurrences are independently supported and the write values derive from their respective reads.

Before reporting the candidate, check conflict controls that apply to both writes, including version predicates, compare-and-set conditions, database locks, or serialization guarantees. `@Transactional` by itself is not a conflict control. Database isolation rules vary by engine and level; an unknown engine or isolation configuration must remain an explicit limitation. PostgreSQL documents, for example, that its repeatable-read implementation can abort conflicting updates and require a retry, while serialization behavior depends on the database's stated semantics ([PostgreSQL transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html)).

An atomic increment is not a lost update when both operations apply the increment to the current stored value. It may still violate a separate business invariant. Keep that case classified as a distinct invariant risk unless the source establishes a lost update.

### Graph presentation

Render each event on the service occurrence where its source evidence belongs. A shared pair ID links the related markers without adding a call edge to the architecture graph.

Suggested markers:

| Event | Marker text | Placement |
|---|---|---|
| Asynchronous send | `ASYNC WRITE REQUEST` | Service occurrence that publishes or dispatches the request |
| Receiver write | `WRITE` or `UPDATE` | Service occurrence that executes the data operation |
| Related synchronous read | `READ AFTER ASYNC WRITE` | Service occurrence that reads the potentially stale data |
| Write pair | `POSSIBLE CONCURRENT WRITES` | Both write occurrences, linked by the same pair ID |
| Read/write pair | `POSSIBLE CONCURRENT READ/WRITE` | Both operation occurrences, linked by the same pair ID |
| Lost-update refinement | `POSSIBLE LOST UPDATE` | Both read-modify-write occurrences, with the read-to-write dependencies shown in details |

The tooltip gives the resource, key, action, source location, confidence, causal evidence, order evidence, identified guards, and unresolved facts. Selecting a marker highlights only its pair. A dashed visual connector may link pair members; it must be clearly styled as diagnostic evidence and must not appear as a service call or data-flow edge.

When an operation is unknown, show the missing field in the tooltip. Do not silently omit an event that was considered and rejected because its key, branch, or transaction evidence was unresolved. If pair volume exceeds the view's display bound, group repeated pairs by stable identity and show the omitted count; never drop the underlying count without notice.

### Complexity and bounds

Let `E` be the number of indexed operation events and `P` the number of candidate pairs after resource indexing. Build resource and key indexes in `O(E)` expected time and space. Evaluate ordering with a directed acyclic graph and memoized reachability rather than searching every simple path. A straightforward all-pairs closure costs `O(V × (V + A))` for `V` events and `A` order edges; the implementation should instead query only candidate pairs and cache repeated reachability questions.

The pair generators must group by canonical resource before comparing keys. Unknown keys remain in a separate resource bucket and are subject to a per-resource candidate bound. When a bound is reached, emit a truncation summary with the number of unexamined pairs; do not silently call the remaining pairs safe.

Use stable event and pair IDs based on flow identity, source-relative path, source location, operation identity, and occurrence identity. This keeps selection stable across rendering while distinguishing repeated calls to the same source site.

### Acceptance criteria

- A send, receiver write, and matching synchronous read produce one linked candidate only when causal and resource evidence support the relation.
- A proven visibility barrier suppresses the stale-read candidate and records the barrier used.
- A topic match without record-key or payload correlation produces lower-confidence or insufficient evidence, never a high-confidence pairing.
- Two disjoint keys or resources do not produce a conflict candidate.
- Reachable writes without branch or scheduler evidence do not become concurrent-write findings.
- A read/write pair with possible overlap is labelled as an interaction unless a freshness or isolation invariant supports an anomaly classification.
- A possible dirty read requires both uncommitted-write visibility evidence and database semantics that allow it.
- A read and write to the same entity without value-flow evidence do not become a lost-update finding.
- A version guard, conditional update, or database lock is recognized only when its scope matches the candidate update.
- A selected pair highlights only its event occurrences and does not change the persisted call graph.
- Partial call graphs and unknown configuration produce explicit uncertainty and truncation states.
- Cyclic call paths and contradictory order edges do not become concurrency findings without a bounded execution-occurrence model.
- Evaluation records confirmed candidates, false positives, abstentions, missed cases, and review time across representative positive, negative, and ambiguous projects.

### Costs and open questions

This design improves the analyst's ability to see which operations may race and why. It builds on existing operation annotations and puts the evidence next to the call path.

The detector supports all classifications when the corresponding explicit
flow facts are present. The main remaining cost is source extraction: Java
aliases, repository key expressions, transaction scope, field-level value
flow, listener/executor concurrency, isolation configuration, and database
semantics are not yet inferred by the native indexer. These limits affect
coverage, not the meaning of missing evidence; missing evidence always abstains.

### References

The vendor documentation below was checked on 2026-10-12; database behavior depends on the selected engine and version.

- Leslie Lamport, [“Time, Clocks, and the Ordering of Events in a Distributed System”](https://lamport.azurewebsites.net/pubs/time-clocks.pdf), *Communications of the ACM*, 1978. Defines a partial order of events and motivates preserving “happened-before” separately from physical or source display order.
- PostgreSQL, [Transaction Isolation](https://www.postgresql.org/docs/current/transaction-iso.html). Documents engine-specific isolation behavior, concurrent updates, serialization failures, and retry requirements.
- MongoDB, [Atomicity and Transactions](https://www.mongodb.com/docs/manual/core/write-operations-atomicity/). Documents single-document atomicity and conditional-update behavior. These guarantees apply to MongoDB operations and must not be generalized to other stores.

The implemented classifiers use conservative evidence rules. The references support the concurrency concepts; they do not validate the detector's precision or coverage. The native Java indexer does not yet extract
all advanced evidence fields. Advanced source-derived findings therefore
require enriched flow records; missing metadata produces no concurrency claim.
