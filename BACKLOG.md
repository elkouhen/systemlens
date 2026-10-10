# Call-flow diagnostic focus backlog

Selecting a consistency problem must reveal its asynchronous write branch and
synchronous read branch directly in the selected call graph. The user must see
their common trigger, affected data, and missing completion guarantee.

Status: in progress. Diagnostic endpoint export, click focus state, branch styling,
and `Vue complète` are implemented. Exact occurrence mapping, browser validation,
and source-backed branch extraction remain open.
Tracking: this file only; do not create GitHub tickets.
Owner: implementing developer. Visual acceptance: product owner.
Documentation language: English. Visible interface labels: French.

Implemented slice: `flow_diagnostics` now carries async and sync endpoint
identities when the persisted steps provide them. The Flux list selects a
diagnostic, captures the call-tree state, expands the selected tree, adds
`is-diagnostic-async` and `is-diagnostic-sync` styling to matching arcs, and
restores the captured state through `Vue complète`. Steps without endpoint
identities remain explicitly unavailable for arc highlighting.

## Scope and evidence

This backlog covers diagnostic selection, exported path evidence, graph focus,
and restoration of the full view. Automatic extraction of new Java data-flow
evidence, runtime traces, and application fixes are outside this task.
CDN dependencies, ARIA semantics, and keyboard navigation remain deferred.

The [diagnostic model](src/systemlens/application/flow_consistency.py) exposes
flow identity and read/write step numbers. The
[call-graph projection](src/systemlens/render/call_graph.py) carries endpoint
transitions and occurrence information. The work must connect these identities
before the browser can highlight the correct service occurrences.

The [Flux renderer](src/systemlens/render/assets/graph/55-code-flows.js) and
[graph rebuild module](src/systemlens/render/assets/graph/10-rebuild.js) present
diagnostic summaries. Their summaries alone do not establish branch witnesses.
The proposed behavior below is an implementation target, not a shipped claim.

Use the [checkout fixture](examples/checkout-single-call-graph.json) and its
[generator](scripts/generate_checkout_call_graph.py) as the primary synthetic
acceptance scenario. Its root is `checkout-root`; the scenario declares the
write path `[1, 8, 10]` and read path `[1, 14, 15]`.
These numbers identify fixture interactions, not runtime timestamps.

## Target interaction

1. The user selects a flow and sees its problems with local identifiers `P1`,
   `P2`, and so on, including badges on the affected read occurrences.
2. Clicking a problem or its badge selects that problem and expands the paths
   needed to show the common trigger, publication, expected write, and read.
3. The graph displays the asynchronous branch in orange and the synchronous
   branch in blue. Other calls remain visible with reduced emphasis.
4. A compact detail shows the data identity, symbolic key, expected operation,
   classification, confidence, guarantee assessment, and source references.
5. `Précédent` and `Suivant` select another problem in the filtered list.
6. `Vue complète` restores the camera and expansion state captured before focus.

Path emphasis must not create an execution edge from the write to the read.
Both branches may execute independently after their common trigger. The read
badge states a potential risk: `Peut lire avant la fin de l’écriture`.

Orange identifies the branch containing an asynchronous boundary. Dashed arcs
identify asynchronous transport; HTTP segments within that branch remain solid.
Labels identify write and read roles so interpretation does not depend on color.

Focus preserves surrounding calls to keep the problem in context. A separate
isolated subgraph was considered, but would require users to reconstruct that
context. The selected approach costs additional occurrence mapping and camera
state management, covered by the tasks below.

## Delivery order

| Task | Priority | Depends on | Deliverable |
|---|---|---|---|
| FOCUS-01 | P0 | None | Branch witness contract |
| FOCUS-02 | P0 | FOCUS-01 | Export and occurrence mapping |
| FOCUS-03 | P0 | FOCUS-02 | Selection and restoration state |
| FOCUS-04 | P0 | FOCUS-02, FOCUS-03 | In-graph branch highlighting |
| FOCUS-05 | P1 | FOCUS-03, FOCUS-04 | Problem list, badges, and details |
| FOCUS-06 | P1 | FOCUS-01 | Representative fixtures |
| FOCUS-07 | P1 | FOCUS-04, FOCUS-05, FOCUS-06 | Browser validation and exports |

P0 items establish correct path selection. P1 items complete the interaction
and its verification. All items are required to deliver this task.

## FOCUS-01: Define branch witnesses

- [ ] Audit diagnostic IDs, flow IDs, endpoint IDs, transition IDs, and rendered
      occurrence IDs across import, persistence, export, and browser rendering.
- [ ] Define a stable diagnostic identity and branch witnesses containing the
      root flow, common prefix, causal split, ordered transitions, and terminal
      read/write references. Include the owning handler occurrence for each access.
- [ ] Represent source evidence, expected operation/version, data identity,
      symbolic key, classification, confidence, and missing evidence explicitly.
- [ ] Separate complete, partial, unavailable, and truncated witness coverage.
      Preserve ambiguity and variant-specific paths and guarantees.
- [ ] Define additive compatibility for old snapshots and manifests. Run any
      required migration before the indexing transaction.
- [ ] Update the authoritative sections linked from [SPEC-TECH](docs/SPEC-TECH.md)
      and [SPEC-FONC](docs/SPEC-FONC.md). Record durable representation decisions
      in a new [ADR](docs/ADR.md), preserving accepted entries.

Acceptance: two occurrences of the same service remain distinguishable.
A step number or service name alone cannot establish a highlight target.
Missing witnesses produce an explicit unavailable or partial focus state.

## FOCUS-02: Preserve witnesses through export

- [ ] Carry branch evidence through the fact importer and persisted snapshot.
      Export must succeed without source files and must not reparse them.
- [ ] Resolve witnesses to existing call-graph transitions using exact endpoint
      and occurrence identities; never select an arbitrary shortest path.
- [ ] Preserve mappings through flow aggregation, deduplication, repeated calls,
      fan-out, fan-in, cycles, and collapsed occurrences.
- [ ] Associate diagnostics with their selected root graph, including diagnostics
      originating in reachable handler fragments. Deduplicate equivalent pairs.
- [ ] Export mappings for badges, shared prefixes, read/write occurrences, and
      available path variants. Retain distinct expected writes and reads.
- [ ] Use indexed lookups and bounded traversal. Name limits, report truncation,
      and document complexity; do not enumerate every possible path.
- [ ] Validate relative evidence paths and keep runtime values and secrets out
      of persisted keys, fixtures, and exports.

Acceptance: exported witnesses select exactly the declared fixture branches.
Repeated runs produce the same mapping. An unresolved segment remains unknown;
it is never replaced with a plausible graph edge.

## FOCUS-03: Implement focus state and restoration

- [ ] Store selected flow, selected diagnostic, selected variant, and the full-view
      camera and expansion snapshot in the Flux state owner.
- [ ] Capture the snapshot once when entering diagnostic focus. Switching
      problems must not overwrite it with an already focused state.
- [ ] On selection, expand only the ancestors needed to reveal both branches.
      Center their combined bounds while keeping labels readable.
- [ ] Preserve selection through layout refreshes and cancel stale render work
      when the user selects another diagnostic before rendering finishes.
- [ ] Make `Vue complète` restore zoom, pan, expanded and collapsed occurrences,
      and remove every diagnostic emphasis.
- [ ] Clear focus on flow changes or leaving Flux. Filtering out the selected
      problem restores the full view; entering another flow starts fresh state.

Acceptance: selecting P1, then P2, then `Vue complète` restores the original
view. Rapid clicks cannot apply a highlight from the previous flow.

## FOCUS-04: Highlight both branches in the graph

- [ ] Emphasize the common prefix and mark the causal split.
- [ ] Render the asynchronous write branch in orange and the synchronous read
      branch in blue, with transport-specific line styles defined above.
- [ ] Attach `Écriture attendue` and `Lecture à risque` labels to the exact
      access occurrences. Where accesses have no graph node, add anchored
      annotations without creating invented service dependencies.
- [ ] Attenuate unrelated nodes and calls while retaining their context.
      Avoid highlighting every occurrence of a service by its name.
- [ ] Preserve existing protocol labels, inspections, zoom, and pan.
- [ ] Show unknown segments and traversal limits explicitly. Never render a
      disconnected path as a complete witness.
- [ ] Integrate a compact focus legend in Flux. Retain the architecture legend's
      existing scope and remove focus styling when returning to Architecture.

Acceptance: both inventory occurrences in the checkout scenario are distinct;
the write and read each receive their own marker. No arrow suggests guaranteed
write-before-read ordering. Shared segments remain legible in both themes.

## FOCUS-05: Connect the list, badges, and explanation

- [ ] Show `Problèmes du flux · N` for the selected flow only. Assign stable local
      display IDs independently of severity; filtering must not renumber them.
- [ ] Count potential risks and review candidates separately. Present guarantees
      outside the problem count and explain missing analysis coverage.
- [ ] Add read-occurrence badges; collapsed nodes show unique problem counts.
      A shared badge opens a choice restricted to that occurrence's problems.
- [ ] Make list selection and badge selection activate the same focus action.
- [ ] Provide classification and data filters, visible/total counts, and
      `Précédent`/`Suivant` with disabled end states and the current position.
- [ ] Show branch evidence, symbolic key/query overlap, expected operation,
      guarantee assessment, confidence, and source links for the active problem.
- [ ] Replace redundant summary banners with the active explanation. Use precise
      wording for insufficient evidence and guarantees rather than calling them risks.
- [ ] Define no-selection, no-problem, old-export, partial-witness, unavailable,
      and filter-empty states. An empty list must not claim the flow is safe.

Acceptance: P1 and P2 focus different read occurrences and update the detail
together. If focus is unavailable, the explanation states which evidence is
missing and does not highlight an unrelated path.

## FOCUS-06: Extend fixtures and regression cases

- [ ] Enrich the checkout fixture with exact witness references derived from
      its declared interactions and handler occurrences. Keep it synthetic.
- [ ] Add a multi-problem fixture with distinct reads in the same service,
      shared prefixes, and one read affected by multiple expected writes.
- [ ] Add equivalent path variants, a cycle, a collapsed branch, ambiguous
      mapping, missing endpoints, truncation, and an old manifest without witnesses.
- [ ] Include a guarantee and insufficient evidence alongside potential risks
      to test counts, labels, and focus availability.
- [ ] Test import, persistence, export, and diagnostic-to-occurrence mapping.
      Assert exact branch membership and exclusion of unrelated calls.

Acceptance: the fixtures fail if service-name matching highlights every
inventory occurrence or if equivalent paths inflate a problem count.

## FOCUS-07: Validate the visible interaction and regenerate models

- [ ] Add browser tests that click P1 and P2 and inspect highlighted arcs,
      occurrence badges, expanded ancestors, and the selected explanation.
- [ ] Verify `Vue complète`, filtering, flow changes, rapid selection, and
      leaving Flux. Check camera restoration within renderer precision.
- [ ] Run the focused tests, Ruff, and the applicable contract checks prescribed
      by [AGENTS.md](AGENTS.md). Run the Chrome browser suite when available.
- [ ] Capture and inspect screenshots of the complete graph, P1 focus, P2 focus,
      and restored view in light and dark themes, including a constrained viewport.
- [ ] Regenerate the checkout HTML from its JSON and verify the actual generated
      file in the browser. Keep the generator and fixture reproducible.
- [ ] If the supermarket export is regenerated, synchronize `architecture-ia.html`
      with `docs/models/simple-supermarket.html` as required by AGENTS.md.
- [ ] Document any unavailable browser prerequisite and unverified behavior.
      Do not mark visual acceptance complete from string-level checks alone.

Acceptance: screenshots and browser assertions demonstrate that selecting a
problem reveals its two exact branches and that returning to the full graph
restores the previous view. Complete this backlog only after all criteria pass.
