"""Regressions of the source extraction and the inventory.

Each test reproduces a finding of the 2026-11-06 review (``BACKLOG.md``,
section 3.2) on a minimal fixture and names the rule it protects.
"""

from pathlib import Path

from systemlens.domain.module_inventory import DiscoveredModule
from systemlens.indexing.materializers import (
    materialize_asyncapi_contracts,
    materialize_openapi_contracts,
)


def _module(path: Path, name: str = "svc", openapi_files: tuple[str, ...] = ()) -> DiscoveredModule:
    return DiscoveredModule(
        name=name, path=path, build_system="maven", version=None, kind="library",
        starts_application=True, configuration_example="", openapi_files=openapi_files,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_non_utf8_yaml_file_is_no_contract_and_no_crash(tmp_path: Path) -> None:
    """Review P3: a Latin-1 YAML file aborted the whole index."""
    svc = tmp_path / "svc"
    (svc / "src/main/resources").mkdir(parents=True)
    (svc / "src/main/resources/messages.yml").write_bytes(b"# r\xe9sum\xe9\nkey: value\n")
    (svc / "src/main/resources/openapi.yml").write_bytes(b"openapi: 3.0.0\n# \xe9\n")
    _write(svc / "src/main/resources/asyncapi.yml", "asyncapi: '2.6.0'\nchannels: {}\n")

    contracts = materialize_asyncapi_contracts([_module(svc)])
    openapi = materialize_openapi_contracts(
        [_module(svc, openapi_files=("src/main/resources/openapi.yml",))]
    )

    assert [contract["path"] for contract in contracts] == ["src/main/resources/asyncapi.yml"]
    assert openapi == []


def test_a_yaml_date_key_is_persisted_as_its_iso_text(tmp_path: Path) -> None:
    """Review P4: a date key raised ``TypeError`` in the JSON projection."""
    svc = tmp_path / "svc"
    _write(
        svc / "src/main/resources/asyncapi.yml",
        "asyncapi: '2.6.0'\ninfo: {title: t, version: '1'}\n"
        "x-changelog:\n  2024-01-01: initial\n  3: third\n",
    )
    _write(
        svc / "src/main/resources/openapi.yml",
        "openapi: 3.0.0\nx-changelog:\n  2024-01-01: initial\n",
    )

    contracts = materialize_asyncapi_contracts([_module(svc)])
    openapi = materialize_openapi_contracts(
        [_module(svc, openapi_files=("src/main/resources/openapi.yml",))]
    )

    assert contracts[0]["spec"]["x-changelog"] == {"2024-01-01": "initial", "3": "third"}  # type: ignore[index]
    assert openapi[0]["spec"]["x-changelog"] == {"2024-01-01": "initial"}  # type: ignore[index]
