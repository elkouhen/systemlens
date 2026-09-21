# Documentation index

SystemLens keeps product intent, observable behavior, implementation constraints,
and architecture decisions in separate documents. Start with the document that
owns the question, then follow its links to implementation evidence.

## Choose a document

| Question | Start here |
|---|---|
| What problem does SystemLens solve? | [Product requirements](PRD.md) |
| Which commands, MCP tools, and exports are public? | [Functional specification](SPEC-FONC.md) |
| How do indexing, storage, extraction, and rendering work? | [Technical specification](SPEC-TECH.md) |
| Why was an architectural choice made? | [Architecture decisions](ADR.md) |
| How should maintainers navigate the code? | [Architecture map](ARCHITECTURE.md) |
| What does the AI graph manifest contain? | [AI graph manifest](AI-GRAPH.md) |
| How are internal flows diagnosed? | [Internal flow diagnosis](DIAGNOSE-INTERNAL-FLOWS.md) |

## Source ownership

The PRD owns product scope and success measures. The functional specification
owns observable behavior. The technical specification owns implementation
constraints and persisted data contracts. ADRs own durable decisions.

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
