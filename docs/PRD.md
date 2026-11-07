# Product requirements: SystemLens

This document owns the product aspect: users, purpose, scope, outcomes, and
success measures. It does not define command contracts, implementation details,
or HTML interaction rules.

Status: current. Owner: the product owner. Last revised: 2026-11-01.
Related: [product decisions](PDR.md),
[functional specification](SPEC-FONC.md), [glossary](glossary.md).

## 1. Problem

Before changing or reviewing a Java and Spring system, a reader must know
which microservices, routes, topics, and data resources a change touches.
The [lab application][lab] shows three situations where that knowledge is
costly to obtain.

[lab]: https://github.com/elkouhen/systemlens-observability-lab/tree/main/apps/supermarket-demo

| Situation | Today's workaround | Cost |
|---|---|---|
| `supermarket.order.placed` has two consumers: `inventory-service` reserves stock, and `restock-service` observes demand with an independent Kafka group. | Follow the lab README's five-step reading order: POM, Dockerfile, Kubernetes manifests, two `application.yml` files, then the code of three modules. | Manual reading across modules; a topic name resolved from configuration or a convention is easy to miss. |
| `order-service` calls `POST /api/reservations` on `inventory-service` over REST. | Search the code for the path text. | A URL built at run time escapes a text search, and nothing marks the search as incomplete. |
| An agent reads the source and reports inter-module dependencies that indexing did not resolve. | Re-read every file the agent cites. | Without relative evidence and a confidence level, a reviewer cannot tell a proven fact from a guess. |

Static evidence alone cannot prove that a relation runs. The lab README
states that an indexing or enrichment result is not, by itself, proof of
execution; runtime metrics are a separate, opt-in source.

## 2. Vision and product principles

SystemLens gives a bounded, evidenced, and locally built architecture
inventory of a Java and Spring codebase before a change or a review, and it
keeps every uncertainty visible instead of guessing a dependency.

### Product principles

1. Source facts must be derived from local AST parsing and deterministic local
   configuration only. Optional Kubernetes enrichment is opt-in, retains its
   workload kind, namespace, and name, and never replaces source evidence.
2. Each source fact must carry enough evidence to navigate to its file and line
   range. Every non-source enrichment must identify its acquisition origin.
3. A changed or deleted source file must update or remove its facts on the next
   index run.
4. The local inventory and its CLI queries must operate without network
   access once indexing is complete. Kubernetes discovery and runtime
   collection (Elastic APM, Prometheus) are opt-in network exceptions.
5. Graph, catalog and audit output must make uncertainty visible rather than
   inventing a dependency.
6. A person analysing a codebase must be able to obtain a bounded answer,
   evidence and unresolved facts before changing or reviewing a supported
   Java/Spring integration. The optional companion skill may enrich the graph
   through reviewable agent analysis.

## 3. Personas and scenarios

| Persona | Context | Goal | Frustration |
|---|---|---|---|
| Codebase analyst (primary) | Prepares a change or a review on a Java and Spring codebase. | Proven dependencies, impact, and unresolved facts for one question. | An answer that mixes evidence and guesses. |
| Developer | Changes one microservice and its integrations. | The evidence behind a microservice, route, topic, or project. | Jumping between modules and configuration files to find a source line. |
| Architect | Reviews the topology across microservices or a workspace. | A shareable topology whose uncertainty is visible. | A diagram that looks complete but hides dynamic or unresolved relations. |

The codebase analyst is a role, not only a person. A person may fill it
directly through the CLI and the HTML export. A person may also direct an
agent through the [companion skill][skill]; the agent then hands its
findings over as a reviewable JSON fact manifest.

[skill]: https://github.com/elkouhen/systemlens-skill

Codebase analyst:

- Before changing the payload of `supermarket.order.placed`, list every
  producer and consumer, with the source line of each.
- When a flow the analyst expects is missing, list the unresolved facts,
  then let an agent complete the gap in a separate manifest namespace.

Developer:

- Before changing `POST /api/reservations`, see every indexed caller and
  the potential code flows that reach it, with their evidence.
- After an edit, reindex and see only the changed facts refreshed.

Architect:

- Review the topology of the three lab microservices and see which
  relations remain dynamic or unresolved.
- Compare the static topology with an Elastic APM collection: runtime
  integrations missing from the index, and indexed ones without traffic.

## 4. Requirements

Each requirement names its persona, an observable acceptance, and a status
from the [delivery practices](process/delivery-practices.md). The
[functional specification](SPEC-FONC.md) owns the supported technologies,
commands, and options.

### Must

| ID | Persona | Requirement | Acceptance | Status |
|---|---|---|---|---|
| M1 | All | Index a Java and Spring codebase locally. | `systemlens init`, `doctor`, and `index` complete with the Python package alone (PDR-5); no source code leaves the machine. | delivered |
| M2 | Analyst, developer | See API, topic, and data relations with their evidence. | Every emitted integration navigates to a file and line range or to a named manifest entry. | delivered |
| M3 | Developer | Keep the index fresh after an edit. | A reindex updates or removes the facts of changed or deleted files and touches only those files, unless an extractor signature, a selected convention, or an analysis dependency changes. | delivered |
| M4 | Analyst, developer | See the potential code flows from an API or topic entry point to its external effects. | `flows list` and `flows show` give ordered steps with evidence, labelled as potential static flows, never as runtime execution. | delivered |
| M5 | Analyst, architect | Audit coverage and unresolved facts. | `analyze coverage`, `analyze indexing-issues`, and `analyze audit` separate resolved relations from unresolved or dynamic facts. | delivered |

### Should

| ID | Persona | Requirement | Acceptance | Status |
|---|---|---|---|---|
| S1 | Analyst (agent) | Import an agent fact manifest. | `import-facts --namespace NAME` stores the facts in their own namespace; they never replace source-derived evidence. | delivered |
| S2 | Architect | Federate a workspace of independently indexed microservices. | `analyze audit --workspace DIR` reports risks across the workspace. | delivered |
| S3 | Architect, developer | Compare the static topology with runtime observations. | `observe report` lists runtime integrations missing from the index and indexed ones without traffic; no static fact is described as observed. | delivered for Elastic APM counts; field validation of percentiles, the Prometheus source, and completeness facts is `blocked` (dropped from the backlog on 2026-11-07) |
| S4 | Developer | Rank runtime hotspots for a selected window. | The export ranks latency and error hotspots with the completeness of the collection and links only to explicitly matched static evidence. | `blocked`: field validation and the live-model regeneration wait on the lab (both dropped from the backlog on 2026-11-07) |
| S5 | Architect | See Kubernetes resource dimensions of indexed projects. | `index --kubernetes` reads the active local `kubectl` context and keeps workload kind, namespace, and name on each matched fact. | delivered |
| S6 | Analyst | Review data-consistency candidates. | `export microservices --review-aids` reports read-modify-write and dual-write candidates with the precision measured in section 6. | `blocked: no second Spring application in the workspace` (dropped from the backlog on 2026-11-07) |

### Later

| ID | Persona | Requirement | Acceptance | Status |
|---|---|---|---|---|
| L1 | Architect | See observed API, topic, data, and S3 activity beside the static architecture. | The export shows the observed activity next to the static elements; an activity without a verified source mapping stays unmapped. | dropped from the backlog on 2026-11-07 |
| L2 | Architect, developer | See the Kubernetes capacity behind a runtime hotspot. | A hotspot shows Deployment or StatefulSet resources only for a verified workload-to-microservice match; an ambiguous match stays unresolved. | dropped from the backlog on 2026-11-07 |

## 5. Out of scope

| Excluded | Reason |
|---|---|
| Security or quality scans, severity filtering, automated remediation | Static evidence can locate a candidate but cannot establish a defect or an incident ([feasibility assessment](microservices-bad-practices.md)). |
| Guaranteed resolution of dynamic values | A guessed value would invent a dependency; unresolved values stay marked as dynamic (principle 5). |
| Downloading runtime traces | At 50 microservices and hundreds of topics, raw traces are too large; aggregated metrics answer the comparison (ADR-60). |
| Sending source code to a hosted service | Facts come from local analysis only (principles 1 and 4). |
| CDN dependencies, ARIA semantics, and keyboard navigation of the export | Deferred until the user reprioritizes them ([PDR-4](PDR.md#pdr-4-accessibility-and-cdn-concerns-are-deferred)). |

## 6. Outcome metrics

The outcome metrics below measure what a persona gains. None has been
measured yet, so each target is set after the first measurement.

| Metric | How it is measured | Baseline | Target |
|---|---|---|---|
| Scenarios answered after one index | Section 3 scenarios run on a fresh copy of the lab; a scenario counts when the answer is bounded and every relation has evidence | Not yet measured | Set after first measurement |
| Integrations traceable to evidence | `export microservices --json` on the lab index; share of relations with a source location or a named manifest entry | Not yet measured | Set after first measurement |
| Agent facts kept after review | Facts of one skill-produced manifest that the analyst keeps unchanged, over facts proposed | Not yet measured | Set after first measurement |

### Measured proxies

The measures above are outcomes. Three proxies make them measurable on
the laboratory application and the live model; each row names how the
value is obtained so a regression is a number, not an impression. A
proxy without a baseline is measured before its first target is set.

| Proxy | How it is measured | Baseline | Target |
|---|---|---|---|
| Precision and abstention of the review aids | `export microservices --review-aids` on a fresh copy of the lab, candidates checked by hand (plan section 6) | 1/1 precise, 1/1 abstention on one application (2026-10-31) | Same on a second Spring application before promotion |
| Share of persisted flow steps with a `proven` evidence level | `flows list --json` on the lab index, steps grouped by evidence level | Not yet measured | Set after the first measurement |
| Time to the first inspector opening for a new user of the live model | Observed on `docs/models/simple-supermarket.html` without documentation, one tester, one session | Not yet measured | Set after backlog task U2 |

Backlog task U2 belongs to the delivered Explorer UX stream; its record is
in Git history at commit `556c0f7`.

## 7. Assumptions, risks, and open questions

- **Assumption: the agent is a first-class user.** The analyst role
  includes an agent driven by the companion skill. Open question: should
  analyst requirements also name a machine-readable acceptance, such as
  `--json` output, for each scenario?
- **Risk: CodeQL licensing.** Interprocedural flows are richer with CodeQL,
  but its licence may not cover proprietary code. A first index therefore
  uses the tree-sitter engine and its closed-world dispatch rules
  ([PDR-5](PDR.md#pdr-5-a-first-index-needs-no-external-tool)).
- **Risk: lab representativeness.** Three microservices are the only
  reference application. The review-aid precision was measured on it alone,
  and S6 waits for a second Spring application.
- **Risk: runtime validation.** The lab Elastic cluster and Prometheus
  server are unreachable from the delivery machine, which keeps S3 and S4
  partly `blocked`.
- **Open question: four-mode navigation drift.** PDR-7 notes that the
  export's navigation drifts from the UX contract and calls it a backlog
  concern. No backlog item tracks it yet.

Implementation details live in the [technical specification](SPEC-TECH.md).
Historical decisions, including the retired external-analyzer design, remain
in [ADR.md](ADR.md).
