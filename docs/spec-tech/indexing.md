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
`--engine-database` avoids rebuilding the CodeQL database during repeatable
debug runs.

`index --module NAME --refresh-call-graph-view` forces the interprocedural stage
and queries source-backed CodeQL methods under the selected module path. The
result replaces that module's method rows while preserving rows from other
modules, so the persisted graph can still traverse shared helpers. The option
is intentionally module-scoped to support creating the method view gradually;
passing `--engine-database` reuses the same global CodeQL database for each
module refresh.

A refresh that reaches the engine deadline keeps the persisted method rows
and call edges unchanged. The join rebuilds the flows from that persisted
graph, and the snapshot is marked `partial`, so the next index retries the
engine.

The `systemlens flows calculate` command reconstructs the complete persisted
flow snapshot from stored endpoints, integration methods, call edges, modules,
data operations and `graph_facts`. It does not extract endpoints or run an
engine. It runs the passes indexing runs, with the configured
`engine_max_hops` and `engine_edge_confidence`: the same-method pass, the
join over the persisted call graph, key tracing and the `after_commit` phase.
Those passes read Java sources under the repository root, as indexing does.

One merge combines the same-method and join flows for indexing with every
engine and for `flows calculate`, so the same snapshot gives the same flows
and identifiers (BL-11). A same-method flow holds every effect of its
method. A direct join flow, without a `method_call` step, is dropped when a
same-method flow of its trigger already holds each of its effects; a direct
flow the same-method pass lacks, such as a contract-only HTTP input, stays.

AI facts are projected into a transient topology view for reconciliation;
the facts remain independent rows and are never copied into source-derived
tables. An index run with an `--engine-edge-confidence` override is rebuilt
with the configured value, not the override.

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
only from input methods owned by `NAME`, and the same-method pass keeps only
the flows `NAME` owns. The method and call-edge snapshots remain global, so
helper methods outside the selected module can still resolve. Flows owned by
other modules remain eligible when every endpoint step still belongs to the
current endpoint snapshot; stale flows are dropped before deduplication.
Without `--module`, every flow is rebuilt and no persisted flow is kept.

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

A file the inventory cannot read, because of its permissions or because it
disappeared after the walk, stays out of the inventory. So does a symbolic
link that resolves outside the indexed root. The index continues, and its
progress output names each skipped file with the reason
(`fichier ignoré, <path> : <reason>`). A previously indexed file that
becomes unreadable is purged like a removed file.

A changed build descriptor or Spring property file rescans the files of the
module that owns it. A Spring Cloud Config file
(`configurations/<application>.yml`, `.yaml` or `.properties`) configures
the application it names, in any module, so its change rescans every file.
A module-rooted `target`, `build` or `out` directory is build output for
the inventory and for the engine projection alike.

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

The database build reaches the network. With `--build-mode=none`, CodeQL
guesses library dependencies from the `import` lines of the projection,
downloads their jars from Maven Central, and types library signatures
from them. SystemLens does not configure or restrict this download.
An offline run may lose the edges that need a library type; the engine
parity cases of the A family depend on those jars. The `source` engine
reads no jar: it types the Spring signatures of its own table offline
(ADR-90).

The database is cached in `.systemlens/codeql/java-database` with
`java-database.fingerprint` beside it (ADR-84). The fingerprint hashes every
projected file's path and content and the CodeQL executable's path, size and
modification time. A run whose projection has the same fingerprint reuses
the database; any difference creates a new one, which replaces the cache.
The stamp is removed before the replacement and written after it, so an
interrupted run leaves no stale stamp. On train-ticket, a reused database
takes the index from 117 s to 24 s. The cache costs disk space, 65 MB there.

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

The declared method of a call is its source declaration, so a call on a
parameterized receiver such as `Gateway<String>.send` keeps its declared
row and its interface witness; the reachability query follows the same
rule. In build mode `none`, CodeQL picks the first declared overload when
an argument has an `ErrorType`, and reports it as exact. When the declaring
type has other same-name overloads with the call's arity that agree with
it on every typed argument, the call reaches each of them as `possible`
and none as `exact`.

The query applies ADR-81. A target is `exact` when it is the one
production body among CodeQL's viable implementations: a body under a
Maven or Gradle test source set (`src/test`, `src/<name>Test`) never
counts, so a user-built database holding tests gives the automatic
database's answer. A call whose declared method belongs to a
`@FeignClient` interface or one of its supertypes has no `exact` target:
the fallback class is `possible` and the interface method stays a
target. A call to `equals`, `hashCode` or `toString` on a receiver typed
`Object` or by a type variable produces no row.

A virtual call also loses its `exact` target when a concrete production
type the receiver may hold has no source body for it (rule 1, BL-32).
The receiver's bound is the one dispatch uses: the type-flow bound, or
else the qualifier's type, a type variable's upper bound, or the enclosing
type of an unqualified call; a created instance is an exact bound. Each
concrete source subtype of the bound must reach a written production body
through `exactMethodImpl`. A record accessor, a body inherited from a
library class or `Object`, or a supertype CodeQL cannot resolve fails
that test. A Lombok member is a viable source method already, so the
unique-body test excludes it. The check runs only on calls that have a
unique target.

Constructor calls follow ADR-75. A `new T(...)`, `this(...)` or
`super(...)` row names the constructor `T.<init>`, `exact` unless an
untyped argument leaves same-arity overloads open, as above. The implicit
`super()` CodeQL adds, which has its constructor's own location, and the
constructors of anonymous classes and lambdas give no row. A constructor
reference `T::new` gives the row of the `new T(..)` CodeQL synthesizes
for it, from the reference's line (BC-19). A call in
instance initializer code (`<obinit>`) is reported once per constructor
that calls `<obinit>`, so its caller is that constructor; `<obinit>`
itself is never a target. Static initializer code keeps the caller
`T.<clinit>`. The method query leaves both initializer methods out.

The join reads the witness as it reads the source pass's witness. At a site
(caller, call line) where an exact row reaches a concrete implementation of
the interface method, the witness expands only to that implementation, and
that expansion is dropped because the exact edge already reaches it
(ADR-70, A4). The symbol fallback then skips the invocation whose
candidates include that target, so it cannot reopen the other
implementations. A witness without an exact row at its site still expands
to every compatible implementation. A rebuild from persisted edges applies
the same rule: a declared abstract edge at a site with a declared `exact`
edge to one of its implementations adds no dispatch (ADR-86).

When the index knows module dependencies, a typed engine's edge, the
dispatch the join adds for it and the symbol fallback beside it never join
two modules the build graph does not relate: the callee's module is the
caller's, one of its transitive dependencies, or a module that depends on
it (ADR-71, J2). A rebuild from persisted edges expands a declared
contract edge through the same `dispatch_expansion` helper as the fresh
join, so it applies the same scope and `index` and `flows calculate`
agree (BL-51). An
`exact` engine edge to an enum method that a constant body overrides
becomes `possible`, and each overriding body is a `possible` target: an
engine without constant-body methods cannot prove the receiver (H9, J4).

`--engine-database` reuses one global database supplied by the caller
(ADR-83). Before any query, the engine reads `sourceLocationPrefix` from
`codeql-database.yml` and compares each indexed Java file with its copy in
the database's source archive, `src.zip` or `src/`. One differing file
refuses the database as stale. No indexed file in the archive refuses it as
built from another source root. A file CodeQL did not archive, such as
`package-info.java`, is not a mismatch. The cost is one read of the archive
and of the indexed Java files.

The call rows are checked again: when CodeQL returns calls and none comes
from an indexed Java file, the engine refuses the database instead of
leaving only the symbol pass's edges under CodeQL's name. A hash of
`codeql-database.yml`, never its path, enters the code-flow signature, so a
recreated database recomputes the flows of unchanged sources. For the
automatic database, a hash of the CodeQL executable's identity enters the
signature instead: a new CodeQL recomputes them as well (ADR-84).

Once the source-backed call graph is constructed, its
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

### Engine declarations and settings

Every engine publishes an `EngineDeclaration` (ADR-85): `name`, `label`,
`description`, a lazy `factory` (`"module:function"`), an
`unavailable_reason()` check, `supports_module_refresh`, `resolves_types`,
its `EngineOption`s, the option that holds a prebuilt database, default
exclusions, and an artifact-directory predicate. `systemlens init --engine`
accepts every declared engine (PDR-13).
`callgraph/registry.py` lists the declaration modules
(`engines.custom`, `engines.codeql`, `engines.joern`) and is the only core
module that names them; importing a declaration never imports an adapter.
When the selected engine is unavailable, the stage loads the default
engine through the registry.

`Config.engine_settings()` builds the `EngineSettings` a factory receives:
the generic `engine_threads` and `engine_ram_mb`, and the engine's options.
Options merge in this order: declared defaults, then
`analysis.engines.NAME`, then `--engine-option` and `--engine-database`
for one run. Each value passes the option's `parse()`; a `path` option is
resolved against the indexed root and never persisted.

An engine method that the index does not hold is persisted as a placeholder
row of the call graph, except a method of an anonymous class
(`<anonymous class>.run`): it has no identity (ADR-62), and the join
attributes its calls to the creating method.

The CLI builds an explicit engine only when one run overrides options;
otherwise `index_repo` builds the configured one. `engine_timeout_seconds`
reaches the engine as `EngineRequest.timeout_seconds` and its deadline;
`engine_max_hops` and `engine_edge_confidence` bound the join and enter the
code-flow signature, with the engine's `input_fingerprint()`.
The join counts, in `stats["hop_limited_entries"]`, the entry methods with
a route stopped at `engine_max_hops` while its last method still has
calls; the index log prints that count with the setting (BL-52). A join
resumed from a checkpoint counts only the entries of the current run.

Persisted names written before ADR-85 keep their spelling: the
`codeql_call_edges` and `codeql_methods` tables, the `codeql_*` meta keys,
the `codeql:` method identifier prefix, the `direct-codeql` flow marker and
the export's `codeql_call_graph` key. Code refers to them only through
`storage/persisted_names.py`, so existing indexes, exports and flow
descriptions stay readable.

### Joern engine

The `joern` engine projects the indexed Java files with
`indexing/source_projection.py`, the projection CodeQL reads, then runs
`joern-parse --language javasrc -o cpg.bin <projection> --frontend-args
--delombok-mode types-only`. In that mode the graph keeps the written
source of a Lombok file: generated members only type the calls, have no
line, and are dropped. Paths in the graph are relative to the projection
root, hence to the indexed root.

The graph is cached as `.systemlens/joern/cpg.bin` with `cpg.fingerprint`,
a hash of the projection, the parse options and the Joern version; the
stamp is written after the graph, as ADR-84 does for CodeQL.

`joern --script engines/joern/export.sc` then loads a copy of the graph in
a temporary workspace and writes one JSON document (`systemlens-joern-v1`)
with every internal method and every non-operator call, each call with the
methods Joern's call graph links it to. `engines/joern/extraction.py`
keeps a call only when its target is resolved and at least one linked
method has a source file among the indexed files.

| Linked source methods | Confidence |
|---|---|
| One method with a body | `exact` |
| One abstract declaration | `possible` |
| One body and the abstract declaration the call names | body `exact`, declaration `possible` |
| Several bodies | every method `possible` |
| Any of the above, and a linked body in an anonymous class, a lambda or a library | every source method `possible` |
| Any of the above, on a call named on a `@FeignClient` interface or one of its direct supertypes (`proxied_types`) | every source method `possible` (ADR-81) |
| A dynamic call whose named type has a concrete internal subtype (`types`) that reaches no linked body, or generates the member (record accessor, Lombok) | every source method `possible` (ADR-81 rule 1) |
| No resolved signature (`Owner.name:<unresolvedSignature>(n)`), one source method `name` with `n` parameters on `Owner`, without a body | that method `possible`; a concrete body is left to the symbol pass |

Static initializers, lambdas and anonymous-class methods are never
targets, but a body among them still keeps the call `possible` (ADR-70,
ADR-81).

The `types` list holds each internal type with its supertypes, its
`ABSTRACT` modifier, its annotations and its fields. Joern drops a
record's interfaces from `inherits`; the adapter reads them from the
generic signature (`LRecord;LNamed;`) by simple name, which can widen the
subtypes of a type but never narrow them. A concrete subtype reaches a
linked body when it or one of its internal supertypes declares one. It
generates the member under the `source` engine's rules: a record
component accessor, or a Lombok getter, setter, `equals`, `hashCode` or
`toString` it does not write itself. Joern parses Lombok types only, so
its call graph never links a generated member. The named type is the
method's declaring type, not the receiver's type flow: Joern narrows
nothing, so the check can only make a call `possible`. An export without
`types`, from an older script, skips the check. A constructor is the target `Owner.<init>` (ADR-75). Joern
inlines field initializers into every constructor that does not start
with `this(...)`, and static field initializers into `Owner.<clinit>`,
which callers keep; it leaves initializer blocks out of its graph, so the
symbol pass adds their calls. A local class method, spelled
`pkg.App.create:void().Local.go:void()` by Joern, is mapped to the index's
name `pkg.App.Local.go`. A call inside a lambda or an anonymous class keeps an
`<anonymous>` caller, which the join attributes to the enclosing indexed
method by call line. Joern returns no reachability and refreshes no
module. For Strategy1, the export also lists each `envoyerMessageKafka*`
call with its second argument's type, its written declaration and the
local assignments of the calling method; `parse_payload_types` follows the
CodeQL query's rules on them: a wrapper's written type argument, else the
types assigned to the variable through other variables, else its own type.
The evidence is decoded only when the stage asks for it, under
`--strategy strategy1`. Both launchers receive `JAVA_OPTS` with
`-Xmx<engine_ram_mb>m` and `-XX:ActiveProcessorCount=<engine_threads>`;
`engine_timeout_seconds` bounds the two processes together.
`input_fingerprint()` is `joern-<version>`, read from the installation
without starting a JVM.

On train-ticket, Joern builds its graph in 42 s and answers in 3.7 s from
the cache (CodeQL: 207 s, then 6.6 s; `source`: 0.6 s). The three engines
find the same 406 flows and, since ADR-75, the same 1712 edges between
indexed methods.
Seven `ContactsService` calls keep an unresolved signature: Joern reaches
the interface declaration as `possible` and the join's dispatch reaches
the implementation as `possible`, so 11 flows stay `low` (ADR-85). Joern
reports 1753 engine calls against CodeQL's 6661 (measured on 2026-11-07):
calls to Lombok-generated accessors have no source line.

Indexing asks the configured engine (ADR-61) for calls through
`callgraph.engine.CallGraphEngine`; it never imports an engine module. A
source symbol pass then follows receiver-typed calls between indexed Java
methods: with the `source` engine it is the whole call graph, with `codeql`
it fills the gaps. A receiver is typed from a field, parameter, local or
`var` initializer, a declared return type, a field access (`obj.field`,
`Type.CONSTANT`, enum constants), an array element, `Outer.this` from a
type it encloses, or a capitalized type name for a static call. A dotted
name such as `Outer.Inner` or `p.Util` is a static call on that source
type when its first name is no variable in scope and its last is no field
of the type before it, as Java reads names (JLS 6.5.2, BC-22).
`receiver::name` is a call when that name has one declared arity, or when
the target functional interface picks one (BL-24); see the lambda
paragraphs below.
A ternary takes the type its two branches share; branches of different
types leave it untyped (BL-23). A `null` branch takes the other branch's
type, boxed when primitive (JLS 15.25, BL-42). A source enum's implicit
`valueOf(String)` returns the enum and `values()` an array of it (BL-42). A zero-argument `getX()` (`isX()` for a
`boolean`) that no source type declares takes the field type when Lombok
`@Data`, `@Value` or `@Getter` generates it. `toString()`, `hashCode()`
and `equals(x)` return `String`, `int` and `boolean` on any receiver.
Variables declared by an enclosing statement also type receivers: an enhanced
`for` variable, a single-type `catch` parameter, a try-with-resources
resource, and an explicitly typed lambda parameter. A `var` loop variable
takes the element type the iterable's written type argument gives through
the container view below (BL-23), or the element type of an array
(BL-42), and nothing otherwise. A multi-catch
parameter has no type, but still shadows a field of the same name.

A pattern variable takes its written type where JLS 6.3.1 proves it in
scope through `!`, `&&`, `||` and parentheses: the true or false branch of
an `if`, `while` or `?:`, the right operand of `&&` or `||`, the
statements after an `if (!(o instanceof C c))` whose branch ends in
`return`, `throw`, `break` or `continue`, and a `switch` case, guard
included. Record pattern components count; a `var` component has no type.
Elsewhere the name keeps its outer meaning. The Kafka payload pass shares
these rules through `discovery/java/patterns.py`.

An untyped lambda parameter is typed when the lambda is a direct
argument of a JDK callback whose parameter the receiver's written type
arguments fix (BL-14).

The receiver is a variable, a field or `this.field` written as a
`java.util` collection, `Stream`, `Optional` or `Map`, or a chain that
keeps its arguments: `stream()`, `filter`, `sorted`, `findFirst`,
`keySet()`, `values()`. A future's `thenApply`, `thenAccept`,
`thenCompose` and their `Async` forms give its value (BL-37).

The callbacks are `forEach`, `filter`, `map`, `anyMatch` and their kin,
comparators of `sorted`, `min` and `max`, and `Map.forEach`, `compute*`
and `merge`, which give the key or the value. `? extends X` gives `X`.

A lambda passed directly to a source method takes the parameter types of
the single abstract method of the source functional interface declared at
that position (BL-24). With `register(Handler h)` and `interface Handler {
void on(Circle c); }`, `register(c -> c.area())` reaches `Circle.area`.
Type arguments come from the parameter's written type, the receiver and
the generic method as for a return type, then through `extends` clauses.

The interface and its source ancestors must be interfaces with no external
supertype and one abstract method name and arity, `Object` methods,
`default`, `static` and `private` methods aside. Every declaration of the
called name and arity must give the same types. Disagreeing overloads, a
varargs overload, a receiver whose external supertype may declare the
name, an unproven type argument, or any other target (a raw or wildcard
functional type, a raw or wildcard container, a type changed by `map`)
leave the parameter untyped, but it still shadows a field.

A parameter declared with a JDK functional type types the lambda as well
(BL-37): `each(Consumer<Order> c)` makes `each(o -> o.ship())` an
`Order` lambda.

The table below fixes, for each type, the type argument each parameter
of its abstract method takes and the one its result takes. `? extends X`
and `? super X` give `X` (JLS 9.9); `?`, a nested type argument and a
raw type give nothing.

| Type | Abstract method | Parameters | Result |
|---|---|---|---|
| `Function<T, R>` | `R apply(T t)` | `T` | `R` |
| `BiFunction<T, U, R>` | `R apply(T t, U u)` | `T`, `U` | `R` |
| `Predicate<T>` | `boolean test(T t)` | `T` | none |
| `BiPredicate<T, U>` | `boolean test(T t, U u)` | `T`, `U` | none |
| `Consumer<T>` | `void accept(T t)` | `T` | none |
| `BiConsumer<T, U>` | `void accept(T t, U u)` | `T`, `U` | none |
| `Supplier<T>` | `T get()` | none | `T` |
| `UnaryOperator<T>` | `T apply(T t)` | `T` | `T` |
| `BinaryOperator<T>` | `T apply(T t, T u)` | `T`, `T` | `T` |

A lambda that initializes or is assigned to a variable whose type is
written takes the parameter types of that type, JDK or source:
`Predicate<Order> p = o -> o.ok()` types `o`. A compound assignment
and a `var` give nothing.

The result position lets a generic method infer its type variable from
a lambda. With `<R> R apply(Function<Order, R> f)`, `apply(o ->
o.getLine())` is a `Line`: the body expression, or the one type every
`return` of a block body gives, binds `R`. Returns of different types
give nothing.

An implicitly typed lambda constrains no type variable of its
parameters (JLS 15.12.2.2): such a variable takes the type the other
arguments prove, or its bound, as Java infers it.

A method reference whose name has several declared arities binds to the
one its target functional interface fixes (BL-24). The target is a
direct argument position or a variable whose type is written (BL-37).
At an argument position, it is a JDK callback above, such as `forEach`
taking one parameter, or a functional interface a source method declares.

That interface is a source one, as for a lambda, or a JDK one
(`Runnable`, `Callable`, `Supplier`, `Consumer`, `Function`, `Predicate`,
`UnaryOperator`, `BiConsumer`, `BiFunction`, `BiPredicate`,
`BinaryOperator`, `Comparator`).

A type name as receiver may name an unbound instance method, so it
accepts the target's count or one less; when both arities exist, the
reference stays unresolved. So does an overloaded reference whose
target is unknown.

Overloads of the bound arity are told apart by the target's parameter
types (BL-37): `orders.forEach(taker::take)` binds `take(Order)`, not
`take(Line)`. An unbound reference passes all but the first type.
Overloads are chosen as for a call; two that match equally leave the
reference unresolved.

The same view types a call chained on a JDK container accessor (BL-27).
The accessor returns the type argument it names: `get(int)`, `getFirst()`
and `getLast()` of a collection, `next()` of an `Iterator` (also reached
through `iterator()`), `get()`, `orElse`, `orElseGet` and `orElseThrow`
of an `Optional`, so `stream().findFirst().get()` too, `get(key)` of a
`Map`, and `getKey()` and `getValue()` of a written `Map.Entry<K, V>`.
A raw or wildcard container gives no type.

A transform changes the element (BL-40): `map` of a stream or an
`Optional` and a future's `thenApply` take the function's result, and
`flatMap` and `thenCompose` the element of the container of the same
kind it returns. A method reference gives its declared return type when
its name has one declared arity, 0 or 1, on a source receiver type, and
the return type is no type variable. A lambda gives the type of its body
or of its agreeing `return`s (`_lambda_result`, BL-37); for `flatMap`
and `thenCompose` its body must be one expression with a container view.
Anything else leaves the chain untyped.

BL-36 completes the view with the JDK signatures below. Each row quotes
the Java SE declaration the pass reads, and the result it gives.

A future (`Future`, `CompletableFuture`), a `Callable` and the
functional types `Supplier`, `Function`, `BiFunction`, `UnaryOperator`
and `BinaryOperator` are containers too: their written type arguments
type the value they return.

| Owner | Java SE signature | Result |
|---|---|---|
| `Map<K, V>` | `V getOrDefault(Object key, V defaultValue)` | `V` |
| `Map<K, V>` | `V computeIfAbsent(K key, Function<? super K, ? extends V> mappingFunction)` | `V` |
| `Map<K, V>` | `V computeIfPresent(K key, BiFunction<? super K, ? super V, ? extends V> remappingFunction)` | `V` |
| `Map<K, V>` | `V compute(K key, BiFunction<? super K, ? super V, ? extends V> remappingFunction)` | `V` |
| `Map<K, V>` | `V merge(K key, V value, BiFunction<? super V, ? super V, ? extends V> remappingFunction)` | `V` |
| `Map<K, V>` | `V put(K key, V value)` | `V` |
| `Map<K, V>` | `V putIfAbsent(K key, V value)` | `V` |
| `Map<K, V>` | `V remove(Object key)` | `V` |
| `Map<K, V>` | `V replace(K key, V value)` | `V` |
| `Stream<T>` | `List<T> toList()` | `List<T>` |
| `Stream<T>` | `<R, A> R collect(Collector<? super T, A, R> collector)`, with `Collectors.toList()`, `toSet()`, `toUnmodifiableList()` or `toUnmodifiableSet()` | a collection of `T` |
| `Stream<T>` | `Optional<T> reduce(BinaryOperator<T> accumulator)` | `Optional<T>` |
| `Stream<T>` | `T reduce(T identity, BinaryOperator<T> accumulator)` | `T` |
| `CompletableFuture<T>` | `T join()`, `T getNow(T valueIfAbsent)` | `T` |
| `Future<V>` | `V get()`, `V get(long timeout, TimeUnit unit)` | `V` |
| `Callable<V>` | `V call()` | `V` |
| `Supplier<T>` | `T get()` | `T` |
| `Function<T, R>` | `R apply(T t)` | `R` |
| `BiFunction<T, U, R>` | `R apply(T t, U u)` | `R` |
| `UnaryOperator<T>` | `T apply(T t)`, inherited from `Function<T, T>` | `T` |
| `BinaryOperator<T>` | `T apply(T t, T u)`, inherited from `BiFunction<T, T, T>` | `T` |
| `List` | `static <E> List<E> of(E... elements)` and its fixed arities | `List<E>` |
| `Set` | `static <E> Set<E> of(E... elements)` and its fixed arities | `Set<E>` |
| `Map` | `static <K, V> Map<K, V> of(K k1, V v1, ...)` | `Map<K, V>` |
| `Arrays` | `static <T> List<T> asList(T... a)` | `List<T>` |
| `Arrays` | `static <T> Stream<T> stream(T[] array)` | `Stream<T>` |
| `Collections` | `static <T> List<T> singletonList(T o)`, `static <T> Set<T> singleton(T o)` | a collection of `T` |
| `Stream` | `static <T> Stream<T> of(T... values)`, `of(T t)` | `Stream<T>` |
| `Optional` | `static <T> Optional<T> of(T value)`, `ofNullable(T value)` | `Optional<T>` |
| `CompletableFuture` | `static <U> CompletableFuture<U> completedFuture(U value)` | `CompletableFuture<U>` |
| `List`, `Set` | `static <E> List<E> copyOf(Collection<? extends E> coll)`, likewise `Set` | a collection of `E` |
| `Map` | `static <K, V> Map<K, V> copyOf(Map<? extends K, ? extends V> map)` | `Map<K, V>` |
| `Collections` | `static <T> List<T> unmodifiableList(List<? extends T> list)`, and the `unmodifiable*` and `synchronized*` collection and map views | the argument's view |
| `Objects` | `static <T> T requireNonNull(T obj)`, `requireNonNull(T obj, String message)`, `requireNonNull(T obj, Supplier<String> messageSupplier)` | the argument's type |
| `ArrayList`, `HashMap` and the other concrete containers | `ArrayList(Collection<? extends E> c)`, `HashMap(Map<? extends K, ? extends V> m)` | the written arguments, or the argument's view for a diamond |

A static factory is named through its imported or qualified type
(`List.of`, `java.util.List.of`) or a single static import
(`requireNonNull(o)`, `collect(toList())`). A method of that name that
an enclosing type declares or may inherit shadows the import (JLS
6.4.1), and an anonymous class around the call leaves it unresolved.

The factory types its elements from its arguments. Arguments of
different types give nothing: `List.of(circle, square)` stays untyped,
because the pass computes no common supertype. An untyped, `null` or
array argument also gives nothing; a primitive one is boxed.

`new ArrayList<Order>(..)` takes its written type arguments. A diamond
takes the view of its one constructor argument when that view has the
same kind, as `new ArrayList<>(orders)`. Any other diamond, a raw
creation and an anonymous class give nothing.

The view also starts at a call to a source method (BL-35): with
`List<Order> list()`, `catalog.list().get(0)` is an `Order`, and so are
`for (var o : catalog.list())` and `catalog.list().forEach(o -> ..)`.
The declared return type is read through the declarations and type
arguments the source-generics model uses below.

Those declarations are the receiver's own and its source ancestors'
(BC-07). Overload selection, as for a call target, picks among them, and
an override in a subtype hides the declaration it overrides, as Java's
covariant return does. Several declarations of one parameter list left,
as `Box<T> wrap()` on two unrelated interfaces, must give the same view.
Unseparated overloads, disagreeing declarations and a raw return give no
view.

A `var` local takes the view of its initializer, read where it is
written: `var l = catalog.list(); l.get(0)` and `var s =
orders.stream(); s.findFirst().get()` are typed. The guard that ends a
`var` typing cycle (T4) also ends a view cycle.

Nested type arguments (BL-41) change the view's data structure. A view
holds its container kind and one value per type argument. A value is a
type name, or a nested view when the argument is itself a JDK
container whose argument count matches, as `List<Order>` in
`Map<String, List<Order>>`. The nested view's own arguments are type
names: nesting is one level deep, and a deeper container gives nothing.

An accessor whose argument is nested returns that nested view, so
`byName.get(k).get(0)` is an `Order`. `entrySet()` on a `Map<K, V>` view
gives a collection of `Map.Entry<K, V>`. A `var` loop variable takes the
nested element of its iterable, so `for (var e : m.entrySet())` types
`e.getValue()`.

A type variable inside a nested argument is substituted as any other:
with `Map<String, List<T>> groups()` on a `Repo<Circle>`,
`repo.groups().get(k).get(0)` is a `Circle`. Where a type name is read,
for a lambda parameter, a method's type variable or a `var` loop
variable, a nested value gives its container type only.

BL-43 adds a hand-kept table of Spring signatures (ADR-90). It gives
the `source` engine, offline, the library return types CodeQL reads from
the jars it downloads. Each entry names the interface, the member, its
arity, and its return type in terms of the interface's type parameters.

The table holds for Spring Data 2.x and 3.x and Spring Web 5.x and 6.x:
it keeps only entries whose return type is the same in all of them.
`findOne` (Spring Data 1.x) and the same-arity `findAll(Sort)`,
`findAll(Pageable)` and `findAll(Example)` overloads are left out. A
member absent from the table keeps today's behavior: no type, no edge.

| Owner | Spring signature | Result |
|---|---|---|
| `CrudRepository<T, ID>` | `Optional<T> findById(ID id)` | `Optional<T>` |
| `CrudRepository<T, ID>` | `Iterable<T> findAll()`, `Iterable<T> findAllById(Iterable<ID> ids)` | `Iterable<T>` |
| `CrudRepository<T, ID>` | `<S extends T> S save(S entity)` | the argument's type, else `T` |
| `ListCrudRepository<T, ID>` (3.x), `JpaRepository<T, ID>` | `List<T> findAll()`, `List<T> findAllById(Iterable<ID> ids)` | `List<T>` |
| `ListCrudRepository<T, ID>` (3.x), `JpaRepository<T, ID>` | `findById` and `save`, as `CrudRepository` | as `CrudRepository` |
| `Page<T>`, `Slice<T>` | `List<T> getContent()` | `List<T>` |
| `HttpEntity<T>`, `ResponseEntity<T>` | `T getBody()` | `T` |
| `ResponseEntity` | `static <T> ResponseEntity<T> ok(T body)` | `ResponseEntity<T>` |

A repository member applies to a source type that extends the interface,
directly or through source ancestors (`OrderRepo extends
BaseRepo<Order>`, `BaseRepo<E> extends JpaRepository<E, Long>`). Its
type arguments come from the written `extends` and `implements` clauses,
substituted as for source generics (BL-26). A raw interface gives
nothing. A receiver typed by the interface itself is not read.

A source declaration of the same name and arity, on the receiver or a
source ancestor, wins over the table. A repository extending two
interfaces of the table takes the most specific declared return:
`List<T>` over `Iterable<T>` with the same `T`. Returns with different
type arguments leave the call untyped.

`save` returns the argument's type when the pass types the argument, so
`repo.save(special)` is a `Special`; otherwise it returns `T`. `Page`,
`Slice`, `HttpEntity` and `ResponseEntity` are views like the JDK
containers above: a written variable, a source method's return, or
`ResponseEntity.ok(o)` gives their type argument.

BL-44 types a class literal and `getBean` from the same table. `X.class`
is a `Class<X>`, as is a variable written `Class<X>`; a primitive, array
or wildcard class gives nothing. Two Spring signatures read it:

| Owner | Spring signature | Result |
|---|---|---|
| `BeanFactory`, `ApplicationContext` | `<T> T getBean(Class<T> requiredType)` | `T` |
| `BeanFactory`, `ApplicationContext` | `<T> T getBean(String name, Class<T> requiredType)`, with a `String` first argument | `T` |

The receiver must be typed by one of these two interfaces.
`getBean(String)` returns `Object` and stays untyped. ModelMapper and
other mappers are not in the table: `mapper.map(src, Order.class)` stays
untyped, a deviation parity case A34 keeps (ADR-90).

The source-generics model below keeps flat arguments: a type variable
bound to a container, as `T` in `Box<List<Circle>>`, stays untyped.

A type variable in a return or field type takes the argument the sources
write for it (BL-26). The receiver's declared type gives it: a field,
local, parameter, creation, cast or declared return type written
`Box<Circle>`, so `box.get()` is a `Circle`. The `extends` and
`implements` clauses carry it to the ancestor that declares the member:
with `CircleRepo extends Repo<Circle>`, `repo.find()` is a `Circle`, and
so is an inherited field `T value` read in `CircleBox extends Box<Circle>`.

A generic method takes its written type arguments
(`Use.<Circle>same(x)`), or the one type its arguments prove: an argument
passed as `T`, or the type argument of a JDK container or source generic
type passed as `List<T>` or `Box<? extends T>`. Two different types, an
untyped argument, a varargs parameter or any other position leave the
variable untyped; the pass computes no least upper bound.

A type variable with no written argument takes its first bound: in the
generic code itself, a parameter, local or field typed `T` with
`<T extends Shape>` is a `Shape`; a raw `Box` erases `T` to its bound; a
method variable that no parameter names takes its bound. A variable
without a bound stays untyped. `? extends X` gives `X`; `?`,
`? super X` and a nested argument such as `Box<List<Circle>>` give
nothing. An `Object` method on such a receiver ignores the bound and has
no edge (ADR-81 rule 4), even when the bound types an inner part of the
receiver chain.

A concrete body whose parameter is a type variable is indexed for
dispatch under its erased signature (JLS 4.6), the bound or
`java.lang.Object`: `Generic<T> implements Handler<T>` then implements
`Handler.handle`, `exact` when it is the one body under the ADR-81 rules.
A contract keeps its type variable unresolved, so an implementation such
as `handle(String)` still matches it.

A `var` initializer is typed where it is written, not at the call site: a
local declared after it never shadows the field it names, and initializers
that name each other in a cycle get no type.
Names resolve lexically, innermost scope first: the locals and parameters
of every enclosing block, lambda and method, anonymous and local class
methods included, then the fields of each enclosing class before those of
its outer classes. A local declarator is in scope from its own start, so
`Foo a = new Foo(), b = a.n()` reads the local `a`. A try-with-resources
resource is in scope in the later resources and the try body, not in
`catch` or `finally`. `Foo x[]` declares an array (BC-11). A member inner class thus reads its outer class's
fields, and a local class the enclosing method's locals.
An anonymous class's own fields come before those of its source
supertype; an external supertype is assumed to declare none, a known
limit. An enum constant's body is such a class: its own fields come
before the enum's (BC-14). A field hides any field of the same name farther up the hierarchy:
the type is searched first, then its supertypes by distance. Field
accesses and Lombok getters follow the same rule.
A type name resolves where it is written. Its type arguments are erased
by bracket depth, so `Outer<String>.Inner<Order>` names `Outer.Inner`. A
dotted name `Map.Entry` also resolves through a wildcard import of its
package when the JDK nested type is known (BC-16). A type parameter of an
enclosing method or class hides any source type of the same simple name:
the name has no type, in a receiver as in a dispatch signature. A local
class is visible from its declaration to the end of its block and inside
itself, never as a member type of the enclosing class.
Two local classes of one file may share a qualified name, such as a
`class Helper` in two methods. The symbol pass keys each one by its
declaration offset and resolves the name to the one in scope; the index
keeps the shared name, and the declaration line tells the methods apart
(BC-20).
An unqualified call binds to the innermost enclosing type, anonymous classes
included, that declares or inherits the name at any arity; a named inner
class thus reaches its outer type's methods. A scope with an external
supertype stops the search, since that supertype may declare the name,
unless the pass knows every member name of that supertype. A name it
declares still stops the search; any other name goes on outward.

The supertypes with fixed members are the JDK types `Object`, `Runnable`,
`Callable`, `Thread`, `TimerTask`, `Serializable`, `Comparable`,
`AutoCloseable`, `Closeable` and `Cloneable`, the Spring interfaces
`UserDetails`, `GrantedAuthority`, `CommandLineRunner`,
`ApplicationRunner`, `InitializingBean` and `DisposableBean`, and the
Spring adapters `WebMvcConfigurerAdapter` and
`WebSecurityConfigurerAdapter` (BL-25). So `helper()` inside
`new Thread() { public void run() { helper(); } }` reaches the enclosing
class's `helper`, attributed to the creating method (ADR-62), while a
`start()` there stays unresolved since `Thread` declares it. Any other
external supertype keeps the call unresolved.
A name no enclosing type has resolves through a single static import, then
through the one static wildcard import whose source type declares that name
and arity. Several candidates stay unresolved. An enclosing type with an
external supertype whose members the pass does not know may inherit the
name, so the import does not apply and the call stays unresolved (BC-13).
The same holds from an anonymous class body: the import applies once the
search has passed every enclosing type, anonymous ones included, without
meeting the name or a supertype that may declare it (BC-21).
Literals, `new`, casts, and typed variables give argument types. Inside
an anonymous class body, `this` is that instance, typed by the supertype it
creates (BC-10); in a lambda it stays the enclosing class. A numeric
literal takes its suffix: `L` gives `long`, `f` gives `float`, and a
decimal, hexadecimal, octal or binary integer is otherwise `int`.
Same-arity overloads follow Java's phases. The first allows identity,
widening primitive conversion and subtyping; the second adds boxing and
unboxing. The first phase with an applicable overload keeps its most
specific one. Subtyping is known for source types, the external interfaces
they list, `String` and the boxes. A source enum also has the supertypes it
does not write, `Enum`, `Comparable`, `Serializable` and `Constable`, and a
record has `Record` (BC-06).
A phase decides only when every argument and parameter type is known and
no overload's applicability depends on an unknown hierarchy. Otherwise the
pass drops only the overloads a known argument provably cannot reach.
Unknown arguments never narrow. If several overloads remain, the closed
world of the `source` engine keeps the call: each overload's targets are
`possible` edges, and the call-edge diagnostic names the line with the
reason `overload unresolved` (ADR-87). A varargs candidate, a method
reference and the open-world gap filler leave such a call without target.

A varargs method `m(A a, T... t)` competes in the first two phases only at
its own arity, as `m(A, T[])`. The third phase, reached when neither finds
an applicable overload, expands it to the call's arity: `T` for each
trailing argument (BC-08). With `f(int)` and `f(Object...)`, `f("x")` binds
`f(Object...)` and `f(1)` binds `f(int)`.

A known argument decides each phase as above; an unknown one keeps every
overload it cannot exclude, so a call that may reach a varargs method has
no target. A `T...` contract is overridden only by a `T[]` parameter.
When one module declares a type in `src/main` and in `src/test`,
production code resolves to the `src/main` one. The file inventory already
excludes test source sets, so the indexed graph never sees the other.
An override whose parameter type comes from an
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
Both stay `exact` only when the implementor set is complete (ADR-81):
every concrete source class, record or enum below the receiver, in the
modules the caller sees, inherits or declares one target and generates no
member of its own for the call. A record accessor, a Lombok getter,
setter, `equals`, `hashCode` or `toString`, a body inherited from an
external class or from `Object`, and a `@FeignClient` proxy break that
rule. The targets then stay `possible`, and the abstract declaration the
call names joins them, as CodeQL reports it. A subtype body whose
signature stays unresolved (varargs, an external wildcard import) and
whose written parameter types match the contract is a `possible` target;
a `T...` parameter is written `T[]`, so `log(int...)` does not match
`log(String...)` (BC-15).
The check costs one scan of the source types per receiver type, cached.

Lombok names a getter `getX`, or `isX` for a primitive `boolean`, whose
field named `isX` keeps that name; a setter is `setX` (BC-15). `@Getter`
also applies to an enum's fields. `@Accessors` renames the accessors, so a
type that carries it may generate any accessor of the call's arity and
types none.

Several implementations are always `possible`. Whenever no target is
`exact`, the abstract declaration the call names joins the targets as a
declared row, as CodeQL reports it (ADR-70, BL-28). The row is left out
when the declaration is concrete, or when its own dispatch reaches a body
outside the targets, so a rebuild from persisted edges opens no route.
A call with no source body reaches the one abstract declaration no source
type implements, such as a Spring Data query method, as a `possible`
target. Outside ADR-87, the pass drops ambiguous overloads while
retaining an ambiguous receiver type long enough to apply module-scoped
interface dispatch.

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
may, a lambda or method reference implements the interface or a
subinterface (BC-09), no source type implements the interface, or its
signature is not fully resolved.
`super.m()` binds without dispatch to the most specific declaration among
the superclass and its ancestors, after overload selection; the call is
`exact` on a concrete body and reaches nothing otherwise. `X.super.m()`
searches interface `X`, or the superclass of an enclosing class `X`.
The pass matches each indexed method to its declaration by start line,
end line and written signature, so two methods on one line stay distinct
in the symbol table. The join locates a method by name, path and start
line. When two same-name overloads share that line, the row's declared
parameter types choose between them: each side is reduced to simple
erased names (`java.util.List<String>` is `List`, `String...` is
`String[]`). The indexed method has no column, so the types replace it.
CodeQL rows and the source pass's rows both carry the caller's and the
callee's types (BC-23). An edge is still dropped when an indexed
signature is unknown or the types name no difference.

The closed world adds two shapes of the CodeQL query (ADR-70). When the
receiver's declared type is a source interface and the call has one exact
concrete target, the pass also emits a `possible` witness edge to the
abstract interface method the call names. The join stores it as a declared,
non-inferred edge. Its dispatch reaches only the site's exact
implementation, which the exact edge already reaches, so the join adds no
second edge; the witness is a dead end that opens no route and leaves flow
confidence unchanged. A static or un-overridden default interface
method that is itself the target, and an overridden default body, get no
witness; neither does a call whose dispatch is already `possible`.

A local assigned once, at its declaration, with `new T(...)`, and a
`final` field the enclosing class declares with that initializer,
dispatch on `T` when `T` is a source subtype of the declared type (ADR-86,
BL-29). The declaration the call names, and so the witness, stay the
declared type's. Any other assignment in the variable's block or class
body, compound and `++` included, keeps the declared type. So do a
parameter and a non-final, blank or inherited field. The declared type
may also be external (BL-31): `T` must then name it directly, or through
one of its source ancestors.

The narrowed call is `exact` only under ADR-81, checked on `T`: an
override in a subclass of `T` keeps it `possible`. When `T` has no
indexed body for the call, such as a record accessor, the declared
type's dispatch stands. The check walks the variable's block once per
narrowed call site. Method references are not narrowed.

Three rules extend that narrowing by type flow (ADR-89, BL-46). A call
that reaches one source method, `exact` under ADR-81, holds `T` when
every `return` of that method's own body is `new T(..)`. Returns in a
lambda or a nested class body are skipped, and the result is cached per
method. A ternary holds the union of its branches: a branch's created or
returned class, else its static source type, and nothing for `null`.

A parameter of a `private` method holds `T` when every call of that name
and arity in its top-level class passes an argument held as, or typed,
`T`. One walk of the top-level class per parameter, cached, finds the
sites; a method reference to the name, a site outside an indexed method,
or a parameter assigned in the body leaves it unproven. A site that
recurses into the parameter being computed counts as unproven.

The union's call reaches each type's dispatch targets, and is `exact`
only when one target remains and the ADR-81 check holds on every type.
A union containing the declared type does not narrow. The BL-28 declared
row is still left out when its rebuilt dispatch would reach a body
outside the union, as at shape s17.

A receiver of a source generic type whose declaration writes a concrete
type argument excludes the implementors that contradict it (ADR-81
amendment, BL-45). The receiver's arguments are read strictly from a
local, a parameter, a field of the caller's class, a cast, a creation or
a source method's declared return type. An implementor's are read from
its `implements` and `extends` clauses, substituted through its source
supertypes and cached per implementor and ancestor.

A raw clause, a wildcard, a nested parameterized type, a type variable
and a library type give no argument and keep the implementor. Two
concrete arguments contradict when they differ and neither may be a
subtype of the other. `_may_subtype` proves subtyping from the source
ancestors, treats an external ancestor as unknown, and knows the
supertypes of `String` and the boxed primitives. An array relates only
to an array of a related element and to the array supertypes.

The excluded types also leave the ADR-81 completeness check, so the one
remaining body can be `exact`. When every target would be excluded, the
filter does not apply and the call keeps them.

A lambda, a method reference or an anonymous class is a body no indexed
method stands for (ADR-62). Two rules keep it from making a named body
`exact` (ADR-72, BL-30). First, a call stays `possible` when its runtime
type, or a subtype, has an anonymous class whose body declares the name
and arity, or is the target type of a lambda or method reference while
the call names an abstract method.

The target type counts only when the code writes it: the variable a
lambda initializes or is assigned to, a cast, the enclosing method's
return type, or a source parameter type of the name and arity it is
passed to. The last one is read by name and arity, a superset of the
real target. The types are collected once, on the first call that needs
them.

A parameter type also counts through `this(..)`, `super(..)` and an
enum constant's arguments, which name constructors, and through a varargs
parameter, which takes every trailing argument. An array initializer's
element takes the array's element type, and a switch arm or `yield` the
target of its switch expression (BC-12).

Second, a variable that the ADR-86 conditions prove to hold a lambda, a
method reference or an anonymous class reaches no named body of a method
the value implements. Its call keeps only the BL-28 declared row, when
that row's dispatch reaches no named body. A method the value inherits,
such as a `default` body, is its supertype's body, `exact` unless an
external supertype may declare the name.

A receiver typed by an external interface, one that a source type lists in
`implements` or `extends` (and `Thread` as a `Runnable`), dispatches to the
most specific concrete, non-static, non-private body of that name and arity
on each source subtype. The edge is always `possible`, even with one body,
because a library can supply the implementation (ADR-71). A subtype whose
same-arity overloads the arguments cannot separate leaves the call
unresolved. A receiver typed by an external class that a source class
extends dispatches the same way (ADR-74, BL-31). `Object` is never such a
class. A subclass of a library subclass stays out of reach: the class
hierarchy of a library is not in the sources.

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

The restriction holds only while every hop since the entry runs on `this`
(ADR-43 amendment, BL-47). The traversal state carries that bit beside the
method and the `possible` label, so one method can be explored twice. A
call leaves `this` when its receiver is another expression, or when it
has no receiver and its target is outside the caller's class hierarchy,
such as an outer-class method or a static import.

`transient_trigger_endpoints` in `integration_methods.py` builds the
triggers that are never persisted: schedules, startup code and event
listeners (PDR-16). `transient_trigger_labels` reads them from one method
declaration. Each gets an ID `scheduler:`, `startup:` or `event:` plus
module, path and line, which persisted integration methods keep, so
`flows calculate` re-reads only those files. A runner is a method `run`
in a class whose `implements` clause names `CommandLineRunner` or
`ApplicationRunner`.

`JavaSymbols.event_dispatch_calls` pairs each `publishEvent(x)` call with
the event listeners. The receiver must type as `ApplicationEventPublisher`,
`ApplicationContext` or `ConfigurableApplicationContext`, and `x` must have
a static type. A listener receives it when its event type is that type or
one of its source ancestors. `_build_call_graph` adds each pair, within
the caller's visible modules (ADR-71), as an inferred `possible` edge with
`other_receiver` set, so the ADR-43 entry restriction does not reach into
the listener. The pass costs one walk of the call sites of the files that
contain `publishEvent`, times the listener count.

An inherited route sets the entry class itself (ADR-88). The endpoint
snippet carries `systemlens-inherited-method:<path>:<line>`, and
`materialize_integration_methods` gives the route to the one method
declared there. In the join, `trigger_entry` returns that method with the
inheritor's module and the inheritor's qualified name as entry class.
`dispatch_matches_entry` and the rebuild's `entry_dispatch_allowed` read
that class instead of the method's owner.

`other_receiver_sites` in `code_flows.py` reads the receivers through
`JavaSymbols.receiver_sites` once per caller. Each persisted edge records
the result as `other_receiver`; a site the symbols do not know, such as a
placeholder caller or an engine line no source call starts on, keeps an
unknown receiver and the restriction. A rebuild reads the flag and does
not recompute it; an expansion of a persisted abstract edge keeps it.

A rebuild from persisted edges has no source symbols. It approximates the rule
per call site: when a site outside the entry's class fans out to several
overrides of one method name and one is declared by the entry's class, only
that override is followed, while the route stays on `this`. The `analyze`
call-chain diagnostics apply the same rule. No application class name is special-cased. Explicit `super` calls target direct parent bodies.
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

Constructors and initializers are nodes too (ADR-75), built by
`initializer_declarations` in `integration_methods.py`. Each written
constructor is `Owner.<init>` with its parameter types. A record's compact
constructor is its canonical one: it takes the record components as
parameters (BL-39). A record with no written constructor gets no node. A
class or enum without one gets an implicit `Owner.<init>` when its field initializers or
instance initializer blocks make a call; a type gets `Owner.<clinit>` when
its static code does. An interface's field initializers are static code
(BC-14), and so are an enum's constants (BC-18): each one creates its
enum's instance, so `<clinit>` exists when a constant reaches a
constructor node or its arguments make a call. Both implicit nodes span
the type's header, from the
first annotation to the opening brace, where the typed engines locate
them. These nodes own no port, so they never start or end a flow.

The symbol pass attributes a call site to its context: a named method, a
constructor, or an initializer member of a named type. Instance
initializer code belongs to each constructor that does not start with
`this(...)`, or to the implicit constructor; static code belongs to
`<clinit>`. `new T(...)` resolves `T` like a declared type and selects the
constructor by arity and argument types (`exact` when one is left, every
candidate `possible` otherwise). `this(...)` targets the caller's type and
`super(...)` its source superclass. An enum constant without a body
selects its enum's constructor the same way, from `<clinit>`; one with a
body creates an anonymous class and reaches none (ADR-62). A constructor
reference `T::new` takes its arity from the target functional type, or
the only arity `T` declares; it reaches a constructor, `exact`, only when
the proven parameter types leave one (BC-19). A lambda in a
constant's arguments belongs to `<clinit>` too, but the CodeQL join
cannot attribute its body below the header, so only the `source` engine
keeps those calls. A constructor binds statically: it is
neither inherited nor a dispatch target of a subtype. The join creates no
placeholder for an engine's constructor or initializer that has no node,
such as a default constructor.
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
A call site reads the declarations of its receiver's types through two
secondary indexes: by owner, name and arity across modules, and the
declared arities by owner and name. A receiver without a source
declaration, such as a Spring Data repository inherited from an external
interface, therefore costs a lookup per ancestor, not a scan of every
source method. On a generated project of 800 such classes the symbol pass
fell from 10.5 s to 0.8 s.

For a method invocation used as a receiver, the fallback reads source-declared
return types through the receiver hierarchy: the receiver's and its
ancestors' declarations, narrowed by overload selection and override hiding
(BC-07). It requires one parameter signature and one resolved return type,
and bounds nested receiver analysis to 16 calls.
A zero-argument call with no declared method reads a record component of the
same name as its implicit accessor; a declared accessor takes precedence.
Overloads the arguments do not separate give no return type.
These inferred calls retain `possible` dispatch and low flow confidence.
Known positions in a partially resolved generic signature still constrain
candidate implementations. Unknown positions may widen the candidate set, but
they do not erase known parameter types.
Route traversal has the existing bounded `O(I × (V + E))` worst case for `I`
indexed input methods, `V` source methods and `E` resolved call edges.

The source projection prevents `build-mode=none` from invoking Maven or Gradle
for dependency discovery. CodeQL still downloads the jars it guesses from
`import` lines, as described above; an external type it cannot resolve is
accepted as the documented accuracy trade-off.
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

`indexing/source_generation.py` prefers a project's `mvnw` over `mvn` and
reports each outermost Gradle project that no generation root covers.
Every failure, including `OSError` from `Popen` and the timeout, is raised
as `SourceGenerationError`, a `RuntimeError` the CLI prints.

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

The index no longer runs the reachability query described below (ADR-84). Since
ADR-78 a row adds no flow the join does not already find: on train-ticket and
the lab, the flows and edges are identical without it, and the query cost
about 3 s on train-ticket. The query and its rules stay for other callers.

The output-anchored transitive reachability query has source and target
predicates are restricted to the already indexed input/output methods by
relative path and indexed line range: the indexed method starts at its first
annotation while CodeQL locates the declaration line. Module-scoped method
and call queries match the whole relative path against the module prefix,
as CodeQL's `regexpMatch` requires. Exact input-to-output reachability is
persisted with medium confidence; CodeQL-reported possible dispatch is
persisted with low confidence. With `engine_edge_confidence = "exact"`, only `medium`
relations are kept, because they are the routes made of exact edges.

A reachability row becomes a flow only through a representative route of the
indexed call graph that passes the join's rules: the application boundary on
every hop, the entry's dispatch and `engine_max_hops` (ADR-78). The route is
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
For one caller, it re-runs `JavaSymbols.fallback_calls` in a closed world,
limited to that caller, and reads `typing_notes`: the calls left to every
overload (ADR-87) and the JDK accessor calls whose receiver had no container
view. The edge reason is not persisted; this recomputation gives it.
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

When `--engine-database` is supplied to `analyze call-edge`, node presence is
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
`integration_methods` and can be rehydrated in later joins. A method the
engine reports is stored once: it is dropped when an indexed method of the
same name and path holds its line, after `$` in CodeQL's nested type names
becomes `.`. CodeQL locates the declaration while the index starts at its
first annotation, so the range, not the start line, identifies it.

Live progress uses a wall-clock watchdog covering pipe reads;

POSIX timeouts terminate the complete process group, and
subprocesses are reaped on errors. The configured deadline covers the complete
CodeQL pass, not each command independently.
An absent selected engine is reported and keeps AST-only results. A CodeQL
timeout is handled as a partial pass: the index keeps AST facts and the calls
of a call query that completed before the deadline, even when a later query
times out. A query cut by the deadline yields no row, because CodeQL writes
its result file only when the query ends. The index runs all remaining
materializers and commits the partial snapshot; the code-flow signature
stays invalid so a later index retries CodeQL. Non-timeout failures from an available executable remain fatal and
leave the previous successful snapshot intact.

## Call shapes

`tests/test_call_graph_shapes.py` runs one Java fixture per dependency
shape through the symbol pass in a closed world, as the `source` engine
does, and checks each target and its confidence. The table below lists
the shapes; the test fails when a row has no case or a case has no row.
A shape not delivered is a strict expected failure that says why.

Status values: `covered`, `defect` (wrong edge or wrong confidence),
`missing` (no edge), `out of scope` (kept unresolved on purpose).
`scripts/count_call_shapes.py` counts each shape in a Java tree.

| # | Shape | Status | Plan |
|---|---|---|---|
| S1 | Call on a declared receiver: field, local, parameter, `var`, chained return read across the receiver's ancestors; dotted and nested generic type names; a field chain from `this` | `covered` (BC-07, BC-16, BC-17) | |
| S2 | Static call and static import, also from an anonymous class body; a qualified type receiver `Outer.Inner.m()` or `p.Util.m()`; an import an external supertype may shadow stays unresolved | `covered` (BC-13, BC-21, BC-22) | |
| S3 | Unqualified call to an outer type, from an inner or anonymous class, including one of a fixed-member external type (BL-25) | `covered` | |
| S4 | Private method | `covered` | |
| S5 | Overload by argument type: subtype, widening before boxing, literal suffixes, an enum's or record's implicit supertypes | `covered` (BC-06) | |
| S6 | Two methods of one type declared on the same line | `covered` in the symbol pass and the join of both engines (BC-23) | |
| S7 | Constructor call `new X(...)`, `this(...)`, `super(...)`; constructor reference `X::new` | `covered` (ADR-75, BC-19) | |
| S8 | Calls inside a constructor, a record's compact constructor, an initializer or a field initializer, an interface's included; an enum constant's arguments and constructor | `covered` (ADR-75, BL-39, BC-14, BC-18) | |
| S9 | Varargs call with any arity from the fixed parameter count up, tried only when no fixed arity applies | `covered` (BC-08) | |
| S10 | Same-arity overloads the arguments cannot separate: each one a `possible` target; a typed argument still selects one; a method reference stays unresolved | `covered` (ADR-87) | |
| H1 | Inherited method called on a subclass receiver | `covered` | |
| H2 | Override on the declared receiver; virtual dispatch over subclasses; varargs overloads and Lombok-generated overrides | `covered` (BC-15) | |
| H3 | Template method: `step()` or `this.step()` from the base class | `covered` | |
| H4 | `super.m()` declared by the direct parent | `covered` | |
| H5 | `super.m()` declared by a grandparent | `covered` | |
| H6 | `super.m()` with same-arity overloads | `covered` | |
| H7 | Interfaces: chains, abstract intermediate class, inherited body, `default` and `static` methods | `covered` | |
| H8 | Records implementing an interface | `covered` | |
| H9 | Enum constant bodies, whose own fields hide the enum's | `covered` (BC-14) | |
| H10 | External interface implemented in the sources | `covered` (ADR-71) | |
| H11 | External class extended in the sources (`Thread`, servlet filters); created once, unseparable overloads, visible modules | `covered` (BL-31) | |
| H12 | `X.super.m()` on a direct superinterface or an outer class | `covered` | |
| H13 | A superclass method wins over an interface `default` | `covered` | |
| H14 | Abstract declaration as a declared row beside possible implementations; none when its dispatch is wider | `covered` (BL-28) | |
| G1 | Generic interface implemented with a concrete type | `covered` | |
| G2 | Generic superclass extended with a concrete type | `covered` | |
| G3 | Generic implementation `class Generic<T> implements Handler<T>` | `covered` (BL-26) | |
| G4 | Receiver typed by a bounded type variable `<T extends Shape>` | `covered` (BL-26) | |
| G5 | Type-variable return or field substituted from a written type argument; raw, wildcard and nested arguments | `covered` (BL-26) | |
| G6 | Generic method type argument, written or inferred from the arguments | `covered` (BL-26) | |
| G7 | Implementor excluded when its clause's type argument contradicts the receiver's written one; related, raw, wildcard and type-variable arguments keep it | `covered` (BL-45) | |
| T1 | Cast, record accessor, Lombok getter, array element, C-style array declarator | `covered` (BC-11) | |
| T2 | Pattern variable of `instanceof Circle c` | `covered` (BL-23) | |
| T3 | Pattern variable of a `switch` pattern | `covered` (BL-23) | |
| T4 | `var` initializer typed where it is written | `covered` | |
| T5 | Names in anonymous, local and inner classes, innermost scope first; `this` in an anonymous class body; declarator and resource scopes | `covered` (BC-10, BC-11) | |
| T6 | Field hiding: the nearest declaration in the hierarchy | `covered` | |
| T7 | A local class is visible only to the end of its block; same-named local classes of one file stay apart | `covered` (BC-20) | |
| T8 | A type parameter hides a source type of the same simple name | `covered` | |
| T9 | `var` loop variable typed by the iterable's written type argument or an array's element | `covered` (BL-23, BL-42) | |
| T10 | Ternary receiver whose branches share a type, or with one `null` branch | `covered` (BL-23, BL-42) | |
| T11 | `Outer.this` from an inner class | `covered` (BL-23) | |
| T12 | Element of a JDK container accessor chain | `covered` (BL-27) | |
| T13 | Local or `final` field created once as a named class; reassigned and inherited values keep the declared type | `covered` (BL-29) | |
| T14 | Element of a JDK container a source method returns or a `var` holds; agreeing inherited declarations | `covered` (BL-35) | |
| T15 | Implicit enum methods `valueOf(String)` and `values()` | `covered` (BL-42) | |
| T16 | Element from the BL-36 JDK signatures: `Map` accessors, stream results, static factories, creations, futures, functional results; a shadowed static import | `covered` (BL-36) | |
| T17 | Element of a JDK container nested as a type argument, one level deep: written, returned with its type variable substituted, `entrySet()` and `var` loops | `covered` (BL-41) | |
| T18 | Element changed by `map`, `flatMap`, `thenApply` or `thenCompose`: a single-arity method reference's return type, a typed lambda result | `covered` (BL-40) | |
| T19 | Call to a source method whose own returns all create one class; mixed creations, a returned parameter and an overridden method keep the declared type | `covered` (BL-46) | |
| T20 | Ternary of two source types dispatches on their union; a `null` branch gives nothing; a branch of the declared type keeps it | `covered` (BL-46) | |
| T21 | Parameter of a private method whose every call site passes one class; mixed sites, a package method, a method reference or a reassigned parameter keep the declared type | `covered` (BL-46) | |
| F1 | Lambda and method reference as call sites, with explicit type arguments `Type::<T>m` | `covered` (BC-19) | |
| F2 | Lambda or method reference implementing a source interface: written target types, also through `super(..)`, an enum constant, varargs, an array initializer or a switch arm; created once; inherited `default`, also behind a parameter | `covered` (BL-30, BC-09, BC-12) | |
| F3 | Anonymous subclass overriding a source method, created once or elsewhere | `covered` (BL-30) | |
| F4 | JDK callbacks: `Executor`, `Callable`, `Runnable` | `covered` (ADR-71) | |
| F5 | Lambda parameter typed by a source functional interface; method reference overload picked by its target | `covered` (BL-24) | |
| F6 | Lambda typed by a JDK functional type: written variable, source parameter, future callback, inferred result; method reference overload picked by its target's parameter types | `covered` (BL-37) | |
| M1 | External-interface dispatch limited to visible modules | `covered` (ADR-71) | |
| M2 | Source-type dispatch from a library into a module that depends on it | `defect` | not planned |
| M3 | Same qualified type in two modules, one visible | `defect` | not planned |
| M4 | Same qualified type in `src/main` and `src/test` of one module | `covered` | |
| W1 | Injected field typed by an interface | `covered` | |
| W2 | `@Qualifier`, `@Primary`, `@Profile`, `@ConditionalOn*` narrowing | `missing` | not planned |
| W3 | `ApplicationEventPublisher.publishEvent` to `@EventListener` | `missing` | not planned |
| W4 | `@Async` method | `covered` as a plain call | |
| W5 | Entry points: `@Scheduled` | `covered` | |
| W6 | Entry points: `@PostConstruct`, `CommandLineRunner`, `ApplicationRunner` | `covered` as calls; no flow root | not planned |
| W7 | Spring Data derived query | `covered` | |
| W8 | Generated implementation (MapStruct, OpenAPI client) | `covered` | |
| W9 | Spring signature table: Spring Data repository members through a source `extends` clause, `Page`/`Slice` content, `HttpEntity`/`ResponseEntity` body; a member absent from the table, a raw repository, a `findAll` conflict and an untyped `save` argument | `covered` (BL-43) | |
| W10 | `getBean(Class<T>)` typed by a class literal or a written `Class<T>`; `getBean(String)` and ModelMapper stay untyped | `covered` (BL-44) | |
| X1 | Reflection, `ServiceLoader`, `Class.forName` | `out of scope`: a dynamic target is never guessed | |
| X2 | AOP advice and proxies (`@Transactional`, aspects) | `out of scope`: the advised method is already the target | |
| X3 | HTTP and Kafka between services | `out of scope`: flow transitions built by the extractors | |

The rows and their 2026-11-03 frequencies on train-ticket and the lab
came from the call-graph coverage plan, in Git at `4a12ad5`.

### Engine parity

`tests/test_engine_parity.py` measures the `source` engine against
CodeQL on two fixture files that hold every shape below. A case lists the
`(call line, callee, confidence)` rows CodeQL gives from one caller, once
the join has attributed anonymous and lambda callers to their creating
method (ADR-62) and dropped callees that are not indexed methods.

The default test runs the symbol pass in a closed world and expects those
rows. A case whose gap an open backlog item owns is a strict expected
failure that names the item; the delivery of the item turns it into a
pass. A case whose difference a decision record keeps lists the rows the
`source` engine gives instead, and its item column names the case.

A `slow` test, skipped without the CodeQL CLI, reads the raw rows of
`CallGraphSession.calls()` and expects the same rows from CodeQL. A gap
of the join that both engines share, such as a caller missing from the
method inventory, is a strict expected failure there too.

The table uses the status values of the call shapes above; the item
column names the owner of each case not yet aligned, or the item that
aligned it. When several items own a case, all are listed, and the case
passes only once the last one is delivered.

Rows A1 to B6 are the alignment shapes of BL-21 to BL-32. Two-digit rows
(A01 to D06) are the cases of the 2026-11-08 review of internal flows,
under the review's numbering. A missing number is a shape on which the
engines already agree, or D03, which is invalid Java. E02 is the generic
return of the review's Appendix B.4, E03 the dispatch BL-45 filters by
type argument, and E04 and E05 the ternary and call-context narrowing of
BL-46. CodeQL types the A-family library shapes from the jars it fetches
while it builds its database.

Rows E06 on are the shapes of the Copilot backlog's BC items
(`backlog-copilot.md`), in a third fixture file.

| # | Shape | Status | Item |
|---|---|---|---|
| A1 | Type-variable return or field: `box.get()`, `repo.find()` through `extends Repo<Circle>`, inherited `T value`, `<T> T first(List<T>)` | `covered` | BL-26 |
| A2 | Element of a JDK container accessor: `List.get`, `Optional.get`, `Map.get`, `stream().findFirst().get()` | `covered` | BL-27 |
| A3 | `var` loop variable | `covered` | BL-23 |
| A4 | Ternary receiver whose branches share a type | `covered` | BL-23 |
| A5 | Pattern variable of `instanceof` (T2) and of a `switch` (T3) | `covered` | BL-23 |
| A6 | `Outer.this.helper()` from an inner class | `covered` | BL-23 |
| A7 | Receiver typed by a bounded type variable (G4): implementations by BL-26, declared row by BL-28 | `covered` | BL-28 |
| A8 | Unqualified call inside an anonymous `Thread` | `covered` | BL-25 |
| A9 | Lambda passed to a source functional interface parameter | `covered` | BL-24 |
| A10 | Method reference, one arity or an overloaded name | `covered` | BL-24 |
| B1 | Local or `final` field declared `Shape`, created as `Circle` | `covered` | BL-29 |
| B2 | Lambda (F2) or anonymous class (F3) implementing the receiver type | `covered` | BL-30; B2-lambda omits the declared row (ADR-72) |
| B3 | Call on an external class reaching a source subclass (H11) | `covered` | BL-31 |
| B4 | Declared interface row beside two possible implementations | `covered` | BL-28 |
| B5 | Generic implementation of a generic interface (G3) | `covered` | BL-26 |
| B6 | Implementor without a written body: record accessor, Lombok member, body inherited from an external class or `Object`; one complete implementor set stays `exact` (ADR-81 rule 1) | `covered` | BL-32 |
| A01 | `repo.findById(id).orElseThrow().getName()` through `extends JpaRepository<Order, Long>` | `covered` | BL-43 |
| A02 | `repo.findById(id).get().getName()` | `covered` | BL-43 |
| A04 | `repo.findById(id).ifPresent(o -> o.ship())` | `covered` | BL-43 |
| A05 | `repo.findAll().get(0).ship()` | `covered` | BL-43 |
| A06 | `repo.findAll().forEach(o -> o.ship())` | `covered` | BL-43 |
| A07 | `repo.findAll().stream().map(o -> o.getName())` | `covered` | BL-43 |
| A08 | `repo.save(order).ship()` | `covered` | BL-43 |
| A09 | `repo.findAllById(ids).get(0).ship()` | `covered` | BL-43 |
| A10 | Source `Optional<Order> findByName(..)`, then `.orElseThrow().ship()` | `covered` | BL-35 |
| A11 | Source `Page<Order> findAll(Pageable)`, then `.getContent().get(0).ship()` | `covered` | BL-43 |
| A12 | `ResponseEntity<Order> r = client.get(); r.getBody().ship()` | `covered` | BL-43 |
| A13 | `ResponseEntity.ok(order).getBody().ship()` | `covered` | BL-43 |
| A15 | `m.computeIfAbsent(k, x -> new Order()).ship()` | `covered` | BL-36 |
| A16 | `m.getOrDefault(k, other).ship()` | `covered` | BL-36 |
| A18 | `stream().map(Order::getLine).toList().get(0).qty()` | `covered` | BL-40 |
| A19 | `stream().collect(Collectors.toList()).get(0).ship()` | `covered` | BL-36 |
| A24 | `Optional.ofNullable(o).map(Order::getLine).orElse(null).qty()` | `covered` | BL-40 |
| A25 | `Optional.of(order).orElseGet(() -> other).ship()` | `covered` | BL-36 |
| A26 | `CompletableFuture<Order> f; f.join().ship()` | `covered` | BL-36 |
| A27 | `f.thenApply(o -> o.getName())` | `covered` | BL-37 (the future's value types `o`; no transform is needed) |
| A28 | `List.of(a, b).get(0).ship()` | `covered` | BL-36 |
| A29 | `Arrays.asList(a, b).get(0).ship()` | `covered` | BL-36 |
| A30 | `Collections.unmodifiableList(orders).get(0).ship()` | `covered` | BL-36 |
| A31 | `new ArrayList<>(orders).get(0).ship()` | `covered` | BL-36 |
| A32 | `ctx.getBean(OrderService.class).place()` | `covered` | BL-44 |
| A33 | `Objects.requireNonNull(order).ship()` | `covered` | BL-36 |
| A34 | `mapper.map(src, Order.class).ship()` (ModelMapper) | `covered` | BL-44; A34 keeps no row where CodeQL types ModelMapper from its jar (ADR-90) |
| A38 | `var list = repo.findAll(); list.get(0).ship()` | `covered` | BL-43; BL-35 typed the `var` and the source return |
| A39 | `for (var e : m.entrySet()) e.getValue().ship()` | `covered` | BL-41 |
| A41 | `stream().map(o -> o.getLine()).forEach(l -> l.qty())` | `covered` | BL-40 |
| A42 | `client.get().getBody().ship()`, source return `ResponseEntity<Order>` | `covered` | BL-43; BL-35 typed the `var` and the source return |
| A43 | `Map<String, List<Order>> n; n.get(k).get(0).ship()` | `covered` | BL-41 |
| A44 | `var opt = Optional.of(order); opt.get().ship()` | `covered` | BL-36 (`Optional.of`); BL-35 typed the `var` |
| A45 | `Stream.of(a, b).findFirst().get().ship()` | `covered` | BL-36 |
| B10 | `service.shape().area()`, `shape()` returns `new Circle()`: CodeQL `Circle` `exact` | `covered` | BL-46 |
| B14 | `Status.valueOf(s).label()` | `covered` | BL-42 |
| B15 | `Status.values()[0].label()` | `covered` | BL-42 |
| B23 | Call in a record compact constructor, indexed as the canonical constructor | `covered` | BL-39 |
| B26 | `Supplier<Order> s; s.get().ship()` | `covered` | BL-36 |
| B27 | `Function<String, Order> f; f.apply(k).ship()` | `covered` | BL-36 |
| B28 | `BiFunction<String, Integer, Order> bi; bi.apply(..).ship()` | `covered` | BL-36 |
| B29 | `Predicate<Order> p = o -> o.ok()` | `covered` | BL-37 |
| B30 | `Consumer<Order> c = o -> o.ship()` | `covered` | BL-37 |
| B39 | `Shape s = service.shape(); s.area()`: CodeQL `Circle` `exact` | `covered` | BL-46 |
| B44 | `for (var o : array)` over `Order[]` | `covered` | BL-42 |
| B46 | `Function<Order, Line> g = Order::getLine; g.apply(o).qty()` | `covered` | BL-36 |
| C01 | Source `List<Order> list()`, then `.get(0).ship()` | `covered` | BL-35 |
| C02 | Source `Optional<Order> maybe()`, then `.get().ship()` | `covered` | BL-35 |
| C03 | Source `Map<String, Order> index()`, then `.get(k).ship()` | `covered` | BL-35 |
| C04 | Static source `List<Order> all()`, then `.get(0).ship()` | `covered` | BL-35 |
| C05 | `catalog.list().forEach(o -> o.ship())` | `covered` | BL-35 |
| C06 | `for (var o : catalog.list())` | `covered` | BL-35 |
| C07 | `var list = orders; list.get(0).ship()` | `covered` | BL-35 |
| C08 | `var s = orders.stream(); s.findFirst().get().ship()` | `covered` | BL-35 |
| C09 | Lambda passed to a source `each(Consumer<Order>)` | `covered` | BL-37 |
| C10 | Lambda passed to a source `<R> R apply(Function<Order, R>)` | `covered` | BL-37 |
| C11 | `(flag ? order : null).ship()` | `covered` | BL-42 |
| C12 | `catalog.list().stream().filter(..).findFirst().get().ship()` | `covered` | BL-35 |
| C14 | `var l = catalog.list(); l.get(0).ship()` | `covered` | BL-35 |
| D01 | `taker.take(catalog.list().get(0))` with `take(Order)` and `take(Line)` | `covered` | BL-35 |
| D04 | `new ArrayList<Order>(orders).get(0).ship()` | `covered` | BL-36 |
| D05 | `catalog.list().forEach(taker::take)`, overloaded `take` | `covered` | BL-37; BL-35 typed `list()` |
| D06 | `orders.forEach(taker::take)`, overloaded `take` | `covered` | BL-37 |
| D07 | `taker.take(Collections.max(orders, cmp))`: the pass does not type the argument | `covered` | BL-38; D07 keeps both overloads `possible` where CodeQL selects one (ADR-87) |
| E02 | `wrapper.wrap().get().ship()`, `Box<T> wrap()` declared on an interface and on the abstract class between it and the receiver type | `covered` | BL-35 |
| E03 | `Codec<String> codec; codec.encode(..)` with `TextCodec implements Codec<String>` and `BinaryCodec implements Codec<byte[]>` | `covered` | BL-45 |
| E04 | `Printer chosen = flag ? csv : json; chosen.print()` with a third `XmlPrinter` | `covered` | BL-46; E04 omits CodeQL's declared row `Printer.print`, whose rebuilt dispatch reaches `XmlPrinter` (ADR-89) |
| E05 | `printer.print()` in a private `printWith(Printer)` whose one call passes a `CsvPrinter` field | `covered` | BL-46 |
| E06 | `o(Tint.RED)`, `r(new Point(1))` and `c(Tint.RED)` against `Object`, `Enum`, `Record`, `Comparable` and `Runnable` overloads | `covered` | BC-06 |
| E07 | `child.a(1).get()` with `Base07.a(Integer)` returning `Store07` and `Child07.a(String)` returning `Cache07` | `covered` | BC-07 |
| E08 | `f("x")` and `f(1)` with `f(int)` and `f(Object...)` | `covered` | BC-08 |
| E09 | `x.before()` on a parameter that receives a lambda and a named `Hook09Impl`, with a `default` `before` the named class overrides | `covered` | BC-09 |
| E10 | `register(this)` in an anonymous `Thread` and in its creating method, with `register(Runnable)` and `register(Anonymous10)` | `covered` | BC-10 |
| E11 | `take(x)` with `Foo11 x[]`; `Foo11 a = new Foo11(), b = a.n()` beside a field `Bar11 a`; a resource `c` shadowing a field in the try body only | `covered` | BC-11 |
| E12 | `step.run()` in a constructor that `super(() -> {})` and `super(new Step12Impl())` both reach | `covered` | BC-12 |
| E13 | `f.go()` in an enum constant body declaring its own `f`; `Seed13.compute()` in an interface field initializer | `covered` | BC-14 |
| E14 | `log.log("x")` on `Log14.log(String...)` with a subclass `IntLog14.log(int...)` | `covered` | BC-15 |
| E15 | `Map.Entry<String, Order>` under `import java.util.*`; `Outer15<String>.Inner15<Order> i; i.z()` | `covered` | BC-16 |
| E16 | `this.client.api.call()` with `Client16.api` a field of type `Api16` | `covered` | BC-17 |
| E17 | Enum constants `A(Seed17.helper())`, `B(new Part17())`, `C(..) { .. }` with a body and `D` without arguments | `covered` | BC-18 |
| E18 | `map(Foo18::new)`, `Supplier<Foo18> make = Foo18::new` and `Foo18::<String>id` | `covered` | BC-19 |
| E19 | A local `class Helper19` declaring `h()` in each of two methods, each calling its own | `covered` | BC-20 |
| E20 | A statically imported `assist20()` called in an anonymous `Runnable` and an anonymous `Thread` | `covered` | BC-21 |
| E21 | `Outer21.Inner21.create()` and `p.Tools21.run()` | `covered` | BC-22 |

Items BL-21 to BL-32 changed the edges of unchanged sources. The
code-flow signature moved to `code-flow-v51-call-graph-alignment` with
them, so the next `index` of an existing index re-runs the call-graph
stage and rebuilds its edges and flows without `--full`.

BL-35, BL-39 and BL-42 moved it to `code-flow-v52-receiver-typing` for the same
reason, BL-36 and BL-37 to `code-flow-v53-jdk-signatures`, and BL-38,
BL-40 and BL-41 to `code-flow-v54-overloads-and-transforms`, BL-47
to `code-flow-v55-entry-rule-on-this`, and BL-48 to BL-50 to
`code-flow-v56-inherited-routes-and-triggers`. These three items also
moved the endpoint inventory to
`endpoint-inventory-v29-inherited-methods-rabbit-jms`, so the next
`index` re-extracts every file. BL-45 and BL-46 moved the code-flow
signature to `code-flow-v57-dispatch-precision`, and BL-43 and BL-44 to
`code-flow-v58-spring-signatures`. BC-02 moved the endpoint inventory to
`endpoint-inventory-v30-redacted-url-secrets`, so an index that persisted
a URL credential re-extracts every file and drops it. BC-06 to BC-16 moved
the code-flow signature to `code-flow-v59-java-semantics`: they change the
edges, and BC-14 the initializer nodes, of unchanged sources.
BC-17 to BC-23 moved it to `code-flow-v60-lost-edges`: they add edges,
and BC-18 enum `<clinit>` nodes, to unchanged sources.

### Internal-flow shapes

`tests/test_internal_flow_shapes.py` indexes
`tests/fixtures/internal_flow_shapes`, a three-module Maven project with
one entry per flow shape of the 2026-11-08 review. It runs the `source`
engine, then `flows calculate` on the persisted index, and checks the
flows of each entry in both results.

Shapes s3 and s4 dispatch `exact` to `TextHandler`, the one implementor
whose type argument matches the receiver's (BL-45). Shape s19 dispatches
`exact` to `CsvExporter`, the one class the private helper's call site
passes, and shape s17 reaches `CsvExporter` and `JsonExporter`, never
`XmlExporter` (BL-46). An open item's shape would be a strict expected
failure naming it; none is left.

Controls pass today: a controller-service-client chain, a job calling its
own inherited template method, a template method called on a sibling
subtype (BL-47), a `@Scheduled(fixedDelay = ...)` entry, and a CRUD route
two controllers inherit from one base method (BL-48): each route reaches
its own controller's hook only. Rabbit and JMS publications end their
routes' flows (BL-50), and `GET /ping`, which reaches no effect, has no
flow and the diagnostic status `entry_method_without_external_effect`.
Rabbit and JMS listeners, `@EventListener` and `@TransactionalEventListener`
methods, `CommandLineRunner`, `ApplicationRunner` and `@PostConstruct`
start flows, and `POST /events` reaches both listeners of its event
through `publishEvent` (BL-49).
A `slow` test checks both template shapes with CodeQL.

The rebuild reproduces the indexed flows with `source`, and a `slow` test
runs the same check with CodeQL: `PluginNotifier`, in a module
`orders-app` cannot see, stays out of both (BL-51).

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
