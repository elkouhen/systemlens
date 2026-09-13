"""Incremental-indexing rules specific to Strategy1 declarations."""

from pathlib import Path

from systemlens.domain.module_inventory import DiscoveredModule


def is_openapi_declaration_path(rel_path: str) -> bool:
    """Recognize a Strategy1 ``src/main/resources/openapi/*.rest`` declaration."""
    path = Path(rel_path)
    parts = path.parts
    return path.suffix.casefold() == ".rest" and any(
        parts[index:index + 4] == ("src", "main", "resources", "openapi")
        for index in range(max(0, len(parts) - 3))
    )


def requires_full_reindex(
    changed_or_deleted: set[str], repo_root: Path, modules: list[DiscoveredModule]
) -> bool:
    """Return whether a delta can change a Strategy1 service-contract join."""
    model_roots = {
        module.path.resolve().relative_to(repo_root.resolve()).as_posix()
        for module in modules
        if module.name.casefold().startswith("model-")
        and module.path.resolve() != repo_root.resolve()
    }
    for rel_path in changed_or_deleted:
        if rel_path.endswith("pom.xml") or is_openapi_declaration_path(rel_path):
            return True
        if any(rel_path == root or rel_path.startswith(f"{root}/") for root in model_roots):
            return True
    return False
