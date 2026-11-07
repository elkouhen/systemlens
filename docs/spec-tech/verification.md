# Verification and test strategy

Parent: [Technical specification](../SPEC-TECH.md).

SystemLens tests the observable CLI, indexing, persistence, extraction, and
rendering contracts under `tests/`. The default suite stays local and excludes
tests that require Chrome or a real CodeQL database. `pyproject.toml` owns the
pytest marker configuration.

## Validation environment

The SimpleWeb functional tests bind an ephemeral loopback port. A restricted
sandbox can reject those two socket operations before the HTTP behavior runs.
Run the default suite in an environment that permits loopback binding.

## Test levels

Functional tests exercise a public command, persisted snapshot, HTTP adapter,
or exported artifact. Their setup creates the smallest repository or SQLite
snapshot needed for the behavior. New or revised functional scenarios use
explicit `Given`, `When`, and `Then` sections.

Focused tests cover algorithms whose contract is below a public adapter. These
include Java extraction, call-graph resolution, graph placement, serialization,
schema migration, and package dependency rules. They use domain facts only
when invoking the public surface would hide the invariant under test.

Integration tests use a real external runtime. Browser tests use Chrome through
Playwright. Real CodeQL tests create and query a Java database. These tests carry
the `slow` marker and do not run in the default suite.

## Test selection

Start with one observable behavior test for each contract. Add a focused
algorithm test when the public command or artifact hides a distinct invariant.
For browser interactions, select a named control or resource and follow the
visible user path before asserting the result.

Keep negative cases that could expose a wrong target, invented annotation, or
partial mutation. When a new test covers the same failure as an existing test,
replace or strengthen the existing case instead of adding a duplicate.
Parameterization is useful for distinct inputs, but each parameter remains a
separate collected test case.

## Coverage map

| Area | Main test files | Observable contract |
|---|---|---|
| CLI and indexed catalogs | `test_cli_catalog.py`, `test_ast_only.py`, `test_modules.py` | Commands load and render the persisted repository snapshot. |
| Call-edge diagnosis | `test_call_edge_diagnostic.py` | The CLI locates an expected call loss in method inventory, CodeQL joining, snapshot state, or flow reconstruction. |
| Potential code flows | `test_code_flows.py`, `test_flow_diagnostic.py` | Indexed IN-to-OUT paths preserve ordered evidence, confidence, reconciliation, and module filtering. |
| One flow set per snapshot | `test_flow_set_parity.py` | `index` with every engine and `flows calculate` persist the same flows and identifiers, with the configured edge confidence, key tracing and `after_commit` phase (BL-11). |
| Joern export decoding and engine | `test_joern_engine.py` | Unresolved, library and anonymous-class constructor targets give no edge; dispatch confidences follow ADR-70. |
| Constructor and initializer nodes | `test_constructor_nodes.py`, `test_call_graph_shapes.py` (S7, S8) | Every engine routes a flow through `Owner.<init>` with the same edges (ADR-75). |
| Strategy1 payload evidence per engine | `test_strategy1_engine_payloads.py` | Each engine completes a Strategy1 Kafka type only under `strategy1`; Joern follows the CodeQL rules (ADR-85). |
| Review regressions (2026-11-06) | `test_call_graph_join_regressions.py`, `test_source_indexing_regressions.py`, `test_engine_seam_cli.py`, `test_spec_conformance.py` | Each finding of the 2026-11-06 review (`BACKLOG.md` in Git at `4a12ad5`) keeps its fix: join checkpoints, witness edges, persisted facts and paths. |
| CodeQL and fallback dispatch | `test_codeql.py`, `test_codeql_inheritance.py` | Call extraction, timeout cleanup, confidence, inheritance, ambiguity, and AST fallback remain conservative. |
| REST, Kafka, DTO, and data extraction | `test_rest_detection.py`, `test_rest_string_resolution.py`, `test_dto_inventory.py`, `test_relations.py` | Source-backed endpoints and relations retain paths, types, and unresolved values. |
| Persistence and compatibility | `test_storage_uniqueness.py`, `test_models.py`, migration cases in feature files | SQLite round trips, uniqueness, and additive migrations preserve existing indexes. |
| Architecture and dependency rules | `test_architecture.py`, `test_architecture_boundaries.py`, `test_architecture_rules.py` | Impact direction, package boundaries, and import acyclicity remain enforced. |
| Rendering and local delivery | `test_render.py`, `test_simpleweb.py`, `test_web.py`, `test_software_layers.py` | JSON, HTML, local HTTP, graph, and layer views expose the persisted model. |
| Browser interaction | `test_browser_export.py` | Chrome verifies graph controls and interactions that string assertions cannot establish. |

## Call-edge functional scenarios

`test_call_edge_diagnostic.py` drives every scenario through
`systemlens analyze call-edge`. Its Given state is a persisted SQLite snapshot,
and its Then assertions use the public text or JSON result.

| Given | When | Then |
|---|---|---|
| The callee is indexed and the caller is absent. | The user diagnoses the expected call. | The command reports `caller_not_indexed` at `method_inventory`. |
| Both methods exist in a complete snapshot without an edge. | The user diagnoses the expected call. | The command reports `edge_not_persisted` at `engine_extraction_or_join`. |
| Both methods exist in a partial snapshot. | The user diagnoses the expected call. | The command reports `snapshot_incomplete` before assigning a CodeQL cause. |
| A possible edge exists under exact-only reconstruction. | The user diagnoses the expected call. | The command reports `edge_filtered_by_confidence`. |
| An exact edge belongs to a persisted IN-to-OUT flow. | The user diagnoses by method ID. | Text output includes the complete verdict, edge evidence, and flow ID. |
| An exact edge has no path from an indexed IN method. | The user diagnoses the expected call. | The command reports `edge_outside_input_path` with its flow context. |

Existing snapshots do not retain rejected raw CodeQL rows. The absent-edge
scenario therefore verifies the combined `engine_extraction_or_join` verdict
instead of claiming which of those two internal steps lost the call.

## Runtime policy

Tests that verify AST extraction, module identity, DTO persistence, same-method
flows, or workspace federation configure `call_graph_engine="none"`. They must
not start a real CodeQL analysis as an incidental side effect. Tests of the
CodeQL engine patch the submodule that owns the behavior:
`systemlens.engines.codeql.database` for the CLI and database lifecycle and
`systemlens.engines.codeql.extraction` for the row parsers.
`test_call_graph_engines.py` checks that the indexing core never imports
an engine adapter and that each declaration matches the engine it builds.
`test_architecture_boundaries.py` checks that no package imports its own
submodules through the package facade (which would be a cycle), and that
no engine name appears outside `engines/`, the registry and
`storage/persisted_names.py` (ADR-85).

`test_joern_engine.py` decodes a frozen Joern export of
`tests/fixtures/joern/repo` without Joern. Its `slow` tests run the real
engine on that repository, then check the CPG cache; on
`fixtures/joern/lombok`, whose lines must name the written source; and on
`fixtures/joern/local-class` through `index_repo`. They are skipped when
`joern` or a launcher prerequisite is missing.

Three default tests wait for a one-second subprocess deadline. They verify that
CodeQL progress handling times out while reading stdout and terminates a child
process that keeps the pipe open. Their elapsed time is part of the behavior
under test.

Volume-only tests belong in a benchmark when their invariant is already covered
by smaller cases. The default suite keeps bounded scale cases only when they
verify a distinct layout or complexity contract.

## Static and security checks

ArchUnitPython tests enforce package dependency direction and the absence of
import cycles. Hypothesis is available for property-based tests. Coverage
reports branch coverage without claiming a global threshold.

Bandit scans Python security patterns, and the repository-local `.semgrep.yml`
checks dynamic execution and shell invocation patterns. These checks run during
development, not during indexing or at runtime. Bandit suppresses deliberate
assertions and explicit argument-list subprocess calls. Each SQL placeholder
suppression covers generated placeholder text; values remain bound parameters.

## Validation commands

Pull requests use `.github/workflows/quality.yml` as the automated quality
gate. It runs lint, type checking, the default tests once under coverage,
Bandit, and Semgrep. The default run includes the architecture rules. Browser
acceptance tests run locally when Chrome is available. A skipped local
prerequisite does not count as a passing CI result.

Run the default checks after a Python change:

```bash
uv run ruff check src tests
uv run mypy
uv run pytest
```

Run one functional area while developing:

```bash
uv run pytest tests/test_call_edge_diagnostic.py
uv run pytest tests/test_code_flows.py
```

Run the external-runtime tests when their prerequisites are installed:

```bash
uv run pytest -m slow tests/test_codeql_inheritance.py
uv run pytest -m slow tests/test_joern_engine.py
SYSTEMLENS_CHROME_BIN=/path/to/chrome uv run pytest -m slow tests/test_browser_export.py
```

The reference benchmark reports indexing duration, peak memory, database size,
and persisted fact counts for a repository with an existing SystemLens
configuration:

```bash
uv run python scripts/benchmark_index.py /path/to/repository --json
```

The CodeQL run requires the local CLI and its pinned Java pack. The browser run
requires Chrome at `SYSTEMLENS_CHROME_BIN`. A skipped prerequisite is reported
as unverified coverage and must not be described as a passing external test.
