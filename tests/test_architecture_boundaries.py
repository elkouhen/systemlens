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


def _module_name(path: Path) -> str:
    relative = path.relative_to(SOURCE_ROOT)
    parts = relative.parent.parts if relative.name == "__init__.py" else relative.with_suffix("").parts
    return "systemlens" + (f".{'.'.join(parts)}" if parts else "")


def test_package_root_contains_no_flat_implementation_modules() -> None:
    assert sorted(path.name for path in SOURCE_ROOT.glob("*.py")) == ["__init__.py"]


def test_domain_does_not_depend_on_outer_layers() -> None:
    forbidden_prefixes = (
        "systemlens.application",
        "systemlens.delivery",
        "systemlens.discovery",
        "systemlens.indexing",
        "systemlens.render",
        "systemlens.scanner",
        "systemlens.storage",
    )
    for path in (SOURCE_ROOT / "domain").glob("*.py"):
        imported = _imported_modules(path)
        assert not any(
            dependency.startswith(forbidden_prefixes)
            for dependency in imported
        ), f"{path.name} imports an outer layer: {sorted(imported)}"


def test_internal_import_graph_is_acyclic() -> None:
    modules = {_module_name(path): path for path in SOURCE_ROOT.rglob("*.py")}
    graph = {
        name: {
            dependency
            for dependency in _imported_modules(path)
            if dependency in modules and dependency != name
        }
        for name, path in modules.items()
    }
    visited: set[str] = set()
    active: list[str] = []

    def visit(name: str) -> None:
        if name in active:
            cycle = " -> ".join([*active[active.index(name):], name])
            raise AssertionError(f"internal import cycle: {cycle}")
        if name in visited:
            return
        active.append(name)
        for dependency in graph[name]:
            visit(dependency)
        active.pop()
        visited.add(name)

    for name in graph:
        visit(name)


def test_module_types_do_not_depend_on_discovery_or_parsing() -> None:
    imported = _imported_modules(SOURCE_ROOT / "domain" / "module_inventory.py")

    assert not imported & {
        "systemlens.discovery.build.gradle",
        "systemlens.discovery.java.parser",
        "systemlens.discovery.build.maven",
        "systemlens.modules",
        "systemlens.scanner",
    }


def test_store_depends_on_module_facts_not_module_discovery() -> None:
    imported = _imported_modules(SOURCE_ROOT / "storage" / "sqlite.py")

    assert "systemlens.domain.module_inventory" in imported
    assert "systemlens.modules" not in imported
