# SystemLens (`systemlens`)

SystemLens indexes Java/Spring source code to show microservices, APIs, Kafka
Topics, data resources, build dependencies, and potential call flows. It stores
source evidence in a local SQLite index and exposes the result through CLI
queries and interactive HTML graphs. AST extraction indexes the architecture;
CodeQL is the default engine for interprocedural Java call graphs when
installed. Both analyses run locally, so source code stays on your machine.

## Choose a path

| Goal | Start with | Result |
|---|---|---|
| Map a Java/Spring repository | [Index and explore](#index-and-explore) | Source-backed architecture index and graph |
| Add AI facts to one HTML report | [Enrich a single export](#enrich-the-index) | A report without changing the index |
| Keep AI facts across reports | [Import enrichment facts](#enrich-the-index) | Reviewable facts in a separate enrichment layer |
| Check indexing against a test application | [Test with the lab](#test-with-the-lab) | An index of the lab's demo application |

SystemLens creates the source-backed index. The optional
[systemlens-skill](https://github.com/elkouhen/systemlens-skill) helps an agent
find and import complementary AI facts. The
[SystemLens observability lab](https://github.com/elkouhen/systemlens-observability-lab)
provides a demo application for testing indexing.

## Index and explore

Install SystemLens, then run these commands from the root of a Java/Spring
repository:

```bash
uv tool install systemlens
systemlens init
systemlens doctor
systemlens index
systemlens export microservices --html architecture.html
```

Open `architecture.html` to inspect the architecture and call graphs. The
export also groups indexed contracts and diagnostics. `systemlens index` uses
CodeQL for method-call evidence when it is installed. For a faster structural
inventory, run `systemlens index --no-codeql`; interprocedural calls may be
incomplete.

Choose an interface for the question you need to answer:

| Goal | Command |
|---|---|
| Browse the current index in a local web app | `systemlens web` |
| Inspect a service, API, Topic, or project in the terminal | `systemlens microservices`, `systemlens apis`, `systemlens topics`, `systemlens projects` |
| List potential flows for one module | `systemlens flows list --module orders --explain` |
| Export facts for another tool or agent | `systemlens export microservices --json` |
| Inspect the project and module hierarchy | `systemlens export modules --html modules.html` |

`systemlens web` serves the persisted index at `http://127.0.0.1:8765/`. If an
HTML export loads adjacent JSON files, serve its directory with `simpleweb`
instead of opening that export through `file://`.

Indexing is incremental. Use `systemlens index --full` when you need to rebuild
the source snapshot. For CodeQL database reuse, progress exports, recovery,
and call-edge diagnostics, see the [CLI reference](docs/spec-fonc/cli.md).

## Enrich the index

Install the companion skill when you want an agent to inspect source code and
add facts that static indexing did not establish:

```bash
npx skills add elkouhen/systemlens-skill
```

The skill produces a reviewable JSON manifest. For a single report, pass the
manifest directly to the HTML export. This leaves the index unchanged:

```bash
systemlens export microservices --graph architecture.ai-graph.json --html architecture.html
```

To keep the facts across reports, import the manifest into a named namespace.
Then refresh the persisted flows and export the graph:

```bash
systemlens import-facts architecture.ai-graph.json --namespace ai-architecture
systemlens flows calculate
systemlens export microservices --html architecture.html
```

The [AI graph manifest reference](docs/AI-GRAPH.md) defines the format and
import rules. Source-derived facts remain in the index; AI facts do not replace
them. Use `systemlens export facts` to review or transfer a namespace.

## Test with the lab

The [lab repository](https://github.com/elkouhen/systemlens-observability-lab)
contains a demo application for checking what SystemLens indexes. Run the
[Index and explore](#index-and-explore) commands from that application's root,
then inspect its exported graph.

## Indexed evidence

| Resource | Evidence |
|---|---|
| Microservices and modules | Maven/Gradle projects, Spring entry points, build dependencies |
| REST APIs | Spring routes, clients, and gateway calls |
| Kafka Topics | Producers, consumers, and known Java payload types |
| Data resources | MongoDB collection access and JPA entity classes |
| Contracts and configuration | OpenAPI, AsyncAPI, Spring properties, and optional manifest facts |
| Kubernetes capacity, when requested | Deployment and StatefulSet CPU/RAM dimensions from `systemlens index --kubernetes` |

SystemLens retains relative source locations, origins, and confidence for
indexed facts. It leaves dynamic or ambiguous targets unresolved instead of
guessing a dependency. See the [functional specification](docs/SPEC-FONC.md)
for relation and export behavior.

## Develop SystemLens

Run `scripts/install-dependencies.sh` on macOS or Ubuntu to provision the local
development tools and run `uv sync --group dev`. Use `make help` to list checks;
`make lint`, `make typecheck`, and `make test` run the common validation steps.
The [test guide](docs/TESTING.md) covers slower browser and companion checks.

The repository also includes `apm.yml` for supported coding-agent harnesses.
Run `apm install` to set it up, or `apm run lint`, `apm run test`, and
`apm run typecheck` for the corresponding checks.

For the full documentation map, start with the [documentation index](docs/README.md).
