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
