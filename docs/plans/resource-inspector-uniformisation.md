# Plan: one `Inspecter` behaviour for every catalogue resource

Status: validated on 2026-11-01 by the product owner, who accepted PDR-10
as written in section 4, settled its open point as the plan recommends (a
JPA entity row opens the node inspector), and kept the lot order A, B, C.
Section 7 records each task's state.

Owner: SystemLens implementer. Reviewers: product owner and UX maintainer.
Baseline: commit `e61e5aa`, lab index of `apps/supermarket-demo` exported
with the current assets, viewport 1400 by 900, headless Chrome.

Parent rules: [UX-UI](../UX-UI.md) (resource catalogue, node inspector),
[ADR-68](../ADR.md#adr-68-dock-the-node-inspector-beside-the-graph),
[PDR-3](../PDR.md#pdr-3-the-export-speaks-french),
[PDR-7](../PDR.md#pdr-7-one-french-word-per-concept-in-the-export-and-the-cli),
[PDR-9](../PDR.md#pdr-9-one-inspector-skeleton-for-every-node-kind).

## 1. Problem

The product owner reports that the `Inspecter` action of the resource
catalogue does not behave the same way from one resource kind to the next.
The button itself is uniform: every row shows it on hover, in the same
place, with the same label. What differs is what happens after the click:
the surface that opens, its header, its vocabulary, and what remains of the
catalogue behind it.

## 2. Baseline

One row of each kind was inspected from the catalogue. The screenshots are
in the delivery machine's job directory
(`~/.claude/jobs/d449c383/tmp/shots/`, files `NN-<kind>-row.png` and
`NN-<kind>-inspect.png`); they are not checked in.

| Row kind (tag) | Inspector title | Surface | Header | Groups | Catalogue after the click |
|---|---|---|---|---|---|
| Microservice | `Microservice · inventory-service` | docked | tiles, badges, actions | Relations, Trafic observé, Architecture, Ports, Flux, Sources, Diagnostics | visible, preview docked |
| Route exposée | `Route HTTP · GET /api/health` | docked | method, path, `0 client` | none; loose sections `Microservice exposant`, `Preuves d’appel`, `Code source` | **replaced** by the owner's inline details widget |
| Route appelée | `Route HTTP · POST /api/reservations` | docked | method, path, `0 client` | same loose sections; `Microservice exposant` names the **caller** | **replaced** by the caller's inline details widget |
| Contrat OpenAPI | `OpenAPI · openapi.yaml` | centered modal | Swagger UI banner | Swagger UI | hidden under the modal |
| Contrat AsyncAPI | `AsyncAPI · asyncapi.yaml` | centered modal | hero banner, VS Code action | AsyncAPI viewer | hidden under the modal |
| Topic | `Topic · Kafka · supermarket.order.placed` | docked | tiles, badges, `Centrer` | Relations, Architecture, Sources | visible |
| Collection | `Donnée · MongoDB · order_fulfillments` | docked | tiles, badges, `Centrer` | Relations, Architecture, Sources | visible |
| Entité JPA | `Entité JPA · JPA · ProductEntity` | docked | tiles, `Centrer` | Relations, Architecture, Sources | visible |
| DTO | `DTO de message · OrderPlaced` | centered modal | one notice line | loose sections `Champs déclarés`, `Messages`, `Producteurs`, `Consommateurs` | hidden under the modal |
| Classe de persistance MongoDB | `Données persistées · OrderFulfillmentDocument` | centered modal | qualified name, path, VS Code action | loose sections `Data`, `Microservice`, `Projet de persistance`, `Champs déclarés` | hidden under the modal |

Defects read from the baseline, with their cause in the assets:

| ID | Defect | Where | Consequence |
|---|---|---|---|
| D1 | `Inspecter` on a route selects the owner node without hiding the inline details, so the catalogue disappears behind the owner's details widget while the route inspector docks. | `openHttpRouteInspector` ends with `selectNode`; the node branch of the catalogue calls `hideInlineDetailsAfterModal`, the route branch does not (`42-resource-catalogue.js`). | The user loses the list they were browsing; closing the inspector leaves the details widget, not the catalogue. |
| D2 | A `Route appelée` row opens the route inspector with the caller as provider: `Microservice exposant : order-service`, `0 client`, `Aucun microservice appelant identifié`, while the catalogue row of the served route says `1 client`. | The catalogue passes `entry.ownerNode` (the caller) to `openHttpRouteInspector`; `openHttpCallInspector` exists for this case and is unused here. | The inspector states a false fact about the architecture. |
| D3 | `Entité JPA` opens the docked node inspector without its fields, while `Classe de persistance MongoDB` opens the centered field modal. The entity's fields are reachable only through the `Classe de persistance` link inside the inspector. | Catalogue branch order: `mongoClassId` wins over `node`; a JPA entity row carries `node` only. | Two persistence classes, two different first screens. |
| D4 | Inspector titles do not reuse the row's kind label: `Route HTTP` versus `Route exposée` / `Route appelée`, `Donnée · MongoDB` versus `Collection`, `Données persistées` versus `Classe de persistance MongoDB`, `DTO de message` versus `DTO`; `Entité JPA · JPA` repeats the technology. | `nodeKindLabel`, `openInspector` call sites, `resourceCatalogueSingularLabels`. | PDR-7 is broken: one concept, two or three words. |
| D5 | The persistence-class modal has an English section `Data`. | `40-details.js`, Mongo persistence inspector. | PDR-3 defect. |
| D6 | The route inspector has no header strip, no groups and no `Centrer dans le graphe`; its sections are loose, unlike every node inspector since PDR-9. | `openHttpRouteInspector`, `openHttpCallInspector` build sections directly in `inspectorBody`. | The same panel position shows two visual grammars. |
| D7 | The three modal views have three headers: a notice line (DTO), a qualified name plus a VS Code button (Mongo class), a hero banner plus a VS Code button (AsyncAPI), and no VS Code action at all (OpenAPI). | Each opener builds its own chrome. | The user cannot predict where the source action is. |

Not a defect: the `Inspecter` button itself. Every row shows it on hover,
focus and selection, at the same position, with the same label and title,
and the rows without a dedicated inspector read `Aperçu seul` as the UX
contract requires. No JavaScript error was raised on any of the ten paths.

## 3. Invariants

1. A visual rule changes in `docs/UX-UI.md` and, when it is a product
   choice, in `docs/PDR.md` first, then in the assets, then in
   `tests/test_browser_export.py`, in the same commit.
2. No task changes persisted facts, identifiers, or the export JSON.
3. Every fixed defect leaves a regression test in the same commit.
4. The deferred concerns of `AGENTS.md` still apply: no CDN, ARIA, or
   keyboard-navigation work. Swagger UI and the AsyncAPI viewer are not
   restyled; only the chrome SystemLens draws around them changes.
5. The field views of DTOs, persistence classes and contracts keep the
   centered modal (ADR-68); the docked panel stays for nodes and routes.

## 4. Decision to validate first (PDR-10, proposed)

`Inspecter` opens one of three surfaces, chosen by the kind of fact and
not by the code path that produced the row:

| Fact | Surface | Skeleton |
|---|---|---|
| A node of the architecture graph (microservice, topic, collection, JPA entity, enriched resource) | docked node inspector | PDR-9 skeleton |
| A relation end (served route, HTTP call) | docked relation inspector | header strip (method, path, client count), one row of actions including `Centrer dans le graphe` on the provider, groups `Relations` (provider, clients, DTO REST) and `Sources` (evidence, code source) |
| A document (OpenAPI or AsyncAPI contract, DTO fields, persistence-class fields) | centered modal | title `<row kind label> · <name>`, one row with the VS Code action when a source exists, then the document body |

Three rules follow. The catalogue stays visible under a docked inspector
and is restored unchanged when a modal closes. The inspector title reuses
the catalogue's kind label (`Route exposée`, `Route appelée`, `Collection`,
`Entité JPA`, `DTO`, `Classe de persistance MongoDB`). A `Route appelée`
opens the call inspector of the caller, never the route inspector with the
caller as provider.

Open point for the product owner: should a JPA entity row open the field
modal (like a Mongo class) or the node inspector (like a collection)? The
plan recommends the node inspector for both the entity and the collection,
because both are graph nodes, and keeps the field modal one click away via
`Classe de persistance`. The Mongo persistence class row, which is not a
node, keeps the field modal.

## 5. Tasks

### R1. Keep the catalogue under a docked route inspector (D1)

Open a route from the catalogue without rendering the owner's inline
details. Closing the inspector returns to the catalogue with the row still
selected.

Acceptance: browser test that inspects `GET /api/health` from the
catalogue and asserts the catalogue list is still visible, the row is
selected, and after `#inspector-close` the list is unchanged.

### R2. Open the call inspector for a called route (D2)

A `Route appelée` row calls `openHttpCallInspector(callerNode, route, call)`
with the served route's provider resolved from the graph. The provider's
route inspector is reachable from it, as today from the node inspector.

Acceptance: browser test on `POST /api/reservations` from the
`order-service` row: title `Route appelée · POST /api/reservations`, caller
`order-service`, provider `inventory-service`, no `0 client` claim.

### R3. One title vocabulary (D4, D5)

Inspector titles reuse `resourceCatalogueSingularLabels` or
`nodeKindLabel`, whichever the glossary owns; `nodeKindLabel` stops
repeating the technology (`Entité JPA · ProductEntity`); the `Data` section
becomes `Donnée`. `docs/glossary.md` lists the kind labels.

Acceptance: `tests/test_spec_conformance.py` asserts that every
`openInspector` title prefix in the assets is a glossary label; browser
test compares each row's tag with its inspector title prefix.

### R4. Relation inspector skeleton (D6)

`openHttpRouteInspector` and `openHttpCallInspector` build a header strip,
an action row with `Centrer dans le graphe`, and the groups `Relations` and
`Sources` through `createDetailsGroup`, so `structureNodeInspector` applies
the same counts and disclosure behaviour as for nodes.

Acceptance: browser test asserts the route inspector shows groups
`Relations` and `Sources` with counts and the `Centrer` action; no loose
section remains in `#inspector-body`.

### R5. Modal chrome (D7)

The three modal views share one header: the title bar, then one row with
the VS Code action when the fact has a source, then the body. The OpenAPI
modal gains the VS Code action it lacks; the DTO notice moves under the
action row.

Acceptance: browser test asserts the position and label of the VS Code
action in the OpenAPI, AsyncAPI, DTO (when a source exists) and Mongo class
modals.

### R6. JPA entity first screen (D3)

Apply the choice of section 4. If the node inspector is kept, no asset
change; the UX contract states the rule and the browser test of R3 covers
the entity row. If the field modal is chosen, the catalogue branch treats a
JPA entity like a Mongo class.

Acceptance: stated in the UX contract; browser test on the `ProductEntity`
row.

## 6. Delivery lots and validation

| Lot | Tasks | Gate |
|---|---|---|
| A, defects | R1, R2, R5 (OpenAPI action only) | `uv run ruff check src tests`, `uv run mypy`, `uv run pytest`, browser suite |
| B, vocabulary | R3, PDR-10 and UX-UI text | same, plus `tests/test_spec_conformance.py` |
| C, skeleton | R4, R5, R6 | same, plus screenshots of the ten paths compared with the baseline of section 2 |

Lot A fixes false statements and lost context and can ship alone. Lots B
and C depend on the validation of PDR-10. After lot C, regenerate the lab
export from a copy of the lab application and copy it to
`docs/models/simple-supermarket.html` (AGENTS.md, Validation).

## 7. Delivery record

| Task | Status | Commit |
|---|---|---|
| R1 | `delivered` | `327e607` |
| R2 | `delivered` | `327e607`; title asserted in lot B |
| R3 | `delivered` | `680f02c` |
| R4 | `delivered` | `8c3b754` |
| R5 | `delivered` | `327e607` (OpenAPI action), `8c3b754` |
| R6 | `delivered` | `680f02c` (rule and entity-row test), `8c3b754` |

Each lot passed `uv run ruff check src tests`, `uv run mypy`,
`uv run pytest`, and the browser suite in headless Chrome. Lot C's ten
paths were captured again at 1400 by 900 from the baseline's lab data
with the new assets and compared with section 2.

The regeneration of `docs/models/simple-supermarket.html` is
`blocked: POC pipeline and lab state`. A fresh export of a copy of the lab
does not reproduce the checked-in model: it lacks the `poc-ai-analysis`
facts, the generated DTO sources, and the flow descriptions, and the
browser suite reads the model's data. BACKLOG task V1 also asks the
product owner to settle the lab's uncommitted `architecture-ia.html`
first.

Follow-up (2026-11-01): the product owner amended PDR-10 after this plan
was delivered. DTO, Mongo persistence-class, and JPA entity field views now
dock with the resource skeleton (`Champs`, `Relations`, `Sources`); only
contracts keep the modal. See the commit `Dock DTO and persistence-class
inspectors beside the catalogue (PDR-10)`.
