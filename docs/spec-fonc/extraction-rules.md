# Core extraction rules

Parent: [Functional specification](../SPEC-FONC.md).


An endpoint has a role (`serve`/`call` for REST, `produce`/`consume` for a topic),
a system, a topic (`METHOD /path` for REST), source location, framework and
optional module, qualified name and Java message type. A value that cannot be
resolved statically is flagged `topic_dynamic=true`; it is never fabricated.
When a Kafka send uses a conditional Java topic expression, extraction keeps
each statically resolved branch as a separate topic endpoint. This covers
ternary expressions, topic variables assigned in `if/else` branches, and
`switch` statements or expressions. The call graph can therefore point to
every statically resolved topic.

With `--strategy strategy1`, the extractor recognizes generated OpenAPI client
calls when an `ApiClient` receiver invokes `invokeAPI` with a literal route and an
`HttpMethod` enum. It retains the exact route, method, source invocation and
uniquely resolved Maven input contract path. A client call may link to a served
operation only through that exact contract path, with one unique target module and
a compatible route. A `.rest` declaration serves the contract its evidence
names. Route similarity alone never identifies a service.

A `@Value` field declared in another Java file is usable only when its name has
one unambiguous property key across the indexed repository; conflicting
declarations stay unresolved. It is never used when the file of the
reference declares a field of that name: that field, or a same-file
constant, decides alone. Test sources declare no such field.

A name declared by the enclosing method, lambda or constructor as a
parameter, local, loop variable, resource or catch parameter is never
resolved by name against a field or another file: it is dynamic unless the
lexical resolver proves its value. A property value that still contains a
`${...}` placeholder after bounded expansion, or a placeholder embedded in
a longer literal that cannot be resolved, is dynamic.

A placeholder nested in another's default, as in `${a:${b}}`, resolves
innermost first, until the text no longer changes. An empty default, as in
`${server.servlet.context-path:}`, is an empty route prefix; it never names
a topic or a channel, which stay dynamic.

A key declared with different values by several profiles, and by no base
document, is dynamic too, even when its placeholder has a default: the
default is the value in none of those profiles. A profile is an
`application-<profile>` file or a YAML document with
`spring.config.activate.on-profile` or `spring.profiles`.

Strategy1 configured API clients may resolve a domain from a `HubRestApi` enum
constant, including statically imported constants and redundant parentheses. If
the factory argument remains unresolved, the scanner may use a unique matching
`XxxApi.class` argument from the same bean method. This fallback is retained as
inferred evidence. Configuration constants alone do not create HTTP call
endpoints. A matched `@Bean` is retained as `configured-api-client-factory`
evidence so dependency analysis can establish the service-level relation without
presenting the factory as a route call. In a `Rest*Config*` class,
`getRest().get("partner")` names the external microservice `partner`.

When the domain or client type identifies a local `model-*` OpenAPI module, its
contract path is retained as additional evidence. Beans returning `WebClient` or
`ExchangeFilterFunction` are excluded from configured API client relations.

Strategy1 also resolves a call such as `client.getRoutings(...)` when the
receiver has a generated `*Api` type, the method name is a unique OpenAPI
`operationId`, and the matching contract is under an indexed OpenAPI directory.
The resulting endpoint keeps the contract path and the inferred domain as
evidence. When the same module and route are also observed inside the generated
`ApiClient.invokeAPI` implementation, the typed adapter operation is retained
as the single endpoint; `invokeAPI` remains the fallback when no operation can
be correlated. If the operation or contract is ambiguous, no route is fabricated.

The Java AST extractor covers Spring MVC/WebFlux, Feign, Spring HTTP interfaces
(`@HttpExchange` with `@GetExchange`/`@PostExchange` and related annotations),
RestTemplate, RestClient, WebClient, Spring Cloud Gateway, Spring Data REST, Spring Kafka and Spring
Cloud Stream. It also reads RabbitMQ and JMS publications through
`RabbitTemplate`, `AmqpTemplate` and `JmsTemplate`. Markdown and JSON Kafka manifests are supported as explicit
sources and are labelled `source=manifest`.

Module discovery records JPA entity classes only when source imports or fully
qualifies the `jakarta.persistence` or `javax.persistence` `Entity` annotation. The service
summary lists their qualified Java names, and the persisted relation set has a `maps`
relation with source evidence. It does not infer a table name, JDBC driver,
database engine, or read/write operation from the annotation.

For Spring Data JPA, indexing resolves a source-declared repository
interface that extends `CrudRepository`, `ListCrudRepository`,
`PagingAndSortingRepository`, `JpaRepository`, or `MongoRepository`, directly
or through another interface of the same module whose type parameters are
substituted. The entity must match exactly one indexed entity; an unknown or
ambiguous entity keeps an unresolved resource with a reason, never a guess.
A receiver is accepted by its declared type as a field, a constructor or
method parameter, or a local variable; a shadowing name wins, and a method
named `save` on another type creates no fact.

CRUD methods classify as read, delete, or create-or-update. A declared
method classifies by the Spring Data grammar: `find`, `read`, `get`,
`query`, `search`, `stream`, `count`, and `exists` are reads, `delete` and
`remove` are deletes. A single `By<Property>` equality with one argument
records that argument as the key expression and the property as its scope.
A literal `@Query` classifies JPQL `select` as read and `update` or `delete`
as such only with `@Modifying`; a native query keeps a JDBC table resource.
A non-literal query, a `Specification`, `Example`, or Querydsl argument, and
a name outside the grammar remain unknown with a reason.

On an `EntityManager` receiver, `persist` is an insert, `merge` is
create-or-update, `remove` is a delete, and `find` or `getReference` is a
read keyed by its second argument. The entity comes from the argument type
or the class literal. `createQuery` with a literal string follows the JPQL
rules; `createNativeQuery` follows the SQL rules; a query string built at
runtime or a named query remains unknown.

On a `JdbcTemplate` or `NamedParameterJdbcTemplate` receiver, `query*` calls
are reads and `update`, `batchUpdate`, and `execute` classify by the first
keyword of a literal single-statement SQL string, which may be a `static
final` constant of the same class. The resource `jdbc:<module>:<table>`
is recorded only when the statement names exactly one table; a single
`column = ?` predicate binds the matching positional argument as the key.
Joins, subqueries, several `FROM` tables, stored procedures, multiple
statements, and concatenated or formatted strings remain unknown with a
reason. `analyze coverage` lists each technology with its supported,
partial, and unsupported counts and every unresolved operation.

A reachable HTTP or Kafka entry has a separate potential path to each
operation, including one whose action or resource is unknown; an entity
name is not a table name.

MongoDB calls on a field imported or fully qualified as a Spring Data Mongo
template or operations interface, or on a resolved Mongo repository field,
retain their raw API
operation. Indexed flows classify supported calls as read, insert,
update, delete, create-or-update, or read-and-update attempts. Deletes and
multi-item inserts are writes. `save` and `upsert` remain create-or-update;

`findAndModify` and `findAndReplace` have both read and write facets. A
`bulkOps` builder, `getCollection`, and `watch` do not create read or write
effects. A field whose name only resembles a Mongo receiver does not create a
MongoDB fact. A field on another object is not treated as a field of the
containing class. Persisted data steps retain the action. HTML export uses
them as potential annotations in microservice tooltips of selected call
graphs, while its flow catalogue lists call flows only.
Unknown collection names remain unresolved.

Every operation records its transaction scope when the enclosing method or
class carries `@Transactional`, or when it runs inside a
`TransactionTemplate.execute` lambda: a stable scope ID, the propagation,
the isolation when stated, and the read-only flag. A helper called from a
transactional method inherits nothing, because propagation is a runtime
decision, and the default isolation stays unknown. `@Transactional` is
scope evidence only; it is never recorded as a guard.

A guard is recorded only on explicit evidence: `optimistic_version` on
every write to an entity with a `@Version` field, `pessimistic_lock` on a
repository method declared with `@Lock(PESSIMISTIC_READ|WRITE)`,
`atomic_update` on a Mongo `findAndModify`, `upsert`, or an inline update
using `$inc` alone or `$set` with a version predicate, and
`conditional_update` on a JDBC `UPDATE` whose `WHERE` names a version
column. Anything else is absent evidence, never "unprotected".

When a write's first argument is the unmodified result of a read in the
same method, or a local initialised from it and never reassigned, the
write records that read as a value dependency and inherits its key. Setter
calls on the local are the "modify" step and keep the dependency.

When the persisted Java call graph uniquely identifies the method containing
a MongoDB call, each reachable HTTP or Kafka entry also gets a potential flow
ending at that call. The flow includes the call sites and keeps uncertain
dispatch at low confidence. These data flows are separate from paths ending
at an HTTP call or Kafka publication; an operation on a sibling branch is not
attached to that output path. An ambiguous owner method has no data flow.

An internal method path stops before a different module that starts its own
application. It may continue into a module without an application entry point.
Calls between applications require indexed HTTP or Kafka endpoints to connect
their separate internal flows.

The same module inventory records conventional Java DTOs associated with JPA
entities through imported, same-package or qualified entity references. If the
module has no OpenAPI file, it also records DTO types used in REST controller
method parameters or return types. The index preserves source locations and
does not infer a mapper or a runtime serialization path.

The Flux call view lists data access sites reachable from the selected entry
through persisted endpoint topology. Paths with the same exact entry endpoint
may contribute separate annotations. This is a set of possible paths, not a
linear execution trace. An asynchronous write is a database write in a Kafka
consumer handler, reached after a message publication. A synchronous read is a
database read on an HTTP entry path. Reads in Kafka consumers and writes on
other trigger types have no async/sync branch classification. Consistency
diagnostics pair only classified async writes and sync reads; missing keys or
query shape leave a candidate pair as insufficient evidence.

For REST clients, a literal URL or a unique, never-reassigned local string base
URL is normalized to its route and retains its HTTP host as target evidence. A
private, uniquely named helper with a single unconditional String return and
literal arguments may be evaluated transitively under the same rules. Spring HTTP
interfaces retain `@ClientRegistrationId` as explicit service-target evidence when
present.

A Feign client's route prefix is the path of its `url` joined with its
`path`. A `url` that is one unresolved placeholder is taken as a base URL
and adds nothing, so a literal `path` still gives the route; an unresolved
part inside the url's path makes the route dynamic.

A Feign client without `url` names its peer by its `name` or `value`
attribute: a literal, a constant or a resolvable property placeholder that
is one internal-shaped host name, such as `employee-service`. It reaches the
service whose module or `spring.application.name` equals that name. With
`url`, only the URL host is target evidence and the name is ignored. An
unresolved, dotted or unknown name leaves the call unresolved.

A mapping declared on an interface or a base class belongs to the type that
inherits it. A concrete `@RestController` or `@Controller` serves it, with
its own type-level `@RequestMapping` or, without one, the first inherited
one. A `@FeignClient` interface extending an API interface calls it, after
its route prefix and the API interface's type-level mapping. The
inheritor's own mapping for the same method wins. The declaring type keeps
its mappings only when no inheritor is indexed, so a route is never served
twice. A route the inheritor does not override sits on its type
declaration, and the inherited method owns it: its flows start there and
belong to the inheritor's module. Dispatch from the inherited body keeps
to the inheritor while the route stays on `this`, so a sibling
inheritor's override never enters the flow (ADR-88).

Spring application names are read from multi-document YAML files;
profile-specific values do not override the base document without an explicit
active-profile selection.

A RestClient call uses the base URL of its own client only: a `baseUrl` or
`RestClient.create` in its own call chain, else the one assigned to the
variable or field the chain starts from. A local variable wins over a field
of the same name. The base host is target evidence and the base path
prefixes a relative URI. An absolute URI, or one whose leading part is
unresolved, ignores the base. A client bound to several bases has none.

A mutable or otherwise unresolved URL remains a dynamic, unresolved port rather
than a guessed service link. A RestTemplate `exchange` call whose HTTP method
is computed, and a fluent `method(HttpMethod.X)` call on WebClient or
RestClient, keep the route; the verb is the enum constant or `ANY`. A mapping
annotation whose array value holds one literal uses that literal; several
candidate routes remain dynamic. A text block literal is its trimmed content.
Spring HTTP interfaces read `url`, `path` and `value`; method-level exchange
annotations without a type-level `@HttpExchange` declare routes with an empty
prefix. A Spring Data REST repository without `@RepositoryRestResource` is
served at the pluralized, uncapitalized entity name (`OrderItem` at
`/orderItems`). That route and its search resources are dynamic: Spring's
inflector knows irregular plurals (`Child` at `/children`) that SystemLens
does not, so the regular spelling is shown as unverified (PDR-12). A Spring Cloud Gateway Java route is recognised with or
without a route identifier; without a `method(...)` predicate it serves and
proxies every verb as `ANY`, like a YAML route. Each route of a builder chain
keeps its own `uri(...)` target and the line of its own `route` call; the
first target URI of its evidence wins, so a later `http://` URL in a filter
does not override `lb://`.

A gateway route serves its public `Path` pattern and calls the path its
filters forward. `StripPrefix`, `RewritePath`, `PrefixPath` and `SetPath`
apply in order, after the `default-filters`, in the shortcut form, the
long `name`/`args` form and the Java DSL methods. A rewrite applies only
when its result does not depend on what a `**`, `*` or `{var}` segment of
the pattern matches; otherwise the call is `<dynamic>`. YAML routes with an
`lb://`, `http://` or `https://` `uri` are kept, each on the line of its
own `uri`, quoted or not.

Indexing materializes AST method facts that associate each Java method with its
HTTP/message entry endpoints and HTTP/message output endpoints. It then
materializes conservative same-method code flows and, with an external
engine such as `codeql`, creates a global source-only Java database to follow
resolved method calls from an indexed entry method to an indexed output
method. The rules below that name CodeQL describe that engine's rows.

When no external engine runs or it is unavailable, a local Java symbol pass may also
connect an entry to an output through a uniquely resolved receiver type and
method signature. A flow through such calls is `potential`: its confidence
is medium when every call is `exact`, and low when one is `possible`
(ADR-61). Ambiguous receivers and overloads remain unresolved.
An inherited `final` method can be a possible target when its source body is
unique, even if a generic parameter type cannot be resolved. This covers a
Kafka handler delegating through a final generic method on its base class.

When a Java method consumes multiple indexed Kafka topics and contains a
`join` invocation, its same-method flow explanation states that the
publication depends on a join. Each input remains a potential path; the graph
does not claim that one message alone triggers the output.

CodeQL can also answer transitive reachability, from indexed output methods
back to indexed input methods. Since ADR-78 such a pair becomes a flow only
through a route the Python-side traversal already checks, so it adds no
flow, and the index no longer runs that query (ADR-84).

Only source-located CodeQL call pairs are materialized. The internal-flow join
keeps source-located intermediate methods even when they have no IN or OUT
integration port; ports only anchor the beginning and end of a flow. CodeQL's
own possible virtual-dispatch rows remain visible as low-confidence potential
flows. When CodeQL returns only a source-backed interface method, the Python
materializer expands it to every source-backed compatible implementation and
marks each added edge as inferred and possible; it preserves all candidates
instead of choosing one runtime target.

When CodeQL also resolves a concrete virtual target, a source-backed interface
declaration is retained as an additional possible witness so the caller-to-
contract edge remains visible in the persisted call-edge listing. At a call
site where CodeQL resolved the exact implementation, the witness adds no
edge beside the exact one, and the source symbol fallback adds no other
candidate there: the witness opens no route (ADR-70). When the index knows
module dependencies, an edge of a typed engine (`codeql`, `joern`) never
joins two modules unrelated by a build dependency (ADR-71).
Calls are aggregated before the global flow join. `--engine-database DIR`
reuses an existing global database of the selected engine instead.

The automatic database is cached in `.systemlens/codeql/` and reused while
the projected Java sources and the CodeQL executable are unchanged; a change
creates it again (ADR-84). Neither its path nor the supplied database path
is recorded in the index. If
the selected engine is unavailable, indexing reports it, uses `source`,
and retains same-method and source-symbol flows. A uniquely resolved dispatch
yields a `potential` flow with medium confidence. When CodeQL identifies several compatible virtual
method implementations, SystemLens retains each candidate as a `potential`
flow with low confidence. Calls without an exact caller and callee source
location remain unresolved unless the extractor cannot prove a usable path and
exactly one indexed method has the qualified name. That fallback is marked
`low` with signature-join provenance; it does not apply to ambiguous names.

Source-declared abstract/interface dispatch may bridge to source-backed concrete
implementations and receiver-based helper calls may be added, always as
`possible`/`low`; multiple candidates remain separate and explicitly ambiguous.
Explicit imports and qualified parameter types can identify a signature without
dependency sources. Interface implementations can use inherited public instance
methods, provided a more specific declaration does not hide the inherited body.

Calls such as `getPort().send()` use the getter's declared return type when its
name and arity identify one parameter signature and one resolved return type.
Unknown return types and ambiguous overloads remain unresolved; the fallback
does not evaluate getter bodies or select runtime beans.
When a caller module has compatible implementations of the same interface method,
the candidates from that caller module are preferred. If the caller module has no
compatible implementation, candidates from other modules remain possible.

The static base type remains a possible target when a subtype override is also
visible. An inherited template call is restricted to the concrete entry subtype
when that subtype is known; an entry declared by the base class itself keeps
every override. The restriction holds only while the route calls methods on
`this`: once it calls another object, such as `secondStep.execute()`, every
override stays possible (ADR-43). `super.method()` resolves to the direct parent
declaration. These rules prevent sibling implementations from entering one
source flow while preserving uncertainty for a base-typed field.

Inheritance relations are kept distinct when the same qualified interface or
class name is declared in different modules. An ambiguous receiver type is
retained long enough to collect compatible contracts, then implementations
from the caller's module are preferred; if no preference is possible, the
alternatives remain explicit instead of being merged or guessed.

CodeQL contributes only source-located resolved callees; recovered receiver
types and `methodFullName` values without source evidence are diagnostics, not
architecture edges. The inventory is static, so reflection, arbitrary string
construction, runtime routing and undeclared external contracts can remain
unresolved. Consumers should interpret facts using dynamic status, confidence
and source evidence.

CodeQL call rows are first merged into a directed method graph. Weak connected
components group methods linked by source-evidenced calls; they do not reverse
call direction or create routes. Flow reconstruction traverses the directed
edges from indexed input methods to indexed output methods within the relevant
component. AST-only and CodeQL observations of the same endpoint flow are then
deduplicated to one representative.

Kafka producer and consumer endpoints remain separate architecture facts. A
Kafka topic match contributes a topology edge, while the CodeQL call graph
connects internal Java methods. Indexing does not compose Kafka endpoints into
new multi-service code-flow candidates. Known message types must match when
both sides provide one; an unknown type remains compatible and lowers the
confidence of the topology fact. A publication without an input trigger does
not become a code-flow root.

For Strategy1 producer conventions, the `codeql` and `joern` engines may
complete a missing payload type from the second argument of
`envoyerMessageKafka*`, including local data flow within the enclosing
method; the `source` engine adds no type beyond the AST rule. Without such
an engine, a call to a unique helper of the
enclosing class gives its declared return type. The second argument remains authoritative when a
third argument is present. Simple `Message<T>`, `GenericMessage<T>` and
`ProducerRecord<K,V>` wrappers are unwrapped. It never replaces an AST type
and applies only a unique source-backed result; ambiguous results remain
unknown.

Strategy1 resolves listener and sender topic keys against `kafka.yml` or
`kafka.yaml` after normalizing case, CamelCase boundaries, digits, and
separators. The supported declaration is the nested `topics.<key>.nom` leaf.
It does not infer a match from a partial key or an undeclared topic; a
literal send topic is validated by its full normalized key or its declared
value, never by its last segment.
Only the `topics` attribute of `@KafkaListener` is inspected for topic
references; a kebab-case key such as `commande-creee` matches
`COMMANDE_CREEE`. Properties such as `topics.<key>.groupe` in `groupId` are
consumer-group settings, not topic references, and are ignored.

Strategy1 reads Java code, not text. A commented-out `@KafkaListener` is not
a consumer, and a listener's payload type is its `@Payload` or first payload
parameter. A `getTopics().getXxx()` accessor makes a producer only as the
topic of a send call, a `ProducerRecord` or a message `TOPIC` header; a
`NewTopic` bean, a log line or a commented-out send publishes nothing.

If the declared value starts with `${kafka.prefix-topic}.`, Strategy1 removes
that environment prefix before persisting and matching the physical topic.
An absent, ambiguous or non-matching declaration remains dynamic and does not
receive a derived name. A conditional topic expression creates one producer
fact for each statically resolved branch when each branch matches a catalog
entry after key normalization. The same branch resolver handles direct
expressions and variables assigned by `if/else` or `switch` control flow.

Kafka topic expressions also resolve source-local conventionally named uppercase
`static final` string constants and concatenations whose operands are all
statically resolvable.
When one operand remains unresolved, the complete expression stays dynamic;
SystemLens does not keep its first literal as a concrete topic. A method call
or lambda computing the topic is dynamic, except `List.of`, `Set.of`,
`Arrays.asList`, `Collections.singletonList` and `Collections.singleton`,
which yield one fact per listed topic, and `Pattern.compile`, which keeps
its literal as a dynamic pattern. A literal with an embedded or nested
placeholder, or a SpEL expression that is not a plain property reference, is
dynamic with its text as evidence. Comments inside an argument list or an
array are not arguments. `@KafkaListener` is repeatable, and a class-level
listener with `@KafkaHandler` methods is a consumer whose payload type is
known only with one handler. Each `@KafkaHandler` method receives that
consumer and starts its flows. A `@Payload` parameter is the listener payload
wherever it sits.

An uppercase `Flux` enum constant is resolved only when its normalized name
has a matching catalog entry. Other variable topic expressions stay dynamic.
When the publishing method is annotated with `@Scheduled`, the flow starts
with an explicit `Déclencheur Cron` step, followed by the Kafka publication
owned by that method. Every schedule starts a flow. The step is named after
the first of the `cron`, `fixedRate` and `fixedDelay` arguments, as
`@Scheduled(cron=0 * * * * *)`, `@Scheduled(fixedRate=5000)` or
`@Scheduled(fixedDelay=5000)`, and `@Scheduled` without any of them. The
schedule is retained as source evidence.

A flow goes from a trigger to an effect (PDR-15). An effect is an HTTP
call, a message publication or a data operation. A route whose calls
reach none of them is not a flow, however deep its call tree; `analyze
flows-diagnostic` reports it as `entry_method_without_external_effect`.
Calls to Redis, Elasticsearch, R2DBC, S3 or SQS are not effects.

A RabbitMQ or JMS publication is an effect with a `Publication message`
step. The channel is resolved as a Kafka topic is; an unresolved one is
dynamic. A Rabbit publication on the default exchange is named by its
routing key, the queue name. One to a named exchange is named
`exchange/routing-key` and pairs with no queue, since exchange bindings
are not read. A JMS publication is named by its destination.

A flow starts at a trigger (PDR-16). Besides REST routes, Kafka
consumers and `@Scheduled` methods, the triggers are:

- `@RabbitListener` and `@JmsListener` methods, consume endpoints named by
  their `queues` or `destination` value, resolved as a Kafka topic is. A
  class-level `@RabbitListener` gives its endpoint to each `@RabbitHandler`
  method. A Rabbit or JMS consumer pairs with a publication of the same
  system and channel name in another module, as a `publishes_to`
  relation; a dynamic name never pairs.
- `CommandLineRunner.run`, `ApplicationRunner.run` and `@PostConstruct`
  methods, with a `Déclencheur de démarrage` step named after them.
- `@EventListener` and `@TransactionalEventListener` methods, with an
  `Écouteur d'événement` step named after the event types, such as
  `@EventListener(OrderCreatedEvent)`.

Startup and event triggers, like schedules, add no topology endpoint.
An event stays in-process: `publishEvent(x)` on an
`ApplicationEventPublisher` or an `ApplicationContext` is a `possible`
call to each listener whose event type is the static type of `x` or one
of its ancestors. The event type is the `classes` or `value` attribute,
else the single parameter. The publisher's flow continues through the
listener, with low confidence; an untyped `x` gives no call.

A publication in a `@TransactionalEventListener` method gets the
`after_commit` phase only when the listener runs after a commit (PDR-17).
Its `phase` attribute, `AFTER_COMMIT` when absent, must be `AFTER_COMMIT`
or `AFTER_COMPLETION`. `BEFORE_COMMIT`, `AFTER_ROLLBACK` and an unreadable
phase, such as a constant, leave the publication unphased. The phase never
changes the listener's trigger or hop.

`main`, `@SqsListener`, Spring Cloud Stream functional beans,
`RouterFunction` handlers, `@MessageMapping`, gRPC services, Spring Batch
and Quartz jobs start no flow. The HTML architecture graph draws Kafka
topics only: a Rabbit or JMS relation is in the persisted topology and
the CLI, and its steps appear in the flows.

The architecture graph remains conservative when message payload typing is
missing or contradictory: a producer/consumer service edge requires the same
concrete topic, while two known and different Java message types prevent the
edge. Missing type evidence does not prevent the edge and is shown as a partial
or unknown type status with medium confidence.

A payload type is known only when it names a DTO. `Object`, `String`,
`byte[]`, wildcards, raw containers such as `Message` or `List`, and a type
variable declared by the enclosing method or class are unknown on both the
producer and the consumer side: a `String` payload is serialized, so its
static type says nothing about the DTO. A consumer typed `String` is
therefore unknown too, and it pairs with any producer on the topic.

Generic carriers are unknown too, whatever their package or generic
arguments (ADR-79): JSON trees (`JsonNode`, `ObjectNode`, `ArrayNode`,
`JsonElement`, `JsonObject`, `JSONObject`), Avro records (`GenericRecord`,
`GenericContainer`, `IndexedRecord`, `SpecificRecord`,
`SpecificRecordBase`), Protobuf base messages (`AbstractMessage`,
`DynamicMessage`, `GeneratedMessage`, `GeneratedMessageV3`), `Bytes`,
`ByteBuffer`, `Serializable`, `Properties` and every `Map` type, raw or
parameterised. A generated Avro or Protobuf class keeps its own name.

Two types are compared without package qualifiers, so `p.OrderEvent` and
`OrderEvent` match. Generic arguments stay part of the type: a
`List<OrderEvent>` sent as one message is the real payload and differs from
`OrderEvent`. The stored type keeps its source spelling. Two different DTO
names still prevent the edge.

A producer value is typed from its declaration: a parameter, a local
variable (also `var` with a typed initializer), a field of the class or a
source superclass, `new`, a cast, a ternary whose branches agree, a lambda
or for-each variable over a typed collection, a call with one source
declaration, a getter, a Lombok builder, or `MessageBuilder.withPayload`.
When the value stays unknown, the value type `V` of the receiving
`KafkaTemplate<K, V>` is used, unless `V` itself names no DTO.

A variable is typed in its own scope, never from an outer field of the
same name: an `instanceof` or `switch` pattern variable, a
try-with-resources resource and an anonymous-class field each give their
declared type. When a pattern variable of that name exists but its scope
cannot be proven to cover the send, the value is unknown.

`KafkaTemplate.sendDefault` publishes to the template's default topic: a
`setDefaultTopic("...")` on that template in the same file, else the
`spring.kafka.template.default-topic` property, else a dynamic topic. Its
first argument is a key or a partition, never the topic; its last argument
is the payload, and the one-argument `sendDefault(value)` is a producer too.

A Kafka Streams source or sink is typed from its `KStream<K, V>` or
`KTable<K, V>` declaration, explicit type arguments, or the value serde of
a `Consumed` or `Produced` argument when they are statically known. The
return type of the enclosing method, such as a `Topology` bean, is never the
payload; without written evidence the payload is unknown.

A `new ProducerRecord(...)` takes its written `ProducerRecord<K, V>` type,
else the type of its value argument chosen by constructor arity. Its
headers, timestamp, partition and key arguments are never the payload;
when a five-argument record does not show whether it carries headers or a
timestamp, the payload is unknown.

The export keeps unmatched concrete endpoints and dynamic topic expressions
as partial, explicitly unresolved topic evidence; dynamic topics never imply
a producer/consumer pairing. Different known producer and consumer types are
displayed as warnings on the topic, relation, or integration port.

The `flows-diagnostic` analysis may still report a possible composition gap
for a concrete topic with a downstream consumer when payload types are absent.
This diagnostic hint does not create an asserted architecture edge or relax
the export's conservative graph contract.

A REST call forms an internal architecture relation only when its target
service is identified by an exact normalized explicit alias, such as an HTTP
host, an `lb://` service name, a configured client domain, or an HTTP host
resolved from a local field annotated `@Value("${…}")`. A matching HTTP
method and route only refines a resource within that already identified
service; it never identifies a service by itself. Calls without a unique target
remain indexed as unresolved evidence and are reported by coverage and indexing
issues rather than being linked to a coincidentally similar route.

Only an internal-shaped host names a service: a single DNS label
(`inventory-service`) or a Kubernetes cluster-local name ending in `.svc` or
`.svc.cluster.local`, which names its first label. An IP address is never a
service. Another multi-label host, such as `auth.okta.com`, resolves only
through an exact configured alias; otherwise the call stays unresolved.

Inside the target service, route matching follows Spring MVC (ADR-77). A
literal segment wins over a template, so `GET /items/count` reaches
`/items/count` and not `/items/{id}`. A `{id:\d+}` constraint rejects a
literal it does not match. A templated call such as `/items/{path}` keeps the
route whose templates align with its own; without one, every candidate stays.

A RestTemplate, WebClient, RestClient, HTTP-interface or Feign call whose route
matches no served route keeps one relation to the service, without a
resource. It is never projected onto every route of the target; only a
configured client factory without a route at its call site is.

Each unresolved dynamic endpoint contributes an informational indexing diagnostic
grouped by source path and extractor. The diagnostic records the endpoint role,
source line and a bounded reason such as `unresolved-expression` or
`topic-pattern`; it does not persist the source expression or its value.
