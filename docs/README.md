# Documentation index

SystemLens keeps each cross-cutting aspect in one owner document. Start with
the aspect document, then follow its links to detailed sections, code, and
tests. A detail page may explain one part of an aspect, but it does not create
a second source of truth.

## Aspect documents

| Aspect | Owner | Owns | Does not own |
|---|---|---|---|
| Product | [Product requirements](PRD.md) | Users, purpose, scope, outcomes, and success measures | Command syntax, data structures, or visual rules |
| Functional behavior | [Functional specification](SPEC-FONC.md) | CLI, export behavior, extraction contracts, and compatibility | Internal module boundaries or visual styling |
| Technical design | [Technical specification](SPEC-TECH.md) | Architecture constraints, persistence, data contracts, algorithms, and verification | Product scope or interaction semantics |
| Code structure | [Code architecture guide](ARCHITECTURE.md) | Package ownership, dependency direction, and maintainer navigation | Public behavior and persisted data contracts |
| UX/UI | [UX/UI rules](UX-UI.md) | Visible presentation, widgets, selection, inspection, layout, and camera behavior | Extraction, persistence, and product scope |

## Markdown file map

Use this map to select a file before reading its full contents. The summaries
route readers to the owner; they do not define behavior or implementation.
The map covers project documentation. Skill copies installed for the Claude,
Grok, and Kiro harnesses live under `.claude/skills/`, `.grok/skills/`, and
`.kiro/skills/`; read a skill there only when its task applies.

### Repository entry points

| File | Read when |
|---|---|
| [README.md](../README.md) | Installing SystemLens or finding the first CLI workflow. |
| [AGENTS.md](../AGENTS.md) | Applying repository rules, documentation ownership, or validation gates. |
| [BACKLOG.md](../BACKLOG.md) | Implementing the multi-diagnostic consistency view or finding previous plans. |
| [BACKLOG.previous.md](../BACKLOG.previous.md) | Consulting the historical root backlog preserved from Git; use its linked plans for current status. |

### Documentation owners and guides

| File | Read when |
|---|---|
| [docs/README.md](README.md) | Finding the owner of a documentation topic. |
| [docs/PRD.md](PRD.md) | Checking users, product scope, or success measures. |
| [docs/SPEC-FONC.md](SPEC-FONC.md) | Finding a public behavior contract. |
| [docs/SPEC-TECH.md](SPEC-TECH.md) | Finding an implementation or data contract. |
| [docs/ADR.md](ADR.md) | Checking why an architectural decision was accepted. |
| [docs/ARCHITECTURE.md](ARCHITECTURE.md) | Finding package ownership, dependencies, or call paths. |
| [docs/UX-UI.md](UX-UI.md) | Changing visible HTML presentation or interaction. |
| [docs/glossary.md](glossary.md) | Choosing canonical names for architecture concepts. |
| [docs/DIAGNOSE-INTERNAL-FLOWS.md](DIAGNOSE-INTERNAL-FLOWS.md) | Investigating a missing internal code flow. |
| [docs/microservices-bad-practices.md](microservices-bad-practices.md) | Reviewing the feasibility of additional microservice diagnostics. |

### Functional sections

| File | Read when |
|---|---|
| [docs/spec-fonc/ai-graph-manifest.md](spec-fonc/ai-graph-manifest.md) | Creating, importing, or exporting an AI graph manifest. |
| [docs/spec-fonc/cli.md](spec-fonc/cli.md) | Changing CLI commands, options, or output. |
| [docs/spec-fonc/configuration.md](spec-fonc/configuration.md) | Changing configuration files or accepted values. |
| [docs/spec-fonc/extraction-rules.md](spec-fonc/extraction-rules.md) | Changing observable endpoint, relation, or flow extraction. |
| [docs/spec-fonc/html-export.md](spec-fonc/html-export.md) | Changing HTML export content or view behavior. |
| [docs/spec-fonc/incrementality.md](spec-fonc/incrementality.md) | Changing refresh and freshness behavior. |
| [docs/spec-fonc/modules.md](spec-fonc/modules.md) | Changing module membership or navigation. |
| [docs/spec-fonc/port-rendering.md](spec-fonc/port-rendering.md) | Changing port evidence or selected-flow arcs. |

### Technical sections

| File | Read when |
|---|---|
| [docs/spec-tech/algorithms.md](spec-tech/algorithms.md) | Changing indexing, graph, flow, or consistency algorithms. |
| [docs/spec-tech/architecture.md](spec-tech/architecture.md) | Changing the snapshot pipeline or delivery adapters. |
| [docs/spec-tech/data-model.md](spec-tech/data-model.md) | Changing persisted facts or graph data contracts. |
| [docs/spec-tech/export-snapshot.md](spec-tech/export-snapshot.md) | Changing how exports consume the persisted snapshot. |
| [docs/spec-tech/extractors.md](spec-tech/extractors.md) | Changing Java and Spring extraction internals. |
| [docs/spec-tech/graph-layout.md](spec-tech/graph-layout.md) | Changing coordinates, camera mechanics, or layout fallbacks. |
| [docs/spec-tech/indexing.md](spec-tech/indexing.md) | Changing index orchestration or flow reconstruction. |
| [docs/spec-tech/persistence.md](spec-tech/persistence.md) | Changing SQLite schema, migrations, or compatibility. |
| [docs/spec-tech/verification.md](spec-tech/verification.md) | Selecting tests, quality checks, or external test prerequisites. |

### Plans, procedures, examples, and fixtures

| File | Read when |
|---|---|
| [docs/plans/data-operations-in-internal-flows.md](plans/data-operations-in-internal-flows.md) | Working on the scoped data-operation plan and its acceptance criteria. |
| [docs/plans/async-write-read-consistency.md](plans/async-write-read-consistency.md) | Working on stale-read detection, visibility ordering, or key matching. |
| [docs/prompts/full-index-codeql-flow-descriptions.md](prompts/full-index-codeql-flow-descriptions.md) | Asking an agent to index with CodeQL and describe every flow. |
| [docs/archive/layered-view.md](archive/layered-view.md) | Investigating historical layer and module rendering rules. |
| [examples/README.md](../examples/README.md) | Importing or regenerating synthetic diagnostic examples. |
| [examples/supermarket/README.md](../examples/supermarket/README.md) | Regenerating the synthetic supermarket dataset. |
| [tests/fixtures/maven_workspace/not-a-service/README.md](../tests/fixtures/maven_workspace/not-a-service/README.md) | Understanding the Maven non-module fixture. |

## Change rules

The owner table above is the responsibility map. When a change crosses several
aspects, update each owner document in the same change. Do not copy the same
rule into a second owner document. Link to the owner instead.

## Terminology

[Glossary](glossary.md) owns the canonical vocabulary, naming distinctions, and
capitalization rules. The specifications remain authoritative for behavior,
data contracts, and interaction rules.

Tests verify these sources. The current implementation is the fallback source
only when the authoritative documents and tests leave a detail unspecified.

## Change documentation with code

Update the owning document in the same change as the code. Add an ADR when the
change introduces a durable architectural choice. Update the relevant
regression test when a public behavior or invariant changes.

Keep paragraphs short, use headings that name their content, and separate
verified facts from proposals. Store detailed material in the section file that
owns it instead of duplicating it in this index.
