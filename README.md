# SystemLens (`systemlens`)

Local, source-evidenced Java/Spring architecture analysis for people who need
to understand a codebase.

SystemLens indexes REST integrations, Topics, Data resources, Maven/Gradle projects, OpenAPI
contracts, and derived architecture relations in a local
SQLite database. It gives analysts, developers, and architects dependencies,
impact paths, and unresolved facts before they change or review a system.
Source code is neither sent to a service nor analysed by an external engine.

**Start here:** install SystemLens, initialise the repository, run an index,
then inspect the result through MCP, the CLI, or the HTML export.

The companion `systemlens-skill` is optional: it lets an agent perform a
focused, evidence-based analysis to enrich the separate graph-fact layer. Its
findings remain reviewable and never replace source-derived facts.

## Install

```bash
uv tool install systemlens
```

### Development dependencies

On macOS and Ubuntu, provision the local development tools (uv, Java 17, and
the optional CodeQL Java call-graph analyser) with:

```bash
scripts/install-dependencies.sh
```

Use `scripts/install-dependencies.sh --help` to omit the optional component or
preview the commands. The script runs `uv sync --group dev` to create the
project environment. CodeQL is the default call-graph engine. SystemLens
remains usable with AST-only flows when CodeQL is not selected or installed.

## Quick start

From the root of the Java/Spring repository:

```bash
systemlens init
systemlens doctor
systemlens index
```

For a human-readable topology, generate an interactive export:

```bash
systemlens export microservices --html architecture.html
```

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
```

For terminal-oriented exploration, use `systemlens microservices`,
`systemlens topics`, `systemlens apis`, `systemlens projects`, and
`systemlens analyze audit`. Use `systemlens analyze flows-diagnostic` to
compare external integrations with persisted local and cross-service flows.
Use `systemlens flows` to list conservative,
source-evidenced paths from an API or topic entry point to its external effects.

### Choose an interface

| Goal | Use |
|---|---|
| Give a coding agent architecture context | `systemlens mcp` |
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

## Agent Package Manager (APM)

This repository includes an `apm.yml` manifest for reproducing its agent setup
across supported coding-agent harnesses. Install APM, then run:

```bash
apm install
```

The manifest currently contains no external agent or MCP dependency; the
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
guessed internal link. The selected local call-graph engine (CodeQL by default)
additionally extends potential code flows across statically resolved
Java method calls; these flows are kept separate from asserted topology relations.

## Connect an MCP client

Start the stdio server from an initialized repository:

```bash
systemlens mcp
```

For example, register it in an MCP client configuration as:

```json
{
  "mcpServers": {
    "systemlens": {
      "command": "systemlens",
      "args": ["mcp"]
    }
  }
}
```

The MCP control surface is deliberately small:

- `index_repository` refreshes source-derived facts.
- `architecture_graph` reads the merged architecture graph.
- `graph_fact_exists`, `add_graph_fact`, `remove_graph_fact`, and
  `list_graph_facts` manage the separate, persistent enrichment layer.

Graph enrichment never modifies or deletes source evidence. Use `data_schema`
and `message_channel` nodes with a `technology` field for SQL, Redis,
RabbitMQ, SQS, or other middleware facts.

## Documentation map

| Read this when you need… | Document |
|---|---|
| An interactive overview and examples | [Documentation site](docs/index.html) |
| CLI, MCP, and HTML-export behaviour | [Functional specification](docs/SPEC-FONC.md) |
| Extraction, storage, and layout design | [Technical specification](docs/SPEC-TECH.md) |
| A maintainer's code-navigation guide | [Architecture map](docs/ARCHITECTURE.md) |
| The rationale for durable design choices | [ADRs](docs/ADR.md) |
| AI graph manifest format | [AI graph manifest](docs/AI-GRAPH.md) |
