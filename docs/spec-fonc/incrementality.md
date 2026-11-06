# Incrementality and freshness

Parent: [Functional specification](../SPEC-FONC.md).


The index stores SHA-256 values for eligible files. A normal run parses added or
changed files and purges facts for deleted files. A change to a Spring
configuration file or Maven/Gradle build descriptor expands the refresh to its
owning module when that boundary is known. Root-level or ambiguous inputs force a
full refresh because they can affect otherwise unchanged Java source files.
This includes `kafka.yml` and `kafka.yaml`, whose declared Strategy1 topic names
can change the endpoint facts of unchanged Java sources, and a Spring Cloud
Config file (`configurations/<application>.yml`), which configures an
application in another module.
When a module identity changes, appears or disappears, every file below that
module is rescanned, so no fact keeps an identity that no longer exists. A
second module with the same name is such a change; removing it is another.
The endpoint extractor signature, analysis configuration signature and selected
topic strategy always force a full refresh. Explicit manifests are included even
when otherwise excluded.

Maven `target/`, Gradle `build/` and IDE `out/` directories, rooted at a
module, are excluded from normal source input; a package of that name below
`src/` stays source. With `--strategy strategy1`, Java files under
`target/generated-sources/openapi/` are the deliberate exception: they are
eligible as generated OpenAPI client evidence. With `--generate-sources`, all
Java files below a Maven `target/` directory are also eligible after the Maven
or Gradle generation phase completes. Other build outputs, copied contracts,
nested descriptors and derived manifests remain excluded.

The index is `.systemlens/findings.db` for compatibility with prior releases. It is a
local implementation detail, not a contract for direct SQL writes.
