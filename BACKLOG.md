# Backlog

Status: written on 2026-11-07 from the review of the `source` engine
against CodeQL, and validated the same day by the product owner, who
asked for every item to be processed autonomously, with the decisions
taken on their behalf listed at the end of the run. The lot order
stands. Owner: SystemLens implementer. Reviewer: product owner.
Baseline: `main` at `e5243ea`. Delivered on 2026-11-07: BL-21, BL-22.
Each delivered item's Delivery line names its commit and test.

The review was never committed: its measurements are kept in Appendix A
so each item stands on its own. Item IDs continue the numbering of the
2026-11-07 backlog, whose items are delivered (`BACKLOG.md` in Git at
`b8277bc`); BL-19 and BL-20 were the dropped ADR-72 and ADR-73 items.

Statuses follow
[`docs/process/delivery-practices.md`](docs/process/delivery-practices.md).
A decision record named under "Depends on" is validated before any code.
Every item adds its shapes to `tests/test_call_graph_shapes.py`, updates
the [call shapes](docs/spec-tech/indexing.md#call-shapes) table and the
symbol-pass section of `docs/spec-tech/indexing.md`, and runs ruff, mypy,
the default suite and the companion check.

Reference projects: train-ticket at `~/examples/train-ticket` and the lab
application `apps/supermarket-demo`. Neither contains the shapes below,
so every item also keeps train-ticket at 406 flows and 1712 edges with
both engines, the lab at 23 flows, and
`systemlens analyze compare-engines --engines source,codeql` at zero
differences.

## Goal

A call between two indexed methods gives the same `(callee, confidence)`
rows with the `source` engine, in closed world, as with the query of
`engines/codeql/queries.py`, once the join has attributed anonymous
callers to their creating method (ADR-62). The target is CodeQL on the
projection SystemLens builds: Java sources only, `build-mode=none`, the
JDK modelled, every other library type unresolved. A target is never
invented (AGENTS.md invariant 4): a shape the sources cannot prove stays
unresolved or `possible`.

## Summary

| ID | Title | Priority | Status |
|---|---|---|---|
| [BL-21](#bl-21) | Make every alignment gap a visible engine-parity test | P1 | `delivered` |
| [BL-22](#bl-22) | Fix three documentation drifts on engine confidence and counts | P2 | `delivered` |
| [BL-23](#bl-23) | Type `var` loop variables, ternaries, pattern variables and `X.this` | P1 | `open` |
| [BL-24](#bl-24) | Type lambda parameters and method references from a source functional interface | P2 | `open` |
| [BL-25](#bl-25) | Resolve unqualified calls inside anonymous classes of fixed-member external types | P2 | `open` |
| [BL-26](#bl-26) | Substitute source type arguments into type-variable returns, fields and bounds | P1 | `open` |
| [BL-27](#bl-27) | Type the element of a JDK container accessor chain | P1 | `open` |
| [BL-28](#bl-28) | Emit the declared row beside several possible implementations | P2 | `open` |
| [BL-29](#bl-29) | Narrow a declared type to the type created once | P3 | `open` |
| [BL-30](#bl-30) | Keep a dispatch open when a lambda or an anonymous class implements the type | P3 | `open` |
| [BL-31](#bl-31) | Dispatch a call on an external class to its source subclasses | P3 | `open` |
| [BL-32](#bl-32) | Apply ADR-81 rule 1 in the CodeQL query and the Joern export | P3 | `open` |

Priority reads: P1 adds edges CodeQL finds on everyday modern Java
(`Optional`, streams, `var`, generics, patterns) without touching a
confidence rule; P2 is a smaller or rarer gain, or a documentation fix;
P3 changes what `exact` means, or reopens an item the product owner
dropped on 2026-11-07, and waits for a decision record.

## Lot 0: visibility

<a id="bl-21"></a>
### BL-21. Make every alignment gap a visible engine-parity test

- **Problem:** the gaps between the `source` engine and CodeQL are known
  only from a session's probe corpus; train-ticket and the lab contain
  none of the shapes, so `compare-engines` cannot show them.
- **Impact:** a regression or a delivery on one shape is invisible until
  someone rebuilds the probes.
- **Cause:** `tests/test_call_graph_shapes.py` runs the symbol pass only
  and has no case for the shapes of Appendix A.
- **Acceptance:** one fixture holds the 21 shapes of Appendix A, tagged
  as the shape test tags its targets. A default test runs the symbol pass
  in closed world on it; a `slow` test, skipped without the CodeQL CLI,
  reads the raw rows of `CallGraphSession.calls()` and asserts the same
  `(caller, call line, callee, confidence)` set after anonymous callers
  are attributed by call line. Each row of an undelivered item is a
  strict expected failure naming the item. The call-shapes table of
  `docs/spec-tech/indexing.md` gains the new rows with their status.
- **Depends on:** none.
- **Status:** `delivered`.
- **Delivery:** `tests/test_engine_parity.py`: 25 cases over rows A1 to
  B5, each with an `item` field; 24 strict expected failures and one
  pass (A10 one arity) in the default suite, 25 passes in the `slow`
  CodeQL test (CodeQL 2.27.3), whose rows match Appendix A.2 exactly.
  B2's anonymous case expects no row: CodeQL reaches only the anonymous
  `hook`, which the join drops. `test_every_parity_row_is_documented`
  ties the cases to the engine-parity table of `indexing.md`. Commit:
  `6851a77`.
- **Why P1:** every other item is measured against this test.

<a id="bl-22"></a>
### BL-22. Fix three documentation drifts on engine confidence and counts

- **Problem:** three statements no longer match the code.
- **Impact:** a reader of the functional specification expects `low`
  confidence from the `source` engine and reads stale measurements.
- **Cause:** `docs/spec-fonc/extraction-rules.md` says source-symbol
  calls "remain `potential` with low confidence"; ADR-61 gives a route of
  exact edges `medium`, and the comparison measures 406 `medium` flows.
  The Joern section of `docs/spec-tech/indexing.md` reports 1524 edges
  for the three engines; the figure is 1712 since ADR-75. Call shape G3
  is `missing` while the pass reaches the body as `possible` (Appendix A,
  B5): a confidence defect.
- **Acceptance:** the three sentences are corrected;
  `tests/test_spec_conformance.py` and the call-shapes test pass.
- **Depends on:** none.
- **Status:** `delivered`.
- **Delivery:** the three sentences are corrected, each checked against
  the code: flow confidence in `code_flows.py`, the ADR-75 table, and
  the G3 case, whose expected failure now names BL-26.
  `tests/test_spec_conformance.py`, `tests/test_call_graph_shapes.py`
  and `tests/test_engine_parity.py` pass. Commit: `e9e392f`.
- **Why P2:** no wrong fact in the index, but a wrong contract in the
  specification.

## Lot 1: expression typing without a rule change

<a id="bl-23"></a>
### BL-23. Type `var` loop variables, ternaries, pattern variables and `X.this`

- **Problem:** four receiver forms have no type in the symbol pass, so
  their calls emit nothing, while CodeQL resolves each as `exact`.
- **Impact:** every call on `for (var c : circles)`, on
  `(flag ? a : b)`, on the variable of `instanceof Circle c` or of a
  `switch` pattern, and on `Outer.this` is a lost edge; a flow through
  such a call is missing.
- **Cause:** `receiver_type` and `expression_type` in
  `src/systemlens/indexing/java_symbols.py` handle identifiers, `this`,
  `super`, creation, casts, parentheses, array elements, field accesses
  and invocations; `_scoped_declaration` gives a `var` loop variable no
  type (indexing.md); call shapes T2 and T3 are `missing`.
- **Acceptance:** the four Appendix A rows A3, A4, A5 (T2 and T3) and A6
  pass in the parity test; a ternary whose branches have different types
  stays untyped; a `var` loop variable takes the element type the
  iterable's written type argument gives, through the BL-14 container
  view, and nothing otherwise.
- **Depends on:** BL-21.
- **Status:** `open`.
- **Why P1:** patterns and `var` are the everyday shapes of Java 17 code;
  pure typing, no confidence rule changes.

<a id="bl-24"></a>
### BL-24. Type lambda parameters and method references from a source functional interface

- **Problem:** an untyped lambda parameter is typed only when the lambda
  is a direct argument of a JDK callback (BL-14); a method reference is
  resolved only when its name has one declared arity.
- **Impact:** `register(c -> c.area())` with `register(Handler h)`, and
  `circles.forEach(sink::handle)` with two `handle` overloads, lose the
  edge to `Circle.area` and to `Sink.handle(Circle)`; CodeQL finds both
  `exact`.
- **Cause:** `_lambda_parameter_type` reads `_LAMBDA_PARAMETERS` for JDK
  containers only; `fallback_calls` requires `_declared_arities` to hold
  one value for a method reference.
- **Acceptance:** rows A9 and A10 pass. A lambda passed to a source
  method takes the parameter types of the single abstract method of the
  source functional interface declared at that position; a method
  reference binds to the overload whose arity is that of the target
  functional interface, source or JDK, and stays unresolved when the
  target is unknown.
- **Depends on:** BL-21.
- **Status:** `open`.
- **Why P2:** common in event and handler code, absent from the reference
  projects.

<a id="bl-25"></a>
### BL-25. Resolve unqualified calls inside anonymous classes of fixed-member external types

- **Problem:** in an anonymous class whose supertype is not in the
  sources, an unqualified call stays unresolved because that supertype
  may declare the name. Only `Runnable`, `Callable` and a few Spring
  interfaces are known to have fixed members (review D6).
- **Impact:** `helper()` inside `new Thread() { public void run() {
  helper(); } }` loses the edge to the enclosing class's `helper`; CodeQL
  attributes it `exact` to the anonymous `run`, and the join to the
  creating method. Train-ticket has 116 anonymous classes of external
  types and two such calls (`JWTUtil`).
- **Cause:** `_lexical_owner` stops at a scope with an external supertype
  unless it is in the fixed-member lists of `java_symbols.py`.
- **Acceptance:** row A8 passes for the JDK classes whose members are
  fixed (`Thread`, `TimerTask`, `Object`) and for the Spring adapter
  types the review lists (`WebMvcConfigurerAdapter`,
  `WebSecurityConfigurerAdapter`); a supertype outside those lists keeps
  the call unresolved. The lists are documented in `indexing.md`.
- **Depends on:** BL-21. A rule that resolves against any external
  supertype would need an ADR; this item does not propose it.
- **Status:** `open`.
- **Why P2:** frequent shape on train-ticket's configuration classes, but
  only two calls reach source methods there.

## Lot 2: generics

<a id="bl-26"></a>
### BL-26. Substitute source type arguments into type-variable returns, fields and bounds

- **Problem:** the symbol pass does no generic substitution: a method
  returning `T`, a field typed `T` and a receiver typed by a bounded type
  variable have no type ("generic return substitution unsupported" in
  `indexing.md`; call shapes G3 and G4 `missing`).
- **Impact:** `box.get().area()` with `Box<Circle> box`,
  `repo.find().area()` with `CircleRepo extends Repo<Circle>`, `value.area()`
  on a field inherited from `Box<Circle>`, `first(circles).area()` with
  `<T> T first(List<T>)`, and `s.area()` with `<T extends Shape>` emit
  nothing; CodeQL resolves the first four `exact` and the last as every
  implementation `possible`. A generic implementation
  `class GenericHandler<T> implements Handler2<T>` is reached `possible`
  where CodeQL says `exact`.
- **Cause:** `receiver_type` resolves a declared return type as written;
  `_type_parameters` only hides same-named source types; the dispatch
  index drops a body whose parameter is a type variable.
- **Acceptance:** rows A1 (four cases), A7 and B5 pass. Substitution reads
  the type arguments written on the receiver's field, local, parameter or
  `extends`/`implements` clause, and the arguments a generic method
  receives from typed arguments; a type variable without a written
  argument takes its bound, and no bound leaves it untyped. A body whose
  parameter is a type variable is indexed for dispatch by erasure and is
  `exact` under the ADR-81 rules when it is the one body.
- **Depends on:** BL-21.
- **Status:** `open`.
- **Why P1:** generic repositories, handlers and mappers are the backbone
  of service code; every call through them is lost today.

<a id="bl-27"></a>
### BL-27. Type the element of a JDK container accessor chain

- **Problem:** a call chained on `list.get(0)`, `optional.get()`,
  `map.get(k)`, `iterator.next()` or
  `list.stream().findFirst().get()` has no receiver type.
- **Impact:** each such call is a lost edge; train-ticket has 88 chains
  of this shape, which end on Lombok accessors there but on written
  service methods elsewhere. CodeQL types them from the JDK it models.
- **Cause:** `_generic_view` and `_GENERIC_CALLS` (BL-14) follow the
  container through `stream()`, `filter`, `findFirst`, `keySet()` and
  `values()` to type lambda parameters, but `receiver_type` never asks
  them for an element type.
- **Acceptance:** rows A2 (four cases) pass. `receiver_type` returns the
  element type when the chain ends on `get`, `orElse`, `orElseGet`,
  `orElseThrow`, `getFirst`, `getLast`, `next`, `getKey`, `getValue` or
  `findFirst().get()` of a container whose written type arguments the
  BL-14 view resolves; a raw or wildcard container, or a chain through a
  type-changing call such as `map`, gives nothing. The table in
  `indexing.md` lists the accessors.
- **Depends on:** BL-21.
- **Status:** `open`.
- **Why P1:** the most frequent lost shape on train-ticket and the idiom
  of every `Optional`-based service method.

## Lot 3: dispatch precision, each behind its decision record

<a id="bl-28"></a>
### BL-28. Emit the declared row beside several possible implementations

- **Problem:** with two or more implementations the pass emits the
  candidates only; CodeQL also emits the interface method as a declared
  `possible` row (third branch of `resolvedSingleOverload`). The pass adds
  the declaration only when the implementor set is incomplete (ADR-81).
- **Impact:** the exported call tree of a `source` index has no contract
  hop at such a site, against the parity goal of ADR-70; a `codeql` export
  of the same code shows it.
- **Cause:** `fallback_calls` in `java_symbols.py` prepends the
  declaration under the `incomplete` condition only; the witness is
  emitted only for an `exact` dispatch.
- **Acceptance:** row B4 passes: the declared row to the interface method
  joins the `possible` candidates, as a non-inferred edge; flows and
  their confidence are unchanged, as the ADR-70 witness is. Train-ticket
  has no such site, so its counts hold.
- **Depends on:** an amendment of ADR-70, validated first.
- **Status:** `open`.
- **Why P2:** export parity only; no flow changes.

<a id="bl-29"></a>
### BL-29. Narrow a declared type to the type created once

- **Problem:** the pass reads the written type of a variable; CodeQL
  narrows it by the type that flows into it.
- **Impact:** `Shape s = new Circle(); s.area()` and
  `private final Shape f = new Circle();` give `Circle.area` and
  `Square.area` `possible` with `source`, `Circle.area` `exact` with a
  witness under CodeQL. The spurious `Square` candidate opens a route the
  code cannot take.
- **Cause:** `variable_type` returns the declared type; no assignment is
  read.
- **Acceptance:** rows B1 (two cases) pass. A local assigned exactly once,
  at its declaration, with `new T(...)`, and a `final` field initialized
  once the same way, take `T` as receiver type; any other assignment, a
  parameter, or a non-final field keeps the declared type. The call is
  then `exact` only under the ADR-81 rules.
- **Depends on:** a new ADR, since it adds an `exact` rule.
- **Status:** `open`.
- **Why P3:** a precision gain the reference projects never exercise; it
  changes what `exact` means.

<a id="bl-30"></a>
### BL-30. Keep a dispatch open when a lambda or an anonymous class implements the type

- **Problem:** `Fn fn = () -> {}; fn.apply()` with one named `FnImpl`
  gives `FnImpl.apply` `exact`; `Base b = new Base() { void hook() {} };
  b.hook()` gives `Base.hook` `exact`. CodeQL resolves both to the
  anonymous body, which the join drops: the named body is at most
  `possible` (F2, F3).
- **Impact:** a false `exact` edge, and a flow confidence raised on a
  route the runtime never takes.
- **Cause:** ADR-62 attributes anonymous and lambda bodies to the creating
  method, so no indexed method stands for the runtime body, and the
  closed-world rule counts only indexed implementors.
- **Acceptance:** rows B2 (two cases) pass: the named body stays
  `possible` when the sources hold a lambda, a method reference or an
  anonymous class whose target type is the receiver type or one of its
  subtypes; no edge is added to the creating method.
- **Depends on:** ADR-72, proposed on 2026-11-03 and dropped by the
  product owner on 2026-11-07 (BL-19). Alignment on CodeQL reopens it;
  the product owner decides whether the drop stands.
- **Status:** `open`.
- **Why P3:** reopens a dropped decision; three to nine candidate sites
  on the reference projects.

<a id="bl-31"></a>
### BL-31. Dispatch a call on an external class to its source subclasses

- **Problem:** `Thread t = new MyThread(); t.run()` emits nothing; CodeQL
  gives `MyThread.run` `exact` (H11).
- **Impact:** framework subclasses, such as threads, filters or output
  streams, are unreachable through their library supertype; ADR-70 names
  the remaining CodeQL-only flow through `TSOStr.write` as this shape.
- **Cause:** `_external_dispatch` handles external interfaces (ADR-71)
  only; a call on an external class reaches no source override.
- **Acceptance:** row B3 passes: a call on an external class reaches the
  most specific source override of the name and arity in each source
  subtype, in the caller's visible modules, always `possible`; overloads
  the arguments cannot separate stay unresolved. With BL-29 the
  created-once case may become `exact` under ADR-81.
- **Depends on:** ADR-74, proposed on 2026-11-03 and dropped on
  2026-11-07 (BL-13). The product owner decides whether the drop stands.
- **Status:** `open`.
- **Why P3:** reopens a dropped decision; 43 external subclasses on
  train-ticket, one known lost flow.

## Lot 4: reverse parity

<a id="bl-32"></a>
### BL-32. Apply ADR-81 rule 1 in the CodeQL query and the Joern export

- **Problem:** ADR-81 rule 1 (a record accessor, a Lombok member or a body
  inherited from a library keeps the call `possible`) is delivered for
  `source` only; its delivery record states that CodeQL and Joern do not
  implement it.
- **Impact:** the same code gives a false `exact` with `codeql` where
  `source` says `possible`, in the direction this backlog otherwise
  closes.
- **Cause:** `exactTarget` in `engines/codeql/queries.py` counts only
  `viableCallable` rows; a generated or library implementor is not among
  them. Joern's `extraction.py` has no equivalent check.
- **Acceptance:** the ADR-81 regression fixtures give `possible` with
  each engine; the parity test rows for rule 1 pass in both directions.
- **Depends on:** ADR-81, accepted; no new record.
- **Status:** `open`.
- **Why P3:** aligns CodeQL on `source`, outside the product owner's
  question; listed so the asymmetry is not forgotten.

## Appendix A: measurements of 2026-11-07

All read-only on copies, at `e5243ea`.

### A.1 Train-ticket, `compare-engines`

| Engine | Flows | Edges between indexed methods | Engine calls | Time |
|---|---|---|---|---|
| `source` | 406, all `medium` | 1712 | 0 | 0.8 s |
| `codeql` | 406, all `medium` | 1712 | 6661 | 91 s, database build included |

Zero rows only one engine finds, zero confidence changes. CodeQL's 4415
further edges touch Lombok-generated members, which are nodes for no
engine: 2596 generated accessors, 1738 calls from generated `equals` and
`hashCode`, 36 builder setters, 31 `toString`, 7 `builder()`, 7
`build()`.

### A.2 Probe corpus

Each shape ran through `JavaSymbols.fallback_calls(closed_world=True)`
and through the raw rows of the CodeQL engine, without the join. The
sources were one Maven module, package `p`, with `interface Shape {
double area(); }` implemented by `Circle` and `Square`.

| # | Shape | `source` | `codeql` |
|---|---|---|---|
| A1 | `box.get().area()`, `class Box<T> { T get(); }`, `Box<Circle> box` | `Box.get` only | adds `Circle.area` exact |
| A1 | `repo.find().area()`, `interface Repo<T> { T find(); }`, `CircleRepo extends Repo<Circle>` | `find` rows only | adds `Circle.area` exact |
| A1 | `value.area()` on `T value` inherited from `Box<Circle>` | nothing | `Circle.area` exact |
| A1 | `first(circles).area()`, `<T> T first(List<T>)` | `first` only | adds `Circle.area` exact |
| A2 | `circles.get(0).area()`, `List<Circle> circles` | nothing | `Circle.area` exact |
| A2 | `maybe.get().area()`, `byName.get("x").area()`, `circles.stream().findFirst().get().area()` | nothing | `Circle.area` exact, three edges |
| A3 | `for (var c : circles) c.area()` | nothing | `Circle.area` exact |
| A4 | `(flag ? a : b).area()`, two `Circle` fields | nothing | `Circle.area` exact |
| A5 | `if (o instanceof Circle c) c.area()` (T2) | nothing | `Circle.area` exact |
| A5 | `switch (o) { case Circle c -> c.area(); }` (T3) | nothing | `Circle.area` exact |
| A6 | `Outer.this.helper()` from an inner class | nothing | `Outer.helper` exact |
| A7 | `<T extends Shape> void g(T s) { s.area(); }` (G4) | nothing | `Circle.area`, `Square.area`, `Shape.area` possible |
| A8 | `helper()` inside `new Thread() { public void run() { helper(); } }` | nothing | `Use.helper` exact from the anonymous `run` |
| A9 | `register(c -> c.area())`, `register(Handler h)`, `Handler` a source functional interface | `register` only | adds `Circle.area` exact from the anonymous `on` |
| A10 | `circles.forEach(sink::handle)`, `handle(Circle)` and `handle(Circle, int)` | nothing | `Sink.handle(Circle)` exact |
| A10 | `circles.forEach(sink::only)`, one arity | `Sink.only` exact | `Sink.only` exact |
| B1 | `Shape s = new Circle(); s.area()` | `Circle.area`, `Square.area` possible | `Circle.area` exact, `Shape.area` possible |
| B1 | `private final Shape f = new Circle();` then `f.area()` | same | same |
| B2 | `Fn fn = () -> {}; fn.apply()`, one named `FnImpl` (F2) | `FnImpl.apply` exact, `Fn.apply` possible | the lambda's `apply` exact, `Fn.apply` possible |
| B2 | `Base b = new Base() { void hook() {} }; b.hook()` (F3) | `Base.hook` exact | the anonymous `hook` exact only |
| B3 | `Thread t = new MyThread(); t.run()` (H11) | nothing | `MyThread.run` exact |
| B4 | `Shape field; field.area()`, two implementations | `Circle.area`, `Square.area` possible | the same, plus `Shape.area` possible |
| B5 | `handler.handle(x)`, `class GenericHandler<T> implements Handler2<T>` (G3) | both possible | `GenericHandler.handle` exact, `Handler2.handle` possible |

### A.3 Frequency on the reference projects

Syntax-tree counts, test sources excluded.

| # | Counted | Train-ticket | Lab |
|---|---|---|---|
| A2 | call chained on a JDK container accessor | 88 | 0 |
| A1 | source methods returning a type variable | 13, all under `old-docs/` | 0 |
| A1 | fields typed by a type variable | 1 | 0 |
| A3, A4, A5, A6, B1 | `var` loops, ternary receivers, patterns, `X.this`, created-as-subtype variables | 0 | 0 |
| A8 | anonymous classes of an external type · unqualified calls inside | 116 · 2 | 0 · 0 |
| A9 | lambdas passed to a non-JDK method | 0 | 6 |
| A10 | method references | 9 | 2 |

### A.4 Other engine capabilities, no alignment needed

The `methods()` projection and `--refresh-call-graph-view` exist for
`codeql` only; the index already holds every source method. The
reachability query is unused since ADR-84. Strategy1 Kafka payload types
come from the scanner rule of ADR-76 for every engine. Lombok-generated
members are nodes for no engine. Framework conventions (`@Qualifier`,
`@Primary`, `publishEvent`) are resolved by neither engine and stay out
of scope.
