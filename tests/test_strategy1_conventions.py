from pathlib import Path

from systemlens.conventions.strategy1.indexing import is_openapi_declaration_path
from systemlens.conventions.strategy1.kafka import apply_kafka_endpoints, infer_kafka_endpoints
from systemlens.conventions.strategy1.layers import classify_module
from systemlens.conventions.strategy1.profile import is_enabled
from systemlens.conventions.strategy1.rest import rest_target_service_hint
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule
from systemlens.scanner import infer_kafka_endpoints as infer_indexed_kafka_endpoints


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

    assert result[0].topic == "ORDERS_CREATED"
    assert result[0].topic_display == "ORDERS_CREATED"
    assert result[0].message_type == "OrderCreated"


def test_strategy1_matches_java_topic_accessor_to_normalized_kafka_yaml_key(tmp_path: Path) -> None:
    (tmp_path / "kafka.yml").write_text(
        "topics:\n  FLUX_11:\n    nom: flux11aemettre\n",
        encoding="utf-8",
    )
    source = tmp_path / "src/main/java/com/example/Publisher.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """class Publisher {
  void publish(OrderCreated event) {
    kafkaService.envoyerMessageKafka(kafkaProperties.getTopics().getFlux11(), event);
  }
}
record OrderCreated(String id) {}
""",
        encoding="utf-8",
    )

    endpoints = infer_kafka_endpoints(
        tmp_path,
        ["src/main/java/com/example/Publisher.java"],
    )

    assert [endpoint.topic for endpoint in endpoints] == ["flux11aemettre"]
    assert [endpoint.topic_dynamic for endpoint in endpoints] == [False]
    assert [endpoint.topic_display for endpoint in endpoints] == ["flux11aemettre"]


def test_strategy1_uses_declared_topic_name_from_kafka_yaml(tmp_path: Path) -> None:
    (tmp_path / "kafka.yml").write_text(
        "topics:\n  OrdersCreated:\n    nom: ${kafka.prefix-topic}.commerce.orders.created\n",
        encoding="utf-8",
    )
    source = tmp_path / "src/main/java/com/example/Publisher.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """import org.springframework.kafka.annotation.KafkaListener;
class Publisher {
  void publish(OrderCreated event) {
    kafkaService.envoyerMessageKafka(kafkaProperties.getTopics().getOrdersCreated(), event);
  }
  @KafkaListener(topics = "${kafka.topics.OrdersCreated.nom}")
  void consume(OrderCreated event) {}
}
record OrderCreated(String id) {}
""",
        encoding="utf-8",
    )

    endpoints = apply_kafka_endpoints([], infer_kafka_endpoints(tmp_path))

    assert sorted((endpoint.role, endpoint.topic) for endpoint in endpoints) == [
        ("consume", "commerce.orders.created"),
        ("produce", "commerce.orders.created"),
    ]


def test_strategy1_does_not_fabricate_topics_missing_from_kafka_yaml(tmp_path: Path) -> None:
    (tmp_path / "kafka.yml").write_text(
        "topics:\n  OrdersCreated:\n    nom: commerce.orders.created\n",
        encoding="utf-8",
    )
    source = tmp_path / "src/main/java/Publisher.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """class Publisher {
  void publish(OrderCreated event) {
    kafkaService.envoyerMessageKafka(kafkaProperties.getTopics().getUnknown(), event);
  }
}
record OrderCreated(String id) {}
""",
        encoding="utf-8",
    )

    endpoints = infer_kafka_endpoints(tmp_path)

    assert [(endpoint.topic, endpoint.topic_dynamic) for endpoint in endpoints] == [
        ("<dynamic>", True)
    ]


def test_strategy1_validates_resolved_dynamic_topic_after_prefix_removal(tmp_path: Path) -> None:
    (tmp_path / "kafka.yml").write_text(
        "topics:\n  OrdersCreated:\n    nom: ${kafka.prefix-topic}.commerce.orders.created\n",
        encoding="utf-8",
    )
    source = tmp_path / "src/main/java/Publisher.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """import org.springframework.beans.factory.annotation.Value;
class Publisher {
  @Value("${kafka.topics.OrdersCreated.nom}")
  private String topic;
  void publish(OrderCreated event) {
    kafkaService.envoyerMessageKafka(topic, event);
  }
}
record OrderCreated(String id) {}
""",
        encoding="utf-8",
    )

    endpoints = infer_kafka_endpoints(tmp_path)

    assert [(endpoint.topic, endpoint.topic_dynamic) for endpoint in endpoints] == [
        ("commerce.orders.created", False)
    ]


def test_strategy1_maps_literal_send_topic_key_from_kafka_yaml(tmp_path: Path) -> None:
    (tmp_path / "kafka.yml").write_text(
        "topics:\n  OrdersCreated:\n    nom: commerce.orders.created\n",
        encoding="utf-8",
    )
    source = tmp_path / "src/main/java/Publisher.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """class Publisher {
  void publish(OrderCreated event) {
    kafkaService.envoyerMessageKafka("OrdersCreated", event);
  }
}
record OrderCreated(String id) {}
""",
        encoding="utf-8",
    )

    endpoints = infer_kafka_endpoints(tmp_path)

    assert [(endpoint.topic, endpoint.topic_dynamic) for endpoint in endpoints] == [
        ("commerce.orders.created", False)
    ]


def test_strategy1_maps_generic_send_topic_key_from_kafka_yaml(tmp_path: Path) -> None:
    (tmp_path / "kafka.yml").write_text(
        "topics:\n  OrdersCreated:\n    nom: commerce.orders.created\n",
        encoding="utf-8",
    )
    source = tmp_path / "src/main/java/Publisher.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """import org.springframework.kafka.core.KafkaTemplate;
class Publisher {
  private KafkaTemplate<String, OrderCreated> template;
  void publish(OrderCreated event) {
    template.send("OrdersCreated", event);
  }
}
record OrderCreated(String id) {}
""",
        encoding="utf-8",
    )

    endpoints = infer_indexed_kafka_endpoints(tmp_path, strategy1=True)

    assert [(endpoint.topic, endpoint.topic_dynamic) for endpoint in endpoints] == [
        ("commerce.orders.created", False)
    ]
