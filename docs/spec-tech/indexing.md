# Indexing

Parent: [Technical specification](../SPEC-TECH.md).


`index_repo` performs these steps:

1. Clear parser and discovery caches for long-lived MCP processes.
2. Discover modules unless disabled.
3. Build the eligible-file hash inventory, respecting include/exclude rules,
   test-source exclusion, Maven test/archetype module exclusion and nested-build
   boundaries.
4. Compare hashes with the stored inventory and purge removed files. A changed
   or deleted Spring configuration file or Maven/Gradle descriptor promotes the
   delta to a full endpoint refresh because these files are dependencies of
   otherwise unchanged Java facts.
5. Force a full refresh when extractor/configuration/strategy signatures differ.
6. Run AST extractors for the changed files and atomically replace their
   endpoints.
7. Persist hashes, modules, dependencies and derived relations.

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
create a database from that projection, query
it, and decode the result as CSV;
progress checkpoints group the global result by source-owning project. Each
completed project publishes the currently available `code_flows` as an
explicit partial checkpoint, while the final pass joins all call facts
together. CodeQL's temporary query pack exports only source-located resolved
calls. `--codeql-database` reuses one global database
supplied by the caller. The temporary CodeQL query pack pins
`codeql/java-all` and resolves it only from the already installed local CodeQL
pack cache; indexing never runs `codeql pack install` or downloads analyzer
dependencies. The configured CodeQL thread count and optional RAM limit are
passed to database creation and query execution only; they tune performance and
do not alter or invalidate persisted architecture facts.
The source projection prevents `build-mode=none` from invoking Maven or Gradle
for dependency discovery; unresolved external types are accepted as the
documented accuracy trade-off for offline indexing.
With `--generate-sources`, a disposable copy runs only Maven
`generate-sources` or Gradle `generateSources` before this projection is
created. The indexed repository is never modified, compiled, or tested; the
generation command may still require cached or remote plugin dependencies.
The call query keeps both caller and callee in source code, folds exact
dispatch before viable-dispatch expansion, and computes that resolution once
per call. The Python post-processing has two distinct stages: it first resolves
the CodeQL rows into one source-backed directed method graph, then groups its
weak connected components and traverses the directed edges from indexed input
methods to indexed output methods. Components only restrict the search to
methods linked by observed calls; they do not reverse edges or create routes.
It does not connect endpoint facts by name alone.
CodeQL also runs an output-anchored transitive reachability query whose source and target
predicates are restricted to the already indexed input/output methods by
relative path and start line. Exact input-to-output reachability is persisted
with medium confidence; CodeQL-reported possible dispatch is persisted with
low confidence.
Both recursive relations seed only indexed outputs and retain that output as
their first argument throughout recursion; they do not construct an unrelated
all-method-pairs closure. Both edges require a source caller and source callee.
The result explicitly names all seven CSV columns consumed by the decoder.
Python joins require exact source path, method and line evidence except for
the unique-name fallback above. Buildless AST fallbacks may add only a
source-declared, uniquely compatible abstract-to-concrete bridge or
receiver-typed helper call; these synthetic edges remain `possible`/`low` and
are never name-only guesses. The BFS visits each
method/confidence state
at most once per input endpoint, under the configured depth/global transition
bounds. Cyclic witness paths are retained without expansion. Medium-confidence
witnesses exclude possible dispatch. One predecessor tree is
cached per input method/confidence in a bounded in-memory cache, costing
O(V+E) per retained tree plus route reconstruction proportional to emitted
steps, instead of a BFS per input/output pair. HTML checkpoints filter cached
flows instead of rebuilding and retraversing the method-call graph for every module.
After the call adjacency is built, the decoded CodeQL rows and transient Java
symbol indexes are released before route expansion; the adjacency remains the
single in-memory call-graph representation used by the BFS, and its normalized
edges are persisted in `codeql_call_edges`. Live progress uses a wall-clock
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
