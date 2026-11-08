# Backlog

Status: written on 2026-11-08 from the review of the `source` engine's
internal-flow detection, and validated the same day by the product
owner, who asked for it to be implemented. Fully processed on
2026-11-08: BL-33 to BL-53 are all `delivered` and none is blocked;
each Delivery line names its commit and tests. Lot order: 0, 1, 4, 3, 2.

Decision records an item depends on are written and accepted under the
product owner's mandate; the owner confirmed them on 2026-11-08.
Owner: SystemLens implementer. Reviewer: product owner. Baseline:
`main` at `b8f23a0`.

The previous backlog (BL-21 to BL-32, source-vs-CodeQL alignment) is
fully delivered and lives in Git at `f3a94e5`. Item IDs continue its
numbering. The review itself was not committed: its measurements are in
Appendix A, its findings without an item in Appendix B, so each item
stands on its own. Probe sources and scripts are in the session
scratchpad and can be regenerated from the shapes named here.

Statuses follow
[`docs/process/delivery-practices.md`](docs/process/delivery-practices.md).
A decision record named under "Depends on" is validated before any code.
Every typing item adds its shapes to `tests/test_engine_parity.py` and
`tests/test_call_graph_shapes.py`, updates the call-shapes and
engine-parity tables of `docs/spec-tech/indexing.md`, and runs ruff,
mypy, the default suite, the slow parity test and the companion check.
Every flow item adds its shape to a flow fixture under `tests/` with the
persisted flows asserted for `index` and for `flows calculate`.

Reference projects: train-ticket at `~/examples/train-ticket` and the
lab application `apps/supermarket-demo`. Every item keeps train-ticket
at 406 flows and 1712 edges with `source` and `codeql`, and
`analyze compare-engines --engines source,codeql` at zero differences
unless the item's acceptance says which edge it adds; the lab stays at
23 flows unless the acceptance says otherwise.

## Diagnosis

The product owner observes, on a Spring project that is not available
here, fewer internal flows with `source` than with `codeql`. The review
probed both engines on 120 receiver-typing shapes and on a three-module
flow project, and counted shape frequencies on the reference projects.

Two findings explain a gap of that kind; the owner's project will say
which one dominates, through `analyze compare-engines --json` run there.

1. **Lost receiver types on everyday code.** Out of 120 typing probes,
   `source` lost the edge on 60 and invented none. The first cause is
   not a library: the pass loses the element type of a JDK container
   (`List`, `Optional`, `Map`, `Stream`) when the container comes from a
   **source method's declared return type** or from a **`var`**
   (`List<Order> list(); list().get(0).ship()`). Then come JDK
   signatures missing from the tables (`Map.getOrDefault`,
   `Stream.toList`, `List.of`, `Supplier.get`), lambdas outside container
   callbacks, and only then Spring Data and `ResponseEntity`
   signatures. A lost receiver type also drops a whole overloaded call
   when one argument is untyped.
2. **`possible` where CodeQL proves `exact`.** On the flow probe, in
   the default `possible` mode, `source` found every flow CodeQL found.
   The difference is confidence: `source` leaves a dispatch `possible`
   where CodeQL proves it `exact` by generic type argument
   (`MessageHandler<String>` against `MessageHandler<byte[]>`), by call
   context (a private helper called with one concrete argument), or by
   type flow (a ternary of two concrete types). Those flows are `low`
   with `source` and `medium` with `codeql`; with
   `engine_edge_confidence: exact` they disappear from `source` only.
   A reader who counts `medium` flows, or runs in `exact` mode, sees
   fewer internal flows.

Everything else the review found loses flows for **both** engines, and
matters for "missing internal flows on a real project" rather than for
the gap between engines: unrecognised entry kinds (`@RabbitListener`,
`@JmsListener`, `@EventListener`, runners), routes inherited from a base
controller, a defect in the ADR-43 entry-subtype rule, flows that reach
no integration point, and the ADR-58 application boundary.

The goal of this backlog is a `source` engine that is **more general**
(types what a written declaration or a documented signature proves) and
**more robust** (one typing gap loses one edge, not a call site; a
defect in the join is visible in a test). A target is never invented
(AGENTS.md invariant 4).

## Summary

| ID | Title | Priority | Status |
|---|---|---|---|
| [BL-33](#bl-33) | Make every review gap a visible parity or flow test | P1 | `delivered` |
| [BL-34](#bl-34) | Fix the documentation drifts the review found | P2 | `delivered` |
| [BL-35](#bl-35) | Type the element of a container returned by a source method or held by `var` | P1 | `delivered` |
| [BL-36](#bl-36) | Complete the JDK signature table | P1 | `delivered` |
| [BL-37](#bl-37) | Type lambdas and method references from JDK functional types | P1 | `delivered` |
| [BL-38](#bl-38) | Keep an overloaded call when one argument is untyped | P1 | `delivered` |
| [BL-39](#bl-39) | Index record compact constructors as methods | P1 | `delivered` |
| [BL-40](#bl-40) | Follow element-type transforms through `map` and `flatMap` | P2 | `delivered` |
| [BL-41](#bl-41) | Resolve nested type arguments | P2 | `delivered` |
| [BL-42](#bl-42) | Type enum `valueOf`/`values`, array `var` loops and `null` ternaries | P2 | `delivered` |
| [BL-43](#bl-43) | Add a documented Spring signature table | P2 | `delivered` |
| [BL-44](#bl-44) | Type `getBean(Class<T>)` and class literals | P3 | `delivered` |
| [BL-45](#bl-45) | Exclude implementors whose type argument contradicts the receiver's | P2 | `delivered` |
| [BL-46](#bl-46) | Narrow a receiver by the creations a method returns or a ternary holds | P3 | `delivered` |
| [BL-47](#bl-47) | Apply the ADR-43 entry rule only while the route stays on `this` | P1 | `delivered` |
| [BL-48](#bl-48) | Start flows from routes inherited from a base controller | P2 | `delivered` |
| [BL-49](#bl-49) | Recognise more entry triggers | P2 | `delivered` |
| [BL-50](#bl-50) | Decide whether an entry-only call tree is a flow | P3 | `delivered` |
| [BL-51](#bl-51) | Apply ADR-71 visibility when rebuilding flows from persisted edges | P2 | `delivered` |
| [BL-52](#bl-52) | Report hop-limit truncation in the index log | P3 | `delivered` |
| [BL-53](#bl-53) | Compare engines by (entry, trigger, output) pair | P3 | `delivered` |

Priority reads: P1 is pure typing with the largest expected gain on
service code, or a defect that drops a flow for both engines; P2 is a
smaller typing gain, a documentation fix, or a change behind a decision
record whose direction is clear; P3 waits for a product or design
decision the owner has not taken.

## Lot 0: visibility

<a id="bl-33"></a>
### BL-33. Make every review gap a visible parity or flow test

- **Problem:** the 60 lost typing edges and the flow findings are known
  only from a session's probes.
- **Impact:** a delivery or a regression on one shape is invisible until
  someone rebuilds the probes.
- **Cause:** `tests/test_engine_parity.py` covers the 31 shapes of the
  previous backlog; no flow fixture holds the shapes of Appendix A.2.
- **Acceptance:** the parity fixture gains the missing and differing
  cases of Appendix A.1, each a strict expected failure naming its item;
  the slow CodeQL test passes on them. A new flow fixture holds the
  Appendix A.2 shapes (S24 sibling template, inherited CRUD route, dead
  end, Rabbit/JMS/event/runner entries, generic-argument dispatch,
  call-context dispatch, typed ternary); a default test asserts the
  persisted flows for `index` and `flows calculate` with `source`, with
  expected failures naming their item. `docs/spec-tech/indexing.md` adds
  the rows to the engine-parity table.
- **Depends on:** none.
- **Status:** `delivered`.
- **Delivery:** `tests/test_engine_parity.py` gains `REVIEW_FIXTURE`
  with the 63 missing and differing cases of A.1 (D03 left out) and E02,
  the B.4 generic return, owned by BL-35. Each is a strict expected
  failure naming every owner from A.1's table; the `slow` CodeQL test
  passes on all 64, except B23, which the join drops for both engines:
  it fails there too, as a strict expected failure naming BL-39
  (`codeql_item`). CodeQL's measured rows equal the review's on every
  case. `tests/test_internal_flow_shapes.py` indexes the new fixture
  `tests/fixtures/internal_flow_shapes` with `source` and checks each
  entry for `index` and `flows calculate`: S24 (BL-47), inherited CRUD
  route (BL-48), Rabbit, JMS, `@EventListener` and runner (BL-49), dead
  end (BL-50), s3 and s4 (BL-45), s19 and s17 (BL-46) are strict
  expected failures; a service chain, S25 and `@Scheduled(fixedDelay)`
  are controls. With `source`, the rebuild equals the index, both
  reaching the `plugin-lib` implementation (B.3, J2), so BL-51 shows
  only in a `slow` CodeQL test, a strict expected failure. On the
  fixture, CodeQL's persisted flows for s17 also include `XmlExporter`,
  added by the join's expansion of the declared `Exporter.export` row
  (J6); the case expects CodeQL's rows, without it. The engine-parity
  table of `indexing.md` lists the new rows. No `src/` change, so the
  reference projects' counts cannot move. Commit `6ca0239`.
- **Why P1:** every other item is measured against these tests.

<a id="bl-34"></a>
### BL-34. Fix the documentation drifts the review found

- **Problem:** four statements no longer match the code.
- **Impact:** a reader of the diagnosis guide looks for a log line that
  does not exist; the specification and the code disagree on a trigger;
  the CodeQL engine's network access is undocumented.
- **Cause:** `docs/DIAGNOSE-INTERNAL-FLOWS.md` cites a log line
  `limite atteinte (N transitions)` that no code emits, and describes the
  CodeQL reachability pass removed by ADR-84. `extraction-rules.md`
  (lines 404-407) says `@Scheduled(fixedDelay|fixedRate)` stays outside
  the trigger classification while `integration_methods.py:279-291`
  labels it and the flow probe shows the flow. The CodeQL engine, with
  `--build-mode=none`, downloads library jars from Maven Central guessed
  from `import` lines (Appendix A.1); `docs/spec-fonc/cli.md` and
  `indexing.md` do not say so.
- **Acceptance:** the guide matches the current logs and stages; the
  `@Scheduled` sentence matches the code and
  `tests/test_spec_conformance.py` covers it; the CodeQL section states
  the network access and that offline runs may lose library-typed
  edges. No behavior change.
- **Depends on:** none.
- **Status:** `delivered`.
- **Delivery:** the guide quotes the current counters, states that
  ADR-84 removed the reachability pass and that the index log does not
  report the hop bound, and points to `--show-call-chains`, whose stop
  reasons are `limite de profondeur`, `aucun appelant indexé` and
  `cycle détecté`; it no longer says an unavailable engine leaves only
  same-method flows, since the index falls back to `source`. The
  `@Scheduled` sentence names the three labels the code produces, and
  `test_documented_scheduled_triggers_start_flows_with_their_label`
  checks them against `scheduled_trigger_endpoints`. `cli.md` and
  `indexing.md` state the CodeQL download, confirmed with CodeQL 2.27.3,
  and replace the "offline indexing" sentence. Not changed: ADR-36 and
  the PRD still call indexing network-independent, a decision the owner
  takes. Commit `37c26c3`.
- **Why P2:** wrong contract in the documentation, no wrong fact in the
  index.

## Lot 1: typing without a rule change

<a id="bl-35"></a>
### BL-35. Type the element of a container returned by a source method or held by `var`

- **Problem:** the BL-27 container view reads a written variable type
  or a `_GENERIC_CALLS` chain only; it never reads a source method's
  declared return type, nor the initializer of a `var`.
- **Impact:** `catalog.list().get(0).ship()` with
  `List<Order> list()`, `maybe().get().ship()` with
  `Optional<Order> maybe()`, `index().get(k).ship()` with
  `Map<String, Order> index()`, `findByName(n).orElseThrow().ship()`
  with a source `Optional<Order> findByName`, `for (var o :
  catalog.list())`, `catalog.list().forEach(o -> o.ship())`,
  `var l = catalog.list(); l.get(0).ship()`, `var s = orders.stream();
  s.findFirst().get().ship()`: all lose the chained edge (Appendix A.1
  cases C01 to C08, C12, C14, A10, parts of A38 and A44). CodeQL finds
  each `exact`. The shape is universal in service code: a service
  returning `List<Dto>` or `Optional<Entity>`.
- **Cause:** `_generic_view` (`java_symbols.py:2268`) has no branch for
  a method invocation whose declaration is a source method, and resolves
  `var` to no container; `receiver_type` then reaches the owner path,
  where `_type_candidates("java.util.List")` is not one source type and
  returns None. The source-generics model (`_returned`, `_receiver_env`)
  already reads declared returns: `Box<Order> box().get().ship()` passes.
- **Acceptance:** the cases above pass in the parity test. The view
  reads the declared return type through the same lookup and
  substitution `_returned` uses, and follows a `var` initializer under
  the existing typing cycle guard. Declarations that disagree, or an
  overload that is not unique, give no view. Train-ticket and lab counts
  hold or gain only edges CodeQL also finds; the lab's
  `ProductPersistenceAdapter` chains (`map`, `orElseGet`) are measured
  before and after.
- **Depends on:** BL-33.
- **Status:** `delivered`.
- **Delivery:** `_generic_view` reads a source method's declared
  container return through `_return_declarations`, shared with
  `_returned`, and a `var` initializer under the `_typing` guard. The
  shared lookup accepts several declarations of one parameter list when
  they agree, which fixes B.4 (E02). A10, C01 to C08, C12, C14 and E02
  pass; C05 and D01 needed only this typing, so BL-37 and BL-38 need
  other cases for their own gaps. A44 also needs `Optional.of` and moves
  to BL-36; A38 and A42 keep BL-43, D05 keeps BL-37. Call shapes T14
  (`-var`, `-inherited`, `-unproven`) add the negative cases. Train-ticket
  and lab counts unchanged; the lab's `ProductPersistenceAdapter` edges
  are identical before and after: `findById` feeds a written local or a
  method reference, and `save` is inherited from `JpaRepository`
  (BL-43). Commit `1578903`.
- **Why P1:** the most frequent lost shape on Spring code, pure typing,
  and it unblocks BL-38's overload cases.

<a id="bl-36"></a>
### BL-36. Complete the JDK signature table

- **Problem:** `_ELEMENT_ACCESSORS`, `_GENERIC_CALLS` and
  `_GENERIC_CONTAINERS` cover the BL-14 and BL-27 accessors only.
- **Impact:** `m.getOrDefault(k, d).ship()`,
  `m.computeIfAbsent(k, f).ship()`, `stream().toList().get(0)`,
  `stream().collect(Collectors.toList()).get(0)`, `List.of(a, b).get(0)`,
  `Arrays.asList(..).get(0)`, `Collections.unmodifiableList(l).get(0)`,
  `new ArrayList<>(orders).get(0)`, `Optional.of(o).orElseGet(..)`,
  `Stream.of(a, b).findFirst().get()`, `Objects.requireNonNull(o)`,
  `Supplier<Order>.get()`, `Function<K, Order>.apply(k)`,
  `BiFunction<..>.apply(..)`, `CompletableFuture<Order>.join()`: all lose
  the edge (cases A15, A16, A19, A25, A26, A28 to A31, A33, A45, B26 to
  B28, B46, D04). Train-ticket has 102 calls on `Map` variables.
- **Cause:** the tables lack those entries; `_generic_view` has no branch
  for a static JDK factory, for `new ArrayList<Order>(..)` or for the
  diamond; `_GENERIC_CONTAINERS` lacks the functional types and
  `CompletableFuture`.
- **Acceptance:** the cases pass. Each new entry quotes its Java SE
  signature in the table of `indexing.md`. A factory whose arguments
  have different types (`List.of(circle, square)`) stays untyped: no
  common-supertype computation. The diamond is typed only from a
  constructor argument with a known view. `Objects.requireNonNull`
  returns its argument's type.
- **Depends on:** BL-33.
- **Status:** `delivered`.
- **Delivery:** the view reads the `Map` accessors that return a value,
  `Stream.toList`, `collect(Collectors.toList()/toSet())`, `reduce`, a
  future's value and the result of `Supplier`, `Callable`, `Function`,
  `BiFunction`, `UnaryOperator` and `BinaryOperator`.

  Static factories (`List.of`, `Set.of`, `Map.of`, `Arrays.asList`,
  `Arrays.stream`, `Stream.of`, `Optional.of`, `copyOf`,
  `Collections.unmodifiable*`) are named through their type or one
  unshadowed static import. An untyped, `null` or array argument also
  stays untyped. `indexing.md` quotes every new signature.

  All 17 cases pass; call shapes T16 add the negative ones. Train-ticket
  and lab counts unchanged. Commit `dfeca0a`.

<a id="bl-37"></a>
### BL-37. Type lambdas and method references from JDK functional types

- **Problem:** a lambda is typed only as a direct argument of a typed
  container call (BL-14) or of a source functional interface (BL-24).
- **Impact:** `Predicate<Order> p = o -> o.ok()`, `Consumer<Order> c =
  o -> o.ship()`, a lambda passed to a source `each(Consumer<Order>)`,
  a lambda passed to `<R> R apply(Function<Order, R>)`, and the
  overloaded reference `orders.forEach(taker::take)` with `take(Order)`
  and `take(Line)` lose their edges (cases B29, B30, C09, C10, D06).
- **Cause:** `_argument_site` returns None for a lambda assigned to a
  variable; `_functional_signature` (`java_symbols.py:2205`) returns
  untyped parameters on purpose for JDK functional types, although
  `_JDK_FUNCTIONAL_ARITIES` knows their arity; a method reference is
  given `argument_types=None` even when `_LAMBDA_PARAMETERS` proves the
  parameter type.
- **Acceptance:** the cases pass. JDK functional types get a fixed table
  of parameter and return positions (`Function<T, R>`: parameter `T`,
  return `R`; `Predicate<T>`, `Consumer<T>`, `Supplier<T>`,
  `BiFunction`, `BiConsumer`, `UnaryOperator`, `BinaryOperator`), and the
  lambda takes the substituted types from its written variable type or
  the source parameter type. An overloaded method reference receives the
  proven parameter types and binds to the one matching overload; two
  matching overloads leave it unresolved.
- **Depends on:** BL-33; BL-36 for the functional containers.
- **Status:** `delivered`.
- **Delivery:** `_JDK_FUNCTIONAL_SIGNATURES` fixes the positions of the
  nine types; `? extends X` and `? super X` give `X` (JLS 9.9). A lambda
  is typed from a source parameter or a written variable, and a
  future's `thenApply`, `thenAccept`, `thenCompose` type their lambda.

  A generic method infers its result type variable from the lambda's
  body or agreeing `return`s (`_lambda_result`, reusable by BL-40); an
  implicit lambda no longer constrains its parameters' type variables.
  A method reference passes its proven parameter types to overload
  selection; equally specific overloads stay unresolved.

  B29, B30, C09, C10, D05, D06 pass, and A27 too: the future's value
  types `o` without a transform, so BL-40 no longer owns it. A04, A06
  and A07 keep BL-43. Call shapes F6 add the negative cases. Train-ticket
  and lab counts unchanged. Commit `3d7a562`.
- **Why P1:** handler and callback code; completes the half-finished
  JDK functional typing.

<a id="bl-38"></a>
### BL-38. Keep an overloaded call when one argument is untyped

- **Problem:** when the target name is overloaded and one argument has
  no type, `_candidate_targets` returns an empty list
  (`java_symbols.py:1601`): no edge, not even `possible`.
- **Impact:** `taker.take(catalog.list().get(0))` with `take(Order)` and
  `take(Line)` loses the call to `take` entirely, and every edge under
  it (case D01). One typing gap costs a call site, not an edge.
- **Cause:** the overload rule treats "cannot separate" as "no target".
  Under invariant 4 the pass must not pick one overload; but dropping the
  call hides a known caller-callee relation.
- **Acceptance:** when the arity matches several overloads and the
  arguments cannot separate them, the pass emits each overload as a
  `possible` edge with reason `overload unresolved`, flows through them
  stay `low`, and the site appears in the call-edge diagnostic. When
  BL-35 or BL-36 types the argument, the single overload is `exact` as
  today. Train-ticket and lab edge counts are measured before and after;
  any new `possible` edge is listed.
- **Depends on:** BL-33. This adds `possible` edges but changes no
  `exact` rule; the owner decides whether an ADR is wanted. The typing
  reviewer recommended fixing the typing first (BL-35, BL-37) and the
  lead proposes this as the robustness fallback.
- **Status:** `delivered`.
- **Delivery:** ADR-87, accepted under the owner's mandate and
  confirmed, gives the rule. In the closed world, unseparable same-arity
  overloads each reach their targets as `possible` edges; a provably
  excluded overload, a varargs candidate, a method reference and the
  open-world gap filler keep their rules (commit `262b404`).

  `analyze call-edge` re-runs the source pass on the caller and lists
  `typing_notes`: `overload unresolved` sites, whose edges carry that
  reason, and `accessor chain broken` sites (Appendix B.6). New parity
  case D07 (`Collections.max` argument) is a documented deviation:
  CodeQL selects `take(Order)` `exact`.

  Call shapes S10 and two diagnostic tests add the negative cases;
  S5-unknown-stays-open now expects both overloads `possible`.
  Train-ticket and lab unchanged: no new edge, no confidence change.
  Commit `0c5ada3`.
- **Why P1:** robustness: it bounds the damage of every remaining typing
  gap to one edge.

<a id="bl-39"></a>
### BL-39. Index record compact constructors as methods

- **Problem:** a call inside a record's compact constructor has no
  indexed caller.
- **Impact:** CodeQL's raw rows hold `b23 → Order.ship exact`, but the
  method inventory has no method for the compact constructor, so the
  join drops the edge for both engines (case B23).
- **Cause:** `materialize_integration_methods` does not emit a method
  for a compact constructor declaration; ADR-75 made constructors and
  initializers part of the graph but did not name this form.
- **Acceptance:** the compact constructor is an indexed method with the
  record's name and the component arity; case B23 passes for both
  engines; a regression test in the ADR-75 test module.
- **Depends on:** none (ADR-75 delivery note updated).
- **Status:** `delivered`.
- **Delivery:** `initializer_declarations` makes a compact constructor
  an `Owner.<init>` node; `declaration_parameters` gives it the record
  components, for the persisted parameter types, the arity `new R(..)`
  selects on and the names its body sees. B23 passes for both engines
  and loses its `codeql_item`; `test_constructor_nodes.py` gains a
  regression test; ADR-75's delivery notes record the change. The lab
  gains `Quantity.of → Quantity.<init>` and `Product.reserve →
  StockReservation.<init>`, `exact` for both engines (source 102 to 104,
  codeql 103 to 105 edges, 23 flows); train-ticket has no compact
  constructor. Commit `42f670d`.
- **Why P1:** a defect in the inventory, cheap.

<a id="bl-40"></a>
### BL-40. Follow element-type transforms through `map` and `flatMap`

- **Problem:** `Stream.map`, `Optional.map` and `CompletableFuture
  .thenApply` change the element type; nothing reads the function's
  return type.
- **Impact:** `stream().map(Order::getLine).toList().get(0).qty()`,
  `Optional.ofNullable(o).map(Order::getLine).orElse(null).qty()`,
  `stream().map(o -> o.getLine()).forEach(l -> l.qty())` lose the edge
  after the transform (cases A18, A24, A41). Streams are rare on
  train-ticket (5) and the lab (1).
- **Cause:** `_GENERIC_CALLS` marks `map` as type-changing and stops.
- **Acceptance:** the new element type is read from a single-arity
  method reference's declared return type, or from a lambda whose body
  is one expression the pass types; anything else keeps the chain
  untyped. The cases pass.
- **Depends on:** BL-33, BL-36.
- **Status:** `delivered`.
- **Delivery:** `map` and `flatMap` of a stream or an `Optional`, and a
  future's `thenApply` and `thenCompose`, take the new element from a
  method reference with one declared arity (0 or 1) and a written,
  non-variable return type, or from `_lambda_result`, which also reads
  agreeing `return`s. `flatMap` needs a container of the same kind.

  A18, A24 and A41 pass; call shapes T18 add the negative cases. The
  code-flow signature moves to `code-flow-v54-overloads-and-transforms`.
  Train-ticket and lab counts unchanged. Commit `2831eb1`.
- **Why P2:** rare on the reference projects, more code than the other
  typing items.

<a id="bl-41"></a>
### BL-41. Resolve nested type arguments

- **Problem:** `_type_argument_value` (`java_symbols.py:1226`) returns
  None for a nested generic argument.
- **Impact:** `Map<String, List<Order>> n; n.get(k).get(0).ship()` and
  `for (var e : m.entrySet()) e.getValue().ship()` lose the edge (cases
  A39, A43).
- **Cause:** type arguments are modelled as flat names; the BL-26
  substitution and the BL-27 view share that model.
- **Acceptance:** a type argument may itself be a parameterized type;
  substitution and the container view work through one level of
  nesting; the cases pass. A design note in `docs/SPEC-TECH.md` records
  the data structure change.
- **Depends on:** BL-33, BL-35.
- **Status:** `delivered`.
- **Delivery:** a view argument may be a nested view (`_Nested`, one
  level deep) when it is a JDK container; its own type variables are
  substituted. Accessors return it, `entrySet()` gives `Map.Entry`
  elements and a `var` loop takes it. The BL-26 environment keeps flat
  arguments. The design note is in `docs/spec-tech/indexing.md`.

  A39 and A43 pass; call shapes T17 add the negative cases. Train-ticket
  and lab counts unchanged. Commit `12c33a5`.
- **Why P2:** changes shared data structures; moderate frequency.

<a id="bl-42"></a>
### BL-42. Type enum `valueOf`/`values`, array `var` loops and `null` ternaries

- **Problem:** three small language rules are missing.
- **Impact:** `Status.valueOf(s).label()`, `Status.values()[0].label()`,
  `for (var o : orderArray) o.ship()` and `(flag ? order : null).ship()`
  lose the edge (cases B14, B15, B44, C11). Train-ticket has 117 enum
  constant calls (already typed) and 13 `values()` calls.
- **Cause:** `_ENUM_METHODS` only blocks an unqualified `values()` from
  binding to an outer type; the `element_of` branch reads iterable views
  only; `null_literal` has no type so the ternary branches never match.
- **Acceptance:** a source enum's `valueOf` returns the enum, `values()`
  an array of it; a `var` loop over an array variable takes the element
  type; a ternary with one `null` branch takes the other branch's type.
  The cases pass.
- **Depends on:** BL-33.
- **Status:** `delivered`.
- **Delivery:** `receiver_type` types a source enum's `valueOf(String)`
  and `values()`; a `var` loop over an array takes the element type; a
  ternary with one `null` branch takes the other's type, boxed when
  primitive. B14, B15, B44 and C11 pass; call shapes T9-array,
  T10-null and T15 add the negative cases (`Enum.valueOf(Class,
  String)`, a non-enum class, two `null` branches). The same commit
  moves `CODE_FLOW_SIGNATURE` to `code-flow-v52-receiver-typing` (B.5).
  Train-ticket counts unchanged. Commit `391a472`.
- **Why P2:** cheap, but the shapes are uncommon.

## Lot 2: library signatures, behind a decision record

<a id="bl-43"></a>
### BL-43. Add a documented Spring signature table

- **Problem:** a method inherited from an external generic supertype
  has no source declaration, so its return type is unknown.
- **Impact:** with `OrderRepo extends JpaRepository<Order, Long>`,
  `repo.findById(id).orElseThrow().getName()`, `repo.findAll().get(0)
  .ship()`, `repo.save(o).ship()`, `repo.findAllById(ids)`, a source
  `Page<Order> findAll(Pageable)` then `.getContent().get(0)`,
  `ResponseEntity<Order> r; r.getBody().ship()`, `ResponseEntity.ok(o)
  .getBody()`: all lose the edge (cases A01, A02, A04 to A09, A11 to
  A13, A38, A42). Train-ticket has 24 repository-to-`Optional` chains
  and 168 `getBody()` calls on `ResponseEntity` variables (most end on
  Lombok accessors, which are nodes for no engine). CodeQL types them
  from jars it downloads during indexing.
- **Cause:** `receiver_type` returns None when the member has no source
  declaration; the supertype's written type arguments are never used for
  an external member.
- **Acceptance:** a table in `indexing.md` lists, per external
  interface, the members whose return type is a type variable or a
  container of one, with the Spring Data and Spring Web versions it
  holds for (`JpaRepository`/`ListCrudRepository`: `findById:
  Optional<T>`, `findAll: List<T>`, `findAllById: List<T>`, `save: S`;
  `CrudRepository.findAll: Iterable<T>`; `Page<T>.getContent: List<T>`;
  `ResponseEntity<T>.getBody: T`, `ResponseEntity.ok(T)`). The type
  arguments come from the source `extends`/`implements` clause or the
  written variable type through the BL-26 substitution. Entries whose
  return type changed across supported versions (`findOne` in Spring
  Data 1.x) are excluded. The cases pass.
- **Depends on:** BL-33, BL-35; a new ADR settling that the engine may
  hold a hand-kept table of external signatures, its version policy and
  the `<S extends T> S save(S)` rule.
- **Status:** `delivered`.
- **Delivery:** ADR-90, accepted on 2026-11-08 under the mandate and
  confirmed (commit `e354e79`). `_REPOSITORY_RETURNS` types `findById`,
  `findAll()`, `findAllById` and `save` of `CrudRepository`,
  `ListCrudRepository` and `JpaRepository` through the source `extends`
  clauses; `Page`/`Slice`, `HttpEntity`/`ResponseEntity` and
  `ResponseEntity.ok` join the container views. A source declaration
  wins, the most specific `findAll` wins, a conflict or a raw repository
  gives nothing. Parity cases A01, A02, A04 to A09, A11 to A13, A38 and
  A42 pass; call shapes W9 add the negatives (member absent from the
  table, raw `JpaRepository`, `findAll` conflict, untyped `save`
  argument). Train-ticket: 447/447 flow pairs, 1712/1712 edges, zero
  differences, unchanged. Lab: the CodeQL-only edge
  `ProductPersistenceAdapter.save → ProductEntity.toDomain` is now found
  by `source` (`exact`), so edges are 105/105 with zero differences; 25
  flows and their confidences unchanged. Commit `151651b`.
- **Why P2:** the owner's project is Spring; but the gain depends on the
  callee being an indexed method, which Lombok entities are not.

<a id="bl-44"></a>
### BL-44. Type `getBean(Class<T>)` and class literals

- **Problem:** a method typed by a `Class<T>` argument is not typed.
- **Impact:** `ctx.getBean(OrderService.class).place()` and
  `mapper.map(src, Order.class).ship()` lose the edge (cases A32, A34).
- **Cause:** `expression_type` has no `class_literal` case; no table
  names `Class<T>`-typed methods.
- **Acceptance:** `X.class` is typed `Class<X>`; `BeanFactory.getBean
  (Class<T>)` returns `T`; ModelMapper and similar stay out unless the
  ADR lists them. The cases pass.
- **Depends on:** BL-43's ADR, extended with the `Class<T>` rule.
- **Status:** `delivered`.
- **Delivery:** ADR-90 holds the `Class<T>` rule. `X.class` and a
  written `Class<X>` are a `Class<X>` view; `getBean(Class<T>)` and
  `getBean(String, Class<T>)` on `BeanFactory` or `ApplicationContext`
  return `T`. A32 passes; A34 (ModelMapper) is a kept deviation of
  ADR-90, not an open gap. Call shapes W10 add the negatives
  (`getBean(String)`, `Class<?>`, untyped name, ModelMapper). The same
  commit moves `CODE_FLOW_SIGNATURE` to `code-flow-v58-spring-signatures`.
  Reference projects unchanged beyond BL-43. Commit `699c935`.
- **Why P3:** rare and framework-specific.

## Lot 3: dispatch precision, behind a decision record

<a id="bl-45"></a>
### BL-45. Exclude implementors whose type argument contradicts the receiver's

- **Problem:** a field `MessageHandler<String> handler` dispatches to
  every implementor of `MessageHandler`, including
  `BinaryHandler implements MessageHandler<byte[]>`.
- **Impact:** flow probe shapes s3 and s4: `source` gives `possible` to
  `TextHandler` and `BinaryHandler`, adds a wrong `BinaryHandler → POST
  /payments` flow, and leaves the true flow `low`; CodeQL dispatches
  `exact` to `TextHandler`. In `exact` mode the flow vanishes from
  `source` only.
- **Cause:** the dispatch index is keyed by erased type; the BL-26
  substitution reads type arguments for return types, not to filter
  implementors.
- **Acceptance:** when the receiver's written type argument and an
  implementor's `implements` clause argument are both concrete source
  or JDK types and differ, the implementor is excluded; a type variable,
  a wildcard or a raw type on either side keeps it. The call is then
  `exact` only under ADR-81. Shapes s3 and s4 give CodeQL's rows.
- **Depends on:** BL-33; an amendment of ADR-81 (the implementor set
  is filtered by type argument).
- **Status:** `delivered`.
- **Delivery:** ADR-81 amendment, accepted on 2026-11-08 under the
  mandate and confirmed (commit `21ebcfd`). Two concrete arguments
  exclude an implementor only when they differ and are not related by
  subtyping, as far as the sources and the final JDK classes prove it;
  a filter that would exclude every target does not apply. Shapes s3
  and s4 give CodeQL's rows; call shapes G7 (with related, raw,
  wildcard, type-variable and library negatives), parity case E03, and
  T8-dispatch, which now expects CodeQL's `exact` row. Train-ticket and
  the lab keep every edge, confidence and flow. Commit `8637485`.
- **Why P2:** a false route today, and the first of the "possible where
  CodeQL proves exact" causes; the rule is local and conservative.

<a id="bl-46"></a>
### BL-46. Narrow a receiver by the creations a method returns or a ternary holds

- **Problem:** ADR-86 narrows a local or a `final` field created once;
  a method whose every `return` is `new T(..)`, a ternary of two
  concrete receivers, and a private helper called with one concrete
  argument are not narrowed.
- **Impact:** `service.shape().area()` with `Shape shape() { return new
  Circle(); }` gives `Circle`, `Square` and `Shape` `possible` where
  CodeQL gives `Circle` `exact` (cases B10, B39); `(flag ? csv :
  json).export()` adds an `XmlExporter` route (probe s17); `exportWith
  (csvExporter)` with `private void exportWith(Exporter e)` gives all
  three implementations (probe s19). Each is `low` with `source`,
  `medium` with `codeql`.
- **Cause:** no interprocedural or union type flow in the pass.
- **Acceptance:** three rules, each conservative: a source method whose
  every return statement is `new T(..)` of one `T` has return type `T`;
  a ternary whose branches have different source types yields the union
  of their dispatch candidates instead of the declared type's; a private
  method whose every call site passes, at a parameter position, a
  receiver narrowed to one `T` has that parameter typed `T`. A method
  with a single `return` of a declared-type expression, a public
  method, or any unproven site keeps the declared type. The cases give
  CodeQL's rows; train-ticket and lab confidences are measured before
  and after.
- **Depends on:** BL-33; a new ADR extending ADR-86 to returns, unions
  and call context.
- **Status:** `delivered`.
- **Delivery:** ADR-89, accepted on 2026-11-08 under the mandate and
  confirmed (commit `21ebcfd`). Cases B10, B39 and shapes s17, s19 give
  CodeQL's rows; call shapes T19 to T21 hold the negatives, and parity
  cases E04, E05 compare with CodeQL. CodeQL's raw rows at E04 include
  the declared row `Printer.print`, which `source` omits because a
  rebuild would dispatch it to `XmlPrinter` (BL-28); at s17 `source`
  therefore never reaches `export-xml`, fresh or rebuilt. A ternary
  receiver whose branches differ and no declared variable names the
  call stays untyped. Signature `code-flow-v57-dispatch-precision`.
  Train-ticket (447 flow pairs, 1712 edges) and the lab (25 flows, 104
  edges) are unchanged. Commit `8a0ff7c`.
- **Why P3:** interprocedural; the owner decides how far the `source`
  engine should follow type flow.

## Lot 4: flow construction

<a id="bl-47"></a>
### BL-47. Apply the ADR-43 entry rule only while the route stays on `this`

- **Problem:** `dispatch_matches_entry` (`code_flows.py:1601-1645`) and
  its persisted twin `entry_dispatch_allowed`
  (`domain/code_flows.py:158-185`) keep an override reached from an
  inherited body only when its owner is an ancestor of the entry's
  class, whatever the receiver.
- **Impact:** flow probe S24: entry `FirstStep extends BaseStep` calls
  `secondStep.execute()` with `SecondStep extends BaseStep`; the edges
  `FirstStep.entry → BaseStep.execute`, `BaseStep.execute →
  SecondStep.doStep`, `SecondStep.doStep → publishProcessed` are
  persisted and **neither engine** produces the flow. The control shape
  S25 (a job calling its own inherited `execute()`) is correct.
- **Cause:** the rule assumes the receiver of the inherited body is the
  entry instance; that holds only while every hop since the entry is a
  call on `this`.
- **Acceptance:** the restriction applies only while the route has not
  left `this`; S24 yields its flow with `source`, `codeql` and `flows
  calculate`; S25 still excludes the sibling; train-ticket and lab flows
  are measured before and after.
- **Depends on:** an amendment of ADR-43.
- **Status:** `delivered`.
- **Delivery:** the ADR-43 amendment of 2026-11-08 (commit `13b7e3c`)
  is accepted under the mandate and confirmed. The traversal state
  carries whether every hop runs on `this`; `JavaSymbols.receiver_sites`
  reads each call's receiver, and a call without receiver to a target
  outside the caller's hierarchy leaves `this` too. Each persisted edge
  records an additive `other_receiver` flag (schema 40); the rebuild,
  `entry_dispatch_allowed` and the call-chain diagnostics read it, and an
  unknown receiver keeps the restriction. S24 passes for `index` and
  `flows calculate` with `source`, and in a `slow` test with `codeql`;
  S25 still excludes the sibling. Signature
  `code-flow-v55-entry-rule-on-this`. Train-ticket (406 flows, 1712
  edges) and the lab (23 flows, 104/105 edges) are unchanged with both
  engines, flow for flow. The flag is set on 4014 of the 6127 persisted
  train-ticket CodeQL edges and on 80 of the 105 lab edges. Commit `5c4928c`.
- **Why P1:** a defect that drops a valid flow for both engines on a
  common template-method shape.

<a id="bl-48"></a>
### BL-48. Start flows from routes inherited from a base controller

- **Problem:** the route of `AbstractCrudController.create`, served by
  `ProductController`, is persisted at the type declaration line of
  `ProductController` and no method owns it.
- **Impact:** `POST /products/crud` exists, the edge
  `AbstractCrudController.create → PaymentClient.charge` is persisted,
  and there is no flow; generic CRUD base controllers and OpenAPI
  delegate interfaces the controller does not override lose every flow.
  `extraction-rules.md:197-198` documents the limitation.
- **Cause:** `_innermost_declaring_spans` (`integration_methods.py:
  373-395`) anchors an inherited route on the subtype declaration.
- **Acceptance:** an inherited route is owned by the inherited method
  with the subtype as entry class, so dispatch from it is restricted to
  that subtype (ADR-43); the probe yields the flow; the specification
  sentence is replaced.
- **Depends on:** an ADR (the entry method of an inherited route).
- **Status:** `delivered`.
- **Delivery:** ADR-88, accepted on 2026-11-08 under the mandate and
  confirmed (commit `6248844`). An inherited route's snippet names the
  declaring method's file and line; `materialize_integration_methods`
  gives the route to that method, and the join runs it with the
  inheritor's module and the inheritor as entry class, so dispatch keeps
  to the inheritor while the route stays on `this`. The flow fixture
  gains `UserController`, a second inheritor whose hook publishes:
  `POST /products/crud` yields its flow and never reaches that hook,
  `POST /users/crud` reaches it. `extraction-rules.md` replaces the
  limitation sentence. Signature `code-flow-v56-inherited-routes-and-triggers`,
  endpoint inventory v29. Neither reference project has an inherited
  route. Commit `43d3b1f`.
- **Why P2:** frequent in generated and CRUD-heavy projects; clear
  direction.

<a id="bl-49"></a>
### BL-49. Recognise more entry triggers

- **Problem:** the join starts only from methods owning a `rest/serve`
  or `kafka/consume` input or a `@Scheduled` trigger
  (`integration_methods.py:20`, `:294-341`; `code_flows.py:1549-1552`).
- **Impact:** on the flow probe, `@RabbitListener`, `@JmsListener`,
  `@EventListener` and `CommandLineRunner.run` have their edges to the
  publisher persisted and no flow. By reading: `ApplicationRunner`,
  `@PostConstruct`, `main`, `@TransactionalEventListener` (used only to
  tag `after_commit`), `@SqsListener`, Spring Cloud Stream functional
  beans, `RouterFunction` handlers, `@MessageMapping`, gRPC services,
  Spring Batch and Quartz jobs.
- **Cause:** product scope: an entry is a REST route, a Kafka
  consumption or a schedule.
- **Acceptance:** the PDR names the triggers added and whether each is
  an endpoint (topology) or a transient trigger like `@Scheduled`; each
  probe trigger yields its flow; topology is unchanged for transient
  triggers; `extraction-rules.md` lists them.
- **Depends on:** a PDR entry.
- **Status:** `delivered`.
- **Delivery:** PDR-16, accepted on 2026-11-08 under the mandate and
  confirmed (commit `6248844`). `@RabbitListener` (with class-level
  `@RabbitHandler`) and `@JmsListener` are consume endpoints and pair
  with a publication of the same system and channel in another module
  as `publishes_to`. Runners and `@PostConstruct` are `startup_entry`
  triggers, `@EventListener` and `@TransactionalEventListener`
  `event_entry` triggers, none a topology endpoint. `publishEvent(x)`
  gets a `possible` edge to each listener of `x`'s static type, so the
  publisher's flow continues into the listener. The probe's Rabbit,
  JMS, event and runner entries yield their flows, plus
  `ApplicationRunner`, `@PostConstruct`, a typed
  `@TransactionalEventListener` and `POST /events`. The export labels
  the new steps; it draws Kafka topics only, so a Rabbit or JMS
  relation shows in the persisted topology and the CLI (PDR-16, known
  limit). A data-only transient flow is now named by its method's owner.
  Commit `a056ae3`; companion skill `4caf5d9`.
  Train-ticket, both engines: 406 to 447 flow pairs, 1712 edges, zero
  differences. The 26 new flow shapes: 20 `CommandLineRunner.run`
  seeders (`InitData`, `InitUser`) to data writes; 2 Rabbit consumers
  (`RabbitReceive.process` in delivery and notification) to their
  repository writes; 4 routes to `RabbitSend.send` (three in food, one
  in notification). 6 Rabbit endpoints, all dynamic (`Queues.queueName`
  lives in another class), so no Rabbit relation pairs. Lab: 23 to 25 flows,
  both engines, 104/105 edges and the same CodeQL-only edge; new:
  `CatalogSeeder.run` to `ProductEntity` count and save. No new endpoint.
- **Why P2:** an unrecognised entry loses every flow under it; the
  `@Scheduled` precedent makes the change mechanical once decided.

<a id="bl-50"></a>
### BL-50. Decide whether an entry-only call tree is a flow

- **Problem:** `record_flow` runs only for an output endpoint, a data
  operation or a cycle; a route that ends on no integration point feeds
  the diagnostic `aucune suite d'appel` and is dropped
  (`code_flows.py:1729-1730`).
- **Impact:** `GET /ping → PingService.compute → MathHelper.square` has
  persisted edges and no flow. Calls the extractors do not model
  (RabbitTemplate, JmsTemplate, `ApplicationEventPublisher`, Redis,
  Elasticsearch, R2DBC, S3/SQS) end nowhere, so their flows are dropped
  too. If the owner's "internal flows" are such trees, this item is the
  whole gap.
- **Cause:** a flow is trigger → effect by design.
- **Acceptance:** the PDR decides between (a) keeping the rule and
  naming the missing effect extractors as items, or (b) persisting an
  entry-only tree as a flow with status `internal` and a defined end
  (deepest method, or every leaf); the export and the diagnosis guide
  follow.
- **Depends on:** a PDR entry.
- **Status:** `delivered`.
- **Delivery:** PDR-15, decided by the product owner on 2026-11-08
  (commit `6248844`): the rule stays, and the missing effects are added.
  `scanner/broker_ast.py` makes `send` and `convertAndSend` on
  `RabbitTemplate`, `AmqpTemplate` and `JmsTemplate` produce endpoints of
  system `rabbitmq` or `jms`; a named exchange gives
  `exchange/routing-key`. `GET /ping` is a passing negative test: no flow,
  diagnostic status `entry_method_without_external_effect`. Positive
  cases cover the default exchange, a named exchange and JMS, and
  `tests/test_broker_extraction.py` the overloads. `publishEvent` is a
  hop, not an effect (PDR-16). Commit `c403b84`.
- **Why P3:** product definition first.

<a id="bl-51"></a>
### BL-51. Apply ADR-71 visibility when rebuilding flows from persisted edges

- **Problem:** the persisted rebuild expands non-inferred edges to
  abstract callees without the ADR-71 `visible()` check
  (`code_flows.py:948-975`); the fresh join applies it (`:1095-1098`).
- **Impact:** `flows calculate` may add cross-module implementations for
  typed engines that `index` excludes; fresh and rebuilt flows differ.
  Not observed on the probe, found by reading.
- **Cause:** two code paths for one rule.
- **Acceptance:** a fixture with an implementation in a module the
  caller cannot see gives identical flows for `index` and `flows
  calculate` with `codeql`; the rebuild shares the fresh join's
  expansion helper.
- **Depends on:** none.
- **Status:** `delivered`.
- **Delivery:** `dispatch_expansion` in `_build_call_graph` holds the
  caller-module preference, the ADR-71 `visible()` check and the
  exact-target narrowing; the fresh join and the rebuild both call it.
  The `slow` CodeQL rebuild test of the flow shapes passes without
  `plugin-notices`, and a unit test in
  `tests/test_codeql_inheritance.py` covers it without CodeQL. Indexed flows are
  unchanged, so the signature is not bumped. Commit `55e03ae`.
- **Why P2:** a latent fresh/rebuilt divergence, the class of defect
  BL-29 already fixed once.

<a id="bl-52"></a>
### BL-52. Report hop-limit truncation in the index log

- **Problem:** `len(route) >= max_hops` records a diagnostic only
  (`code_flows.py:1716`); nothing reaches the index log.
- **Impact:** a deep project silently loses flows past `engine_max_hops`.
- **Cause:** no counter.
- **Acceptance:** the index log reports how many entries hit the limit
  and names the setting; the diagnosis guide (BL-34) cites that line.
- **Depends on:** none.
- **Status:** `delivered`.
- **Delivery:** the join counts the entries whose route stopped at the
  bound while calls remained, and the index log prints
  `→ <engine> : N méthode(s) d'entrée arrêtée(s) à la limite
  engine_max_hops=H.`, cited by `docs/DIAGNOSE-INTERNAL-FLOWS.md`. A
  resumed join counts its own entries only. Train-ticket and the lab
  report 0 at the default 12. Commit `49ea9fe`.
- **Why P3:** observability only.

<a id="bl-53"></a>
### BL-53. Compare engines by (entry, trigger, output) pair

- **Problem:** `_flow_key` (`engine_comparison.py:47-55`) includes every
  step, and each engine picks its own representative route.
- **Impact:** the same pair can appear in both `only_source` and
  `only_codeql`, so a route difference reads as a missing flow. The tool
  also runs the CodeQL reachability pass that indexing dropped (ADR-84),
  for time and no flow.
- **Cause:** the key was written when routes were compared.
- **Acceptance:** the diff is keyed by (entry, trigger, output) with the
  route differences listed separately; the reachability run is removed;
  `docs/spec-fonc/cli.md` updated.
- **Depends on:** none.
- **Status:** `delivered`.
- **Delivery:** `flows` now counts (entry, trigger, output) pairs, with
  module and status; `routes` lists the pairs both engines connect
  through different routes, and each run adds `flow_pairs`. The
  comparison runs no reachability query. `docs/spec-fonc/cli.md`
  describes the keys. Train-ticket: 406 flows and 406 pairs per engine,
  406 common, 0 route differences, edges 1712/1712 with zero
  differences. Lab: 23 flows and 23 pairs, 23 common, 0 route
  differences, edges 104 common and 1 CodeQL-only
  (`ProductPersistenceAdapter.save → ProductEntity.toDomain`). Commit
  `ccca614`.
- **Why P3:** tooling; it matters once the owner runs the comparison on
  their project.

## Appendix A: measurements of 2026-11-08

All read-only, at `b8f23a0`, CodeQL 2.27.3.

### A.1 Receiver-typing probe

One Maven module, package `p`, 120 tagged callers, run through
`JavaSymbols.fallback_calls(closed_world=True)` and through the raw
rows of the CodeQL engine with the parity test's attribution. Result:
56 equal, 60 missing in `source` (one further case, D03, is invalid
Java), 2 differing, 1 empty in both after the join (B23), 0 edges found
by `source` only. Missing cases by cause:

| Cause | Cases | Item |
|---|---|---|
| K1 container from a source method's return | C01-C06, C12, C14, A10, A42, D01, D05 | BL-35 |
| K2 `var` holds a container | C07, C08, A38, A44, C14 | BL-35 |
| K3 JDK signature missing from the tables | A15, A16, A19, A25, A26, A28-A31, A33, A45, B26-B28, B46, D04 | BL-36 |
| K4 member inherited from an external generic supertype | A01, A02, A04-A09, A11-A13, A38, A42 | BL-43 |
| K5 lambda outside a container callback | B29, B30, C09, C10, A04, A06, A07, A27, C05 | BL-37 |
| K6 `map`/`thenApply` changes the element type | A18, A24, A41, A27 | BL-40 |
| K7 nested type argument | A39, A43 | BL-41 |
| K8 enum `valueOf`/`values` | B14, B15 | BL-42 |
| K9 `var` loop over an array | B44 | BL-42 |
| K10 ternary with a `null` branch | C11 | BL-42 |
| K11 overload unresolved drops the call | D01, D05, D06 | BL-38, BL-37 |
| K12 `Class<T>`-typed method | A32, A34 | BL-44 |
| K13 narrowing by a method's returned creation (differs) | B10, B39 | BL-46 |
| Inventory: record compact constructor | B23 | BL-39 |

Shapes that match CodeQL today: `super.m()`, inherited protected
methods, template methods, static imports, builders, fluent chains,
enum constants and switches, default and static interface methods,
inner-to-outer calls, casts, explicit type witnesses, wildcards, catch
and try-with-resources variables, `throw new`, `this` callbacks, fields
set in a constructor, and a written `Optional<Order> x = repo.findById
(id)` local.

The CodeQL database build, with `--build-mode=none` and Java sources
only, fetched `spring-data-jpa-3.1.1.jar`, `modelmapper-3.1.1.jar`,
spring-context, spring-web and spring-tx from Maven Central, with
versions guessed from the imports, and typed the K4 and K12 shapes
from those jars. Offline behavior was not measured.

### A.2 Flow probe

Three Maven modules (`common-lib` library, `billing-app` and
`orders-app` applications, a `tools/` directory outside every module),
59 files, one shape per entry. `source` in default `possible` mode: 48
flows against 43 for `codeql`, `only_codeql_count: 0`. In `exact` mode:
27 against 29, `only_codeql_count: 3`, 21 `source` flows dropped.

| Shape | Observed | Finding |
|---|---|---|
| s3, s4 `MessageHandler<String>` field, two implementors with different type arguments | `source`: both `possible`, wrong `BinaryHandler` flow, true flow `low`; `codeql`: `TextHandler` `exact` | BL-45 |
| s19 private helper `exportWith(Exporter)` called with `csvExporter` | `source`: three implementations; `codeql`: `CsvExporter` `exact` | BL-46 |
| s17 ternary `csv : json` | `source` adds `XmlExporter`; `codeql` does not | BL-46 |
| Feign hop; template-method hop | `possible`/`low` for both engines (ADR-81 rule 3, ADR-43); dropped in `exact` mode | Appendix B |
| S24 sibling template (`FirstStep` calls `secondStep.execute()`) | edges persisted, no flow for either engine | BL-47 |
| S25 job calling its own inherited `execute()` | correct, sibling excluded | control |
| inherited CRUD route `POST /products/crud` | endpoint and edge persisted, no flow | BL-48 |
| `@RabbitListener`, `@JmsListener`, `@EventListener`, `CommandLineRunner.run` | edges persisted, no flow | BL-49 |
| `GET /ping → compute → square` (dead end) | edges persisted, no flow, diagnostic `aucune suite d'appel` | BL-50 |
| `POST /bill → BillingCalculator.compute` into `billing-app` | edge persisted, no flow (ADR-58) | Appendix B |
| `@Scheduled(fixedDelay=5000)` | flow produced; spec says excluded | BL-34 |
| controller → service → client + JPA step; Kafka class-level listener; abstract template through a typed field; default method; static and private helpers; lambdas (`forEach`, `CompletableFuture`, `Optional`, reactive `doOnNext`); method references; anonymous `Runnable`; Lombok constructors and getters; `var`; inner class; one-implementation interface; call into a library module; file outside every module | flow produced with `source` | works |

`flows calculate` reproduced `index` exactly on the probe. Traversal is
breadth-first on (method, possible) states, follows every `possible`
candidate, has no fan-out cap (the `[:50]` limit applies to diagnostics
only), and turns cycles into `cycle` flows.

### A.3 Frequency on the reference projects

Syntax-tree counts through `discovery/java/parser.py`; test sources,
`old-docs/` and `target/` excluded. Train-ticket: 401 files, 402 types,
1538 methods, 7906 call sites (receiver: identifier 65 %, chained call
17 %, field access 10 %, unqualified 7 %). Lab: 47 files, 46 types, 114
methods, 247 call sites (identifier 73 %, chained call 22 %).

| Shape | Train-ticket | Lab | Item |
|---|---|---|---|
| `SourceType.STATIC_FIELD.m()` (almost all `LOGGER.info`, library) | 651 | 0 | none |
| unqualified call to a method of the same class | 335 | 5 | works |
| unqualified call through a static import (mostly `ok(..)`) | 209 | 0 | works |
| `getBody()` on a `ResponseEntity` variable | 168 | 0 | BL-43 |
| `new HttpEntity(..)` | 146 | 0 | none |
| `SourceType.staticMethod(..)` | 120 | 4 | works |
| `SourceEnum.CONST.m()` | 117 | 0 | works |
| calls on a `Map` variable (`put` 64, `get` 27, `entrySet` 8, `getOrDefault` 1) | 102 | 1 (`computeIfAbsent`) | BL-36, BL-41 |
| `isPresent()`/`get()` on an `Optional` variable | 69 | 0 | works |
| repository call then accessor (`orElse` 14, `isPresent` 5, `get` 3, `orElseThrow` 2) | 24 | 3 | BL-43, BL-35 |
| `Optional` chain on a call result | 24 | 5 | BL-35 |
| `e.m()` on a catch parameter | 23 | 1 | works |
| `SourceEnum.values()` | 13 | 0 | BL-42 |
| `a[i].m()` on an array variable | 9 | 0 | works |
| fluent chain of 3+ calls on a source type; builder chain | 8; 7 | 0 | works |
| stream pipelines (all `collect`) | 5 | 1 | BL-36, BL-40 |
| inherited source method, unqualified (approximation) | 0 | 5 | works |
| `((T) x).m()` | 3 | 0 | works |
| `super.m()`; outer-class call | 1; 1 | 0 | works |
| `Future.get()` | 1 | 0 | BL-36 |
| collection factories, Page/Slice, functional interfaces, `CompletableFuture`, ternary or assignment receivers | 0 | 0 | BL-36, BL-42 |

## Appendix B: findings without an item

Decisions the product owner may want to take, and facts worth keeping.

1. **`exact` mode is all-or-nothing.** `code_flows.py:1349-1357` drops
   every `possible` or inferred edge before traversal, so one Feign hop
   (ADR-81 rule 3) or one template hop (ADR-43) removes the whole flow:
   21 of 48 probe flows. Changing it would amend ADR-61's confidence
   contract; not proposed.
2. **ADR-58 boundary.** A shared module that also holds a Boot `main`
   (demo, batch, starter) blocks every hop into it. Documented
   consequence; a project hitting it should move the `main`.
3. **Join differences between engines** are all in how edges enter the
   adjacency, never in the traversal: `source` is the closed-world
   symbol pass alone while `codeql` gets its rows plus the open-world
   pass as gap filler (J1); the ADR-71 `visible()` filter applies to
   CodeQL rows and the gap filler, not to closed-world `source` edges
   (J2, the matter of the dropped ADR-73); placeholder nodes, unique-name
   fallback, anonymous attribution, abstract-callee expansion and enum
   constant-body demotion exist for CodeQL rows only (J3-J7); a
   `possible` edge is `inferred=True` for `source` and `False` for
   CodeQL, with the same confidence (J8).
4. **Two generic models in the pass.** `_receiver_env`/`_returned`
   (source generics) read creations, casts and returns; `_generic_view`
   (JDK containers) reads written variables and chains only. BL-35
   joins them at the return-type seam; a full merge is a refactoring the
   owner may ask for after Lot 1. `_returned` requires exactly one
   declaration where `receiver_type` accepts several with one parameter
   list, so a generic return declared on both an interface and an
   abstract class silently loses its type arguments: found by reading,
   not measured; to be added to BL-33's fixture.
5. **Manual signature bump.** `CODE_FLOW_SIGNATURE` is bumped by hand;
   a change to `java_symbols.py` without a bump keeps old flows on an
   unchanged repository. Every typing item of this backlog bumps it in
   its delivery commit.
6. **Diagnostic on a broken chain.** In `receiver_type`, an accessor
   name with no view falls through to the owner path, which cannot
   succeed for a JDK owner; a diagnostic there would show where a chain
   broke on a real project. Folded into BL-38's call-edge diagnostic.
