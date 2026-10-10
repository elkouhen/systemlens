# Asynchronous write and synchronous read backlog

Detect and visualize a synchronous read that depends on an asynchronous write
whose completion is not guaranteed before the read. The result is a potential
consistency risk supported by source evidence, never a claim about an observed
runtime failure.

Status: in progress. The snapshot contract, conservative detector, export field,
Flux problem list, and focused unit tests are implemented. Java extraction of
the new evidence and full browser validation remain open.
Implemented slice: `CodeFlowStep` now accepts nullable branch/data/causal
metadata; `flow_consistency` emits deterministic pair diagnostics; exports
include `flow_diagnostics`; and Flux renders one row per diagnostic. The current
indexer still emits only the existing Mongo write evidence, so real read/write
coverage is incomplete until ASR-03 is delivered.
Tracking: this file only; do not create GitHub tickets.
Owner: implementing developer; product owner reviews the visual acceptance.

## Story and scope

As an architect inspecting a selected call flow, I want to see where a
synchronous read can overtake a causally related asynchronous write, so I can
locate the missing completion guarantee and inspect the supporting code.

Illustrative scenario: a request publishes an update command and continues
through HTTP to read the affected record. A consumer performs the write.
Publication before the read does not establish write completion before the read.

The analysis must preserve the common trigger, the asynchronous write branch,
and the synchronous read branch. It must distinguish a proven key relationship
from access to the same collection with an unknown key.

The read may occur in a later request, callback, or event-triggered treatment.
Require source-backed causal links between treatments; a shared business key
alone does not establish causality. Unsupported links remain explicit unknowns.

A selected flow may have several diagnostics. Users must be able to identify
each affected read, inspect one diagnostic at a time, and return to the complete
flow without losing their camera or expansion state.

Scope includes persisted evidence, bounded static analysis, export diagnostics,
and visualization in the selected Flux view. Runtime trace collection,
automatic application fixes, and generic architecture smells are excluded.
CDN dependencies, ARIA semantics, and keyboard navigation remain deferred.

## Constraints and design questions

[ADR-27](docs/ADR.md#adr-27-persist-potential-code-flows-separately-from-topology-and-runtime-truth)
limits asynchronous composition and excludes producer flows with a later external
effect from linear composition. The implementation must examine that constraint
because the target story requires preserving both continuations.

[ADR-40](docs/ADR.md#adr-40-preserve-complete-service-arcs-in-selected-flow-graphs)
preserves source-backed branches and endpoint identities. Rendering order and
reachability alone must not become evidence of execution order or data dependence.

Follow the [technical specification](docs/SPEC-TECH.md) and
[functional specification](docs/SPEC-FONC.md). Exports consume the persisted
snapshot; they must not reparse source code. Store evidence paths relative to
the indexed project and preserve compatibility with existing SQLite indexes.

The proposed design adds a branch-aware diagnostic alongside existing flows.
This costs additional evidence storage and analysis time, but preserves existing
flow consumers. Extending the existing flow contract is an alternative to assess
in task ASR-01; silently changing linear flow semantics is unacceptable.

## Delivery order

All tasks are required for this story. P0 establishes sound evidence and
classification; P1 delivers the user-facing diagnosis and release validation.

| Task | Priority | Dependencies | Deliverable |
|---|---|---|---|
| ASR-01 | P0 | None | Evidence audit and contract decision |
| ASR-02 | P0 | ASR-01 | Acceptance fixtures |
| ASR-03 | P0 | ASR-01, ASR-02 | Data-access and causal evidence |
| ASR-04 | P0 | ASR-03 | Persistence and snapshot compatibility |
| ASR-05 | P0 | ASR-04 | Bounded branch analysis |
| ASR-06 | P0 | ASR-05 | Completion guarantees and classification |
| ASR-07 | P1 | ASR-06 | Export diagnostic contract |
| ASR-08 | P1 | ASR-07 | Diagnostic list in Flux |
| ASR-09 | P1 | ASR-08 | Two-branch visualization and evidence |
| ASR-10 | P1 | ASR-09 | Browser validation and synchronized model |

## Tasks and acceptance criteria

### ASR-01: Audit evidence and define the diagnostic contract

- [x] Inspect the flow domain, indexing, storage, and call-graph projection.
- [ ] Record which facts support access kind, data identity, key propagation,
      call-site order, branch compatibility, and completion guarantees.
- [ ] List missing facts and the extraction work needed to obtain them.
- [x] Define a rule identifier and a diagnostic contract with stable IDs,
      branch step references, common trigger, resource and key evidence,
      confidence, classification, limitations, and snapshot completeness.
- [ ] Include expected operation/version identity, causal links across treatments,
      read occurrence identity, query overlap evidence, and path variants.
- [x] Record the branch representation decision in a new ADR, including its
      relationship to ADR-27. Preserve accepted decision history.
- [x] Specify the intended behavior in the functional and technical specifications.

Acceptance: a reviewer can distinguish stored facts from proposed enrichment.
Every required field has a producer or an explicit unknown state. The contract
can express two branches without claiming that consumer completion precedes HTTP.

### ASR-02: Build positive, negative, and incomplete fixtures

- [ ] Add a minimal Java/Spring fixture that publishes an update and then reads
      the same business key through a synchronous call.
- [ ] Include the consumer write and evidence linking the command key to the read.
- [ ] Add variants for a proven different key, unrelated roots, mutually exclusive
      branches, and a read that precedes publication.
- [ ] Add a correlated completion response after commit, a broker acknowledgement,
      and a fixed delay as distinct synchronization cases.
- [ ] Add unknown resource targets, unknown keys, unresolved dispatch, cycles,
      partial indexing, and traversal-limit cases.
- [ ] Add a causally linked later request and callback, plus an unrelated request
      using the same business key as a negative case.
- [ ] Add successive writes to the same key and a confirmation of the older write.
- [ ] Add filtered-list and aggregate reads affected by an asynchronous write,
      plus a provably disjoint query as a negative case.
- [x] Add multiple diagnostics in one flow: distinct reads of the same data,
      different data targets, and equivalent paths for one write/read pair.

| Fixture | Expected outcome |
|---|---|
| Asynchronous command followed by a causally related read without completion evidence | Potential risk |
| Broker acknowledgement or fixed delay only | Potential risk |
| Confirmation of an older write to the same key | Potential risk |
| Expected write completion and visibility established before the read | Guarantee identified |
| Cache or projection freshness unresolved | Insufficient evidence |
| Causal link, resource identity, or query overlap unresolved | Insufficient evidence |
| Proven independent treatments or disjoint data access | Not applicable |

Risk fixtures must establish causality, overlapping data access, and compatible
paths. Missing prerequisites change the result to insufficient evidence.

Acceptance: each fixture declares its expected classification and evidence.
The positive fixture demonstrates both possible execution orders; it does not
require reproducing a runtime race to pass the static-analysis test.

### ASR-03: Extract data identity and causal relationships

- [ ] Capture read/write operations, method and call-site identity, source location,
      target resource identity, and supported key expressions.
- [ ] Resolve table or collection ownership using proven mappings and namespace
      context. Retain unresolved aliases instead of merging names heuristically.
- [ ] Track supported key propagation through request parameters, message payloads,
      method arguments, and repository calls. Never persist runtime key values.
- [ ] Represent publication and synchronous continuation under their common trigger.
- [ ] Preserve supported causal links across later requests, callbacks, and events,
      including operation/version propagation and the expected write relationship.
- [ ] Represent point reads, filtered lists, and aggregates. Determine whether the
      write can affect the query result, or explicitly report unknown overlap.
- [ ] Preserve execution-order evidence and branch conditions where available;
      mark unknown order or feasibility explicitly.

Acceptance: same resource names in different stores remain distinct.
Proven unequal keys exclude the risk only for disjoint point accesses.
List and aggregate reads require query overlap analysis; unresolved overlap
remains a review candidate. Unknown keys remain review candidates.
Source line order alone does not prove interprocedural or asynchronous ordering.

### ASR-04: Persist evidence and preserve existing indexes

- [ ] Add the required domain structures, serialization, and additive migrations.
- [ ] Run migrations before the indexing transaction and preserve old snapshots.
- [ ] Include extraction profile changes in diagnostic invalidation and rebuilds.
- [ ] Expose snapshot completeness and unsupported evidence through read adapters.
- [ ] Validate relative evidence paths and absence of credentials or secret values.

Acceptance: an old index remains readable and reports unavailable analysis where
necessary. Reindexing refreshes affected results. Export succeeds with source
files unavailable and does not silently reparse them.

### ASR-05: Analyze the asynchronous and synchronous branches

- [ ] Find source-backed publication-to-consumer-to-write paths and synchronous
      reads under a common trigger or linked through supported causal evidence.
- [ ] Join candidates by resource identity and point-key or query overlap evidence.
- [ ] Require evidence that publication can precede the related read; distinguish
      structural reachability from compatible execution paths.
- [ ] Preserve fan-out and cycles without inventing an edge from write to read.
- [ ] Use indexed lookups and bounded traversal rather than enumerating all paths.
- [ ] Define configurable or named limits, deterministic ordering, deduplication,
      complexity expectations, and explicit truncation reporting.
- [ ] Group equivalent paths for the same causal context, expected write occurrence,
      read occurrence, and affected data into one diagnostic with path variants.
- [ ] Keep distinct reads and distinct expected writes as separate diagnostics,
      even when they access the same data. Preserve variant-specific guarantees.

Acceptance: the positive fixture produces one diagnostic with both branch
witnesses. Unrelated roots and proven exclusive branches produce no risk.
Unknown feasibility yields insufficient evidence. Repeated runs are deterministic.

### ASR-06: Evaluate completion guarantees and classify findings

- [ ] Recognize only supported, source-backed guarantees that apply to the same
      expected operation or version and establish completion before the read.
- [ ] Reject confirmation of a previous write to the same key as evidence for
      the expected write. A matching business key alone is insufficient.
- [ ] Check that an application completion response follows the relevant commit;
      waiting on publication or receiving a broker acknowledgement is insufficient.
- [ ] Treat fixed delays as timing assumptions, not completion guarantees.
- [x] Separate classification from confidence using the outcomes below.
- [ ] Record unrecognized synchronization and replica/read-model visibility limits.

| Outcome | Required interpretation |
|---|---|
| Potential risk | Related write and read established; no applicable completion guarantee identified in analyzed evidence |
| Insufficient evidence | Causality, identity, branch feasibility, ordering, or analysis coverage is unresolved |
| Guarantee identified | Supported evidence orders completion before the read, within the stated visibility scope |
| Not applicable | Proven disjoint data access, unrelated causes, or incompatible execution paths |

Acceptance: absence of a recognized guarantee never becomes proof that none
exists. A commit acknowledgement alone does not establish visibility on an
unproven replica or separate read model. UI wording never claims a proven race.
An unresolved variant cannot be hidden by a guaranteed variant of the same
diagnostic. Expose variant outcomes and retain any supported potential risk.

### ASR-07: Export diagnostics from the persisted snapshot

- [x] Add an additive diagnostic field to the graph export JSON model.
- [ ] Include stable diagnostic and step IDs, branch witnesses, source references,
      data/key matching evidence, guarantee evidence, and coverage limitations.
- [ ] Preserve endpoint identity through aggregation and flow deduplication.
- [ ] Export diagnostic-to-read mappings and path variants so collapsed nodes and
      repeated service occurrences preserve all associated diagnostics.
- [ ] Define behavior for missing diagnostics, old exports, and partial snapshots.
- [ ] Keep rule evaluation outside browser presentation code.

Acceptance: exports contain enough evidence to reproduce the explanation without
source access. Existing consumers remain compatible. Missing analysis is distinct
from a completed analysis with no findings.

### ASR-08: Present points to examine in the Flux view

- [x] Add a "Problèmes du flux · N" panel below the call-flow controls, with one
      row per diagnostic and a total computed after path deduplication.
- [ ] Give each diagnostic a flow-local display identifier such as P1 or P2.
      These identifiers indicate neither severity nor backlog priority; keep them
      stable while filtering or navigating the same exported flow.
- [ ] Show the affected data, writing and reading services, classification,
      confidence, and the missing or identified guarantee.
- [ ] Separate insufficient evidence from potential risks in the presentation.
- [ ] Expose guarantees separately from the problem count. Exclude not-applicable
      results from the problem list; show incomplete coverage independently.
- [ ] Provide filters by classification and affected data, with visible/total counts.
- [ ] Expand the selected row to explain causal links, key/query overlap,
      expected operation/version, guarantee assessment, and source evidence.
- [ ] Add "Précédent" and "Suivant" over the filtered diagnostic list, showing
      current position and disabling navigation at either end.
- [ ] Define empty, unavailable, incomplete, and no-selection states.
- [ ] Reset diagnostic selection when changing flows or leaving the Flux view.

Acceptance: selecting a flow exposes only its diagnostics. The list explains
that the read may occur before the asynchronous write completes. Architecture
controls do not appear in Flux, and Flux diagnostics do not leak into Architecture.
Selecting successive diagnostics updates the explanation and graph together.
Filtering out the selected diagnostic clears its focus and restores the full
flow. An empty result from incomplete analysis must never imply that the flow is safe.

### ASR-09: Visualize the causal split and supporting evidence

- [ ] In the complete graph, add a diagnostic-count badge at each affected read.
      Collapsed service nodes show unique associated diagnostic counts.
- [ ] Clicking a badge selects its sole diagnostic or filters the list to its
      associated diagnostics when several problems affect the same read.
- [ ] On diagnostic selection, reveal the common trigger or cross-treatment causal
      links and both branch witnesses. Highlight only the selected diagnostic.
- [ ] Attenuate unrelated paths and label the operations "Écriture attendue" and
      "Lecture à risque" for a risk, or "Lecture à examiner" for uncertain evidence.
- [ ] Mark asynchronous edges with dashed lines and synchronous calls with solid
      lines; label the distinction without relying on color alone.
- [ ] Add read/write badges and highlight the shared data target or proven mapping.
- [ ] Place the warning at the read and expose the synchronization gap explanation.
- [ ] Distinguish inferred uncertainty overlays from indexed dependency edges.
- [ ] Open file/line evidence and key propagation details from the diagnostic.
- [ ] Fit the highlighted paths in the viewport and provide a return to the full
      selected flow, restoring its camera and expansion state.
- [ ] Label that return action "Vue complète". Save the full-flow state before
      the first diagnostic focus; previous/next navigation must not overwrite it.
- [ ] Allow inspection of grouped path variants while keeping their uncertainty
      and guarantee assessments distinct.

Acceptance: the visualization never numbers asynchronous completion and the read
as a guaranteed total order. Repeated service occurrences retain their step IDs.
The user can inspect both branches, the common data, and the reason for the risk.
Multiple diagnoses on the same read remain individually selectable. Service
aggregation must not move a warning onto an unrelated read occurrence.

### ASR-10: Verify interactions and deliver the laboratory model

- [ ] Run focused extractor, detector, persistence, migration, and export tests
      against every ASR-02 fixture, including negative cases.
- [ ] Add browser regression tests for diagnostic selection, both highlighted
      branches, evidence inspection, flow switching, and state restoration.
- [ ] Verify a flow with several diagnostics: counts, shared-read badges,
      filtering, previous/next boundaries, grouped variants, and stable IDs.
- [ ] Verify that only one diagnostic is highlighted and that "Vue complète"
      restores the state saved before navigating across several diagnostics.
- [ ] Check graph/Flux toolbar separation and camera/expansion controls.
- [ ] Capture and inspect dark/light screenshots at desktop and constrained widths.
- [ ] Capture the selected diagnostic with both branches visible and a return to
      Architecture with no residual diagnostic controls.
- [ ] Capture the complete multi-diagnostic flow, two successive diagnostic
      selections, and the restored full flow. Inspect each rendered capture.
- [ ] Run Ruff, mypy, the full default test suite, and the Chrome browser suite.
- [ ] Run `uv run python scripts/check_companion_contracts.py` using temporary
      fixtures; this check must not mutate the laboratory index.
- [ ] Validate the story on the supermarket model. Add a dedicated fixture if its
      existing facts do not establish the required causal/key relationship.
- [ ] Generate the laboratory `architecture-ia.html`, synchronize its
      `architecture.html` viewing copy, and copy the POC export to
      `docs/models/simple-supermarket.html`.
- [ ] Confirm exports use the current source assets and contain the same diagnostics.
- [ ] Update affected specification sections and companion guidance if its workflow
      or public contracts change; review the final documentation.

Acceptance: record commands, results, screenshot paths, inspected UI states, and
any unavailable prerequisite here. Static HTML assertions alone cannot validate
interaction or visual acceptance. No screenshot or browser check may be claimed
when it was skipped.

## Definition of done

- [ ] All required tasks satisfy their acceptance criteria.
- [ ] Positive and negative cases validate the evidence and classification rules.
- [ ] Later causal treatments, stale confirmations, and query overlap cases have
      explicit expected outcomes and passing regression tests.
- [ ] Multiple diagnostics remain distinguishable without highlighting all paths
      simultaneously or merging different expected writes and read occurrences.
- [ ] Unknown data and incomplete analysis remain visible as limitations.
- [ ] The selected diagnostic displays the asynchronous write and synchronous
      read together, with source evidence and no invented completion ordering.
- [ ] Browser tests and inspected screenshots validate the delivered laboratory HTML.
- [ ] Documentation and generated models match the implementation.
