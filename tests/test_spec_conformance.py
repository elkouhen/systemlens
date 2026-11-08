"""Specification-to-implementation conformance checks.

The functional specification and the UX contract are sources of truth that
rank above the implementation. These tests make a drift between a
specification and the code visible in the default suite instead of waiting
for a review to notice it. A gap that the backlog already owns is listed
explicitly with its task so the suite stays green until that task lands,
and fails as soon as the task lands without removing the entry.
"""

from __future__ import annotations

import re
from pathlib import Path

import click
import typer

from systemlens.delivery.cli import app

ROOT = Path(__file__).parents[1]
SPEC_FONC = ROOT / "docs" / "spec-fonc"
UX_CONTRACT = ROOT / "docs" / "UX-UI.md"
GRAPH_ASSETS = ROOT / "src" / "systemlens" / "render" / "assets"

FENCED_BLOCK = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`([^`]*)`")
FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")

# Flags added by Typer itself, never part of the documented contract.
TYPER_FLAGS = {"--install-completion", "--show-completion", "--help"}


def _documented_flags() -> set[str]:
    """Flags named in inline code of the functional specification pages.

    Fenced blocks are skipped: they quote other tools (for example the CodeQL
    CLI) whose flags are not SystemLens options.
    """
    flags: set[str] = set()
    for page in sorted(SPEC_FONC.glob("*.md")):
        prose = FENCED_BLOCK.sub("", page.read_text(encoding="utf-8"))
        for span in INLINE_CODE.findall(prose):
            flags.update(FLAG.findall(span))
    return flags


def _implemented_flags() -> tuple[set[str], set[str]]:
    """Visible and hidden long options of every command of the Typer app."""
    visible: set[str] = set()
    hidden: set[str] = set()
    root = typer.main.get_command(app)

    def walk(command: click.Command) -> None:
        for parameter in command.params:
            target = hidden if getattr(parameter, "hidden", False) else visible
            target.update(opt for opt in parameter.opts if opt.startswith("--"))
        for sub in getattr(command, "commands", {}).values():
            walk(sub)

    walk(root)
    return visible - TYPER_FLAGS, hidden


def test_every_documented_cli_flag_exists() -> None:
    visible, hidden = _implemented_flags()
    missing = sorted(_documented_flags() - visible - hidden)
    assert not missing, (
        "docs/spec-fonc documents flags the CLI does not expose: "
        f"{missing}. Update the specification or restore the option."
    )


def test_every_visible_cli_flag_is_documented() -> None:
    visible, _hidden = _implemented_flags()
    undocumented = sorted(visible - _documented_flags())
    assert not undocumented, (
        "the CLI exposes flags that no docs/spec-fonc page documents: "
        f"{undocumented}. Document them in cli.md or the owning page."
    )


# Labels the UX contract names as visible controls or statuses of the export.
# Each must appear verbatim in the graph assets. A dynamic label lists the
# stable fragment that the script always renders.
UX_LABELS = {
    "Architecture",
    "Ressources",
    "Graphe",
    "Flux de code",
    "Centrer",
    "Centrer dans le graphe",
    "Prouvées",
    "Inférées",
    "Sans niveau de preuve",
    "Cartes",
    "Symboles",
    "Index : complet",
    "à vérifier",
    "Visibilité",
    "Ressources masquées",
    "Plus d’options",
    "Légende",
    "Inspecter",
    "Aperçu seul",
    "Tout voir",
    "Sélection",
    "Développer",
    "Réduire",
    "Déplier tout l’arbre",
    "Déplier jusqu’au niveau",
    "appels",
    "replier",
    "Suite inconnue",
    "Routes appelées",
    "Racine",
    "Incohérence possible",
    "Points chauds",
    "Aucun arbre d’appel sélectionné",
    "indexé le",
    "Filtres et affichage",
    "Tout le graphe",
    "Vue lisible",
    "Flux potentiels, pas une trace d’exécution",
    "Types de relations",
    "Appels d’API",
    "Accès aux données",
}

# Contract labels the implementation does not render yet, each owned by a
# backlog task. Remove the entry in the commit that delivers the task; the
# test below fails if a listed label appears while still listed here.
KNOWN_UX_GAPS: dict[str, str] = {}


def _graph_asset_text() -> str:
    parts = [(GRAPH_ASSETS / "graph.html").read_text(encoding="utf-8")]
    parts.extend(
        path.read_text(encoding="utf-8")
        for path in sorted((GRAPH_ASSETS / "graph").glob("*.js"))
    )
    return "\n".join(parts)


def test_ux_contract_labels_are_rendered_or_owned_by_a_backlog_task() -> None:
    assets = _graph_asset_text()
    contract = UX_CONTRACT.read_text(encoding="utf-8")
    missing = sorted(label for label in UX_LABELS if label not in assets and label not in KNOWN_UX_GAPS)
    assert not missing, (
        f"docs/UX-UI.md names controls the export does not render: {missing}. "
        "Implement them or record the gap in KNOWN_UX_GAPS with its backlog task."
    )
    delivered = sorted(label for label in KNOWN_UX_GAPS if label in assets)
    assert not delivered, f"these labels are rendered now; remove them from KNOWN_UX_GAPS: {delivered}"
    stale = sorted(label for label in KNOWN_UX_GAPS if f"`{label}`" not in contract)
    assert not stale, f"KNOWN_UX_GAPS names labels the contract no longer mentions: {stale}"


GLOSSARY = ROOT / "docs" / "glossary.md"
CORE_ASSET = GRAPH_ASSETS / "graph" / "00-core.js"
# Views that are not a resource keep a plain title without a kind label.
NON_RESOURCE_INSPECTOR_TITLES = {
    "Points chauds observés",
    "Écarts entre l’index et le trafic observé",
}


def _glossary_kind_labels() -> dict[str, str]:
    """Pairs of the `Resource kind labels` table of the glossary."""
    text = GLOSSARY.read_text(encoding="utf-8")
    section = text.split("## Resource kind labels", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| `([a-z_]+)` \| `([^`]+)` \|$", section, re.M)
    fallback = re.search(r"^\| any other kind \| `([^`]+)` \|$", section, re.M)
    assert rows and fallback, "docs/glossary.md must list the resource kind labels"
    return {**dict(rows), "*": fallback.group(1)}


def _asset_kind_labels() -> dict[str, str]:
    core = CORE_ASSET.read_text(encoding="utf-8")
    block = re.search(r"const resourceKindLabels = new Map\(\[(.*?)\]\);", core, re.S)
    assert block, "00-core.js must define resourceKindLabels"
    pairs = dict(re.findall(r'\["([a-z_]+)", "([^"]+)"\]', block.group(1)))
    fallback = re.search(r'resourceKindLabels\.get\(kind\) \|\| "([^"]+)"', core)
    assert fallback, "resourceKindLabel must name its fallback label"
    return {**pairs, "*": fallback.group(1)}


def test_resource_kind_labels_match_the_glossary() -> None:
    """PDR-10: the catalogue tags and inspector titles use glossary labels."""
    assert _asset_kind_labels() == _glossary_kind_labels()


def test_every_inspector_title_starts_with_a_glossary_kind_label() -> None:
    """PDR-10 (D4): each resource view is titled `<kind label> · <name>`.

    A title built by `resourceInspectorTitle(kind, name)` reads its prefix from the
    shared map, which the previous test aligns with the glossary; a literal
    kind must be a key of that map. `openFieldResourceInspector` forwards
    its `title` option, so each of its callers is checked instead.
    """
    labels = _glossary_kind_labels()
    calls = []
    for path in sorted((GRAPH_ASSETS / "graph").glob("*.js")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"(?<!function )\bopenInspector\((.*)", text):
            if match.group(1).startswith("title, { docked: true });"):
                assert "function openFieldResourceInspector(" in text[:match.start()]
                continue
            calls.append((path.name, match.group(1)))
        for match in re.finditer(r"(?<!function )\bopenFieldResourceInspector\(\{\s*title: (.*)", text):
            calls.append((path.name, match.group(1)))
    assert calls
    offenders = []
    for name, arguments in calls:
        literal = re.match(r'"([^"]+)"\)', arguments)
        if literal and literal.group(1) in NON_RESOURCE_INSPECTOR_TITLES:
            continue
        built = re.match(r'resourceInspectorTitle\((?:"([a-z_]+)"|nodeResourceKind\(node\)), ', arguments)
        if not built or (built.group(1) and built.group(1) not in labels):
            offenders.append(f"{name}: openInspector({arguments.strip()}")
    assert not offenders, (
        "inspector titles must be built with resourceInspectorTitle() and a glossary kind: "
        f"{offenders}"
    )


SITE_PAGES = [
    ROOT / "docs" / "index.html",
    ROOT.parent / "systemlens-skill" / "docs" / "index.html",
]
CODE_BLOCK = re.compile(r"<code>(.*?)</code>", re.S)
SPEC_LINK = re.compile(r"docs/spec-fonc/([\w-]+\.md)#([\w-]+)")


def _github_anchor(heading: str) -> str:
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def _site_commands(page: Path) -> list[str]:
    """The ``systemlens`` command lines quoted in the page's code blocks."""
    commands: list[str] = []
    for block in CODE_BLOCK.findall(page.read_text(encoding="utf-8")):
        for command in block.replace("\\\n", " ").splitlines():
            if command.strip().startswith("systemlens "):
                commands.append(command.strip())
    return commands


def test_documentation_site_commands_use_existing_flags() -> None:
    """Review C1: the site and the skill page showed the removed ``--codeql-database``."""
    visible, hidden = _implemented_flags()
    pages = [page for page in SITE_PAGES if page.is_file()]
    assert pages and pages[0] == SITE_PAGES[0]
    unknown = sorted({
        (page.relative_to(ROOT.parent).as_posix(), flag)
        for page in pages
        for command in _site_commands(page)
        for flag in FLAG.findall(command)
        if flag not in visible | hidden
    })
    assert not unknown, f"documentation pages name flags the CLI does not expose: {unknown}"


def test_documentation_site_links_name_existing_specification_headings() -> None:
    """Review C1: a link pointed at a heading ADR-85 renamed."""
    broken = []
    for page in (page for page in SITE_PAGES if page.is_file()):
        for target, anchor in SPEC_LINK.findall(page.read_text(encoding="utf-8")):
            spec = SPEC_FONC / target
            headings = {
                _github_anchor(line.lstrip("#"))
                for line in spec.read_text(encoding="utf-8").splitlines()
                if line.startswith("#")
            } if spec.is_file() else set()
            if anchor not in headings:
                broken.append((page.name, target, anchor))
    assert not broken, f"links to missing specification headings: {broken}"


SCHEDULED_LABEL = re.compile(r"^@Scheduled(?:\((\w+)=[^)]*\))?$")


def test_documented_scheduled_triggers_start_flows_with_their_label(tmp_path: Path) -> None:
    """extraction-rules.md names the ``@Scheduled`` arguments that label a
    trigger; each one, and a schedule without any, must start a flow."""
    from systemlens.domain.module_inventory import DiscoveredModule
    from systemlens.indexing.integration_methods import transient_trigger_endpoints

    prose = FENCED_BLOCK.sub("", (SPEC_FONC / "extraction-rules.md").read_text(encoding="utf-8"))
    keys = {
        span: match.group(1) for span in INLINE_CODE.findall(prose)
        if (match := SCHEDULED_LABEL.match(span)) and match.group(1)
    }
    labels = set(keys)
    assert set(keys.values()) == {"cron", "fixedRate", "fixedDelay"}, f"documented labels: {sorted(labels)}"

    path = "app/src/main/java/example/Jobs.java"
    declarations = []
    for index, label in enumerate(sorted(labels)):
        key, value = label[len("@Scheduled("):-1].split("=", 1)
        written = f'"{value}"' if key == "cron" else value
        declarations.append(f"    @Scheduled({key} = {written})\n    void run{index}() {{}}")
    methods = "\n".join(declarations)
    source = tmp_path / path
    source.parent.mkdir(parents=True)
    source.write_text(
        "package example;\nclass Jobs {\n" + methods
        + "\n    @Scheduled(initialDelay = 10)\n    void bare() {}\n}\n",
        encoding="utf-8",
    )
    module = DiscoveredModule(
        name="app", path=tmp_path / "app", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    triggers = transient_trigger_endpoints(tmp_path, [path], [], [module])
    assert sorted(trigger.topic for trigger in triggers) == sorted({*labels, "@Scheduled"})
