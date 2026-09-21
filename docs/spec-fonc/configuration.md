# Configuration

Parent: [Functional specification](../SPEC-FONC.md).


`systemlens init` creates `.systemlens/config.yml`:

```yaml
include: ["**/*"]
exclude: [".git/**", ".venv/**", "node_modules/**", ".systemlens/**"]
min_severity: INFO
root_path: .
analysis:
  strategy: default
  codeql: true
  call_graph_engine: codeql
  codeql_timeout_seconds: 600
  codeql_threads: 0
  codeql_ram_mb: null
  codeql_verbosity: null
  codeql_max_hops: 12
  disabled_extractors: []
```

This is a breaking rename from `cccr`, `archlens`, and `codeatlas`: SystemLens
does not read an existing `.cccr/`, `.archlens/`, or `.codeatlas/` directory.
Run `systemlens init` and
`systemlens index` to create a new local inventory in `.systemlens/`.

`include` and `exclude` control source inventory. Maven/Gradle test source
sets (`src/test`, `src/componentTest`, and names ending in `Test`) are always
excluded. Maven modules whose artifact identifier or directory name contains
`test` or `archetype` are also excluded from the production index. The
`min_severity` setting remains accepted for database compatibility
but does not alter AST endpoint extraction.
`analysis` is the versionable source of truth for the topic convention,
call-graph engine, and disabled extractors. `root_path` is local export configuration only:
it resolves relative source evidence into VS Code links and is never persisted
in the architecture snapshot.

