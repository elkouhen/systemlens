from pathlib import Path

from systemlens.conventions.strategy1.indexing import is_openapi_declaration_path
from systemlens.conventions.strategy1.kafka import apply_kafka_endpoints
from systemlens.conventions.strategy1.layers import classify_module
from systemlens.conventions.strategy1.profile import is_enabled
from systemlens.conventions.strategy1.rest import rest_target_service_hint
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule


def _module(name: str, path: Path) -> DiscoveredModule:
    return DiscoveredModule(
        name=name, path=path, build_system="maven", kind="library", version=None,
        configuration_example=None,
        starts_application=False,
    )


def test_strategy1_pack_keeps_repository_rules_out_of_the_default_profile(tmp_path: Path) -> None:
    assert is_enabled("strategy1") is True
    assert is_enabled("default") is False
    assert is_openapi_declaration_path("orders/src/main/resources/openapi/orders.rest")
    assert not is_openapi_declaration_path("orders/src/test/resources/openapi/orders.rest")
    module = _module("domain-orders", tmp_path / "DOMAIN" / "domain-orders")
    assert classify_module(module, tmp_path) == "domain"

    endpoint = MessageEndpoint(
        id="call", role="call", system="rest", topic="GET /orders",
        topic_dynamic=False, source="code", framework="rest-template",
        path="Client.java", start_line=1, end_line=1,
        snippet="client.getOrdersServiceUrl()",
    )
    assert rest_target_service_hint(endpoint) == "orders-service"


def test_strategy1_replacement_keeps_an_ast_payload_type() -> None:
    generic = MessageEndpoint(
        id="generic", role="produce", system="kafka", topic="dynamic",
        topic_dynamic=True, source="code", framework="spring-kafka",
        path="Publisher.java", start_line=7, end_line=7, snippet="send(...)",
        message_type="OrderCreated",
    )
    strategy = MessageEndpoint(
        id="strategy", role="produce", system="kafka", topic="ORDERS_CREATED",
        topic_dynamic=False, source="code", framework="kafka-topic-strategy1",
        path="Publisher.java", start_line=7, end_line=7,
        snippet="envoyerMessageKafka(...)",
    )

    result = apply_kafka_endpoints([generic], [strategy])

    assert result[0].message_type == "OrderCreated"
