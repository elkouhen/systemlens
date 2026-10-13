from __future__ import annotations

import json
import re
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from systemlens.domain.code_flows import CodeFlow, CodeFlowStep
from systemlens.domain.models import MessageEndpoint
from systemlens.delivery.cli import app
from systemlens.render.html_export import render_graph_html
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
        "id": "orders-in", "service": "orders", "role": "serve",
        "system": "rest", "topic": "POST /orders", "path": "README.md",
        "start_line": 1, "end_line": 1, "snippet": "request",
    }, {
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
            "order": 1, "kind": "http_entry", "name": "POST /orders",
            "path": "README.md", "start_line": 1, "end_line": 1,
            "endpoint_id": "orders-in",
        }, {
            "order": 2, "kind": "data_write", "name": "orders",
            "path": "README.md", "start_line": 1, "end_line": 1,
            "action": "delete", "resource": "orders",
        }, {
            "order": 3, "kind": "message_publish", "name": "orders.created",
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
        assert len(store.all_endpoints()) == 2
        assert len(store.all_code_flows()) == 1
        assert store.all_code_flows()[0].steps[1].action == "delete"


def _indexed_order_flow(tmp_path: Path, monkeypatch) -> tuple[Path, MessageEndpoint, CodeFlow]:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    monkeypatch.chdir(repo)
    assert RUNNER.invoke(app, ["init"]).exit_code == 0
    endpoint = MessageEndpoint(
        id="order-entry", role="serve", system="rest", topic="GET /orders",
        topic_dynamic=False, source="code", framework="spring-mvc",
        path="Orders.java", start_line=1, end_line=1, snippet="route", module="orders",
    )
    flow = CodeFlow(
        id="order-flow", module="orders", method="Orders.get", path="Orders.java",
        start_line=1, end_line=3, status="potential", confidence="medium",
        reason="indexed call", steps=(
            CodeFlowStep(1, "http_entry", "GET /orders", "Orders.java", 1, 1, endpoint.id),
        ),
    )
    with Store(repo) as store:
        store.replace_endpoints_for_files([], [endpoint])
        store.replace_code_flows([flow])
    return repo, endpoint, flow


def _access_manifest(flow_id: str, action: str, branch: str) -> dict[str, object]:
    manifest = _manifest()
    manifest["nodes"].append({
        "id": "order-access", "kind": "flow_data_access", "name": "OrderEntity",
        "confidence": "medium", "metadata": {
            "flow_id": flow_id, "action": action, "branch": branch,
        },
        "evidence": [{"path": "Orders.java", "start_line": 2}],
    })
    return manifest


def _graph_data(document: str) -> dict[str, object]:
    match = re.search(r'<script id="graph-data" type="application/json">(.*)</script>', document)
    assert match is not None
    return json.loads(match.group(1))


@pytest.mark.parametrize(("action", "branch"), [
    ("read", "sync"),
    ("create_or_update", "async"),
    ("delete", "async"),
])
def test_imported_data_access_reaches_cli_html_export(
    tmp_path: Path, monkeypatch, action: str, branch: str,
) -> None:
    repo, _, _ = _indexed_order_flow(tmp_path, monkeypatch)
    (repo / "facts.json").write_text(
        json.dumps(_access_manifest("order-flow", action, branch)), encoding="utf-8"
    )
    imported = RUNNER.invoke(app, ["import-facts", "facts.json"])
    assert imported.exit_code == 0, imported.output

    exported = RUNNER.invoke(app, [
        "export", "microservices", "--html", "graph.html",
    ])
    assert exported.exit_code == 0, exported.output
    graph = _graph_data((repo / "graph.html").read_text(encoding="utf-8"))
    accesses = graph["flow_data_accesses"]["order-flow"]
    assert len(accesses) == 1
    assert accesses[0]["action"] == action
    assert accesses[0]["branch"] == branch
    assert accesses[0]["origin"] == "imported"
    assert accesses[0]["path"] == "Orders.java"
    assert all(node["name"] != "OrderEntity" for node in graph["nodes"])

    plain_export = RUNNER.invoke(app, [
        "export", "microservices", "--html", "graph-plain.html",
        "--no-call-graph-annotations", "--call-graphs-only",
    ])
    assert plain_export.exit_code == 0, plain_export.output
    plain_graph = _graph_data((repo / "graph-plain.html").read_text(encoding="utf-8"))
    assert plain_graph["call_graphs_only"] is True
    assert plain_graph["flow_data_accesses"] == {}
    assert plain_graph["flow_diagnostics"] == []
    assert plain_graph["call_graphs"] == graph["call_graphs"]
    assert [flow["id"] for flow in plain_graph["code_flows"]] == [
        flow["id"] for flow in graph["code_flows"]
    ]


def test_imported_data_access_survives_fact_round_trip(tmp_path: Path, monkeypatch) -> None:
    repo, _, _ = _indexed_order_flow(tmp_path, monkeypatch)
    (repo / "facts.json").write_text(
        json.dumps(_access_manifest("order-flow", "delete", "sync")), encoding="utf-8"
    )
    assert RUNNER.invoke(app, ["import-facts", "facts.json"]).exit_code == 0
    exported = RUNNER.invoke(app, [
        "export", "facts", "facts-export.json", "--namespace", "ai-pass",
    ])
    assert exported.exit_code == 0, exported.output
    round_trip = RUNNER.invoke(app, ["import-facts", "facts-export.json"])
    assert round_trip.exit_code == 0, round_trip.output
    with Store(repo, readonly=True) as store:
        facts = store.graph_facts_by_namespace("ai-pass")
    annotation = next(fact for fact in facts if fact.kind == "flow_data_access")
    assert annotation.metadata == {
        "flow_id": "order-flow", "action": "delete", "branch": "sync",
    }


def test_imported_data_access_rejects_unknown_flow_without_mutation(
    tmp_path: Path, monkeypatch,
) -> None:
    repo, _, _ = _indexed_order_flow(tmp_path, monkeypatch)
    manifest = _access_manifest("order-flow", "delete", "sync")
    (repo / "facts.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert RUNNER.invoke(app, ["import-facts", "facts.json"]).exit_code == 0
    with Store(repo, readonly=True) as store:
        existing = store.graph_facts_by_namespace("ai-pass")

    manifest["nodes"][-1]["metadata"]["flow_id"] = "missing-flow"
    (repo / "facts.json").write_text(json.dumps(manifest), encoding="utf-8")
    rejected = RUNNER.invoke(app, ["import-facts", "facts.json"])
    assert rejected.exit_code == 2
    with Store(repo, readonly=True) as store:
        assert store.graph_facts_by_namespace("ai-pass") == existing


def test_imported_data_access_does_not_annotate_replaced_flow(
    tmp_path: Path, monkeypatch,
) -> None:
    repo, endpoint, flow = _indexed_order_flow(tmp_path, monkeypatch)
    (repo / "facts.json").write_text(
        json.dumps(_access_manifest("order-flow", "delete", "sync")), encoding="utf-8"
    )
    assert RUNNER.invoke(app, ["import-facts", "facts.json"]).exit_code == 0

    with Store(repo) as store:
        store.replace_code_flows([replace(flow, id="new-order-flow")])
        retained_facts = store.all_graph_facts()
        new_flows = store.all_code_flows()
    document = render_graph_html(
        {"orders": [endpoint]}, [], graph_facts=retained_facts, code_flows=new_flows,
    )
    assert not _graph_data(document)["flow_data_accesses"]
