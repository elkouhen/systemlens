# Extractors

Parent: [Technical specification](../SPEC-TECH.md).


`infer_framework_endpoints` walks Java declarations, annotations and method
invocations to discover Spring MVC/WebFlux routes, Feign clients, Spring HTTP
interfaces (`@HttpExchange` and method-level exchange annotations), RestTemplate,
WebClient, Spring Data REST and gateway routes. It resolves literals, known
Spring property expressions, unique never-reassigned local string base URLs,
and bounded private String helpers with one unconditional return. Multi-document Spring YAML is read document by document;
base-document values take precedence where no active-profile selection exists.
YAML parse failures, including unrendered Helm Go-template expressions, leave
that file without Spring-property facts and never abort the repository index.

REST graph construction first resolves an explicit target identity from an HTTP
host, `lb://` URI, configured client domain, or an opt-in Strategy1 convention.
For a URL expression that concatenates a local `@Value`-annotated field, a
unique never-reassigned local string base URL, or a bounded private String
helper with literal arguments, and a path, the extractor
resolves the value and retains its HTTP host as endpoint evidence while
persisting only the normalized route as the endpoint topic.
HTTP-interface targets come from the explicit `@ClientRegistrationId` marker;
the interface group alone is not treated as a target. Helper evaluation is
source-only and rejects mutation, overload ambiguity, conditional returns,
foreign receivers, recursion and unresolved arguments.
The normalized alias must match exactly one indexed service; prefix, suffix and
substring matching are not used. Route compatibility is evaluated only within
that service. A targetless or ambiguous call remains an endpoint fact and is
reported as unresolved rather than creating an internal edge.

`infer_kafka_endpoints` recognises Spring Kafka listeners and send sites,
KafkaTemplate/ProducerRecord usage and Spring Cloud Stream StreamBridge calls.
It preserves dynamic topic expressions and derives a payload type only from an
explicit listener parameter or client generic signature. A concrete shared
topic creates a producer/consumer service arc even when one or both Java
message types are unknown. The arc has medium confidence when type evidence is
missing. Two known and different types remain incompatible and do not create an
arc. An unmatched endpoint is exported as partial evidence, and a dynamic
topic receives an endpoint-specific unresolved topic node; dynamic topics do
not create a producer/consumer pairing. The HTML payload includes a warning
status (`unknown`, `partial`, or `mismatch`) so consumers can distinguish
evidence from a complete typed match. For topic-based
`KafkaTemplate.send` overloads, the final argument is the payload: preceding
arguments are a partition and/or key and are never reported as a message type.

With `--strategy strategy1`, every method whose name starts with
`envoyerMessageKafka` is an additional producer convention, including
`envoyerMessageKafkaRequest(topic, payload)` and
`envoyerMessageKafkaReply(topic, payload)`. A first argument shaped as
`kafkaProperties.getTopics().getXxx()` resolves to the normalized Strategy1
topic name; other values use the conservative topic resolver. The second
argument is used to derive the payload type from its method parameter, local
variable declaration or enclosing class field.

Strategy1 also enables the `getXxxServiceUrl()` REST target-name convention;
without it, SystemLens uses only an explicit URL or `lb://` service target.

The graph export exposes only the indexed Java payload-type identities linked
to Kafka endpoints. It does not resolve Java DTO fields or enums from source
roots at render time; recursive DTO inspection requires a future persisted
schema contract.

When persisted architecture relations are available, the HTML topology projects
their source evidence and confidence for MongoDB reads and writes. The older
module-inventory projection remains only as a compatibility fallback when that
snapshot relation set is absent.

The graph export keeps an exact-name index of its visual nodes. Its client-side
itinerary algorithm performs directed breadth-first searches for each pair of
user-supplied stops and concatenates those shortest segments. It uses only
Kafka graph relations, independently of temporary display filters, so an
itinerary always alternates between microservices and Kafka topics. Kafka
links carry relation-specific published or consumed Java message types; the
selected-path detail uses only these adjacent links and explicitly reports
missing type information.

Display filtering first maps persisted node and edge vocabulary to visual
categories. `kafka_topic` and `message_channel` are messaging nodes;
`mongodb_collection` and `data_schema` are data nodes. Edge classification
uses the native kind, its relation label and endpoint kinds so that native
`rest`/`kafka`/`mongodb` links and enriched `mcp_*` links respond to the same
HTTP, Kafka and data-access selectors. Unrecognized kinds remain conservative
and independently selectable as `Other`; they are never silently discarded.

In symbol rendering, label decluttering is recomputed from projected viewport
coordinates on each coalesced render. Candidates are ordered deterministically
by selection/hover state, visible degree, semantic node category, complexity,
then name. A greedy screen-space collision pass accepts labels up to a limit
derived from viewport area and inverse camera ratio, rejects labels that would
cover another accepted label or symbol, and chooses the free side of the
symbol. Zooming in therefore increases the labeled share of currently visible
nodes without changing graph positions or camera state. Selected and hovered
labels bypass the density and collision constraints so their names remain
visible.
For `n` visible nodes, the current conservative collision pass is `O(n²)` and
uses `O(n)` temporary rectangles; exported architecture reports are expected
to remain within interactive inventory sizes.

Symbol styling reuses each node's `--card-accent`: a light accent-tinted
gradient and a high-contrast accent border mirror the card surface/border
rules, while the dark theme mixes the same accent into the slate surface.
Selection and hover add scale and glow without changing the 30×30 collision
envelope. The microservice hexagon uses nested, pixel-aligned polygons for its
border and surface; this avoids clipping a rectangular CSS border and keeps the
six edges visually even at the compact symbol size.

The manifest extractors add explicitly declared Kafka facts from Markdown and
JSON. Strategy1 is separate and opt-in because it embeds repository-specific
naming conventions.

Strategy1 is implemented behind the `systemlens.conventions.strategy1` pack.
The pack owns Kafka topic normalization and replacement, REST target hints,
OpenAPI-declaration invalidation, and module-layer classification. Generic
indexing, graph, relation, and rendering modules call
those explicit operations only when the persisted profile selects Strategy1.
CLI selection, profile persistence, SQLite snapshots, and export mechanics
remain outside the pack. Historical scanner imports remain compatibility
façades and do not make Strategy1 active by default.

`systemlens analyze indexing-issues --json` exposes unresolved facts as a structured
remediation review payload. Each endpoint-backed issue has a stable code,
severity, service, framework, topic/API, extracted message type and its source
path, line range and snippet. The command does not infer or apply a heuristic;
its evidence is intended for a human or an AI to assess a conservative rule.
