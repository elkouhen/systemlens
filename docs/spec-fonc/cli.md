# CLI

Parent: [Functional specification](../SPEC-FONC.md).

## Commands

| Command | Behaviour |
|---|---|
| `systemlens init [--profile NAME]` | Creates `.systemlens/config.yml`; it never overwrites an existing file. `--profile default` (the default) selects the `source` call-graph engine and needs no external tool. Every engine that declares a profile (`codeql`, `joern`) can be named instead: the profile selects that engine, writes its `analysis.engines.NAME` defaults, and warns when the engine's prerequisite is missing. An unknown profile exits with code 2 and writes nothing. |
| `systemlens doctor [--json]` | Read-only check of configuration, local AST readiness and index state. |
| `systemlens version` | Prints the installed `systemlens` package version. |
| `systemlens index` | Incrementally indexes architecture facts. See [indexing options and output](#indexing-options-and-output). |
| `systemlens import-facts FILE [--namespace NAME] [--complete]` | Validates and transactionally upserts a reviewable fact manifest, including one produced by an agent through the companion skill, into the separate enrichment layer. A `flow_data_access` node can annotate an existing flow by ID; an unknown ID rejects the import. `--complete` removes stale facts only within the selected namespace. |
| `systemlens export facts FILE [--namespace NAME] [--partial]` | Exports one persisted enrichment namespace as a `systemlens-ai-graph-v1` manifest. The result preserves fact IDs, evidence, status, confidence, metadata and edge endpoints, and can be reviewed and passed to `import-facts`. `--partial` marks the manifest as an incremental pass; the default is a complete namespace snapshot. |
| `systemlens microservices`, `topics`, `apis`, `dtos`, `mongodb`, `projects` | Browse the indexed catalog; `microservices`, `topics` and `mongodb` list the corresponding architecture objects directly, each with a `kind` and `name`, and support the documented list/show/neighbors actions and JSON output where applicable. `systemlens topics dynamic` lists every unresolved Kafka endpoint occurrence with its role, module, framework and source location. |
| `systemlens flows list` | Lists persisted potential code flows. See [module queries](#module-queries) for filters and evidence fields. |
| `systemlens flows calculate [--root DIR] [--json] [--module NAME]` | Reconstructs persisted source-backed flows from the stored AST, call-graph and enrichment snapshots. It does not rerun indexation, and it preserves the independent AI fact layer. With `--module`, it reconstructs only flows whose indexed input method belongs to that module, while retaining the global persisted method graph for helper calls. |
| `systemlens flows show ID_OR_QUERY [--root DIR] [--json]` | Shows the ordered steps and source evidence of one unambiguously selected potential code flow. A data step adds its raw operation and action, resource identity (`mongo:`, `jpa:`, or `jdbc:`), key, guard, and transaction, each stated as unknown when not established. Text output also renders the downstream call-flow tree; JSON includes the same tree under `tree`, the derived `root` attribute, and every persisted step field including `operation_id`. |
| `systemlens flows stats [--root DIR] [--json] [--by-module]` | Reports the number of persisted internal HTTP and Kafka connections. Only cross-module edges with both indexed endpoint sides count; dynamic or targetless integrations are excluded. `--by-module` instead counts source-backed IN-to-OUT flows for each module and includes its indexed IN and OUT port counts. |
| `systemlens microservices topics\|apis\|mongodb\|properties\|openapi NAME [--root DIR] [--json]` | Follow one linked object kind from a single named microservice. |
| `systemlens microservices implementation KIND ID [--root DIR] [--json]` | Jump to the source implementation of one identified integration. |
| `systemlens projects integrations PROJECT [--json]` | Lists the integrations owned by one Maven/Gradle project. |
| `systemlens projects graph [--json]` | Prints the Maven/Gradle build-dependency graph between projects. |
| `systemlens analyze audit [--workspace DIR]` | Reports static architecture risks; `--workspace` analyzes a parent workspace of independently indexed services instead of the current repository. |
| `systemlens analyze coverage [--root DIR] [--json]` | Reports inventory coverage and unresolved integrations. `persistence` lists MongoDB, JPA, and JDBC with their supported, partial, and unsupported operation counts, every unresolved operation with its reason, and the note that operations are source-level attempts. |
| `systemlens analyze indexing-issues [--root DIR] [--json]` | Lists unresolved indexing facts. JSON includes source evidence suitable for reviewing proposed heuristics. |
| `systemlens analyze indexing-audit [--root DIR] [--json]` | Audits twenty distinct indexing-quality controls using only the persisted snapshot. It reports detected gaps such as unresolved endpoints, incomplete flows, ambiguous dispatch and orphaned graph edges, with source evidence when available. An edge is orphaned only when its caller or callee is neither an integration method nor a persisted call-graph method. |
| `systemlens analyze call-edge CALLER CALLEE [--module NAME] [--root DIR] [--json]` | Diagnoses why one expected Java call is absent from a flow or graph. See [call-edge diagnosis](#call-edge-diagnosis). |
| `systemlens analyze call-edges [--module NAME] [--root DIR] [--json]` | Lists every persisted source-backed method-call edge, including edges that do not belong to a reconstructed flow. Without `--module`, it lists all callers. With `--module`, it keeps edges whose caller belongs to that module and preserves cross-module callees. JSON includes caller and callee method facts, source call location, dispatch confidence and whether the edge was inferred. |
| `systemlens analyze methods [--module NAME] [--root DIR] [--json]` | Lists every persisted Java method fact, including methods that have no persisted call edge or reconstructed flow. `--module` filters by the method's owning module. JSON includes method IDs, qualified names, source paths, line ranges, endpoint anchors, and source-backed method inheritance edges to concrete implementations. |
| `systemlens analyze flows-diagnostic [--root DIR] [--json]` | Reconciles persisted external endpoints, integration methods, local code flows, and cross-service flows; classifies where each external entry disappears without re-parsing source files. |
| `systemlens analyze microservices calls\|dependencies\|external-apis\|orphan-integrations [NAME] [--root DIR] [--json]` | Lists a service's outgoing calls, dependencies, external APIs, or integrations with no resolved caller/callee, depending on the subcommand. `external-apis` and `orphan-integrations` accept an optional `NAME` to scope the result to one service. |
| `systemlens analyze microservices impact NAME [--root DIR] [--json]` | Lists direct neighbors and bounded transitive impact paths for a microservice. REST impact travels from a provider to its callers; Kafka impact travels from a producer to its consumers. For a topic, API, or collection, the command keeps direct neighbors and returns no transitive paths. JSON keeps `neighbors` for direct compatibility and adds `paths` for the transitive result. `paths_truncated` reports whether the result reached the depth or result limit; `paths_max_depth` and `paths_limit` report those bounds. |
| `systemlens analyze microservices path FROM TO [--root DIR] [--json] [--max-depth N] [--limit N]` | Lists bounded paths between services. |
| `systemlens observe elastic --url URL [--since 7d] [--interval auto] [--api-key-env NAME] [--ca-cert FILE] [--service-map APM=SERVICE] [--json]` | Opt-in network command. Collects aggregated Elastic APM metrics for the window, replaces the persisted observations, and prints the reconciliation. See [runtime observations](#runtime-observations). |
| `systemlens observe report [--service-map APM=SERVICE] [--json]` | Reconciles the persisted observations with the persisted index, without network access. |
| `systemlens export microservices (--html FILE | --json) [OPTIONS]` | Exports persisted topology. See [HTML export](html-export.md) for graph modes, including the gated `--review-aids`, and [AI graph manifests](ai-graph-manifest.md) for `--graph`. |
| `systemlens export projects --html FILE` | Exports the Maven/Gradle build-dependency view. |
| `systemlens export layers --html FILE` | Exports a dedicated software-layer view. With the persisted Strategy1 profile, the project groups `PORTAIL` and `CYCLE-DE-VIE` are rendered in API/contracts and Orchestration, `DOMAIN-*` projects in Domain, and the documented layer-name prefixes/suffixes in their matching layers; shared libraries and other non-deployable projects are omitted, and without Strategy1 the repository-specific conventions are disabled. |
| `systemlens export modules --html FILE` | Exports the structural hierarchy where a module can contain child modules and indexed projects. Membership comes from project directory paths, never from Kubernetes namespaces. The legacy `export clusters` and `export namespaces` spellings remain hidden compatibility aliases. |
| `systemlens web [--host HOST] [--port PORT]` | Starts the local Python web application at `http://127.0.0.1:8765/` by default. Its home page links to Architecture. Architecture renders the persisted snapshot for each request, excluding test-fixture microservices and every relation attached to them; when no index exists, it offers an explicit local button that creates the default configuration when needed and indexes the repository. The default loopback host prevents network exposure unless the user explicitly changes `--host`. |
| `simpleweb [DIRECTORY] [--host HOST] [--port PORT]` | Serves static files from `DIRECTORY`, or from the current directory when omitted, for opening generated HTML files that load adjacent JSON. It binds to `http://127.0.0.1:8000/` by default, has no write routes, and does not create or modify files. The directory must exist. |

## Module queries

After a global index, use `systemlens flows list --module NAME` for a
read-only module query. This command reads the persisted flow snapshot and
does not rerun AST extraction or the call-graph engine. The
`index --module NAME` option is still an indexing command: it limits the
diagnostic output, while its source inventory and engine analysis remain
global.

Each listed flow identifies its owning and target modules, input and output
flows, and known Java types. The JSON result keeps `input_topic` and
`output_topics` for compatibility. Unknown types are `null`, and the `root`
field is true when no persisted flow reaches the input through an OUT-to-IN
topology edge. Endpoint evidence is marked `complete` or `partial` relative to
the persisted topology snapshot.

`--publishes-to-topic` keeps flows with a Kafka publication. `--module NAME`
keeps flows owned by that module whose first and last endpoints are its IN and
OUT ports; scheduled flows without an IN port are excluded. With `--explain`,
text output names the receiving method, indexed call-graph transitions, the
external invocation when its source evidence is available, and the OUT port.
JSON output also includes `found`, indexed ports, ordered call steps, and
excluded flows.

Like the rest of the CLI, the `flows` help and text output are in French.
`flows show` prints `Flux de code potentiel ID`, then `Méthode`, `Confiance`,
`Réconciliation topologique`, the ordered steps, and `Arbre d'appel`. JSON
keys and the `key=value` evidence tokens of a step keep their spelling.

To populate or refresh the persisted engine method view one module at a time,
run `systemlens index --module NAME --refresh-call-graph-view`. The option
requires an engine that declares module refresh (today `codeql`) and a
previously completed global call graph. It should reuse the global database
supplied with `--engine-database`; the command then queries the selected
module's methods and reuses the persisted global calls. Other module rows
remain in the SQLite snapshot. Use `--full` as well when the normal file delta
should also be forced.

With `systemlens flows list --module NAME --explain`, the diagnostic includes
the tested graph metrics and up to ten partial call paths when no internal flow
is found. A path can end at an indexed method with no persisted call edge;
this explains why the search stopped without hiding the attempted node.

## Call-edge diagnosis

Use `systemlens analyze call-edge CALLER CALLEE [--module MODULE]` when one
expected Java call is missing from a flow or graph view. The command reads the
method inventory, persisted call graph, flow snapshot status, and configured
edge confidence. It does not run the engine.

For an interface selector, indexed source symbols can match a persisted edge
whose target is a compatible concrete implementation. Text and JSON output
report AST and engine presence (`ast`, `engine`) for both methods and their
directed edge as `present`, `absent`, or `unknown`.

Without `--module`, the command keeps the repository-wide candidate set and
reports ambiguity when a selector matches several projects. Use `--module` only
to restrict both selectors and interface implementation matching to one module.

An absent AST method is provable; missing raw engine or AST-call evidence
stays `unknown`. JSON output also includes the proof basis for each presence
value (`proof_ast`, `proof_engine`), the matched method facts, persisted edge
evidence, flow IDs, and the pipeline stage associated with the verdict. A
persisted integration-method fact proves an AST node; only a persisted
non-inferred call edge proves an engine node or edge.

The stage of a missing edge is `engine_extraction_or_join`; the status is
`call_graph_disabled`, `engine_unavailable` or `edge_not_persisted`.

Pass `--engine-database DIR` to query node presence directly from an existing
analyzer database during the diagnostic. The database belongs to the
configured engine, or to the one named by `--call-graph-engine`; that engine
must accept a database. Query outputs are temporary and are not copied into
the SystemLens index. The database must match the indexed source paths and
revision; without it, node presence falls back to the persisted snapshot.

When `index --show-call-chains` is given the same `--engine-database`, the
module diagnostic re-queries the engine for the complete source call graph
when needed.
This keeps ordinary intermediate methods visible even when no source file was
rescanned and the persisted edge snapshot contains only endpoint anchors.

## Indexing options and output

`systemlens index` accepts these option groups in addition to positional
manifests:

- Input and scope: `--manifest FILE`, `--module NAME_OR_PATH`, `--full`,
  `--strategy default|strategy1`, and `--disable TYPE`.
- Module diagnostics: `--refresh-call-graph-view`, `--internal-flows-only`,
  and `--show-call-chains`.
- Runtime inventory: `--kubernetes` and `--kubernetes-namespace NAME`.
- Method calls: `--call-graph-engine NAME`,
  `--engine-edge-confidence exact|possible`, `--engine-database DIR`,
  `--engine-option NAME=VALUE`, and `--generate-sources`.
- Progress and recovery: `--engine-progress`,
  `--call-graph-progress-html FILE`, and `--resume-call-graph-join`.

No option names an engine. [Call-graph engines](#call-graph-engines) lists
the engines and what each option requires of them.

Only one `systemlens index` writes to an index at a time. A second one waits
up to five seconds for the lock, then prints
`Indexation impossible : Index verrouillé (...)` with the reason and exits
with code 2, without a traceback. Read-only commands keep working meanwhile.

`systemlens index` reports the main stages in order: module discovery, file
inventory, incremental delta, AST extraction, endpoint persistence, module and
property persistence, architecture relations, interprocedural flows,
and final statistics. Each completed stage reports its elapsed duration.

AST extraction receives all changed files in one pass and reports the
`AST 1/1` checkpoint. An external engine builds one global database, then processes each
source-owning Maven/Gradle module as `module <current>/<total>` with its
extracted-call count and elapsed duration. Interactive terminals colour phase
headers in cyan, module progress in yellow and completed work in green; the
plain text content is unchanged when output is redirected.

The indexing output ends with per-module counts for `IN` ports, `OUT` ports,
call flows, and potential data-access annotations, followed by global totals.
Port paths and Java implementations remain available through endpoint and
module commands. The flow materialization line separates call flows from
data-access annotations.

`flows calculate` uses the same split in text output. Its JSON result reports
`flows` and `data_access_annotations` separately; `flows list` returns call
flows only. The total duration and a hint for the microservice HTML export
follow this result line:

```text
scanned=<N> skipped=<N> +integrations=<N> -integrations=<N>
```

With `--internal-flows-only`, no indexing stage is run. The command reads the
persisted snapshot and prints only the selected module's internal-flow
diagnosis. The snapshot can include shared Java methods and cross-module call
edges from the preceding global index.

With `--call-graph-engine source`, interprocedural calls come
from the tree-sitter source engine: declared receiver types, imports,
inheritance, and unique interface implementations. Dynamic calls, lambdas,
and generic dispatch may remain incomplete. After
`systemlens import-facts`, the JSON result remains on stdout; the CLI prints
`systemlens flows calculate` on stderr as the next step for interactive users.

## Reuse an externally created analyzer database

`--engine-database DIR` hands a prebuilt database to the selected engine,
which must declare a database option. With `codeql`, create the database from
the repository root, then pass that global database to SystemLens:

```bash
codeql database create .codeql/systemlens-java \
  --language=java \
  --source-root=. \
  --build-mode=none

systemlens index --full \
  --call-graph-engine codeql \
  --engine-database .codeql/systemlens-java
```

SystemLens reuses the database for method-call extraction and still performs
its own AST extraction, inventory refresh, relation materialization and
persistence. The database must be global for the indexed repository, use the
same source-root layout, and represent the source revision being indexed.
The caller owns its lifecycle and must recreate it when relevant source files
change.

SystemLens checks the database against the indexed sources before using it
(ADR-83). The index fails, and keeps the previous snapshot, when:

- an indexed Java file differs from its copy in the database's source
  archive: `The CodeQL database is stale: N indexed Java file(s) changed
  since it was created (...)`;
- the archive holds none of the indexed Java files, or no call row comes
  from one: `... the database was built from another source root`.

A file CodeQL did not archive, such as `package-info.java`, is accepted.

`--engine-database` fails with code 2 when the selected engine accepts no
database, and cannot be combined with `--generate-sources`. Source generation, when needed, must finish
before the external database is created. SystemLens does not replace the
supplied database with its automatic temporary source-only database.

The first AST-only run removes stale results from the retired analyzer.

## Source generation

When `--generate-sources` is enabled, SystemLens runs only the Maven
`generate-sources` or Gradle `generateSources` phase in the indexed repository,
with batch, non-interactive and offline options for Maven. A root build
descriptor generates all of its nested modules; without one, each outermost
nested Maven or Gradle project is generated independently.

Generated Java files below a Maven `target/` directory enter the AST and method
index; non-Java build outputs remain excluded. SystemLens does not invoke
compile or test tasks. Maven must find its plugins and dependencies in the
local cache.

## Engine progress and recovery

When the engine reaches its time limit, the CLI prints `systemlens index`
as the recovery path: the next index runs the engine again.

Automatic method-call analysis by an external engine creates one source-only
database for the whole repository. The engine caches it under
`.systemlens/<engine>/` and reuses it while the projected Java sources and the
analyzer are unchanged; any change creates it again (ADR-84, ADR-85).
Progress reports the completed project over the
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
diagnostic from the persisted call graph without rewriting the index.
Interactive terminals render constructed chains in green; redirected output
remains plain text.

The index commits a provisional flow snapshot after each join batch, so an
HTML progress export or a later export from the index retains the call-graph
edges found before an interruption.
The final reconciliation replaces this partial snapshot and marks it complete.
SystemLens maps module-relative evidence paths back to the repository root,
aggregates all calls, and only then joins them to the global method inventory.
An explicit `--engine-database` remains a caller-managed global-database
override. Its calls and methods are queried once globally, then processed by
source-owning project for the same module progress reporting.

`--strategy strategy1` is opt-in and applies to that run only. Without it,
every indexing pass, incremental or full, applies `analysis.strategy` from
`.systemlens/config.yml`; set `strategy: strategy1` there to keep Strategy1.
The strategy of the last pass is persisted with the index, so derived views
read the snapshot's profile, and a change of strategy forces a full refresh.
## Call-graph engines

`analysis.call_graph_engine` and `--call-graph-engine` accept every declared
engine and `none`. The selector, the options and the help text come from the
engines' declarations (ADR-85); the CLI lists no engine by hand.

- `source` (default) resolves calls from tree-sitter syntax and declared
  types. It needs no external tool. It types fields, parameters, locals,
  `var` initializers, chained calls by declared return type, field accesses
  and constants, static calls on a type name, method references, and calls
  inside lambdas. Literal and typed arguments choose between same-arity
  overloads. Overrides typed through an external wildcard import still match
  their contract. A call is `exact` when one concrete source body can receive
  it, including the unique implementation of an interface; several
  implementations stay `possible`. A Spring Data query method declared in the
  sources is a `possible`, non-inferred target: the receiver type declares it.
  Library methods and unknown argument types stay unresolved.
- `codeql` resolves calls from CodeQL's typed program model. It needs the
  CodeQL CLI. A call whose argument type CodeQL cannot resolve reaches every
  matching same-name overload as `possible` instead of one as `exact`. It
  accepts `--engine-database` and refreshes one module.
- `joern` builds a code property graph of the projected Java sources with
  `joern-parse` (`javasrc2cpg`, no build) and reads its call graph. It needs
  Joern and a JDK on `PATH`, and on macOS `greadlink` (coreutils) for the
  Joern launchers. One linked body is `exact`; a unique implementation is
  `exact` with its interface declaration as a `possible` witness; several
  implementations are all `possible`, as are the targets of a call that
  an anonymous class, a lambda or a library class can also receive. A
  Lombok file keeps its written lines. A call whose argument types Joern
  cannot resolve reaches the owner's one abstract method of that name and
  arity as `possible`. Other calls Joern cannot resolve are
  dropped. It accepts no database, refreshes no module, reports no Kafka
  payload type, and caches its graph in `.systemlens/joern/`.
- `none` builds no interprocedural path: only same-method flows remain.

When the selected engine is unavailable, indexing says so and uses `source`.

| Option | Requirement |
|---|---|
| `--engine-database DIR` | The engine declares a database option. |
| `--engine-option NAME=VALUE` | `NAME` is an option the engine declares; the value is validated like the configuration. |
| `--refresh-call-graph-view` | The engine declares module refresh; `--module` is set. |
| `--call-graph-progress-html FILE`, `--resume-call-graph-join` | Any engine but `none`. |
| `--engine-progress`, `--engine-edge-confidence` | None. |

`--engine-edge-confidence` overrides `analysis.engine_edge_confidence`, which
accepts `exact` or `possible`. The default `possible` keeps possible dispatch
and inferred bridges as low-confidence flow evidence; `exact` excludes them
from flow reconstruction.

The automatic projection of an external engine preserves Java sources below
`target/generated-sources` so generated AsyncAPI/OpenAPI types remain
available; other build outputs and build descriptors are excluded.
`--call-graph-engine` and `--engine-option` apply to one run and do not modify
`.systemlens/config.yml`.

`systemlens analyze compare-engines --engines A,B [--root DIR] [--json]`
rebuilds interprocedural flows from the persisted index with two engines
and reports the flows and call edges both find, those only one finds,
and confidence changes. It persists nothing. `--engines` is required and
names two distinct declared engines; both must be available. Each engine
receives its `analysis.engines.NAME` options and the generic `engine_*`
settings.

Each engine joins the inputs indexing uses: persisted endpoints, integration
methods, modules, data operations and module dependencies. A flow is keyed by
its module, entry method, status and every step, so two routes between the
same ends stay two flows.

Call edges are compared only between indexed integration methods, which both
engines share. An edge is keyed by caller, callee, source path and call line;
its dispatch confidence and `inferred` label are compared as its value.
`confidence_changes_count` counts the edges whose labels differ. Edges that
touch a method only one engine models, such as a helper of a typed engine, appear under
`edges_outside_shared_methods` with a count and examples per engine.

`--call-graph-progress-html FILE` is an opt-in progress aid: after each
completed engine project it atomically replaces `FILE` with a graph labelled as
provisional, including the completed-project count. Users may refresh that
file in a browser to inspect the current method-call coverage. It is generated
by filtering the once-materialized global flows to reported call sites; direct
answers without intermediate evidence appear at the final checkpoint.
These in-progress indexing facts must not be treated as an exportable or
complete architecture snapshot; the normal `export microservices --html`
command remains the authoritative post-index export.

Engine diagnostics are silent by default. The explicit `--engine-progress`
option relays the engine's own progress messages for one command; `codeql`
then runs with `progress++` unless `analysis.engines.codeql.verbosity` sets
another level. The engine's messages are diagnostic progress only; they do not provide a
guaranteed percentage or remaining-time estimate. During the Python-side join,
SystemLens reports the call-attachment phase and the input-method exploration
at least every five seconds while work continues. These messages include
processed calls or methods, explored transitions, and elapsed time.

`analysis.engine_timeout_seconds` sets the positive wall-clock budget for the
complete engine pass, including source generation, database creation and
query execution; its default is `600` seconds. The remaining
budget is propagated to every subprocess. The deadline includes live progress
reading, even when a subprocess stops producing output. On POSIX, timeout or
interrupted progress handling terminates and reaps the complete process group.

A timeout is a soft boundary for the index: SystemLens keeps AST facts and
the engine calls of a call query that completed before the deadline, even
when a later query times out. A query cut by the deadline yields no call.
SystemLens then executes the remaining relation, flow, reconciliation and
statistics post-processing, and commits the resulting partial snapshot so it
can be exported to HTML. The
CLI reports that the graph is partial. A timed-out engine pass does not update
the code-flow signature, so the next index retries the interprocedural analysis
even when source files are unchanged. Other engine failures remain fatal and
preserve the previous committed snapshot.

When the join itself is interrupted after one or more committed batches,
`systemlens index --resume-call-graph-join` reuses the compatible repository
checkpoint and skips the completed IN methods. The option rejects changed
analysis inputs, incompatible settings, or a non-partial snapshot; generated
presentation files such as HTML exports do not invalidate it. An engine
timeout leaves no join checkpoint, because its flows come from a partial
call set: the option then fails and asks for a normal index. If the persisted
method list no longer
matches the current indexed method list, the command discards the stale join
cursor and restarts the join from the beginning.

`analysis.engine_threads` sets the number of threads an external engine may
use; its default is `0`, which means one thread per available core.
`analysis.engine_ram_mb` optionally sets the positive RAM limit in MiB; it
defaults to `null`, so the engine chooses its own limit.
`--disable` accepts `properties`,
`module-architecture`, and `module-tree-sitter`.

## Runtime observations

`systemlens observe elastic` contacts the Elasticsearch cluster at `--url`.
It reads only pre-aggregated APM metric data streams and aggregates of APM
error groups, never trace or error documents, so the transfer depends on
cardinality, not traffic. The API key needs `read` on `metrics-apm*` and
`logs-apm.error-*`; without the latter, no error is collected.
Elastic APM 8.7 or later is required. `--since` accepts `h` and `d` suffixes.
`--interval auto`, the default, picks the coarsest rollup that holds data;
`1m`, `10m`, or `60m` forces one.

The API key is read from the environment variable named by `--api-key-env`,
`SYSTEMLENS_ELASTIC_API_KEY` by default. A URL containing credentials is
rejected. `--ca-cert` adds a trusted CA bundle; TLS verification cannot be
disabled. The command needs an initialized repository and stores only the
cluster scheme, host, and port with the observations.

`systemlens observe prometheus --url URL --since 7d` reads OpenTelemetry
HTTP and messaging metrics through the Prometheus HTTP query API only, with
instant queries aggregated server side. The bearer token is read from the
environment variable named by `--bearer-env`,
`SYSTEMLENS_PROMETHEUS_TOKEN` by default; it is never logged or persisted.
A URL containing credentials is rejected. `--ca-cert` applies as above.
Observations are stored with `source = prometheus`; a Prometheus collection
has no error group because the metrics carry no exception grouping.

Each collection is persisted with its completeness: window, interval,
pages read per query, overflow buckets (`_other`), truncation, and notes.
The last three collections per source are kept. The report starts with
one "Complétude" line per source and states explicitly when neither
overflow nor truncation occurred.

The report groups observations by status: `matched`, `observed_only`,
`external`, `static_only`, `unverified`, and `not_compared`. `unverified`
lists indexed integrations whose silence the telemetry cannot prove, such as
routes of a service whose HTTP transactions carry no route. The report also
lists APM services that are `unmapped` or `ambiguous`, indexed services
without any observation, and APM error groups: exception type, occurrences,
transaction, and code location. Error messages are never stored.
Repeat `--service-map` to map an APM service name explicitly. Persistent
mappings belong in `runtime.service_map` of the
[configuration](configuration.md); the option overrides an entry with the
same APM name. `runtime.ignore_services` removes infrastructure services,
such as OTel collectors, from the unmapped list.

Request transactions carry p50, p95, and p99 latencies computed by the
backend from aggregated histograms; a destination keeps an average and no
percentile. `--json` exposes them as `p50_us`, `p95_us`, `p99_us`, and
`avg_us` on each row; existing fields keep their names and values.

`systemlens observe report --hotspots N --by p95|p99|failures|errors`
lists the N entry points with the highest value: service, target, indexed
`endpoint_ids`, window, calls, value, status, and `incomplete` when the
service overflowed in its collection. Ties break by service then target.
Entry points that are `not_compared` and indexed entries that are
`unverified` are listed apart. Rows without the measure are counted, not
ranked. An unknown `--by` exits with code 2.

`systemlens observe report --compare-previous` adds, per row matched on
kind, service, target, and target type, the delta of calls, of the failure
ratio, and of p95 against the previous collection of the same source and
interval. Without such a collection the report says why no comparison is
shown.

When observations exist, `systemlens export microservices --html` embeds
them as an optional overlay described in the
[HTML export behavior](html-export.md#runtime-observations-overlay).

Counts are lower bounds that depend on sampling; percentiles are backend
approximations. `static_only` means no traffic in the window, not dead
code. Observations never alter source facts.
The [technical specification](../spec-tech/runtime-observations.md) defines
the queries and matching rules.
