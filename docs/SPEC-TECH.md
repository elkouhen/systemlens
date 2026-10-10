# Technical specification: systemlens

This document owns implementation constraints: package architecture,
persistence, data contracts, algorithms, and verification. It does not define
product scope, public command behavior, or visual interaction semantics.

This page routes readers to the implementation contract. The detailed sections
live in `docs/spec-tech/` so architecture, storage, algorithms, and verification
can evolve without one oversized specification.

## Reading guide

| Change | Read |
|---|---|
| Architecture ownership or adapters | [Architecture](spec-tech/architecture.md) |
| Persisted facts or materialized contracts | [Data model](spec-tech/data-model.md) |
| Export inputs and snapshot rules | [Export snapshot](spec-tech/export-snapshot.md) |
| Index orchestration or flow algorithms | [Indexing](spec-tech/indexing.md) |
| Source extraction | [Extractors](spec-tech/extractors.md) |
| Browser coordinates, camera, or placement | [Graph layout](spec-tech/graph-layout.md) |
| Visible HTML presentation or interaction | [UX/UI rules](UX-UI.md) |
| SQLite schema or migrations | [Persistence](spec-tech/persistence.md) |
| Validation and quality gates | [Verification](spec-tech/verification.md) |

The indexing section specifies Java signature identity, inherited interface
implementations, and declared return types used by the call-graph fallback.
HTML delivery adapters pass a typed `GraphExportInput` snapshot to the renderer;
the legacy `render_graph_html` argument surface remains available for
compatibility.

The HTML resource catalogue is a projection of that persisted snapshot. Its
browser module builds stable entries for graph resources, HTTP routes, indexed
contracts, DTOs and persistence descriptions. It does not parse source files,
guess owners, or create facts that are absent from the snapshot. Filtering is a
view operation over the projection and keeps source evidence attached to each
entry.

Flow consistency analysis is also a snapshot projection. `CodeFlowStep` keeps
optional branch, resource, key, causal identity, expected version, completion,
and query-shape metadata; old SQLite JSON rows load with these fields unknown.
The detector produces deterministic pair diagnostics without reparsing source
files. It reports incomplete evidence instead of treating an empty result as a
proof of safety, and the HTML layer only presents the exported diagnostics.

## Detailed sections

- [Architecture](spec-tech/architecture.md)
- [Data model](spec-tech/data-model.md)
- [Export snapshot contract](spec-tech/export-snapshot.md)
- [Indexing](spec-tech/indexing.md)
- [Extractors](spec-tech/extractors.md)
- [Graph layout algorithms](spec-tech/graph-layout.md)
- [Persistence and compatibility](spec-tech/persistence.md)
- [Verification](spec-tech/verification.md)
- [UX/UI rules](UX-UI.md)
