# SystemLens delivery backlog

This file is the only active backlog for the repository. It records the
implementation order, files to change, acceptance criteria, and validation
commands. GitHub issues and project boards are not used for planning or status.

## Status

| Task | Status | Evidence |
|---|---|---|
| BL-001 | Complete | Projection tests cover business names, fixtures, and placeholders. |
| BL-002 | Complete | Web export passes persisted `code_flows` to the renderer. |
| BL-003 | Complete | `.github/workflows/quality.yml` defines Python and browser gates. |
| BL-004 | Complete | CLI and web use `GraphExportInput` with compatibility wrappers. |
| BL-005 | Complete | Benchmark script and reference corpus are checked in. |
| BL-006 | Complete | README, PRD, persistence, glossary, and technical index wording align. |

## Working rules

- Keep one task focused on one observable outcome.
- Update the relevant specification and regression test with the implementation.
- Mark a task complete only after its acceptance criteria and validation pass.
- Keep credentials, machine-specific paths, and generated indexes out of Git.

## Execution order

Complete BL-001 and BL-002 first because they correct user-visible data loss.
Complete BL-003 before BL-004. Run BL-005 after the functional fixes and before
release. BL-006 is independent once the export contract is stable.

## BL-001: Preserve valid service names in graph projections

**Priority:** P0
**Goal:** Stop excluding real business services whose names contain `test`,
such as `attestation-service` or `contest-service`.

**Files:** `src/systemlens/application/architecture_projection.py`,
`tests/test_architecture_projection.py`, `tests/test_web.py` when applicable,
and the owning module-rendering specification.

**Implementation:** Replace the substring heuristic in
`is_exportable_microservice` with an explicit rule. Exclude unresolved property
placeholders and test fixtures only when module metadata, paths, or an explicit
fixture marker identifies them as test artifacts. Keep valid external service
names visible.

**Acceptance criteria:**

1. `attestation-service` and `contest-service` remain in the exported service map.
2. `test-fixture` remains excluded.
3. `${SERVICE_NAME}` remains excluded as an unresolved placeholder.
4. Relations to retained services remain present.
5. Projection and one public export path cover all three cases.

**Validation:** `uv run ruff check src tests` and
`uv run pytest tests/test_architecture_projection.py tests/test_web.py`.

## BL-002: Pass persisted code flows through the web export

**Priority:** P0
**Goal:** Make `/architecture` display the same persisted code-flow snapshot as
the CLI HTML export.

**Files:** `src/systemlens/delivery/web.py`,
`src/systemlens/render/html_export.py` if its input contract changes,
`tests/test_web.py`, `tests/test_render.py`, and
`docs/spec-fonc/html-export.md`.

**Implementation:** Pass `inventory.code_flows` to `render_graph_html`. Add a
fixture with one flow and at least two steps. Assert that the document contains
the flow identity and step data. Keep the existing-index web path read-only.

**Acceptance criteria:**

1. A persisted flow appears in the `/architecture` HTML document.
2. CLI and web produce equivalent flow data for the same snapshot.
3. An empty flow snapshot renders successfully.
4. Serving an existing database does not re-index source files.

**Validation:** `uv run pytest tests/test_web.py tests/test_render.py`.

## BL-003: Add mandatory pull-request quality gates

**Priority:** P0
**Goal:** Run Ruff, mypy, pytest, architecture, security, coverage, and browser
acceptance checks automatically on pull requests and pushes to `main`.

**Files:** `.github/workflows/quality.yml`, `docs/TESTING.md`.

**Implementation:** Use the locked uv environment, run the documented checks,
install the pinned Playwright browser, and fail the browser job when its
prerequisite is unavailable.

**Acceptance criteria:** The workflow runs all required checks, any failure
fails the workflow, and no issue or project board is needed to interpret it.

**Validation:** Inspect the workflow and run its commands locally when the
required tools are installed.

## BL-005: Establish an extraction-quality and scale benchmark

**Priority:** P1
**Goal:** Measure extraction correctness and indexing cost on a fixed corpus.

**Files:** `scripts/benchmark_index.py`, `tests/benchmarks/`, `docs/TESTING.md`.

**Implementation:** Keep a reference corpus for resolved, ambiguous, dynamic,
and generated-source cases. The benchmark reports elapsed time, peak memory,
database size, endpoint count, relation count, and flow count without committing
machine-specific measurements.

**Acceptance criteria:** The corpus covers one expected relation, one rejected
ambiguous relation, and one incremental update. Results identify the command and
fixture revision.

**Validation:** `uv run pytest tests/benchmarks -q` and
`uv run python scripts/benchmark_index.py --help`.


## BL-004: Consolidate the graph-render input contract

**Priority:** P1
**Goal:** Prevent omissions caused by the long list of optional renderer
arguments.

**Files:** `src/systemlens/render/html_export.py`,
`src/systemlens/render/graph_view_model.py`, `src/systemlens/delivery/cli.py`,
`src/systemlens/delivery/web.py`, `tests/test_render.py`, `tests/test_web.py`,
and the relevant technical specification.

**Implementation:** Introduce one typed immutable export-input object containing
the snapshot, relations, code flows, integration methods, diagnostics,
contracts, and source-root context. Build it in CLI and web adapters. Preserve
compatibility wrappers until internal callers migrate.

**Acceptance criteria:** CLI and web use the same input type; flows, relations,
diagnostics, and contracts cannot be lost through positional omission; existing
render tests pass; architecture rules remain acyclic.

**Validation:** `uv run ruff check src tests`, `uv run mypy`, and
`uv run pytest tests/test_render.py tests/test_web.py tests/test_architecture_rules.py`.


## BL-006: Align product documentation with CodeQL behavior

**Priority:** P1
**Goal:** Remove ambiguity between local analysis, external services, temporary
databases, and persisted CodeQL projections.

**Files:** `README.md`, `docs/PRD.md`, `docs/spec-tech/persistence.md`,
`docs/spec-tech/indexing.md`, and `docs/glossary.md`.

**Implementation:** Define local analysis as execution on the user's machine,
state when CodeQL runs, and describe persisted method and edge projections.
Remove wording that suggests no analysis engine is used when CodeQL is the local
default. Keep source-root and credential handling statements consistent.

**Acceptance criteria:** README, PRD, and technical docs use the same vocabulary;
source facts are distinguished from potential flows; commands match CLI help and
tests; no credential or machine-specific path appears.

**Validation:** `uv run pytest tests/test_codeql.py tests/test_code_flows.py` and
`uv run ruff check src tests`.

## Definition of done

A task is complete when its implementation, focused regression tests,
authoritative documentation, and validation commands are updated. The release
check runs mypy, pytest, architecture, security, coverage, and browser
acceptance when its prerequisite is available.
