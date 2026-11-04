# Review: weaknesses of the indexing algorithms (2026-11-03)

Status: lots 1 and 2 validated on 2026-11-03 by the product owner, who
asked to treat them; H4 follows Spring's rule (a literal route wins over a
template, and a template constraint is honoured). Lot 3 continued at the
product owner's request on 2026-11-04 (ADR-80). Lots 4 to 7 stay
`proposed`. Section 5 records each finding's state. Baseline: commit
`224dcfd`.
Owner: SystemLens implementer. Reviewer: product owner.

Four independent reviews covered HTTP extraction and target resolution,
messaging extraction and pairing, the call-graph join and flows, and
orchestration, discovery and storage. Each finding was proved by a probe on
a minimal fixture or a reference-project copy, unless marked `plausible`.
The most severe findings of each review were re-run by the coordinator.
Defects already recorded in [the case analysis](indexing-case-analysis.md)
and [the coverage plan](call-graph-coverage.md) are not repeated.

Probe scripts live in the session scratchpad and are not kept: each fix
rebuilds its probe as a regression test.

## 1. Root causes

Most findings come from seven recurring weaknesses.

| # | Weakness | Findings |
|---|---|---|
| R1 | Raw-text regular expressions where the AST is needed: comments, logs and beans count as sends or listeners | K1, K2 |
| R2 | A client is recognized by its variable name, not by its declared type | K9, K11, H11 |
| R3 | Repository-wide lookups that ignore module boundaries, test sources and the incremental delta | H3, I5 |
| R4 | Over-permissive matching that turns weak evidence into an asserted fact | H1, H2, H4, H5, K7 |
| R5 | Positional assumptions that pick the wrong argument or the wrong type source | K3, K4, K6 |
| R6 | Flow paths that skip rules the main join enforces, or lose its configuration | F1, F4, F5, F8, F12, F14 |
| R7 | Build discovery that does not follow the build tool's own model | I2, I3, I4, I7, I8, I9 |

## 2. Findings

Severity values: `false fact` (an asserted relation, route, topic, type or
flow that is wrong), `stale fact`, `missing fact`, `crash`, `performance`.

### 2.1 HTTP

| ID | Finding | Where | Severity |
|---|---|---|---|
| H1 | A dynamic call with a known host is projected onto every same-verb route of the target, because ordinary RestTemplate, WebClient and RestClient calls get the configured-API marker. Probe: one call to `catalog` gave three edges. Re-run by the coordinator. | `domain/graph.py:220-235`, `:423`; `scanner/rest_mvc.py:1221`, `:1253`, `:1472` | false fact |
| H2 | Gateway Java DSL: every route takes the first `uri(...)` of the builder chain, and an `http://` anywhere beats `lb://`. Re-run by the coordinator. | `domain/graph.py:195`; `rest_mvc.py:1673-1677` | false fact |
| H3 | A `@Value` field is looked up by name across the whole repository, including other modules and test sources, before the reassignment check. | `scanner/_spring_properties.py:362-399` | false fact |
| H4 | A `{var}` segment matches literal segments both ways, so a call fans out to the literal route and every sibling template; `{id:\d+}` matches `abc`. | `domain/graph.py:139-142` | false fact |
| H5 | The first DNS label of an external host names an internal service: `auth.okta.com` becomes module `auth`; an IP yields hint `10`. Re-run by the coordinator. | `domain/graph.py:146`; `rest_mvc.py:269`, `:298` | false fact |
| H6 | A method-local base URL loses its host as soon as a dynamic part is concatenated. On train-ticket, 51 of 107 unresolved calls have this shape. | `rest_mvc.py:273-303` | missing fact |
| H7 | Mapping constants, concatenations and multi-path arrays become `<dynamic>` before the existing constant resolvers run. | `rest_mvc.py:525-526`, `:587-588` | missing fact |
| H8 | Mappings declared on an interface or a base class are attributed to the declaring file; the implementing controller serves nothing, a base-class route has no prefix, and a Feign client extending the interface emits no call. | `rest_mvc.py:454-496`, `:554-576` | false fact |
| H9 | RestClient: a file-wide unique `baseUrl` host is applied to every client of the file, and its path is dropped. | `rest_mvc.py:1445-1456` | false fact |
| H10 | WebClient `baseUrl` on a field, `.uri(builder -> ...)`, `@HttpExchange(url=...)` and a literal Feign `url` host are not used. | `rest_mvc.py:1230-1262` | missing fact |
| H11 | RestTemplate is recognized only when the receiver name contains `resttemplate`; `RestOperations` and `getRestTemplate()` are missed. | `rest_mvc.py:1205-1207` | missing fact |
| H12 | An unresolved Feign `url = "${prop}"` makes every method route `<dynamic>`, although the paths are literal. | `rest_mvc.py:570-589` | missing fact |
| H13 | Gateway: YAML `uri: http://...` routes are dropped, long-form `StripPrefix` and Java DSL `stripPrefix` are ignored, and a quoted `uri` gets line 1. | `rest_mvc.py:1650-1666`, `:1715-1755` | missing fact |
| H14 | Extraction is quadratic in a file's size: 1000 controller methods take 53 s, through repeated whole-class walks. | `java_strings.py:98`; `_core.py:266` | performance |
| H15 | No Spring relaxed binding for property names, and constructor-injected `@Value` is not seen. | `_spring_properties.py` | missing fact |
| H16 | `/users/**` does not match `/users`, and a single `*` segment never matches; `String.format`, `URI.create` and `UriComponentsBuilder` URLs stay dynamic. | `domain/graph.py:127` | missing fact |

### 2.2 Messaging

| ID | Finding | Where | Severity |
|---|---|---|---|
| K1 | Strategy1: any `getTopics().getXxx()` in the raw text is a producer, including a `NewTopic` bean, a log line and a commented-out send. Re-run by the coordinator. | `scanner/kafka_conventions.py:385` | false fact |
| K2 | Strategy1 listeners are found by regex: a commented-out listener is a consumer, and the regex payload type overrides the AST type (`Payment` instead of `Order`). Re-run by the coordinator. | `kafka_conventions.py:437`, `:549`; `scanner/_core.py:151-152` | false fact |
| K3 | `sendDefault(key, value)` stores the key as the topic; `sendDefault(value)` is dropped. Re-run by the coordinator. | `scanner/kafka_ast.py:452-458` | false fact |
| K4 | A `ProducerRecord` with headers is typed by its headers argument (`List<Header>`), which then blocks pairing. Re-run by the coordinator. | `kafka_ast.py:297`, `:509` | false fact |
| K5 | Payload typing misses `instanceof` and `switch` pattern variables, try-with-resources resources and anonymous-class fields, and picks up an outer field of the same name instead. | `scanner/kafka_payload_types.py:454` | false fact |
| K6 | A Kafka Streams fact is typed by the enclosing method's return type (`Topology`, or the output `KStream`), which blocks pairing. | `kafka_ast.py:484`, `:490` | false fact |
| K7 | Generic carriers count as known DTOs and block pairing: `JsonNode`, `GenericRecord`, `SpecificRecord`, `Bytes`, `ByteBuffer`, `Map<...>`, `Serializable`. | `domain/message_types.py:23` | false fact |
| K8 | Spring Cloud Stream: the binding name becomes the topic, `bindings.*.destination` is never read, and functional consumers are missing. | `kafka_ast.py:473` | false fact |
| K9 | A Kafka Streams consumer is found only when the receiver is named `builder`. | `kafka_ast.py:485` | missing fact |
| K10 | A topic constant from another class or a static import stays dynamic; `this.topic` stays dynamic while `topic` resolves. | `kafka_ast.py:124` | missing fact |
| K11 | Producers are recognized by receiver name: `KafkaOperations`, `getKafkaTemplate().send`, `executeInTransaction` lambdas are missed. | `kafka_ast.py:44`, `:452-456` | missing fact |
| K12 | An inline `send(MessageBuilder...build())` is dropped. | `kafka_ast.py:465` | missing fact |
| K13 | The `@KafkaListeners({...})` container annotation is ignored. | `kafka_ast.py:430` | missing fact |
| K14 | The supertype walk of payload typing is exponential on wide hierarchies and nothing is cached. | `kafka_payload_types.py:356` | performance |
| K15 | The Strategy1 catalog tests excluded directories against absolute path parts: a clone under `~/build/` finds no `kafka.yml`. `plausible`. | `_spring_properties.py:278` | missing fact |

### 2.3 Call-graph join and flows

| ID | Finding | Where | Severity |
|---|---|---|---|
| F1 | A CodeQL reachability row becomes a flow without the application-boundary, entry-dispatch and hop-limit rules the main join applies; it also replaces any join flow of the same pair. Re-run by the coordinator. | `indexing/code_flows.py:1605-1701` | false fact |
| F2 | An OUT followed by another OUT of the same module is never reconciled, so an `IN → OUT1 → OUT2` flow is `complete` when OUT1 has no consumer. | `code_flows.py:1758` | false fact |
| F3 | `alternative_count` is always 1 after deduplication: 406 of 406 flows on train-ticket. Contradicts `data-model.md` and `indexing.md`. Re-run by the coordinator. | `code_flows.py:238` | false fact |
| F4 | The two engines and `flows calculate` persist different flow sets, and different IDs, for one method with several effects. | `call_graph_stage.py:685-691`; `application/flow_calculation.py:77-110` | false fact |
| F5 | A scheduled flow is persisted twice, because the AST and the transient trigger label the trigger differently. | `code_flows.py:342`; `integration_methods.py:181` | false fact |
| F6 | A second data operation with the same name on the same line is dropped: the resource is not in the key. | `code_flows.py:989`, `:1450` | missing fact |
| F7 | A port inside a local named class is attached to the enclosing method, which yields a flow through code never called. Re-run by the coordinator. | `indexing/integration_methods.py:263` | false fact |
| F8 | The CodeQL interface witness fans out to every implementation even when CodeQL resolved the exact target, against ADR-70. | `code_flows.py:877-897` | false fact |
| F9 | The hop limit drops an OUT silently: nothing is persisted or reported, while CodeQL reachability is unbounded. | `code_flows.py:1464` | missing fact |
| F10 | Flow IDs change on cosmetic edits: method IDs hash the parameter source text, and data-flow IDs include the line. | `integration_methods.py:292`; `code_flows.py:1527-1532` | stale fact |
| F11 | The reverse call-chain diagnostic enumerates every path: about 4^12 lines on a dense graph. Only with `--show-call-chains`. | `code_flows.py:1222-1257` | performance |
| F12 | `flows calculate` ignores the index configuration: no edge-confidence setting, no repository root for key tracing, no `after_commit` phase, and `--module` replaces AST flows instead of rebuilding them. | `flow_calculation.py:77-87` | false fact |
| F13 | An interruption during module-progress checkpoints can make `--resume-codeql-join` keep a filtered subset of flows. `plausible`. | `call_graph_stage.py:466-503`; `indexing/service.py:322` | missing fact |
| F14 | Every source-engine flow says "dispatch fallback", even through exact static calls. | `code_flows.py:1554` | false fact |

### 2.4 Orchestration, discovery and storage

| ID | Finding | Where | Severity |
|---|---|---|---|
| I1 | A crash after the first call-graph checkpoint publishes new file hashes with the old flows and an intact flow signature; incremental indexes then skip the flow stage forever, until `--full`. Contradicts ADR-7 and ADR-37. | `indexing/call_graph_stage.py:400`; `code_flows.py:1088`; `service.py:454`, `:507` | stale fact |
| I2 | Gradle: a Spring Boot root project owns every subproject's endpoints; for nested services the outer one wins. Re-run by the coordinator. | `discovery/build/gradle.py:169` | false fact |
| I3 | Gradle: endpoints in a library module get no module, so their relations are lost, and the ownership goes stale incrementally. | `gradle.py:160-173`; `scanner/_core.py:321` | false fact |
| I4 | Module dependencies ignore the groupId and the scope: an external `core` artifact and a test dependency become internal dependencies. Re-run by the coordinator. | `discovery/build/modules.py:1418-1438` | false fact |
| I5 | Topic and URL resolution read `@Value` fields from other files and test sources, outside the incremental delta: a test file changed a production topic. | `_spring_properties.py:362-396` | stale fact |
| I6 | A second module with the same artifactId renames the first module's identity, but its endpoints keep the old one until `--full`. | `discovery/build/maven.py:316-335`; `indexing/file_inventory.py:200-242` | stale fact |
| I7 | Modules deeper than five levels are dropped from the inventory while their endpoints still name them. | `modules.py:155`, `:1290` | false fact |
| I8 | Gradle project paths are assumed to be directory paths, and the name fallback never matches, so dependency edges are lost. | `modules.py:1398-1412` | missing fact |
| I9 | Discovery ignores `settings.gradle` includes and Maven `<modules>`: `buildSrc`, convention plugins and stray build files become modules. | `gradle.py:119-138`; `modules.py:1290` | false fact |
| I10 | One POM with an internal DTD entity aborts the whole index (`EntitiesForbidden` is not caught). Re-run by the coordinator. | `maven.py:44`, `:217`, `:232`; `modules.py:1423` | crash |
| I11 | Gradle identity lookups walk the repository once per file with endpoints: 47 of 55 s on 80 modules. | `gradle.py:141-150` | performance |
| I12 | Maven module identity walks the tree once per POM, on every index: 21 of 45 s on 300 modules. | `maven.py:321` | performance |
| I13 | Nested `node_modules`, `.venv` and `.idea` directories are hashed and indexed, and can trigger module rescans; every file is rehashed every run. | `indexing/file_inventory.py:159-163`; `infrastructure/config.py:10` | performance |
| I14 | A second concurrent index fails with a raw `database is locked` traceback, because every writable open takes the write lock; the checkpoint gap lets another writer in. | `storage/sqlite.py:207-214`, `:589` | crash |

## 3. Checked and found sound

- No absolute path and no secret value in any probe database; the
  configuration template redacts sensitive keys.
- Migrations are additive and committed before the index transaction.
- Deleting or moving a file purges its facts; endpoint IDs include the path.
- A full re-index of train-ticket reproduces all 406 flows byte for byte.
- The forward flow search is linear, and recursion ends as `cycle`.
- `normalize_path`, verb handling, `${x:default}` placeholders, conflicting
  profile values and host normalization behave as specified.
- Kafka topic arrays, `topicPartitions`, placeholders with defaults,
  `@Payload` placement and `ConsumerRecord` unwrapping behave as specified.

## 4. Proposed lots

| Lot | Findings | Why this order |
|---|---|---|
| 1 | I10, I1, I14 | A crash, a snapshot that never heals, and an unreadable failure. |
| 2 | H1, H2, H4, H5, K1, K2, K3, K4, K6, K7, F1, F7, F8 | False facts presented as proved; most are local fixes. |
| 3 | I2, I3, I4, I7, I9, I6 | Wrong module ownership and dependencies, which every other fact inherits. |
| 4 | F2, F3, F4, F5, F12, F14, F6, F9, F10 | Flow consistency between engines and commands. |
| 5 | H3, I5, K5, H8, H9, K8 | Scoped lookups and ownership of inherited mappings. |
| 6 | Remaining missing facts | Coverage, ordered by the case analysis. |
| 7 | H14, I11, I12, I13, K14, F11 | Performance on large repositories. |

Fixes that change a visible rule, such as F3's alternative count or H4's
literal-over-template choice, need their decision record first. Every fix
leaves a regression test built from its probe.

## 5. Delivery record

| ID | Outcome |
|---|---|
| I10 | Delivered in `68517b0`. A POM refused by defusedxml is skipped like a malformed one; protection stays on. |
| I1 | Delivered in `d934618`. The flow stage unsets the flow signature and marks the snapshot partial before its first checkpoint; a partial snapshot reruns the flow stage and drops flows of deleted files. |
| I14 | Delivered in `0203dc5`. A current index opens without the write lock; a locked index fails with a clear message and exit code 2. Residual risk: the `index_engine` write after the transaction can still meet a raw lock error. |
| H1 | Delivered in `b227619`. A dynamic call of a route-declaring client keeps one service-level relation without a resource. |
| H2 | Delivered in `b227619`. Each gateway DSL route uses its own `uri(...)` and line. |
| H4 | Delivered in `b227619` with ADR-77. A literal route wins over a template; constraints are honoured; unaligned templated calls keep every candidate as separate edges, without a confidence change. |
| H5 | Delivered in `b227619`. Only single-label and cluster-local hosts name a service; other hosts need an exact alias. |
| K1 | Delivered in `9b7c824`. Strategy1 producers are send calls found in the syntax tree. |
| K2 | Delivered in `9b7c824`. Strategy1 listeners are read from the syntax tree; the AST payload type is used. |
| K3 | Delivered in `53c02fe`. `sendDefault` publishes to the template's known default topic, else a dynamic topic. |
| K4 | Delivered in `0188929`. The `ProducerRecord` value is chosen by constructor arity or written type argument. |
| K6 | Delivered in `915706c`. Streams facts are typed from their own declarations or serdes. |
| K7 | Delivered in `9c92a43` with ADR-79. Generic payload carriers are unknown types. |
| F1 | Delivered in `a9ee304` with ADR-78, which amends ADR-29 and ADR-30. A reachability row creates a flow only through a route that passes the join's rules and never replaces a join flow. Train-ticket with CodeQL keeps 408 flows; 170 flow IDs change back to the join's IDs. |
| F7 | Delivered in `8af758e`. A port belongs to the narrowest method that contains it. |
| F8 | Delivered in `9d495ce`. The witness and the symbol fallback expand only to the exactly resolved target at a site CodeQL resolved. |
| I2 | Delivered in `74789a1` with ADR-80. A file belongs to the deepest Maven or Gradle project; a Spring Boot root no longer owns its subprojects. |
| I3 | Delivered in `74789a1`. Gradle library endpoints name their library module, so their relations exist; ownership no longer depends on a main class. |
| I4 | Delivered in `74789a1`. A Maven dependency needs the local groupId when known; Maven `test` and Gradle test configurations give no edge; `provided`, `compileOnly`, `runtimeOnly` stay edges. |
| I7 | Delivered in `74789a1`. No depth limit; one walk that skips hidden, `node_modules` and build-output directories feeds inventory and ownership. |
| I9 | Delivered in `74789a1`. Aggregator `<modules>` and literal `settings.gradle` includes and `projectDir` remaps declare the build; `buildSrc`, `includeBuild` and undeclared build directories are excluded; computed declarations keep the former discovery. |
| I6 | Delivered in `74789a1`. An incremental index rescans every file of a module whose identity changed, appeared or disappeared. |

Validation on `main` after all lots 1 and 2 commits: ruff, mypy, 868 tests
passed with 14 expected failures, and the companion contract check. A fresh
train-ticket index with the source engine keeps 2999 edges, 406 flows and
78 HTTP service relations; the lab keeps its 23 flows. K1 and K2 were not
measured on a real Strategy1 repository.

Validation on `main` after lot 3: ruff, mypy, 878 tests passed with 14
expected failures, and the companion contract check. Fresh source-engine
indexes keep train-ticket's 406 flows, 78 HTTP service relations, 1153
relations and 524 endpoints, the sample's 4 flows and the lab's 23 flows.
Train-ticket drops the undeclared `old-docs/Lib/ms-monitoring-core` copy:
one module and its 1190 call edges (2999 to 1809), with no endpoint,
relation or flow. Gradle findings were measured on fixtures only; no
reference project uses Gradle.
