# Technical specification: systemlens

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
| SQLite schema or migrations | [Persistence](spec-tech/persistence.md) |
| Validation and quality gates | [Verification](spec-tech/verification.md) |

The functional specification owns observable CLI, MCP, export, and interaction
behavior. This specification owns implementation constraints and persisted data
contracts. [ADRs](ADR.md) own durable architectural choices.

## Detailed sections

- [Architecture](spec-tech/architecture.md)
- [Data model](spec-tech/data-model.md)
- [Export snapshot contract](spec-tech/export-snapshot.md)
- [Indexing](spec-tech/indexing.md)
- [Extractors](spec-tech/extractors.md)
- [Graph layout algorithms](spec-tech/graph-layout.md)
- [Persistence and compatibility](spec-tech/persistence.md)
- [Verification](spec-tech/verification.md)
