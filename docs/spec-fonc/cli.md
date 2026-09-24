# CLI

Parent: [Functional specification](../SPEC-FONC.md).


| Command | Behaviour |
|---|---|
| `systemlens init` | Creates `.systemlens/config.yml`; it never overwrites an existing file. |
| `systemlens doctor [--json]` | Read-only check of configuration, local AST readiness and index state. |
| `systemlens version` | Prints the installed `systemlens` package version. |
| `systemlens index [MANIFEST]... [--full] [--resume-codeql-join] [--strategy default\|strategy1] [--manifest FILE]... [--kubernetes] [--kubernetes-namespace NAME] [--call-graph-engine codeql\|none] [--codeql-database DIR] [--codeql-progress] [--codeql-progress-html FILE] [--generate-sources] [--no-codeql] [--disable TYPE]...` | Incrementally extracts and persists architecture facts. The default `codeql` engine creates one temporary source-only Java database for the whole repository, then reports extracted calls project by project; this preserves cross-project references. `--codeql-progress` forwards CodeQL's detailed live progress output. `--generate-sources` runs only the Maven/Gradle source-generation phase in a temporary copy; it never compiles or runs tests. `none` keeps AST-only flows. `--codeql-database` reuses an already-built global CodeQL database and requires the `codeql` engine. `--codeql-progress-html` rewrites an explicitly provisional HTML graph after each reported CodeQL project; it requires the `codeql` engine and is not a final export. `--resume-codeql-join` resumes a compatible partial CodeQL join from its last committed IN-method batch and cannot be combined with `--full`. `--no-codeql` is the legacy AST-only alias. |
| `systemlens import-facts FILE [--namespace NAME] [--complete]` | Validates and transactionally upserts a reviewable fact manifest, including one produced by an agent through the companion skill, into the separate enrichment layer. `--complete` removes stale facts only within the selected namespace. |
| `systemlens microservices`, `topics`, `apis`, `dtos`, `mongodb`, `projects` | Browse the indexed catalog; `microservices`, `topics` and `mongodb` list the corresponding architecture objects directly, each with a `kind` and `name`, and support the documented list/show/neighbors actions and JSON output where applicable. |
| `systemlens flows [list] [--root DIR] [--json]` | Lists persisted potential code flows from an entry point to a source-evidenced external effect, within one method or across method calls resolved by the selected method-call engine. Each flow reports whether its endpoint evidence is `complete` or `partial` relative to the persisted topology snapshot. |
| `systemlens flows show ID_OR_QUERY [--root DIR] [--json]` | Shows the ordered steps and source evidence of one unambiguously selected potential code flow. |
| `systemlens microservices topics\|apis\|mongodb\|properties\|openapi NAME [--root DIR] [--json]` | Follow one linked object kind from a single named microservice. |
| `systemlens microservices implementation KIND ID [--root DIR] [--json]` | Jump to the source implementation of one identified integration. |
| `systemlens projects integrations PROJECT [--json]` | Lists the integrations owned by one Maven/Gradle project. |
| `systemlens projects graph [--json]` | Prints the Maven/Gradle build-dependency graph between projects. |
| `systemlens analyze audit [--workspace DIR]` | Reports static architecture risks; `--workspace` analyzes a parent workspace of independently indexed services instead of the current repository. |
| `systemlens analyze coverage [--root DIR] [--json]` | Reports inventory coverage and unresolved integrations. |
| `systemlens analyze indexing-issues [--root DIR] [--json]` | Lists unresolved indexing facts. JSON includes source evidence suitable for reviewing proposed heuristics. |
| `systemlens analyze flows-diagnostic [--root DIR] [--json]` | Reconciles persisted external endpoints, integration methods, local code flows, and cross-service flows; classifies where each external entry disappears without re-parsing source files. |
| `systemlens analyze microservices calls\|dependencies\|external-apis\|orphan-integrations [NAME] [--root DIR] [--json]` | Lists a service's outgoing calls, dependencies, external APIs, or integrations with no resolved caller/callee, depending on the subcommand. `external-apis` and `orphan-integrations` accept an optional `NAME` to scope the result to one service. |
| `systemlens analyze microservices impact NAME [--root DIR] [--json]` | Lists direct and transitive impact paths. |
| `systemlens analyze microservices path FROM TO [--root DIR] [--json] [--max-depth N] [--limit N]` | Lists bounded paths between services. |
| `systemlens export microservices (--html FILE | --c4 DIRECTORY | --json) [--graph FILE] [--workspace DIRECTORY] [--root-path DIRECTORY]` | Exports the deployable microservice, API, Data, and Topic topology. Non-deployable indexed projects (libraries and aggregators without an application entry point) are excluded from this view and remain available to `export projects` and `export layers`. Persisted MCP graph facts are included in the HTML export. `--graph FILE` reads a validated `systemlens-ai-graph-v1` manifest; `--workspace` federates separately indexed services below one parent directory; `--root-path` provides the local source root for HTML source links. |
| `systemlens export projects --html FILE` | Exports the Maven/Gradle build-dependency view. |
| `systemlens export layers --html FILE` | Exports a dedicated software-layer view. With the persisted Strategy1 profile, the project groups `PORTAIL` and `CYCLE-DE-VIE` are rendered in API/contracts and Orchestration, `DOMAIN-*` projects in Domain, and the documented layer-name prefixes/suffixes in their matching layers; shared libraries and other non-deployable projects are omitted, and without Strategy1 the repository-specific conventions are disabled. |
| `systemlens export modules --html FILE` | Exports the structural hierarchy where a module can contain child modules and indexed projects. Membership comes from project directory paths, never from Kubernetes namespaces. The legacy `export clusters` and `export namespaces` spellings remain hidden compatibility aliases. |
| `systemlens web [--host HOST] [--port PORT]` | Starts the local Python web application at `http://127.0.0.1:8765/` by default. Its home page links to Architecture. Architecture renders the persisted snapshot for each request, excluding test-fixture microservices and every relation attached to them; when no index exists, it offers an explicit local button that creates the default configuration when needed and indexes the repository. The default loopback host prevents network exposure unless the user explicitly changes `--host`. |
| `simpleweb [DIRECTORY] [--host HOST] [--port PORT]` | Serves static files from `DIRECTORY`, or from the current directory when omitted, for opening generated HTML files that load adjacent JSON. It binds to `http://127.0.0.1:8000/` by default, has no write routes, and does not create or modify files. The directory must exist. |
| `systemlens mcp` | Starts the stdio MCP server. |

`systemlens index` reports its file delta, AST analysis stage, persisted endpoint
count and materialized relations. AST extraction receives all changed files in
one pass and reports the `AST 1/1` checkpoint. CodeQL creates one global
database, then reports each source-owning Maven/Gradle module as `module
<current>/<total>` with its extracted-call count and elapsed duration. The
indexing output ends with compact statistics for each module: the number of
`IN` ports, `OUT` ports, and internal code flows. A global line then reports
the same totals across the indexed repository. Individual port paths and Java
implementations remain available through the persisted endpoint and module
commands rather than being printed in the indexing summary.
remaining indexing stages also report their progress and elapsed wall-clock
duration with two decimal places. The total duration follows, then a next-step
hint towards the interactive microservice HTML export. Its result line is:

```text
scanned=<N> skipped=<N> +integrations=<N> -integrations=<N>
```

The first AST-only run removes stale results from the retired analyzer.

When `--generate-sources` is enabled for a Maven project, SystemLens runs only
`mvn generate-sources` in a temporary copy, with batch, non-interactive and
offline options. Maven must find its plugins and dependencies in the local
cache. The command never compiles the project or runs tests.

Automatic method-call analysis creates one source-only Java database for the
whole repository with `codeql`. Progress
reports the completed project over the total and the calls extracted from it.
After each completed project, SystemLens persists a partial `code_flows`
snapshot and emits a checkpoint line with the number of provisional flows.
The checkpoint line names the source-owning Maven module and lists the Java
methods searched for IN and OUT integration points. The optional HTML
checkpoint carries the same summary in its provisional progress banner.
During the final join, progress reports the number of calls attached to Java
methods, the IN methods explored, the transitions traversed, and the flows
materialized before final reconciliation.
The index commits a provisional flow snapshot after each join batch, so an
HTML progress export or a later export from the index retains the call-graph
arcs found before an interruption.
The final reconciliation replaces this partial snapshot and marks it complete.
SystemLens maps module-relative evidence paths back to the repository root,
aggregates all calls, and only then joins them to the global method inventory.
An explicit `--codeql-database` remains a caller-managed global-database
override, but its call query is scoped to each source-owning project so it
produces the same progressive checkpoints.

`--strategy strategy1` is opt-in. The selected strategy is persisted with
the index and reused by incremental MCP reindexing and all derived views.
`analysis.call_graph_engine` accepts `codeql` (default) or `none`.
The automatic CodeQL projection preserves Java sources below
`target/generated-sources` so generated AsyncAPI/OpenAPI types remain
available; other build outputs and build descriptors are excluded.
`--call-graph-engine` selects the method-call engine for one run and does not modify
`.systemlens/config.yml`. `--no-codeql` remains a legacy AST-only alias.
`--codeql-progress-html FILE` is an opt-in progress aid: after each completed
CodeQL project it atomically replaces `FILE` with a graph labelled as
provisional, including the completed-project count. Users may refresh that
file in a browser to inspect the current method-call coverage. It is generated
by filtering the once-materialized global flows to reported call sites; direct
answers without intermediate evidence appear at the final checkpoint.
These in-progress indexing facts must not be treated as an exportable or
complete architecture snapshot; the normal `export microservices --html`
command remains the authoritative post-index export.
CodeQL diagnostics are silent by default, including for repositories whose
older configuration still contains `analysis.codeql_verbosity`. The explicit
`--codeql-progress` option temporarily enables `progress++` for one command.
CodeQL's messages are diagnostic progress only; they do not provide a
guaranteed percentage or remaining-time estimate. During the Python-side join,
SystemLens reports the call-attachment phase and the input-method exploration
at least every five seconds while work continues. These messages include
processed calls or methods, explored transitions, and elapsed time.
`analysis.codeql_timeout_seconds` sets the positive wall-clock budget for the
complete CodeQL pass, including source generation, temporary database creation,
query execution and BQRS decoding; its default is `600` seconds. The remaining
budget is propagated to every subprocess. The deadline includes live progress
reading, even when a subprocess stops producing output. On POSIX, timeout or
interrupted progress handling terminates and reaps the complete process group.
A timeout is a soft boundary for the index: SystemLens keeps AST facts and any
CodeQL calls recovered from a completed partial result, executes the remaining
relation, flow, reconciliation and statistics post-processing, then commits
the resulting partial snapshot so it can be exported to HTML. The
CLI reports that the graph is partial. A timed-out CodeQL pass does not update
the code-flow signature, so the next index retries the interprocedural analysis
even when source files are unchanged. Other CodeQL failures remain fatal and
preserve the previous committed snapshot.
When the join itself is interrupted after one or more committed batches,
`systemlens index --resume-codeql-join` reuses the compatible repository
checkpoint and skips the completed IN methods. The option rejects changed
analysis inputs, incompatible settings, or a non-partial snapshot; generated
presentation files such as HTML exports do not invalidate it. If the persisted
method list no longer
matches the current indexed method list, the command discards the stale join
cursor and restarts the CodeQL join from the beginning.
`analysis.codeql_threads` sets the number of threads passed to CodeQL database
creation and query execution; its default is `0`, which delegates one thread
per available core to CodeQL. `analysis.codeql_ram_mb` optionally sets
the positive RAM limit in MiB for those operations; it defaults to `null`, so
CodeQL chooses its own limit.
`--disable` accepts `properties`,
`module-architecture`, and `module-tree-sitter`.
