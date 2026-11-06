"""What the CodeQL engine publishes to the registry (ADR-85).

This module stays light: declaring CodeQL never imports its adapter.
"""

import shutil
from pathlib import Path

from systemlens.callgraph.engine import EngineDeclaration, EngineOption

# The metadata file every CodeQL database holds at its root.
DATABASE_MARKER = "codeql-database.yml"
VERBOSITIES = ("errors", "progress", "progress+", "progress++", "progress+++")


def _unavailable_reason() -> str | None:
    if shutil.which("codeql") is None:
        return "CLI codeql introuvable dans le PATH."
    return None


def _is_database(directory: Path) -> bool:
    """A CodeQL database holds a copy of the sources: never inventory it."""
    return (directory / DATABASE_MARKER).is_file()


DECLARATION = EngineDeclaration(
    name="codeql",
    label="CodeQL",
    description="types résolus, requiert la CLI codeql",
    factory="systemlens.engines.codeql.engine:create_engine",
    unavailable_reason=_unavailable_reason,
    supports_module_refresh=True,
    resolves_types=True,
    options=(
        EngineOption(
            "database",
            "Base Java CodeQL existante à réutiliser au lieu de la base automatique.",
            kind="path",
        ),
        EngineOption(
            "verbosity",
            "Verbosité transmise à la CLI codeql.",
            choices=VERBOSITIES,
        ),
    ),
    database_option="database",
    init_profile=True,
    default_excludes=(".codeql/**",),
    artifact_directory=_is_database,
)
