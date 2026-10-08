# SystemLens (`systemlens`)

Local, source-evidenced Java/Spring architecture analysis for people who need
to understand a codebase.

SystemLens indexes REST integrations, Topics, Data resources, Maven/Gradle projects, OpenAPI
contracts, and derived architecture relations in a local
SQLite database. It gives analysts, developers, and architects dependencies,
impact paths, and unresolved facts before they change or review a system.
Source code is neither sent to a service nor analysed by an external engine.

**Start here:** install SystemLens, initialise the repository, run an index,
then inspect the result through the CLI or the HTML export.

## Scope

This repository owns the local SystemLens product: deterministic indexing,
source evidence, the CLI, persisted flows, and HTML exports.
It does not own agent prompts or the observability environment.

The companion `systemlens-skill` is optional: it lets an agent perform a
focused, evidence-based analysis to enrich the separate graph-fact layer. Its
findings remain reviewable and never replace source-derived facts.

## Related projects

- [systemlens-skill](https://github.com/elkouhen/systemlens-skill) owns agent
  guidance and flow descriptions.
- [SystemLens observability lab](https://github.com/elkouhen/systemlens-observability-lab)
  owns the runnable observability environment.

## Three-repository workflow

The three repositories form one architecture-analysis workflow with distinct
responsibilities:

| Repository | Responsibility | Main output |
|---|---|---|
| `systemlens` | Index Java/Spring source evidence and visualize the persisted model. | SQLite index, CLI results, interactive HTML graphs |
| `systemlens-skill` | Index with CodeQL and SystemLens, analyze source directly, and import complementary facts through the CLI. | Versioned AI fact manifests and HTML exports |
| `systemlens-observability-lab` | Run test applications, deploy the complete Kubernetes/Elastic environment, and validate instrumentation and observability. | Deployed workloads, telemetry checks, reproducible integration fixtures |

Use either the direct-analysis skill workflow or the deterministic index-first
workflow. The direct workflow reads source code with the agent, imports a
reviewable JSON manifest, and exports HTML through the SystemLens CLI.
The indexed workflow builds the local source snapshot before enrichment. Use
the observability lab to validate runtime behavior and telemetry separately.

## Install

```bash
uv tool install systemlens
```

### Development dependencies

On macOS and Ubuntu, provision the local development tools (uv, Java 17, and
the optional CodeQL Java method-call analyser) with:

```bash
scripts/install-dependencies.sh
```

Use `scripts/install-dependencies.sh --help` to omit the optional component or
preview the commands. The script runs `uv sync --group dev` to create the
project environment. CodeQL is the default method-call engine. SystemLens
remains usable with AST-only flows when CodeQL is not selected or installed.

For the common development checks, use the repository `Makefile`:

```bash
make setup       # install development dependencies
make lint        # run Ruff
make architecture # enforce package dependency boundaries
make test        # run the default test suite
make typecheck   # run mypy
make security    # run Bandit and Semgrep
make coverage    # run tests and report branch coverage
make check       # run lint, typecheck, security, tests, and coverage
make help        # list all available tasks
```

Use `make test-slow` for tests marked as slow, `make browser-test` for the
browser export test, and `make companion-contracts` to validate the companion
repositories when they are available.

## Quick start

From the root of the Java/Spring repository:

```bash
systemlens init
systemlens doctor
systemlens index
```

`systemlens index` uses CodeQL when an available database can provide richer
call-graph evidence. Use `systemlens index --no-codeql` for a faster AST-only
inventory when you only need the structural baseline; dynamic and
interprocedural calls may be incomplete in this mode. If CodeQL reaches its
timeout, resume the persisted join with `systemlens index --resume-codeql-join`.

For a human-readable topology, generate an interactive export:

```bash
systemlens export microservices --html architecture.html
```

The HTML export is organized into four top-level areas: `Architecture`, `Flux
de code`, `Contrats`, and `Diagnostics`. `Architecture` contains the
microservice graph, topics, collections, and routes. `Contrats` groups the
OpenAPI, AsyncAPI, and indexed DTO views. In `Flux de code`, check several
flows to view their call graphs together in independent side-by-side panels.
The comparison button can also open the current selection explicitly.

For direct source analysis, add the companion skill and follow its manifest
workflow:

```bash
npx skills add elkouhen/systemlens-skill
```

Ask the agent to explain selected persisted flows, audit dependency and call
graphs for complexity, or complete one of the focused topology passes. The
skill keeps its reviewable AI facts separate from the SystemLens index; it
does not replace or silently rewrite source-derived evidence.

For a repeatable targeted analysis, create `.systemlens/analysis-scope.json`
in the analyzed repository. The skill can reuse the same exact service, flow,
protocol, Topic, and Data selectors across its prompts. See the
[analysis scope contract](https://github.com/elkouhen/systemlens-skill/blob/main/references/analysis-scope.md).

To inspect the structural hierarchy of modules, child modules, and indexed
projects, use the dedicated module view. Kubernetes namespaces do not define
this hierarchy:

```bash
systemlens export modules --html modules.html
```

For iterative AI enrichment, validate and replace facts in the separate local
enrichment layer:

```bash
systemlens import-facts architecture.ai-graph.pass-001.json \
  --namespace ai-architecture

systemlens export facts architecture.ai-graph.export.json \
  --namespace ai-architecture
```

The facts export is a complete, re-importable namespace snapshot by default.
Use `--partial` for an incremental pass. Source-derived facts are regenerated
by `systemlens index`; they are not converted into enrichment facts.
After importing facts, use `systemlens flows calculate` to refresh the persisted
source-backed flow snapshot while reusing the indexed endpoints. The optional
command reads the stored AST and CodeQL results; it does not rerun indexation.
Use `--module orders` to reconstruct only the flows owned by one module while
retaining the global persisted call graph for helper methods.

Once the global index is current, inspect one module without indexing again:

```bash
systemlens flows list --module orders --explain
```

The `--module` option is a read-only filter over the persisted snapshot. It
does not run AST extraction or CodeQL. Use `systemlens index --module ...` only
when the source snapshot itself must be refreshed; that command keeps the
indexing and CodeQL analysis global so calls across module boundaries remain
available.

For terminal-oriented exploration, use `systemlens microservices`,
`systemlens topics`, `systemlens apis`, `systemlens projects`, and
`systemlens analyze audit`. Use `systemlens analyze flows-diagnostic` to
compare external integrations with persisted local and cross-service flows.
Use `systemlens flows` to list conservative,
source-evidenced paths from an API or topic entry point to its external effects.

To locate a missing Java call between the CodeQL graph and flow
post-processing, diagnose the expected caller and callee from the persisted
snapshot:

```bash
systemlens analyze call-edge OrderController.create OrderService.reserve
```

Add `--json` to retrieve matched methods, edge evidence, flow IDs, and the
pipeline stage associated with the verdict.

### Choose an interface

| Goal | Use |
|---|---|
| Give a coding agent architecture context | `systemlens export microservices --json` and `systemlens export facts …` |
| Investigate one service or integration in a terminal | Catalog and `analyze` commands |
| Review the whole topology visually | `systemlens export microservices --html …` |
| Browse the persisted graph locally | `systemlens web` |

To use the static architecture export as a local Python web application, start:

```bash
systemlens web
```

Open `http://127.0.0.1:8765/`. The Architecture page renders the current
persisted index snapshot on each request. Use `--host` and `--port` to adjust
the local server. The default loopback address avoids exposing indexed
architecture details on the network.

When an HTML export loads adjacent JSON data, serve its output directory with:

```bash
cd report-directory
simpleweb
```

It serves the current directory at `http://127.0.0.1:8000/`; use `--port` or
`--host` to change the local address.

Indexing is incremental. Use `systemlens index --full` after a broad change.
Use `systemlens index --strategy strategy1` only for repositories that
follow the documented Strategy1 Kafka and REST conventions.

For a long CodeQL run, write a provisional graph after each completed Java
project and refresh it in a browser to follow progress:

```bash
systemlens index --codeql-progress-html codeql-progress.html
```

The progress graph is explicitly incomplete; generate the authoritative HTML
export after indexing finishes.

### Reuse a CodeQL database

You can create the Java CodeQL database first, then give its path to
SystemLens. Create one global database from the repository root so that
cross-module calls remain available:

```bash
codeql database create .codeql/systemlens-java \
  --language=java \
  --source-root=. \
  --build-mode=none

systemlens index --full \
  --call-graph-engine codeql \
  --codeql-database .codeql/systemlens-java
```

The `--codeql-database` option reuses the supplied database for method-call
analysis while SystemLens continues to run its own AST extraction and stores
the combined snapshot in `.systemlens/findings.db`. The database must describe
the same repository revision and source-root layout as the SystemLens index.
Recreate it after source changes that must be reflected in the call graph.

After this one global index, module-level flow queries reuse the persisted
snapshot:

```bash
systemlens flows list --module orders --explain
```

During a focused index, add `--show-call-chains --module orders` to print
caller chains as they are joined, starting at each OUT and walking toward an
IN. The output also keeps terminal paths that do not reach an indexed input,
with their stop reason.

This option is mutually exclusive with `--generate-sources`, because source
generation must happen before the external database is created. The supplied
database is caller-managed; SystemLens reads it and does not create a temporary
replacement for that run.

## Agent Package Manager (APM)

This repository includes an `apm.yml` manifest for reproducing its agent setup
across supported coding-agent harnesses. Install APM, then run:

```bash
apm install
```

The manifest currently contains no external agent or server dependency; the
repository's `AGENTS.md` remains the source of project instructions. The
following project checks are also available through APM:

```bash
apm run lint
apm run test
apm run typecheck
```

Keep `apm.yml` under version control. The generated `apm_modules/` directory is
local installation state and must not be committed.

## What SystemLens indexes

SystemLens builds a local inventory of the following architecture resources:

| Resource | Source evidence indexed |
|---|---|
| Microservices and Java modules | Maven/Gradle projects, Spring application entry points, and build dependencies. |
| REST APIs | Spring MVC/WebFlux and Spring Data REST routes, plus Feign, RestTemplate, WebClient, and gateway calls. |
| Messaging topics | Kafka producers and consumers, including Spring Kafka and Spring Cloud Stream; known Java payload types are retained. |
| MongoDB collections | MongoDB collection access and the Java method that reads or writes it. |
| JPA entities | Source classes annotated with `jakarta.persistence.Entity` or `javax.persistence.Entity`; no physical table or database is inferred. |
| Supporting contracts | OpenAPI contracts, Spring properties, and optional Kafka facts from Markdown or JSON manifests. |
| Kubernetes capacity (optional) | Deployment and StatefulSet CPU/RAM dimensions from the active local context, with `systemlens index --kubernetes`. |

Dynamic paths and topic values are retained as dynamic facts; the tool does not
guess a concrete dependency.

## Relations in the indexed graph

SystemLens stores source-evidenced, typed relations rather than inferring a
generic dependency graph. The main relations are:

- **Build:** a Maven or Gradle project `depends_on` another project.
- **API calls:** a microservice `provides` a REST route or `calls` an API. A
  `calls_service` relation between two microservices is created only when the
  client target is explicitly and uniquely resolved (for example from an HTTP
  host, `lb://` name, or configured client alias).
- **Topic read/write:** a microservice `publishes` (writes) to or `consumes`
  (reads) a Kafka topic. A concrete producer/consumer match also produces a
  `publishes_to` relation between the two microservices. When known, the graph
  links a topic to the Java payload type it publishes or consumes.
- **MongoDB read/write:** a microservice and its owning Java method `reads` or
  `writes` an indexed MongoDB collection.
- **Implementation and configuration:** an indexed Java class `implements` an
  API or topic endpoint and may `uses_configuration` a referenced Spring
  property.

Each relation retains its relative source location, origin and confidence.
Unresolved or dynamic endpoints remain visible as evidence, but never become a
guessed internal link. The selected local method-call engine (CodeQL by default)
additionally extends potential code flows across statically resolved
Java method calls; these flows are kept separate from asserted topology relations.

## Documentation map

| Read this when you need… | Document |
|---|---|
| A map of documentation ownership and reading paths | [Documentation index](docs/README.md) |
| An interactive overview and examples | [Documentation site](docs/index.html) |
| CLI and HTML-export behaviour | [Functional specification](docs/SPEC-FONC.md) |
| Extraction, storage, and layout design | [Technical specification](docs/SPEC-TECH.md) |
| Product scope and success measures | [Product requirements](docs/PRD.md) |
| Package ownership and code navigation | [Code architecture guide](docs/ARCHITECTURE.md) |
| HTML export UX/UI rules | [UX/UI rules](docs/UX-UI.md) |
| Canonical project vocabulary | [Glossary](docs/glossary.md) |
| Test coverage, levels, and validation commands | [Test strategy and validation report](docs/TESTING.md) |
| The rationale for durable design choices | [ADRs](docs/ADR.md) |
| AI graph manifest format | [AI graph manifest](docs/AI-GRAPH.md) |
