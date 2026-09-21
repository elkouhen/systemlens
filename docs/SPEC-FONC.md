# Functional specification: systemlens

This page routes readers to the observable contract. The detailed sections live
in `docs/spec-fonc/` so each topic can be read and reviewed independently.

## Reading guide

| Task | Read |
|---|---|
| Configure an index | [Configuration](spec-fonc/configuration.md) |
| Use the CLI | [CLI](spec-fonc/cli.md) |
| Change endpoint or flow extraction | [Extraction rules](spec-fonc/extraction-rules.md) |
| Change HTML export behavior | [HTML export](spec-fonc/html-export.md), [port rendering](spec-fonc/port-rendering.md), and [placement](spec-fonc/placement.md) |
| Change graph views | [Layered view](spec-fonc/layered-view.md) and [module rendering](spec-fonc/modules.md) |
| Change incremental indexing or MCP | [Incrementality](spec-fonc/incrementality.md) and [MCP](spec-fonc/mcp.md) |
| Check a limitation | [Boundaries](spec-fonc/boundaries.md) |

The keywords **MUST** and **MUST NOT** identify compatibility requirements.
The section files are normative for the behavior they own.

## Contract ownership

The functional specification owns CLI, MCP, export, interaction, and
compatibility behavior. The technical specification owns implementation
constraints, persistence, algorithms, and internal data shape.

When a change crosses both contracts, update both entry points and their
owning section files. Keep product intent in [PRD](PRD.md) and durable design
choices in [ADRs](ADR.md).

## Detailed sections

- [Configuration](spec-fonc/configuration.md)
- [CLI](spec-fonc/cli.md)
- [Extraction rules](spec-fonc/extraction-rules.md)
- [HTML export](spec-fonc/html-export.md)
- [Port rendering](spec-fonc/port-rendering.md)
- [Placement and interaction](spec-fonc/placement.md)
- [Layered view](spec-fonc/layered-view.md)
- [Module rendering](spec-fonc/modules.md)
- [Incrementality and freshness](spec-fonc/incrementality.md)
- [MCP](spec-fonc/mcp.md)
- [Boundaries](spec-fonc/boundaries.md)
