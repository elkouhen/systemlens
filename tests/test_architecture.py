from pathlib import Path

import systemlens.application.architecture as architecture
from systemlens.application.architecture import analyze, build_catalog
from systemlens.domain.models import MessageEndpoint, compute_endpoint_id
from systemlens.domain.module_inventory import DiscoveredModule
from systemlens.indexing.relations import build_architecture_relations


def _module(name: str) -> DiscoveredModule:
    return DiscoveredModule(
        name=name,
        path=Path("/workspace") / name,
        build_system="maven",
        version=None,
        kind="microservice",
        starts_application=True,
        configuration_example="",
    )


def _endpoint(
    module: str,
    role: str,
    topic: str,
    path: str,
    snippet: str = "",
    system: str = "rest",
) -> MessageEndpoint:
    return MessageEndpoint(
        id=compute_endpoint_id(role, topic, path, 1),
        role=role,
        system=system,
        topic=topic,
        topic_dynamic=False,
        source="code",
        framework="spring",
        path=path,
        start_line=1,
        end_line=1,
        snippet=snippet,
        module=module,
    )


def test_impact_returns_transitive_rest_paths_from_provider_to_callers() -> None:
    endpoints = [
        _endpoint("gateway", "call", "GET /orders", "gateway/Client.java", "http://orders"),
        _endpoint("orders", "serve", "GET /orders", "orders/Controller.java"),
        _endpoint("orders", "call", "GET /payments", "orders/Client.java", "http://payments"),
        _endpoint("payments", "serve", "GET /payments", "payments/Controller.java"),
    ]
    modules = [_module("gateway"), _module("orders"), _module("payments")]
    relations = build_architecture_relations(modules, endpoints, [])
    catalog = build_catalog(modules, endpoints, relations)

    result = analyze(catalog, "impact", "payments")

    assert result is not None
    assert [path["nodes"][-1]["name"] for path in result["paths"]] == [
        "orders",
        "gateway",
    ]
    assert result["paths"][1]["nodes"] == [
        {"kind": "microservice", "name": "payments"},
        {"kind": "microservice", "name": "orders"},
        {"kind": "microservice", "name": "gateway"},
    ]


def test_impact_returns_kafka_paths_from_producer_to_consumers() -> None:
    endpoints = [
        _endpoint("orders", "produce", "orders.created", "orders/Producer.java", system="kafka"),
        _endpoint("billing", "consume", "orders.created", "billing/Consumer.java", system="kafka"),
        _endpoint("shipping", "consume", "orders.created", "shipping/Consumer.java", system="kafka"),
    ]
    modules = [_module("orders"), _module("billing"), _module("shipping")]
    relations = build_architecture_relations(modules, endpoints, [])
    catalog = build_catalog(modules, endpoints, relations)

    result = analyze(catalog, "impact", "orders")

    assert result is not None
    assert [path["nodes"][-1]["name"] for path in result["paths"]] == [
        "billing",
        "shipping",
    ]
    assert result["paths"][0]["relations"] == [
        {"kind": "kafka", "label": "orders.created"},
    ]
    assert result["paths_truncated"] is False


def test_impact_reports_when_the_result_limit_truncates_paths(monkeypatch) -> None:
    modules = [_module("orders"), _module("billing"), _module("shipping")]
    endpoints = [
        _endpoint("orders", "produce", "orders.created", "orders/Producer.java", system="kafka"),
        _endpoint("billing", "consume", "orders.created", "billing/Consumer.java", system="kafka"),
        _endpoint("shipping", "consume", "orders.created", "shipping/Consumer.java", system="kafka"),
    ]
    relations = build_architecture_relations(modules, endpoints, [])
    catalog = build_catalog(modules, endpoints, relations)
    monkeypatch.setattr(architecture, "_IMPACT_LIMIT", 1)

    result = analyze(catalog, "impact", "orders")

    assert result is not None
    assert len(result["paths"]) == 1
    assert result["paths_truncated"] is True
    assert result["paths_limit"] == 1
