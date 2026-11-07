# Backlog: review of the source indexing and the Joern engine (2026-11-06)

Status: lots 1 to 5 validated on 2026-11-06 by the product owner, in
the proposed order; lot 6 waits for ADR-75. Owner:
SystemLens implementer. Reviewer: product owner. Baseline: commit
`e21f8d0` (ADR-85 delivered), Joern 4.0.674, CodeQL 2.27.3, train-ticket
at `~/examples/train-ticket` (411 Java files, 406 flows with every engine).

This backlog replaces the viewport backlog, now in
[`docs/plans/graph-viewport-navigation.md`](docs/plans/graph-viewport-navigation.md)
(V1 to V6 still `proposed`). The items carried over from earlier backlogs
are restated in section 6. The previous file is in Git at `e21f8d0`.

## 1. Scope and method

The review covers the `source` indexing (tree-sitter extraction, the
symbol pass, the call-graph join and the flow materialisation), the
engine seam delivered by ADR-85, and the new `joern` engine. CodeQL was
reviewed on 2026-11-04 and is out of scope, except where a typed-engine
rule applies to Joern as well.

Every finding below was confirmed on the code and by one of: a probe test
(kept in the session scratchpad, to become the regression test of the
fix), a Joern run on a fixture, or a measure on train-ticket. Findings of
the earlier reviews are not repeated; S9, S10, Q6, Q8 and Q11 still wait
for ADR-81's lot 3.

Severity values: `false exact`, `false edge`, `lost edge`, `false fact`,
`lost fact`, `wrong label`, `crash`, `staleness`, `drift`, `missing test`.

## 2. Root causes

| # | Root cause | Findings |
|---|---|---|
| R1 | Joern's front end rewrites Lombok sources before building the graph, so its lines no longer name the indexed files | J1 |
| R2 | A typed engine's rows enter the graph without the module scope the symbol pass applies (ADR-71), and without the overload the engine could not prove | J2, J3 |
| R3 | The Joern adapter maps names and candidates without Joern's own conventions: local-class names carry a signature, and dropped candidates still count for dispatch; enum constant bodies and initializers (ADR-75 pending) are not methods | A2, A3, J4, J5 |
| R9 | The timeout path keeps a join checkpoint that the resume path trusts | A1 |
| R10 | The witness hop is modelled as a second search state instead of a label on the exact edge; engine methods are persisted without the ADR-62 rule | A4, A5, A6 |
| R4 | Extractors resolve a name by the only candidate in the repository, or by a hand-written rule, where the source keeps the ambiguity | P5, P9 |
| R5 | Inventory, projection and materialisers disagree on which files are part of the index | P1, P2, P7, P8 |
| R6 | Materialisers trust every YAML or JSON file under a module | P3, P4 |
| R7 | Endpoint ownership only knows method declarations | P6 |
| R8 | ADR-85 left documentation pages and CLI paths without a test | C1, C2, C3, C4, C5 |

## 3. Findings

### 3.1 Joern engine

Fixtures: `scratchpad/jfx` (dispatch shapes, three modules),
`scratchpad/jfx2` (external interfaces, overloads), `scratchpad/jlomb`
(Lombok). The Joern export of the cached train-ticket graph is
`scratchpad/ttcache/export.json`.

| ID | Finding | Where | Severity |
|---|---|---|---|
| J1 | `joern-parse` runs `delombok` when a file imports Lombok and builds the graph on the rewritten source. Method and call lines then point into a file that does not exist: on train-ticket 108 of 410 files (every Lombok file) have methods beyond their last line; `auth.entity.User.getPassword` is reported at line 35, the field, while the index has it at 46-49; 59 engine-only methods duplicate an indexed method and 182 `exact` edges reach such a phantom, 70 of them with no edge to the real method. `--delombok-mode types-only` keeps the original lines and leaves generated accessors without a line, which the adapter already drops. `measured` | `engines/joern/engine.py:196` (`joern-parse` command); `extraction.py:_is_source` | false edge, lost edge |
| J2 | Joern resolves a dynamic dispatch over every type in the projection, with no module scope. In `jfx`, `c.UseC.go` calling `Shape.area` gets `b.Circle.area` as a `possible` target although module `c` does not depend on `b`. The join adds engine rows unconditionally: `prefer_caller_module` applies only to synthetic implementations. The source engine keeps the ADR-71 scope; CodeQL had the same gap (C6, delivered with J2). 0 such edges on train-ticket, whose modules do not share interfaces. | `extraction.py:_calls_of`; `code_flows.py:918-942` | false edge |
| J3 | A call whose argument types Joern cannot resolve (`<unresolvedSignature>`) is dropped even when the owner, the name and the arity are resolved and name one source method: 7 `ContactsController` → `ContactsService` sites on train-ticket, where `HttpHeaders` is external. The symbol fallback adds them as `possible`, so 11 flows fall to `low` where `codeql` and `source` give `medium`. `measured` | `extraction.py:_resolved`, `_calls_of` | lost edge |
| J4 | An enum constant body that overrides a method is not a Joern method: `Square.Kind.BIG.w()` is `exact` to `Kind.w` while `BIG` declares its own `w`. The source engine (K6) knows constant bodies. | `extraction.py:_calls_of`; Joern export of `jfx` | false exact |
| J5 | Calls made by field initializers and constructors are reported by Joern under `Owner.<init>`, a name the index does not have while ADR-75 stays proposed; the adapter also drops every `<init>` callee. When ADR-75 lands, the adapter must keep both. No edge is wrong today. | `extraction.py:_INITIALIZERS` | lost edge (deferred) |
| J6 | The availability message says `brew install coreutils` when `greadlink` is missing; on this machine coreutils was installed but not linked, and `brew link coreutils` was the fix. | `engines/joern/declaration.py:19` | drift |

Checked and found sound: `super` calls are `exact`; a call on an
external interface (`Runnable`, `UserDetailsService`) reaches only the
external stub, so the fallback keeps ADR-71's `possible`; `over(1)` with
`over(long)` and `over(Integer)` picks `over(long)` as Java does; nested
types spelled `Outer$Inner` join through the `$` normalisation; lambda
and anonymous-class callers are attributed to the enclosing method by
line (ADR-62); the cache fingerprint covers the projection and the Joern
version; a timeout keeps the index partial.

### 3.2 Source extraction and inventory

Probe tests: `scratchpad/reviewB/test_probes.py` (P1 to P7) and
`test_probes2.py` (P8, P9); each asserts the current, defective
behaviour and becomes the regression test once inverted.

| ID | Finding | Where | Severity |
|---|---|---|---|
| P1 | Mongo `reads collection` and `writes collection` relations persist a module-relative evidence path (`src/main/java/...`) while the data operations of the same call are root-relative; `--root-path` then opens a file that does not exist for every nested module (invariant 1). | `indexing/relations.py:167-182`; `discovery/build/modules.py:470`, `:741-744` | false fact |
| P2 | AsyncAPI documents under `src/test/**`, or under an excluded path, become persisted contracts: the materialiser walks every module tree without the inventory's exclusions. | `indexing/materializers.py:47-62` | false fact |
| P3 | A non-UTF-8 YAML or JSON file anywhere under a module aborts the whole index: `UnicodeDecodeError` is not an `OSError`. | `indexing/materializers.py:29`, `:54` | crash |
| P4 | A YAML date key in an AsyncAPI or OpenAPI document (`2024-01-01:`) raises `TypeError` in `json.dumps`, whose `default=str` does not apply to keys. | `indexing/materializers.py:37`, `:61` | crash |
| P5 | The Kafka DTO closure binds a message type to the only same-named class in the repository even when the publisher imports a different, external qualified name: `com.external.OrderEvent` becomes `com.a.OrderEvent` with its fields. | `indexing/dto_inventory.py:222-234` | false fact |
| P6 | A class-level `@KafkaListener` with `@KafkaHandler` methods yields a consume endpoint no `IntegrationMethod` owns: the consumer is in the topology but starts no flow. | `indexing/integration_methods.py:224-246`, `:280-296`; `scanner/kafka_ast.py:627-650` | lost fact |
| P7 | A Java file under `module/out/` is hashed and extracted by the inventory but never copied to an engine projection, which treats `out` as a build directory. | `indexing/file_inventory.py:70-84`; `indexing/source_projection.py:41-57` | lost fact |
| P8 | A change to a config-server property file in another module (`configurations/<app>.yml`) rescans only that module, so a `${...}` topic resolved through it stays stale until `--full`. | `indexing/file_inventory.py:228-245`; `scanner/_spring_properties.py:139-155` | staleness |
| P9 | Spring Data REST default collection paths come from a three-rule pluraliser: `Child` → `/childs`, `Analysis` → `/analysises`, where Spring serves `/children` and `/analyses`. | `scanner/rest_mvc.py:624-634`, `:697-703` | false fact |

### 3.3 Call-graph join and flow materialisation

Probe tests: `scratchpad/reviewA/test_review_a.py` (A1),
`test_review_b.py` (A3, A4) and the `joern_probe*.py` runs through
`index_repo` with Joern 4.0.674 (A2, A3).

| ID | Finding | Where | Severity |
|---|---|---|---|
| A1 | An engine timeout followed by `--resume-call-graph-join` persists the flows of the partial call set as `complete`, with the flow signature set: the timeout path writes the join offset at the total and keeps the join signature, so the resume joins nothing and the full edge set is persisted next to the partial flows. A timed-out run must leave no resumable checkpoint. | `indexing/call_graph_stage.py:752-759`, `:812`, `:847-857`; `indexing/service.py:312-332` | staleness |
| A2 | Joern names a local class after the enclosing method's full name with its signature (`App.create:void().Local.go:void()`); the adapter cuts at the first `:`, so `Local.go` becomes `App.create`. The join's same-file range fallback then adds a self-edge `OrderController.create → OrderController.create` and a second flow with status `cycle`. | `engines/joern/extraction.py:50-56`; `indexing/code_flows.py:793-798` | false edge |
| A3 | The Joern adapter decides `exact` after dropping the anonymous-class and external candidates Joern listed: `port.run()` with `Adapter implements Port` and an anonymous `new Port() {...}` gives `Adapter.run` `exact`, where ADR-70 and ADR-81 keep it `possible`. The symbol fallback cannot correct an `exact` site. | `engines/joern/extraction.py:197-223` | false exact |
| A4 | The witness expansion adds a second `possible`, inferred edge to the target the engine already resolved `exact` at the same site, for typed engines and the source witness alike. The forward search keys its visited set by `(target, possible)`, so every method below an interface call is explored twice: 9 transitions instead of 5 on a four-hop chain. `tests/test_code_flows.py:2725` asserts the three-edge shape, so the fix changes a test. | `indexing/code_flows.py:964-978`, `:1001-1004`, `:1664-1669` | performance |
| A5 | `persist_call_graph` stores an engine's anonymous-class methods (`<anonymous class>.run`) as placeholder rows, although ADR-62 gives them no identity; the join itself attributes their calls to the creating method. | `indexing/call_graph_stage.py:430-448` | wrong label |
| A6 | The automatic CodeQL database puts no CodeQL version in the flow signature (`input_fingerprint` is empty), while ADR-84 keys the database cache on it: an upgraded CodeQL recomputes nothing until a source changes. Effect on results not measured. | `engines/codeql/engine.py:107-111`; `indexing/service.py:304-307` | staleness |

Checked and found sound: flow labels follow the ADR rules on every edge
path traced; `_deduplicate_code_flows` and the flow identifiers are
order-independent; the join and the search are linear in calls and
edges; CodeQL attaches anonymous and nested-lambda callers to the
creating method (ADR-62).

### 3.4 Engine seam (ADR-85)

| ID | Finding | Where | Severity |
|---|---|---|---|
| C1 | The documentation site and the companion skill page still show `--codeql-database` and link to a heading that no longer exists. | `docs/index.html:184-185`; `../systemlens-skill/docs/index.html:38` | drift |
| C2 | No test exercises the CLI side of `--engine-option` (split, unknown option, wrong value, option of another engine), `--engine-database` on an engine without a database option, or `init --profile joern`; all behave correctly by hand. | `tests/` | missing test |
| C3 | `analyze call-edge --engine-database` swallows a `ConfigError` and runs with default settings, so a broken `config.yml` yields "`source` n'en accepte pas" instead of the configuration error. | `delivery/cli.py:162-181` | drift |
| C4 | `analysis.engines: []` loads as an empty section; `compare-engines --engines` lists `none` among its values although it refuses it; two error messages end with a double period. | `infrastructure/config.py:127`; `delivery/cli.py:1299`; `callgraph/engine.py:112` | drift |
| C5 | `analyze call-edge` recommends `systemlens doctor` for an unavailable engine, but `doctor` checks no engine availability. | `application/call_edge_diagnostic.py:375`; `application/doctor.py` | drift |

Flagged, not a defect: `index --call-graph-engine joern` with Joern
missing announces the fallback to `source` and exits 0 (ADR-61). The
product owner may want the explicit choice to fail instead, as ADR-83
does for a supplied database; that is a decision record, not a fix.

## 4. Proposed lots

| Lot | Findings | Why this order |
|---|---|---|
| 1 | J1, A2, A3, A1, P3, P4 | Joern fails on Lombok projects and invents an edge and an `exact`; a resume persists stale flows as complete; two inputs crash the index. |
| 2 | P1, P2, P5, P9, P6 | Facts that are wrong or missing in the persisted snapshot. |
| 3 | J3, J2, J4, A4 | Typed-engine precision and the doubled search; J2 shares its fix with C6 of the dispatch-scope plan. |
| 4 | P7, P8, A5, A6, C3, C4, C5, J6 | Consistency of inventory, freshness, persisted methods and messages. |
| 5 | C1, C2 | Documentation and test debt of ADR-85. |
| 6 | J5 | Waits for ADR-75. |

Every fix leaves a regression test in the same commit, built from its
probe or fixture. J1's acceptance: on train-ticket, no Joern method line
beyond its file's last line, no engine-only method sharing a path and
name with an indexed method, and the J3 count unchanged.

## 5. Delivery record

Each lot is one commit on `main`, named in the Commit column by its
subject until the next lot records its hash. Test files are under
`tests/`.

| Item | Status | Commit | Regression test | Deviation |
|---|---|---|---|---|
| J1 | `delivered` | `e419154` | `test_joern_engine.py::test_joern_parse_keeps_lombok_sources_and_rebuilds_an_older_cache`, `::test_joern_lines_name_the_written_lombok_source` (slow) | Train-ticket: 0 method or call lines beyond their file, 0 phantom methods, 406 flows, J3 still 7 edges; engine calls 6314 to 1534 (Lombok accessors have no line). |
| A2 | `delivered` | `e419154` | `test_joern_engine.py::test_a_local_class_method_takes_the_index_spelling`, `::test_a_local_class_call_joins_its_method_without_a_self_edge` (slow) | None. |
| A3 | `delivered` | `e419154` | `test_joern_engine.py::test_a_dropped_anonymous_or_library_candidate_keeps_the_dispatch_possible` | No edge changed on train-ticket. |
| A1 | `delivered` | `e419154` | `test_call_graph_join_regressions.py::test_an_engine_timeout_leaves_no_resumable_join_checkpoint` | The timeout hint now names `systemlens index`; `--resume-call-graph-join` refuses a timed-out snapshot. |
| P3 | `delivered` | `e419154` | `test_source_indexing_regressions.py::test_a_non_utf8_yaml_file_is_no_contract_and_no_crash` | OpenAPI contracts get the same guard. |
| P4 | `delivered` | `e419154` | `test_source_indexing_regressions.py::test_a_yaml_date_key_is_persisted_as_its_iso_text` | OpenAPI contracts get the same guard. |
| P1 | `delivered` | `1d88e4d` | `test_source_indexing_regressions.py::test_a_mongo_relation_path_is_relative_to_the_indexed_root` | `build_architecture_relations` takes `repo_root`; the module inventory keeps module-relative Mongo paths. |
| P2 | `delivered` | `1d88e4d` | `test_source_indexing_regressions.py::test_an_asyncapi_document_outside_the_inventory_is_no_contract`, `::test_an_index_persists_no_asyncapi_contract_from_an_excluded_path` | None. |
| P5 | `delivered` | `1d88e4d` | `test_source_indexing_regressions.py::test_an_explicit_import_of_an_absent_class_keeps_the_message_type_unresolved` | The publisher's imports now come from its file, not only from DTO files. |
| P9 | `delivered` | `1d88e4d` | `test_source_indexing_regressions.py::test_a_spring_data_rest_default_path_stays_dynamic` | PDR-12: the regular spelling stays, as a dynamic route. |
| P6 | `delivered` | `1d88e4d` | `test_source_indexing_regressions.py::test_a_class_level_kafka_listener_is_owned_by_its_handler_methods`, `::test_a_class_level_kafka_listener_with_one_handler_takes_its_payload_type` | Also fixed: the single-handler payload type was never found (`is` on tree-sitter nodes, in `kafka_ast.py` and `kafka_conventions.py`). |
| J3 | `delivered` | `6bca925` | `test_joern_engine.py::test_an_unresolved_signature_on_a_source_owner_keeps_its_one_method_possible` | Only an abstract declaration is kept: with a concrete body, the `possible` row hid the symbol pass's `exact` answer and lowered 3 `ts-basic-service` flows. Train-ticket: the 7 edges are now shared, but the implementation is `possible`, inferred; the 11 flows stay `low`, as the join's rules require. |
| J2 | `delivered` | `6bca925` | `test_call_graph_join_regressions.py::test_a_typed_engine_row_stays_inside_the_callers_visible_modules` | ADR-71 amended: a module that depends on the caller's stays reachable (the lab needs it); the product owner confirmed that refinement on 2026-11-07, which delivers C6. Train-ticket: 0 edges between unrelated modules with `codeql` and `joern`. |
| J4 | `delivered` | `6bca925` | `test_call_graph_join_regressions.py::test_an_enum_constant_body_keeps_a_typed_engine_call_possible` | Fixed in the join, for every typed engine: Joern exports no method for a constant body. |
| A4 | `delivered` | `6bca925` | `test_call_graph_join_regressions.py::test_a_witness_adds_no_second_edge_to_the_exact_target`; `test_code_flows.py::test_source_interface_witness_adds_the_contract_hop_without_a_new_route` updated | ADR-70 amended. Train-ticket: 1811 to 1524 edges between indexed methods for `codeql` and `source`, flows unchanged; lab: 92 to 78 edges, 23 flows. |
| P7 | `delivered` | `e424c6c` | `test_source_indexing_regressions.py::test_the_inventory_and_the_engine_projection_skip_the_same_out_directory` | The inventory now treats a module-rooted `out/` as build output, as the projection and `test_codeql.py` already did. |
| P8 | `delivered` | `e424c6c` | `test_source_indexing_regressions.py::test_a_config_server_file_change_rescans_the_application_it_configures` | A Spring Cloud Config file change rescans every file. |
| A5 | `delivered` | `e424c6c` | `test_call_graph_join_regressions.py::test_an_anonymous_class_method_of_an_engine_is_not_persisted` | None. |
| A6 | `delivered` | `e424c6c` | `test_codeql.py::test_the_codeql_cli_enters_the_flow_signature_of_the_automatic_database` | A hash of the executable's identity, as ADR-84 keys the database cache. |
| C3 | `delivered` | `e424c6c` | `test_engine_seam_cli.py::test_call_edge_with_a_database_reports_a_broken_configuration` | None. |
| C4 | `delivered` | `e424c6c` | `test_engine_seam_cli.py::test_an_engines_section_must_be_a_mapping`, `::test_engine_option_errors_end_with_one_period`, `::test_compare_engines_offers_only_engines_it_can_compare` | Both double periods came from `config.py`'s wrapping of the option error. |
| C5 | `delivered` | `e424c6c` | `test_engine_seam_cli.py::test_doctor_checks_the_configured_engine`, `::test_doctor_reports_an_available_engine` | `doctor` now checks the configured engine instead of the message changing. |
| J6 | `delivered` | `e424c6c` | `test_engine_seam_cli.py::test_a_missing_greadlink_names_the_link_step` | None. |
| C1 | `delivered` | Lot 5; `systemlens-skill` `329ef31` | `test_spec_conformance.py::test_documentation_site_commands_use_existing_flags`, `::test_documentation_site_links_name_existing_specification_headings` | The skill page is checked when the sibling checkout is present. |
| C2 | `delivered` | Lot 5 | `test_engine_seam_cli.py::test_index_refuses_an_engine_option_the_selected_engine_does_not_declare` (six cases), `::test_index_accepts_a_declared_engine_option`, `::test_init_with_the_joern_profile_selects_joern_and_warns_without_it` | None. |
| Lot 6 (J5) | `proposed` | | | Waits for ADR-75. |

## 6. Items carried over from earlier backlogs

Their context lives in Git history before `fdac92e`. Statuses follow
`docs/process/delivery-practices.md`.

| Item | Status |
|---|---|
| Promote the read-modify-write and dual-write review aids out of `export microservices --review-aids`. | `blocked: no second Spring application in the workspace for the precision measurement` |
| Field validation of runtime percentiles, the Prometheus source, and completeness facts (R1, R4, R5). | `blocked: lab Elastic cluster and Prometheus server unreachable from the delivery machine` |
| Regenerate `docs/models/simple-supermarket.html` with the hotspot group and the badge facts. | `blocked: needs a lab reindex plus a runtime collection`; V1 regenerates the static part only |
| Make the lab reachable from a scheduled CI job so the three items above can leave `blocked`. | `open`; depends on the lab repository's compose setup |
| Regenerate the live model in the `pages.yml` pipeline, as `supermarket.html` already is. | `open`; would make V2 redundant once delivered |
| Show observed API, topic, data, and S3 activity beside the static architecture, conservatively and without inventing a source mapping. | `proposed` |
| Give runtime hotspots an explicit Kubernetes capacity context where a verified workload-to-microservice match exists. The matcher prefers an exact name, then accepts a unique token-bounded microservice-name inclusion in a Deployment or StatefulSet name; an ambiguous match stays unresolved. | `proposed` |

## 7. Archived backlogs

| Document | Purpose |
|---|---|
| [Graph viewport navigation](docs/plans/graph-viewport-navigation.md) | V1 to V6 `proposed`, V7 and V8 delivered. |
| [Archived consistency backlog](BACKLOG.consistency-diagnostics.md) | Historical diagnostic rendering tasks. |
| [Earlier backlog](BACKLOG.previous.md) | Historical index of data-operation work. |
