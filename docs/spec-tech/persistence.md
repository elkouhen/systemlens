# Persistence and compatibility

Parent: [Technical specification](../SPEC-TECH.md).


SQLite schema migration is additive where possible. `files` stores hash state,
`endpoints` stores source facts, and normalized tables store modules,
dependencies and relations. Each module has a collision-safe identity used by
endpoints and relations; its artifact/project name remains a display alias.
Schema version 36 adds the source-backed `codeql_methods` projection alongside
the complete `codeql_call_edges` graph. Schema version 35 added `jpa_entities`
and `jpa_dtos` JSON columns to module
inventory with empty defaults for existing indexes. Migration runs before
indexing begins.

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

Every persisted and provisional `code_flows` snapshot has unique flow IDs.
Collision handling preserves the first stable ID and adds a deterministic
content-derived suffix to later colliding candidates. This rule applies before
checkpoint commits, final indexing persistence, and `flows calculate`.

The SQLite replacement methods apply the same boundary protection to generated
IDs for endpoints, relations, integration methods, findings, and code chunks.
Exact duplicates on composite keys are collapsed; conflicting OpenAPI,
AsyncAPI, dependency, diagnostic, or CodeQL-edge rows are rejected with a
domain-level `StoreError` before the previous snapshot is deleted.
