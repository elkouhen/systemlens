# Runtime observations

This section owns the collection, persistence, and reconciliation of
aggregated runtime metrics (ADR-60, ADR-65). The command contract lives in
the [CLI reference](../spec-fonc/cli.md#runtime-observations).
Section 5 covers the HTML export overlay; the export plan
(`docs/plans/runtime-observations-in-export.md`, in Git at `4a12ad5`)
records its delivery.

## 1. Runtime sources

Every backend implements the `RuntimeSource` seam of
`application/runtime_observations.py`: `collect(window, now)` returns
observations, error groups when the backend groups exceptions, and one
collection record stating how complete the aggregation was. A source folds
its own metric model into the shared row vocabulary, so reconciliation,
hotspots, comparison, and the overlay never depend on the backend. A source
never reads source facts and never persists anything itself.

The `source` column names the backend: `elastic_apm` or `prometheus`.
Observations of several sources coexist; the report and the overlay name
each one and show its completeness line.

### 1.1 Elastic APM

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
| `metrics-apm.transaction.<interval>-*` | `service.name`, `transaction.type`, `transaction.name` | bucket `doc_count` (honors `_doc_count`); a `failures` filter on `event.outcome: failure`; `percentiles` 50, 95, 99 of `transaction.duration.histogram` |

Both queries filter `@timestamp` on the requested window and set
`size: 0`. Optional sources use `missing_bucket`. Pages of 1,000 buckets
follow `after_key`. Collection stops with an error after 100 pages per
query, so an unexpected cardinality cannot turn into a bulk download. The
number of pages each query read is recorded on the collection.

The transfer is proportional to the number of distinct service, target, and
outcome combinations. At 50 services and 300 topics this is thousands of
buckets, independent of traffic volume.

#### Percentiles

Elasticsearch computes p50, p95, and p99 from the pre-aggregated
`transaction.duration.histogram` field of each transaction bucket. They are
approximations (TDigest over histogram buckets) and are persisted as such.
Transaction names that differ only by query string fold into one row; the
first histogram wins, so a percentile is never averaged across buckets. A
bucket whose histogram field is absent or empty yields `NULL` percentiles
and the collection carries a note; it is never an error. The destination
stream exposes only a sum and a count, so a destination row keeps an
average (`duration_sum_us / calls`) and no percentile; the report says so.

#### Overflow

A terms aggregation that exceeds its limit folds the remainder into an
`_other` key. The collector detects `_other` in the service name, the
transaction name, or the destination resource, drops that bucket, and
records the affected service on the collection. Counts of that service are
shown with the lower-bound wording, and its entry points are `incomplete`
in the hotspot ranking.

#### Error groups

A third query aggregates `logs-apm.error-*` with composite sources
`service.name` and `error.grouping_key`. Each bucket keeps its `doc_count`,
the maximum `@timestamp`, and the most frequent `error.exception.type`,
`error.culprit`, and `transaction.name` through `terms` of size 1. It reads
no document and no message field: messages can carry business data or
secrets. Paging, window, and the 100-page bound match the metric queries.

A wildcard index without a match, or one the API key may not read, returns
no bucket rather than an error. The key therefore needs `read` on
`logs-apm.error-*` as well as `metrics-apm*`; without it, errors are
silently absent. Errors that an application catches and maps to a response,
as in the lab's `@ExceptionHandler`, produce no APM error document; they
still appear as transaction failures.

#### Folding

Buckets are folded into one observation per kind, service, target, and
target type. `calls` sums every outcome; `failures` sums `failure`.
Transaction names drop any query string. The cluster URL must not embed
credentials. The API key comes from an environment variable and is sent as
an `ApiKey` authorization header; it is never logged or persisted.

### 1.2 Prometheus with OpenTelemetry metrics

The collector uses only the HTTP query API (`GET /api/v1/query`). Each
query is an instant query over a range vector computed server side, such
as `sum by (...) (increase(<metric>_count[7d]))`, so one value per series
comes back and no sample is downloaded. The window is rendered as a PromQL
duration (`7d`, `36h`).

| OpenTelemetry metric | Labels used | Observation |
|---|---|---|
| `http.server.request.duration` | `service_name` or `job`, `http_request_method`, `http_route`, `http_response_status_code`, `error_type` | transaction `METHOD /route`, type `request` |
| `http.client.request.duration` | same service label, `server_address`, `server_port`, status, `error_type` | destination `host[:port]`, type `http`, with the summed duration |
| `messaging.client.operation.duration` | same service label, `messaging_destination_name`, `messaging_operation_type` or `_name`, `messaging_system`, `error_type` | `publish`, `create`, or `send`: destination `<system>/<topic>`; `process`, `receive`, or `deliver`: transaction `<topic> process`, type `messaging` |

Metric names are accepted with the `_seconds` unit suffix and without it;
the older `messaging.publish.duration` and `messaging.process.duration`
names are read when the unified metric is absent. A server failure is a
5xx status or an `error_type`; a client failure is a status of 400 or
more or an `error_type`. A series without `http_route` is kept under its
method only, so reconciliation reports it as not attributable to a route;
the collection notes how many such series were seen.

Percentiles are `histogram_quantile(0.5|0.95|0.99, sum by (le, ...)
(increase(<metric>_bucket[window])))`, computed by Prometheus from the
exported buckets and converted to microseconds. They are approximations
bounded by the bucket boundaries. Destinations keep a sum and no
percentile. Prometheus exports no exception grouping, so a collection has
no error group; the collection says so.

The scrape interval is read from
`prometheus_target_interval_length_seconds{quantile="0.5"}` when the server
exposes it and recorded as the collection `step`; the range vector length
is recorded as the `interval`. The server URL must not embed credentials.
A bearer token comes from an environment variable, is sent as an
`Authorization: Bearer` header, and is never logged or persisted.

## 2. Persistence

Table `runtime_observations` stores one row per observation: `id`, `source`,
`origin`, `kind`, `service_name`, `target`, `target_type`, `calls`,
`failures`, `duration_sum_us`, `window_start`, `window_end`,
`collected_at`, the nullable `duration_p50_us`, `duration_p95_us`, and
`duration_p99_us`, and the nullable `collection_id`. `origin` is the
backend scheme, host, and port only. The row `id` hashes the source, the
collection, and the target, so several collections keep distinct rows.

Table `runtime_error_groups` stores one row per service and grouping key:
`id`, `source`, `origin`, `service_name`, `grouping_key`, `exception_type`,
`culprit`, `transaction_name`, `occurrences`, `last_seen`, the window
fields, and `collection_id`. It holds no error message.

Table `runtime_collections` stores one row per collection run: `id`,
`source`, `origin`, `window_start`, `window_end`, `interval`, `step`,
`collected_at`, `page_counts` (JSON per query), `truncated`,
`overflow_services` (JSON list), and `notes` (JSON list).

A collection inserts its record and its rows, then keeps the last three
collections of its source and deletes the rows of older ones, all in one
transaction. Readers return the newest collection of each source by
default and can read one collection by ID. Rows written before collection
history exist without a collection; they are reported as the current rows
of their source until the next collection of that source replaces them,
with `NULL` percentiles and an unknown completeness. Indexing never reads
or clears these tables, so observations survive a reindex. The tables and
columns are created or added on open; a read-only open of an older index
without them returns no observations.

## 3. Reconciliation

Reconciliation reads persisted observations and the persisted inventory. It
never contacts a backend and never changes source or enrichment facts.

Service mapping, in order:

1. An explicit `--service-map APM=SERVICE` entry, then a
   `runtime.service_map` entry from the project config.
2. A case-insensitive exact match with an indexed service name.
3. A unique indexed service name contained in the APM name on `-`, `_`, `.`
   or string boundaries.

Several candidates make the service `ambiguous`; none makes it `unmapped`.
Observations of an unresolved service are `not_compared`. A service listed
in `runtime.ignore_services` skips mapping: its observations are
`not_compared`, it is not reported as unresolved, and it proves no coverage.

| Observation | Rule | Status |
|---|---|---|
| Destination `kafka/<topic>`, or target type `kafka` | Mapped service has a static, non-dynamic Kafka producer for the topic | `matched`, else `observed_only` |
| Other destination `host[:port]` | Host maps to an indexed service and a REST edge links both services | `matched` |
| Same | Host maps to an indexed service without such an edge | `observed_only` |
| Same | Host maps to no indexed service | `external` |
| Destination of a database or cache type | Out of scope | `not_compared` |
| Transaction `request` named `METHOD /route` | Mapped service serves that method and route | `matched`, else `observed_only` |
| Transaction `messaging` naming a topic | Mapped service consumes that topic | `matched`, else `observed_only` |
| Transaction named `Class.method`, other type | A `@Scheduled` flow of the mapped service has that simple class and method | `matched`, else `not_compared` |
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

A request transaction named only by its method cannot be attributed to a
route: it may be an unrouted server request or a root HTTP client span. In
the lab, Micrometer's OTLP metric exports appear this way. Asserting
silence there would be a false claim. Services with no observation are
listed apart: missing telemetry is not evidence of an unused integration.
`rest_call_covered` lists services with at least one HTTP destination; only
their unmatched REST calls are shown as silent in the export.

`observed_only` on a service with a dynamic producer or caller keeps that
hint, since a dynamic expression may explain the runtime target.

Each observation row lists the `endpoint_ids` that justify a `matched`
status: the producer, consumer, or server endpoints, or the caller endpoints
of the REST edges between both services. A matched REST destination also
names its `edge` services. Routes that normalize alike share one observation.
Each `static_only` item lists its `endpoint_ids`. Other rows list none.

Each row also carries its `source`, `p50_us`, `p95_us`, and `p99_us`
(transactions, `null` when the backend gave none), `avg_us` (destinations),
and a `completeness` of `complete` or `incomplete`; the latter marks a row
of a service that overflowed in its collection. The `window` names the
`sources` present and lists one `collections` entry per source with its
interval, step, page counts, overflow services, truncation flag, notes, and
a rendered completeness `line`.

Error groups of ignored services are dropped. Others keep their mapped
service and, when their transaction name designates an indexed served route
or consumed topic, that entry point's `endpoint_ids`. They change no status:
errors are reported beside the reconciliation, under `errors`.

## 4. Hotspots and comparison

`rank_hotspots(result, by, limit)` orders the transaction rows by `p95`,
`p99`, `failures`, or `errors`; `errors` sums the occurrences of the error
groups whose service and transaction name match the row. Ties break by
service then target. Rows that are `not_compared` are listed apart, with
the `unverified` indexed entries of the window, because their value cannot
be attributed to an indexed entry point. Rows without the metric are
counted, not ranked: a missing percentile is not zero latency. Each ranked
row carries its `endpoint_ids`, service, window, calls, value, source, and
`completeness`. The export overlay ranks by `p95` when any row has one,
otherwise by `failures`, and keeps ten rows.

`compare_with_previous` needs a previous collection of the same source and
the same interval; otherwise it states why no comparison is shown. For
each row matched on kind, service name, target, and target type it gives
the delta of calls, of the failure ratio, and of p95 when both runs have
one. The deltas are differences of lower bounds, never a rate.

## 5. Export overlay

`systemlens export microservices --html` reconciles persisted observations
against the exported services and edges, using the configured service map.
The JSON export ignores them. An invalid map target stops the HTML export
with exit code 2, like `observe report`.

The view model gains a `runtime` key only when observations exist and the
export is not `--call-graphs-only`. Every other key stays identical.

| Field | Content |
|---|---|
| `window`, `summary` | Collection window, sources, and completeness per source; counts per status |
| `observations` | Reconciliation rows, with `endpoint_ids`, `edge`, percentiles, and `completeness` |
| `hotspots` | The ranking of section 4: `by`, `rows`, `without_metric`, `not_compared`, `unverified` |
| `endpoint_observations` | Endpoint ID to the indexes of its observation rows |
| `silent_endpoint_ids`, `silent` | `static_only` endpoints and items |
| `unverified`, `rest_call_covered` | Unverifiable items; services whose REST calls can be silent |
| `services` | Indexed service to `observed` or `no_telemetry` |
| `unresolved_services` | Unmapped or ambiguous APM service names |
| `errors`, `endpoint_errors` | Error groups; endpoint ID to their indexes |
| `data_destinations` | Per service, database destinations with calls and failures |

The browser sums an edge's counts over the distinct observation indexes of its
`endpoint_ids`, so a service-pair count shared by several call sites is
counted once. A REST edge whose caller is observed but has no matching
destination is silent. The block is linear in observations plus endpoints.

In a selected call tree, a card reads the traffic of its input endpoint:
the flow trigger for the root, the edge target otherwise. An edge reads its
output endpoint as emitted traffic and its input endpoint as received
traffic. It is styled by the received count, or by the emitted count when the input
has none or when only the emitted side is failing: APM counts a 4xx as a
caller failure but a server success.

A `@Scheduled` trigger has no endpoint, so a matched scheduler transaction
lists the key `cron:<path>:<line>` of the trigger step; the root card of that
flow reads it. Unmatched `Class.method` names stay `not_compared`, because
repository and helper spans share that shape. A card with data-access
annotations shows its service's database destinations whose engine matches
them (relational for JPA, `mongodb` for collections): APM measures database
calls per service and engine, never per table or collection. The two counts are never summed. A card
whose input endpoint has error groups turns red and lists them.

The legend's runtime section shows one completeness line per source and a
"Points chauds" button. Its inspector lists the ranked rows; selecting one
selects the service card and opens the route inspector of the matched
served route, through the existing selection path, so the edges into that
route highlight. The ranking draws no edge and moves no camera.

## 6. Invariants and limits

- Observations never create, confirm, or remove a source fact or edge. A
  second source follows the same rule.
- Counts depend on agent sampling and backend version; they are lower
  bounds. Percentiles are backend approximations over aggregated
  histograms; the CLI and the overlay say so wherever they show one.
- `static_only` means no traffic in the window, not dead code. It is never
  asserted without instrumentation evidence; `unverified` is used instead.
- Host-to-service mapping uses the same conservative rule as service names.
- An overflowed service keeps its rows; they are marked incomplete, never
  dropped silently. Absence of a completeness record is reported as
  unknown, not as complete.
- Reconciliation is linear in observations plus indexed endpoints, with
  hash lookups by service and topic or route. The hotspot ranking sorts
  the transaction rows once.
