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
| Change export behavior | [HTML export](spec-fonc/html-export.md), [XLSX diagnostic export](spec-fonc/xlsx-export.md), [port rendering](spec-fonc/port-rendering.md), and [placement](spec-fonc/placement.md) |
| Change graph views | [Layered view](spec-fonc/layered-view.md) and [module rendering](spec-fonc/modules.md) |
| Change HTML presentation or interaction | [UX/UI rules](UX-UI.md) |
| Change incremental indexing | [Incrementality](spec-fonc/incrementality.md) |
| Check a limitation | [Boundaries](spec-fonc/boundaries.md) |

The keywords **MUST** and **MUST NOT** identify compatibility requirements.
The section files are normative for the behavior they own.

The extraction rules include interface calls through inherited implementations
and declared method return types, with explicit fallback confidence limits.

## Detailed sections

- [Configuration](spec-fonc/configuration.md)
- [CLI](spec-fonc/cli.md)
- [Extraction rules](spec-fonc/extraction-rules.md)
- [HTML export](spec-fonc/html-export.md)
- [XLSX diagnostic export](spec-fonc/xlsx-export.md)
- [Port rendering](spec-fonc/port-rendering.md)
- [Placement and interaction](spec-fonc/placement.md)
- [Layered view](spec-fonc/layered-view.md)
- [Module rendering](spec-fonc/modules.md)
- [Incrementality and freshness](spec-fonc/incrementality.md)
- [Boundaries](spec-fonc/boundaries.md)
- [UX/UI rules](UX-UI.md)
