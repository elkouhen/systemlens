# Backlog: async write and sync read consistency

Status: planned; implementation has not started.
Owner: SystemLens implementer. Reviewers: product owner and architecture
maintainer.

The detector already supports explicit identity, request-order, and visibility
evidence. This backlog extends source extraction; it does not establish new
defects in the existing detector. The
[plan](docs/plans/async-write-read-consistency.md) records completed corrections.
The [algorithm contract](docs/spec-tech/algorithms.md#8-data-consistency-diagnostics)
owns current behavior. The tasks below are future acceptance criteria, not
claims about implemented capabilities.

## 1. Index and scope

| Document | Purpose |
|---|---|
| [Consistency plan](docs/plans/async-write-read-consistency.md) | Findings, completed corrections, deferred extensions. |
| [Archived consistency backlog](BACKLOG.consistency-diagnostics.md) | Historical rendering tasks. |
| [Earlier backlog](BACKLOG.previous.md) | Historical index of data-operation work. |

## 2. Scope and invariants

The first increment must demonstrate one source-derived asynchronous write
request followed by a synchronous read of the same record. Start with a
bounded Java message-handler scenario; C0 selects and documents the exact
framework APIs before extraction work begins.

Preserve canonical resource identity, per-pair uncertainty, and existing
manifest behavior. Equal topics, payload types, display names, or source lines
are not proof of record identity or execution order. Unknown evidence must not
become a guarantee. Exports consume persisted facts without re-parsing source.

General mutable heap analysis, arbitrary serialization, broker scheduling,
query-overlap inference, and concurrent-write diagnostics are out of scope.
Unsupported cases retain their current conservative classification.

## 3. Delivery order and dependencies

IDs C1-C6 are retained from the previous backlog; C0 is the new scoping gate.
All tasks are pending. Validation availability must be checked, not assumed.

| ID | Deliverable | Prerequisites |
|---|---|---|
| C0 | Supported scenario, evidence mapping, and regression matrix | None |
| C1 | Bounded record-key value propagation | C0 |
| C2 | Message/request identity propagation | C1 |
| C5 | Request-before-read evidence and first end-to-end risk | C2 |
| C4 | Scoped visibility evidence and end-to-end guarantee | C5 |
| C3 | Occurrence-scoped branch exclusion | C5 |
| C6 | Recorded validation for each delivered increment | Each increment under validation |

C4 and C3 are independent follow-up increments after C5. C6 runs after each
increment; its initial baseline can run after C0 and does not block drafting
fixtures. Missing tools block the affected checks, not unrelated work.

## 4. Work packages and acceptance criteria

### C0. Define the first supported scenario

Document exact send, receive, serialization, storage, and synchronous-read APIs
in a small Java fixture. Prefer existing fixtures and extraction helpers.
Map each required fact to its source evidence and persisted representation.
Do not start C1 until this mapping is reviewed.

Acceptance:

- The fixture identifies the send, receiver write, read occurrence, canonical
  resource, record key, and causal scenario. Shared storage is established
  independently of matching entity names.
- The support matrix names supported APIs and rejected variants. It separates
  record identity, causal identity, request ordering, and visibility.
- A regression matrix states expected persisted facts and diagnostic results
  for C1-C5. Baseline behavior is recorded before changes.
- Any required schema or interface extension is specified before coding,
  including additive migration and old-snapshot behavior.

### C1. Propagate record keys through a bounded value path

Support scalar copies and explicitly resolved payload-field assignments used
by C0. Follow reaching definitions at the operation site, not expression text.
Leave arbitrary object aliasing and unknown serializers unsupported.

Acceptance:

- A supported producer-to-consumer mapping preserves canonical key identity
  through construction, serialization, and receiver field access.
- A scalar copy preserves identity until reassignment. A changed value must
  not retain the original identity; ambiguous mutations remain unknown.
- Equal field names in unrelated objects, shadowed variables, and unknown
  transformations do not establish equal keys.
- Re-indexing and reloading preserve identities and relative source evidence.
  Existing literal and final-scalar propagation cases remain unchanged.

### C2. Propagate causal identity without conflating messages

Connect the specific send and receiver occurrence from C0 using supported
framework and payload mappings. Record the evidence separately from the key.
The same record key does not imply the same request or message.

Acceptance:

- The supported path gives the linked write and read compatible causal
  evidence that survives persistence.
- Two sends to one topic, including sends for the same record, are not merged
  merely because their topic, payload type, or key matches.
- Missing or conflicting correlation evidence remains insufficient. A causal
  match alone neither establishes request order nor creates a guarantee.

### C5. Derive request-before-read order

From C0's supported execution semantics, link the asynchronous request to a
specific subsequent read with `request_before_reads`. Use resolved control
flow and call behavior, not lexical line order or call-graph reachability.

Acceptance:

- With resource, key, and causal evidence supplied by C1-C2, the positive
  source fixture persists the relation and reports
  `classification=potential_risk` when visibility is unproven and no other
  required evidence is missing. This is the existing JSON classification;
  the algorithm design calls this case `potential_async_write_read`.
- A read before the send receives no forward relation. Unresolved callbacks,
  unordered execution, and shared-root-only paths remain insufficient.
- The relation targets the correct read when a flow contains several reads.
  Another exact pair must not suppress an uncertain pair.
- An index-to-reload-to-JSON/HTML test observes the same classification and
  explains the missing visibility evidence without re-parsing source.

### C4. Derive a scoped visibility barrier

Extend C0 with one documented framework barrier whose semantics establish
visibility to the relevant read. Identify request, flush, commit, and visible
events separately. Neither flush nor commit is a universal visibility barrier.

Acceptance:

- A supported barrier persists `visibility_before_reads` for one read and
  produces `guarantee_identified` when all existing detector requirements,
  including completion and version evidence, also hold.
- Broker acknowledgement, method return, transaction annotation, flush alone,
  rollback, and matching versions alone do not establish that guarantee.
- A primary-store commit does not establish replica or cache visibility.
  A barrier for another record or read does not apply to this pair.
- If C0's framework has no provable barrier, record that limitation and select
  a supported follow-up fixture before implementation. Do not weaken the
  guarantee contract to complete this task.

### C3. Exclude incompatible branches within one occurrence

Initially support directly resolved alternatives of one `if/else` in a single
execution occurrence. Do not infer general predicate equivalence or exclusivity
across separate invocations.

Acceptance:

- A write and read on proven exclusive alternatives of that occurrence
  produce no async-write/read candidate pair.
- The same source sites in distinct invocations are not excluded by this rule.
  Compatible paths preserve their existing diagnostics.
- Unknown predicates or occurrence identity do not suppress a pair or prove
  ordering. Loops and repeated handlers remain conservative unless bounded
  occurrence evidence exists.

### C6. Validate and record availability

The plan reports earlier localhost, Chrome, and `scc` limitations. These are
historical observations, not verified blockers in the current environment.

Acceptance:

- Record baseline and per-increment commands, outcomes, and any actual missing
  prerequisites. Separate pre-existing failures from regressions.
- Run focused regression cases, including `tests/test_flow_consistency.py`,
  then Ruff, mypy, and the full default suite for extraction/storage changes.
- Run browser and companion checks when applicable using the commands below.
  A blocked check stays explicitly unverified; it is never reported as passed.
- C6 is complete for an increment only when its required checks pass. Record
  unresolved blockers without marking that increment fully validated.

## 5. Definition of done and validation

Every implementation task includes positive and negative behavior tests,
persisted evidence checks, old-index compatibility, and uncertainty fallback.
Store source paths relative to the indexed root; never persist local roots.
Document performance implications and avoid unbounded path expansion.

Update `docs/SPEC-FONC.md` for observable changes and `docs/SPEC-TECH.md` plus
`docs/spec-tech/algorithms.md` for model or algorithm changes. Record durable
design decisions in `docs/ADR.md`, and update the consistency plan's status
only after delivery. Update the companion skill when its workflow is affected.

```bash
uv run ruff check src tests
uv run pytest tests/test_flow_consistency.py
uv run mypy
uv run pytest
```

Also run the relevant extractor and persistence tests for each increment.
For generated HTML interactions and workflow changes, respectively:

```bash
SYSTEMLENS_CHROME_BIN=/path/to/chrome uv run pytest -m slow tests/test_browser_export.py
uv run python scripts/check_companion_contracts.py
```

Use an installed Chrome executable instead of the placeholder. If dependencies
are missing, use the documented `uv sync --group dev` setup. Companion checks
must not modify the laboratory checkout or reuse its persisted index.
