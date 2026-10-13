# Test strategy and validation report

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
| Both methods exist in a complete snapshot without an edge. | The user diagnoses the expected call. | The command reports `edge_not_persisted` at `codeql_extraction_or_join`. |
| Both methods exist in a partial snapshot. | The user diagnoses the expected call. | The command reports `snapshot_incomplete` before assigning a CodeQL cause. |
| A possible edge exists under exact-only reconstruction. | The user diagnoses the expected call. | The command reports `edge_filtered_by_confidence`. |
| An exact edge belongs to a persisted IN-to-OUT flow. | The user diagnoses by method ID. | Text output includes the complete verdict, edge evidence, and flow ID. |
| An exact edge has no path from an indexed IN method. | The user diagnoses the expected call. | The command reports `edge_outside_input_path` with its flow context. |

Existing snapshots do not retain rejected raw CodeQL rows. The absent-edge
scenario therefore verifies the combined `codeql_extraction_or_join` verdict
instead of claiming which of those two internal steps lost the call.

## Runtime policy

Tests that verify AST extraction, module identity, DTO persistence, same-method
flows, or workspace federation configure `call_graph_engine="none"`. They must
not start a real CodeQL analysis as an incidental side effect.

Three default tests wait for a one-second subprocess deadline. They verify that
CodeQL progress handling times out while reading stdout and terminates a child
process that keeps the pipe open. Their elapsed time is part of the behavior
under test.

Volume-only tests belong in a benchmark when their invariant is already covered
by smaller cases. The default suite keeps bounded scale cases only when they
verify a distinct layout or complexity contract.

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
