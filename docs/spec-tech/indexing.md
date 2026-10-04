# Indexing

Parent: [Technical specification](../SPEC-TECH.md).


`index_repo` performs these steps:

1. Clear parser and discovery caches for long-lived MCP processes.
2. Discover modules unless disabled.
3. Build the eligible-file hash inventory, respecting include/exclude rules,
   test-source exclusion, Maven test/archetype module exclusion and nested-build
   boundaries.
4. Compare hashes with the stored inventory and purge removed files. A changed
   or deleted Spring configuration file or Maven/Gradle descriptor expands the
   delta to the owning module when the module boundary is known. Root-level or
   otherwise ambiguous inputs still promote the delta to a full endpoint refresh.
5. Force a full refresh when extractor/configuration/strategy signatures differ.
6. Run AST extractors for the changed files and atomically replace their
   endpoints.
7. Persist hashes, modules, dependencies and derived relations.

The CLI `index --module NAME_OR_PATH` keeps the repository snapshot and the
CodeQL analysis global, then filters the final flow listing to the selected
build module. This preserves cross-module call resolution. A supplied global
`--codeql-database` avoids rebuilding the CodeQL database during repeatable
debug runs.

`index --module NAME --refresh-codeql-view` forces the interprocedural stage
and queries source-backed CodeQL methods under the selected module path. The
result replaces that module's method rows while preserving rows from other
modules, so the persisted graph can still traverse shared helpers. The option
is intentionally module-scoped to support creating the method view gradually;
passing `--codeql-database` reuses the same global CodeQL database for each
module refresh.

The `systemlens flows calculate` command reconstructs the complete persisted
flow snapshot from stored endpoints, integration methods, CodeQL call edges,
modules, existing source-flow candidates and `graph_facts`. It does not invoke
source extraction or CodeQL. The persisted method graph is rebuilt in memory
and all endpoint-to-endpoint paths are traversed again; existing `code_flows`
are retained as the persisted AST/source-flow snapshot and deduplicated with
the reconstructed interprocedural flows. AI facts are projected into a
transient topology view for reconciliation; the facts remain independent rows
and are never copied into source-derived tables.

With `systemlens flows calculate --module NAME`, the same reconstruction starts
only from input methods owned by `NAME`. The method and call-edge snapshots
remain global, so helper methods outside the selected module can still resolve.
Existing flows remain in the database, and reconstructed flows for the selected
module replace their matching derived representatives during deduplication.

Read-model flow listing builds inverted indexes from endpoint identity and
`(system, topic, role)` to avoid rescanning every flow and endpoint for each
candidate. Graph projections similarly index Kafka endpoints by service/topic
and relation targets by service/system/role/topic. Indexing audits build
flow-by-input and flow-by-output maps in one pass. These projections keep the
usual lookup paths linear in the number of facts plus emitted matches; bounded
sorting remains only for deterministic output ordering.

The optional flow-listing module filter selects flows owned by the requested
module only when every endpoint with an explicit module identity also belongs
to that module. Missing endpoint module identities do not create a guessed
cross-module relationship. The filter reads the persisted flow and endpoint
snapshot and never reparses source files.

The diagnostic form also exposes the ordered construction of each selected
port flow: IN, persisted method-call transitions and OUT, with relative path
and line evidence. A direct IN-to-OUT flow is labelled as a same-method call,
not as an interprocedural chain. Scheduled flows without an IN port are
reported as excluded from this view. When no port flow is persisted, the
diagnostic reports the module's indexed inputs and outputs and keeps the
result as not found rather than inferring a path.

The module statistics view counts a flow only when its first indexed endpoint
is an IN port, its last indexed endpoint is an OUT port, and every explicitly
assigned endpoint belongs to the same module. Scheduled flows and unresolved
paths are not counted as internal module flows; their ports remain visible in
the IN and OUT totals.

When several routes share the same indexed IN and OUT ports, the persisted
representative prefers a route with method-call evidence. Confidence and route
length remain secondary selection criteria, and the number of alternatives is
retained. This keeps the representative useful for debugging without turning
alternative routes into additional asserted flows.

The file inventory scans all eligible paths and hashes their contents on each
index run. With `F` eligible files, `B` total bytes and `P` configured path
patterns, its cost is `O(F log F + B + F × P)`. Sorting provides deterministic
processing order, hashing provides the change decision, and pattern checks are
applied once per file. Incrementality limits AST extraction to changed files,
but it does not avoid this complete inventory pass.

Steps 1 to 7 execute inside `Store.transaction()`. The writable connection uses
`BEGIN IMMEDIATE`, then commits the complete snapshot only after relation
materialization succeeds; any exception rolls back files, endpoints, modules,
dependencies, relations and index signatures together. Schema creation and
compatible migrations happen when a writable store opens, before an index
transaction. Read-only stores open SQLite in `mode=ro` and never migrate or
commit. A concurrent reader sees the last committed snapshot until the writer
commits the next one.

AST endpoint analysis uses no subprocess. For interprocedural flows, the local
CodeQL executable is used by default once for the repository to create a
temporary source-only Java projection (Java files only, without Maven/Gradle
descriptors or build outputs, except Java files below `target/generated-sources`),
create a database from that projection, query it, and decode the result as CSV.
The temporary database remains global so cross-module references have the same
resolution context. CodeQL calls and methods are queried once globally, then
the returned rows are partitioned and processed one source-owning module at a
time. Each module reports its own extracted-call count and duration, and each
completed module can publish the currently available `code_flows` as an
explicit partial checkpoint when explicit progress output is enabled. Without
that option, module processing only reports progress and the final pass
persists the complete flow snapshot once. The final pass joins all call facts
together.
CodeQL's temporary query pack exports only source-located resolved calls.
`--codeql-database` reuses one global database supplied by the caller. Once the
source-backed call graph is constructed, its
edges are committed as an explicit partial checkpoint before input-to-output
reconstruction begins. The checkpoint records
`codeql_call_graph_status` and the persisted edge count; this status does not
imply that the flow join is complete. After the input-to-output calculation,
the provisional merged flows are committed before topology reconciliation;
`codeql_input_output_status` distinguishes a complete join from one based on a
timed-out partial CodeQL pass. The temporary CodeQL query pack pins
`codeql/java-all` and resolves it only from the already installed local CodeQL
pack cache; indexing never runs `codeql pack install` or downloads analyzer
dependencies. The configured CodeQL thread count and optional RAM limit are
passed to database creation and query execution only; they tune performance and
do not alter or invalidate persisted architecture facts.

When CodeQL is disabled or unavailable, a source symbol pass follows uniquely
resolved, receiver-typed calls between indexed Java methods. It keeps these
candidates at low confidence and drops ambiguous types or overloads.
When duplicate qualified types exist in different build modules, resolution
first restricts candidates to the caller's module. If that scope is absent or
still ambiguous, the call remains unresolved rather than selecting by name.
Symbol collection scans source bytes and syntax nodes once per flow rebuild.
Route traversal has the existing bounded `O(I × (V + E))` worst case for `I`
indexed input methods, `V` source methods and `E` resolved call edges.

The source projection prevents `build-mode=none` from invoking Maven or Gradle
for dependency discovery; unresolved external types are accepted as the
documented accuracy trade-off for offline indexing.
With `--generate-sources`, the indexed repository runs only Maven
`generate-sources` or Gradle `generateSources` before this projection is
created. A root build descriptor owns its nested modules. If the repository has
no root descriptor, generation runs once in each outermost nested Maven or
Gradle project, so an aggregator is not required. Java files below a Maven
`target/` directory are then included in the persisted file inventory and AST
analysis. Other non-Java build output remains excluded. The repository is not
compiled or tested; Maven generation uses the local cache and can therefore
fail when the required plugin or dependency is unavailable.
The call query keeps both caller and callee in source code, folds exact
dispatch before viable-dispatch expansion, and computes that resolution once
per call. The Python post-processing has two distinct stages: it first resolves
the CodeQL rows into a transient source-backed directed graph containing every
source method returned by CodeQL, then groups its weak connected components and
traverses the directed edges from indexed input methods to indexed output
methods. `integration_methods` are anchors for those endpoints, not a filter
for intermediate methods. Components only restrict the search to methods linked
by observed calls; they do not reverse edges or create routes. It does not
connect endpoint facts by name alone.
CodeQL also runs an output-anchored transitive reachability query whose source and target
predicates are restricted to the already indexed input/output methods by
relative path and start line. Exact input-to-output reachability is persisted
with medium confidence; CodeQL-reported possible dispatch is persisted with
low confidence.

Module ownership uses repository-path ancestry rather than comparing every
source path with every module. Call partitioning uses the same prefix map, so
both operations are linear in the number of paths or calls times the maximum
path depth, not in the product of paths or calls and module count. The
resolved call graph is built once per index pass; module checkpoints reuse that
graph and do not rematerialize it. Flow traversal remains bounded by the
number of indexed input methods and the graph size, `O(I × (V + E))` in the
worst case, because each input preserves its own dispatch and cycle state.
Both recursive relations seed only indexed outputs and retain that output as
their first argument throughout recursion; they do not construct an unrelated
all-method-pairs closure. Both edges require a source caller and source callee.
The result explicitly names all seven CSV columns consumed by the decoder.
Python joins require exact source path, method and line evidence except for
the unique-name fallback above. Buildless AST fallbacks may add only a
source-declared, uniquely compatible abstract-to-concrete bridge or
receiver-typed helper call; these synthetic edges remain `possible`/`low` and
are never name-only guesses. A receiver-typed helper call preserves the
concrete consumer while walking an inherited method body; when that body calls
a method overridden exactly once by the concrete consumer, the unique override
is retained as a possible edge. Ambiguous sibling overrides remain unresolved.
The fallback also scans methods that already own an output endpoint and keeps
separate resolved-call keys for calls sharing one source line. A generic
interface call may be completed when the indexed implementations expose one
unique compatible method with the same arity; multiple compatible
implementations remain unresolved. Repository-root modules attribute source
files without endpoints to the root module so their helper methods remain
available to the call graph.
The BFS visits each
method/confidence state
at most once per input endpoint, under the configured depth/global transition
bounds. Cyclic witness paths are retained without expansion. Medium-confidence
witnesses exclude possible dispatch. One predecessor tree is
cached per input method/confidence in a bounded in-memory cache, costing
O(V+E) per retained tree plus route reconstruction proportional to emitted
steps, instead of a BFS per input/output pair. HTML checkpoints filter cached
flows instead of rebuilding and retraversing the method-call graph for every module.
When a join is resumed, both the direct-flow pass and the bounded BFS start at
the persisted input-method offset. Previously completed input methods are not
revisited. The resume checkpoint therefore preserves the same `O(I × (V + E))`
worst-case bound as a complete join while reducing the work in proportion to
the completed prefix.
The read-only `analyze indexing-audit` command evaluates twenty distinct
quality-control rules against the persisted snapshot. It reports only detected
findings, keeps zero-count rules in the JSON summary, and includes relative
source evidence when the finding has a persisted location. The audit does not
re-parse source files or convert informational uncertainty into an asserted
architecture relation.
The read-only `analyze call-edge` command resolves each selector against the
persisted method ID, qualified name, then qualified-name suffix. It reports
ambiguous selectors without choosing a candidate. For one caller and callee,
it checks the persisted edge before applying the recorded confidence filter.
When the callee is an interface method, the diagnostic also resolves its
source-backed indexed implementations and accepts a persisted edge to one of
those concrete targets as the requested dynamic-dispatch edge.
It then tests reachability from any indexed input method and to any indexed
output method. Existing snapshots do not persist rejected raw CodeQL rows, so
an absent edge remains classified across extraction and CodeQL-to-AST joining.
The diagnostic reports presence as a three-state fact and includes the proof
basis for each value. An integration method proves AST node presence, and an
incident non-inferred call edge proves CodeQL node presence when only the
persisted call-edge snapshot is available. The CodeQL projection also stores
all source-backed method nodes, so ordinary intermediate methods can be
rehydrated during a persisted flow reconstruction. A non-inferred
caller-to-callee edge proves CodeQL edge presence. Inferred edges do not count
as CodeQL proof; unsupported absence checks remain `unknown`.
When `--codeql-database` is supplied to `analyze call-edge`, node presence is
queried directly from the source-backed CodeQL `Method` entities instead of
being inferred from the persisted call-edge snapshot. The BQRS/CSV query
artifacts are temporary and are not persisted; the database must correspond to
the indexed source revision and relative paths.
The module-level `--show-call-chains` diagnostic follows the same rule when an
external CodeQL database is supplied: it rebuilds the transient full call graph
before rendering internal-flow examples, so an unchanged snapshot does not
restrict the diagnostic to persisted endpoint-anchor edges.
After the call adjacency is built, the decoded CodeQL rows and transient Java
symbol indexes are released before route expansion; the adjacency remains the
single in-memory call-graph representation used by the BFS. The source-backed
method projection and all source-backed call edges are written to
`codeql_methods` and `codeql_call_edges`; ordinary intermediate methods remain
distinct from `integration_methods` and can be rehydrated in later joins. Live progress uses a wall-clock
watchdog covering pipe reads;
POSIX timeouts terminate the complete process group, and
subprocesses are reaped on errors. The configured deadline covers the complete
CodeQL pass, not each command independently.
An absent selected engine is reported and keeps AST-only results. A CodeQL
timeout is handled as a partial pass: the index keeps AST facts and any calls
recovered from a completed partial result, runs all remaining materializers and
commits the partial
snapshot; the code-flow signature stays invalid so a later index retries
CodeQL. Non-timeout failures from an available executable remain fatal and
leave the previous successful snapshot intact.
