from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from systemlens.delivery.cli import app
from systemlens.storage.sqlite import Store


RUNNER = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "endpoint_index_repo"


def _manifest() -> dict[str, object]:
    return {
        "format": "systemlens-ai-graph-v1",
        "generated_by": {"namespace": "ai-pass", "pass": "001"},
        "mode": "complete",
        "nodes": [
            {"id": "orders", "kind": "service", "name": "orders"},
            {
                "id": "orders-topic",
                "kind": "message_channel",
                "name": "orders.created",
                "technology": "kafka",
            },
            {
                "id": "orders-db",
                "kind": "data_schema",
                "name": "orders",
                "technology": "postgresql",
                "metadata": {"table": "orders"},
            },
        ],
        "edges": [
            {
                "id": "publish-orders",
                "source": "orders",
                "target": "orders-topic",
                "kind": "publishes",
                "relation": "publishes",
                "confidence": "high",
            },
            {
                "id": "write-orders",
                "source": "orders",
                "target": "orders-db",
                "kind": "writes",
                "relation": "writes",
                "confidence": "medium",
            },
        ],
    }


def test_cli_import_facts_reconciles_and_exports_graph_facts(
    tmp_path: Path, monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    manifest_path = repo / "facts.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    monkeypatch.chdir(repo)

    assert RUNNER.invoke(app, ["init"], catch_exceptions=False).exit_code == 0
    first = RUNNER.invoke(
        app, ["import-facts", "facts.json", "--namespace", "ai-pass", "--complete"]
    )
    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout)["inserted"] == 5

    exported = repo / "facts-export.json"
    result = RUNNER.invoke(
        app,
        ["export", "facts", str(exported), "--namespace", "ai-pass"],
    )
    assert result.exit_code == 0, result.output
    exported_manifest = json.loads(exported.read_text(encoding="utf-8"))
    assert exported_manifest["format"] == "systemlens-ai-graph-v1"
    assert len(exported_manifest["nodes"]) == 3
    assert len(exported_manifest["edges"]) == 2

    updated = _manifest()
    updated["nodes"][2]["metadata"] = {"table": "orders_v2"}
    manifest_path.write_text(json.dumps(updated), encoding="utf-8")
    second = RUNNER.invoke(
        app, ["import-facts", "facts.json", "--namespace", "ai-pass", "--complete"]
    )
    assert second.exit_code == 0, second.output
    assert json.loads(second.stdout)["updated"] == 5
    with Store(repo, readonly=True) as store:
        facts = store.graph_facts_by_namespace("ai-pass")
    db_fact = next(fact for fact in facts if fact.name == "orders" and fact.kind == "data_schema")
    assert db_fact.metadata == {"table": "orders_v2"}


def test_cli_import_facts_supports_direct_flow_bootstrap(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    manifest = _manifest()
    manifest["mode"] = "partial"
    manifest["endpoints"] = [{
        "id": "orders-out", "service": "orders", "role": "produce",
        "system": "kafka", "topic": "orders.created", "path": "README.md",
        "start_line": 1, "end_line": 1, "snippet": "publish",
    }]
    manifest["flows"] = [{
        "id": "orders-flow", "module": "orders", "method": "Orders.publish",
        "path": "README.md", "start_line": 1, "end_line": 1,
        "status": "potential", "confidence": "high",
        "reason": "direct source evidence",
        "steps": [{
            "order": 1, "kind": "message_publish", "name": "orders.created",
            "path": "README.md", "start_line": 1, "end_line": 1,
            "endpoint_id": "orders-out",
        }],
    }]
    (repo / "facts.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.chdir(repo)

    assert RUNNER.invoke(app, ["init"], catch_exceptions=False).exit_code == 0
    result = RUNNER.invoke(app, ["import-facts", "facts.json"])

    assert result.exit_code == 0, result.output
    with Store(repo, readonly=True) as store:
        assert len(store.all_endpoints()) == 1
        assert len(store.all_code_flows()) == 1
