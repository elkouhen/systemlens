# Configuration

Parent: [Functional specification](../SPEC-FONC.md).


`systemlens init` creates `.systemlens/config.yml`:

```yaml
include: ["**/*"]
exclude: [".git/**", ".venv/**", "node_modules/**", ".systemlens/**", ".codeql/**"]
min_severity: INFO
root_path: .
analysis:
  strategy: default
  codeql: false
  call_graph_engine: source
  codeql_timeout_seconds: 600
  codeql_threads: 0
  codeql_ram_mb: null
  codeql_verbosity: null
  codeql_max_hops: 12
  codeql_edge_confidence: possible
  disabled_extractors: []
```

`systemlens init --profile codeql` writes the same file with `codeql: true`
and `call_graph_engine: codeql`, so every index without a
`--call-graph-engine` option uses CodeQL. A profile only chooses the
call-graph engine: the strategy and every other key keep their defaults,
and the file stays editable afterwards.

`analysis.strategy` (`default` or `strategy1`) is the strategy of every
indexing pass that has no `--strategy` option, including incremental passes
and `systemlens web` reindexing. The persisted index profile never overrides
it: a Strategy1 index reindexed with `strategy: default` loses its Strategy1
facts.

This is a breaking rename from `cccr`, `archlens`, and `codeatlas`: SystemLens
does not read an existing `.cccr/`, `.archlens/`, or `.codeatlas/` directory.
Run `systemlens init` and
`systemlens index` to create a new local inventory in `.systemlens/`.

`include` and `exclude` control source inventory. Maven/Gradle test source
sets (`src/test`, `src/componentTest`, and names ending in `Test`) are always
excluded. Maven modules whose artifact identifier or directory name has a
`test` or `tests` name token (`orders-test-support`) or contains `archetype`
are also excluded from the production index; `attestation-service` stays
indexed. A CodeQL database, any directory holding `codeql-database.yml`,
is never inventoried, whatever `exclude` says: it copies the sources and
holds thousands of analyzer files. The
`min_severity` setting remains accepted for database compatibility
but does not alter AST endpoint extraction.

`analysis` is the versionable source of truth for the topic convention,
method-call engine, and disabled extractors. `call_graph_engine` is `source`
(the default), `codeql`, or `none`; with an explicit `codeql: true` and no
engine, it is `codeql`, as in configurations written before the source
default. `codeql_timeout_seconds`, `codeql_max_hops`, and
`codeql_edge_confidence` apply to every engine; their names are kept for
compatibility with existing configurations. Under Strategy1, listener and
sender topic keys match declared keys in `kafka.yml` or `kafka.yaml` after
normalization.
An optional `runtime` section maps APM service names to indexed services for
`systemlens observe` and the HTML export overlay. It applies to every
runtime source (`observe elastic` and `observe prometheus`) alike.
`ignore_services` lists APM services left out of reconciliation, such as
OTel collectors: they stay `not_compared` and are never reported as
unmapped. The section holds names only, never credentials: the Elastic API
key and the Prometheus bearer token come from the environment variables
named by `--api-key-env` and `--bearer-env`. A non-string entry is a
configuration error.

```yaml
runtime:
  service_map:
    prod-orders-api: order-service
  ignore_services: [elastic-otel-collector, otel-edge]
```

`root_path` is local export configuration only:
it resolves relative source evidence into VS Code links and is never persisted
in the architecture snapshot.
