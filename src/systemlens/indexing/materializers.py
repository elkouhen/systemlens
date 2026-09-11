"""Materializers that turn discovered evidence into persisted snapshot facts."""

import json

import yaml

from systemlens.domain.module_inventory import DiscoveredModule
from systemlens.discovery.build.modules import deduplicate_openapi_contract_owners


def materialize_openapi_contracts(
    modules: list[DiscoveredModule],
) -> list[dict[str, object]]:
    """Load each valid discovered OpenAPI contract exactly once.

    Discovery owns file attribution; this indexing projection owns reading and
    normalizing the contract before the store publishes the new snapshot.
    """
    contracts: list[dict[str, object]] = []
    indexed_paths = set()
    for module in deduplicate_openapi_contract_owners(modules):
        for path in module.openapi_files:
            contract_path = (module.path / path).resolve()
            if contract_path in indexed_paths:
                continue
            try:
                spec = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(spec, dict) or not ({"openapi", "swagger"} & spec.keys()):
                continue
            contracts.append({
                "module": module.identity or module.name,
                "path": path,
                "spec": json.loads(json.dumps(spec, default=str)),
            })
            indexed_paths.add(contract_path)
    return contracts
