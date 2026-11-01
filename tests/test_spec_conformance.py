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
    "Déplier tout le graphe",
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
    "Tout le graphe",
    "Vue lisible",
}

# Contract labels the implementation does not render yet, each owned by a
# backlog task. Remove the entry in the commit that delivers the task; the
# test below fails if a listed label appears while still listed here.
KNOWN_UX_GAPS = {
    "Tout le graphe": "BACKLOG.md U1",
    "Vue lisible": "BACKLOG.md U1",
}


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
