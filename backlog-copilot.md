# Backlog: Copilot review of source indexing

Status: written on 2026-11-08 and validated the same day by the product
owner, who asked for it to be processed. Lots 1 to 3 (BC-01 to BC-23)
are `delivered`; the other items are `open`. Lot order: 1 to 6. A
decision record an item depends on is written and accepted under the
product owner's mandate, to be confirmed by them.
Each item first re-checks its finding against current `main`, since
BL-42 to BL-53 landed after the review. Findings were checked
against `main` between `262b404` and `2831eb1` (BL-38, BL-40, BL-41
landed meanwhile); S2, S6, S9, S18 and S19 were re-run on both.

GitHub Copilot CLI reviewed the `source` engine, the indexing pipeline
and the scanners by reading the code only. It reported about 50
findings. Each one was then reproduced on a minimal Java fixture or a
unit-level probe, and the findings that did not hold were filtered out.
Appendix A lists the rejected findings and Appendix B the probes.

IDs use the `BC-` prefix so they cannot collide with `BACKLOG.md`.
No item duplicates BL-33..BL-53 or that backlog's Appendix B. Statuses
follow
[`docs/process/delivery-practices.md`](docs/process/delivery-practices.md).

## Common acceptance

Every item leaves a regression test in the same commit as its fix.
A typing item adds its shapes to `tests/test_call_graph_shapes.py` and
`tests/test_engine_parity.py`, and bumps `CODE_FLOW_SIGNATURE`. A
scanner item adds a fixture assertion on the persisted endpoint. Every
item runs ruff, mypy and the default suite. It also keeps train-ticket
at 447 flow pairs and 1763 edges (1712 before BC-18) and the lab at 25
flows and 105 edges, with `analyze compare-engines --engines
source,codeql` at zero differences on both, unless its acceptance names
the change.

## Summary

| ID | Title | Source | Sev | Status |
|---|---|---|---|---|
| [BC-01](#bc-01) | Survive invalid YAML scalars | C1 | high | `delivered` |
| [BC-02](#bc-02) | Redact credentials in gateway URIs and ignore userinfo in host hints | C7f, C7g | high | `delivered` |
| [BC-03](#bc-03) | Decode parameter text leniently | G3 | high | `delivered` |
| [BC-04](#bc-04) | Skip unreadable files in the inventory | G7 | med | `delivered` |
| [BC-05](#bc-05) | Report source-generation failures as indexing errors | G2 | med | `delivered` |
| [BC-06](#bc-06) | Give source enums and records their implicit supertypes | S2 | med | `delivered` |
| [BC-07](#bc-07) | Read a call's return type across the receiver's ancestors | S5 | med | `delivered` |
| [BC-08](#bc-08) | Keep varargs out of fixed-arity overload selection | S6 | med | `delivered` |
| [BC-09](#bc-09) | Keep default methods reachable through lambdas | S7 | med | `delivered` |
| [BC-10](#bc-10) | Type `this` inside an anonymous class body | S8 | low | `delivered` |
| [BC-11](#bc-11) | Fix local declarator scoping | S9, S10 | low-med | `delivered` |
| [BC-12](#bc-12) | Recognise every lambda argument position | S15 | med | `delivered` |
| [BC-13](#bc-13) | Do not prefer a static import over a possibly inherited method | S16 | low-med | `delivered` |
| [BC-14](#bc-14) | Scope enum-constant bodies and interface fields | S17 | low | `delivered` |
| [BC-15](#bc-15) | Fix varargs overrides and Lombok boolean accessors | S18 | low | `delivered` |
| [BC-16](#bc-16) | Resolve dotted type names and erase generics by depth | S19 | low | `delivered` |
| [BC-17](#bc-17) | Type `this.a.b` receivers | S1 | med | `delivered` |
| [BC-18](#bc-18) | Index enum-constant arguments as static initialisation | S3 | med | `delivered` |
| [BC-19](#bc-19) | Resolve constructor references `Foo::new` | S4 | low-med | `delivered` |
| [BC-20](#bc-20) | Disambiguate same-named local classes | S11 | low-med | `delivered` |
| [BC-21](#bc-21) | Reach static imports from anonymous class bodies | S12 | low | `delivered` |
| [BC-22](#bc-22) | Resolve qualified type receivers | S13 | low-med | `delivered` |
| [BC-23](#bc-23) | Carry parameter types on source `MethodCall`s | S14 | low | `delivered` |
| [BC-24](#bc-24) | Persist the real `alternative_count` | F1, F5 | med | `open` |
| [BC-25](#bc-25) | Identify data steps by operation, not by line | F2 | med | `open` |
| [BC-26](#bc-26) | Fix key tracing's usage line and caller comparison | F3 | med | `open` |
| [BC-27](#bc-27) | Honour `@TransactionalEventListener(phase)` | F4 | low-med | `open` |
| [BC-28](#bc-28) | Bound the reverse call-chain diagnostic | F6 | low | `open` |
| [BC-29](#bc-29) | Keep the persisted graph when a module refresh times out | P1 | high (CodeQL) | `open` |
| [BC-30](#bc-30) | Keep a refreshed module's method rows | P2 | med (CodeQL) | `open` |
| [BC-31](#bc-31) | Keep profile-conflicting properties dynamic | C2 | med | `open` |
| [BC-32](#bc-32) | Scope `@Value` field resolution to the declaring class | C3 | med | `open` |
| [BC-33](#bc-33) | Join Feign `url` and `path`; resolve empty and nested defaults | C4 | med | `open` |
| [BC-34](#bc-34) | Fix StreamBridge and Kafka Streams detection | C5 | med | `open` |
| [BC-35](#bc-35) | Infer REST `message_type` only for mapped methods | C6 | med | `open` |
| [BC-36](#bc-36) | Recognise dropped client and listener shapes | C7, G6 | low-med | `open` |
| [BC-37](#bc-37) | Cache parsed OpenAPI contracts | G6 | med | `open` |
| [BC-38](#bc-38) | Small fixes and dead code | C7, G1, G5 | low | `open` |
| [BC-39](#bc-39) | Performance hygiene in the symbol pass and the walks | P3, G7, C7 | low | `open` |

Severity reads: high aborts an index, leaks a secret or corrupts a
persisted view; med loses or invents a target on a plausible shape; low
is rare or cosmetic. A **false exact** edge violates AGENTS.md
invariant 4 and ranks above a lost edge of the same frequency.

Suggested order: Lot 1, then Lot 2, then Lots 3 to 6 by owner priority.

## Lot 1: robustness and secrets

<a id="bc-01"></a>
### BC-01. Survive invalid YAML scalars

- **Problem:** PyYAML raises a plain `ValueError` for an impossible
  date such as `build-date: 2023-02-30`. The loaders at
  `_spring_properties.py:171` and `rest_mvc.py:2274, 2308` catch only
  `yaml.YAMLError`, and the gateway parser also reads `.properties` files
  as YAML. The index fails with "Indexation impossible : day is out of
  range for month", which breaks `extractors.md:15`.
- **Fix:** catch `(yaml.YAMLError, ValueError, RecursionError)` in all
  three loaders. Stop sending `.properties` files to the gateway YAML
  parser.
- **Acceptance:** a fixture holding that scalar in `application.yml`
  and in `application.properties` indexes, and its other endpoints are
  kept.
- **Status:** `delivered`.
- **Delivery:** `f54bc2d`. Reproduced on `a1fa0ef`. Every YAML loader of
  the extractors and the contract materializers (`_spring_properties`,
  the two gateway loaders, the two OpenAPI loaders of `rest_mvc`,
  `integration_methods`, `materializers`, `discovery/build/modules`)
  catches `yaml.YAMLError`, `ValueError` and `RecursionError`.
  `.properties` files skip the gateway YAML parser. Test:
  `tests/test_copilot_review_regressions.py::test_an_impossible_yaml_date_does_not_abort_the_index`.
  `extractors.md` names the three errors.

<a id="bc-02"></a>
### BC-02. Redact credentials in gateway URIs and ignore userinfo in host hints

- **Problem:** gateway YAML `uri: https://admin:s3cr3t@inventory/?token=abc`
  is stored verbatim in `endpoints.snippet`, on both the serve row and
  the call row. That violates AGENTS.md invariant 5. `_URL_HOST_RE`
  (`domain/graph.py:176`) stops at `:`, so the service hint for this URI
  is `admin`.
- **Fix:** add one shared redaction helper. It strips userinfo and masks
  sensitive query values (`token`, `key`, `secret`, `password`, ...)
  before any snippet is built, including Java DSL `uri(...)` snippets.
  Allow an optional `userinfo@` before the host group.
- **Acceptance:** no persisted column or HTML export contains the
  secret, and the hint is `inventory`.
- **Status:** `delivered`.
- **Delivery:** `352d244`. Reproduced on `a1fa0ef` with fake values.
  `domain/redaction.redact_url_secrets` strips userinfo and masks secret
  query values, keeping `${...}` and `{...}` values. `MessageEndpoint`
  applies it to every snippet, whichever scanner builds it. `_URL_HOST_RE`
  skips userinfo. The endpoint inventory signature moves to
  `endpoint-inventory-v30-redacted-url-secrets`. Tests:
  `test_a_gateway_uri_credential_is_redacted_everywhere` (SQLite dump and
  HTML export) and
  `test_url_redaction_keeps_placeholders_and_hints_skip_userinfo`.
  Train-ticket and the lab hold no URI userinfo and no snippet the helper
  changes: their persisted endpoints are unchanged.

<a id="bc-03"></a>
### BC-03. Decode parameter text leniently

- **Problem:** `integration_methods.py:561` and `:608` decode parameter
  text with strict UTF-8. One Latin-1 byte in a comment inside a
  parameter list aborts the whole index.
- **Fix:** use `java_parser.node_text`, which decodes with
  `errors="replace"`.
- **Acceptance:** a Latin-1 fixture indexes and keeps its integration
  methods.
- **Status:** `delivered`.
- **Delivery:** `653ec97`. Reproduced on `a1fa0ef`. Both sites, method
  and constructor parameter lists, use `java_parser.node_text`. UTF-8
  sources keep the same text, so method ids are unchanged. Test:
  `test_a_latin1_comment_in_a_parameter_list_keeps_its_integration_methods`.

<a id="bc-04"></a>
### BC-04. Skip unreadable files in the inventory

- **Problem:** `sha256_file` is unguarded. A `chmod 000` file, or one
  removed between the walk and the hash, aborts the index with a
  `PermissionError` traceback.
- **Fix:** skip and log the file on `OSError`. Reject symlinks that
  resolve outside the repository root.
- **Acceptance:** an unreadable non-Java file is reported and the index
  completes.
- **Status:** `delivered`.
- **Delivery:** `e598ee8`. Reproduced on `a1fa0ef`. `list_repo_files`
  leaves out an unreadable file and a symbolic link that resolves outside
  the root, and `index_repo` prints `fichier ignoré, <path> : <reason>`
  for each. Test:
  `test_an_unreadable_file_is_reported_and_the_index_completes`.
  `indexing.md` documents the rule.

<a id="bc-05"></a>
### BC-05. Report source-generation failures as indexing errors

- **Problem:** with `--generate-sources`, a missing `mvn` raises
  `FileNotFoundError`, a non-executable `gradlew` raises
  `PermissionError`, and a timeout raises `TimeoutExpired`. The CLI
  catches only `RuntimeError` and `ValueError`, so each one ends as a
  traceback. `mvnw` is ignored, and a Gradle project without `gradlew` is
  skipped without a message.
- **Fix:** wrap `Popen` and the timeout in `SourceGenerationError`.
  Prefer `mvnw` when present. Log a skipped Gradle module.
- **Acceptance:** each failure prints "Indexation impossible" with the
  cause; `cli.md` documents `mvnw`.
- **Status:** `delivered`.
- **Delivery:** `3500d57`. Reproduced on `a1fa0ef` (missing `mvn`,
  non-executable `gradlew`). `generate_sources` wraps `OSError` and the
  timeout in `SourceGenerationError`, runs `./mvnw` when present, and
  reports each Gradle project that no `gradlew` generates. Tests:
  `tests/test_source_generation.py`, including the CLI message.
  `cli.md` and `indexing.md` document the wrapper and the failures.

Lot 1 validation, on fresh scratch copies at `3500d57`: train-ticket
keeps 447 flow pairs and 1712 edges, the lab 25 flows and 105 edges, and
`analyze compare-engines --engines source,codeql` reports zero
differences on both. Their persisted flows and endpoints equal the
`a1fa0ef` baseline. Ruff, mypy, the default suite and
`scripts/check_companion_contracts.py` pass.

## Lot 2: false exact edges (invariant 4)

Each item here produces an `exact` edge that Java semantics contradict.
The probe in Appendix B shows the wrong edge.

<a id="bc-06"></a>
### BC-06. Give source enums and records their implicit supertypes

- **Problem:** `_applicable` builds `externals` from written `extends`
  and `implements` clauses only. Given `o(Object)` and `o(Enum<?>)`, the
  call `o(Color.RED)` goes exact to `o(Object)`, and `r(new Pt(1))` goes
  to `r(Object)` instead of `r(Record)`. With `m(Enum)` and `m(String)`,
  the call loses every target.
- **Fix:** add `Enum`, `Comparable`, `Serializable` and `Constable` for
  an enum argument, and `Record` for a record argument.
- **Status:** `delivered`.
- **Delivery:** `efe12ea`. Reproduced on `7f06bed`. `_applicable` and
  `_subtype_of` give an enum `Enum`, `Comparable`, `Serializable` and
  `Constable`, and a record `Record`. Shape `S5-implicit-supertypes`,
  parity case E06.

<a id="bc-07"></a>
### BC-07. Read a call's return type across the receiver's ancestors

- **Problem:** `_return_declarations` reads the receiver's own
  same-arity declarations and consults ancestors only when there are
  none. `_receiver_contracts` merges owner and ancestors, so the two
  disagree. With `Base.a(Integer) -> Repo` and `Child.a(String) -> Cache`,
  `child.a(1).get()` goes exact to `Cache.get`.
- **Fix:** gather owner and ancestor declarations, then apply
  `_select_overload`.
- **Status:** `delivered`.
- **Delivery:** `43f8d9d`. Reproduced on `7f06bed`.
  `_returning_declarations` reads the receiver's and its ancestors'
  declarations, applies `_select_overload`, then drops those an override
  hides, as a covariant return does. Shape `S1-return-across-ancestors`,
  parity case E07. T14-unproven now types `Mid`'s `List<Circle>
  items()`, which hides `Api`'s, as CodeQL does.

<a id="bc-08"></a>
### BC-08. Keep varargs out of fixed-arity overload selection

- **Problem:** with `f(int)` and `f(Object...)`, `f("x")` yields both
  as `possible`, and `f(1)` yields no edge at all. Java picks
  `f(Object...)` and `f(int)`. `indexing.md:~695` says varargs applies
  only "when no fixed arity applies".
- **Fix:** keep varargs methods out of the phase 1 and 2 competition,
  and try them only when those phases find nothing.
- **Status:** `delivered`.
- **Delivery:** `32124e8`. Reproduced on `7f06bed`. `_select_overload`
  runs the three phases: a `T...` method competes in phases 1 and 2 only
  at its own arity, as `T[]`, and phase 3 expands it. A typed call also
  considers the varargs overloads, and a `T...` contract is overridden
  only by `T[]`. Shapes `S9-varargs-last` and `S9-varargs-last-fixed`,
  parity case E08.

<a id="bc-09"></a>
### BC-09. Keep default methods reachable through lambdas

- **Problem:** `_drop_unreachable_defaults` spares a default body only
  for anonymous supertypes. For `reg(H x) { x.before(); }` called with
  `reg(() -> {})`, the call goes exact to `A.before`, which ignores the
  lambda's inherited `H.before`.
- **Fix:** also keep a default whose interface is a lambda or
  method-reference target.
- **Status:** `delivered`.
- **Delivery:** `7bdd816`. Reproduced on `7f06bed`. A default stays a
  target when a lambda or method reference implements its interface or a
  subinterface. Shape `F2-inherited-default`, parity case E09, where
  `reg` receives the lambda and a named instance: with only the lambda,
  CodeQL narrows by call context to `H.before`.

<a id="bc-10"></a>
### BC-10. Type `this` inside an anonymous class body

- **Problem:** `this` is typed as the enclosing class. Inside
  `new Thread(){ run(){ register(this); } }`, with `register(Runnable)`
  and `register(Main)`, the call goes exact to `register(Main)`.
- **Fix:** type `this` as the anonymous supertype, or as unknown.
- **Status:** `delivered`.
- **Delivery:** `965b20c`. Reproduced on `7f06bed`. In an anonymous
  class body, an argument `this` is typed by the creation's supertype;
  in a lambda it stays the enclosing class. Shape `T5-anonymous-this`,
  parity case E10.

<a id="bc-11"></a>
### BC-11. Fix local declarator scoping

- **Problem:** three scoping errors.
  - C-style arrays (`Foo x[]`) are typed as the element type.
  - In `Foo a = new Foo(), b = a.n();` with a field `Bar a`, the call
    goes exact to `Bar.n`, because the whole declaration's end byte is
    compared.
  - A try-with-resources resource shadows a same-named field inside
    `catch` and `finally`.
- **Fix:** add `[]` for each `dimensions` node. Compare each
  declarator's end byte. Scope a resource to the try body and the later
  resources.
- **Status:** `delivered`.
- **Delivery:** `188b721`. Reproduced on `7f06bed`. A declarator counts
  its own `[]`, as a parameter, local or field, and is in scope from its
  own start; a resource only in the later resources and the try body.
  Shapes `T1-c-style-array` and `T5-declarator-scope`, parity case E11.

<a id="bc-12"></a>
### BC-12. Recognise every lambda argument position

- **Problem:** `_functional_targets` and `_parameter_type_nodes` handle
  only method-invocation and creation parents. A lambda passed through
  `super(...)`, an enum-constant argument, a varargs parameter, an array
  initializer or a switch arm leaves `h.handle()` exact to the single
  named implementor. That contradicts ADR-72.
- **Fix:** handle `explicit_constructor_invocation`, `enum_constant`,
  `spread_parameter`, `array_initializer` and switch/`yield` parents.
- **Status:** `delivered`.
- **Delivery:** `db24c13`. Reproduced on `7f06bed`.
  `_functional_targets` climbs a switch arm or `yield` to its switch
  expression and reads an array initializer's element type;
  `_parameter_type_nodes` reads `this(..)`, `super(..)` and
  enum-constant constructors and a varargs element. Shape
  `F2-more-contexts`, parity case E12, where the constructor also
  receives a named instance.

<a id="bc-13"></a>
### BC-13. Do not prefer a static import over a possibly inherited method

- **Problem:** in `class Y extends org.acme.ExternalBase { foo(); }`
  with `import static p.Util.foo`, the call goes exact to `Util.foo`. The
  same happens with `extends AbstractList`. An inherited `foo()` would
  win in Java, and the spec says the call stays unresolved.
- **Fix:** stop `_static_import_owner` on `_may_inherit_unknown`, as
  `_lexical_owner` does.
- **Status:** `delivered`.
- **Delivery:** `6087d6c`. Reproduced on `7f06bed`.
  `_static_import_owner` stops on `_may_inherit_unknown`. Shapes
  `S2-import-shadowed`, `S2-import-shadowed-jdk` and
  `S2-import-known-supertype`. No parity case: CodeQL resolves the
  import when it lacks the external jar, and it knows `AbstractList`'s
  members, where the pass now loses that edge on purpose (invariant 4).

<a id="bc-14"></a>
### BC-14. Scope enum-constant bodies and interface fields

- **Problem:** inside an enum constant body `A { W f; x(){ f.go(); } }`,
  the call goes exact through the enum's own field. Interface field
  initializers (`H DEFAULT = new Impl(Impl.compute())`) get no call
  context, so their edges are lost.
- **Fix:** treat a constant's `class_body` as an anonymous scope. Give
  `interface_body` fields a static-initializer context.
- **Status:** `delivered`.
- **Delivery:** `52ccc84`. Reproduced on `7f06bed`. A constant's
  `class_body` is a class scope, like an anonymous body. An interface
  constant with an initializer is static code: the interface gets a
  `<clinit>` node, as CodeQL reports it. Shapes `H9-constant-field` and
  `S8-interface-field`, parity cases E13-constant and E13-interface.

<a id="bc-15"></a>
### BC-15. Fix varargs overrides and Lombok boolean accessors

- **Problem:** a spread parameter's written type is `""`, so
  `Sub.log(int...)` looks like an override of `Base.log(String...)` and
  becomes an invented `possible` target. A Lombok `@Getter boolean
  isActive` generates `isActive()`, but the code expects `isIsActive`,
  so a call goes exact to `Base.isActive`.
- **Fix:** write a spread parameter as `T[]`. Map an `isX` boolean field
  to `isX()`. Accept `@Getter` on an enum, and treat `@Accessors` as an
  open shape.
- **Status:** `delivered`.
- **Delivery:** `fb3d3ce`. Reproduced on `7f06bed`. A spread parameter
  is written `T[]`. Lombok names follow `getX`, `isX` for a primitive
  `boolean`, a `boolean isX` keeping its name, and `setX`; `@Getter`
  applies to an enum, and `@Accessors` (from `lombok.experimental`) is
  an open shape. Shapes `H2-varargs-overload`, `H2-lombok-boolean`,
  `H2-lombok-enum` and `H2-lombok-accessors`, parity case E14. CodeQL
  does not expand Lombok, so it arbitrates only E14.

<a id="bc-16"></a>
### BC-16. Resolve dotted type names and erase generics by depth

- **Problem:** `Map.Entry<K,V>` under `import java.util.*` becomes the
  bogus type `Map.Entry`, which loses the edge. The greedy `<.*>` erasure
  turns `Outer<String>.Inner<Order>` into `Outer`, so `i.z()` goes exact
  to `Outer.z`.
- **Fix:** resolve the first segment through wildcard imports and the
  JDK nested-type table. Erase type arguments by bracket depth.
- **Status:** `delivered`.
- **Delivery:** `55be89f`. Reproduced on `7f06bed`. Type arguments are
  erased by bracket depth, and `Map.Entry` resolves through `import
  java.util.*` from a JDK nested-type table. Shape
  `S1-dotted-generic-type`, parity case E15. This commit moves
  `CODE_FLOW_SIGNATURE` to `code-flow-v59-java-semantics`.

Lot 2 validation, on fresh scratch copies at `55be89f`: every finding
still reproduced at `7f06bed`, and none needed a decision record. Each
new parity case passes against CodeQL.

Train-ticket keeps 447 flow pairs and 1712 edges, the lab 25 flows and
105 edges, and `analyze compare-engines --engines source,codeql`
reports zero differences on both. Their persisted methods, edges and
flows equal the `7f06bed` baseline: no edge or confidence changed, since
neither project holds the Lot 2 shapes.

Ruff, mypy, the default suite, the slow parity, internal-flow and
code-flow tests, and `scripts/check_companion_contracts.py` pass.

## Lot 3: lost edges

Each item loses an edge that a written declaration proves. None invents
a target.

<a id="bc-17"></a>
### BC-17. Type `this.a.b` receivers

- **Problem:** any receiver starting with `this.` is read as a direct
  field. `this.client.api.call()` yields no edge, while
  `client.api.call()` resolves.
- **Fix:** take the `this.` branch only when `object` is `this` and
  `field` is an identifier.
- **Status:** `delivered`.
- **Delivery:** `74435b6`. Reproduced on `2cccab9`. A longer chain is
  now a field access on `this.client`. Shape `S1-this-chain`, parity case
  E16.

<a id="bc-18"></a>
### BC-18. Index enum-constant arguments as static initialisation

- **Problem:** in `enum E { A(H.helper()), B(new X()) }` there is no
  edge to `H.helper`, `X.<init>` or `E.<init>`. ADR-75 assigns static
  code to `Owner.<clinit>`.
- **Fix:** treat `enum_constant` as a `<clinit>` member and emit its
  constructor edge.
- **Status:** `delivered`.
- **Delivery:** `27bb790`. Reproduced on `2cccab9`. An enum gets
  `<clinit>` when a constant reaches a constructor node or its arguments
  call something; a constant with a body reaches no constructor (ADR-62),
  as in CodeQL. ADR-75 gains a delivery note within the decision. Shape
  `S8-enum-constant`, a node test, parity case E17. A lambda in a
  constant's arguments stays a `source`-only edge: the CodeQL join cannot
  attribute its body below the type header.

<a id="bc-19"></a>
### BC-19. Resolve constructor references `Foo::new`

- **Problem:** method references need an `identifier` as their last
  child, so `map(Foo::new)` produces no `Foo.<init>` edge.
  `Type::<T>m` is dropped for the same reason.
- **Fix:** map `::new` to `_constructor_targets` by the target
  functional interface's arity.
- **Status:** `delivered`.
- **Delivery:** `3e410da`. Reproduced on `2cccab9` for `Foo::new`;
  `Type::<T>m` did not reproduce and is pinned by `F1-type-arguments`.
  CodeQL dropped the edge too: its implicit-`super()` filter removed the
  `new Foo(..)` it synthesizes, so the query now filters `super(...)`
  only. Overloads left open stay unresolved, as for a method reference.
  Shape `S7-constructor-reference`, parity case E18.

<a id="bc-20"></a>
### BC-20. Disambiguate same-named local classes

- **Problem:** two methods that each declare a local `class Helper` share
  the name `p.A.Helper`. Every call in and to both classes is lost.
- **Fix:** key local types by their enclosing method or declaration
  offset.
- **Status:** `delivered`.
- **Delivery:** `64e61d8`. Reproduced on `2cccab9`. A same-named local
  class takes the internal name `Helper~<offset>`; the index keeps the
  shared name, as CodeQL does. Shape `T7-same-named`, parity cases E19.

<a id="bc-21"></a>
### BC-21. Reach static imports from anonymous class bodies

- **Problem:** a statically imported `helper()` inside an anonymous
  `Runnable` yields no edge. `_lexical_owner` returns `None` for both
  "the supertype may declare it" and "nobody declares it".
- **Fix:** separate the two outcomes, and try the static import in the
  second.
- **Status:** `delivered`.
- **Delivery:** `8d71411`. Reproduced on `2cccab9`. `_lexical_lookup`
  reports a blocked search apart from one that met no declaration; only
  the latter reaches the import, consistent with BC-13. Shape
  `S2-import-anonymous`, parity case E20.

<a id="bc-22"></a>
### BC-22. Resolve qualified type receivers

- **Problem:** `Outer.Inner.create()` and `p.Util.run()` yield no edge,
  because the receiver is a `field_access` that the field lookup cannot
  resolve.
- **Fix:** resolve the dotted text as a source type first and treat it
  as a static receiver.
- **Status:** `delivered`.
- **Delivery:** `8d5356e`. Reproduced on `2cccab9`. The order follows
  JLS 6.5.2 rather than "type first": a variable, then a field, then a
  source type, so a variable `p` obscures the package `p`. Shapes
  `S2-qualified-type` and `S2-qualified-type-variable`, parity case E21.

<a id="bc-23"></a>
### BC-23. Carry parameter types on source `MethodCall`s

- **Problem:** found by reading, not run end to end. Both `MethodCall`
  constructions in `java_symbols.py` omit the caller and callee
  parameter types. `locate` (`code_flows.py:836`) returns `None` when
  same-name overloads share a line, so the edge is silently dropped.
- **Fix:** pass `info.method.parameter_types` and
  `target.parameter_types`.
- **Status:** `delivered`.
- **Delivery:** `a52953a`. Proven on `2cccab9`: with `f(int)` and
  `f(String)` on one line, the source join gave no edge where CodeQL
  gave two, and calls from either overload were lost too. The pass sets
  both types, and the join passes the callee's to `locate`. Test
  `test_source_calls_reach_same_line_overloads`; the parity harness keys
  rows by line, so it holds no case. This commit moves
  `CODE_FLOW_SIGNATURE` to `code-flow-v60-lost-edges`.

Lot 3 validation, on fresh scratch copies at `a52953a` against a
`2cccab9` baseline: every finding reproduced except `Type::<T>m`
(BC-19), and BC-23 was proven end to end. No accepted rule changed;
ADR-75 gains a delivery note for BC-18. Each new parity case, E16 to
E21, passes against CodeQL.

Train-ticket gains 12 enum `<clinit>` nodes and 51 `exact` edges, each
from an enum's `<clinit>` to its constructor at a constant's line
(BC-18). No edge is removed and the flows are unchanged. The lab is
unchanged. Train-ticket now has 447 flow pairs and 1763 edges, and the
lab has 25 flows and 105 edges.

`analyze compare-engines --engines source,codeql` reports zero
differences on both, so CodeQL has every new edge. No `Foo::new`, local
class, anonymous static import or qualified type receiver occurs there.

Ruff, mypy, the default suite, the slow parity, internal-flow,
code-flow and constructor-node tests, and
`scripts/check_companion_contracts.py` pass.

## Lot 4: flow construction

<a id="bc-24"></a>
### BC-24. Persist the real `alternative_count`

- **Problem:** the diamond `A -> B -> D`, `A -> C -> D` is persisted
  with `alternative_count=1`, although `data-model.md:740` and
  `ADR.md:440` promise the route count. The final
  `_deduplicate_code_flows` recounts routes from the one flow left per
  group. `record_flow` also resets seeded flows to 1 on resume.
- **Fix:** take the maximum of the incoming counts in the final dedupe,
  and keep the count when seeding.

<a id="bc-25"></a>
### BC-25. Identify data steps by operation, not by line

- **Problem:** `orders.findById(x); items.findById(y);` on one line
  gives one flow, and the `Item` read is lost. The key is (path, line,
  operation) in both `record_flow` and `flow_identity`. The data-flow id
  embeds `operation.line` (`code_flows.py:1702`), although
  `compute_code_flow_id` promises an id that survives line movement.
- **Fix:** use `operation_id` in both keys and in the id coordinate.
- **Acceptance:** flow ids change once; the delivery note says so.

<a id="bc-26"></a>
### BC-26. Fix key tracing's usage line and caller comparison

- **Problem:** `java_value_origins.py:168` passes the method's
  `end_line` as the usage line, so a `final String key = "x"` declared
  after the operation shadows the field `key`. Line 184 compares raw
  CodeQL caller names (`Entry$In.handle`) with dotted names, so `index`
  with CodeQL and `flows calculate` disagree on nested callers.
- **Fix:** pass `operation.line`, and compare normalised names or
  method ids.

<a id="bc-27"></a>
### BC-27. Honour `@TransactionalEventListener(phase)`

- **Problem:** a publication inside a `phase = AFTER_ROLLBACK` listener
  is persisted as `after_commit`. The spec says "a
  `@TransactionalEventListener` method" without a phase, so this needs a
  product decision.
- **Depends on:** a PDR entry deciding which phases mark `after_commit`.
- **Fix:** read `phase` (default `AFTER_COMMIT`) and mark only
  `AFTER_COMMIT` and `AFTER_COMPLETION`.

<a id="bc-28"></a>
### BC-28. Bound the reverse call-chain diagnostic

- **Problem:** found by reading. The reverse walk behind
  `--show-call-chains` checks cycles per route only, so it enumerates
  every simple path up to `max_hops`, which is exponential on fan-in.
- **Fix:** add a visited set per (method, depth), or cap the route count
  and report the cap.

## Lot 5: CodeQL module refresh

Both items affect only engines with `supports_module_refresh`, so they
affect CodeQL and not `source`.

<a id="bc-29"></a>
### BC-29. Keep the persisted graph when a module refresh times out

- **Problem:** on `CallGraphTimeout` during `--refresh-call-graph-view`,
  the fallback runs without the persisted edges and methods. The sink
  then overwrites the view: in the probe, `call_graph_methods` went from
  17 rows to 0, an engine-only edge disappeared, and 5 edges were left
  dangling.
- **Fix:** skip the sink and the flow replacement when a refresh times
  out, or pass the persisted data to the fallback.

<a id="bc-30"></a>
### BC-30. Keep a refreshed module's method rows

- **Problem:** a normal refresh dropped the module's 17 method rows and
  added back 2 placeholders with `end_line == start_line`. All edges
  then dangled, which contradicts `indexing.md:34-37`.
- **Fix:** apply the prefix filter to the old table only, and add the
  refreshed module's methods unfiltered.

## Lot 6: scanner precision

<a id="bc-31"></a>
### BC-31. Keep profile-conflicting properties dynamic

- **Problem:** `${app.topic:orders}` with different values in
  `application-dev.yml` and `application-prod.yml` gives the static topic
  `orders`, which is wrong in every profile. `extraction-rules.md:31-32`
  requires dynamic. A multi-document `application.yml` takes the first
  profile document's value through `setdefault`.
- **Fix:** return an "ambiguous" sentinel that stays dynamic. Tag YAML
  documents by `on-profile` and apply the same "all profiles agree" rule.

<a id="bc-32"></a>
### BC-32. Scope `@Value` field resolution to the declaring class

- **Problem:** the repo-wide table of unique `@Value` field names
  resolves a plain `private final String topic` in another class to that
  class's topic. The table includes `src/test`, and it wins over a
  same-file constant.
- **Fix:** when the current class declares the name, resolve from that
  file only. Try same-file constants first. Exclude test sources from the
  table.

<a id="bc-33"></a>
### BC-33. Join Feign `url` and `path`; resolve empty and nested defaults

- **Problem:** `@FeignClient(url="${x}", path="/api/v1")` loses the
  `/api/v1` prefix, and an unresolved `url` makes the route `<dynamic>`
  despite a literal `path`. `${server.servlet.context-path:}/orders` and
  nested defaults (`${a:${b}}`) collapse to `<dynamic>`.
- **Fix:** join the url's path with `path`, keeping a literal `path`
  when the url is dynamic. Treat an empty default as `""`. Substitute
  embedded placeholders until the text is stable.

<a id="bc-34"></a>
### BC-34. Fix StreamBridge and Kafka Streams detection

- **Problem:** `streamBridge.send("orders-out-0", ...)` persists the
  binding name as a static topic. `mapper.to(dto)` in a file that
  mentions `KStream` becomes a produce endpoint. `streamsBuilder.stream`
  and `builder.table` are dropped.
- **Fix:** map a binding through
  `spring.cloud.stream.bindings.<n>.destination`, or else emit a dynamic
  topic. Match Kafka Streams calls on the receiver type and add `table`,
  `globalTable` and `through`.

<a id="bc-35"></a>
### BC-35. Infer REST `message_type` only for mapped methods

- **Problem:** `_infer_rest_parameter_type` runs for every serve
  endpoint. Gateway and WebFlux endpoints get `RouteLocatorBuilder` or
  `OrderHandler` as `message_type`, and a Data REST search gets `String`.
- **Fix:** restrict the inference to `spring` and `feign` endpoints
  that are anchored on a mapping annotation.

<a id="bc-36"></a>
### BC-36. Recognise dropped client and listener shapes

- **Problem:** each of these shapes produces no endpoint, or a
  `<dynamic>` one, and leaves no trace:
  - RestTemplate calls through receivers named `template` or `rest`
    (`RestOperations`), and `postForLocation`.
  - `WebClient.builder().baseUrl(...)` is ignored.
  - Inline `MessageBuilder` sends, `send(Message)` on a parameter, and
    `@KafkaListeners`.
  - `@Schedules` and repeated `@Scheduled`.
  - `@GetMapping(BASE + "/c")` with a same-class constant.
  - `@HttpExchange(method=..., url=...)`.
  - Gateway `Path` predicates after `_genkey_0`.
- **Fix:** match by declared receiver type and add the missing verbs.
  Apply the RestClient base-URL rule to WebClient. Emit a dynamic
  endpoint for an unresolved message send. Unwrap the container
  annotations, run `local_string` over annotation values, and iterate
  over every predicate key.
- **Note:** `@GetMapping({"/a","/b"})` is documented as dynamic
  (`extraction-rules.md:215`) and stays out of scope.

<a id="bc-37"></a>
### BC-37. Cache parsed OpenAPI contracts

- **Problem:** each unresolved OpenAPI endpoint re-parses its contract.
  Measured at 0.09 s for a 98 KiB spec, that is about 27 s for 300
  operations. The method lookup is O(endpoints × methods).
- **Fix:** cache operation ids per contract, and index methods by
  (module, name).

<a id="bc-38"></a>
### BC-38. Small fixes and dead code

- A single-quoted OpenAPI path key (`'/a':`) falls back to line 1.
  Found by reading.
- `_extract_rest_path`, `_class_base_path` and the legacy regex
  extractors have no caller.
- `prepare_generation_workspace` is unreachable, because the CodeQL
  engine forces `generate_sources=False`, and it filters `build`
  packages at any depth.
- `source_projection` treats an empty `allowed_paths` as "no
  restriction". Test `is not None` instead.

<a id="bc-39"></a>
### BC-39. Performance hygiene in the symbol pass and the walks

Real costs that are not material at today's scale: the symbol pass
takes 0.8 s for 800 classes. Take this item when a large project shows
them.

- `_assigned` walks a block or class body for each narrowed call.
- Receiver chains are re-typed with no memoisation.
- `_external_dispatch` rebuilds the source-type list on every cache
  miss.
- The inventory walk does not prune `.git`, `node_modules` or `target`.
- Spring property file discovery is not cached.

## Appendix A: rejected or reduced findings

| Copilot claim | Verdict |
|---|---|
| F5: the resume inflates `META_INPUT_OUTPUT_FLOW_COUNT` | Rejected: the count is 2 before and after the resume |
| F6: `edge_confidence` is validated only after persistence | Rejected: config and the CLI `Literal` already validate it |
| G2: Gradle `generateSources` is not a standard task | Documented intent (`cli.md:243`, `indexing.md:985`) |
| G4: generation runs in place, and runs twice with CodeQL | In place is documented; twice is false (`codeql/engine.py:146`) |
| G1: the any-depth filter drops packages from the CodeQL database | Code path unreachable; moved to BC-38 |
| S6: the varargs case gives an exact `f(int)` | The mechanism is different; the defect is real (BC-08) |
| S7: `H x = () -> {}; x.before()` loses its edge | Correct through narrowing; the parameter variant is BC-09 |
| C7: `@GetMapping({"/a","/b"})` collapses to dynamic | Documented (`extraction-rules.md:215`) |
| P3, G7, C7e: performance hot spots | Real, not material today (BC-39) |
| G6: per-method package walk | Negligible: only default-package files pay it |

## Appendix B: probes

The raw Copilot report and every probe are in the session scratchpad,
which is not committed. Each probe can be rebuilt from the shape that
its item names.

- `copilot/review.md`: the raw Copilot report.
- `validate-symbols/`: one `A.java` per S-finding, run by `run.py`.
- `validate-flows/`: fixtures `fx1`, `fx2`, `fx-gen` and `fx-perm`, plus
  `probe_*.py`.
- `validate-scanner/`: fixtures `c1` to `c7`.
