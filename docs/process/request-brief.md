# Request brief

Parent: [Delivery practices](delivery-practices.md).

A request that changes behavior, a contract, a document structure, or more
than one package starts with a brief of five lines. The agent writes it from
the user's sentence and the repository context; the user validates or
corrects it before any file changes. A one-line request that only fixes a
typo, renames a symbol, or runs a documented command needs no brief.

## Template

```text
Goal:        <the outcome, in one sentence, from the user's point of view>
Persona:     <the PRD persona served, and the task they are doing>
Out of scope: <what the request does not change, named explicitly>
Acceptance:  <the observable signal that the work is done: a test, a command
              and its expected output, a capture, a measured value>
Constraints: <contracts that stay fixed, deferred concerns, data invariants>
```

The brief is written in the conversation, not in the repository. When the
work opens a backlog task, the task's acceptance criteria repeat the
acceptance line.

## Example

Request: "move all the CodeQL code into a dedicated module".

```text
Goal:        A maintainer finds every CodeQL concern under one package and
             the index service no longer carries engine-specific code.
Persona:     Architecture maintainer, refactoring without changing behavior.
Out of scope: CLI flags, persisted table and meta names, the registry entry
             name, the companion skill.
Acceptance:  ruff, mypy, the default suite and the companion contract check
             pass; the core-never-imports-CodeQL test still passes; the
             import graph stays acyclic.
Constraints: ADR-61 (engine seam), ADR-66 (source default); exports read the
             persisted snapshot only.
```

## When the brief changes a contract

If the brief touches a rule of `docs/UX-UI.md`, a functional contract, or an
accepted ADR, the brief names the rule and the work starts with the decision
record (an ADR for a technical choice, a [product decision](../PDR.md) for a
product choice), validated by the user before implementation.
