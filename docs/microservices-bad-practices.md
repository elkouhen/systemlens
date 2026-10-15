# Microservices bad practices

Prioritize bounded diagnostics for synchronous calls without established time
budgets and for structural contract changes between two known versions. A
database update and event publication is a third candidate. Lost-update analysis
is a valuable follow-up because indexed data operations provide part of its
evidence, but a first release should report review candidates rather than claim a
concurrency defect. The other patterns remain review questions until their
decisive evidence is available.

This is a feasibility assessment for SystemLens, not a list of implemented
checks or measured defect rates. Static evidence can identify a candidate or
establish a narrow configuration fact. It cannot establish that a runtime
incident occurred. Missing evidence must produce an unknown result.

## Assessment method

The ratings below are estimates for SystemLens's current Java and Spring scope.
**Value** is the potential benefit to an analyst reviewing an integration,
not the prevalence of the pattern. **Static signal** rates whether indexed
source, with a bounded extractor extension, can locate a useful candidate.
**Precision** estimates whether a
reported candidate would survive review under the stated conditions; no
precision or recall has been measured.

**Dependencies** rates how much external configuration or semantic evidence is
needed: low, medium, or high. **Effort** is an estimated relative implementation
cost: small, medium, or large. Neither rating is a delivery estimate. A high
value with low precision does not justify an automatic defect verdict.

SystemLens already persists potential HTTP and Kafka flows and some MongoDB
accesses, but lexical order does not prove execution or commit order. It also
stores OpenAPI and AsyncAPI documents. JPA entity declarations do not identify
physical tables or read/write operations. The [flow model](spec-tech/data-model.md)
and [product scope](PRD.md) define these boundaries. The existing
[async-write/sync-read diagnostic](SPEC-FONC.md) covers the first row as
a potential consistency risk.

## Comparative assessment

The precision ratings apply to the limited signals in the fourth column.
They do not claim that static analysis can prove the absence of a problem.

| Practice and main risk | Value | Static signal | Expected precision | Dependencies | Effort |
|---|---|---|---|---|---|
| Synchronous read after a causally related asynchronous write request without visibility confirmation; stale data | High | Medium: pair reachable accesses | Medium: potential ordering risk | High: keys, causality, visibility | Medium to large for source extraction |
| Event publication before the related database commit; consumer sees uncommitted data | High | Low to medium: find publication and transaction sites | Low without commit semantics | High: transaction manager, Kafka configuration | Large |
| Fresh data required from an asynchronously updated CQRS projection; stale decision | High when freshness is required | Low: locate projection and read | Low without the freshness requirement | High: business rule, projection lag | Large |
| Cache read before asynchronous invalidation; stale cached value | Medium to high | Low: locate read and invalidation | Low without matching keys and policy | High: keys, TTL, external cache | Large |
| Database update and event publication without recovery from partial failure; missing or premature event | High | Medium after data operations join internal flows | Medium for a candidate, low for a failure claim | High: operation evidence, transaction, outbox, CDC, recovery | Large |
| Redelivered message processed without idempotency; duplicate business effect | High | Low: locate listener and effect | Low for absence of protection | High: constraints, deduplication, downstream guarantees | Large |
| Event order assumed across Kafka partitions; dependent updates out of order | Medium | Medium: inspect producer key and handler | Low to medium without the dependency | High: partitioning and business order | Medium to large |
| Concurrent read-modify-write without conflict protection; lost update | High | Medium for a same-method read and write to the same known resource; low for value dependence and record identity | Low unless the value dependency, record key, transaction boundary, and protection are understood | High: database semantics, isolation, locks, versioning | Large |
| Availability check before action without atomic reservation; overselling | High when inventory is shared | Low: locate check and action | Low without the inventory invariant | High: business constraint, database enforcement | Large |
| Late asynchronous action after compensation; cancelled action takes effect | High when sagas are used | Low: locate both handlers | Low without state transitions | High: saga policy and guards | Large |
| Side effect retried after timeout without idempotency; duplicate effect | High | Medium: find retry and remote effect | Low to medium without remote guarantees | High: retry policy, idempotency key | Medium to large |
| Direct access to another service's private tables; schema coupling | High | Medium if physical access is known | High only with attested ownership | High: database identity, table, owner | Large with current data model |
| Synchronous call chain without established timeouts or overall deadline; held resources | High | Medium to high for supported clients | Medium to high for explicit configuration gaps | Medium to high: inherited client and proxy settings | Medium |
| Retries at several layers without bounds and backoff; amplified load | High | Low to medium: find local retry layers | Medium only with complete policies | High: client, library, proxy, broker | Medium to large |
| Incompatible API or event contract change; consumer failure | High | High with two comparable versions | High for bounded structural changes | Medium: baseline, compatibility direction, consumers | Medium |

Transaction and messaging limits are described by [Spring transaction-bound
events](https://docs.spring.io/spring-framework/reference/data-access/transaction/event.html),
[Spring Kafka synchronization](https://docs.spring.io/spring-kafka/reference/tips.html),
the [transactional outbox](https://learn.microsoft.com/en-us/azure/architecture/databases/guide/transactional-out-box-cosmos),
and [Kafka partition semantics](https://kafka.apache.org/protocol.html).

Consistency and concurrency limits are described by [CQRS](https://learn.microsoft.com/en-us/azure/architecture/patterns/cqrs),
[cache-aside](https://learn.microsoft.com/en-us/azure/architecture/patterns/cache-aside),
[PostgreSQL isolation](https://www.postgresql.org/docs/current/transaction-iso.html),
[sagas](https://learn.microsoft.com/en-us/azure/architecture/patterns/saga),
and [idempotent consumers](https://learn.microsoft.com/en-us/azure/architecture/patterns/idempotent-consumer).

[AWS retry](https://docs.aws.amazon.com/wellarchitected/2023-04-10/framework/rel_mitigate_interaction_failure_limit_retries.html)
and [timeout](https://docs.aws.amazon.com/wellarchitected/2023-04-10/framework/rel_mitigate_interaction_failure_client_timeouts.html)
guidance covers remote-call failures. [Data ownership](https://learn.microsoft.com/en-us/azure/architecture/microservices/design/data-considerations)
and [API versioning](https://learn.microsoft.com/en-us/azure/architecture/microservices/design/api-design)
guidance covers service boundaries and contracts. These sources support the
failure mechanisms; the ratings are estimates based on indexed evidence.

## Recommended directions

First, assess time budgets on known synchronous HTTP paths. Resolve explicit
connection, read, and overall deadlines from supported client and inherited
configuration. Report a bounded configuration gap only when the relevant
settings are accounted for; otherwise report unknown. Global client builders,
library defaults, and proxies may supply a deadline that is absent beside the
call. This work reuses indexed HTTP paths and can link a finding to its source.

Second, compare a supplied reference OpenAPI contract with the indexed version.
Start with removed operations, removed response fields, and newly required
request parameters, then show affected consumers when their use is known.
Specify compatibility direction and version selection before classifying a
change. An unindexed external consumer and a semantic behavior change remain
unknown. Event contracts can follow once their format and compatibility policy
are explicit. [OpenAPI diff](https://github.com/OpenAPITools/openapi-diff/blob/master/README.md)
demonstrates a structural comparison; [schema compatibility guidance](https://docs.confluent.io/platform/current/schema-registry/fundamentals/schema-evolution.html)
shows why event compatibility depends on policy.

Third, prototype a database update and event publication candidate on a small
annotated Spring corpus. First, classify source-located data operations and
attach them to internal call paths: the current [MongoDB flow projection](../src/systemlens/indexing/code_flows.py)
only attaches same-method accesses as reads or writes, while the [JPA inventory](spec-tech/data-model.md)
records declarations without operations. Preserve the two source sites and any
identified transaction or recovery mechanism. Treat an unknown outbox, CDC
process, or transaction manager as missing evidence rather than proof of unsafe delivery.
Spring can synchronize Kafka publication with a database transaction, and a
transaction-bound listener can defer handling until commit. This limits the
precision of a rule based on source order alone.

Treat lost-update analysis as a separate follow-up built on the same operation
facts. A useful candidate requires evidence that a write value depends on an
earlier read of the same record, plus the transaction boundary and any applicable
conflict control. A matching entity name is insufficient: the record key may be
unknown, aliases may obscure the value flow, and another layer may provide
protection. `@Transactional` alone does not establish conflict protection, and
the indexed source may not identify the database isolation mode. Report
“potential read-modify-write; protection not established” only when the read,
write, and value dependency are source-backed. Otherwise abstain or show the
operations as separate facts. Recognize a protection mechanism only when its
scope and applicability to that update are established. Validate the candidate
against protected, unprotected, and ambiguous examples before considering an
automatic finding. The current operation facts do not yet establish these
conditions, so this is not among the first three bounded diagnostics.

Before promoting any candidate to a user-facing check, evaluate it on positive,
negative, and ambiguous examples from more than one repository. Record
confirmed findings, missed cases, abstentions, and reviewer time per finding.
No such benchmark is reported here. A new diagnostic would also require an
explicit product-scope decision and updates to the functional and technical
specifications.
