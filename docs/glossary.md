# Glossary

This glossary contains the canonical terms used across the SystemLens
documentation, CLI, and generated graph exports.

## Graph and architecture

| Term | Definition and usage |
|---|---|
| Architecture snapshot | Persisted representation of the indexed architecture, including nodes, relations, evidence, and metadata. Use this term instead of the generic `model`. |
| Architecture topology | Complete inventory of microservices, topics, APIs, resources, and their indexed relations. |
| Interaction graph | Relations between microservices, including synchronous HTTP calls and asynchronous Kafka exchanges. Use `inter-service interaction graph` when the scope needs to be explicit. |
| Flow graph | Graph for one selected potential execution path through the architecture topology. |
| Call graph | Persisted service-level graph derived from one selected flow, with ordered causal edges and flow metadata. |
| Call tree | Hierarchical rendering of one call graph. A microservice may appear more than once when reached through different occurrences. |
| Method call graph | Code-level method relationships resolved by CodeQL or an equivalent engine. Do not use this term for an architecture or flow graph. |
| Local analysis | Analysis executed on the user's machine, including AST extraction and an optional local CodeQL invocation. |
| Potential static flow | A source-backed path inferred from indexed methods and calls. It does not assert runtime execution. |
| Node | Graph vertex representing a microservice or resource. Use `nœud` in user-facing French text. |
| Edge | Relation in the graph, whether persisted or displayed. Use this term instead of `arc`. |
| Relation | Semantic dependency or communication between two architectural elements. An edge is its graph representation. |
| Subgraph | Graph projection containing a selected node and the relations included by a scope or traversal rule. |

## Architectural elements

| Term | Definition and usage |
|---|---|
| Microservice | Deployable service represented as an architectural node. Use this term instead of the generic `service`. |
| Resource | Indexed architectural element other than a microservice, such as a topic, route, collection, DTO, or JPA entity. |
| Topic | Kafka topic used to publish or consume messages. |
| Message channel | Generic asynchronous channel whose transport is not established as Kafka. |
| Endpoint | Communication boundary exposed or consumed by a microservice. |
| Port | Directional connection between a microservice and an endpoint, identified as input or output. |
| Route | HTTP endpoint identified by a method and path, such as `POST /orders`. |
| Producer | Microservice or endpoint that publishes a message to a topic. |
| Consumer | Microservice or endpoint that consumes a message from a topic. |
| Trigger | Event that starts a flow, such as an HTTP request, Kafka message, or scheduled execution. |
| Module | Logical code or deployment grouping. |
| Layer | Architectural level or level in the corresponding visualization. |

## Analysis and persistence

| Term | Definition and usage |
|---|---|
| Fact | Structured assertion extracted from source code, configuration, or an imported manifest. A fact may remain ambiguous or unresolved. |
| Evidence | Source location or provenance supporting a fact or relation. Persist paths relative to the indexed project root. |
| Confidence | Degree of certainty associated with a fact or relation. |
| Ambiguity | Condition where several targets or interpretations remain possible. It does not justify inventing a target. |
| Extractor | Component that discovers facts from source code, configuration, or build metadata. |
| Index | SQLite persistence containing normalized facts and the architecture snapshot. |
| Indexing | Operation that builds or updates the index. |
| Import | CLI operation that adds or replaces facts from a JSON manifest. It is distinct from extraction. |
| Enrichment | Additional indexed information supplied by an external or supplemental analysis source. |
| Export | Generated representation of the persisted architecture snapshot, such as HTML, JSON, GraphML, or XLSX. An export must not silently re-parse source code. |

## User interface

| Term | Definition and usage |
|---|---|
| Inspector | Window that displays the details of a selected node or relation. |
| Introspection | Action of examining a selected node or relation in the inspector. |
| Selection | UI state identifying the active node, relation, or flow. It is distinct from opening the inspector. |
| Tooltip | Transient contextual content shown on hover. It must not imply an action that the current view does not support. |
| Hit area | Interactive region that receives pointer input for a visual node or edge. Use when clickability differs from visible geometry. |

## Naming conventions

- Use lowercase for generic terms: `microservice`, `topic`, `node`, `edge`,
  `flow`, and `call tree`.
- Preserve product, protocol, and technology names: `SystemLens`, `Kafka`,
  `MongoDB`, `CodeQL`, `DTO`, `JPA`, `HTTP`, and `JSON`.
- Use `graph` for a data structure and `view` only for a rendered projection.
- Use `call tree` for the causal hierarchy and `method call graph` for
  code-level method relationships.

This glossary owns terminology. The functional and technical specifications
remain authoritative for behavior, data contracts, and algorithms.
