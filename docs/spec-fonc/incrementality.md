# Incrementality and freshness

Parent: [Functional specification](../SPEC-FONC.md).


The index stores SHA-256 values for eligible files. A normal run parses added or
changed files and purges facts for deleted files. A change to a Spring
configuration file or Maven/Gradle build descriptor expands the refresh to its
owning module when that boundary is known. Root-level or ambiguous inputs force a
full refresh because they can affect otherwise unchanged Java source files.
The endpoint extractor signature, analysis configuration signature and selected
topic strategy always force a full refresh. Explicit manifests are included even
when otherwise excluded.

Maven `target/` and Gradle `build/` directories are excluded from normal source
input. With `--strategy strategy1`, Java files under
`target/generated-sources/openapi/` are the deliberate exception: they are
eligible as generated OpenAPI client evidence. Other build outputs, copied
contracts, nested descriptors and derived manifests remain excluded.

The index is `.systemlens/findings.db` for compatibility with prior releases. It is a
local implementation detail, not a contract for direct SQL writes.
