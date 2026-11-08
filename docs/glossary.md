# Glossary

This glossary contains the canonical terms used across the SystemLens
documentation, CLI, and generated graph exports.

## Graph and architecture

| Term | Definition and usage | French label |
|---|---|---|
| Architecture snapshot | Persisted representation of the indexed architecture, including nodes, relations, evidence, and metadata. Use this term instead of the generic `model`. | `snapshot` |
| Architecture topology | Complete inventory of microservices, topics, APIs, resources, and their indexed relations. | `topologie` |
| Interaction graph | Relations between microservices, including synchronous HTTP calls and asynchronous Kafka exchanges. Use `inter-service interaction graph` when the scope needs to be explicit. | `graphe des interactions` |
| Flow graph | Graph for one selected potential execution path through the architecture topology. | `graphe du flux` |
| Call graph | Persisted service-level graph derived from one selected flow, with ordered causal edges and flow metadata. | `arbre d’appel` (its rendered view; never `graphe d’appel`) |
| Call tree | Hierarchical rendering of one call graph. A microservice may appear more than once when reached through different occurrences. | `arbre d’appel` |
| Method call graph | Code-level method relationships resolved by CodeQL or an equivalent engine. Do not use this term for an architecture or flow graph. | `graphe d’appels de méthodes` |
| Local analysis | Analysis executed on the user's machine, including AST extraction and an optional local CodeQL invocation. | `analyse locale` |
| Potential static flow | A source-backed path inferred from indexed methods and calls. It does not assert runtime execution. | `flux potentiel` |
| Code flow | Persisted catalogue entry for one potential static flow. Its rendered view is a call tree. | `flux de code` |
| Path | Shortest directed route searched between two or more nodes of the architecture graph. Do not use it for a code flow. | `chemin` |
| Node | Graph vertex representing a microservice or resource. Use `nœud` in user-facing French text. | `nœud` |
| Edge | Relation in the graph, whether persisted or displayed. Use this term instead of `arc`. | `relation`; the CLI keeps `arête d’appel` for CodeQL call edges only |
| Relation | Semantic dependency or communication between two architectural elements. An edge is its graph representation. | `relation` |
| Subgraph | Graph projection containing a selected node and the relations included by a scope or traversal rule. | `sous-graphe` |

## Architectural elements

| Term | Definition and usage | French label |
|---|---|---|
| Microservice | Deployable service represented as an architectural node. Use this term instead of the generic `service`. | `microservice` |
| Resource | Indexed architectural element other than a microservice, such as a topic, route, collection, DTO, or JPA entity. | `ressource` |
| Data resource | Persisted data resource, such as a MongoDB collection or a JPA entity. | `donnée` |
| Persistence class | Java class bound to a data resource, such as a Mongo document class or a JPA entity class. | `classe de persistance` |
| APM service | Service name reported by the runtime overlay. It is not necessarily an indexed microservice. | `service` |
| Topic | Kafka topic used to publish or consume messages. | `topic` |
| Message channel | Generic asynchronous channel whose transport is not established as Kafka. | `canal de messages` |
| Endpoint | Communication boundary exposed or consumed by a microservice. | `endpoint` |
| Port | Directional connection between a microservice and an endpoint, identified as input or output. | `port` (`IN`, `OUT`) |
| Route | HTTP endpoint identified by a method and path, such as `POST /orders`. | `route` |
| Producer | Microservice or endpoint that publishes a message to a topic. | `producteur` |
| Consumer | Microservice or endpoint that consumes a message from a topic. | `consommateur` |
| Trigger | Event that starts a flow, such as an HTTP request, a Kafka, RabbitMQ or JMS message, a scheduled execution, startup code (`démarrage`) or an in-process event (`événement`). | `déclencheur` |
| Module | Logical code or deployment grouping. | `module` |
| Layer | Architectural level or level in the corresponding visualization. | `couche` |

## Analysis and persistence

| Term | Definition and usage | French label |
|---|---|---|
| Fact | Structured assertion extracted from source code, configuration, or an imported manifest. A fact may remain ambiguous or unresolved. | `fait` |
| Evidence | Source location or provenance supporting a fact or relation. Persist paths relative to the indexed project root. | `preuve` |
| Confidence | Degree of certainty associated with a fact or relation. | `confiance` |
| Ambiguity | Condition where several targets or interpretations remain possible. It does not justify inventing a target. | `ambiguïté` |
| Extractor | Component that discovers facts from source code, configuration, or build metadata. | `extracteur` |
| Index | SQLite persistence containing normalized facts and the architecture snapshot. | `index` |
| Indexing | Operation that builds or updates the index. | `indexation` |
| Import | CLI operation that adds or replaces facts from a JSON manifest. It is distinct from extraction. | `import` |
| Enrichment | Additional indexed information supplied by an external or supplemental analysis source. | `enrichissement` |
| Export | Generated representation of the persisted architecture snapshot, such as HTML or JSON. An export must not silently re-parse source code. | `export` |

## User interface

| Term | Definition and usage | French label |
|---|---|---|
| Inspector | Window that displays the details of a selected node or relation. | `inspecteur` |
| Introspection | Action of examining a selected node or relation in the inspector. | `introspection` |
| Selection | UI state identifying the active node, relation, or flow. It is distinct from opening the inspector. | `sélection` |
| Tooltip | Transient contextual content shown on hover. It must not imply an action that the current view does not support. | `info-bulle` |
| Hit area | Interactive region that receives pointer input for a visual node or edge. Use when clickability differs from visible geometry. | `zone cliquable` |

## User interface vocabulary

The `French label` column fixes the one word that the HTML export and the
CLI use for each concept, as decided in
[PDR-7](PDR.md#pdr-7-one-french-word-per-concept-in-the-export-and-the-cli):

1. Technical loan words such as `topic`, `DTO`, `snapshot`, and `handler`
   stay untranslated; every other label is French.
2. `microservice` names an indexed service; `service` is kept only for the
   APM services of the runtime overlay.
3. `relation` names any link between two elements in the export; the CLI
   keeps `arête d’appel` for CodeQL call edges only.
4. The node-type filter for topics reads `Topics`; the relation types read
   `Appels d’API`, `Messages`, `Accès aux données`, and `Autres`.
5. `flux de code` names a catalogue entry, `arbre d’appel` its rendered
   view, and `chemin` the path searched between two nodes.
6. `donnée` names a persisted data resource, and the Java classes bound to
   it are `classes de persistance`.
7. CLI help uses the infinitive and a lower-case `topic`, and the export uses
   the typographic apostrophe.

## Resource kind labels

One label names each resource kind in the export: the catalogue tag, the
catalogue preview, and the inspector title `<label> · <name>` read the same
word, as decided in
[PDR-10](PDR.md#pdr-10-inspecter-opens-one-surface-per-kind-of-fact).
The `resourceKindLabels` map of the graph assets holds the same pairs, and
`tests/test_spec_conformance.py` keeps both aligned.

| Kind key | French label |
|---|---|
| `microservice` | `Microservice` |
| `external_microservice` | `Microservice externe` |
| `http_route` | `Route HTTP` |
| `served_route` | `Route exposée` |
| `called_route` | `Route appelée` |
| `openapi_contract` | `Contrat OpenAPI` |
| `asyncapi_contract` | `Contrat AsyncAPI` |
| `kafka_topic` | `Topic` |
| `message_channel` | `Message` |
| `mongodb_collection` | `Collection` |
| `data_schema` | `Donnée` |
| `jpa_entity` | `Entité JPA` |
| `dto` | `DTO` |
| `mongo_persistence_class` | `Classe de persistance MongoDB` |
| `diagnostic` | `Diagnostic` |
| any other kind | `Ressource` |

`Route HTTP` names the catalogue category; a route row reads `Route
exposée` or `Route appelée`. `Collection` is the kind of a MongoDB data
resource; `Donnée` stays the generic word of rule 6 above and of the
`Données` node-type filter. A message channel keeps the `Message` label of
its graph card (`docs/spec-fonc/port-rendering.md`); the `canal de
messages` label of the table above is not yet used in the export.

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
