"""Strategy1 module-layer naming conventions."""

from pathlib import Path

from systemlens.domain.module_inventory import DiscoveredModule


def classify_module(module: DiscoveredModule, root_path: Path | None = None) -> str | None:
    """Return a convention-derived layer, or ``None`` when none applies."""
    name = module.name.casefold()
    parent = module.path.resolve().parent
    namespace = (
        "root" if root_path is not None and parent == root_path.resolve()
        else parent.name or "root"
    ).casefold()
    if namespace == "portail":
        return "api"
    if namespace == "cycle-de-vie":
        return "orchestration"
    if name.startswith(("persistence-", "repository-", "storage-", "data-")) or name.endswith(
        ("-persistence", "-repository", "-storage", "-data")
    ):
        return "persistence"
    if name.startswith("domain-"):
        return "domain"
    if name.startswith(("api-", "contract-", "contracts-")) or name.endswith(
        ("-api", "-contract", "-contracts")
    ):
        return "api"
    if name.startswith(("infra-", "infrastructure-")) or name.endswith(
        ("-infra", "-infrastructure")
    ):
        return "infrastructure"
    if name.startswith(("shared-", "common-", "lib-", "library-")):
        return "shared"
    return None
