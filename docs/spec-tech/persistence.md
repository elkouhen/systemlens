# Persistence and compatibility

Parent: [Technical specification](../SPEC-TECH.md).


SQLite schema migration is additive where possible. `files` stores hash state,
`endpoints` stores source facts, and normalized tables store modules,
dependencies and relations. Each module has a collision-safe identity used by
endpoints and relations; its artifact/project name remains a display alias.
Schema version 39 adds the `data_operations` table: one row per supported
source invocation with its repository-relative path, owner method, line
range, technology, raw operation, action, read and write facets, resource
identity, key expression and identity, confidence, unresolved reason, and the
transaction, guard, and value-dependency columns. It is rebuilt with the
module inventory on each index run, loses the rows of deleted files, and is
the source of data steps; an index created before it projects the module
inventory JSON until reindexed. Schema version 36 adds the source-backed
`codeql_methods` projection alongside the complete `codeql_call_edges` graph. Schema version 35 added `jpa_entities`
and `jpa_dtos` JSON columns to module
inventory with empty defaults for existing indexes. Migration runs before
indexing begins.

The optional `runtime_observations` and `runtime_error_groups` tables hold
aggregated APM traffic and error groups (ADR-60). It is created on the first write without a schema version change.
Indexing never clears it, and a read-only open of an older index without the
table returns no observations. See
[runtime observations](runtime-observations.md#2-persistence).

The database filename remains `findings.db` for backward compatibility; new
AST-only behavior must not infer that it contains security findings.

SystemLens stores this database under `.systemlens/`. It intentionally does not
load the former `.cccr/`, `.archlens/`, or `.codeatlas/` state directory: the
product rename requires a fresh `systemlens init` and `systemlens index` so the
configuration and index namespace remain unambiguous.

The endpoint-inventory signature in `meta` is bumped whenever extractor
behaviour changes. This forces a complete refresh before new facts are served.

After the CodeQL method-call graph is built, its source-backed method nodes and
call edges are persisted before input-to-output reconstruction starts. The
index records
`codeql_call_graph_status=complete` for a completed extraction, or `partial`
when the extraction reaches its timeout, and commits a partial-snapshot checkpoint;
`codeql_call_graph_edge_count` records the number of persisted edges. This
checkpoint is independent from the later flow-join cursor.

CodeQL is a local executable invoked by the indexer when the configured call
graph engine is enabled. The automatic CodeQL database is an analysis input,
cached in `.systemlens/codeql/` until the projected sources change (ADR-84);
the persisted `codeql_methods` and `codeql_call_edges` tables are the durable
SystemLens projections used by exports and flow reconstruction. Exports never
reopen the database or reparse source files.

With `index --module NAME --refresh-codeql-view`, the method projection is
refreshed only for source paths below the selected module. Existing rows for
other modules are retained, while the call-edge graph remains global so helper
methods outside the module can still be traversed. The refresh is additive to
the schema and does not copy CodeQL query output outside the persisted method
rows.

After the `input → output` calculation completes, the provisional merged flow
snapshot is committed again before topology reconciliation and final status
publication. `codeql_input_output_status` is `complete` for a complete CodeQL
pass or `partial` after a timeout, and `codeql_input_output_flow_count` records
the interprocedural flow count at that checkpoint.

Long-running CodeQL indexing can publish an explicit partial checkpoint after
each completed analysis project. The checkpoint persists the currently known
`code_flows` together with `code_flow_snapshot_status=partial`; the completed
index replaces those flows after reconciliation and sets the status to
`complete`. A missing or partial code-flow signature causes the next index to
retry the interprocedural stage.

The flow stage unsets `code_flow_signature` and sets
`code_flow_snapshot_status=partial` before its first checkpoint, whatever the
engine. A run that stops after any checkpoint, including the call-graph
checkpoint, therefore publishes new file hashes with a snapshot marked
`partial`. The next incremental index runs the flow stage again while the
status is `partial`, even when no file changed.

Before that first checkpoint, the stage also drops the persisted flows that
name a deleted Java file or a purged endpoint, so they cannot survive an
interrupted run. The cost is one read of the flow table per index.

Every persisted and provisional `code_flows` snapshot has unique flow IDs.
Collision handling preserves the first stable ID and adds a deterministic
content-derived suffix to later colliding candidates. This rule applies before
checkpoint commits, final indexing persistence, and `flows calculate`.

The SQLite replacement methods apply the same boundary protection to generated
IDs for endpoints, relations, integration methods, findings, and code chunks.
Exact duplicates on composite keys are collapsed; conflicting OpenAPI,
AsyncAPI, dependency, diagnostic, or CodeQL-edge rows are rejected with a
domain-level `StoreError` before the previous snapshot is deleted.

## Concurrent writers

Opening a writable store on a current schema runs no write statement: each
migration reads the schema first and writes only when something is missing.
A second `systemlens index` therefore opens the database without waiting and
meets the lock only at the index transaction's `BEGIN IMMEDIATE` (ADR-7).
A writer waits `BUSY_TIMEOUT_SECONDS` (5 s) for another writer's lock, then
fails with `StoreLockedError`. The CLI prints
`Indexation impossible : Index verrouillé (...)` and exits with code 2.

`commit_checkpoint` commits, then opens a new `BEGIN IMMEDIATE`. Python's
`sqlite3` cannot keep the lock across a commit, so another writer can take
it in that gap. The run then stops with a `StoreLockedError` that says the
published snapshot stays partial. That snapshot is the checkpoint, marked
`code_flow_snapshot_status=partial` without a code-flow signature, so the
other writer and the next index both rerun the flow stage.
