# Delivery practices for AI-assisted work

Parent: [AGENTS.md](../../AGENTS.md).

This page holds the operating practices that `AGENTS.md` routes to. They
apply to every agent session on this repository. The rules of `AGENTS.md`
(document ownership, invariants, validation commands) stay authoritative;
this page says how a session runs and how work is declared done.

## Framing

1. A non-trivial request starts with a [request brief](request-brief.md)
   validated by the user before any file changes.
2. A backlog or plan written by an agent is a proposal. It carries the status
   `proposed` until the user has read its baseline, reordered its lots if
   needed, and named the tasks that change a contract. Only then does a task
   start.
3. A change to a rule of `docs/UX-UI.md`, a functional contract, or an
   accepted ADR starts with the decision record, validated by the user:
   an ADR in `docs/ADR.md` for a technical choice, an entry in
   `docs/PDR.md` for a product choice. Implementation follows the record.

## Status vocabulary

A task or backlog item carries exactly one of these states:

| State | Meaning |
|---|---|
| `proposed` | Written by an agent, not yet validated by the user. |
| `open` | Validated, not started. |
| `in progress` | Started; the delivery record names the commit range. |
| `blocked: <reason>` | Cannot be validated as its acceptance requires; the reason names the missing prerequisite (lab unreachable, Chrome absent, no second application). |
| `delivered` | Acceptance verified with the prerequisite the acceptance names. |

`delivered` is never used for work whose field validation (lab cluster,
browser, second application) could not run. Such work is `blocked` with the
reason, even when its unit tests pass, so it stays visible until the
prerequisite exists.

## Defects

Every fixed defect leaves a regression test in the same commit, in a module
whose name ends with `_regressions.py` when the defect belongs to an
extractor or a discovery rule, or next to the existing tests of the surface
otherwise. The test reproduces the defect on a minimal fixture and names
the rule it protects.

## Specification conformance

`tests/test_spec_conformance.py` checks that every flag named in
`docs/spec-fonc` exists in the CLI, that every visible CLI flag is
documented, and that every control the UX contract names is rendered by the
export. A gap owned by a backlog task is listed in the test with the task
identifier; the entry is removed in the commit that delivers the task.

## Session hygiene

1. Compact the conversation when context becomes large or before a new
   substantial phase. Preserve the objective, decisions, changed files,
   unresolved risks, and validation results in the handoff.
2. Before compaction, leave a short checkpoint so work resumes without
   rereading the repository.
3. After compaction, re-check the latest user request; treat it as
   authoritative and do not repeat completed work unless validation
   requires it.
4. Prefer small, reversible edits, inspect the diff, and run proportionate
   focused tests before reporting progress or completion.
5. Never claim browser, network, or external-system validation when the
   prerequisite was unavailable; state the unverified risk explicitly and
   use the `blocked` state.
6. When a session crosses several work phases, remind the user of the
   options: compact, record a checkpoint, split unrelated work into a new
   thread, preserve the validation status.
7. Use Archify only to analyze or visualize Python projects, never to
   replace SystemLens exports or infer facts SystemLens has not indexed.
8. Before a material action, state what you will do. After it, state what
   changed, which files or systems, and which validation ran.

## Cross-functional review

Before delivering a non-trivial change, examine it from the relevant points
of view and state any material trade-off or unverified risk:

1. **Product:** does it serve a concrete user task and preserve the intended
   scope and terminology?
2. **UX:** is the primary path discoverable, progressively disclosed, and
   usable at constrained viewport sizes? (Keyboard operation and ARIA stay
   deferred; see `AGENTS.md`.)
3. **Development and QA:** are public contracts preserved, edge cases
   covered, and behavior verified beyond string-level output when
   interaction is involved?
4. **Architecture and data quality:** are static facts, confidence levels,
   ambiguity handling, and compatibility with existing indexes explicit?
5. **Operations and security:** are offline/network dependencies,
   performance, local-path disclosure, and deployment consequences
   understood?

Apply only the perspectives relevant to the change. Do not use this
checklist as a substitute for proportionate implementation and tests.
