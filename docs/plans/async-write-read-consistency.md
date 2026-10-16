# Async write and sync read consistency

Status: complete for explicit evidence. Source-derived propagation is deferred.
Owner: SystemLens implementer. Reviewers: product owner and architecture
maintainer. This plan covers the pair detector for a write performed through
an asynchronous path and a read performed synchronously.
The [technical specification](../spec-tech/algorithms.md#8-data-consistency-diagnostics)
owns the algorithm contract. The [data operations plan](data-operations-in-internal-flows.md)
owns broader extraction of data operations and key facts.

## 1. Goal

Report a possible stale read only when persisted evidence supports the relation.
Distinguish a candidate risk from a proven visibility guarantee, and keep
uncertainty visible when resource, key, causal, or ordering evidence is missing.
Static findings describe possible behavior, not observed incidents.

## 2. Findings and completed corrections

The detector compared persisted `data_write` and `data_read` steps. It used
resource and textual key equality, causal IDs or a shared reachable root, and a
`visible` completion plus matching version to classify a guarantee. It also
computed exact matches globally and used them to suppress uncertain pairs.
These rules could overstate certainty or hide a diagnostic in another root.

The detector evaluates uncertain pairs independently, so one exact match cannot
hide another possible pair. Canonical resource and key identities establish
record identity; display labels alone do not. Cross-flow pairs require matching
causal IDs and evidence that the asynchronous request precedes the read.
Guarantees also require an explicit write-to-read visibility relation. Tooltips
explain identity, causality, visibility, and uncertainty.

## 3. Implemented scope and validation

### 3.1 Explicit visibility ordering

Files: [flow model](../../src/systemlens/domain/code_flows.py), flow extraction
and persistence, [consistency detector](../../src/systemlens/application/flow_consistency.py),
and their tests.

The flow contract accepts `request_before_reads` and
`visibility_before_reads`. The detector requires the first relation to classify
a possible risk and the second to identify a guarantee. Manifest validation
rejects either relation on non-write steps and checks resolved targets are reads.
Unknown targets remain allowed for partial manifests.
Source extraction does not derive lifecycle events or scoped happens-before
evidence, including commit and visibility barriers.

Implemented: matching versions with no explicit visibility evidence never
produce a guarantee. A direct relation identifies one read event. Older flow
snapshots remain readable.

### 3.2 Resource and key identity

Files: operation and flow extraction, persisted data model, and focused tests.

Canonical resource and key identity fields are implemented. Mongo and JPA
operations receive module-scoped resource identities. The extractor derives key
identity for exact Java literals. CodeQL-derived flows also trace unmodified
scalar parameters and final scalar local aliases through resolved Java calls.
The indexer records other raw key expressions without resolving mutable aliases,
fields, and message payload values. Keep scopes distinct and retain unknown
mappings instead of guessing aliases.

Implemented: equal text without matching canonical resource and key identities
does not prove record identity. Exact literals can match within a canonical
resource. CodeQL call chains preserve unmodified scalar parameters and final
scalar aliases. Mutable aliases, fields, serialized payload values, and
cross-service message correlation remain open, so those expressions do not
match through indexed value flow.

### 3.3 Causal scenarios

Files: flow reconstruction, consistency analysis, and regression fixtures.

Cross-flow pairing now requires matching causal IDs; shared reachability alone
produces insufficient evidence. The extractor does not propagate identity
across sends and receivers or model mutually exclusive branches. Those
source-derived analyses require framework and branch semantics that the
current index does not persist.

Implemented: cross-flow pairs without matching causal IDs, or with conflicting
IDs, remain insufficient evidence even under a shared root. Source-derived
propagation and mutually exclusive branch analysis remain open.

### 3.4 Diagnostic evidence

Files: diagnostic contract, HTML export, graph renderer, and browser tests.

The detector JSON and graph tooltip expose resource/key identity, causal and
visibility evidence, and the reason for uncertainty. Browser interaction
validation remains environment-dependent.

Implemented: JSON diagnostics and graph tooltips distinguish reachability,
causal evidence, identity, visibility, and limitations.

## 4. Deferred extensions

The source index does not infer per-event causal identity through serialized
Kafka payload fields, mutually exclusive branches, transaction lifecycle, or
database visibility barriers. Equal topics, payload types, source order, and
shared roots are insufficient proof for those relations. Add source-derived
support only with framework semantics and end-to-end fixtures that establish
the specific event, record key, and ordering edge. Until then, callers can
provide explicit facts, and unknown relations remain uncertain.

## 5. Validation

Focused detector tests, Ruff, and mypy pass. The full suite has two unrelated
failures because the environment blocks localhost socket binding. Browser tests
cannot complete because Chrome aborts on launch. The companion check cannot
create its CodeQL database because the `scc` executable is unavailable.
