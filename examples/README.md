# Examples

`async-write-sync-read-facts.json` is a synthetic, secret-free fact manifest
for exercising consistency diagnostics. It contains five persisted flow
scenarios: one potential risk, one flow with two independent risks, one
visibility guarantee, one unresolved causal link, and one proven disjoint key.

Import it into an initialized disposable repository with:

```bash
systemlens init
systemlens import-facts /path/to/systemlens/examples/async-write-sync-read-facts.json \
  --namespace async-consistency-fixture
```

The `expected_diagnostics` fields are fixture expectations for tests and review;
they are not runtime observations. Source paths are deliberately relative and
point to illustrative locations only.
The risk examples provide `request_before_reads` evidence for each paired read.
This relation establishes that the asynchronous request came first; it does not
establish when the write became visible.

For a larger demonstration, `async-write-sync-read-complex-facts.json` contains
five services, 10 HTTP routes, 10 Kafka topics with producer and consumer arcs,
and eight mixed flows. Its generated HTML counterpart is
`async-write-sync-read-complex-facts.html`.

`supermarket-three-anomalies.json` is a compact synthetic call graph for one
order flow. It demonstrates an asynchronous read-after-write risk, a concurrent
read/write interaction, and a lost-update candidate on a shared product record.
Its generated graph is `supermarket-three-anomalies.html`. The shared database,
concurrency, transaction, and value-dependency facts are illustrative inputs,
not claims about the supermarket demo application's implementation. Regenerate
the dataset and graph with:

```bash
python scripts/generate_supermarket_anomaly_fixture.py
systemlens export microservices --graph examples/supermarket-three-anomalies.json \
  --html examples/supermarket-three-anomalies.html
```
