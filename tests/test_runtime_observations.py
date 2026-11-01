import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
import yaml
from typer.testing import CliRunner

from systemlens.application.runtime_observations import (
    PrometheusSource,
    collect_prometheus_observations,
    collect_runtime_observations,
    compare_with_previous,
    completeness_line,
    parse_service_map,
    rank_hotspots,
    reconcile_observations,
    render_runtime_report_text,
    runtime_overlay,
    scheduled_entries,
)
from systemlens.delivery import cli
from systemlens.delivery.cli import app
from systemlens.discovery import prometheus_otel
from systemlens.discovery.elastic_apm import (
    ElasticApmError,
    cluster_origin,
    collect,
    collect_error_groups,
    collect_observations,
    parse_window,
)
from systemlens.domain.code_flows import CodeFlow, CodeFlowStep
from systemlens.domain.graph import GraphEdge
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.runtime import RuntimeCollection, RuntimeErrorGroup, RuntimeObservation
from systemlens.infrastructure.config import ConfigError, config_path, init_config, load_config
from systemlens.render import render_graph_html
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

    def __init__(
        self, pages: dict[str, list[list[dict[str, object]]]], intervals: tuple[str, ...] = ("60m",),
    ) -> None:
        self.pages = pages
        self.intervals = intervals
        self.requests: list[tuple[str, dict[str, object]]] = []

    def __call__(self, index: str, body: dict[str, object]) -> dict[str, object]:
        self.requests.append((index, body))
        if "aggs" not in body:
            interval = index.rsplit(".", 1)[1].removesuffix("-*")
            return {"hits": {"total": {"value": int(interval in self.intervals), "relation": "gte"}}}
        prefix = index.split(".", 1)[1].split(".", 1)[0].removesuffix("-*")
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


def _transaction(
    service: str, kind: str, name: str, outcome: str, count: int,
    percentiles: tuple[float | None, float | None, float | None] | None = None,
) -> dict[str, object]:
    """A transaction bucket: failures and percentiles are sub-aggregations."""
    p50, p95, p99 = percentiles or (None, None, None)
    return {
        "key": {"service": service, "type": kind, "name": name},
        "doc_count": count,
        "failures": {"doc_count": count if outcome == "failure" else 0},
        "duration": {"values": {"50.0": p50, "95.0": p95, "99.0": p99}},
    }


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
    transaction_request = search.requests[2][1]["aggs"]["groups"]  # type: ignore[index]
    assert [list(source)[0] for source in transaction_request["composite"]["sources"]] == [  # type: ignore[index]
        "service", "type", "name",
    ]
    assert transaction_request["aggs"] == {  # type: ignore[index]
        "failures": {"filter": {"term": {"event.outcome": "failure"}}},
        "duration": {"percentiles": {
            "field": "transaction.duration.histogram", "percents": [50.0, 95.0, 99.0],
        }},
    }
    assert [(item.kind, item.target, item.calls, item.failures) for item in observations] == [
        ("destination", "kafka/orders.placed", 42, 2),
        ("transaction", "POST /api/orders", 33, 3),
    ]
    assert observations[0].duration_sum_us == 42_000
    assert observations[0].window_end == "2026-10-31T12:00:00Z"
    assert observations[0].duration_p95_us is None
    assert observations[0].collection_id == observations[1].collection_id


def test_transactions_carry_backend_percentiles_or_null_with_a_note() -> None:
    search = FakeElasticsearch({"transaction": [[
        _transaction("orders", "request", "POST /api/orders", "success", 30, (1200.0, 4800.0, 9100.0)),
        _transaction("orders", "request", "GET /api/orders", "success", 5),
    ]]})

    result = collect(search, origin="https://es:9200", window=timedelta(days=1), now=NOW)

    by_target = {item.target: item for item in result.observations}
    assert (
        by_target["POST /api/orders"].duration_p50_us,
        by_target["POST /api/orders"].duration_p95_us,
        by_target["POST /api/orders"].duration_p99_us,
    ) == (1200.0, 4800.0, 9100.0)
    assert by_target["GET /api/orders"].duration_p95_us is None
    assert result.collection.interval == "60m"
    assert result.collection.overflow_services == ()
    assert result.collection.page_counts == {
        "metrics-apm.service_destination.60m-*": 1, "metrics-apm.transaction.60m-*": 1,
    }
    assert any("percentiles indisponibles pour 1 transaction" in note for note in result.collection.notes)
    assert "ni débordement ni troncature" in completeness_line(result.collection)


def test_overflow_buckets_mark_the_collection_and_name_the_service() -> None:
    search = FakeElasticsearch({
        "service_destination": [[
            _destination("orders", "kafka/orders.placed", "success", 40, "kafka"),
            _destination("orders", "_other", "success", 9, "kafka"),
            _destination("_other", "payments:443", "success", 7, "http"),
        ]],
        "transaction": [[_transaction("orders", "request", "POST /api/orders", "success", 30)]],
    })

    result = collect(search, origin="https://es:9200", window=timedelta(days=1), now=NOW)

    assert [item.target for item in result.observations] == ["kafka/orders.placed", "POST /api/orders"]
    assert result.collection.overflow_services == ("_other", "orders")
    assert "débordement d'agrégation pour _other, orders (compteurs minimaux)" in (
        completeness_line(result.collection)
    )
    endpoints, edges = _lab_inventory()
    reconciled = reconcile_observations(
        [RuntimeObservation(**{**item.__dict__, "service_name": "order-service"})
         for item in result.observations],
        endpoints, edges,
        collections=[RuntimeCollection(**{**result.collection.__dict__, "overflow_services": ("order-service",)})],
    )
    assert {row["completeness"] for row in reconciled["observations"]} == {"incomplete"}
    assert reconciled["window"]["collections"][0]["overflow_services"] == ["order-service"]
    report = render_runtime_report_text(reconciled)
    assert "Complétude : Elastic APM https://es:9200" in report
    assert "compteurs minimaux" in report


def _error_bucket(service: str, key: str, exception: str | None, transaction: str | None,
                  count: int, culprit: str | None = "OrderController in triggerOutOfStock") -> dict[str, object]:
    def top(value: str | None) -> dict[str, object]:
        return {"buckets": [{"key": value, "doc_count": count}] if value else []}

    return {
        "key": {"service": service, "grouping_key": key}, "doc_count": count,
        "last_seen": {"value": 1, "value_as_string": "2026-10-31T11:58:00.000Z"},
        "exception_type": top(exception), "culprit": top(culprit), "transaction": top(transaction),
    }


def test_error_groups_are_aggregated_without_reading_messages() -> None:
    search = FakeElasticsearch({"error": [[
        _error_bucket("orders", "g1", "OutOfStockException", "GET /api/error?x=1", 5),
        _error_bucket("orders", "g2", None, None, 1, culprit=None),
    ]]})

    groups = collect_error_groups(search, origin="https://es:9200", window=timedelta(days=1), now=NOW)

    [(index, body)] = search.requests
    assert index == "logs-apm.error-*"
    assert body["size"] == 0
    assert "message" not in json.dumps(body)
    assert [(g.grouping_key, g.exception_type, g.transaction_name, g.occurrences) for g in groups] == [
        ("g1", "OutOfStockException", "GET /api/error", 5), ("g2", None, None, 1),
    ]
    assert groups[0].last_seen == "2026-10-31T11:58:00.000Z"
    assert groups[0].culprit == "OrderController in triggerOutOfStock"
    assert FakeElasticsearch({})("logs-apm.error-*", body) == {"aggregations": {"groups": {"buckets": []}}}


def test_error_groups_attach_to_the_indexed_entry_point_of_their_transaction() -> None:
    endpoints, edges = _lab_inventory()
    error = RuntimeErrorGroup(
        id="e1", source="elastic_apm", origin="https://es:9200", service_name="order-service",
        grouping_key="g1", exception_type="OutOfStockException", culprit="OrderController",
        transaction_name="POST /api/orders/", occurrences=5, last_seen=None,
        window_start="2026-10-24T12:00:00Z", window_end="2026-10-31T12:00:00Z",
        collected_at="2026-10-31T12:00:00Z",
    )
    ignored = RuntimeErrorGroup(**{**error.__dict__, "id": "e2", "service_name": "otel-edge"})

    result = reconcile_observations(
        _lab_observations(), endpoints, edges, ignored_services=["otel-edge"], errors=[error, ignored],
    )
    overlay = runtime_overlay(result, endpoints)

    assert [(row["service"], row["endpoint_ids"]) for row in result["errors"]] == [
        ("order-service", ["order-service:rest:serve:POST /api/orders"]),
    ]
    assert overlay is not None
    assert overlay["endpoint_errors"] == {"order-service:rest:serve:POST /api/orders": [0]}
    report = render_runtime_report_text(result)
    assert "order-service OutOfStockException × 5 sur POST /api/orders/" in report
    assert "Aucune erreur APM" in render_runtime_report_text(
        reconcile_observations(_lab_observations(), endpoints, edges)
    )


def test_scheduled_transactions_match_their_scheduled_entry_and_data_is_per_service() -> None:
    endpoints, edges = _lab_inventory()
    path = "order-service/src/main/java/io/example/ScheduledOrderPublisher.java"
    flow = CodeFlow(
        id="cron", module="order-service",
        method="io.example.ScheduledOrderPublisher.publishOnlineOrder",
        path=path, start_line=32, end_line=40, status="potential", confidence="low",
        reason="scheduler", steps=(
            CodeFlowStep(order=1, kind="cron_entry", name="@Scheduled", path=path,
                         start_line=32, end_line=32),
        ),
    )
    observations = [
        *_lab_observations(),
        _observation("transaction", "order-service", "ScheduledOrderPublisher.publishOnlineOrder", "unknown", 220),
        _observation("transaction", "inventory-service", "SpringDataProductRepository.count", "unknown", 1),
        _observation("destination", "inventory-service", "postgresql", "postgresql", 1517),
    ]

    result = reconcile_observations(
        observations, endpoints, edges,
        scheduled=scheduled_entries([flow], endpoints),
    )
    overlay = runtime_overlay(result, endpoints)

    by_target = {row["target"]: row for row in result["observations"]}
    scheduler = by_target["ScheduledOrderPublisher.publishOnlineOrder"]
    assert scheduler["status"] == "matched"
    assert scheduler["endpoint_ids"] == [f"cron:{path}:32"]
    # Repository spans share the ``Class.method`` shape but name no scheduler.
    assert by_target["SpringDataProductRepository.count"]["status"] == "not_compared"
    assert overlay is not None
    assert f"cron:{path}:32" in overlay["endpoint_observations"]
    assert {"target": "postgresql", "calls": 1517, "failures": 0} in (
        overlay["data_destinations"]["inventory-service"]
    )


def test_collection_stops_before_an_unbounded_download() -> None:
    def endless(_index: str, _body: dict[str, object]) -> dict[str, object]:
        return {"aggregations": {"groups": {
            "buckets": [_destination("a", "b:1", "success", 1)], "after_key": {"x": 1},
        }}}

    with pytest.raises(ElasticApmError, match="réduisez la fenêtre"):
        collect_observations(endless, origin="https://es", window=timedelta(hours=1), now=NOW)


def test_auto_interval_uses_the_coarsest_rollup_with_data() -> None:
    search = FakeElasticsearch({"transaction": [[
        _transaction("orders", "request", "POST /api/orders", "success", 3),
    ]]}, intervals=("1m",))

    observations = collect_observations(
        search, origin="https://es", window=timedelta(days=1), interval="auto", now=NOW,
    )

    assert [index for index, _ in search.requests] == [
        "metrics-apm.transaction.60m-*", "metrics-apm.transaction.10m-*",
        "metrics-apm.transaction.1m-*", "metrics-apm.service_destination.1m-*",
        "metrics-apm.transaction.1m-*",
    ]
    assert search.requests[0][1]["track_total_hits"] == 1
    assert [item.calls for item in observations] == [3]
    with pytest.raises(ElasticApmError, match="Aucune métrique APM"):
        collect_observations(
            FakeElasticsearch({}, intervals=()), origin="https://es",
            window=timedelta(days=1), interval="auto", now=NOW,
        )


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
    edges = [
        GraphEdge(
            "rest", "order-service", "inventory-service",
            endpoints["order-service"][1], endpoints["inventory-service"][0],
        ),
        GraphEdge(
            "kafka", "order-service", "inventory-service",
            endpoints["order-service"][2], endpoints["inventory-service"][2],
        ),
        GraphEdge(
            "kafka", "inventory-service", "restock-service",
            endpoints["inventory-service"][3], endpoints["restock-service"][0],
        ),
    ]
    return endpoints, edges


def _lab_observations() -> list[RuntimeObservation]:
    return [
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


def test_reconciliation_classifies_agreement_and_gaps_in_both_directions() -> None:
    endpoints, edges = _lab_inventory()

    result = reconcile_observations(_lab_observations(), endpoints, edges)

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
        ("inventory-service", "rest_serve", "POST /api/reservations"),
    ]
    # No messaging transaction proves inventory consumers are instrumented.
    assert [(item["integration"], item["name"], item["reason"]) for item in result["unverified"]] == [
        ("kafka_consume", "supermarket.order.placed", "aucune consommation Kafka observée"),
    ]
    assert "GET /api/products/{productId}" not in {item["name"] for item in result["static_only"]}
    assert result["unresolved_services"] == [
        {"name": "legacy-gateway", "status": "unmapped", "candidates": []},
    ]
    assert result["unobserved_services"] == ["billing-service"]
    assert result["summary"] == {
        "matched": 5, "observed_only": 1, "external": 1, "static_only": 2, "unverified": 1,
        "not_compared": 2,
    }
    assert "indexées mais sans trafic observé" in render_runtime_report_text(result)


def test_reconciliation_rows_name_the_static_evidence_they_match() -> None:
    endpoints, edges = _lab_inventory()
    result = reconcile_observations(_lab_observations(), endpoints, edges)

    evidence = {
        row["target"]: (row["endpoint_ids"], row.get("edge"))
        for row in result["observations"]
    }
    assert evidence["kafka/supermarket.order.placed"] == (
        ["order-service:kafka:produce:supermarket.order.placed"], None,
    )
    assert evidence["inventory-service:8080"] == (
        ["order-service:rest:call:POST /api/reservations"],
        {"source": "order-service", "target": "inventory-service"},
    )
    assert evidence["GET /api/products/{id}"] == (
        ["inventory-service:rest:serve:GET /api/products/{productId}"], None,
    )
    assert evidence["supermarket.stock.depleted process"] == (
        ["restock-service:kafka:consume:supermarket.stock.depleted"], None,
    )
    for target in ("payments.example.com:443", "kafka/audit.events", "postgresql", "GET /"):
        assert evidence[target] == ([], None)
    assert [item["endpoint_ids"] for item in result["static_only"]] == [
        ["inventory-service:kafka:produce:supermarket.stock.depleted"],
        ["inventory-service:rest:serve:POST /api/reservations"],
    ]


def test_silence_needs_instrumentation_that_names_the_traffic() -> None:
    """Reproduce the observability lab: OTel names HTTP server spans `POST`."""
    endpoints = {"orders": [
        _endpoint("orders", "rest", "serve", "POST /api/orders"),
        _endpoint("orders", "rest", "serve", "GET /api/health"),
        _endpoint("orders", "kafka", "produce", "orders.placed"),
    ]}
    observations = [
        _observation("transaction", "orders", "POST", "request", 309),
        _observation("transaction", "orders", "GET /api/health", "request", 930),
        _observation("destination", "orders", "postgresql", "postgresql", 12),
    ]

    result = reconcile_observations(observations, endpoints, [])

    assert result["static_only"] == []
    assert [(item["name"], item["reason"]) for item in result["unverified"]] == [
        ("orders.placed", "aucune publication Kafka observée"),
        ("POST /api/orders", "transactions HTTP sans route : trafic non attribuable"),
    ]
    assert result["rest_call_covered"] == []
    assert "non vérifiables" in render_runtime_report_text(result)


def test_routes_that_normalize_alike_share_one_observation() -> None:
    endpoints = {"catalog": [
        _endpoint("catalog", "rest", "serve", "GET /items/{id}"),
        _endpoint("catalog", "rest", "serve", "GET /items/{sku}"),
    ]}
    observation = _observation("transaction", "catalog", "GET /items/{itemId}", "request")

    result = reconcile_observations([observation], endpoints, [])
    overlay = runtime_overlay(result, endpoints)

    assert result["observations"][0]["endpoint_ids"] == [
        "catalog:rest:serve:GET /items/{id}", "catalog:rest:serve:GET /items/{sku}",
    ]
    assert overlay is not None
    assert overlay["endpoint_observations"] == {
        "catalog:rest:serve:GET /items/{id}": [0], "catalog:rest:serve:GET /items/{sku}": [0],
    }


def test_runtime_overlay_indexes_traffic_by_endpoint_and_service() -> None:
    endpoints, edges = _lab_inventory()
    result = reconcile_observations(_lab_observations(), endpoints, edges)

    overlay = runtime_overlay(result, endpoints)

    assert overlay is not None
    rows = overlay["observations"]
    by_endpoint = {
        endpoint_id: [rows[index]["target"] for index in indexes]
        for endpoint_id, indexes in overlay["endpoint_observations"].items()
    }
    assert by_endpoint == {
        "inventory-service:rest:serve:GET /api/products/{productId}": ["GET /api/products/{id}"],
        "order-service:kafka:produce:supermarket.order.placed": ["kafka/supermarket.order.placed"],
        "order-service:rest:call:POST /api/reservations": ["inventory-service:8080"],
        "order-service:rest:serve:POST /api/orders": ["POST /api/orders"],
        "restock-service:kafka:consume:supermarket.stock.depleted": [
            "supermarket.stock.depleted process",
        ],
    }
    assert overlay["silent_endpoint_ids"] == [
        "inventory-service:kafka:produce:supermarket.stock.depleted",
        "inventory-service:rest:serve:POST /api/reservations",
    ]
    assert overlay["rest_call_covered"] == ["order-service"]
    assert overlay["services"] == {
        "billing-service": "no_telemetry", "inventory-service": "observed",
        "order-service": "observed", "restock-service": "observed",
    }
    assert overlay["window"]["origin"] == "https://es:9200"
    assert runtime_overlay(reconcile_observations([], endpoints, edges), endpoints) is None


def test_service_map_comes_from_config_and_command_line_overrides_it(tmp_path: Path) -> None:
    init_config(tmp_path)
    path = config_path(tmp_path)
    raw = yaml.safe_load(path.read_text())
    raw["runtime"] = {"service_map": {"prod-orders": "orders", "prod-stock": "stock"}}
    path.write_text(yaml.safe_dump(raw))

    raw["runtime"]["ignore_services"] = ["otel-edge", " elastic-otel-collector "]
    path.write_text(yaml.safe_dump(raw))
    config = load_config(tmp_path)
    configured = config.runtime_service_map

    assert configured == {"prod-orders": "orders", "prod-stock": "stock"}
    assert config.runtime_ignore_services == ["otel-edge", "elastic-otel-collector"]
    assert parse_service_map(["prod-stock=inventory"], configured) == {
        "prod-orders": "orders", "prod-stock": "inventory",
    }
    raw["runtime"] = {"service_map": {"prod-orders": 3}}
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match="runtime.service_map"):
        load_config(tmp_path)
    raw["runtime"] = {"ignore_services": "otel-edge"}
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match="runtime.ignore_services"):
        load_config(tmp_path)


def _payload(document: str) -> dict[str, object]:
    match = re.search(r'<script id="graph-data" type="application/json">(.*?)</script>', document)
    assert match is not None
    return json.loads(match.group(1))


def test_html_export_carries_the_overlay_only_when_observations_exist() -> None:
    endpoints, edges = _lab_inventory()
    overlay = runtime_overlay(reconcile_observations(_lab_observations(), endpoints, edges), endpoints)

    with_overlay = _payload(render_graph_html(endpoints, edges, runtime_overlay=overlay))
    without = _payload(render_graph_html(endpoints, edges))
    call_graphs_only = _payload(render_graph_html(
        endpoints, edges, runtime_overlay=overlay, call_graphs_only=True,
    ))

    assert with_overlay["runtime"] == json.loads(json.dumps(overlay))
    assert "runtime" not in without
    assert "runtime" not in call_graphs_only
    assert {key: value for key, value in with_overlay.items() if key != "runtime"} == without


def test_export_rejects_a_service_map_that_targets_an_unindexed_service(
    tmp_path: Path,
) -> None:
    init_config(tmp_path)
    path = config_path(tmp_path)
    raw = yaml.safe_load(path.read_text())
    raw["runtime"] = {"service_map": {"supermarket-restock-service": "missing"}}
    path.write_text(yaml.safe_dump(raw))
    endpoints, edges = _lab_inventory()
    graph_data = SimpleNamespace(
        runtime_observations=_lab_observations(), services_by_name=endpoints, edges=edges,
    )

    with pytest.raises(typer.Exit):
        cli._export_runtime_overlay(tmp_path, graph_data)  # type: ignore[arg-type]
    raw["runtime"] = {
        "service_map": {"supermarket-restock-service": "restock-service"},
        "ignore_services": ["legacy-gateway"],
    }
    path.write_text(yaml.safe_dump(raw))
    overlay = cli._export_runtime_overlay(tmp_path, graph_data)  # type: ignore[arg-type]
    assert overlay is not None and overlay["services"]["restock-service"] == "observed"
    assert overlay["unresolved_services"] == []
    assert cli._export_runtime_overlay(
        tmp_path, SimpleNamespace(runtime_observations=[]),  # type: ignore[arg-type]
    ) is None


def test_ignored_apm_services_are_neither_gaps_nor_evidence() -> None:
    endpoints, edges = _lab_inventory()
    observations = [
        *_lab_observations(),
        _observation("transaction", "otel-edge", "POST", "request", 500),
        _observation("destination", "otel-edge", "elasticsearch:9200", "http", 500),
    ]

    result = reconcile_observations(
        observations, endpoints, edges, ignored_services=["otel-edge", "legacy-gateway"],
    )

    ignored = [row for row in result["observations"] if row["service_name"] in {"otel-edge", "legacy-gateway"}]
    assert {(row["status"], row["service"], row["hint"]) for row in ignored} == {
        ("not_compared", None, "service APM ignoré (runtime.ignore_services)"),
    }
    assert result["unresolved_services"] == []
    assert result["summary"]["not_compared"] == 4


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
    search = FakeElasticsearch({
        "service_destination": [[
            _destination("order-service", "kafka/supermarket.order.placed", "success", 5, "kafka"),
        ]],
        "error": [[_error_bucket("order-service", "g1", "OutOfStockException", "GET /api/orders", 4)]],
    })
    observations, errors = collect_runtime_observations(
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
    assert [error["occurrences"] for error in report["errors"]] == [4]
    with Store(tmp_path, readonly=True) as store:
        assert [item.calls for item in store.all_runtime_observations()] == [5]
        assert [item.exception_type for item in store.all_runtime_error_groups()] == ["OutOfStockException"]
        assert [item.source for item in store.latest_runtime_collections()] == ["elastic_apm"]
    assert len(errors) == 1
    assert "Complétude : Elastic APM https://es.example:9200" in CliRunner().invoke(
        app, ["observe", "report"],
    ).output

    # A second collection of the same interval enables the comparison; the
    # hotspot ranking reads the latest one.
    later = FakeElasticsearch({
        "service_destination": [[
            _destination("order-service", "kafka/supermarket.order.placed", "success", 9, "kafka"),
        ]],
        "transaction": [[
            _transaction("order-service", "request", "POST /api/orders", "failure", 4, (10.0, 90.0, 100.0)),
            _transaction("inventory-service", "request", "GET /api/products/{id}", "success", 50, (5.0, 400.0, 900.0)),
        ]],
    })
    collect_runtime_observations(
        tmp_path, "https://es.example:9200", search=later, now=NOW + timedelta(days=1),
    )
    compared = CliRunner().invoke(
        app, ["observe", "report", "--json", "--compare-previous", "--hotspots", "1", "--by", "p95"],
    )
    assert compared.exit_code == 0, compared.output
    payload = json.loads(compared.output)
    assert payload["comparison"]["elastic_apm"]["reason"] is None
    assert [(row["target"], row["calls_delta"]) for row in payload["comparison"]["elastic_apm"]["rows"]] == [
        ("kafka/supermarket.order.placed", 4),
    ]
    assert [(row["target"], row["value"]) for row in payload["hotspots"]["rows"]] == [
        ("GET /api/products/{id}", 400.0),
    ]
    text = CliRunner().invoke(app, ["observe", "report", "--hotspots", "2", "--by", "failures"]).output
    assert "Points chauds par échecs" in text
    assert " 1. order-service POST /api/orders · échecs 4" in text
    assert CliRunner().invoke(app, ["observe", "report", "--by", "latency"]).exit_code == 2


@pytest.mark.slow
@pytest.mark.integration
def test_runtime_overlay_annotates_the_graph_without_moving_it() -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")
    endpoints, edges = _lab_inventory()
    observations = [
        *_lab_observations(),
        _observation("transaction", "inventory-service", "POST /api/reservations", "request", 90),
    ]
    overlay = runtime_overlay(reconcile_observations(observations, endpoints, edges), endpoints)
    document = render_graph_html(endpoints, edges, runtime_overlay=overlay)
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    configured = os.environ.get("SYSTEMLENS_CHROME_BIN")
    executable = configured or (str(chrome) if chrome.is_file() else None)
    with playwright_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True, executable_path=executable)
        except playwright_api.Error as error:
            pytest.skip(f"No Chromium browser is available: {error}")
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.set_default_timeout(5_000)
        page.set_content(document, wait_until="load")
        page.wait_for_function(
            "() => document.querySelectorAll('#graph-port-paths .graph-architecture-path').length >= 4"
        )
        page.wait_for_function(
            "() => document.getElementById('layout-status')?.textContent.startsWith('Disposition active')"
        )
        page.wait_for_timeout(400)
        card_boxes = """() => [...document.querySelectorAll('.graph-node-card-label')]
            .map(card => [card.dataset.nodeId, Math.round(card.getBoundingClientRect().x),
                          Math.round(card.getBoundingClientRect().y)]).sort()"""
        # The overlay starts enabled because the export carries observations.
        page.wait_for_function(
            "() => document.querySelectorAll('.graph-architecture-path.is-runtime-observed').length > 0"
        )
        # Opening the legend reserves its footprint and refits once (ADR-63):
        # sample the cards only after that refit has settled.
        page.locator("#graph-legend summary").first.click()
        before = page.evaluate(card_boxes)
        for _ in range(10):
            page.wait_for_timeout(300)
            settled = page.evaluate(card_boxes)
            if settled == before:
                break
            before = settled
        assert page.locator("#graph-runtime-toggle").is_checked()
        page.locator("#graph-runtime-toggle").uncheck()
        page.wait_for_function("() => !document.querySelector('.graph-node-runtime-badge')")
        assert page.evaluate(card_boxes) == before
        page.locator("#graph-runtime-toggle").check()
        page.wait_for_function(
            "() => document.querySelectorAll('.graph-architecture-path.is-runtime-observed').length > 0"
        )

        assert page.evaluate(card_boxes) == before
        assert page.locator(".graph-architecture-path.is-runtime-silent").count() >= 1
        arc_labels = page.locator("#graph-port-paths .graph-runtime-link-label").all_text_contents()
        assert "aucun trafic" in arc_labels
        assert any(label.endswith("msg") or "appel" in label for label in arc_labels)
        inventory = page.locator(
            '.graph-node-card-label[data-node-id="microservice:inventory-service"] .graph-node-runtime-badge'
        )
        assert "entrée" in inventory.inner_text() and "active" in inventory.inner_text()
        assert "hors index" in (inventory.get_attribute("title") or "")
        assert "rapprochée" in page.locator(".legend-runtime-summary").inner_text()
        billing = page.locator(
            '.graph-node-card-label[data-node-id="microservice:billing-service"] .graph-node-runtime-badge'
        )
        assert billing.inner_text() == "Sans télémétrie APM"
        assert "2026-10-24 → 2026-10-31" in page.locator(".legend-runtime-window").inner_text()

        page.locator("#graph-runtime-gaps").click()
        page.locator("#inspector-modal").wait_for(state="visible")
        gaps = page.locator("#inspector-body").inner_text()
        assert "kafka/audit.events" in gaps
        assert "payments.example.com:443" in gaps
        assert "legacy-gateway" in gaps
        assert "INDEXÉES SANS TRAFIC SUR LA FENÊTRE" in gaps.upper()
        page.locator("#inspector-close").click()

        page.locator("#graph-runtime-toggle").uncheck()
        page.wait_for_function(
            "() => !document.querySelector('.graph-node-runtime-badge, .is-runtime-observed, .is-runtime-silent')"
        )
        assert page.evaluate(card_boxes) == before
        browser.close()


def _call_tree_document() -> str:
    def kafka(role: str, topic: str, module: str) -> MessageEndpoint:
        return MessageEndpoint(
            id=f"{module}:{role}:{topic}", role=role, system="kafka", topic=topic,
            topic_dynamic=False, source="code", framework="spring-kafka",
            path=f"{module}/Handler.java", start_line=42, end_line=42, snippet="", module=module,
        )

    orders_publish = kafka("produce", "orders.created", "orders")
    payments_consume = kafka("consume", "orders.created", "payments")
    payments_publish = kafka("produce", "payments.completed", "payments")
    inventory_consume = kafka("consume", "payments.completed", "inventory")
    endpoints = {
        "orders": [orders_publish],
        "payments": [payments_consume, payments_publish],
        "inventory": [inventory_consume],
    }
    edges = [
        GraphEdge("kafka", "orders", "payments", orders_publish, payments_consume),
        GraphEdge("kafka", "payments", "inventory", payments_publish, inventory_consume),
    ]
    path = "payments/src/main/java/com/example/payments/PaymentHandler.java"
    flow = CodeFlow(
        id="flow-payments", module="payments", method="com.example.payments.PaymentHandler.handle",
        path=path, start_line=42, end_line=73, status="potential", confidence="medium",
        reason="Entry and publication in the same method.",
        steps=(
            CodeFlowStep(order=1, kind="message_entry", name="orders.created", path=path,
                         start_line=42, end_line=42, endpoint_id=payments_consume.id),
            CodeFlowStep(order=2, kind="message_publish", name="payments.completed", path=path,
                         start_line=68, end_line=68, endpoint_id=payments_publish.id),
            CodeFlowStep(order=3, kind="message_entry", name="payments.completed",
                         path="inventory/Handler.java", start_line=42, end_line=42,
                         endpoint_id=inventory_consume.id),
        ),
    )
    observations = [
        RuntimeObservation(
            id="payments-in", source="elastic_apm", origin="https://es:9200", kind="transaction",
            service_name="payments", target="orders.created process", target_type="messaging",
            calls=40, failures=10, duration_sum_us=None, window_start="2026-10-24T12:00:00Z",
            window_end="2026-10-31T12:00:00Z", collected_at="2026-10-31T12:00:00Z",
        ),
        _observation("destination", "payments", "kafka/payments.completed", "kafka", 38),
        _observation("destination", "inventory", "postgresql", "postgresql", 12),
        _observation("transaction", "inventory", "inventory.audit process", "messaging", 5),
    ]
    errors = [RuntimeErrorGroup(
        id="e1", source="elastic_apm", origin="https://es:9200", service_name="payments",
        grouping_key="g1", exception_type="PaymentDeclinedException",
        culprit="PaymentHandler in handle", transaction_name="orders.created process",
        occurrences=3, last_seen="2026-10-31T11:58:00.000Z",
        window_start="2026-10-24T12:00:00Z", window_end="2026-10-31T12:00:00Z",
        collected_at="2026-10-31T12:00:00Z",
    )]
    overlay = runtime_overlay(
        reconcile_observations(observations, endpoints, edges, errors=errors), endpoints,
    )
    return render_graph_html(endpoints, edges, code_flows=[flow], runtime_overlay=overlay)


@pytest.mark.slow
@pytest.mark.integration
def test_runtime_overlay_annotates_the_selected_call_tree() -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")
    document = _call_tree_document()
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    executable = os.environ.get("SYSTEMLENS_CHROME_BIN") or (str(chrome) if chrome.is_file() else None)
    with playwright_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True, executable_path=executable)
        except playwright_api.Error as error:
            pytest.skip(f"No Chromium browser is available: {error}")
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.set_default_timeout(5_000)
        page.set_content(document, wait_until="load")
        page.locator("#flows-tab").click()
        page.locator("#code-flows .code-flow-item").first.click()
        page.locator("#graph-call-tree .graph-call-tree-node").first.wait_for()
        page.locator("#flow-mode-expand-all").click()
        page.locator(".graph-call-tree-runtime-badge").first.wait_for()
        assert page.locator("#flow-runtime-toggle").get_attribute("aria-pressed") == "true"

        badges = page.locator(".graph-call-tree-runtime-badge")
        assert badges.all_inner_texts() == ["APM · 40 appels · 25 % d’échecs · 3 erreurs", "APM · aucun trafic"]
        assert "is-failing" in (badges.first.get_attribute("class") or "")
        assert "PaymentDeclinedException × 3" in (badges.first.get_attribute("title") or "")
        edge = page.locator("#graph-call-tree .graph-call-tree-edge.is-kafka").first
        assert "is-runtime-observed" in (edge.get_attribute("class") or "")
        title = edge.get_attribute("title") or ""
        assert "APM · émis : 38 appels (tous consommateurs du topic)" in title
        assert "APM · reçu : Aucun trafic observé sur la fenêtre" in title
        assert page.locator("#graph-runtime-toggle").is_checked()
        edge_label = page.locator("#graph-call-tree .graph-call-tree-edge-label").first.text_content() or ""
        assert "APM · 38 msg" in edge_label

        page.locator("#flow-runtime-toggle").click()
        page.wait_for_function("() => !document.querySelector('.graph-call-tree-runtime-badge')")
        assert page.locator("#flow-runtime-toggle").get_attribute("aria-pressed") == "false"
        browser.close()


def _collection(source: str, end: str, interval: str | None = "60m", **extra: object) -> RuntimeCollection:
    values: dict[str, object] = {
        "id": f"{source}:{end}:{interval}", "source": source, "origin": "https://es:9200",
        "window_start": "2026-10-24T12:00:00Z", "window_end": end, "interval": interval,
        "step": None, "collected_at": end, "page_counts": {},
    }
    values.update(extra)
    return RuntimeCollection(**values)  # type: ignore[arg-type]


def test_collection_history_keeps_three_per_source_and_prunes_atomically(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        for day in range(4):
            end = f"2026-10-{28 + day}T12:00:00Z"
            collection = _collection("elastic_apm", end)
            row = RuntimeObservation(**{
                **_observation("transaction", "orders", "GET /a", "request", day).__dict__,
                "id": f"row-{day}", "collection_id": collection.id, "window_end": end,
            })
            with store.transaction():
                store.add_runtime_collection(collection, [row], [])
        history = store.all_runtime_collections()
        assert [item.window_end for item in history] == [
            "2026-10-31T12:00:00Z", "2026-10-30T12:00:00Z", "2026-10-29T12:00:00Z",
        ]
        assert [item.calls for item in store.all_runtime_observations()] == [3]
        assert [item.calls for item in store.all_runtime_observations(history[-1].id)] == [1]
        remaining = store.conn.execute("SELECT COUNT(*) AS c FROM runtime_observations").fetchone()
        assert remaining["c"] == 3


def test_rows_without_collection_are_reported_until_the_next_collection(tmp_path: Path) -> None:
    legacy = _observation("transaction", "orders", "GET /a", "request", 7)
    with Store(tmp_path) as store:
        store.conn.execute(
            "INSERT INTO runtime_observations (id, source, origin, kind, service_name, target,"
            " target_type, calls, failures, duration_sum_us, window_start, window_end, collected_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (legacy.id, legacy.source, legacy.origin, legacy.kind, legacy.service_name, legacy.target,
             legacy.target_type, legacy.calls, legacy.failures, None, legacy.window_start,
             legacy.window_end, legacy.collected_at),
        )
        store.conn.commit()
        assert store.all_runtime_collections() == []
        assert [(item.calls, item.duration_p95_us, item.collection_id) for item in store.all_runtime_observations()] == [
            (7, None, None),
        ]
        collection = _collection("elastic_apm", "2026-11-01T12:00:00Z")
        with store.transaction():
            store.add_runtime_collection(collection, [RuntimeObservation(**{
                **legacy.__dict__, "id": "new", "calls": 8, "collection_id": collection.id,
            })], [])
        assert [item.calls for item in store.all_runtime_observations()] == [8]


def test_compare_previous_needs_the_same_source_and_interval() -> None:
    endpoints, edges = _lab_inventory()
    current = _collection("elastic_apm", "2026-10-31T12:00:00Z")
    rows = [RuntimeObservation(**{
        **_observation("transaction", "order-service", "POST /api/orders", "request", 12).__dict__,
        "failures": 3, "duration_p95_us": 500.0, "collection_id": current.id,
    })]
    result = reconcile_observations(rows, endpoints, edges, collections=[current])

    only_one = compare_with_previous(result, [current], {})
    assert only_one["elastic_apm"]["reason"] == (
        "aucune collecte antérieure de même source et de même intervalle"
    )
    other_interval = _collection("elastic_apm", "2026-10-30T12:00:00Z", interval="1m")
    assert compare_with_previous(result, [current, other_interval], {})["elastic_apm"]["reason"] == (
        "les collectes antérieures ont un autre intervalle"
    )
    previous = _collection("elastic_apm", "2026-10-30T12:00:00Z")
    earlier = RuntimeObservation(**{
        **rows[0].__dict__, "id": "old", "calls": 10, "failures": 1, "duration_p95_us": 400.0,
        "collection_id": previous.id,
    })
    comparison = compare_with_previous(result, [current, previous], {previous.id: [earlier]})
    [delta] = comparison["elastic_apm"]["rows"]
    assert delta["calls_delta"] == 2
    assert delta["failure_ratio_delta"] == pytest.approx(0.25 - 0.1)
    assert delta["p95_delta_us"] == 100.0
    assert "p95 +100 µs" in render_runtime_report_text({**result, "comparison": comparison})


def test_hotspot_ranking_is_deterministic_and_lists_unattributed_rows_apart() -> None:
    endpoints, edges = _lab_inventory()
    collection = _collection("elastic_apm", "2026-10-31T12:00:00Z", overflow_services=("inventory-service",))

    def row(service: str, target: str, kind: str, p95: float | None, failures: int = 0) -> RuntimeObservation:
        return RuntimeObservation(**{
            **_observation("transaction", service, target, kind, 20).__dict__,
            "failures": failures, "duration_p95_us": p95, "collection_id": collection.id,
        })

    observations = [
        row("order-service", "POST /api/orders", "request", 300.0, 2),
        row("inventory-service", "GET /api/products/{id}", "request", 300.0),
        row("inventory-service", "POST /api/reservations", "request", None, 5),
        row("legacy-gateway", "GET /", "request", 900.0),
        row("inventory-service", "postgresql", "db", 50.0),
    ]
    error = RuntimeErrorGroup(
        id="e1", source="elastic_apm", origin="https://es:9200", service_name="order-service",
        grouping_key="g1", exception_type="OutOfStockException", culprit=None,
        transaction_name="POST /api/orders", occurrences=4, last_seen=None,
        window_start="2026-10-24T12:00:00Z", window_end="2026-10-31T12:00:00Z",
        collected_at="2026-10-31T12:00:00Z",
    )
    result = reconcile_observations(observations, endpoints, edges, errors=[error], collections=[collection])

    by_p95 = rank_hotspots(result, "p95", 5)
    assert [(item["service"], item["target"], item["completeness"]) for item in by_p95["rows"]] == [
        ("inventory-service", "GET /api/products/{id}", "incomplete"),
        ("order-service", "POST /api/orders", "complete"),
    ]
    assert by_p95["rows"][0]["endpoint_ids"] == ["inventory-service:rest:serve:GET /api/products/{productId}"]
    assert by_p95["rows"][0]["window"] == {"start": "2026-10-24T12:00:00Z", "end": "2026-10-31T12:00:00Z"}
    assert by_p95["without_metric"] == 1
    assert [item["target"] for item in by_p95["not_compared"]] == ["GET /", "postgresql"]
    assert "supermarket.order.placed" in {item["name"] for item in by_p95["unverified"]}
    assert [(item["target"], item["value"]) for item in rank_hotspots(result, "failures", 1)["rows"]] == [
        ("POST /api/reservations", 5.0),
    ]
    assert [(item["target"], item["value"]) for item in rank_hotspots(result, "errors", 1)["rows"]] == [
        ("POST /api/orders", 4.0),
    ]
    with pytest.raises(ValueError, match="--by"):
        rank_hotspots(result, "latency", 1)
    overlay = runtime_overlay(result, endpoints)
    assert overlay is not None and overlay["hotspots"]["by"] == "p95"
    assert [item["target"] for item in overlay["hotspots"]["rows"]] == [
        "GET /api/products/{id}", "POST /api/orders",
    ]
    text = render_runtime_report_text({**result, "hotspots": by_p95})
    assert " 1. inventory-service GET /api/products/{id} · p95 300 µs · 20 appels · matched · incomplet" in text


class FakePrometheus:
    """Answer instant queries by metric name with the lab's OTel labels."""

    def __init__(self, series: dict[str, list[tuple[dict[str, str], float]]]) -> None:
        self.series = series
        self.queries: list[str] = []

    def __call__(self, expression: str, time: str) -> dict[str, object]:
        self.queries.append(expression)
        match = re.search(r"(?:increase|max)\(([a-z_]+)", expression)
        name = match.group(1) if match else ""
        quantile = re.match(r"histogram_quantile\(([\d.]+)", expression)
        rows = self.series.get(name, [])
        if quantile:
            factor = {"0.5": 1.0, "0.95": 2.0, "0.99": 3.0}[quantile.group(1)]
            rows = [(labels, value * factor) for labels, value in rows]
        return {"status": "success", "data": {"resultType": "vector", "result": [
            {"metric": labels, "value": [1761912000, str(value)]} for labels, value in rows
        ]}}


def _lab_prometheus() -> FakePrometheus:
    orders = {"service_name": "order-service"}
    inventory = {"job": "inventory-service"}
    return FakePrometheus({
        "http_server_request_duration_seconds_count": [
            ({**orders, "http_request_method": "POST", "http_route": "/api/orders", "http_response_status_code": "201"}, 30),
            ({**orders, "http_request_method": "POST", "http_route": "/api/orders", "http_response_status_code": "500"}, 3),
            ({**inventory, "http_request_method": "GET", "http_route": "/api/products/{productId}", "http_response_status_code": "200"}, 12),
            ({**inventory, "http_request_method": "GET", "http_response_status_code": "200"}, 100),
        ],
        "http_server_request_duration_seconds_bucket": [
            ({**orders, "http_request_method": "POST", "http_route": "/api/orders"}, 0.2),
        ],
        "http_client_request_duration_seconds_count": [
            ({**orders, "server_address": "inventory-service", "server_port": "8080", "http_response_status_code": "201"}, 30),
            ({**orders, "server_address": "payments.example.com", "server_port": "443", "http_response_status_code": "404"}, 2),
        ],
        "http_client_request_duration_seconds_sum": [
            ({**orders, "server_address": "inventory-service", "server_port": "8080", "http_response_status_code": "201"}, 6.0),
        ],
        "messaging_client_operation_duration_seconds_count": [
            ({**orders, "messaging_destination_name": "supermarket.order.placed", "messaging_operation_type": "publish", "messaging_system": "kafka"}, 30),
            ({"service_name": "supermarket-restock-service", "messaging_destination_name": "supermarket.stock.depleted", "messaging_operation_type": "process", "messaging_system": "kafka"}, 4),
        ],
        "prometheus_target_interval_length_seconds": [({"quantile": "0.5"}, 15.0)],
    })


def test_prometheus_collection_maps_otel_conventions_and_reconciles_like_elastic() -> None:
    query = _lab_prometheus()

    result = prometheus_otel.collect(query, origin="http://prom:9090", window=timedelta(days=7), now=NOW)

    assert all("[7d]" in expression for expression in query.queries if "increase(" in expression)
    rows = {(item.kind, item.service_name, item.target): item for item in result.observations}
    orders = rows[("transaction", "order-service", "POST /api/orders")]
    assert (orders.calls, orders.failures) == (33, 3)
    assert (orders.duration_p50_us, orders.duration_p95_us, orders.duration_p99_us) == pytest.approx(
        (200_000.0, 400_000.0, 600_000.0),
    )
    assert rows[("transaction", "inventory-service", "GET")].calls == 100
    destination = rows[("destination", "order-service", "inventory-service:8080")]
    assert (destination.target_type, destination.duration_sum_us, destination.duration_p95_us) == (
        "http", 6_000_000.0, None,
    )
    assert rows[("destination", "order-service", "payments.example.com:443")].failures == 2
    assert rows[("destination", "order-service", "kafka/supermarket.order.placed")].target_type == "kafka"
    assert rows[("transaction", "supermarket-restock-service", "supermarket.stock.depleted process")].target_type == "messaging"
    assert result.collection.source == "prometheus"
    assert (result.collection.interval, result.collection.step) == ("7d", "15s")
    assert any("sans étiquette http_route" in note for note in result.collection.notes)
    assert result.errors == []

    endpoints, edges = _lab_inventory()
    elastic = reconcile_observations(_lab_observations(), endpoints, edges)
    prometheus = reconcile_observations(result.observations, endpoints, edges, collections=[result.collection])
    statuses = {(row["service_name"], row["target"]): row["status"] for row in prometheus["observations"]}
    for key in (
        ("order-service", "POST /api/orders"), ("order-service", "inventory-service:8080"),
        ("order-service", "payments.example.com:443"), ("order-service", "kafka/supermarket.order.placed"),
        ("supermarket-restock-service", "supermarket.stock.depleted process"),
    ):
        assert statuses[key] == {
            (row["service_name"], row["target"]): row["status"] for row in elastic["observations"]
        }[key]
    assert statuses[("inventory-service", "GET")] == "not_compared"
    assert prometheus["window"]["sources"] == ["prometheus"]
    assert "Prometheus (OpenTelemetry) http://prom:9090" in render_runtime_report_text(prometheus)
    assert "pas de collecte 15s" in render_runtime_report_text(prometheus)


def test_prometheus_rejects_credentials_in_the_url_and_keeps_the_token_out_of_the_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_config(tmp_path)
    monkeypatch.setenv("SYSTEMLENS_PROMETHEUS_TOKEN", "secret-token")
    with pytest.raises(RuntimeError, match="identifiants"):
        collect_prometheus_observations(tmp_path, "http://user:pw@prom:9090", query=_lab_prometheus(), now=NOW)
    with pytest.raises(RuntimeError, match="http://"):
        collect_prometheus_observations(tmp_path, "ftp://prom", query=_lab_prometheus(), now=NOW)

    result = collect_prometheus_observations(tmp_path, "http://prom:9090/", query=_lab_prometheus(), now=NOW)

    assert len(result.observations) == 7
    with Store(tmp_path, readonly=True) as store:
        assert {item.source for item in store.all_runtime_observations()} == {"prometheus"}
        assert "secret-token" not in (tmp_path / ".systemlens" / "findings.db").read_bytes().decode("latin-1")
    source = PrometheusSource(_lab_prometheus(), "http://prom:9090")
    assert source.name == "prometheus"


def test_observations_of_two_sources_coexist_and_the_report_names_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_config(tmp_path)
    collect_runtime_observations(tmp_path, "https://es.example:9200", search=FakeElasticsearch({
        "transaction": [[_transaction("order-service", "request", "POST /api/orders", "success", 9)]],
    }), now=NOW)
    collect_prometheus_observations(tmp_path, "http://prom:9090", query=_lab_prometheus(), now=NOW)
    endpoints, edges = _lab_inventory()
    monkeypatch.setattr(cli, "load_architecture_inventory", lambda _root: SimpleNamespace(
        endpoints_by_service=endpoints, relations=[],
    ))
    monkeypatch.setattr(cli, "graph_edges_from_relations", lambda _relations, _endpoints: edges)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["observe", "report", "--json"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["window"]["sources"] == ["elastic_apm", "prometheus"]
    assert [item["source"] for item in report["window"]["collections"]] == ["elastic_apm", "prometheus"]
    assert {row["source"] for row in report["observations"]} == {"elastic_apm", "prometheus"}
    text = CliRunner().invoke(app, ["observe", "report"]).output
    assert "Complétude : Elastic APM" in text and "Complétude : Prometheus (OpenTelemetry)" in text
    with Store(tmp_path, readonly=True) as store:
        assert [item.source for item in store.latest_runtime_collections()] == ["elastic_apm", "prometheus"]
