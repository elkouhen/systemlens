# Plan: cover every Java dependency shape in the call graph

Status: validated on 2026-11-03 by the product owner, who kept the lot
order. Lots 0 and 1 are `open`. Lots 2, 3 and 4 wait for their decision
records, validated before any code. Section 7 records each task's state.
Owner: SystemLens implementer. Reviewers:
product owner and engine maintainer.

Baseline: commit `2e42b93` (ADR-71), `source` engine in closed world.
Each status below comes from a minimal Java fixture run through
`JavaSymbols.fallback_calls` on 2026-11-03; the fixtures are listed in
section 6. Frequencies on real projects are not measured yet (task K0).

Parent rules: [ADR-62](../ADR.md#adr-62-attribute-anonymous-class-bodies-to-the-creating-method),
[ADR-70](../ADR.md#adr-70-give-the-source-engine-codeqls-interface-witness-and-dispatch-gaps),
[ADR-71](../ADR.md#adr-71-scope-external-interface-dispatch-to-the-callers-visible-modules),
[indexing specification](../spec-tech/indexing.md), invariant 4 of
[AGENTS.md](../../AGENTS.md) (conservative extraction).

## 1. Problem

The product owner asked for the list of dependency shapes the call graph
must handle, and a plan to cover them all. A review of the `source` engine
found nine defects and four minor gaps, spread over inheritance, generics,
confidence rules and missing caller kinds. No document lists the shapes
the engine claims to support, so a gap is found only by accident.

## 2. Dependency shapes

Status values: `covered`, `defect` (wrong edge or wrong confidence),
`missing` (no edge), `out of scope` (kept unresolved on purpose).

### 2.1 Static binding

| # | Shape | Status | Evidence |
|---|---|---|---|
| S1 | Call on a declared receiver: field, local, parameter, `var`, chained return | `covered` | existing tests |
| S2 | Static call and static import | `defect` | `Base.util()` also reaches `Sub.util()`, which only hides it |
| S3 | Unqualified call, outer class from an inner class | `covered` | fixture 27 |
| S4 | Private method | `defect` | `Base.go()` also reaches the private `Sub.go()` |
| S5 | Overload chosen by argument type, including a subtype | `covered` | fixtures 14b, 14c |
| S6 | Two methods of one type declared on the same line | `defect` | both methods are dropped from the symbol table |
| S7 | Constructor call `new X(...)`, `this(...)`, `super(...)` | `missing` | constructors are not methods of the graph |
| S8 | Calls inside a constructor, an initializer or a field initializer | `missing` | these bodies are never callers |

### 2.2 Inheritance and dispatch

| # | Shape | Status | Evidence |
|---|---|---|---|
| H1 | Inherited method called on a subclass receiver | `covered` | fixtures 4, 13, 38, 43 |
| H2 | Override on the declared receiver; virtual dispatch over subclasses | `covered` | fixtures 5, 6, 7, 37 |
| H3 | Template method: `step()` or `this.step()` from the base class | `covered` | fixtures 1, 1b, 19, 42 |
| H4 | `super.m()` declared by the direct parent | `covered` | fixture 3 |
| H5 | `super.m()` declared by a grandparent | `defect` | no edge |
| H6 | `super.m()` with same-arity overloads | `defect` | two `possible` edges instead of one exact edge |
| H7 | Interfaces: chains, abstract intermediate class, inherited body, `default` and `static` methods | `covered` | fixtures 8, 8b, 15, 16, 17, 41, 50 |
| H8 | Records implementing an interface | `covered` | fixture 28 |
| H9 | Enum constant bodies | `missing` | only the abstract declaration is reached |
| H10 | External interface implemented in the sources | `covered` | ADR-71 |
| H11 | External class extended in the sources (`Thread`, servlet filters) | `missing` | no dispatch to the source override |

### 2.3 Generics

| # | Shape | Status | Evidence |
|---|---|---|---|
| G1 | Generic interface implemented with a concrete type | `covered` | fixture 9 |
| G2 | Generic superclass extended with a concrete type | `covered` | fixtures 31, 32 |
| G3 | Generic implementation `class Generic<T> implements Handler<T>` | `missing` | only the witness edge to the interface |
| G4 | Receiver typed by a bounded type variable `<T extends Shape>` | `missing` | no edge |

### 2.4 Receiver typing

| # | Shape | Status | Evidence |
|---|---|---|---|
| T1 | Cast, record accessor, Lombok getter, array element | `covered` | fixture 35, existing tests |
| T2 | Pattern variable of `instanceof Circle c` | `missing` | `c.area()` is not resolved |
| T3 | Pattern variable of a `switch` pattern | not tested | to add to the fixture corpus |

### 2.5 Functional and implicit implementations

| # | Shape | Status | Evidence |
|---|---|---|---|
| F1 | Lambda and method reference as call sites | `covered` | fixtures 36, 48 |
| F2 | Lambda or method reference implementing a source interface | `defect` | the call stays `exact` on the one named implementation |
| F3 | Anonymous subclass overriding a source method | `defect` | the call stays `exact` on the base body |
| F4 | JDK callbacks: `Executor`, `Callable`, `Runnable` | `covered` | ADR-71 |

### 2.6 Module boundaries

| # | Shape | Status | Evidence |
|---|---|---|---|
| M1 | External-interface dispatch limited to visible modules | `covered` | ADR-71 |
| M2 | Source-type dispatch from a library into a module that depends on it | `defect` | fixture 51; none on train-ticket today |
| M3 | Same qualified type in two modules, one visible | `defect` | stays `possible` although the dependencies decide it |

### 2.7 Framework-mediated calls (Spring)

| # | Shape | Status | Evidence |
|---|---|---|---|
| W1 | Injected field typed by an interface | `covered` | as H2 or H7 |
| W2 | `@Qualifier`, `@Primary`, `@Profile`, `@ConditionalOn*` narrowing | `missing` | every implementation stays a candidate |
| W3 | `ApplicationEventPublisher.publishEvent` to `@EventListener` | `missing` | no edge |
| W4 | `@Async` method | `covered` as a plain call | no asynchronous marker |
| W5 | Entry points: `@Scheduled` | `covered` | transient trigger |
| W6 | Entry points: `@PostConstruct`, `CommandLineRunner`, `ApplicationRunner` | not measured | to check in K0 |
| W7 | Spring Data derived query | `covered` | unimplemented-contract rule |
| W8 | Generated implementation (MapStruct, OpenAPI client) | not measured | to check in K0 |

### 2.8 Out of scope

| # | Shape | Reason |
|---|---|---|
| X1 | Reflection, `ServiceLoader`, `Class.forName` | Invariant 4: a dynamic target is never guessed. |
| X2 | AOP advice and proxies (`@Transactional`, aspects) | No business call; the advised method is already the target. |
| X3 | HTTP and Kafka between services | Flow transitions built by the extractors, not call edges. |

## 3. Invariants

1. A shape that changes a confidence rule or adds an edge kind starts with
   an ADR, validated before the code.
2. No rule invents a target. A shape that cannot be proved stays
   unresolved or `possible`.
3. Every fixed defect leaves a regression test in the same commit, in the
   fixture corpus of K1.
4. Persisted names and the `codeql_*` keys keep their shape. A schema
   change, if a lot needs one, is additive.
5. On train-ticket, no lot drops a flow that the parent commit finds,
   unless the plan names the flow as a false positive.

## 4. Tasks

### K0. Measure the shapes on the reference projects

Count each shape of section 2 on train-ticket and on the lab with the
tree-sitter parser, not with text search. Record the counts in section 2.

- Files: a script under `scripts/`; this plan.
- Acceptance: every row of section 2 has a count on both projects.

### K1. Build the fixture corpus and the shape contract

Turn the section 6 fixtures into one parametrized test module, one case
per shape, with the expected edges and confidences. A case whose shape is
not delivered is marked as an expected failure naming its task. Add a
"Call shapes" section to `docs/spec-tech/indexing.md` that lists the
shapes and their status.

- Files: `tests/test_call_graph_shapes.py`, `docs/spec-tech/indexing.md`.
- Acceptance: the module runs in the default suite; every `defect` and
  `missing` row is an expected failure with its task identifier.

### K2. Fix the static-binding and `super` defects (lot 1)

Exclude static and private methods from the dispatch index (S2, S4).
Resolve `super.m()` through all ancestors of the direct parent and select
the overload by argument type (H5, H6). Key methods by declaration node,
not by line, so two methods on one line stay distinct (S6).

- Files: `src/systemlens/indexing/java_symbols.py`, K1 corpus.
- Acceptance: the five cases pass; train-ticket flows hold.

### K3. Lower the confidence of implicit implementations (lot 2)

Decision first: an ADR that extends ADR-70's prudence to source types. A
call on a source type stays `possible` when an anonymous class, a lambda
or a method reference can implement it (F2, F3).

- Files: `docs/ADR.md`, `java_symbols.py`, K1 corpus.
- Acceptance: the two cases pass; the change in flow confidence on
  train-ticket is recorded.

### K4. Apply the module scope to source types (lot 2)

Decision first: amend ADR-71 so every dispatch, not only external
interfaces, stays in the caller's visible modules (M2). Use the
dependencies to choose between two declarations of one qualified type
(M3). This task also covers the `codeql` engine, as task C6 of the
[dispatch-scope plan](call-graph-external-dispatch-scope.md) proposes.

- Files: `docs/ADR.md`, `java_symbols.py`, `code_flows.py`, K1 corpus.
- Acceptance: the two cases pass; no edge on train-ticket leaves the
  caller's dependencies with either engine.

### K5. Resolve generics and pattern variables (lot 3)

Index a body whose parameter is a type variable for dispatch by erasure
(G3). Type a receiver by the bound of its type variable (G4). Type the
variable of an `instanceof` or `switch` pattern (T2, T3).

- Files: `java_symbols.py`, `docs/spec-tech/indexing.md`, K1 corpus.
- Acceptance: the four cases pass; train-ticket flows hold.

### K6. Add enum bodies and external superclasses (lot 3)

Treat each enum constant body as a subtype of its enum (H9). Dispatch a
call on an external class to the source overrides of its subclasses,
always `possible`, in the visible modules (H11).

- Files: `docs/ADR.md` for H11, `java_symbols.py`, K1 corpus.
- Acceptance: the two cases pass; ADR-70's remaining CodeQL-only flow
  through `TSOStr.write` is rechecked.

### K7. Make constructors and initializers part of the graph (lot 3)

Decision first: an ADR, because a constructor becomes a node of the graph
with a persisted identity. A `new X(...)` call reaches the constructor,
and the calls inside constructors and initializers get a caller (S7, S8).

- Files: `docs/ADR.md`, `src/systemlens/indexing/integration_methods.py`,
  `java_symbols.py`, `docs/spec-tech/indexing.md`, K1 corpus.
- Acceptance: the two cases pass; existing indexes open without a full
  reindex error; the export shows constructor nodes as the UX contract
  states.

### K8. Cover the Spring-mediated calls (lot 4)

Decision first: a PDR entry, because these edges are conventions, not
Java semantics. The candidates are: `publishEvent` to the listeners of the
event type (W3), narrowing by `@Qualifier` and `@Primary` (W2), and the
entry points and generated implementations K0 finds frequent (W6, W8).

- Files: `docs/PDR.md`, `docs/ADR.md`, `java_symbols.py` or a new
  convention module, `docs/spec-fonc/extraction-rules.md`, K1 corpus.
- Acceptance: each delivered convention has its corpus case and its
  documented rule; an unproved narrowing keeps every candidate.

## 5. Delivery lots and validation

| Lot | Tasks | Why this order |
|---|---|---|
| 0 | K0, K1 | Measures the shapes and makes every gap a visible test. |
| 1 | K2 | Local defects with no contract change. |
| 2 | K3, K4 | Confidence and scope rules; each needs its ADR first. |
| 3 | K5, K6, K7 | New shapes; K7 changes the graph's node set. |
| 4 | K8 | Framework conventions; needs a product decision. |

Each task runs:

```bash
uv run ruff check src tests
uv run pytest tests/test_call_graph_shapes.py tests/test_source_engine.py tests/test_code_flows.py
```

Each lot closes with `uv run mypy`, `uv run pytest`,
`uv run python scripts/check_companion_contracts.py`, and a fresh index of
a train-ticket copy with both engines, compared with
`systemlens analyze compare-engines`.

## 6. Review fixtures

The fixtures of the 2026-11-03 review are in the session scratchpad and
are not kept. K1 rebuilds them from this list, one class set per row of
section 2, with every method on its own line so S6 does not mask a
result. Rough text counts on train-ticket, to be replaced by K0: about 40
anonymous classes, 41 subclasses of JDK or Spring classes, 23
`@PostConstruct` or runner entry points, one `super.m()` call and no
`publishEvent` call.

## 7. Delivery record

| ID | Outcome |
|---|---|
