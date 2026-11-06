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
        "systemlens.conventions",
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
        "systemlens.scanner",
    }


def test_store_depends_on_module_facts_not_module_discovery() -> None:
    imported = _imported_modules(SOURCE_ROOT / "storage" / "sqlite.py")

    assert "systemlens.domain.module_inventory" in imported


def test_engine_names_stay_in_engine_packages_and_the_registry() -> None:
    """ADR-85: outside ``engines/`` and the registry, code is engine-neutral.

    The names are the declared external engines plus a fixed floor, so a
    declaration that disappears cannot weaken the rule. Persisted names
    that predate ADR-85 live in ``storage/persisted_names.py``; Python code
    must use those constants, and a non-Python asset may spell only them.
    """
    import re

    from systemlens.callgraph.registry import declarations
    import systemlens.storage.persisted_names as persisted_names

    names = {"codeql", "joern"} | {
        item.name for item in declarations() if item.factory.startswith("systemlens.engines.")
    }
    pattern = re.compile("|".join(sorted(map(re.escape, names))), re.IGNORECASE)
    persisted = sorted(
        (
            value for key, value in vars(persisted_names).items()
            if key.isupper() and isinstance(value, str)
        ),
        key=len,
        reverse=True,
    )
    allowed = {
        SOURCE_ROOT / "callgraph" / "registry.py",
        SOURCE_ROOT / "storage" / "persisted_names.py",
    }
    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*")):
        relative = path.relative_to(SOURCE_ROOT)
        if (
            not path.is_file()
            or relative.parts[0] == "engines"
            or "__pycache__" in relative.parts
            or path in allowed
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if path.suffix != ".py":
            for value in persisted:
                text = text.replace(value, "")
        offenders.extend(
            f"{relative}:{number}: {line.strip()}"
            for number, line in enumerate(text.splitlines(), 1)
            if pattern.search(line)
        )
    assert not offenders, "engine names outside engines/ and the registry:\n" + "\n".join(offenders)
