# SystemLens (`systemlens`)

Local, source-evidenced Java/Spring architecture context for coding agents.

SystemLens indexes REST and Kafka integrations, Maven/Gradle modules, OpenAPI
contracts, MongoDB collections, and derived architecture relations in a local
SQLite database. It gives agents dependencies, impact paths, and unresolved
facts before they change a system. Source code is neither sent to a service nor
analysed by an external engine.

**Start here:** install SystemLens, initialise the repository, run an index,
then inspect the result through MCP, the CLI, or the HTML export.

## Install

```bash
uv tool install systemlens
```

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

To inspect resolved Kubernetes namespaces and attached modules, use the
dedicated namespace view:

```bash
systemlens export namespaces --html namespaces.html
```

For iterative AI enrichment, validate and replace facts in the separate local
enrichment layer:

```bash
systemlens import-facts architecture.ai-graph.pass-001.json \
  --namespace ai-architecture
```

For terminal-oriented exploration, use `systemlens microservices`,
`systemlens topics`, `systemlens apis`, and `systemlens analyze audit`.

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
Use `systemlens index --topic-strategy strategy1` only for repositories that
follow the documented Strategy1 Kafka and REST conventions.

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

## What SystemLens extracts

- Spring MVC/WebFlux routes and Spring Data REST exposure.
- Feign, RestTemplate, WebClient and gateway HTTP calls.
- Spring Kafka and Spring Cloud Stream producers/consumers, including explicit
  payload types when available.
- Maven/Gradle modules and dependencies, OpenAPI contracts, MongoDB usage and
  Spring properties.
- Optional Kafka facts from Markdown and JSON manifests.
- Optional Deployment and StatefulSet CPU/RAM dimensions from the active local
  Kubernetes context with `systemlens index --kubernetes`.

Dynamic paths and topic values are retained as dynamic facts; the tool does not
guess a concrete dependency.

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
