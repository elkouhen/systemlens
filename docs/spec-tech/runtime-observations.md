# Runtime observations from Elastic APM

This section owns the collection, persistence, and reconciliation of
aggregated Elastic APM metrics (ADR-60). The command contract lives in the
[CLI reference](../spec-fonc/cli.md#runtime-observations).
Section 4 covers the HTML export overlay; the
[export plan](../plans/runtime-observations-in-export.md) records its delivery.

## 1. Collection

The collector reads two Elastic APM metric data streams for one rollup
interval (`1m`, `10m`, or `60m`). They exist from Elastic APM
8.7. It never reads `traces-*` documents.

The default interval `auto` probes `60m`, `10m`, then `1m` with a
`size: 0`, `track_total_hits: 1` query on the transaction stream, and keeps
the coarsest one holding data in the window. Clusters do not all write every
rollup: Elastic 9.5 in the observability lab writes only `1m`. A wildcard
index without a match returns no hit, so a fixed wrong interval would yield
an empty collection. No rollup with data stops collection with an error.

| Data stream | Composite sources | Measures |
|---|---|---|
| `metrics-apm.service_destination.<interval>-*` | `service.name`, `span.destination.service.resource`, `service.target.type`, `event.outcome` | sum of `span.destination.service.response_time.count` and `.sum.us` |
| `metrics-apm.transaction.<interval>-*` | `service.name`, `transaction.type`, `transaction.name`, `event.outcome` | bucket `doc_count`, which honors `_doc_count` |

Both queries filter `@timestamp` on the requested window and set
`size: 0`. Optional sources use `missing_bucket`. Pages of 1,000 buckets
follow `after_key`. Collection stops with an error after 100 pages per
query, so an unexpected cardinality cannot turn into a bulk download.

The transfer is proportional to the number of distinct service, target, and
outcome combinations. At 50 services and 300 topics this is thousands of
buckets, independent of traffic volume.

Buckets are folded into one observation per kind, service, target, and
target type. `calls` sums every outcome; `failures` sums `failure`.
Transaction names drop any query string. The cluster URL must not embed
credentials. The API key comes from an environment variable and is sent as
an `ApiKey` authorization header; it is never logged or persisted.

## 2. Persistence

Table `runtime_observations` stores one row per observation: `id`, `source`,
`origin`, `kind`, `service_name`, `target`, `target_type`, `calls`,
`failures`, `duration_sum_us`, `window_start`, `window_end`, and
`collected_at`. `origin` is the cluster scheme, host, and port only.

A collection replaces every row of its source in one transaction. Indexing
never reads or clears the table, so observations survive a reindex. The
table is created on the first write. A read-only open of an older index
without the table returns no observations; `SCHEMA_VERSION` is unchanged.

## 3. Reconciliation

Reconciliation reads persisted observations and the persisted inventory. It
never contacts Elasticsearch and never changes source or enrichment facts.

Service mapping, in order:

1. An explicit `--service-map APM=SERVICE` entry, then a
   `runtime.service_map` entry from the project config.
2. A case-insensitive exact match with an indexed service name.
3. A unique indexed service name contained in the APM name on `-`, `_`, `.`
   or string boundaries.

Several candidates make the service `ambiguous`; none makes it `unmapped`.
Observations of an unresolved service are `not_compared`.

| Observation | Rule | Status |
|---|---|---|
| Destination `kafka/<topic>`, or target type `kafka` | Mapped service has a static, non-dynamic Kafka producer for the topic | `matched`, else `observed_only` |
| Other destination `host[:port]` | Host maps to an indexed service and a REST edge links both services | `matched` |
| Same | Host maps to an indexed service without such an edge | `observed_only` |
| Same | Host maps to no indexed service | `external` |
| Destination of a database or cache type | Out of scope | `not_compared` |
| Transaction `request` named `METHOD /route` | Mapped service serves that method and route | `matched`, else `observed_only` |
| Transaction `messaging` naming a topic | Mapped service consumes that topic | `matched`, else `observed_only` |
| Any other transaction | Out of scope | `not_compared` |

Routes compare after collapsing whitespace and replacing each `{name}` path
variable with `{}`. Messaging names accept `<topic> process`,
`<topic> receive`, and `Kafka record from <topic>`.

For each indexed service with at least one mapped observation, a static
Kafka producer, Kafka consumer, or REST server endpoint without a matching
observation is `static_only` only when the service's telemetry proves it
would name that traffic. Otherwise it is `unverified`, with a reason:

| Integration | Silence requires, for the same service |
|---|---|
| REST server | Request transactions named `METHOD /route`, and none named without a route |
| Kafka consumer | Messaging transactions naming a topic, and none without one |
| Kafka producer | At least one Kafka destination |

OTel agents without `http.route` name server transactions `POST`; the lab
does. Such traffic exists but cannot be attributed to a route, so silence
would be a false claim. Services with no observation are listed apart:
missing telemetry is not evidence of an unused integration.
`rest_call_covered` lists services with at least one HTTP destination; only
their unmatched REST calls are shown as silent in the export.

`observed_only` on a service with a dynamic producer or caller keeps that
hint, since a dynamic expression may explain the runtime target.

Each observation row lists the `endpoint_ids` that justify a `matched`
status: the producer, consumer, or server endpoints, or the caller endpoints
of the REST edges between both services. A matched REST destination also
names its `edge` services. Routes that normalize alike share one observation.
Each `static_only` item lists its `endpoint_ids`. Other rows list none.

## 4. Export overlay

`systemlens export microservices --html` reconciles persisted observations
against the exported services and edges, using the configured service map.
The JSON export ignores them. An invalid map target stops the HTML export
with exit code 2, like `observe report`.

The view model gains a `runtime` key only when observations exist and the
export is not `--call-graphs-only`. Every other key stays identical.

| Field | Content |
|---|---|
| `window`, `summary` | Collection window and origin; counts per status |
| `observations` | Reconciliation rows, with `endpoint_ids` and `edge` |
| `endpoint_observations` | Endpoint ID to the indexes of its observation rows |
| `silent_endpoint_ids`, `silent` | `static_only` endpoints and items |
| `unverified`, `rest_call_covered` | Unverifiable items; services whose REST calls can be silent |
| `services` | Indexed service to `observed` or `no_telemetry` |
| `unresolved_services` | Unmapped or ambiguous APM service names |

The browser sums an arc's counts over the distinct observation indexes of its
`endpoint_ids`, so a service-pair count shared by several call sites is
counted once. A REST arc whose caller is observed but has no matching
destination is silent. The block is linear in observations plus endpoints.

In a selected call tree, a card reads the traffic of its input endpoint:
the flow trigger for the root, the arc target otherwise. An arc reads its
output endpoint as emitted traffic and its input endpoint as received
traffic. It is styled by the received count, or by the emitted count when
the input has none. The two counts are never summed.

## 5. Invariants and limits

- Observations never create, confirm, or remove a source fact or edge.
- Counts depend on agent sampling and APM version; they are lower bounds.
- `static_only` means no traffic in the window, not dead code. It is never
  asserted without instrumentation evidence; `unverified` is used instead.
- Host-to-service mapping uses the same conservative rule as service names.
- Reconciliation is linear in observations plus indexed endpoints, with
  hash lookups by service and topic or route.
