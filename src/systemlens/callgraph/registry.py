"""Discover the declared call-graph engines and build one by name.

This module and the engine packages are the only places that name an
engine (ADR-85). Each entry of ``_DECLARATIONS`` is a light module that
publishes ``DECLARATION``; the analyzer itself is imported only when an
engine is built, so the core has no import-time dependency on it.
"""

import importlib
from functools import cache
from pathlib import Path

from systemlens.callgraph.engine import CallGraphEngine, EngineDeclaration, EngineSettings

# The selector value that builds no interprocedural path.
NO_ENGINE = "none"
# The engine used when the selected one is unavailable, and by default.
DEFAULT_ENGINE = "source"

_DECLARATIONS = (
    "systemlens.engines.custom.declaration",
    "systemlens.engines.codeql.declaration",
    "systemlens.engines.joern.declaration",
)


@cache
def declarations() -> tuple[EngineDeclaration, ...]:
    """Every declared engine, in registry order."""
    found: list[EngineDeclaration] = []
    for module_name in _DECLARATIONS:
        declaration = importlib.import_module(module_name).DECLARATION
        if not isinstance(declaration, EngineDeclaration):
            raise TypeError(f"{module_name}.DECLARATION is not an EngineDeclaration")
        found.append(declaration)
    names = [item.name for item in found]
    if len(set(names)) != len(names) or NO_ENGINE in names:
        raise ValueError(f"Duplicate or reserved engine name in {names}.")
    return tuple(found)


def engine_names() -> tuple[str, ...]:
    """Every valid ``analysis.call_graph_engine`` value."""
    return (*(item.name for item in declarations()), NO_ENGINE)


def declaration(name: str) -> EngineDeclaration:
    """The declaration of ``name``; ``ValueError`` for an unknown engine."""
    for item in declarations():
        if item.name == name:
            return item
    raise ValueError(
        f"Moteur de graphe d'appel inconnu : {name!r}. Valeurs : {', '.join(engine_names())}."
    )


def default_excludes() -> tuple[str, ...]:
    """Inventory exclusions that the declared engines contribute."""
    return tuple(pattern for item in declarations() for pattern in item.default_excludes)


def is_engine_artifact(directory: Path) -> bool:
    """Whether a directory is an engine artifact, such as an analyzer database."""
    return any(
        item.artifact_directory is not None and item.artifact_directory(directory)
        for item in declarations()
    )


def load_engine(name: str, settings: EngineSettings | None = None) -> CallGraphEngine | None:
    """Return the engine for ``name``; ``none`` disables interprocedural flows."""
    if name == NO_ENGINE:
        return None
    module_name, _, function = declaration(name).factory.partition(":")
    factory = getattr(importlib.import_module(module_name), function)
    engine: CallGraphEngine = factory(settings or EngineSettings())
    return engine
