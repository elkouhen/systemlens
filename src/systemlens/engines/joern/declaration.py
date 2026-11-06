"""What the Joern engine publishes to the registry (ADR-85).

This module stays light: declaring Joern never imports its adapter.
"""

import platform
import shutil

from systemlens.callgraph.engine import EngineDeclaration


def unavailable_reason() -> str | None:
    """Joern needs its two launchers; on macOS they call ``greadlink``."""
    missing = [name for name in ("joern", "joern-parse") if shutil.which(name) is None]
    if missing:
        return f"Joern introuvable dans le PATH ({', '.join(missing)})."
    if platform.system() == "Darwin" and shutil.which("greadlink") is None:
        return (
            "greadlink introuvable dans le PATH : les lanceurs Joern l'utilisent sur macOS "
            "(brew install coreutils)."
        )
    return None


DECLARATION = EngineDeclaration(
    name="joern",
    label="Joern",
    description="graphe de propriétés du code, requiert Joern et un JDK",
    factory="systemlens.engines.joern.engine:create_engine",
    unavailable_reason=unavailable_reason,
    resolves_types=True,
    init_profile=True,
)
