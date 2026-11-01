"""Regression coverage for conservative Kafka extraction rules."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from systemlens.scanner import clear_analysis_caches, infer_kafka_endpoints
from systemlens.scanner.kafka_conventions import infer_kafka_topic_strategy1_endpoints
from systemlens.scanner._core import _message_payload_type


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _facts(endpoints):
    return sorted(
        (e.role, e.topic, e.topic_dynamic, e.message_type, e.framework, e.start_line)
        for e in endpoints
    )


@pytest.fixture(autouse=True)
def _clear():
    clear_analysis_caches()
    yield
    clear_analysis_caches()


# ---------------------------------------------------------------- topic resolution
def test_first_literal_inside_nested_call_is_not_a_topic(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/com/x/P.java", """package com.x;
import org.springframework.kafka.core.KafkaTemplate;
class P {
  private KafkaTemplate<String, Object> template;
  void publish(String region, Object event) {
    template.send(String.format("orders-%s", region), event);
  }
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert [(f[1], f[2]) for f in facts] == [("<dynamic>", True)], facts


def test_subscribe_list_of_keeps_every_topic(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/com/x/C.java", """package com.x;
import java.util.List;
import org.apache.kafka.clients.consumer.KafkaConsumer;
class C {
  void run(KafkaConsumer<String, String> consumer) {
    consumer.subscribe(List.of("orders", "payments"));
  }
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert sorted(f[1] for f in facts) == ["orders", "payments"], facts


def test_subscribe_pattern_stays_dynamic(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/com/x/C.java", """package com.x;
import java.util.regex.Pattern;
import org.apache.kafka.clients.consumer.KafkaConsumer;
class C {
  void run(KafkaConsumer<String, String> consumer) {
    consumer.subscribe(Pattern.compile("orders.*"));
  }
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert all(f[2] is True for f in facts), facts


def test_placeholder_with_suffix_stays_dynamic(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/com/x/L.java", """package com.x;
class L {
  @KafkaListener(topics = "${topic.prefix}.orders")
  void on(Order o) {}
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert facts and all(f[2] is True for f in facts), facts


def test_nested_placeholder_stays_dynamic(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/com/x/L.java", """package com.x;
class L {
  @KafkaListener(topics = "${kafka.${env}.orders}")
  void on(Order o) {}
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert facts and all(f[2] is True for f in facts), facts


def test_spel_expression_stays_dynamic(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/com/x/L.java", """package com.x;
class L {
  @KafkaListener(topics = "#{'${kafka.topics}'.split(',')}")
  void on(Order o) {}
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert facts and all(f[2] is True for f in facts), facts


def test_property_value_with_unresolved_placeholder_is_not_concrete(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/resources/application.yml", "app:\n  topic: ${app.prefix}.orders\n")
    _write(tmp_path, "src/main/java/com/x/L.java", """package com.x;
class L {
  @KafkaListener(topics = "${app.topic}")
  void on(Order o) {}
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    # Either resolved through the nested placeholder or dynamic; never a
    # concrete topic that still contains `${`.
    assert facts and all(("${" not in f[1]) or f[2] for f in facts), facts


def test_property_value_resolves_nested_placeholder(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/resources/application.yml",
           "app:\n  prefix: dev\n  topic: ${app.prefix}.orders\n")
    _write(tmp_path, "src/main/java/com/x/L.java", """package com.x;
class L {
  @KafkaListener(topics = "${app.topic}")
  void on(Order o) {}
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert [(f[1], f[2]) for f in facts] == [("dev.orders", False)], facts


def test_conflicting_profile_values_without_base_stay_dynamic(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/resources/application-dev.yml", "app:\n  topic: orders-dev\n")
    _write(tmp_path, "src/main/resources/application-prod.yml", "app:\n  topic: orders-prod\n")
    _write(tmp_path, "src/main/java/com/x/L.java", """package com.x;
class L {
  @KafkaListener(topics = "${app.topic}")
  void on(Order o) {}
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert facts and all(f[2] is True for f in facts), facts


def test_method_parameter_shadows_value_field_in_other_file(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/resources/application.yml", "app:\n  topic: orders.created\n")
    _write(tmp_path, "src/main/java/com/x/Other.java", """package com.x;
class Other {
  @Value("${app.topic}") private String topic;
}
""")
    _write(tmp_path, "src/main/java/com/x/Wrapper.java", """package com.x;
import org.springframework.kafka.core.KafkaTemplate;
class Wrapper {
  private KafkaTemplate<String, Object> template;
  void send(String topic, Object payload) {
    template.send(topic, payload);
  }
}
""")
    facts = _facts(e for e in infer_kafka_endpoints(tmp_path) if e.path.endswith("Wrapper.java"))
    assert [(f[1], f[2]) for f in facts] == [("<dynamic>", True)], facts


def test_local_variable_shadows_value_field_in_same_file(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/resources/application.yml", "app:\n  topic: orders.created\n")
    _write(tmp_path, "src/main/java/com/x/P.java", """package com.x;
import org.springframework.kafka.core.KafkaTemplate;
class P {
  @Value("${app.topic}") private String topic;
  private KafkaTemplate<String, Object> template;
  void send(Object payload, boolean dlq) {
    String topic = dlq ? "orders.dlq" : computeTopic(payload);
    template.send(topic, payload);
  }
  String computeTopic(Object p) { return "x"; }
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert ("orders.created", False) not in [(f[1], f[2]) for f in facts], facts


def test_comment_inside_topics_array_does_not_add_a_dynamic_endpoint(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/com/x/L.java", """package com.x;
class L {
  @KafkaListener(topics = {"orders", // legacy
                           "payments"})
  void on(Order o) {}
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert sorted((f[1], f[2]) for f in facts) == [("orders", False), ("payments", False)], facts


def test_comment_before_topic_argument(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/com/x/P.java", """package com.x;
import org.springframework.kafka.core.KafkaTemplate;
class P {
  private KafkaTemplate<String, Object> template;
  void publish(Object event) {
    template.send(/* topic */ "orders", event);
  }
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert [(f[1], f[2]) for f in facts] == [("orders", False)], facts


def test_kstream_to_with_lambda_extractor_stays_dynamic(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/com/x/S.java", """package com.x;
import org.apache.kafka.streams.StreamsBuilder;
import org.apache.kafka.streams.kstream.KStream;
class S {
  KStream<String, Order> build(StreamsBuilder builder) {
    KStream<String, Order> s = builder.stream("orders");
    s.to((key, value, ctx) -> value.urgent() ? "urgent" : "normal");
    return s;
  }
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    produce = [f for f in facts if f[0] == "produce"]
    assert produce and all(f[2] is True for f in produce), facts


# ---------------------------------------------------------------- message types
def test_void_streams_builder_method_has_no_message_type(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/com/x/S.java", """package com.x;
import org.apache.kafka.streams.StreamsBuilder;
import org.apache.kafka.streams.kstream.KStream;
class S {
  void buildPipeline(StreamsBuilder builder) {
    KStream<String, Order> s = builder.stream("orders");
    s.to("orders.out");
  }
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert facts and all(f[3] != "void" for f in facts), facts


def test_send_key_is_not_reported_when_payload_is_a_call(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/com/x/P.java", """package com.x;
import org.springframework.kafka.core.KafkaTemplate;
class P {
  private KafkaTemplate<String, Object> template;
  void publish(String orderId, Order order) {
    template.send("orders", orderId, mapper.toEvent(order));
  }
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert [f[3] for f in facts] != ["String"], facts


def test_payload_annotated_parameter_wins_over_key_parameter(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/com/x/L.java", """package com.x;
class L {
  @KafkaListener(topics = "orders")
  void on(@Header(KafkaHeaders.RECEIVED_KEY) String key, @Payload Order order) {}
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert [f[3] for f in facts] == ["Order"], facts


def test_payload_annotated_parameter_after_unannotated_string(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/com/x/L.java", """package com.x;
class L {
  @KafkaListener(topics = "orders")
  void on(String key, @Payload Order order) {}
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert [f[3] for f in facts] == ["Order"], facts


def test_strategy1_get_topics_uses_payload_not_first_parameter(tmp_path: Path) -> None:
    _write(tmp_path, "kafka.yml", "kafka:\n  topics:\n    ordersCreated:\n      nom: orders.created\n")
    _write(tmp_path, "src/main/java/com/x/P.java", """package com.x;
import org.springframework.kafka.core.KafkaTemplate;
class P {
  private KafkaTemplate<String, OrderCreated> kafkaTemplate;
  void publish(String key, OrderCreated event) {
    kafkaTemplate.send(kafkaProperties.getTopics().getOrdersCreated(), key, event);
  }
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path, strategy1=True))
    assert [(f[1], f[3]) for f in facts] == [("orders.created", "OrderCreated")], facts


def test_strategy1_multiline_send_does_not_duplicate_endpoint(tmp_path: Path) -> None:
    _write(tmp_path, "kafka.yml", "kafka:\n  topics:\n    ordersCreated:\n      nom: orders.created\n")
    _write(tmp_path, "src/main/java/com/x/P.java", """package com.x;
import org.springframework.kafka.core.KafkaTemplate;
class P {
  private KafkaTemplate<String, OrderCreated> kafkaTemplate;
  void publish(OrderCreated event) {
    kafkaTemplate.send(
        kafkaProperties.getTopics().getOrdersCreated(),
        event);
  }
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path, strategy1=True))
    assert [(f[1], f[2], f[3]) for f in facts] == [("orders.created", False, "OrderCreated")], facts


def test_strategy1_does_not_remap_literal_topic_by_last_segment(tmp_path: Path) -> None:
    _write(tmp_path, "kafka.yml", "kafka:\n  topics:\n    created:\n      nom: payments.created\n")
    _write(tmp_path, "src/main/java/com/x/P.java", """package com.x;
import org.springframework.kafka.core.KafkaTemplate;
class P {
  private KafkaTemplate<String, OrderCreated> kafkaTemplate;
  void publish(OrderCreated event) {
    kafkaTemplate.send("orders.created", event);
  }
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path, strategy1=True))
    assert "payments.created" not in [f[1] for f in facts], facts


def test_strategy1_char_vs_byte_offset_picks_wrong_method(tmp_path: Path) -> None:
    accents = "é" * 400
    _write(tmp_path, "kafka.yml", "kafka:\n  topics:\n    ordersCreated:\n      nom: orders.created\n")
    _write(tmp_path, "src/main/java/com/x/P.java", f"""package com.x;
// {accents}
class P {{
  void unrelated(WrongType wrong) {{
    log.info("nothing here");
    log.info("nothing here");
    log.info("nothing here");
    log.info("nothing here");
    log.info("nothing here");
    log.info("nothing here");
    log.info("nothing here");
    log.info("nothing here");
    log.info("nothing here");
    log.info("nothing here");
  }}
  void publish(OrderCreated event) {{
    kafkaTemplate.send(kafkaProperties.getTopics().getOrdersCreated(), event);
  }}
}}
""")
    facts = _facts(infer_kafka_topic_strategy1_endpoints(tmp_path))
    assert [f[3] for f in facts] == ["OrderCreated"], facts


# ---------------------------------------------------------------- listeners
def test_repeatable_kafka_listener_keeps_every_annotation(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/com/x/L.java", """package com.x;
class L {
  @KafkaListener(topics = "orders", groupId = "a")
  @KafkaListener(topics = "payments", groupId = "b")
  void on(Order o) {}
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert sorted(f[1] for f in facts) == ["orders", "payments"], facts


def test_class_level_kafka_listener_is_indexed(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/com/x/L.java", """package com.x;
@KafkaListener(topics = "orders")
class L {
  @KafkaHandler
  void on(Order o) {}
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert [f[1] for f in facts] == ["orders"], facts


# ---------------------------------------------------------------- paths / roots
def test_symlinked_repo_root_still_finds_module_properties(tmp_path: Path) -> None:
    real = tmp_path / "real"
    _write(real, "orders/src/main/resources/application.yml", "app:\n  topic: orders.created\n")
    _write(real, "orders/src/main/java/com/x/L.java", """package com.x;
class L {
  @KafkaListener(topics = "${app.topic}")
  void on(Order o) {}
}
""")
    link = tmp_path / "link"
    os.symlink(real, link)
    facts = _facts(infer_kafka_endpoints(link))
    assert [(f[1], f[2]) for f in facts] == [("orders.created", False)], facts


def test_relative_repo_root_still_finds_module_properties(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "orders/src/main/resources/application.yml", "app:\n  topic: orders.created\n")
    _write(tmp_path, "orders/src/main/java/com/x/L.java", """package com.x;
class L {
  @KafkaListener(topics = "${app.topic}")
  void on(Order o) {}
}
""")
    monkeypatch.chdir(tmp_path)
    facts = _facts(infer_kafka_endpoints(Path(".")))
    assert [(f[1], f[2]) for f in facts] == [("orders.created", False)], facts


def test_diamond_producer_record_takes_payload_argument_type(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/com/x/P.java", """package com.x;
import org.apache.kafka.clients.producer.ProducerRecord;
class P {
  void publish(Order event) {
    producer.send(new ProducerRecord<>("orders", event));
  }
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert [(f[1], f[3]) for f in facts] == [("orders", "Order")], facts


def test_message_payload_type_void() -> None:
    assert _message_payload_type("void") is None
