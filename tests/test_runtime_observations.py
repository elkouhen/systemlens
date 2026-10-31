import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from systemlens.application.runtime_observations import (
    collect_runtime_observations,
    reconcile_observations,
    render_runtime_report_text,
)
from systemlens.delivery import cli
from systemlens.delivery.cli import app
from systemlens.discovery.elastic_apm import (
    ElasticApmError,
    cluster_origin,
    collect_observations,
    parse_window,
)
from systemlens.domain.graph import GraphEdge
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.runtime import RuntimeObservation
from systemlens.infrastructure.config import init_config
from systemlens.storage.sqlite import Store

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


def _endpoint(service: str, system: str, role: str, topic: str, *, dynamic: bool = False) -> MessageEndpoint:
    return MessageEndpoint(
        id=f"{service}:{system}:{role}:{topic}", role=role, system=system, topic=topic,
        topic_dynamic=dynamic, source="code", framework=None,
        path=f"{service}/Handler.java", start_line=7, end_line=7, snippet="", module=service,
    )


def _observation(kind: str, service: str, target: str, target_type: str | None,
                 calls: int = 10) -> RuntimeObservation:
    return RuntimeObservation(
        id=f"{kind}:{service}:{target}", source="elastic_apm", origin="https://es:9200",
        kind=kind, service_name=service, target=target, target_type=target_type,
        calls=calls, failures=0, duration_sum_us=None,
        window_start="2026-10-24T12:00:00Z", window_end="2026-10-31T12:00:00Z",
        collected_at="2026-10-31T12:00:00Z",
    )


class FakeElasticsearch:
    """Serve composite pages per index and record every request body."""

    def __init__(self, pages: dict[str, list[list[dict[str, object]]]]) -> None:
        self.pages = pages
        self.requests: list[tuple[str, dict[str, object]]] = []

    def __call__(self, index: str, body: dict[str, object]) -> dict[str, object]:
        self.requests.append((index, body))
        prefix = index.split(".", 1)[1].split(".", 1)[0]
        composite = body["aggs"]["groups"]["composite"]  # type: ignore[index]
        page = int(composite.get("after", {}).get("page", 0))  # type: ignore[union-attr]
        pages = self.pages.get(prefix, [])
        buckets = pages[page] if page < len(pages) else []
        groups: dict[str, object] = {"buckets": buckets}
        if page + 1 < len(pages):
            groups["after_key"] = {"page": page + 1}
        return {"aggregations": {"groups": groups}}


def _destination(service: str, target: str, outcome: str, calls: int,
                 target_type: str | None = None) -> dict[str, object]:
    return {
        "key": {"service": service, "target": target, "target_type": target_type, "outcome": outcome},
        "doc_count": 1, "calls": {"value": calls}, "duration_us": {"value": calls * 1000},
    }


def _transaction(service: str, kind: str, name: str, outcome: str, count: int) -> dict[str, object]:
    return {"key": {"service": service, "type": kind, "name": name, "outcome": outcome},
            "doc_count": count}


def test_collection_queries_only_aggregated_metrics_and_folds_outcomes() -> None:
    search = FakeElasticsearch({
        "service_destination": [
            [_destination("orders", "kafka/orders.placed", "success", 40, "kafka")],
            [_destination("orders", "kafka/orders.placed", "failure", 2, "kafka")],
        ],
        "transaction": [[
            _transaction("orders", "request", "POST /api/orders?debug=1", "success", 30),
            _transaction("orders", "request", "POST /api/orders", "failure", 3),
        ]],
    })

    observations = collect_observations(
        search, origin="https://es:9200", window=timedelta(days=7), now=NOW,
    )

    assert {index for index, _ in search.requests} == {
        "metrics-apm.service_destination.60m-*", "metrics-apm.transaction.60m-*",
    }
    for _index, body in search.requests:
        assert body["size"] == 0
        assert body["query"] == {"range": {"@timestamp": {
            "gte": "2026-10-24T12:00:00Z", "lt": "2026-10-31T12:00:00Z",
        }}}
    assert search.requests[1][1]["aggs"]["groups"]["composite"]["after"] == {"page": 1}  # type: ignore[index]
    assert [(item.kind, item.target, item.calls, item.failures) for item in observations] == [
        ("destination", "kafka/orders.placed", 42, 2),
        ("transaction", "POST /api/orders", 33, 3),
    ]
    assert observations[0].duration_sum_us == 42_000
    assert observations[0].window_end == "2026-10-31T12:00:00Z"


def test_collection_stops_before_an_unbounded_download() -> None:
    def endless(_index: str, _body: dict[str, object]) -> dict[str, object]:
        return {"aggregations": {"groups": {
            "buckets": [_destination("a", "b:1", "success", 1)], "after_key": {"x": 1},
        }}}

    with pytest.raises(ElasticApmError, match="réduisez la fenêtre"):
        collect_observations(endless, origin="https://es", window=timedelta(hours=1), now=NOW)


def test_cluster_url_and_window_are_validated() -> None:
    assert cluster_origin("https://es.example:9243/") == "https://es.example:9243"
    with pytest.raises(ElasticApmError, match="identifiants"):
        cluster_origin("https://elastic:secret@es.example")
    with pytest.raises(ElasticApmError):
        cluster_origin("ftp://es.example")
    assert parse_window("24h") == timedelta(hours=24)
    with pytest.raises(ElasticApmError):
        parse_window("0d")


def _lab_inventory() -> tuple[dict[str, list[MessageEndpoint]], list[GraphEdge]]:
    endpoints = {
        "order-service": [
            _endpoint("order-service", "rest", "serve", "POST /api/orders"),
            _endpoint("order-service", "rest", "call", "POST /api/reservations"),
            _endpoint("order-service", "kafka", "produce", "supermarket.order.placed"),
        ],
        "inventory-service": [
            _endpoint("inventory-service", "rest", "serve", "POST /api/reservations"),
            _endpoint("inventory-service", "rest", "serve", "GET /api/products/{productId}"),
            _endpoint("inventory-service", "kafka", "consume", "supermarket.order.placed"),
            _endpoint("inventory-service", "kafka", "produce", "supermarket.stock.depleted"),
            _endpoint("inventory-service", "kafka", "produce", "<dynamic>", dynamic=True),
        ],
        "restock-service": [
            _endpoint("restock-service", "kafka", "consume", "supermarket.stock.depleted"),
        ],
        "billing-service": [_endpoint("billing-service", "rest", "serve", "GET /api/bills")],
    }
    edges = [GraphEdge(
        "rest", "order-service", "inventory-service",
        endpoints["order-service"][1], endpoints["inventory-service"][0],
    )]
    return endpoints, edges


def test_reconciliation_classifies_agreement_and_gaps_in_both_directions() -> None:
    endpoints, edges = _lab_inventory()
    observations = [
        _observation("destination", "order-service", "kafka/supermarket.order.placed", "kafka"),
        _observation("destination", "order-service", "inventory-service:8080", "http"),
        _observation("destination", "order-service", "payments.example.com:443", "http"),
        _observation("destination", "inventory-service", "kafka/audit.events", "kafka"),
        _observation("destination", "inventory-service", "postgresql", "postgresql"),
        _observation("transaction", "order-service", "POST /api/orders", "request"),
        _observation("transaction", "inventory-service", "GET /api/products/{id}", "request"),
        _observation("transaction", "supermarket-restock-service",
                     "supermarket.stock.depleted process", "messaging"),
        _observation("transaction", "legacy-gateway", "GET /", "request"),
    ]

    result = reconcile_observations(observations, endpoints, edges)

    statuses = {(row["service_name"], row["target"]): row["status"] for row in result["observations"]}
    assert statuses == {
        ("order-service", "kafka/supermarket.order.placed"): "matched",
        ("order-service", "inventory-service:8080"): "matched",
        ("order-service", "payments.example.com:443"): "external",
        ("inventory-service", "kafka/audit.events"): "observed_only",
        ("inventory-service", "postgresql"): "not_compared",
        ("order-service", "POST /api/orders"): "matched",
        ("inventory-service", "GET /api/products/{id}"): "matched",
        ("supermarket-restock-service", "supermarket.stock.depleted process"): "matched",
        ("legacy-gateway", "GET /"): "not_compared",
    }
    audit = next(row for row in result["observations"] if row["target"] == "kafka/audit.events")
    assert "dynamique" in audit["hint"]
    assert [(item["service"], item["integration"], item["name"]) for item in result["static_only"]] == [
        ("inventory-service", "kafka_produce", "supermarket.stock.depleted"),
        ("inventory-service", "kafka_consume", "supermarket.order.placed"),
        ("inventory-service", "rest_serve", "POST /api/reservations"),
    ]
    assert "GET /api/products/{productId}" not in {item["name"] for item in result["static_only"]}
    assert result["unresolved_services"] == [
        {"name": "legacy-gateway", "status": "unmapped", "candidates": []},
    ]
    assert result["unobserved_services"] == ["billing-service"]
    assert result["summary"] == {
        "matched": 5, "observed_only": 1, "external": 1, "static_only": 3, "not_compared": 2,
    }
    assert "indexées mais sans trafic observé" in render_runtime_report_text(result)


def test_ambiguous_service_names_need_an_explicit_mapping() -> None:
    endpoints = {
        "orders": [_endpoint("orders", "rest", "serve", "GET /a")],
        "orders-api": [_endpoint("orders-api", "rest", "serve", "GET /b")],
    }
    observation = _observation("transaction", "prod-orders-api", "GET /b", "request")

    ambiguous = reconcile_observations([observation], endpoints, [])
    mapped = reconcile_observations([observation], endpoints, [], {"prod-orders-api": "orders-api"})

    assert ambiguous["unresolved_services"][0]["status"] == "ambiguous"
    assert ambiguous["unresolved_services"][0]["candidates"] == ["orders", "orders-api"]
    assert mapped["observations"][0]["status"] == "matched"
    with pytest.raises(ValueError, match="non indexés"):
        reconcile_observations([observation], endpoints, [], {"prod-orders-api": "missing"})


def test_observe_commands_persist_then_report_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_config(tmp_path)
    search = FakeElasticsearch({"service_destination": [[
        _destination("order-service", "kafka/supermarket.order.placed", "success", 5, "kafka"),
    ]]})
    observations = collect_runtime_observations(
        tmp_path, "https://es.example:9200", search=search, now=NOW,
    )
    endpoints, edges = _lab_inventory()
    monkeypatch.setattr(cli, "load_architecture_inventory", lambda _root: SimpleNamespace(
        endpoints_by_service=endpoints, relations=[],
    ))
    monkeypatch.setattr(cli, "graph_edges_from_relations", lambda _relations, _endpoints: edges)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["observe", "report", "--json"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert len(observations) == 1
    assert report["window"]["origin"] == "https://es.example:9200"
    assert report["observations"][0]["status"] == "matched"
    with Store(tmp_path, readonly=True) as store:
        assert [item.calls for item in store.all_runtime_observations()] == [5]
