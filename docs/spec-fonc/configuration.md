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
  call_graph_engine: source
  engine_timeout_seconds: 600
  engine_threads: 0
  engine_ram_mb: null
  engine_max_hops: 12
  engine_edge_confidence: possible
  disabled_extractors: []
```

The `.codeql/**` exclusion comes from the `codeql` engine's declaration:
each declared engine may add the directories it writes.

`systemlens init --engine NAME`, where `NAME` is a declared engine
(`source`, `codeql` or `joern`), writes the same file with
`call_graph_engine: NAME` and the engine's own options under
`analysis.engines.NAME`. Every index without a `--call-graph-engine` option
then uses that engine. With `--engine codeql`:

```yaml
analysis:
  call_graph_engine: codeql
  # generic engine_* keys as above
  engines:
    codeql:
      database: null
      verbosity: null
```

`systemlens init --strategy strategy1` writes `strategy: strategy1`. The
two options are independent (PDR-13): every other key keeps its default,
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
indexed. An engine artifact that the engine's declaration recognizes, such
as a CodeQL database (any directory holding `codeql-database.yml`), is never
inventoried, whatever `exclude` says: it copies the sources and holds
thousands of analyzer files.

Neither is a `.git` or `node_modules` directory at any depth, nor a
`target`, `build` or `out` build output outside `src`, whatever `include`
says; `target` stays with `--generate-sources` and for Strategy1's
generated OpenAPI sources. The
`min_severity` setting remains accepted for database compatibility
but does not alter AST endpoint extraction.

`analysis` is the versionable source of truth for the topic convention,
method-call engine, and disabled extractors. `call_graph_engine` names a
declared engine (`source` by default, `codeql`, `joern`) or `none`.

| Key | Default | Meaning |
|---|---|---|
| `engine_timeout_seconds` | `600` | Wall-clock budget of the engine pass, source generation included. |
| `engine_threads` | `0` | Threads an external engine may use; `0` is one per core. |
| `engine_ram_mb` | `null` | RAM limit in MiB of an external engine; `null` lets it choose. |
| `engine_max_hops` | `12` | Longest call chain the flow join follows. |
| `engine_edge_confidence` | `possible` | `exact` drops possible and inferred edges from flows. |
| `engines.NAME.OPTION` | per engine | An option the engine `NAME` declares. |

Engine-owned options of `codeql` are `database` (a prebuilt database, a
path relative to the indexed root or absolute) and `verbosity` (`errors`,
`progress`, `progress+`, `progress++` or `progress+++`). `joern` declares no
option: `engine_ram_mb` becomes the JVM's `-Xmx` and a positive
`engine_threads` its `-XX:ActiveProcessorCount`, both through `JAVA_OPTS`.

`analysis` accepts only the keys above, plus `strategy`, `topic_strategy`
and `disabled_extractors`. Any other key, such as the removed `codeql`
switch or a `codeql_*` setting, fails with
`Clé(s) inconnue(s) dans analysis : ...`. An unknown engine under
`engines`, or an option it does not declare, fails the same way.
`engines` must be a mapping: a list, even an empty one, fails with
`analysis.engines doit être un objet YAML.`

Under Strategy1, listener and
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
