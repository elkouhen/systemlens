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
    collect_runtime_observations,
    parse_service_map,
    reconcile_observations,
    render_runtime_report_text,
    runtime_overlay,
)
from systemlens.delivery import cli
from systemlens.delivery.cli import app
from systemlens.discovery.elastic_apm import (
    ElasticApmError,
    cluster_origin,
    collect_observations,
    parse_window,
)
from systemlens.domain.code_flows import CodeFlow, CodeFlowStep
from systemlens.domain.graph import GraphEdge
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.runtime import RuntimeObservation
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

    configured = load_config(tmp_path).runtime_service_map

    assert configured == {"prod-orders": "orders", "prod-stock": "stock"}
    assert parse_service_map(["prod-stock=inventory"], configured) == {
        "prod-orders": "orders", "prod-stock": "inventory",
    }
    raw["runtime"] = {"service_map": {"prod-orders": 3}}
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match="runtime.service_map"):
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
    raw["runtime"] = {"service_map": {"supermarket-restock-service": "restock-service"}}
    path.write_text(yaml.safe_dump(raw))
    overlay = cli._export_runtime_overlay(tmp_path, graph_data)  # type: ignore[arg-type]
    assert overlay is not None and overlay["services"]["restock-service"] == "observed"
    assert cli._export_runtime_overlay(
        tmp_path, SimpleNamespace(runtime_observations=[]),  # type: ignore[arg-type]
    ) is None


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
            "() => document.getElementById('layout-status')?.textContent.endsWith('actif.')"
        )
        page.wait_for_timeout(400)
        card_boxes = """() => [...document.querySelectorAll('.graph-node-card-label')]
            .map(card => [card.dataset.nodeId, Math.round(card.getBoundingClientRect().x),
                          Math.round(card.getBoundingClientRect().y)]).sort()"""
        before = page.evaluate(card_boxes)
        assert page.locator(".graph-node-runtime-badge").count() == 0
        assert page.locator(".is-runtime-observed, .is-runtime-silent").count() == 0

        page.locator("#graph-legend summary").click()
        page.locator("#graph-runtime-toggle").check()
        page.wait_for_function(
            "() => document.querySelectorAll('.graph-architecture-path.is-runtime-observed').length > 0"
        )

        assert page.evaluate(card_boxes) == before
        assert page.locator(".graph-architecture-path.is-runtime-silent").count() >= 1
        inventory = page.locator(
            '.graph-node-card-label[data-node-id="microservice:inventory-service"] .graph-node-runtime-badge'
        )
        assert "1 écart" in inventory.inner_text()
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
    overlay = runtime_overlay(reconcile_observations(observations, endpoints, edges), endpoints)
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
        assert page.locator(".graph-call-tree-runtime-badge").count() == 0

        page.locator("#flow-runtime-toggle").click()
        page.locator(".graph-call-tree-runtime-badge").first.wait_for()

        badges = page.locator(".graph-call-tree-runtime-badge")
        assert badges.all_inner_texts() == ["APM · 40 appels · 25 % d’échecs", "APM · aucun trafic"]
        assert "is-failing" in (badges.first.get_attribute("class") or "")
        edge = page.locator("#graph-call-tree .graph-call-tree-edge.is-kafka").first
        assert "is-runtime-observed" in (edge.get_attribute("class") or "")
        title = edge.get_attribute("title") or ""
        assert "APM · émis : 38 appels (tous consommateurs du topic)" in title
        assert "APM · reçu : Aucun trafic observé sur la fenêtre" in title
        assert page.locator("#graph-runtime-toggle").is_checked()

        page.locator("#flow-runtime-toggle").click()
        page.wait_for_function("() => !document.querySelector('.graph-call-tree-runtime-badge')")
        assert page.locator("#flow-runtime-toggle").get_attribute("aria-pressed") == "false"
        browser.close()
