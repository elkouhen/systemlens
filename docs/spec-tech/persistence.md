# Persistence and compatibility

Parent: [Technical specification](../SPEC-TECH.md).


SQLite schema migration is additive where possible. `files` stores hash state,
`endpoints` stores source facts, and normalized tables store modules,
dependencies and relations. Each module has a collision-safe identity used by
endpoints and relations; its artifact/project name remains a display alias.
The database filename remains `findings.db` for backward compatibility; new
AST-only behavior must not infer that it contains security findings.

SystemLens stores this database under `.systemlens/`. It intentionally does not
load the former `.cccr/`, `.archlens/`, or `.codeatlas/` state directory: the
product rename requires a fresh `systemlens init` and `systemlens index` so the
configuration and index namespace remain unambiguous.

The endpoint-inventory signature in `meta` is bumped whenever extractor
behaviour changes. This forces a complete refresh before new facts are served.

