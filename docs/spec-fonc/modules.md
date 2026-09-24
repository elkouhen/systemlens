# Module rendering rules

Parent: [Functional specification](../SPEC-FONC.md).


The module view MUST preserve these visual invariants:

- Module membership MUST use the same resolver for placement and for the
  visible module rectangle.
- A Topic, message channel, Data resource, or other resource MUST be
  assigned first to the module of its producing microservice, using the
  incoming source relation. A consumer module MUST NOT move the resource
  into its module. Resources without an identifiable producer remain in
  `ROOT`.
- Resources inside one module MUST be placed on a grid with a positive
  horizontal and vertical gap greater than the projected card size.
- Within each module, microservices MUST occupy the first
  sub-layer and Topics, Data, and other resources MUST occupy a second
  sub-layer below them. Empty sub-layers are omitted. This ordering is conveyed
  by placement only; the renderer MUST NOT add visible `Microservices` or
  `Resources` sub-layer labels inside the module.
- Module rectangles MUST be packed with a positive gap based on their full
  rendered envelope, including card, title, and padding margins, and MUST NOT
  overlap each other.
- When a second grouping level is displayed, each parent module MUST be the
  union of its visible child module rectangles plus its own title/padding
  margin. Parent bounds MUST contain the complete child boxes; the
  non-overlap rule applies between sibling modules, not between a parent and
  its descendants.
- A project-group parent MUST remain attached to the module of its owning
  projects and MUST contain only those owning projects. Resources MUST remain
  in the module of their producing microservice; relation targets
  MUST NOT be added as children of the structural parent or enlarge it across
  unrelated modules.
- Module bounds MUST be calculated from graph-coordinate bounds and projected
  after camera changes. Parent bounds MUST be recomputed from the projected
  child bounds, so zooming cannot make a child escape its parent or make the
  hierarchy drift.
- The layout MUST NOT depend on the software-layer order. Narrow viewports MAY
  use additional rows to keep modules inside the visible graph area.

Microservices with no indexed inter-service relation remain visible in a
separate isolated area of the graph, so their absence of dependencies is not
confused with an absent service.

Microservices, Topics, and Data resources are marked by their
relative connectivity. In the HTML graph, the shape identifies the resource
type. Microservices, Topics, and Data resources use a coloured
outline around a neutral interior; the fill never carries connectivity or risk
meaning.

The relation count includes their indexed API, Topic, and Data dependencies,
while low/medium/high relative tiers are calculated separately for each
resource type.

For a microservice, the count is its distinct direct
API clients and targets, Topic producer/consumer relations, and Data
relations; multiple HTTP routes between the same client and target
are counted once.

The HTML complexity badge exposes the HTTP, topic, and
Data breakdown as a tooltip, along with the resource rank and its soft
tercile bounds. The lowest third is blue, the middle third orange, and the
highest third red; the terciles are recalculated separately for each resource
type in every export. The graph label of each coloured resource also displays
its relative connectivity through its coloured outline; the exact details are
available after selecting the node.

The Explore search suggests indexed resource names and accepts either one
exact, unambiguous graph-node name or a topic itinerary written with `->`.

An itinerary starts and ends with a microservice and follows only directed topic
relations through Topics; it never traverses APIs or Data dependencies.

When a microservice and another resource have the same display name, a direct
resource search remains ambiguous, while an itinerary endpoint resolves the
unique microservice candidate required by the itinerary grammar.

Invalid, ambiguous, repeated, or unreachable stops leave the current graph
unchanged and produce an actionable message. The itinerary detail is an
ordered, clickable list of node names and types. A Topic lists its associated
DTO names in parentheses. Selecting any path stop reveals its ordinary detail
view, including the indexed Topic source links where present.

Every graph resource detail starts with one relation count when every indexed
relation is visible. When filters hide relations, it instead distinguishes the
indexed and visible counts. A `Relations` section follows. For a microservice,
that section separates
consumed and published API and Topic resources, plus Data resources. Each
microservice resolved to an indexed Maven or Gradle project also provides a
visible action that opens the module root directory in VS Code. Topics list
their applicable DTOs. A collapsed `Sources` section lists the indexed OpenAPI
and Topic files that provide the evidence, avoiding repetition in every
topic.

At constrained viewport sizes, the empty context panel is hidden. Once it
contains selected-node or path details, it replaces the Explorer controls and
starts at the top of the panel content. Its normal controls remain interactive
inside the single left-panel scroll region.

Indexing persists source evidence only as paths relative to the project root.
HTML export joins those paths to `--root-path` (the current directory by
default) when building VS Code links. No WSL distribution or absolute local
source path is stored in the index.

When several modules reference one OpenAPI/Swagger file, it is listed once for
the module that directly contains the source file.

MongoDB collection details list the services using the collection once, followed
by indexed Java persistence classes resolved from
`@Document`, Mongo repository entity generics, or unambiguous `Type.class`
arguments passed to `MongoTemplate`. They include their qualified name, source
location, collection, owning microservice, module, and declared fields without
repeating the collection in the inspector summary. The Persistence view provides the same inventory
with filtering by class, package, collection, or service and opens a dedicated
inspector. Fields whose type resolves uniquely to another indexed project class
are navigable recursively; the inspector provides a return action to the
containing class. External and ambiguous field types remain plain text.

Persistence classes declared in a dependent build project are attached to the
owning service collection through the indexed project-dependency graph. When
dependency metadata is unavailable, a workspace-wide class is used only if it
is the unique candidate for that collection name; ambiguous candidates remain
unassociated rather than being guessed.

The Topic detail lists resolved DTOs once. It lists message types only
when no matching DTO has been resolved, avoiding duplicate published and
consumed type lists when they describe the same contract.

Indexing issues that have a source endpoint expose a VS Code link to the
associated file and line. The HTML export provides dedicated OpenAPI, Topics,
and Data views. OpenAPI and Topics both support
filtering their complete list (OpenAPI by path or service, DTOs by simple name
or package); Persistence filters by class, package, collection, or service. A
persistent inventory status reports whether unresolved indexing facts exist and
opens their review view.

`--strategy strategy1` selects the Strategy1 profile for every indexing pass
and adds opt-in convention extraction for selected
`getTopics()` accessors and `envoyerMessageKafka*(kafkaProperties.getTopics().getXxx(), payload[, ...])` calls.
The second argument is always the DTO, including the three-argument form;
this applies to `envoyerMessageKafka`, `envoyerMessageKafkaRequest` and
`envoyerMessageKafkaReply`.
`${kafka.topics.*.name}` expressions and configured REST client constants. It
also enables the `getXxxServiceUrl()` REST target-name convention.

For a `src/main/resources/openapi/xxx.rest` publication declaration, it
searches the entire indexed repository for same-named `xxx.yaml`,
`xxx.yml`, or `xxx.json` OpenAPI contracts, including contracts in a sibling
shared project without an `openapi-generator` Maven configuration. It also
searches every YAML or JSON document below
`model-xxx/src/main/resources/openapi/`, so that module may contain several
contracts with distinct names. Only a valid OpenAPI document is attached, and
its resulting endpoints remain attributed to the module that owns the `.rest`
declaration.

Independently of Strategy1, each build project inventories every valid YAML or
JSON OpenAPI document under its own `src/main/resources/openapi/` directory;
contract file names do not need to follow an `openapi.*` or `swagger.*`
pattern.

Topic conventions never create synthetic request/reply relations. Kafka
relations are derived only from indexed producers, consumers, concrete topics,
and compatible payload evidence.
