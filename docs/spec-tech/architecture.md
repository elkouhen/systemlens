# Architecture

Parent: [Technical specification](../SPEC-TECH.md).


### Pipeline at a glance

The pipeline is deliberately local:

```text
repository files → file hashes → Tree-sitter Java AST extractors
                 → endpoints/modules/properties → SQLite facts and relations
                 → CLI, MCP, graph and audit views
```

When explicitly enabled with `--kubernetes`, indexing also invokes the local
`kubectl` CLI once to list Deployments and StatefulSets. It aggregates the
declared requests and limits of regular containers (init containers are
excluded) and attaches a workload only when its Kubernetes name exactly matches
the indexed project name. This optional step can contact the current Kubernetes
API context; it is never enabled by default.

### Ownership boundaries

`scanner/` (a package; see `docs/ARCHITECTURE.md`) owns Java/Spring extraction.
`discovery/java/parser.py` provides cached Tree-sitter parsing and syntax
helpers. `domain/module_inventory.py` owns the
build-inventory facts shared with persistence and projections, without
depending on discovery. The `module_types/` compatibility package retains the
former import path.
`discovery/build/` discovers build units; `indexing/relations.py` derives typed
architecture relations from modules, endpoints and build dependencies.
`indexing/service.py` orchestrates the incremental transaction, while
`indexing/file_inventory.py` owns file eligibility and conservative full-rescan
promotion and `indexing/materializers.py` owns persisted contract projections.
`indexing/dto_inventory.py` materializes the conservative Java
DTO closure referenced by Kafka endpoints before that closure is persisted.
`storage/sqlite.py` owns SQLite connections, schema, transactions, and queries;
`storage/serialization.py` owns SQLite row and JSON conversions. The `store/`
compatibility package retains the former Python import.

Within `render/`, `graph_view_model.py` owns the architecture projection from
persisted facts to the browser data model, and `call_graph.py` owns the
call-graph projection and its flow-level transformations. `html_export.py`
only serializes those models and assembles the standalone document from the
HTML template and ordered browser assets. Rendering code must not be imported
by indexing or discovery code.

Impact analysis uses the persisted topology edges without re-parsing source
files. Its directed projection reverses REST edges, so a provider change
reaches its callers, and preserves Kafka edges, so a producer change reaches
its consumers. The path and impact queries share one deterministic topology
projection, while the path query retains explicit Kafka topic nodes for its
output model.

The impact traversal emits one deterministic shortest path per affected
microservice. It stops at depth 32 and returns at most 100 paths. The response
reports the two bounds and whether the result was truncated. A reached set
avoids cycles and duplicate work. Architecture snapshots also expose indexed
lookups for modules, endpoints, collections, and relations so catalog queries
do not rescan the complete inventory for every object.

`delivery/cli.py`, `delivery/mcp.py`, and the standard-library local HTTP server
in `delivery/web.py` are delivery layers over the domain modules. Shared CLI
option resolution, manifest validation, progress reporting, and architecture
output formatting live in `delivery/cli_support.py`. REST route normalization
lives in `scanner/rest_paths.py`, separate from framework-specific extraction.
The CLI export and
`systemlens web` both use `application/architecture_projection.py` to select the same
deployable, exportable service topology before choosing their output format.
The web command serves only an in-memory landing page and the existing
`/architecture` HTML
projection: it loads the persisted architecture snapshot and renders it for
that request. If no local index exists, its explicit POST action creates the
default configuration when needed and indexes the repository before rendering
the snapshot.
The web layer has no filesystem-serving route and writes SQLite only for this
explicit initial-index action; it does not persist credentials. It binds to loopback by
default; changing the host is an explicit user choice.

The separate `simpleweb` executable is a dependency-free static server for an
explicit report directory. It uses `SimpleHTTPRequestHandler` with that
directory as its only document root, has no application or write routes, and
binds to loopback by default. It is intentionally separate from `systemlens
web`, whose only route is the in-memory architecture projection.

### Future adapters

S3 support requires a separate conservative Java extractor for explicit AWS SDK
v1/v2 operations and configured bucket names, with dynamic bucket expressions
preserved as unresolved evidence. Kafka, MongoDB, S3, and Kubernetes runtime
signals require source-specific adapters and conservative evidence handling.

Future Kubernetes correlation must first use the current exact workload/service
name match. Its only fallback is a normalized token-sequence containment check
between a Deployment or StatefulSet name and an indexed service name. The
fallback succeeds only for one candidate; it records the matching strategy and
leaves zero or multiple candidates unresolved. It must never use an arbitrary
substring search or change persisted source topology.
