"""Regression tests for dependency boundaries between architecture layers."""

import ast
from pathlib import Path


SOURCE_ROOT = Path(__file__).parents[1] / "src" / "systemlens"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return imported


def test_module_types_do_not_depend_on_discovery_or_parsing() -> None:
    imported = _imported_modules(SOURCE_ROOT / "module_types.py")

    assert not imported & {
        "systemlens.gradle",
        "systemlens.java_parser",
        "systemlens.maven",
        "systemlens.modules",
        "systemlens.scanner",
    }


def test_store_depends_on_module_facts_not_module_discovery() -> None:
    imported = _imported_modules(SOURCE_ROOT / "store.py")

    assert "systemlens.module_types" in imported
    assert "systemlens.modules" not in imported
