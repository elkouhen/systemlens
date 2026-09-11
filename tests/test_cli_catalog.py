import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from systemlens import cli
from systemlens.cli import app


runner = CliRunner()


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> object:
    value = object()
    monkeypatch.setattr(cli, "_microservice_catalog", lambda _root: value)
    return value


@pytest.mark.parametrize(
    ("command", "kind"),
    [
        ("topics", "topic"),
        ("dtos", "dto"),
        ("apis", "api"),
        ("mongodb", "collection"),
    ],
)
def test_catalog_roots_list_their_object_kind(
    command: str,
    kind: str,
    catalog: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def list_objects(received_catalog: object, received_kind: str) -> list[dict[str, str]]:
        assert received_catalog is catalog
        assert received_kind == kind
        return [{"kind": kind, "name": "example"}]

    monkeypatch.setattr(cli, "list_architecture_objects", list_objects)

    result = runner.invoke(app, [command, "--root", str(tmp_path), "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == [{"kind": kind, "name": "example"}]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (
            ["topics", "consumers", "orders.created", "--json"],
            {"query": "consumers", "topic": "orders.created"},
        ),
        (
            ["dtos", "producers", "OrderCreated", "--json"],
            {"query": "producers", "dto": "OrderCreated", "microservices": ["orders"]},
        ),
        (
            ["apis", "providers", "GET /orders", "--json"],
            {"query": "providers", "api": "GET /orders", "microservices": ["orders"]},
        ),
        (
            ["mongodb", "services", "orders", "--json"],
            {"query": "services", "collection": "orders", "microservices": ["orders"]},
        ),
    ],
)
def test_catalog_relationship_commands_keep_their_public_result_shape(
    arguments: list[str],
    expected: dict[str, object],
    catalog: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "analyze_architecture",
        lambda received_catalog, query, topic: {
            "query": query,
            "topic": topic,
        }
        if received_catalog is catalog
        else None,
    )
    monkeypatch.setattr(
        cli,
        "show_architecture_object",
        lambda received_catalog, kind, _target: {
            "producer_microservices": ["orders"],
            "consumer_microservices": ["billing"],
            "providers": ["orders"],
            "consumers": ["billing"],
        }
        if received_catalog is catalog and kind in {"dto", "api"}
        else None,
    )
    monkeypatch.setattr(
        cli,
        "_mongodb_services",
        lambda received_catalog, collection: {
            "query": "services",
            "collection": collection,
            "microservices": ["orders"],
        }
        if received_catalog is catalog
        else None,
    )

    result = runner.invoke(app, arguments)

    assert result.exit_code == 0
    assert json.loads(result.output) == expected


def test_catalog_command_preserves_missing_object_error(
    catalog: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "show_architecture_object", lambda *_args: None)

    result = runner.invoke(app, ["apis", "show", "GET /missing"])

    assert result.exit_code == 2
    assert "API HTTP introuvable : GET /missing" in result.output
