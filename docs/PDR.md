# Product decision records

Parent: [Product requirements](PRD.md).

Technical choices are recorded in [ADR.md](ADR.md). This file records
product choices: rules a user sees, vocabulary, defaults that shape the
experience, and concerns deliberately deferred. Each record says whether it
is open to revision and who decides. A record whose rationale was not
written down when the choice was made says so, rather than inventing one.

| ID | Decision | Open to revision |
|---|---|---|
| [PDR-1](#pdr-1-inspection-requires-shift--click) | Inspection requires Shift + click; a plain click selects | Yes, product owner |
| [PDR-2](#pdr-2-the-node-inspector-docks-beside-the-graph) | The node inspector docks beside the graph; wide views stay modal | Yes, product owner; see ADR-68 |
| [PDR-3](#pdr-3-the-export-speaks-french) | The export speaks French | No for the vocabulary; yes for individual labels |
| [PDR-4](#pdr-4-accessibility-and-cdn-concerns-are-deferred) | Accessibility and CDN concerns are deferred | Yes, by the user only |
| [PDR-5](#pdr-5-a-first-index-needs-no-external-tool) | A first index needs no external tool | Yes, product owner; see ADR-66 |
| [PDR-6](#pdr-6-the-minimap-stays-once-the-user-navigates) | The minimap stays once the user navigates | Yes, product owner |
| [PDR-7](#pdr-7-one-french-word-per-concept-in-the-export-and-the-cli) | One French word per concept in the export and the CLI | Yes, product owner; the glossary owns the list |
| [PDR-8](#pdr-8-a-resource-has-no-software-layer) | A resource has no software layer | Yes, product owner |
| [PDR-9](#pdr-9-one-inspector-skeleton-for-every-node-kind) | One inspector skeleton for every node kind | Yes, product owner; refines ADR-68 |
| [PDR-10](#pdr-10-inspecter-opens-one-surface-per-kind-of-fact) | `Inspecter` opens one surface per kind of fact | Yes, product owner; refines ADR-68 and PDR-9 |
| [PDR-11](#pdr-11-spring-conventions-add-possible-call-edges) | Proposed: Spring conventions add possible call edges | Yes, product owner; not validated |
| [PDR-12](#pdr-12-a-spring-data-rest-default-path-is-an-unverified-route) | A Spring Data REST default path is an unverified route | Yes, product owner |
| [PDR-13](#pdr-13-init-chooses-the-engine-and-the-strategy-separately) | `init` chooses the engine and the strategy separately | Yes, product owner |
| [PDR-14](#pdr-14-a-two-finger-scroll-pans-and-a-pinch-zooms) | A two-finger scroll pans and a pinch zooms | Yes, product owner |
| [PDR-15](#pdr-15-a-flow-ends-on-an-effect-and-rabbit-and-jms-publications-are-effects) | A flow ends on an effect; Rabbit and JMS publications are effects | Yes, product owner |
| [PDR-16](#pdr-16-rabbit-jms-events-and-startup-code-start-flows) | Rabbit, JMS, events and startup code start flows | Yes, product owner; to be confirmed |

## PDR-1: Inspection requires Shift + click

**Status:** Accepted before 2026-11-01; amended on 2026-11-01 when the
product owner validated backlog task U2. The original rationale was
reconstructed from the contract, not recorded at decision time.

**Decision:** Opening the introspection window of a node or a supported
relation requires `Shift + click`. Since 2026-11-01 a plain click on an
Architecture node selects it (details widget, emphasized relations, dimmed
unrelated nodes) without opening anything; a plain click on a relation still
does nothing, and a permanent hint at the bottom of the graph names the
three gestures (`docs/UX-UI.md`, "Navigation, selection, and inspection").

**Why, as far as the contract says:** a plain click on a crowded graph
risks opening a window by accident while panning or reading; the modifier
keeps the graph the primary workspace.

**Consequences:** the inspection path is named on screen, and a plain
click gives feedback instead of silence. Selection is a view state: it
changes no persisted fact.

## PDR-2: The node inspector docks beside the graph

**Status:** Accepted before 2026-11-01 as a modal, rationale not recorded;
amended on 2026-11-01 when the product owner validated backlog task U3
(ADR-68).

**Decision:** From 901 pixels of width, the inspector of a node, an HTTP
route or an HTTP call docks to the right edge and leaves the graph visible.
Contract, schema, DTO, entity and runtime views stay a centered modal over a
blurred graph, and every view is modal at 900 pixels or less. The header
owns identity, breadcrumb, back, and close in both layouts.

**Consequences:** the user reads a node and its neighbours side by side,
and the inspected node keeps a halo. Wide documents keep the room they need.

## PDR-3: The export speaks French

**Status:** Accepted before 2026-11-01.

**Decision:** The generated interface, its CLI messages, and the names of
its widgets are in French; the project documentation is in English
(`AGENTS.md`, documentation rule 1).

**Consequences:** an English label is a defect, not a choice; backlog
task U8 renamed the inspector's `Layer` heading to `Couche`. Palette names
such as `Obsidian` are documentation names, not interface labels: the theme
control reads `Sombre` and `Clair`.

## PDR-4: Accessibility and CDN concerns are deferred

**Status:** Accepted before 2026-11-01; owner: the user.

**Decision:** Work on the export's CDN dependencies, ARIA semantics, and
keyboard navigation is deferred until the user reprioritizes it. No task
may propose, implement, or block on these concerns (`AGENTS.md`, "Deferred
HTML accessibility and delivery concerns").

**Consequences:** UX tasks move and restyle controls without widening their
markup role; the review checklist of the delivery practices names keyboard
operation as deferred.

## PDR-5: A first index needs no external tool

**Status:** Accepted on 2026-10-31 with ADR-66.

**Decision:** The tree-sitter source engine is the default call-graph
engine; CodeQL is opt-in. A first `systemlens index` runs with the Python
package alone.

**Why:** CodeQL needs its CLI, a query pack, and a licence that may not
cover proprietary code; a silent fallback at run time hid that dependency.

**Consequences:** flows of a first index use the closed-world dispatch rules
of ADR-61; the companion skill passes the engine explicitly where CodeQL is
installed.

## PDR-6: The minimap stays once the user navigates

**Status:** Accepted on 2026-11-01 by the product owner, who reported that
the navigation minimap disappears.

**Decision:** After a user pan, wheel zoom, zoom control, or minimap move,
the minimap of the Architecture graph and of the call tree stays visible
until the next fit. A fit is `Tout le graphe`, `Vue lisible`, a layout or
filter refit, a tree refit, or opening another flow. The earlier size and
off-screen rules still show it before any navigation.

**Why:** the minimap hid as soon as every node centre was back inside the
graph area. On small graphs it blinked during a slow pan and vanished at
the moment the user wanted it to find their way back.

**Consequences:** a programmatic camera move, such as centring a selected
flow, does not count as navigation. Since BL-17 a window resize keeps a
camera the user moved, so the minimap stays; it refits, and may hide the
minimap, only a graph the user never moved.

## PDR-7: One French word per concept in the export and the CLI

**Status:** Accepted on 2026-11-01 by the product owner after a vocabulary
review of the export, the CLI help, and the glossary.

**Decision:** Every concept has one French label in the generated
interface and in the CLI, fixed by the `French label` column of
`docs/glossary.md`. The rules the review settled:

1. Technical loan words stay untranslated: `topic`, `DTO`, `snapshot`,
   `handler`, `namespace`, `checkpoint`, `pipeline`, `backend`, and
   product or protocol names. Everything else is French; `Lost update`
   reads `Perte de mise à jour`.
2. `microservice` names an indexed service everywhere. `service` survives
   only for the APM services of the runtime overlay, which are not
   necessarily indexed microservices.
3. `relation` names any link between two elements in the export. The CLI
   keeps `arête d'appel` for CodeQL call edges only. `arc` and `lien`
   leave the labels, and the English contracts say `edge`, as the
   glossary requires.
4. The node-type chip for topics reads `Topics`, not `Messages`. The
   relation types read `Appels d'API`, `Messages`, `Accès aux données`,
   and `Autres`.
5. `flux de code` names a catalogue entry, `arbre d'appel` its rendered
   view, and `chemin` the path searched between two nodes. `itinéraire`,
   `parcours`, and `graphe d'appel` leave the labels; the catalogue kicker
   reads `Flux potentiels, pas une trace d'exécution`.
6. `donnée` names a persisted data resource (collection, entity); flow
   steps read `Lecture de donnée` and `Écriture de donnée`; the Java
   classes bound to a resource are `classes de persistance`.
7. CLI help describes commands and options in the infinitive, writes
   `topic` in lower case, and the export uses the typographic apostrophe.

**Why:** the review found four words for a relation, two for a path, both
`service` and `microservice`, an English `flows` command group, and a node
chip and a relation type sharing the label `Messages`. A reader cannot
tell whether two labels name one concept or two.

**Consequences:** command, option, and JSON key names are a public
contract and keep their spelling. A renamed contractual label is updated
in `docs/UX-UI.md`, `tests/test_spec_conformance.py`, and the export in
the same change. The four-mode navigation drift from the UX contract is a
separate backlog concern, not a vocabulary one.

## PDR-8: A resource has no software layer

**Status:** Accepted on 2026-11-01 by the product owner, who reported a
Kafka topic shown with a software layer in the graph inspector.

**Decision:** A topic or a collection is a resource, not a module. Its
inspector shows no `Couche` row; its Architecture section shows only its
owner module. A microservice keeps its `Couche` row when its layer is
known.

**Why:** the software layer is a property of a module. The inspector gave
a resource the layer of its owner module, so a topic published by a module
without a layer convention read `Couche : Application`, which is none of
the conventional layers (`api`, `orchestration`, `domain`, `infrastructure`,
`persistence`, `shared`). Unknown owners fell back to the English `Unknown`.

**Consequences:** the `docs/UX-UI.md` sentence "A resource's layer and
module remain available in its details" becomes "A resource's module
remains available in its details". The JSON `architecture_layer` field on
resource nodes is unchanged: it records the owner layer used to order owner
candidates. The software-layer model keeps its `Application` pseudo-layer
for modules that start an application.

## PDR-9: One inspector skeleton for every node kind

**Status:** Accepted on 2026-11-01 by the product owner, after a review
of the inspector of each node kind on the supermarket lab export.

**Decision:** The inspector of every node kind has the same header and
lists its groups in one order: `Relations`, `Trafic observé`,
`Architecture`, `Ports d’intégration`, `Flux associés`, `Flux internes`,
`Kubernetes`, `Sources`, `Diagnostics`. A header without a badge is not
drawn.

A group is defined for a node kind only when it can carry content for
that kind; an undefined group is not listed. The ADR-68 rule still applies
to the groups a kind defines: an empty `Flux associés` of a microservice
stays listed with a zero count.

For a resource (topic, collection, JPA entity, enriched resource),
`Relations` holds its links to microservices, DTOs, and persistence
classes. `Architecture` holds its module and its technology, plus the
metadata of an enriched resource. `Sources` holds its evidence files.
A resource lists only the groups it has content for.

**Why:** an architect comparing a topic, a collection, and a JPA entity
found three layouts. Each resource opened on an `Architecture` group at
zero, the entity used its own `Ressource JPA` group, a topic listed its
source files among its relations, and an entity without badges kept an
empty badge frame.

**Consequences:** `Trafic observé` and `Diagnostics` stay microservice
groups. `Ressource JPA` and `Ressource enrichie` leave the inspector; the
entity name moves out of the body since the title names it. The
`docs/UX-UI.md` empty-group sentence is refined accordingly. The JSON
contract is unchanged: sources read the existing location fields of
endpoints, persistence classes, and entities.

## PDR-10: `Inspecter` opens one surface per kind of fact

**Status:** Accepted on 2026-11-01 by the product owner, after a review of
`Inspecter` on one catalogue row of each kind on the supermarket lab
export (plan
`docs/plans/resource-inspector-uniformisation.md`, in Git at `4a12ad5`).
Amended on 2026-11-01 by the product owner: the catalogue preview hides
while an inspector opened from the catalogue is open. Amended again on
2026-11-01 by the product owner: a DTO and a persistence class (Mongo
class, JPA entity field view) open a docked resource inspector; only
contracts keep the centered modal.

**Decision:** `Inspecter` opens one of four surfaces, chosen by the kind
of fact and not by the code path that produced the row:

| Fact | Surface | Skeleton |
|---|---|---|
| A node of the architecture graph (microservice, topic, collection, JPA entity, enriched resource) | docked node inspector | PDR-9 skeleton |
| A relation end (served route, HTTP call) | docked relation inspector | header strip (method, path, client count), one row of actions including `Centrer dans le graphe` on the provider, groups `Relations` (provider, clients, DTO REST) and `Sources` (evidence, code source) |
| A DTO or a persistence class (Mongo class, JPA entity field view) | docked resource inspector | header strip (kind or DTO role, owner, field count), one row of actions with `Ouvrir dans VS Code` when a source exists and `Centrer dans le graphe` on the related node (a DTO's topic, else its producer; a Mongo class's collection, else its microservice; a JPA entity itself), groups `Champs`, `Relations`, and `Sources` |
| A contract (OpenAPI or AsyncAPI) | centered modal | title `<row kind label> · <name>`, one row with the VS Code action when a source exists, then the embedded viewer |

The catalogue stays visible under a docked inspector and is restored
unchanged when a modal closes. While the inspector or modal opened by
`Inspecter` is open, the catalogue preview is hidden, the result list takes
its width, and the row stays selected. Any close (`×`, `Escape`, backdrop)
brings back the same preview and its back history. An inspector opened from
the graph leaves the preview alone.

The inspector title reuses the catalogue's kind label (`Route exposée`,
`Route appelée`, `Collection`, `Entité JPA`, `DTO`, `Classe de persistance
MongoDB`). A `Route appelée` opens the call inspector of the caller, never
the route inspector with the caller as provider.

A JPA entity row opens the node inspector, like a collection: both are
graph nodes, and the entity's field view stays one click away through
`Classe de persistance`. The Mongo persistence-class row is not a node; it
opens the docked resource inspector.

A DTO or persistence-class inspector keeps its field navigation in the
docked panel: a field type that references another DTO or Mongo class
opens it in place, and the header back action returns to the containing
one. A view without fields says so (`Aucun champ indexé.`).

**Why:** the `Inspecter` button was uniform, but what it opened was not. A
route replaced the catalogue with its owner's details, a called route named
the caller as its provider with `0 client`, titles used two or three words
for one kind, and the three modal views placed the VS Code action in three
different ways, or omitted it.

The second amendment follows a review of the DTO view. A DTO or a
persistence class is mostly relations (topics, producers, consumers,
collection, microservice), not a wide document; the modal hid the
catalogue and the graph that those relations point to. Only the contract
viewers need the full width.

**Consequences:** `docs/glossary.md` lists the kind labels that titles and
catalogue tags share. A node title no longer repeats the technology
(`Collection · order_fulfillments`, not `Donnée · MongoDB ·
order_fulfillments`); the Architecture group still names it. The DTO role
(`DTO de message`, `DTO REST`) moves from the title to the header strip.
Swagger UI and the AsyncAPI viewer are not restyled; only the chrome around
them changes. The JSON contract and persisted facts are unchanged.

## PDR-11: Spring conventions add possible call edges

**Status:** Proposed on 2026-11-03 for task K8 of the
call-graph coverage plan (backlog item BL-22). Not validated; the
product owner dropped the item from the backlog on 2026-11-07.
Points 1 and 4 are replaced by PDR-16 (BL-49).

**Problem:** Some calls happen through Spring, not through Java
dispatch. A `publishEvent(event)` reaches the `@EventListener` methods
whose parameter type matches the event, and an injected interface may
have one implementation selected by `@Qualifier` or `@Primary`. Java
semantics alone cannot prove either, so the call graph shows no edge for
the first and every implementation for the second.

**Proposed rule:**
1. `ApplicationEventPublisher.publishEvent(x)` gets a `possible` edge to
   every `@EventListener` or `@TransactionalEventListener` method, in the
   caller's visible modules, whose single parameter type is the static
   type of `x` or one of its ancestors.
2. An injected field or constructor parameter annotated `@Qualifier("n")`
   keeps only the implementation whose bean name is `n`. A
   `@Primary` implementation is kept alone only when it is the single
   `@Primary` candidate. Any doubt keeps every candidate.
3. `@Profile` and `@ConditionalOn*` never narrow candidates: they depend
   on the runtime configuration.
4. `@PostConstruct` methods and `CommandLineRunner` or `ApplicationRunner`
   implementations become flow entry points only if task K0 shows them
   on the reference projects; otherwise this point is withdrawn.

**Why:** these edges are what a reader of a Spring application expects to
see, and keeping them `possible` respects the conservative rule. The
narrowing of point 2 removes false candidates only when the annotation
proves it.

## PDR-12: A Spring Data REST default path is an unverified route

**Status:** Accepted on 2026-11-06 with lot 2 of the
review backlog (`BACKLOG.md` in Git at `4a12ad5`, finding P9), whose fix the product
owner validated.

**Problem:** A Spring Data REST repository without an explicit `path`
is served at the plural of its entity name. Spring computes that plural
with an inflector that knows irregular words: `Child` is served at
`/children`, `Analysis` at `/analyses`. SystemLens applied three regular
rules and showed `/childs` and `/analysises` as certain routes.

**Rule:** The default collection path keeps the regular spelling, and
the route and its search resources are dynamic: the export marks them as
unverified, as any other route SystemLens could not resolve. A path given
by `@RepositoryRestResource(path = ...)` stays a resolved route.
SystemLens adds no irregular-plural table.

**Why:** a table would always miss a word, and the route would look
certain again. A dynamic route still links to its callers, so the common
regular case keeps its edges, with a visible warning.

## PDR-13: `init` chooses the engine and the strategy separately

**Status:** Accepted on 2026-11-07 by the product owner.

**Problem:** `systemlens init --profile NAME` chose only the call-graph
engine, under a name that suggested more. A project that wanted Strategy1
had to edit `config.yml` by hand after `init`.

**Decision:** `systemlens init` takes two independent options:

1. `--engine source|codeql|joern`, default `source`. The values are the
   engines of the registry, without `none`. `codeql` or `joern` writes
   that engine's `analysis.engines.NAME` defaults and warns when its
   prerequisite is missing.
2. `--strategy default|strategy1`, default `default`. It writes
   `analysis.strategy`.

`--profile` is removed, with no alias and no deprecation path. An unknown
value exits with code 2 and writes nothing; `init` never overwrites an
existing configuration. The `config.yml` format is unchanged.

**Why:** the six combinations of three engines and two strategies each
deserve a ready configuration, and two options name what they set. The
product has a single user, so no compatibility path is kept.

**Consequences:** the defaults keep ADR-5/5a (Strategy1 is opt-in) and
ADR-66 (`source` needs no external tool). Engine declarations lose their
`init` profile flag: every declared engine can be chosen (ADR-85).

## PDR-14: A two-finger scroll pans and a pinch zooms

**Status:** Accepted on 2026-11-07 by the product owner (BL-18).

**Problem:** on both graph surfaces every vertical wheel delta zoomed and
a horizontal delta did nothing. On a Mac trackpad the graph could not be
panned without a click and drag.

**Decision:** on the Architecture graph and on the selected call tree:

1. A pinch, a wheel event with `ctrlKey`, zooms around the pointer.
2. A whole-line or page delta (`deltaMode` line or page, a classic mouse
   wheel) zooms around the pointer.
3. Any other pixel delta, such as a two-finger trackpad scroll,
   horizontal or vertical, pans the graph by that delta.

Both zooms use the one step of BL-16: about 1.2 times per notch.

**Known limit:** a browser cannot tell a fine-grained pixel mouse wheel
from a trackpad, so that wheel pans. `Ctrl` + wheel zooms with any mouse.

**Consequences:** the zoom controls and the minimap are unchanged. A
pinch zooms at the same rate per wheel delta as a notch.

## PDR-15: A flow ends on an effect, and Rabbit and JMS publications are effects

**Status:** Decided by the product owner on 2026-11-08 (BL-50).

**Problem:** a route whose calls reach no integration point, such as
`GET /ping → PingService.compute → MathHelper.square`, has persisted call
edges and no flow. The call-chain diagnostic reports it as `aucune suite
d'appel`. Calls to a broker SystemLens does not model end nowhere too, so
the flows that publish through them are dropped.

**Decision:**
1. A flow goes from a trigger to an effect. An entry-only call tree is not
   persisted as a flow, whatever its depth. Its dead end stays visible in
   the call-chain diagnostic.
2. Two message publications become effects, with a `message_publish`
   step and an endpoint of role `produce`:
   - `RabbitTemplate` and its interface `AmqpTemplate`, `send` and
     `convertAndSend`, system `rabbitmq`;
   - `JmsTemplate`, `send` and `convertAndSend`, system `jms`.
3. The channel is named as a Kafka topic is: a literal, a constant or a
   resolvable property; anything else is dynamic. A Rabbit publication on
   the default exchange is named by its routing key, which is the queue
   name. A publication to a named exchange is named
   `exchange/routing-key` and pairs with no queue, since SystemLens does
   not read exchange bindings. A JMS publication is named by its
   destination name. A call that names no channel is dynamic.
4. `ApplicationEventPublisher.publishEvent` is not an effect: PDR-16 makes
   it a hop into the listeners.

**Out of scope:** Redis, Elasticsearch, R2DBC, S3 and SQS calls remain
outside the effect list. A flow that ends on one of them is not persisted.

**Why:** a flow answers "what does this trigger change outside the
method". A tree without an effect answers nothing a call graph does not
already show, and listing it would multiply flows on every helper route.
Missing effects are an extractor gap, fixed one technology at a time.

**Consequences:** routes that publish through Rabbit or JMS gain their
flows. The `GET /ping` probe keeps no flow; a regression test asserts it.

## PDR-16: Rabbit, JMS, events and startup code start flows

**Status:** Accepted on 2026-11-08 under the product owner's mandate to
implement the internal-flow backlog; to be confirmed by the product
owner (BL-49). It replaces points 1 and 4 of the proposed PDR-11.

**Problem:** a flow starts only at a REST route, a Kafka consumption or a
`@Scheduled` method. On the flow probe, `@RabbitListener`,
`@JmsListener`, `@EventListener` and `CommandLineRunner.run` have call
edges to a publisher and no flow.

**Decision:**
1. `@RabbitListener` and `@JmsListener` methods are consume endpoints,
   like `@KafkaListener`, with a `message_entry` step. The channel is the
   `queues` value of a Rabbit listener or the `destination` value of a
   JMS listener, resolved as a Kafka topic is. A class-level
   `@RabbitListener` gives its endpoint to each `@RabbitHandler` method.
2. A Rabbit or JMS consumer pairs with a publication of the same system
   and the same concrete channel name (PDR-15) in another module. The
   pair is a topology relation, `publishes_to`, as for Kafka. Dynamic
   names never pair.
3. `@EventListener` and `@TransactionalEventListener` methods are
   in-process event consumers. They are internal hops, not topology
   endpoints. `publishEvent(x)` on an `ApplicationEventPublisher` or an
   `ApplicationContext` gets a `possible` call edge to every listener
   whose event type is the static type of `x` or one of its ancestors.
   The event type is the `classes` or `value` attribute, else the single
   parameter. An untyped `x` gives no edge. The publisher's flow
   continues through the listener to its effects instead of ending.
4. An event listener is also a transient trigger, with an `event_entry`
   step named `@EventListener(Type)`. Its flows exist even when the
   publisher is unknown, such as a framework event.
5. `CommandLineRunner.run`, `ApplicationRunner.run` and `@PostConstruct`
   methods are transient triggers, with a `startup_entry` step named
   `CommandLineRunner.run`, `ApplicationRunner.run` or `@PostConstruct`.
   Like `@Scheduled`, a transient trigger starts flows and adds no
   topology endpoint.

**Out of scope:** `main`, `@SqsListener`, Spring Cloud Stream functional
beans, `RouterFunction` handlers, `@MessageMapping`, gRPC services, Spring
Batch and Quartz jobs start no flow.

**Known limit:** the HTML architecture graph draws Kafka topics only. A
Rabbit or JMS relation is in the persisted topology and the CLI; the
export shows its steps in the flows, not as a channel node.

**Why:** these are the entries a Spring reader expects, and each maps to
an existing shape: a broker consumer to a Kafka consumer, a startup hook
or a listener to a schedule. An event stays in-process, so a topology
node would show a channel that does not exist. The edge stays `possible`
because Spring dispatches through a runtime registry and listener
conditions.

**Consequences:** the probe's Rabbit, JMS, event and runner entries yield
their flows. Projects with runners or `@PostConstruct` seeders gain flows,
mostly to data writes.
