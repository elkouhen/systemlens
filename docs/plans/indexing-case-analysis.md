# Analysis: the cases indexing must handle

Status: `proposed` on 2026-11-03. Not validated. This document lists cases;
it schedules no work. Owner: SystemLens implementer. Reviewer: product owner.

Sources: the extraction rules in `docs/spec-fonc/extraction-rules.md` and
`docs/spec-tech/extractors.md`, a search of `src/systemlens` for each
framework or configuration key, and three measured projects:
train-ticket, the lab application `apps/supermarket-demo`, and
`sample-spring-microservices-new` at `d7bf01c`.

## 1. Method

Indexing turns source files into one chain of facts:

```text
module -> port (IN or OUT) -> channel (route, topic, collection)
       -> peer (target service or resource) -> internal path -> flow
```

Each fact varies along the same five axes. A case is one value of one axis
for one fact; the lists below enumerate them axis by axis.

| Axis | Question | Values |
|---|---|---|
| A. Declaration site | Where is the fact written? | annotation, code call, configuration file, contract (OpenAPI, AsyncAPI), build file, generated code, manifest |
| B. Value form | How is the value spelled? | literal, constant, concatenation, property placeholder, profile-specific value, enum, conditional branch, method parameter, runtime computation |
| C. Peer identity | What names the other side? | explicit name, URL host, discovery id, gateway route, contract, convention, nothing |
| D. Ownership | Which module owns the fact at runtime? | the file's module, another module (config server, shared library), several modules |
| E. Multiplicity | How many facts does one declaration give? | one, one per branch, one per profile, the same name in several modules |

Each case gets one status:

| Status | Meaning |
|---|---|
| `verified` | Measured in a fixture or a reference project on 2026-11-03. |
| `defect` | Measured, and the result is wrong. |
| `specified` | The specification claims it; not measured here. |
| `absent` | Neither the specification nor the code mentions it. |

## 2. Modules and services

| # | Case | Status | Evidence |
|---|---|---|---|
| M1 | Maven multi-module build with an aggregator | `verified` | sample: seven modules, one aggregator |
| M2 | Gradle build, `settings.gradle` includes | `specified` | `discovery/build` |
| M3 | Application module versus library, by `SpringApplication.run` | `verified` | sample: entry points recorded |
| M4 | Service name from `spring.application.name` in a multi-document YAML | `specified` | sample names equal the module names, so not discriminating |
| M5 | Service name served by a config server from another module | `absent` | sample: `config-service/.../config/*.yml` |
| M6 | Deployment name differs from module name (Compose, Kubernetes) | `specified` | Kubernetes matcher in `BACKLOG.md` section 7 |
| M7 | Same qualified type declared in two modules | `defect` | call shape M3 of the coverage plan |
| M8 | Non-Java module (front end, scripts) | `absent` | |

## 3. HTTP served ports

| # | Case | Status | Evidence |
|---|---|---|---|
| H1 | `@RestController` with class and method mappings | `verified` | sample: 16 of 16 routes |
| H2 | Route from a constant, an array or several paths | `specified` | extraction rules |
| H3 | `server.servlet.context-path` or `spring.webflux.base-path` prefix | `absent` | no reference in `src/systemlens` |
| H4 | WebFlux functional routes (`RouterFunction`) | `absent` | only YAML `server.webflux.routes` is read |
| H5 | JAX-RS (`@Path`) | `absent` | |
| H6 | Spring Data REST repositories | `specified` | `rest_mvc.py` |
| H7 | Contract-first OpenAPI, generated interfaces | `specified` | extraction rules |
| H8 | Gateway routes as served ports | `defect` | sample: attributed to `config-service`, duplicated per profile |
| H9 | Actuator and management endpoints | `absent` | not decided whether they are ports |

## 4. HTTP calling ports

| # | Case | Status | Evidence |
|---|---|---|---|
| C1 | Feign interface: route from the method mapping | `verified` | sample: 4 of 4 calls |
| C2 | Feign `url` or `path` attribute, literal or placeholder | `specified` | `_ast_feign_base` |
| C3 | `RestTemplate`, `RestClient`, `WebClient` with a literal or `@Value` base URL | `specified` | extraction rules |
| C4 | Base URL from a builder bean or a configuration class | `specified` | `rest_client_config.py` |
| C5 | Spring HTTP interface with `@ClientRegistrationId` | `specified` | extractors |
| C6 | Generated OpenAPI client | `specified` | Strategy1 rules |
| C7 | Gateway route: `Path` predicate and `lb://` target | `verified` | sample: three routes read |
| C8 | Gateway filters `RewritePath`, `PrefixPath`, `SetPath` | `defect` | sample: only `StripPrefix` is applied |
| C9 | Gateway discovery locator (implicit routes per service) | `absent` | sample sets `discovery.locator.enabled` |
| C10 | URL computed at runtime | `verified` | stays `<dynamic>` with a diagnostic |

## 5. HTTP peer identity

| # | Case | Status | Evidence |
|---|---|---|---|
| P1 | URL host equal to a service name | `specified` | extraction rules |
| P2 | `lb://service` URI | `verified` | sample: gateway routes resolved |
| P3 | Feign `name` or `value` | `defect` | sample: never used, so 4 of 4 calls stay external |
| P4 | `@LoadBalanced` client with `http://service/...` | `specified` | as P1 |
| P5 | Host from a property that a config server or profile sets | `absent` | |
| P6 | Same route served by two services | `verified` | sample: no route-only guess, as required |
| P7 | Gateway path matched against routes without its rewrite | `defect` | sample: `/organization/**` matched `/{id}/...` by coincidence |
| P8 | External service outside the workspace | `specified` | extraction rules |

## 6. Messaging ports

| # | Case | Status | Evidence |
|---|---|---|---|
| K1 | `KafkaTemplate.send` with a literal or constant topic | `verified` | lab |
| K2 | `@KafkaListener(topics = ...)` with a placeholder | `verified` | lab |
| K3 | `topicPattern` listener | `specified` | `kafka_ast.py` |
| K4 | Conditional topic: ternary, `if/else`, `switch` | `verified` | Strategy1 probe; three gaps measured |
| K5 | Spring Cloud Stream bindings and `StreamBridge` | `specified` | `spring.cloud.stream` support |
| K6 | Kafka Streams topology | `specified` | extractors |
| K7 | Payload type through `Message<T>` or `ProducerRecord` | `verified` | fixture; ADR-76 |
| K8 | RabbitMQ (`@RabbitListener`, `RabbitTemplate`) | `absent` | |
| K9 | JMS (`@JmsListener`, `JmsTemplate`) | `absent` | |
| K10 | Reply topic `@SendTo` | `absent` | |

Producer payload shapes, measured on both send paths (`KafkaTemplate.send`
and Strategy1 `envoyerMessageKafka*`) on 2026-11-03, before and after
ADR-76. Each `verified` row has a regression test in
`tests/test_kafka_extraction_regressions.py`.

| # | Payload shape | Before | After |
|---|---|---|---|
| KP1 | Method parameter, typed local, `var x = new T()`, class field, `new` | typed | `verified` |
| KP2 | `Message<T>` local, `ProducerRecord<K, V>` | typed | `verified` |
| KP3 | `this.field`, inherited field from another file | unknown | `verified` |
| KP4 | `var x = mapper.toEvent(o)`, direct mapper call | unknown | `verified` |
| KP5 | Getter `h.getEvent()` (declared, record or Lombok) | unknown | `verified` |
| KP6 | Lombok `X.builder()...build()` | unknown | `verified` with `@Builder`; unknown without |
| KP7 | Cast, ternary (`null` branch neutral) | unknown | `verified` |
| KP8 | Lambda parameter, for-each variable over a typed collection | unknown | `verified` |
| KP9 | Same-class helper for `KafkaTemplate.send` | unknown | `verified` |
| KP10 | `MessageBuilder.withPayload(p)` sent alone | unknown | `verified` |
| KP11 | Untyped value, `KafkaTemplate<K, V>` receiver | unknown | `verified`: `V` |
| KP12 | Type variable `T`, `Object`, JSON `String`, `byte[]` | wrong known type | `verified`: unknown |
| KP13 | `p.OrderEvent` against `OrderEvent` | blocked pairing | `verified`: paired |
| KP14 | `List<OrderEvent>` sent as one message | `List<OrderEvent>` | unchanged by decision |
| KP15 | Consumer typed `String`, `Object`, `byte[]` | known | `verified`: unknown |
| KP16 | Two different DTO names on one topic | blocked pairing | unchanged: blocked |
| KP17 | Generic substitution (`Holder<E>.get()`), overloads of one arity | unknown | `specified`: stays unknown |

## 7. Data resources

| # | Case | Status | Evidence |
|---|---|---|---|
| D1 | Spring Data JPA repository and derived queries | `verified` | train-ticket: 27 repositories, 79 queries |
| D2 | `EntityManager`, `JdbcTemplate` operations | `specified` | extraction rules |
| D3 | Spring Data MongoDB and `MongoTemplate` | `verified` | lab |
| D4 | In-memory repository class, no database | `verified` | sample: no false operation |
| D5 | R2DBC, Cassandra, Redis, Elasticsearch | `absent` | at most a mention in `src/systemlens` |
| D6 | Transaction scope and guards | `specified` | extraction rules |

## 8. Configuration semantics

| # | Case | Status | Evidence |
|---|---|---|---|
| G1 | Multi-document YAML with `on-profile` documents | `specified` | profile values never override without an active profile |
| G2 | Profile-specific file (`*-docker.yml`) next to the base file | `defect` | sample: gateway routes indexed twice |
| G3 | Config server files that belong to other services | `absent` | sample: owner is the file's module |
| G4 | Placeholder with a default, `${X:default}` | `specified` | |
| G5 | Environment variable override | `absent` | runtime, out of static scope |

## 9. Internal paths and flows

| # | Case | Status | Evidence |
|---|---|---|---|
| F1 | Every Java call shape | see the [coverage plan](call-graph-coverage.md) | 53 fixture cases |
| F2 | Lambda with an untyped parameter | `defect` | sample: 8 edges found by CodeQL only |
| F3 | Flow from an HTTP entry to an HTTP call | `verified` | sample: 4 of 4 flows |
| F4 | Flow from a scheduler | `verified` | lab |
| F5 | Flow from a runner or `@PostConstruct` | `absent` | train-ticket: 21 runners, 2 methods |
| F6 | Cross-service chain of flows | `defect` | sample: broken by P3 |
| F7 | Cycle in the call graph | `verified` | train-ticket, ADR-71 |

## 10. Use

1. A row marked `specified` becomes `verified` or `defect` only through a
   fixture or a reference-project measurement, as K1 of the coverage plan
   does for call shapes.
2. A direct analysis of a project by reading its code, compared with the
   tool's index, finds the rows nobody listed. The 2026-11-03 sample
   comparison found P3, P7, C8, H8, G2, G3 and F2 this way.
3. An `absent` row is a scope question for the product owner before it is
   a task: some, like G5, stay out of static analysis on purpose.
