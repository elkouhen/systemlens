# Functional specification: systemlens

This document owns observable behavior: CLI, export, extraction, and
compatibility contracts. It does not define internal package ownership or the
global visual language.

This page routes readers to the observable contract. The detailed sections live
in `docs/spec-fonc/` so each topic can be read and reviewed independently.

## Reading guide

| Task | Read |
|---|---|
| Configure an index | [Configuration](spec-fonc/configuration.md) |
| Use the CLI | [CLI](spec-fonc/cli.md) |
| Change endpoint or flow extraction | [Extraction rules](spec-fonc/extraction-rules.md) |
| Change export behavior | [HTML export](spec-fonc/html-export.md) and [port rendering](spec-fonc/port-rendering.md) |
| Change graph rendering | [UX/UI rules](UX-UI.md), [module navigation](spec-fonc/modules.md), and [graph layout](spec-tech/graph-layout.md) |
| Change HTML presentation or interaction | [UX/UI rules](UX-UI.md) |
| Change incremental indexing | [Incrementality](spec-fonc/incrementality.md) |
| Use or change the AI graph manifest | [AI graph manifest](spec-fonc/ai-graph-manifest.md) |

The keywords **MUST** and **MUST NOT** identify compatibility requirements.
The section files are normative for the behavior they own.

The extraction rules include interface calls through inherited implementations
and declared method return types, with explicit fallback confidence limits.

## Async-write / sync-read diagnostics

The selected Flux view MAY expose consistency diagnostics derived from the
persisted flow snapshot. A diagnostic pairs an explicitly asynchronous
`data_write` with an explicitly synchronous `data_read` when both handlers are
reachable from the same persisted trigger and their resource evidence overlaps.
The wording MUST describe a potential ordering risk, never an observed runtime
race.

Each pair is classified as `potential_risk`, `guarantee_identified`,
`insufficient_evidence`, or omitted as not applicable. Different key
expressions, conflicting causal IDs, and incomplete query overlap remain
explicit limitations. A visibility guarantee requires an explicit visible
completion and matching non-empty expected versions. Several pairs are listed
independently. The selected call graph shows pair badges on the matching
microservice occurrences; selecting a badge highlights its pair. An export
without the optional diagnostic array remains a valid older export.

The selected call graph MAY also show `potential_concurrent_read_write`,
`potential_concurrent_write`, `potential_lost_update`, `potential_dirty_read`,
`potential_read_write_anomaly`, or `conflict_protection_identified` when their
explicit flow evidence is available. Concurrency findings require positive
concurrency metadata; an async branch alone is insufficient. Lost-update
findings require paired read-to-write value dependencies and distinct named
transactions. Dirty-read findings require an explicitly uncommitted write and
read-uncommitted isolation evidence. Tooltips show the evidence and unresolved
conditions. Missing metadata MUST NOT be treated as proof of safety or as proof
of concurrency. These static findings do not assert a runtime incident.

## Detailed sections

- [Configuration](spec-fonc/configuration.md)
- [CLI](spec-fonc/cli.md)
- [Extraction rules](spec-fonc/extraction-rules.md)
- [HTML export](spec-fonc/html-export.md)
- [Port rendering](spec-fonc/port-rendering.md)
- [Module navigation](spec-fonc/modules.md)
- [Incrementality and freshness](spec-fonc/incrementality.md)
- [AI graph manifest](spec-fonc/ai-graph-manifest.md)
- [UX/UI rules](UX-UI.md)
