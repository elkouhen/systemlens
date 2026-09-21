# Incrementality and freshness

Parent: [Functional specification](../SPEC-FONC.md).


The index stores SHA-256 values for eligible files. A normal run parses added or
changed files and purges facts for deleted files. A full refresh is forced when
the endpoint extractor signature, analysis configuration signature, selected
topic strategy, Spring configuration file, or Maven/Gradle build descriptor
changes. Spring properties and build descriptors can affect facts attributed to
otherwise unchanged Java source files. Explicit manifests are included even when
otherwise excluded.

Maven `target/` and Gradle `build/` directories are never eligible source input:
their generated code, copied contracts, nested build descriptors, and derived
manifests cannot create or refresh indexed facts.

The index is `.systemlens/findings.db` for compatibility with prior releases. It is a
local implementation detail, not a contract for direct SQL writes.

