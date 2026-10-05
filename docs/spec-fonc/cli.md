# CLI

Parent: [Functional specification](../SPEC-FONC.md).


| Command | Behaviour |
|---|---|
| `systemlens init` | Creates `.systemlens/config.yml`; it never overwrites an existing file. |
| `systemlens doctor [--json]` | Read-only check of configuration, local AST readiness and index state. |
| `systemlens version` | Prints the installed `systemlens` package version. |
| `systemlens index [MANIFEST]... [--module NAME_OR_PATH] [--refresh-codeql-view] [--internal-flows-only] [--show-call-chains] [--full] [--resume-codeql-join] [--strategy default\|strategy1] [--manifest FILE]... [--kubernetes] [--kubernetes-namespace NAME] [--call-graph-engine codeql\|none] [--codeql-edge-confidence exact\|possible] [--codeql-database DIR] [--codeql-progress] [--codeql-progress-html FILE] [--generate-sources] [--no-codeql] [--disable TYPE]...` | Incrementally extracts and persists architecture facts. `--module` focuses the final output on one discovered Maven/Gradle module by name or path and lists all persisted flows whose indexed evidence stays inside that module. `--refresh-codeql-view` requires `--module` and forces a CodeQL method-view refresh for that module, even when the normal incremental index would skip the interprocedural stage. The refreshed module projection replaces only that module's source paths; methods already persisted for other modules remain available for cross-module traversal. `--internal-flows-only` requires `--module` and switches to a fast, read-only diagnostic of the existing snapshot. It does not rerun AST extraction or CodeQL and prints only the module's internal IN-to-OUT flow diagnosis. `--show-call-chains` requires `--module` and prints each new internal IN-to-OUT chain as the CodeQL join constructs it. Run a normal global index first, and rerun it after source changes. The normal index and CodeQL database remain global to the repository, so calls crossing module boundaries can be resolved. `--codeql-database` reuses an already-built global database and is the fastest repeatable debug path for a normal index. The default `codeql` engine creates one temporary source-only Java database for the whole repository, then reports extracted calls project by project; this preserves cross-project references. `--codeql-edge-confidence` controls whether reconstruction accepts only exact edges or also possible dispatch and inferred bridges; the default is `possible`. `--codeql-progress` forwards CodeQL's detailed live progress output. `--generate-sources` runs only the Maven/Gradle source-generation phase in the indexed repository, then indexes production Java files below `target/generated-sources`; it never compiles or runs tests. `none` keeps AST-only flows. `--codeql-progress-html` rewrites an explicitly provisional HTML graph after each reported CodeQL project; it requires the `codeql` engine and is not a final export. `--resume-codeql-join` resumes a compatible partial CodeQL join from its last committed IN-method batch and cannot be combined with `--full`. `--no-codeql` is the legacy AST-only alias. |
| `systemlens import-facts FILE [--namespace NAME] [--complete]` | Validates and transactionally upserts a reviewable fact manifest, including one produced by an agent through the companion skill, into the separate enrichment layer. `--complete` removes stale facts only within the selected namespace. |
| `systemlens export facts FILE [--namespace NAME] [--partial]` | Exports one persisted enrichment namespace as a `systemlens-ai-graph-v1` manifest. The result preserves fact IDs, evidence, status, confidence, metadata and edge endpoints, and can be reviewed and passed to `import-facts`. `--partial` marks the manifest as an incremental pass; the default is a complete namespace snapshot. |
| `systemlens microservices`, `topics`, `apis`, `dtos`, `mongodb`, `projects` | Browse the indexed catalog; `microservices`, `topics` and `mongodb` list the corresponding architecture objects directly, each with a `kind` and `name`, and support the documented list/show/neighbors actions and JSON output where applicable. |
| `systemlens flows [list] [--root DIR] [--json] [--publishes-to-topic] [--module NAME] [--explain]` | Lists persisted potential code flows from an entry point to a source-evidenced external effect, within one method or across method calls resolved by the selected method-call engine. Each summary exposes the module, target modules, input flow and Java type, output flow and Java type, plus the compatibility fields `input_topic` and `output_topics`. `root` is `true` when no persisted flow reaches the flow's input endpoint through an OUT→IN topology arc. Unknown types remain `null`; `--publishes-to-topic` keeps only flows with at least one Kafka publication. `--module NAME` keeps only flows owned by `NAME` whose first endpoint is a port IN and whose last endpoint is a port OUT in the same module. Scheduled flows without an IN port are excluded from this internal-flow view. With `--explain` and `--module`, text output shows the method receiving the IN port, each CodeQL method transition when present, the external invocation carrying the OUT port when it is visible in endpoint source evidence, and the OUT port. JSON output returns `found`, indexed inputs and outputs, each ordered call step, and excluded flows. If no flow is found, `--explain` also returns up to ten `attempted_paths`, each with its partial call chain and stop reason. Each flow reports whether its endpoint evidence is `complete` or `partial` relative to the persisted topology snapshot. |
| `systemlens flows calculate [--root DIR] [--json] [--module NAME]` | Reconstructs persisted source-backed flows from the stored AST, CodeQL and enrichment snapshots. It does not rerun indexation, and it preserves the independent AI fact layer. With `--module`, it reconstructs only flows whose indexed input method belongs to that module, while retaining the global persisted method graph for helper calls. |
| `systemlens flows show ID_OR_QUERY [--root DIR] [--json]` | Shows the ordered steps and source evidence of one unambiguously selected potential code flow. Text output also renders the downstream call-flow tree; JSON includes the same tree under `tree` and the derived `root` attribute. |
| `systemlens flows stats [--root DIR] [--json] [--by-module]` | Reports the number of persisted internal HTTP and Kafka connections. Only cross-module edges with both indexed endpoint sides count; dynamic or targetless integrations are excluded. `--by-module` instead counts source-backed IN-to-OUT flows for each module and includes its indexed IN and OUT port counts. |
| `systemlens microservices topics\|apis\|mongodb\|properties\|openapi NAME [--root DIR] [--json]` | Follow one linked object kind from a single named microservice. |
| `systemlens microservices implementation KIND ID [--root DIR] [--json]` | Jump to the source implementation of one identified integration. |
| `systemlens projects integrations PROJECT [--json]` | Lists the integrations owned by one Maven/Gradle project. |
| `systemlens projects graph [--json]` | Prints the Maven/Gradle build-dependency graph between projects. |
| `systemlens analyze audit [--workspace DIR]` | Reports static architecture risks; `--workspace` analyzes a parent workspace of independently indexed services instead of the current repository. |
| `systemlens analyze coverage [--root DIR] [--json]` | Reports inventory coverage and unresolved integrations. |
| `systemlens analyze indexing-issues [--root DIR] [--json]` | Lists unresolved indexing facts. JSON includes source evidence suitable for reviewing proposed heuristics. |
| `systemlens analyze indexing-audit [--root DIR] [--json]` | Audits twenty distinct indexing-quality controls using only the persisted snapshot. It reports detected gaps such as unresolved endpoints, incomplete flows, ambiguous dispatch and orphaned graph edges, with source evidence when available. |
| `systemlens analyze call-edge CALLER CALLEE [--module NAME] [--root DIR] [--json]` | Diagnoses one expected Java call edge from the persisted snapshot. Each method selector accepts an exact method ID, a qualified name, or a qualified-name suffix. Without `--module`, selectors and interface implementations are resolved across the indexed repository. `--module` adds an explicit module filter when the same selector exists in several projects. The result identifies a method-inventory gap, an absent CodeQL edge, a confidence filter, or the missing IN-to-OUT context. An absent edge is classified as a CodeQL extraction or CodeQL-to-AST join gap because existing snapshots do not retain rejected raw CodeQL rows. |
| `systemlens analyze call-edges [--module NAME] [--root DIR] [--json]` | Lists every persisted source-backed method-call edge, including edges that do not belong to a reconstructed flow. Without `--module`, it lists all callers. With `--module`, it keeps edges whose caller belongs to that module and preserves cross-module callees. JSON includes caller and callee method facts, source call location, dispatch confidence and whether the edge was inferred. |
| `systemlens analyze flows-diagnostic [--root DIR] [--json]` | Reconciles persisted external endpoints, integration methods, local code flows, and cross-service flows; classifies where each external entry disappears without re-parsing source files. |
| `systemlens analyze microservices calls\|dependencies\|external-apis\|orphan-integrations [NAME] [--root DIR] [--json]` | Lists a service's outgoing calls, dependencies, external APIs, or integrations with no resolved caller/callee, depending on the subcommand. `external-apis` and `orphan-integrations` accept an optional `NAME` to scope the result to one service. |
| `systemlens analyze microservices impact NAME [--root DIR] [--json]` | Lists direct neighbors and bounded transitive impact paths for a microservice. REST impact travels from a provider to its callers; Kafka impact travels from a producer to its consumers. For a topic, API, or collection, the command keeps direct neighbors and returns no transitive paths. JSON keeps `neighbors` for direct compatibility and adds `paths` for the transitive result. `paths_truncated` reports whether the result reached the depth or result limit; `paths_max_depth` and `paths_limit` report those bounds. |
| `systemlens analyze microservices path FROM TO [--root DIR] [--json] [--max-depth N] [--limit N]` | Lists bounded paths between services. |
| `systemlens export microservices (--html FILE | --c4 DIRECTORY | --json) [--graph FILE] [--workspace DIRECTORY] [--root-path DIRECTORY]` | Exports the deployable microservice, API, Data, and Topic topology. Non-deployable indexed projects (libraries and aggregators without an application entry point) are excluded from this view and remain available to `export projects` and `export layers`. Persisted MCP graph facts are included in the HTML export. `--graph FILE` reads a validated `systemlens-ai-graph-v1` manifest; `--workspace` federates separately indexed services below one parent directory; `--root-path` provides the local source root for HTML source links. |
| `systemlens export projects --html FILE` | Exports the Maven/Gradle build-dependency view. |
| `systemlens export layers --html FILE` | Exports a dedicated software-layer view. With the persisted Strategy1 profile, the project groups `PORTAIL` and `CYCLE-DE-VIE` are rendered in API/contracts and Orchestration, `DOMAIN-*` projects in Domain, and the documented layer-name prefixes/suffixes in their matching layers; shared libraries and other non-deployable projects are omitted, and without Strategy1 the repository-specific conventions are disabled. |
| `systemlens export modules --html FILE` | Exports the structural hierarchy where a module can contain child modules and indexed projects. Membership comes from project directory paths, never from Kubernetes namespaces. The legacy `export clusters` and `export namespaces` spellings remain hidden compatibility aliases. |
| `systemlens web [--host HOST] [--port PORT]` | Starts the local Python web application at `http://127.0.0.1:8765/` by default. Its home page links to Architecture. Architecture renders the persisted snapshot for each request, excluding test-fixture microservices and every relation attached to them; when no index exists, it offers an explicit local button that creates the default configuration when needed and indexes the repository. The default loopback host prevents network exposure unless the user explicitly changes `--host`. |
| `simpleweb [DIRECTORY] [--host HOST] [--port PORT]` | Serves static files from `DIRECTORY`, or from the current directory when omitted, for opening generated HTML files that load adjacent JSON. It binds to `http://127.0.0.1:8000/` by default, has no write routes, and does not create or modify files. The directory must exist. |
| `systemlens mcp` | Starts the stdio MCP server. |

After a global index, use `systemlens flows list --module NAME` for a
read-only module query. This command reads the persisted flow snapshot and
does not rerun AST extraction or CodeQL. The `index --module NAME` option is
still an indexing command: it limits the diagnostic output, while its source
inventory and CodeQL analysis remain global.

To populate or refresh the persisted CodeQL method view one module at a time,
run `systemlens index --module NAME --refresh-codeql-view`. The option requires
CodeQL and a previously completed global CodeQL call graph. It should reuse the
global database supplied with `--codeql-database`; the command then queries the
selected module's methods and reuses the persisted global calls. Other module
rows remain in the SQLite snapshot. Use `--full` as well when the normal file
delta should also be forced.

With `systemlens flows list --module NAME --explain`, the diagnostic includes
the tested graph metrics and up to ten partial call paths when no internal flow
is found. A path can end at an indexed method with no persisted CodeQL edge;
this explains why the search stopped without hiding the attempted node.

When CodeQL reaches its time limit, the CLI prints
`systemlens index --resume-codeql-join` as the recovery path. With
`--no-codeql`, the CLI explicitly reports that the result is AST-only and that
dynamic or interprocedural calls may remain incomplete. After
`systemlens import-facts`, the JSON result remains on stdout and the CLI prints
`systemlens flows calculate` on stderr as the next step, so scripts can keep
parsing stdout as JSON.

Use `systemlens analyze call-edge CALLER CALLEE [--module MODULE]` when one expected Java call
is missing from a flow or graph view. The command reads the method inventory,
the persisted CodeQL graph, the flow snapshot status, and the configured edge
confidence recorded by the index. It does not run CodeQL. For an interface
selector, it also uses the indexed source symbols to match a persisted edge
whose target is a compatible concrete implementation. Text and JSON output report AST and CodeQL presence for both selected
nodes and their directed edge. Presence is `present`, `absent`, or `unknown`.
Without `--module`, the command keeps the repository-wide candidate set and
reports ambiguity when a selector matches several projects. Use `--module` only
to restrict both selectors and interface implementation matching to one module.
An absent AST method is provable; missing raw CodeQL or AST-call evidence stays
`unknown`. JSON output also includes the proof basis for each presence value,
the matched method facts, persisted edge evidence, flow IDs, and the pipeline
stage associated with the verdict. An AST node is proved by its persisted
integration-method fact; a CodeQL node or edge is proved only by a persisted
non-inferred CodeQL call edge.

Pass `--codeql-database DIR` to query node presence directly from an existing
CodeQL database during the diagnostic. Query outputs are temporary and are not
copied into the SystemLens index. This option requires the database to match
the indexed source paths and revision; without it, node presence falls back to
the persisted snapshot.

When `index --show-call-chains` is given the same `--codeql-database`, the module
diagnostic re-queries CodeQL for the complete source call graph when needed.
This keeps ordinary intermediate methods visible even when no source file was
rescanned and the persisted edge snapshot contains only endpoint anchors.

`systemlens index` reports the main stages in order: module discovery, file
inventory, incremental delta, AST extraction, endpoint persistence, module and
property persistence, architecture relations, CodeQL/interprocedural flows,
and final statistics. Each completed stage reports its elapsed duration. AST
extraction receives all changed files in one pass and reports the `AST 1/1`
checkpoint. CodeQL creates one global database, then processes each
source-owning Maven/Gradle module as `module <current>/<total>` with its
extracted-call count and elapsed duration. Interactive terminals colour phase
headers in cyan, module progress in yellow and completed work in green; the
plain text content is unchanged when output is redirected. The indexing output
ends with compact statistics for each module: the number of `IN` ports, `OUT`
ports, and internal code flows. A global line then reports the same totals
across the indexed repository. Individual port paths and Java implementations
remain available through the persisted endpoint and module commands rather than
being printed in the indexing summary. The total duration follows, then a
next-step hint towards the interactive microservice HTML export. Its result
line is:

```text
scanned=<N> skipped=<N> +integrations=<N> -integrations=<N>
```

With `--internal-flows-only`, no indexing stage is run. The command reads the
persisted snapshot and prints only the selected module's internal-flow
diagnosis. The snapshot can include shared Java methods and cross-module call
edges from the preceding global index.

### Reuse an externally created CodeQL database

Create the CodeQL database from the repository root, then pass that global
database to SystemLens:

```bash
codeql database create .codeql/systemlens-java \
  --language=java \
  --source-root=. \
  --build-mode=none

systemlens index --full \
  --call-graph-engine codeql \
  --codeql-database .codeql/systemlens-java
```

SystemLens reuses the database for method-call extraction and still performs
its own AST extraction, inventory refresh, relation materialization and
persistence. The database must be global for the indexed repository, use the
same source-root layout, and represent the source revision being indexed.
The caller owns its lifecycle and must recreate it when relevant source files
change.

`--codeql-database` requires the `codeql` call-graph engine and cannot be
combined with `--generate-sources`. Source generation, when needed, must finish
before the external database is created. SystemLens does not replace the
supplied database with its automatic temporary source-only database.

The first AST-only run removes stale results from the retired analyzer.

When `--generate-sources` is enabled, SystemLens runs only the Maven
`generate-sources` or Gradle `generateSources` phase in the indexed repository,
with batch, non-interactive and offline options for Maven. A root build
descriptor generates all of its nested modules; without one, each outermost
nested Maven or Gradle project is generated independently. The generated Java
files below a Maven `target/` directory are included in the same AST and method
index, while non-Java build outputs remain excluded. The command never compiles
the project or runs tests. Maven must find
its plugins and dependencies in the local cache.

Automatic method-call analysis creates one source-only Java database for the
whole repository with `codeql`. Progress reports the completed project over the
total and the calls processed from the global result. By default, SystemLens
persists the flow snapshot once after all modules have been processed; this
avoids rewriting the complete SQLite flow table for every module. When
explicit progress output is enabled, SystemLens can persist a partial
`code_flows` snapshot after each completed project and emit a checkpoint line
with the number of provisional flows.
The checkpoint line names the source-owning Maven module and lists the Java
methods searched for IN and OUT integration points. The optional HTML
checkpoint carries the same summary in its provisional progress banner.
During the final join, progress reports the number of calls attached to Java
methods, the IN methods explored, the transitions traversed, and the flows
materialized before final reconciliation.
With `--show-call-chains --module NAME`, each newly explored internal chain
prefix is also printed during the join, starting at an indexed OUT method and
walking through callers toward an indexed IN method. Constructed OUT-to-IN
chains and terminal paths without an input are shown. Terminal paths include
their stop reason, such as no indexed caller, a cycle, or the hop limit.
Direct IN-to-OUT calls show the indexed entry method before the output in the
final flow report. Every live line includes the current call depth, and every
terminal line includes the depth and the exact stop reason. If the selected
snapshot is unchanged and no indexing stage runs, the command replays this
diagnostic from the persisted CodeQL graph without rewriting the index.
Interactive terminals render constructed chains in green; redirected output
remains plain text.
The index commits a provisional flow snapshot after each join batch, so an
HTML progress export or a later export from the index retains the call-graph
arcs found before an interruption.
The final reconciliation replaces this partial snapshot and marks it complete.
SystemLens maps module-relative evidence paths back to the repository root,
aggregates all calls, and only then joins them to the global method inventory.
An explicit `--codeql-database` remains a caller-managed global-database
override. Its calls and methods are queried once globally, then processed by
source-owning project for the same module progress reporting.

`--strategy strategy1` is opt-in. The selected strategy is persisted with
the index and reused by incremental MCP reindexing and all derived views.
`analysis.call_graph_engine` accepts `codeql` (default) or `none`.
`analysis.codeql_edge_confidence` accepts `exact` or `possible`. The default
`possible` keeps possible dispatch and inferred bridges as low-confidence flow
evidence; `exact` excludes them from flow reconstruction.
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
