# Indexing

Parent: [Technical specification](../SPEC-TECH.md).


`index_repo` performs these steps:

1. Clear parser and discovery caches before each indexing pass.
2. Discover modules unless disabled. `discovery/build/projects.py` builds
   one `BuildLayout` per pass: projects, identities and excluded build
   directories (ADR-80). Inventory, file ownership (`module_for_path`) and
   the excluded perimeter all read it, so they cannot disagree.
3. Build the eligible-file hash inventory, respecting include/exclude rules,
   test-source exclusion, the layout's excluded build directories and
   nested-build boundaries.
4. Compare hashes with the stored inventory and purge removed files. A changed
   or deleted Spring configuration file or Maven/Gradle descriptor expands the
   delta to the owning module when the module boundary is known. Root-level or
   otherwise ambiguous inputs still promote the delta to a full endpoint refresh.
   A module whose identity changed, appeared or disappeared since the stored
   inventory expands the delta to every file below it; the root forces a full
   refresh.
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
and all endpoint-to-endpoint paths are traversed again. Persisted flows remain
eligible only when every endpoint step still belongs to the current endpoint
snapshot; stale flows are dropped before deduplication. AI facts are projected
into a transient topology view for reconciliation; the facts remain independent
rows and are never copied into source-derived tables.

The flow reconstruction treats each discovered module with an application
entry point as a service boundary. From an entry module, it rejects Java call
targets in another application module while retaining helper modules. It
applies the same rule to direct CodeQL input-to-output pairs. A set of
application module identities makes each boundary check constant time per
candidate edge. An engine method without an indexed row, and each method of
the persisted engine projection, takes the identity of the innermost
discovered build module containing its path, so the boundary applies to it.
A path outside every discovered module has no module identity.

When module metadata is absent, the reconstruction cannot classify the
boundary and retains the call-graph result without this filter.
The code-flow signature triggers a rebuild of existing indexes after this
rule changes.

With `systemlens flows calculate --module NAME`, the same reconstruction starts
only from input methods owned by `NAME`. The method and call-edge snapshots
remain global, so helper methods outside the selected module can still resolve.
Flows owned by other modules remain eligible when their endpoint steps are still
current. Flows owned by the selected module are rebuilt from the current method
and endpoint snapshots.

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
A transient scheduler trigger has no endpoint ID, so its source path and line
anchor the flow instead. Two schedulers reaching one publication therefore
remain two flows, even when their schedule labels are identical.

The file inventory scans all eligible paths and hashes their contents on each
index run. With `F` eligible files, `B` total bytes and `P` configured path
patterns, its cost is `O(F log F + B + F × P)`. Sorting provides deterministic
processing order, hashing provides the change decision, and pattern checks are
applied once per file. Incrementality limits AST extraction to changed files,
but it does not avoid this complete inventory pass.

Steps 1 to 7 execute inside `Store.transaction()`, which starts with
`BEGIN IMMEDIATE`. CodeQL stages can publish partial snapshots through
`Store.commit_checkpoint()`: each checkpoint commits the pending changes and
opens a new transaction. Final reconciliation publishes the completed snapshot.
See [checkpoint persistence](persistence.md) for stage and completion metadata.

An exception rolls back only changes since the latest commit. Without an
intermediate checkpoint, the previous snapshot remains intact; with one,
the committed partial snapshot remains available. Schema creation and
compatible migrations happen when a writable store opens, before an index
transaction. Read-only stores open SQLite in `mode=ro` and never migrate or
commit. Concurrent readers see the latest committed snapshot.

AST endpoint analysis uses no subprocess. For interprocedural flows, the
source engine is the default and needs no subprocess either. With the
`codeql` engine, the local CodeQL executable is used once for the repository
to create a temporary source-only Java projection (Java files only, without
Maven/Gradle descriptors or build outputs, except Java files below
`target/generated-sources`), create a database from that projection, query
it, and decode the result as CSV.

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
When a call has a source-backed interface declaration and CodeQL also exposes
a concrete virtual target, the query keeps the declared interface call as an
additional possible witness; an interface method that is itself the exact
target is reported once, as exact. This preserves the contract edge for
module-aware dispatch enrichment; duplicate graph rows are deduplicated by
their target and confidence.

The join reads the witness as it reads the source pass's witness. At a site
(caller, call line) where an exact row reaches a concrete implementation of
the interface method, the witness expands only to that implementation. The
symbol fallback then skips the invocation whose candidates include that
target, so it cannot reopen the other implementations. A witness without an
exact row at its site still expands to every compatible implementation.

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

Indexing asks the configured engine (ADR-61) for calls through
`callgraph.engine.CallGraphEngine`; it never imports an engine module. A
source symbol pass then follows receiver-typed calls between indexed Java
methods: with the `source` engine it is the whole call graph, with `codeql`
it fills the gaps. A receiver is typed from a field, parameter, local or
`var` initializer, a declared return type, a field access (`obj.field`,
`Type.CONSTANT`, enum constants), an array element, or a capitalized type
name for a static call; `receiver::name` is a call when that name has one
declared arity. A zero-argument `getX()` (`isX()` for a `boolean`) that no
source type declares takes the field type when Lombok `@Data`, `@Value` or
`@Getter` generates it. `toString()`, `hashCode()` and `equals(x)` return
`String`, `int` and `boolean` on any receiver.
Variables declared by an enclosing statement also type receivers: an enhanced
`for` variable, a single-type `catch` parameter, a try-with-resources
resource, and an explicitly typed lambda parameter. An untyped lambda
parameter, a `var` loop variable or a multi-catch parameter has no type, but
still shadows a field of the same name.
A `var` initializer is typed where it is written, not at the call site: a
local declared after it never shadows the field it names, and initializers
that name each other in a cycle get no type.
Names resolve lexically, innermost scope first: the locals and parameters
of every enclosing block, lambda and method, anonymous and local class
methods included, then the fields of each enclosing class before those of
its outer classes. A member inner class thus reads its outer class's
fields, and a local class the enclosing method's locals.
An anonymous class's own fields come before those of its source
supertype; an external supertype is assumed to declare none, a known
limit. A field hides any field of the same name farther up the hierarchy:
the type is searched first, then its supertypes by distance. Field
accesses and Lombok getters follow the same rule.
A type name resolves where it is written. A type parameter of an
enclosing method or class hides any source type of the same simple name:
the name has no type, in a receiver as in a dispatch signature. A local
class is visible from its declaration to the end of its block and inside
itself, never as a member type of the enclosing class.
An unqualified call binds to the innermost enclosing type, anonymous classes
included, that declares or inherits the name at any arity; a named inner
class thus reaches its outer type's methods. A scope with an external
supertype stops the search, since that supertype may declare the name,
unless it is a JDK type with fixed members (`Runnable`, `Callable`,
`Serializable`, `Comparable`, `AutoCloseable`, `Closeable`, `Cloneable`).
A name no enclosing type has resolves through a single static import, then
through the one static wildcard import whose source type declares that name
and arity. Several candidates stay unresolved.
Literals, `new`, casts, and typed variables give argument types. A numeric
literal takes its suffix: `L` gives `long`, `f` gives `float`, and a
decimal, hexadecimal, octal or binary integer is otherwise `int`.
Same-arity overloads follow Java's phases. The first allows identity,
widening primitive conversion and subtyping; the second adds boxing and
unboxing. The first phase with an applicable overload keeps its most
specific one. Subtyping is known for source types, the external interfaces
they list, `String` and the boxes.
A phase decides only when every argument and parameter type is known and
no overload's applicability depends on an unknown hierarchy. Otherwise the
pass drops only the overloads a known argument provably cannot reach.
Unknown arguments never narrow. An override whose parameter type comes from an
external wildcard import (`import org.springframework.http.*`) adopts the
signature of the one ancestor contract that matches its resolved types and
written simple names.
A concrete method with an unresolved parameter still implements itself, so
self-calls to it resolve. A call is `exact` when one concrete body sits on
the receiver's declared type or one of its ancestors. With the `source`
engine the pass is the whole call graph and runs in a closed world, like
CodeQL's `exactVirtualMethod`: a unique source implementation of a
source interface is also `exact`. Gap-filling for `codeql` stays open-world, so
there interface dispatch to a subtype is `possible`.
Several implementations are always `possible`. A call with no source body
reaches the one abstract declaration no source type implements, such as a
Spring Data query method, as a `possible` target. The pass drops ambiguous
overloads while retaining an ambiguous receiver type long enough to apply
module-scoped interface dispatch.

The join labels an edge `inferred` only when it is a candidate dispatch to a
concrete body. A call to a declaration without a body, such as the Spring
Data query method, is named by the receiver type: it stays a non-inferred
`possible` edge with both engines. When the engine already returned that
declaration, the symbol pass does not add it a second time.

A flow's reason names the weakest hop of its route, whichever engine built
it. An inferred dispatch candidate gives "A source-declared Java dispatch
fallback ...", an inferred signature join gives the signature-join reason,
a `possible` declared edge gives the virtual-dispatch reason, and a route of
`exact` edges gives "The call graph resolved a static call path ...".
A static or private method binds to its own declaration: it implements no
ancestor's contract, and no subtype homonym is a candidate for it.
An interface `default` body follows Java's "class wins" rule: a method a
class declares or inherits from its superclass chain, and a subinterface's
redeclaration, take precedence. The default stays a dispatch target only
while some source class, record or enum inherits it, an anonymous class
may, no source type implements the interface, or its signature is not
fully resolved.
`super.m()` binds without dispatch to the most specific declaration among
the superclass and its ancestors, after overload selection; the call is
`exact` on a concrete body and reaches nothing otherwise. `X.super.m()`
searches interface `X`, or the superclass of an enclosing class `X`.
The pass matches each indexed method to its declaration by start line,
end line and written signature, so two methods on one line stay distinct
in the symbol table. The join still locates a callee by name, path and
line, so it drops an edge to one of two same-name overloads on one line.

The closed world adds two shapes of the CodeQL query (ADR-70). When the
receiver's declared type is a source interface and the call has one exact
concrete target, the pass also emits a `possible` witness edge to the
abstract interface method the call names. The join stores it as a declared,
non-inferred edge and adds the inferred `possible` edge to the site's exact
implementation, as it does for a CodeQL row; it adds no other
implementation, so the witness is a dead end that opens no route and leaves
flow confidence unchanged. A static or un-overridden default interface
method that is itself the target, and an overridden default body, get no
witness; neither does a call whose dispatch is already `possible`.

A receiver typed by an external interface, one that a source type lists in
`implements` or `extends` (and `Thread` as a `Runnable`), dispatches to the
most specific concrete, non-static, non-private body of that name and arity
on each source subtype. The edge is always `possible`, even with one body,
because a library can supply the implementation (ADR-71). A subtype whose
same-arity overloads the arguments cannot separate leaves the call
unresolved. A call on an external class, such as `Object` or
`OutputStream`, reaches no source override: the class hierarchy of a
library is not in the sources.

ADR-71 scopes these candidates to the caller's visible modules: a body
counts only when its method belongs to the caller's module or to one of
that module's transitive build dependencies. Both sides use the module
identity of `module_identity`, which `module_dependencies` also stores.
The call-graph stage passes the discovered dependencies to the symbol
pass; a caller module without a known dependency sees only itself. A
library therefore never dispatches into a module that depends on it. The
dependency closure is computed once per caller module, and the candidate
cache is keyed by that visible set.

Each Spring `@Scheduled` method yields a transient scheduler trigger, used
only while flows are built and never persisted as an endpoint. It makes the
method an input of the call graph, so a scheduler that publishes through
other classes gets an interprocedural flow. Without it, the dominant lab
flow, a scheduler publishing `supermarket.order.placed` through a workflow
class, had no selectable root.
The same-method AST materializer records JPA and Mongo accesses in scheduled
methods alongside their publications. A data step's resource identity uses the
module of the flow's trigger, never another discovered module. It also persists a scheduled method
whose only indexed effect is a data access. Such a data-only flow remains
supporting evidence rather than a selectable inter-service call graph.

When duplicate qualified types exist in different build modules, resolution
first restricts candidates to the caller's module. Inheritance declarations and
dispatch indexes retain the `(qualified name, module)` identity, so homonymous
interfaces and classes cannot merge their relations. If the module scope is
absent or still ambiguous, all compatible candidates remain possible rather
than selecting one by name. An ambiguous receiver type is retained for
dispatch; compatible contracts are collected and the caller-module preference
is applied to their concrete implementations.

For a statically declared base-class receiver, both the base body and known
overrides remain possible. A single override does not prove the runtime type.
When a flow starts in a concrete subtype and then enters an inherited body,
dispatch is restricted to that subtype and its ancestors so sibling overrides
cannot cross into the flow. Owners are compared by qualified name, so the rule
also holds when the inherited body is declared in a helper module. When the
entry method is declared by the inherited body's own class, the runtime
receiver is unknown and every override stays possible.
A rebuild from persisted edges has no source symbols. It approximates the rule
per call site: when a site outside the entry's class fans out to several
overrides of one method name and one is declared by the entry's class, only
that override is followed. The `analyze` call-chain diagnostics apply the same
rule. No application class name is special-cased. Explicit `super` calls target direct parent bodies.
Each indexing pass sizes the parsed-tree cache to its Java inventory, bounded
by 20,000 files, so extractors, the method inventory, the symbol pass and flow
materialization share one parse per file. Retained memory therefore grows with
the inventory up to that bound; larger repositories reparse the excess files.
A method of an anonymous class is not indexed; its body belongs to the named
method creating the instance, like a lambda body (ADR-62). In that body, a
call on `this`, `super`, `this.field` or without receiver stays unresolved.
A method of a local named class is indexed as its own method: a port in its
body belongs only to that innermost declaring method, never to the enclosing
method that declares the class.
Symbol collection scans source bytes and syntax nodes once per flow rebuild.
`JavaSymbols.resolve` accepts explicit imports and qualified type names as
signature identities even when dependency sources are absent. Callees still
require indexed source methods; external type identities do not create edges.

The symbol index groups inherited declarations by name and parameter signature
for each source class. More specific declarations hide ancestor bodies, including
abstract redeclarations. Public instance bodies are associated with the class's
ancestor contracts, preserving separate compatible implementations.
For `D` inherited declarations in one signature group, hiding checks cost
`O(D²)`; the additional index stores compatible contract-to-method memberships.
A contract with an unresolved parameter type reads only the dispatch signatures
indexed under its owner, module, name and arity, so its lookup is linear in
those candidates instead of in the whole dispatch index.

For a method invocation used as a receiver, the fallback reads source-declared
return types through the receiver hierarchy. It requires one parameter signature
and one resolved return type, and bounds nested receiver analysis to 16 calls.
A zero-argument call with no declared method reads a record component of the
same name as its implicit accessor; a declared accessor takes precedence.
Generic return substitution and ambiguous overload selection are unsupported.
These inferred calls retain `possible` dispatch and low flow confidence.
Known positions in a partially resolved generic signature still constrain
candidate implementations. Unknown positions may widen the candidate set, but
they do not erase known parameter types.
Route traversal has the existing bounded `O(I × (V + E))` worst case for `I`
indexed input methods, `V` source methods and `E` resolved call edges.

The source projection prevents `build-mode=none` from invoking Maven or Gradle
for dependency discovery; unresolved external types are accepted as the
documented accuracy trade-off for offline indexing.
The projection receives the eligible relative-path inventory from the indexer,
so excluded test sources and build outputs cannot enter the temporary CodeQL
database or its persisted method projection. The projection treats `target`
and `out` as build directories only outside a `src` directory, like the
inventory, so a Java package with one of these names enters the database.

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
relative path and indexed line range: the indexed method starts at its first
annotation while CodeQL locates the declaration line. Module-scoped method
and call queries match the whole relative path against the module prefix,
as CodeQL's `regexpMatch` requires. Exact input-to-output reachability is
persisted with medium confidence; CodeQL-reported possible dispatch is
persisted with low confidence. With `codeql_edge_confidence = "exact"`, only `medium`
relations are kept, because they are the routes made of exact edges.

A reachability row becomes a flow only through a representative route of the
indexed call graph that passes the join's rules: the application boundary on
every hop, the entry's dispatch and `codeql_max_hops` (ADR-78). The route is
a shortest path from a predecessor tree cached per source and confidence. A
row without such a route creates no flow, and a pair the join already
connects keeps the join's flows.

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
interface call may be completed when the indexed implementations expose
compatible methods with the same arity. Each compatible implementation is
retained as a possible, low-confidence candidate; multiple candidates remain
separate rather than being collapsed into one runtime target. Repository-root
modules attribute source files without endpoints to the root module so their
helper methods remain available to the call graph.

When several modules provide compatible implementations, the call graph prefers
implementations from the caller's module. It retains cross-module candidates only
when the caller's module has no compatible implementation. This preference never
raises confidence: a target left unique only because other modules' candidates
were set aside keeps `possible` dispatch, even in the closed-world source pass.

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
method projection and all source-backed call edges are written to `codeql_methods`
and `codeql_call_edges`; ordinary intermediate methods remain distinct from
`integration_methods` and can be rehydrated in later joins. Live progress uses a
wall-clock watchdog covering pipe reads;

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

## Call shapes

`tests/test_call_graph_shapes.py` runs one Java fixture per dependency
shape through the symbol pass in a closed world, as the `source` engine
does, and checks each target and its confidence. The
[call-graph coverage plan](../plans/call-graph-coverage.md#2-dependency-shapes)
defines the rows, their frequency on the reference projects, and the task
that owns each gap. A shape not delivered is a strict expected failure.

| Status | Shapes |
|---|---|
| `covered` | S1 declared receivers, S2 static calls, S3 outer-type calls, S4 private methods, S5 overloads by argument type, S6 methods sharing a line (symbol pass), H1 inherited methods, H2 overrides and virtual dispatch, H3 template methods, H4, H5 and H6 `super.m()`, H7 interfaces, H8 records, H9 enum constant bodies, H10 external interfaces, H12 `X.super.m()`, H13 class wins over a `default`, G1 and G2 generic supertypes with a concrete type, T1 casts and array elements, T4 `var` initializers at their position, T5 names in anonymous, local and inner classes, T6 field hiding, T7 local class scope, T8 type parameters, F1 lambdas and method references as call sites, F4 JDK callbacks, M1 external dispatch scope, W1 injected fields, W4 `@Async` as a plain call, W5 `@Scheduled`, W6 runners as calls, W7 derived queries, W8 generated implementations |
| `defect` | F2 lambda implementations, F3 anonymous subclasses, M2 dispatch out of the visible modules, M3 one type in two modules |
| `missing` | S7 constructors, S8 constructor and initializer bodies, H11 external superclasses, G3 generic implementations, G4 bounded type variables, T2 and T3 pattern variables, W2 bean narrowing, W3 application events |
| out of scope | X1 reflection, X2 AOP advice, X3 HTTP and Kafka transitions |

## Build layout

The layout walks the repository once with `os.walk`, without a depth limit.
It prunes hidden directories, `node_modules`, `__pycache__`,
`bower_components`, and `target` or `build` directories outside `src`. The
cost is one directory listing per source directory, below the file
inventory's own walk.

Maven projects are resolved by a fixpoint over the POM directories, from
the shallowest. A kept aggregator whose `<modules>` all resolve to a POM
directory governs its subtree: a POM below it is kept only when some kept
aggregator declares it. Entries leaving the repository are ignored.

Gradle builds are resolved per `settings.gradle(.kts)`, from the shallowest.
`parse_gradle_settings` strips comments outside strings, reads literal
`include`, `includeBuild` and `projectDir` remaps, and derives each
project's directory from its parent's. A settings file with no literal
include, a non-literal argument, an unmatched `projectDir` assignment or a
dynamic marker (`apply from`, `eachDir`, `.each`, `forEach`, loops) is
incomplete: its directory is the root project and its build files stay
projects.

`BuildLayout.owner(path)` walks the path's parents and returns the first
project, or `None` at an excluded directory. Layouts are cached per root and
cleared by `clear_analysis_caches`; `discover_modules` refreshes the cache.
Maven module dependencies resolve `${...}` groupIds from the POM and its
local parent chain; a placeholder that stays unresolved never matches a
known groupId.
