"""Materializers that turn discovered evidence into persisted snapshot facts."""

import json
from pathlib import Path

import yaml

from systemlens.domain.module_inventory import DiscoveredModule
from systemlens.discovery.build.modules import deduplicate_openapi_contract_owners


def _json_value(value: object) -> object:
    """A YAML document as JSON: every key a string, other scalars via ``str``.

    ``json.dumps(default=str)`` converts values only; a YAML date or number
    used as a mapping key would raise ``TypeError``.
    """
    if isinstance(value, dict):
        return {
            _json_key(key): _json_value(item) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return json.loads(json.dumps(value, default=str))


def _json_key(key: object) -> str:
    """The key ``json.dumps`` would write, or ``str`` for a date or other type."""
    if isinstance(key, str):
        return key
    if key is None or isinstance(key, (bool, int, float)):
        return json.dumps(key)
    return str(key)


def _load_yaml(path: Path) -> object:
    """The parsed document, or ``None`` when the file is unreadable or invalid.

    A file that is not UTF-8 is not a contract; it must not abort the index.
    """
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None


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
            if {"target", "build"}.intersection(contract_path.parts):
                continue
            if contract_path in indexed_paths:
                continue
            spec = _load_yaml(contract_path)
            if not isinstance(spec, dict) or not ({"openapi", "swagger"} & spec.keys()):
                continue
            contracts.append({
                "module": module.identity or module.name,
                "path": path,
                "spec": _json_value(spec),
            })
            indexed_paths.add(contract_path)
    return contracts


def materialize_asyncapi_contracts(modules: list[DiscoveredModule]) -> list[dict[str, object]]:
    """Persist valid AsyncAPI documents discovered in production module trees."""
    contracts: list[dict[str, object]] = []
    seen: set[object] = set()
    for module in sorted(modules, key=lambda item: len(item.path.resolve().parts), reverse=True):
        for contract_path in sorted(module.path.rglob("*")):
            if not contract_path.is_file() or {"target", "build", ".git"}.intersection(contract_path.parts):
                continue
            if contract_path.suffix.lower() not in {".yaml", ".yml", ".json"}:
                continue
            spec = _load_yaml(contract_path)
            if not isinstance(spec, dict) or "asyncapi" not in spec or contract_path.resolve() in seen:
                continue
            contracts.append({"module": module.identity or module.name,
                              "path": contract_path.relative_to(module.path).as_posix(),
                              "spec": _json_value(spec)})
            seen.add(contract_path.resolve())
    return contracts
