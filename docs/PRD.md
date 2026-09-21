# Product requirements: SystemLens

## Product summary

SystemLens provides a local architecture inventory for Java and Spring
codebases. The product preserves evidence and uncertainty instead of guessing
dependencies.

| Audience | Primary outcome |
|---|---|
| Codebase analyst (primary) | Bounded, evidenced context before a change or review |
| Developer | A navigable inventory of services and integrations |
| Architect | A reviewable topology with visible uncertainty |

## Product purpose

SystemLens gives analysts, developers, and architects local architecture
context before they change or review a Java or Spring system. It derives
source facts from local ASTs without starting an external rule engine or
sending source code to a service. When CodeQL is installed locally, it also
creates a temporary source-only call graph for bounded inter-method flows.

The product answers questions such as:

- Which services expose or call an API?
- Which topics are produced and consumed, and with which payload type?
- What are the dependencies and likely impact paths between services?
- Which Maven/Gradle projects, OpenAPI contracts, Data resources and
  Spring properties belong to a service?

When deterministic extraction leaves a bounded gap, an analyst may use the
companion SystemLens skill to direct an agent through a focused, evidence-based
review. Its reviewable, namespaced JSON fact manifest is the durable handoff
for complementary facts. Importing that manifest never replaces source-derived
evidence.

## Users and primary workflows

| User | Need | Surface |
|---|---|---|
| Codebase analyst (primary) | Establish proven dependencies, impact and unresolved facts before a change or review | CLI catalog commands, HTML export, and MCP |
| Developer | Inspect the evidence behind a service, API, topic, project or architecture module | CLI catalog commands and HTML export |
| Architect | Review topology, uncertainty and static architecture risks across services | `analyze`, graph export |

The primary workflow is `systemlens init`, `systemlens doctor`, and
`systemlens index`. The analyst then uses the catalog or graph for the
question at hand. The optional companion skill can produce a reviewable JSON
fact manifest when deterministic extraction leaves a bounded gap. The analyst
can import that manifest into its own namespace before exporting or querying
the merged model.

The `coverage`, `indexing-issues`, and `audit` commands provide optional
diagnostics for inventory completeness, extraction issues, and static topology
risk. Reindex after an edit. Indexing is incremental, while `--full` refreshes
every eligible source file. The complete command and MCP contracts belong to
the [functional specification](SPEC-FONC.md).

## Scope boundaries

### Delivered

- Tree-sitter Java AST extraction for Spring MVC/WebFlux, Feign,
  RestTemplate/WebClient, Spring Cloud Gateway and Spring Data REST endpoints.
- Topic producers and consumers, dynamic-topic evidence and explicit Java
  payload types.
- Maven/Gradle project discovery, OpenAPI and Data inventory.
- Local SQLite persistence, architecture relations, graph/audit views and
  workspace federation.
- Markdown/JSON Kafka manifests and the opt-in Strategy1 conventions.
- Optional Kubernetes Deployment and StatefulSet resource dimensions from the
  active local `kubectl` context, matched conservatively to indexed projects.
- Persisted potential code flows from an API or topic entry point to external
  effects located in the same Java method.

### Planned

- Conservative presentation of observed API, Topic, Data, and S3 activity
  beside the static architecture, without inventing a source mapping.
- Explicit Kubernetes capacity context for runtime hotspots where a verified
  workload-to-service match exists. A future matcher must prefer an exact name,
  then accept a unique token-bounded service-name inclusion in a Deployment or
  StatefulSet name; ambiguous matches remain unresolved.

### Not delivered

- Security or quality scans, severity filtering or automated remediation.
- Guaranteed resolution of dynamic values; unresolved values remain explicitly
  marked as dynamic rather than guessed.

## Product requirements

1. Source facts must be derived from local AST parsing and deterministic local
   configuration only. Optional Kubernetes enrichment is opt-in, retains its
   workload kind, namespace, and name, and never replaces source evidence.
2. Each source fact must carry enough evidence to navigate to its file and line
   range. Every non-source enrichment must identify its acquisition origin.
3. A changed or deleted source file must update or remove its facts on the next
   index run.
4. The local inventory and its CLI and MCP queries must operate without network
   access once indexing is complete. Kubernetes discovery is an opt-in network
   exception.
5. Graph, catalog and audit output must make uncertainty visible rather than
   inventing a dependency.
6. A person analysing a codebase must be able to obtain a bounded answer,
   evidence and unresolved facts before changing or reviewing a supported
   Java/Spring integration. The optional companion skill may enrich the graph
   through reviewable agent analysis.

## Success measures

- On each supported reference repository, a user can answer the primary
  change or review questions (service dependencies, APIs, topic flow and
  impact) after one local index, with a bounded result and its evidence.
- Every emitted source integration is traceable to a concrete source location
  or an explicitly named manifest entry; optional Kubernetes facts identify the
  matched workload kind, namespace, and name.
- Coverage output distinguishes resolved relations from unresolved or dynamic
  facts, so an analyst can decline to assume a missing dependency.
- Incremental indexing touches only changed files unless an extractor signature,
  selected convention, or an analysis dependency (Spring configuration or build
  descriptor) changes.
- For a selected runtime window, a developer can identify the highest-ranked
  latency and error hotspots, determine the completeness of the observation,
  and navigate only to explicitly linked static evidence.

For observable command and MCP contracts, see the
[functional specification](./SPEC-FONC.md). For implementation details, see
the [technical specification](./SPEC-TECH.md). Historical decisions,
including the retired external-analyzer design, remain in
[ADR.md](./ADR.md).
