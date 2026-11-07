"""What the in-house engine publishes to the registry (ADR-85).

This module stays light: declaring the engine never imports its adapter.
"""

from systemlens.callgraph.engine import EngineDeclaration

DECLARATION = EngineDeclaration(
    name="source",
    label="Source (tree-sitter)",
    description="tree-sitter, sans dépendance externe",
    factory="systemlens.engines.custom.engine:create_engine",
    unavailable_reason=lambda: None,
)
