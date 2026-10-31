"""Resolve a configured engine name to an engine instance.

Engines outside the core are imported lazily by module path, so the core
has no import-time dependency on any external analyzer.
"""

import importlib
from pathlib import Path

from systemlens.callgraph.engine import CallGraphEngine
from systemlens.callgraph.source import SourceEngine

ENGINE_NAMES = ("codeql", "source", "none")
_EXTERNAL_ENGINES = {"codeql": "systemlens.engines.codeql"}


def load_engine(
    name: str,
    *,
    database: Path | None = None,
    threads: int = 1,
    ram_mb: int | None = None,
) -> CallGraphEngine | None:
    """Return the engine for ``name``; ``none`` disables interprocedural flows.

    ``database`` reuses an existing analyzer database when the engine
    supports one; ``threads`` and ``ram_mb`` bound external analyzers.
    """
    if name == "none":
        return None
    if name == "source":
        return SourceEngine()
    module_name = _EXTERNAL_ENGINES.get(name)
    if module_name is None:
        raise ValueError(
            f"Moteur de graphe d'appel inconnu : {name!r}. Valeurs : {', '.join(ENGINE_NAMES)}."
        )
    module = importlib.import_module(module_name)
    engine: CallGraphEngine = module.create_engine(
        database=database, threads=threads, ram_mb=ram_mb,
    )
    return engine
