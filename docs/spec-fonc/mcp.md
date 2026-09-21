# MCP

Parent: [Functional specification](../SPEC-FONC.md).


The MCP server exposes a deliberately small control surface for the
index-then-enrich workflow. It no longer mirrors every read-only CLI command:

| Tool | Purpose |
|---|---|
| `index_repository` | Index or refresh the current repository; preserves graph enrichment facts. |
| `graph_fact_exists` | Check a semantic node/edge fact before proposing it. |
| `add_graph_fact` | Add a user assertion or an agent-produced assertion from the companion skill, with confidence and optional relative evidence; rejects semantic duplicates. |
| `import_graph_facts` | Validate and atomically upsert a `systemlens-ai-graph-v1` manifest into one enrichment namespace, optionally removing stale facts for a complete snapshot. |
| `remove_graph_fact` | Remove an assertion previously added through MCP; never removes extracted source facts. |
| `list_graph_facts` | List the persisted enrichment layer. |
| `architecture_graph` | Return the complete generic dependency graph (services, APIs, Topics, Data resources and external resources) merged with persisted enrichment facts. |

Only `index_repository` creates or refreshes source-derived facts. Enrichment
facts are stored separately in `graph_facts`, survive reindexing, and are never
treated as source evidence.

Nodes require `fact_type=node`, `kind` and `name`;
edges require source/target kinds and names plus `relation`. Evidence paths are
relative to the indexed repository and may not escape it.

`add_graph_fact` remains an additive single-fact API and rejects duplicates.
For iterative analysis, use `import_graph_facts`: it reconciles by the
manifest namespace and stable node/edge id, replacing the complete stored
value for an existing AI fact. A partial manifest never removes facts; a
manifest with `mode=complete` (or an explicit `complete=true`) removes stale
facts only from that namespace. Source-derived facts are stored separately and
are never overwritten. The import is transactional and returns inserted,
updated and removed counts.

For generic middleware, use `kind=data_schema` or `kind=message_channel`, set
`technology` to the concrete implementation, and put provider-specific facts
such as database/schema/table, exchange/queue or partition in `metadata`.

