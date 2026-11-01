# Product decision records

Parent: [Product requirements](PRD.md).

Technical choices are recorded in [ADR.md](ADR.md). This file records
product choices: rules a user sees, vocabulary, defaults that shape the
experience, and concerns deliberately deferred. Each record says whether it
is open to revision and who decides. A record whose rationale was not
written down when the choice was made says so, rather than inventing one.

| ID | Decision | Open to revision |
|---|---|---|
| [PDR-1](#pdr-1-inspection-requires-shift--click) | Inspection requires Shift + click; a plain click selects | Yes, product owner |
| [PDR-2](#pdr-2-the-node-inspector-docks-beside-the-graph) | The node inspector docks beside the graph; wide views stay modal | Yes, product owner; see ADR-68 |
| [PDR-3](#pdr-3-the-export-speaks-french) | The export speaks French | No for the vocabulary; yes for individual labels |
| [PDR-4](#pdr-4-accessibility-and-cdn-concerns-are-deferred) | Accessibility and CDN concerns are deferred | Yes, by the user only |
| [PDR-5](#pdr-5-a-first-index-needs-no-external-tool) | A first index needs no external tool | Yes, product owner; see ADR-66 |
| [PDR-6](#pdr-6-the-minimap-stays-once-the-user-navigates) | The minimap stays once the user navigates | Yes, product owner |
| [PDR-7](#pdr-7-one-french-word-per-concept-in-the-export-and-the-cli) | One French word per concept in the export and the CLI | Yes, product owner; the glossary owns the list |

## PDR-1: Inspection requires Shift + click

**Status:** Accepted before 2026-11-01; amended on 2026-11-01 when the
product owner validated backlog task U2. The original rationale was
reconstructed from the contract, not recorded at decision time.

**Decision:** Opening the introspection window of a node or a supported
relation requires `Shift + click`. Since 2026-11-01 a plain click on an
Architecture node selects it (details widget, emphasized relations, dimmed
unrelated nodes) without opening anything; a plain click on a relation still
does nothing, and a permanent hint at the bottom of the graph names the
three gestures (`docs/UX-UI.md`, "Navigation, selection, and inspection").

**Why, as far as the contract says:** a plain click on a crowded graph
risks opening a window by accident while panning or reading; the modifier
keeps the graph the primary workspace.

**Consequences:** the inspection path is named on screen, and a plain
click gives feedback instead of silence. Selection is a view state: it
changes no persisted fact.

## PDR-2: The node inspector docks beside the graph

**Status:** Accepted before 2026-11-01 as a modal, rationale not recorded;
amended on 2026-11-01 when the product owner validated backlog task U3
(ADR-68).

**Decision:** From 901 pixels of width, the inspector of a node, an HTTP
route or an HTTP call docks to the right edge and leaves the graph visible.
Contract, schema, DTO, entity and runtime views stay a centered modal over a
blurred graph, and every view is modal at 900 pixels or less. The header
owns identity, breadcrumb, back, and close in both layouts.

**Consequences:** the user reads a node and its neighbours side by side,
and the inspected node keeps a halo. Wide documents keep the room they need.

## PDR-3: The export speaks French

**Status:** Accepted before 2026-11-01.

**Decision:** The generated interface, its CLI messages, and the names of
its widgets are in French; the project documentation is in English
(`AGENTS.md`, documentation rule 1).

**Consequences:** an English label is a defect, not a choice; backlog
task U8 renamed the inspector's `Layer` heading to `Couche`. Palette names
such as `Obsidian` are documentation names, not interface labels: the theme
control reads `Sombre` and `Clair`.

## PDR-4: Accessibility and CDN concerns are deferred

**Status:** Accepted before 2026-11-01; owner: the user.

**Decision:** Work on the export's CDN dependencies, ARIA semantics, and
keyboard navigation is deferred until the user reprioritizes it. No task
may propose, implement, or block on these concerns (`AGENTS.md`, "Deferred
HTML accessibility and delivery concerns").

**Consequences:** UX tasks move and restyle controls without widening their
markup role; the review checklist of the delivery practices names keyboard
operation as deferred.

## PDR-5: A first index needs no external tool

**Status:** Accepted on 2026-10-31 with ADR-66.

**Decision:** The tree-sitter source engine is the default call-graph
engine; CodeQL is opt-in. A first `systemlens index` runs with the Python
package alone.

**Why:** CodeQL needs its CLI, a query pack, and a licence that may not
cover proprietary code; a silent fallback at run time hid that dependency.

**Consequences:** flows of a first index use the closed-world dispatch rules
of ADR-61; the companion skill passes the engine explicitly where CodeQL is
installed.

## PDR-6: The minimap stays once the user navigates

**Status:** Accepted on 2026-11-01 by the product owner, who reported that
the navigation minimap disappears.

**Decision:** After a user pan, wheel zoom, zoom control, or minimap move,
the minimap of the Architecture graph and of the call tree stays visible
until the next fit. A fit is `Tout le graphe`, `Vue lisible`, a layout or
filter refit, a tree refit, or opening another flow. The earlier size and
off-screen rules still show it before any navigation.

**Why:** the minimap hid as soon as every node centre was back inside the
graph area. On small graphs it blinked during a slow pan and vanished at
the moment the user wanted it to find their way back.

**Consequences:** a programmatic camera move, such as centring a selected
flow, does not count as navigation. A window resize that refits the graph
hides the minimap again until V6 of the backlog keeps the camera instead.

## PDR-7: One French word per concept in the export and the CLI

**Status:** Accepted on 2026-11-01 by the product owner after a vocabulary
review of the export, the CLI help, and the glossary.

**Decision:** Every concept has one French label in the generated
interface and in the CLI, fixed by the `French label` column of
`docs/glossary.md`. The rules the review settled:

1. Technical loan words stay untranslated: `topic`, `DTO`, `snapshot`,
   `handler`, `namespace`, `checkpoint`, `pipeline`, `backend`, and
   product or protocol names. Everything else is French; `Lost update`
   reads `Perte de mise à jour`.
2. `microservice` names an indexed service everywhere. `service` survives
   only for the APM services of the runtime overlay, which are not
   necessarily indexed microservices.
3. `relation` names any link between two elements in the export. The CLI
   keeps `arête d'appel` for CodeQL call edges only. `arc` and `lien`
   leave the labels, and the English contracts say `edge`, as the
   glossary requires.
4. The node-type chip for topics reads `Topics`, not `Messages`. The
   relation types read `Appels d'API`, `Messages`, `Accès aux données`,
   and `Autres`.
5. `flux de code` names a catalogue entry, `arbre d'appel` its rendered
   view, and `chemin` the path searched between two nodes. `itinéraire`,
   `parcours`, and `graphe d'appel` leave the labels; the catalogue kicker
   reads `Flux potentiels, pas une trace d'exécution`.
6. `donnée` names a persisted data resource (collection, entity); flow
   steps read `Lecture de donnée` and `Écriture de donnée`; the Java
   classes bound to a resource are `classes de persistance`.
7. CLI help describes commands and options in the infinitive, writes
   `topic` in lower case, and the export uses the typographic apostrophe.

**Why:** the review found four words for a relation, two for a path, both
`service` and `microservice`, an English `flows` command group, and a node
chip and a relation type sharing the label `Messages`. A reader cannot
tell whether two labels name one concept or two.

**Consequences:** command, option, and JSON key names are a public
contract and keep their spelling. A renamed contractual label is updated
in `docs/UX-UI.md`, `tests/test_spec_conformance.py`, and the export in
the same change. The four-mode navigation drift from the UX contract is a
separate backlog concern, not a vocabulary one.
