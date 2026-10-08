# Documentation index

SystemLens keeps each cross-cutting aspect in one owner document. Start with
the aspect document, then follow its links to detailed sections, code, and
tests. A detail page may explain one part of an aspect, but it does not create
a second source of truth.

## Aspect documents

| Aspect | Owner | Owns | Does not own |
|---|---|---|---|
| Product | [Product requirements](PRD.md) | Users, purpose, scope, outcomes, and success measures | Command syntax, data structures, or visual rules |
| Functional behavior | [Functional specification](SPEC-FONC.md) | CLI, MCP, export behavior, extraction contracts, and compatibility | Internal module boundaries or visual styling |
| Technical design | [Technical specification](SPEC-TECH.md) | Architecture constraints, persistence, data contracts, algorithms, and verification | Product scope or interaction semantics |
| Code structure | [Code architecture guide](ARCHITECTURE.md) | Package ownership, dependency direction, and maintainer navigation | Public behavior and persisted data contracts |
| UX/UI | [UX/UI rules](UX-UI.md) | Visible presentation, widgets, selection, inspection, layout, and camera behavior | Extraction, persistence, and product scope |

## Supporting documents

| Need | Document |
|---|---|
| Why a durable architectural choice was made | [Architecture decisions](ADR.md) |
| How tests are organized and validated | [Test strategy and validation report](TESTING.md) |
| What the AI graph manifest contains | [AI graph manifest](AI-GRAPH.md) |
| How internal flows are diagnosed | [Internal flow diagnosis](DIAGNOSE-INTERNAL-FLOWS.md) |
| How to run a full CodeQL index and describe flows | [Full CodeQL indexing and flow descriptions](prompts/full-index-codeql-flow-descriptions.md) |

## Change rules

The owner table above is the responsibility map. When a change crosses several
aspects, update each owner document in the same change. Do not copy the same
rule into a second owner document. Link to the owner instead.

## Terminology

Use `architecture topology` for the complete inventory of services, topics,
APIs, and resources. Use `inter-service interaction graph` for the relations
between microservices, including asynchronous Kafka exchanges. Use `flow graph`
for one selected path through that topology. Reserve `method call graph` for
the Java method relationships resolved by CodeQL or an equivalent engine.

Tests verify these sources. The current implementation is the fallback source
only when the authoritative documents and tests leave a detail unspecified.

## Change documentation with code

Update the owning document in the same change as the code. Add an ADR when the
change introduces a durable architectural choice. Update the relevant
regression test when a public behavior or invariant changes.

Keep paragraphs short, use headings that name their content, and separate
verified facts from proposals. Store detailed material in the section file that
owns it instead of duplicating it in this index.
