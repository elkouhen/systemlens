# Core extraction rules

Parent: [Functional specification](../SPEC-FONC.md).


An endpoint has a role (`serve`/`call` for REST, `produce`/`consume` for a topic),
a system, a topic (`METHOD /path` for REST), source location, framework and
optional module, qualified name and Java message type. A value that cannot be
resolved statically is flagged `topic_dynamic=true`; it is never fabricated.

The Java AST extractor covers Spring MVC/WebFlux, Feign, Spring HTTP interfaces
(`@HttpExchange` with `@GetExchange`/`@PostExchange` and related annotations),
RestTemplate, WebClient, Spring Cloud Gateway, Spring Data REST, Spring Kafka and Spring
Cloud Stream. Markdown and JSON Kafka manifests are supported as explicit
sources and are labelled `source=manifest`.

For REST clients, a literal URL or a unique, never-reassigned local string
base URL is normalized to its route and retains its HTTP host as target
evidence. A private, uniquely named helper with a single unconditional String
return and literal arguments may be evaluated transitively under the same
rules. Spring HTTP interfaces retain `@ClientRegistrationId` as explicit
service-target evidence when present. Spring application names are read from multi-document YAML files;
profile-specific values do not override the base document without an explicit
active-profile selection. A mutable or otherwise unresolved URL remains a
dynamic, unresolved port rather than a guessed service link.

Indexing materializes AST method facts that associate each Java method with its
HTTP/message entry endpoints and HTTP/message output endpoints. It then
materializes conservative same-method code flows and, when CodeQL is
available, creates a global source-only Java database to follow resolved
static method calls from an indexed entry method to an indexed output method.
CodeQL additionally answers transitive reachability by starting at indexed
output methods and walking callers until indexed input methods are reached;
these direct answers are used when the Python-side traversal cannot reconstruct
the intermediate calls. The configured timeout remains the operational guard.
Only source-located CodeQL call pairs are materialized. CodeQL's own possible
virtual-dispatch rows remain visible as low-confidence potential flows; the
Python materializer does not create additional targets.
Calls are aggregated before the global flow join. `--codeql-database DIR`
reuses an existing global CodeQL database instead.
The temporary database and the supplied database path are never persisted. If
the selected engine is unavailable, indexing reports that interprocedural flows were skipped
and retains AST-only flows. A uniquely resolved dispatch yields a `potential`
flow with medium confidence. When CodeQL identifies several compatible virtual
method implementations, SystemLens retains each candidate as a `potential`
flow with low confidence. Calls without an exact caller and callee source
location remain unresolved unless the extractor cannot prove a usable path and
exactly one indexed method has the qualified name. That fallback is marked
`low` with signature-join provenance; it does not apply to ambiguous names.
Source-declared abstract/interface dispatch may bridge to one unique concrete
implementation and receiver-based helper calls may be added, always as
`possible`/`low`; ambiguous candidates remain unresolved.
CodeQL contributes only source-located resolved callees; recovered receiver
types and `methodFullName` values without source evidence are diagnostics, not
architecture edges. Reflection, dynamic routing, and runtime-only routing are
not added.

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
For Strategy1 producer conventions, CodeQL may complete a missing payload type
from the second argument of `envoyerMessageKafka*`, including local data-flow
within the enclosing method. Simple `Message<T>`, `GenericMessage<T>` and
`ProducerRecord<K,V>` wrappers are unwrapped. It never replaces an AST type
and applies only a unique source-backed result; ambiguous results remain
unknown.
When the publishing method is annotated with `@Scheduled(cron = "...")`, the
flow starts with an explicit `Déclencheur Cron` step, followed by the Kafka
publication owned by that method. The cron expression is retained as source
evidence; methods scheduled by a fixed delay/rate without a cron expression
remain outside this trigger classification.
The architecture graph remains conservative when message payload typing is
missing or contradictory: a producer/consumer service arc requires the same
concrete topic, while two known and different Java message types prevent the
arc. Missing type evidence does not prevent the arc and is shown as a partial
or unknown type status with medium confidence. The export keeps unmatched
concrete endpoints and dynamic topic expressions as partial, explicitly
unresolved topic evidence; dynamic topics never imply a producer/consumer
pairing. Different known producer and consumer types are displayed as warnings
on the topic, relation, or integration port.

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
