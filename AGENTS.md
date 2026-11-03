# AGENTS.md: How to navigate and maintain this project's documentation

This file is for any agent working on `systemlens`. It points to the right
documents and summarizes the documentation hygiene expected in this repository.

## Document map

| Document | Content | When to read it |
|---|---|---|
| [`README.md`](README.md) | Entry point: positioning, installation, quickstart, MCP setup | First stop for onboarding or user-facing repo updates |
| [`docs/README.md`](docs/README.md) | One-line map of every project Markdown file | When choosing which document to read for a task |
| [`docs/PRD.md`](docs/PRD.md) | Problem, vision, personas, scope, success metrics | When you need product intent and scope boundaries |
| [`docs/SPEC-FONC.md`](docs/SPEC-FONC.md) | Observable behavior: CLI commands, flags, error messages, MCP tools, skill workflows | Before changing anything a user or agent sees |
| [`docs/SPEC-TECH.md`](docs/SPEC-TECH.md) | Modules, data model, SQLite schema, algorithms, JSON contract | Before changing internal architecture in `src/systemlens/` |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Package ownership, dependency direction, maintainer navigation | Before moving code across packages or changing an interface |
| [`docs/UX-UI.md`](docs/UX-UI.md) | HTML presentation, graph controls, selection, and camera behavior | Before changing the generated interface |
| [`docs/ADR.md`](docs/ADR.md) | Architecture decisions: context, choice, consequences | Before revisiting an existing technical choice |
| [`docs/PDR.md`](docs/PDR.md) | Product decisions: visible rules, vocabulary, defaults, deferred concerns, and whether each is open to revision | Before changing a rule a user sees |
| [`docs/process/delivery-practices.md`](docs/process/delivery-practices.md) | Session hygiene, status vocabulary, defect rule, review checklist, request brief | At the start of a session and before declaring work done |
| [`BACKLOG.md`](BACKLOG.md) | Proposed graph viewport navigation fixes V1 to V6, the items carried over from earlier backlogs, and the index of archived backlogs | When changing graph pan, zoom, or the published live model, picking up a carried-over item, or finding archived plans |
| [`docs/plans/async-write-read-consistency.md`](docs/plans/async-write-read-consistency.md) | Remaining work on asynchronous write and synchronous read consistency | When changing consistency diagnostics |
| [`docs/plans/data-operations-in-internal-flows.md`](docs/plans/data-operations-in-internal-flows.md) | Scoped data-operation plan, status, and acceptance criteria | When working on data operations in internal flows |
| [`docs/plans/runtime-observations-in-export.md`](docs/plans/runtime-observations-in-export.md) | Elastic APM overlay in the HTML export | When showing runtime observations in an export |
| [`docs/plans/call-graph-external-dispatch-scope.md`](docs/plans/call-graph-external-dispatch-scope.md) | Proposed correction C0 to C5 of the source engine's external-interface dispatch (ADR-70 regression) | When changing source-engine dispatch or measuring call-graph edges against CodeQL |
| [`docs/plans/resource-inspector-uniformisation.md`](docs/plans/resource-inspector-uniformisation.md) | Delivered plan R1 to R6 (PDR-10) for one `Inspecter` behaviour across catalogue resource kinds | When changing what `Inspecter` opens from the resource catalogue |
| [`../systemlens-skill/`](../systemlens-skill/) | Companion skill repo: agent workflow, bundled rule packs, operational guidance | Whenever a SystemLens change can affect the skill or its docs |
| [`../systemlens-observability-lab/`](../systemlens-observability-lab/) | Infrastructure and a test application for observability/integration testing | When validating SystemLens against a real running app/infra, or working on observability-related integration tests |

`../systemlens-skill/` and `../systemlens-observability-lab/` are separate
repositories (`https://github.com/elkouhen/systemlens-skill` and
`https://github.com/elkouhen/systemlens-observability-lab`), expected as
siblings of this repository in a multi-repo workspace. If a directory is
absent from the current checkout, skip the rules that reference it rather
than treating it as a broken link.

`README.md` stays intentionally short. The specifications and ADRs hold the
authoritative detail.

## Skill routing

Load these project skills when the task matches their trigger:

- [`writing-for-agents`](.agents/skills/writing-for-agents/SKILL.md): When
  creating or editing an agent skill, `AGENTS.md`, `CLAUDE.md`, or a document
  that routes agents to project context. Keep context pointers short.
- [`codebase-design`](.agents/skills/codebase-design/SKILL.md): When designing
  a module interface, choosing a seam, or planning an architecture refactor.
  Use its vocabulary when updating the technical specification or an ADR.
- [`playwright-cli`](.agents/skills/playwright-cli/SKILL.md): When inspecting,
  reproducing, or reviewing an interaction in the generated HTML export. Run
  the browser integration test under Validation for regression coverage.

## Source-of-truth and conflict resolution

Apply requirements in this order: explicit user and platform safety
requirements, accepted ADRs, functional and technical specifications, tests,
then the current implementation. The PRD provides product intent when those
sources leave a product decision open.

Do not silently choose when a specification, test, and implementation disagree.
Report the conflict, preserve the public contract unless the requested change
explicitly alters it, and update the affected source of truth and regression
test together.

## Documentation maintenance rules

1. Write and update user-facing documentation in English, including
   `README.md`, `docs/`, generated-document templates, and report indexes.
2. Keep `README.md` focused on onboarding and day-to-day usage.
3. Update `docs/SPEC-FONC.md` in the same change as any CLI, MCP, or skill
   behavior change.
4. Update `docs/SPEC-TECH.md` in the same change as any architectural or data
   model change.
5. Update the authoritative documentation in the same pass whenever an
   algorithm changes, including its invariants, fallback behavior and
   complexity or performance implications when relevant.
6. Record durable design decisions in `docs/ADR.md` instead of leaving them only
   in commit messages.
7. If a change in SystemLens affects the companion skill, update
   `../systemlens-skill/` in the same pass.
8. Keep changes consistent with the existing codebase and ensure the code is
   correct, including its behavior, contracts, and edge cases.
9. Wrap documentation prose at about 80 characters per source line. Keep each
   paragraph to five short lines where practical and never more than ten.
   Split longer explanations at natural thought boundaries without changing
   their technical meaning. Apply the same limit to prose in list items;
   code blocks and tables are separate structures.
10. Load the `technical-writing` skill before writing or restructuring
    technical prose, and run its review checklist before delivery.

## Non-negotiable engineering invariants

1. Persist source evidence only as paths relative to the indexed project root.
   Resolve local paths only at export time through `--root-path`; never persist
   WSL or machine-specific source roots.
2. Preserve compatibility with existing SQLite indexes. Schema migrations must
   be additive where possible and occur before the index transaction.
3. Exports consume the persisted architecture snapshot; they must not silently
   re-parse source files or turn unresolved dynamic facts into guessed static
   dependencies.
4. Keep extraction conservative: retain ambiguity and confidence in persisted
   facts instead of inventing a target or relationship.
5. Never place credentials, tokens, or unredacted secret values in persisted
   facts, generated exports, fixtures, or documentation.

## Validation

Use the project environment and commands below as the canonical validation
path. Set up the development environment first when needed:

```bash
uv sync --group dev
```

For Python changes, run focused tests and Ruff:

```bash
uv run ruff check src tests
uv run pytest tests/path/to_relevant_test.py
```

Run the full default suite for public contracts, storage migrations, extractor
changes, or before a release:

```bash
uv run mypy
uv run pytest
```

When changing generated HTML interactions, also run the browser integration
test when Chrome is available:

```bash
SYSTEMLENS_CHROME_BIN=/path/to/chrome uv run pytest -m slow tests/test_browser_export.py
```

When a CLI, indexing, flow, graph-contract, or companion-skill change can affect
the documented agent workflow, validate the development checkout against both
sibling repositories:

```bash
uv run python scripts/check_companion_contracts.py
```

This check validates the skill metadata, local links, and JSON examples; copies
the Java lab application to a temporary directory; creates a fresh index there;
verifies its potential flows; and exports an HTML graph. It must never modify
the laboratory checkout or reuse its persisted index.

If the prescribed environment or command is unavailable, do not substitute an
unverified setup silently: report the missing prerequisite and the validation
that could not be performed.

When regenerating the observability-lab supermarket HTML graph, use
`../systemlens-observability-lab/apps/supermarket-demo/architecture-ia.html` as
the POC source, then copy that generated file to
`docs/models/simple-supermarket.html` in this repository. The latter is the
documentation site's checked-in live model and must stay synchronized with the
`architecture-ia.html` POC export.

## Delivery practices

[`docs/process/delivery-practices.md`](docs/process/delivery-practices.md)
holds the session hygiene rules, the cross-functional review checklist, the
status vocabulary of backlog items, and the defect rule. The rules that
gate work are:

1. A non-trivial request starts with a
   [request brief](docs/process/request-brief.md) the user validates.
2. A backlog or plan an agent writes stays `proposed` until the user
   validates it; no task starts before that.
3. A change to a UX rule, a functional contract, or an ADR starts with the
   decision record (`docs/ADR.md` or `docs/PDR.md`), validated first.
4. `delivered` requires the validation the acceptance names; otherwise the
   item is `blocked: <reason>`.
5. Every fixed defect leaves a regression test in the same commit.
6. `tests/test_spec_conformance.py` keeps the CLI specification and the UX
   contract aligned with the code; a gap is listed there with its task.

## Deferred HTML accessibility and delivery concerns

Until explicitly reprioritized by the user, do not propose, implement, or
block work on the generated HTML export's CDN dependencies, ARIA semantics, or
keyboard navigation. These concerns are intentionally deferred and must not
expand the scope of an otherwise requested UX or graph-rendering change.
