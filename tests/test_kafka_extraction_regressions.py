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


# ------------------------------------------------- payload typing (ADR-76)
# One expression-typing rule serves ``KafkaTemplate.send`` and the Strategy1
# ``envoyerMessageKafka*`` methods. The template is ``KafkaTemplate<String,
# Object>`` so its value type, which is no DTO, never masks the argument.
_PAYLOAD_FILES = {
    "src/main/java/p/OrderEvent.java": "package p;\npublic record OrderEvent(String id) {}\n",
    "src/main/java/p/Order.java": "package p;\npublic record Order(String id) {}\n",
    "src/main/java/p/base/Base.java": """package p.base;
import p.OrderEvent;
public class Base { protected OrderEvent inherited; }
""",
    "src/main/java/p/mapping/Mapper.java": """package p.mapping;
import p.Order;
import p.OrderEvent;
public interface Mapper { OrderEvent toEvent(Order order); }
""",
    "src/main/java/p/Holder.java": """package p;
import lombok.Getter;
public class Holder { @Getter private OrderEvent event; }
""",
    "src/main/java/p/Built.java": """package p;
import lombok.Builder;
@Builder
public class Built { private String id; }
""",
}
_PAYLOAD_HEADER = """package p;
import java.util.List;
import org.springframework.kafka.core.KafkaTemplate;
import p.base.Base;
import p.mapping.Mapper;
"""


def _producer_types(tmp_path: Path, members: str, path: str) -> list[str | None]:
    for rel, text in _PAYLOAD_FILES.items():
        _write(tmp_path, rel, text)
    _write(tmp_path, "kafka.yml", "kafka:\n  topics:\n    a:\n      nom: t.a\n")
    if path == "template":
        body = members.replace("SEND(", 'template.send("t.a", ')
        _write(tmp_path, "src/main/java/p/P.java", _PAYLOAD_HEADER
               + "class P extends Base {\n  KafkaTemplate<String, Object> template;\n  "
               + body + "\n}\n")
        endpoints = infer_kafka_endpoints(tmp_path, ["src/main/java/p/P.java"])
    else:
        body = members.replace("SEND(", "kafkaService.envoyerMessageKafka(kafkaProperties.getTopics().getA(), ")
        _write(tmp_path, "src/main/java/p/P.java", _PAYLOAD_HEADER
               + "class P extends Base {\n  " + body + "\n}\n")
        endpoints = infer_kafka_topic_strategy1_endpoints(tmp_path, ["src/main/java/p/P.java"])
    return sorted(
        (e.message_type for e in endpoints if e.role == "produce"), key=lambda value: str(value)
    )


_LOST_SHAPES = [
    ("this-field", "OrderEvent field; void s() { SEND(this.field); }"),
    ("inherited-field", "void s() { SEND(inherited); }"),
    ("var-from-call", "Mapper mapper; void s(Order o) { var e = mapper.toEvent(o); SEND(e); }"),
    ("mapper-call", "Mapper mapper; void s(Order o) { SEND(mapper.toEvent(o)); }"),
    ("getter", "void s(Holder h) { SEND(h.getEvent()); }"),
    ("cast", "void s(Object o) { SEND((OrderEvent) o); }"),
    ("ternary", "void s(OrderEvent a, OrderEvent b, boolean c) { SEND(c ? a : b); }"),
    ("ternary-null", "void s(OrderEvent a, boolean c) { SEND(c ? a : null); }"),
    ("lambda-parameter", "void s(List<OrderEvent> l) { l.forEach(e -> SEND(e)); }"),
    ("stream-lambda", "void s(List<OrderEvent> l) { l.stream().filter(x -> true).forEach(e -> SEND(e)); }"),
    ("for-each-var", "void s(List<OrderEvent> l) { for (var e : l) { SEND(e); } }"),
    ("same-class-helper", "OrderEvent build() { return null; } void s() { SEND(build()); }"),
    ("declared-getter", "static class Box { OrderEvent get() { return null; } } void s(Box b) { SEND(b.get()); }"),
    ("record-accessor", "record Wrap(OrderEvent event) {} void s(Wrap w) { SEND(w.event()); }"),
    ("map-for-each", "void s(java.util.Map<String, OrderEvent> m) { m.forEach((k, v) -> SEND(v)); }"),
    ("list-get", "void s(List<OrderEvent> l) { SEND(l.get(0)); }"),
]


@pytest.mark.parametrize("path", ["template", "strategy1"])
@pytest.mark.parametrize(("shape", "members"), _LOST_SHAPES, ids=[shape for shape, _ in _LOST_SHAPES])
def test_payload_expression_shapes_are_typed(tmp_path: Path, path: str, shape: str, members: str) -> None:
    # Rule ADR-76 point 4: each shape lost its DTO type before the shared rule.
    assert _producer_types(tmp_path, members, path) == ["OrderEvent"], shape


@pytest.mark.parametrize("path", ["template", "strategy1"])
def test_lombok_builder_payload_is_the_built_type(tmp_path: Path, path: str) -> None:
    members = 'void s() { SEND(Built.builder().id("1").build()); }'
    assert _producer_types(tmp_path, members, path) == ["Built"]


@pytest.mark.parametrize("path", ["template", "strategy1"])
def test_builder_without_lombok_annotation_stays_unknown(tmp_path: Path, path: str) -> None:
    # ``OrderEvent`` declares no ``@Builder``: nothing says what ``build()`` returns.
    members = 'void s() { SEND(OrderEvent.builder().id("1").build()); }'
    assert _producer_types(tmp_path, members, path) == [None]


_UNINFORMATIVE_SHAPES = [
    ("method-type-variable", "<T> void s(T e) { SEND(e); }"),
    ("object", "void s(Object e) { SEND(e); }"),
    ("json-string", "com.fasterxml.jackson.databind.ObjectMapper om; "
                    "void s(OrderEvent e) throws Exception { String json = om.writeValueAsString(e); SEND(json); }"),
    ("bytes", "void s(byte[] e) { SEND(e); }"),
    ("generic-of-type-variable", "<T> void s(List<T> e) { SEND(e); }"),
]


@pytest.mark.parametrize("path", ["template", "strategy1"])
@pytest.mark.parametrize(("shape", "members"), _UNINFORMATIVE_SHAPES, ids=[shape for shape, _ in _UNINFORMATIVE_SHAPES])
def test_uninformative_payload_types_are_unknown(tmp_path: Path, path: str, shape: str, members: str) -> None:
    # Rule ADR-76 point 1: such a type names no DTO and must not block a pairing.
    assert _producer_types(tmp_path, members, path) == [None], shape


def test_class_type_variable_payload_is_unknown(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/p/P.java", """package p;
import org.springframework.kafka.core.KafkaTemplate;
class P<E> {
  KafkaTemplate<String, E> template;
  void s(E event) { template.send("t.a", event); }
}
""")
    assert [e.message_type for e in infer_kafka_endpoints(tmp_path)] == [None]


@pytest.mark.parametrize("path", ["template", "strategy1"])
def test_collection_payload_keeps_its_written_type(tmp_path: Path, path: str) -> None:
    # A list sent as one message is the real payload type; it is not unwrapped.
    assert _producer_types(tmp_path, "void s(List<OrderEvent> l) { SEND(l); }", path) == ["List<OrderEvent>"]


def test_template_value_type_is_the_fallback(tmp_path: Path) -> None:
    # Rule ADR-76 point 3: an untyped value takes V of KafkaTemplate<K, V>.
    _write(tmp_path, "src/main/java/p/P.java", """package p;
import org.springframework.kafka.core.KafkaTemplate;
class P {
  private final KafkaTemplate<String, OrderEvent> template;
  void s(Object event, String json) {
    template.send("t.a", event);
    this.template.send("t.b", "k", json);
    template.send("t.c", unknown());
  }
}
""")
    facts = {e.topic: e.message_type for e in infer_kafka_endpoints(tmp_path)}
    assert facts == {"t.a": "OrderEvent", "t.b": "OrderEvent", "t.c": "OrderEvent"}, facts


def test_template_value_object_gives_no_fallback(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/p/P.java", """package p;
import org.springframework.kafka.core.KafkaTemplate;
class P {
  KafkaTemplate<String, Object> template;
  KafkaTemplate<String, String> json;
  void s(Object event) { template.send("t.a", event); json.send("t.b", event); }
}
""")
    assert sorted(str(e.message_type) for e in infer_kafka_endpoints(tmp_path)) == ["None", "None"]


def test_argument_type_wins_over_template_value_type(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/p/P.java", """package p;
import org.springframework.kafka.core.KafkaTemplate;
class P {
  KafkaTemplate<String, Object> template;
  KafkaTemplate<String, BaseEvent> typed;
  void s(OrderEvent event) { typed.send("t.a", event); }
}
""")
    assert [e.message_type for e in infer_kafka_endpoints(tmp_path)] == ["OrderEvent"]


def test_message_builder_single_argument_send_is_typed(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/p/P.java", """package p;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.KafkaHeaders;
import org.springframework.messaging.support.MessageBuilder;
class P {
  KafkaTemplate<String, Object> template;
  void s(OrderEvent event) {
    var message = MessageBuilder.withPayload(event).setHeader(KafkaHeaders.TOPIC, "t.a").build();
    template.send(message);
  }
}
""")
    assert [(e.topic, e.message_type) for e in infer_kafka_endpoints(tmp_path)] == [("t.a", "OrderEvent")]


@pytest.mark.parametrize(
    ("parameter", "expected"),
    [
        ("String json", None),
        ("Object payload", None),
        ("byte[] payload", None),
        ("ConsumerRecord<String, String> record", None),
        ("ConsumerRecord record", None),
        ("Message<?> message", None),
        ("String json, Acknowledgment ack", None),
        ("OrderEvent event", "OrderEvent"),
        ("ConsumerRecord<String, OrderEvent> record", "OrderEvent"),
    ],
)
def test_listener_uninformative_payload_is_unknown(tmp_path: Path, parameter: str, expected: str | None) -> None:
    # Rule ADR-76 point 1 applies to consumers too, so both sides stay symmetric.
    _write(tmp_path, "src/main/java/p/L.java", f"""package p;
import org.springframework.kafka.annotation.KafkaListener;
class L {{
  @KafkaListener(topics = "t.a")
  void on({parameter}) {{}}
}}
""")
    assert [e.message_type for e in infer_kafka_endpoints(tmp_path)] == [expected]


def test_generic_listener_type_variable_is_unknown(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/p/L.java", """package p;
import org.springframework.kafka.annotation.KafkaListener;
class L<E> {
  @KafkaListener(topics = "t.a")
  void on(E event) {}
}
""")
    assert [e.message_type for e in infer_kafka_endpoints(tmp_path)] == [None]


def test_strategy1_listener_string_payload_is_unknown(tmp_path: Path) -> None:
    _write(tmp_path, "kafka.yml", "kafka:\n  topics:\n    a:\n      nom: t.a\n")
    _write(tmp_path, "src/main/java/p/L.java", """package p;
import org.springframework.kafka.annotation.KafkaListener;
class L {
  @KafkaListener(topics = "${kafka.topics.a.nom}")
  public void consume(String json) {}
}
""")
    consumers = [e for e in infer_kafka_endpoints(tmp_path, strategy1=True) if e.role == "consume"]
    assert [(e.topic, e.message_type) for e in consumers] == [("t.a", None)]


# ------------------------------------------------------ pairing (ADR-76)
def _kafka_edges(tmp_path: Path, producer: str, consumer: str) -> list:
    from systemlens.domain.graph import build_graph

    _write(tmp_path, "orders/src/main/java/p/OrderEvent.java", "package p;\npublic record OrderEvent(String id) {}\n")
    _write(tmp_path, "orders/src/main/java/p/P.java", """package p;
import org.springframework.kafka.core.KafkaTemplate;
class P {
  KafkaTemplate<String, Object> template;
  """ + producer + """
}
""")
    _write(tmp_path, "stock/src/main/java/s/C.java", """package s;
import org.springframework.kafka.annotation.KafkaListener;
import p.OrderEvent;
class C {
  @KafkaListener(topics = "t.a")
  """ + consumer + """
}
""")
    endpoints = infer_kafka_endpoints(tmp_path)
    by_service = {
        "orders": [e for e in endpoints if e.role == "produce"],
        "stock": [e for e in endpoints if e.role == "consume"],
    }
    return [edge for edge in build_graph(by_service) if edge.kind == "kafka"]


@pytest.mark.parametrize(
    "producer",
    [
        '<T> void s(T e) { template.send("t.a", e); }',
        'void s(Object e) { template.send("t.a", e); }',
        'com.fasterxml.jackson.databind.ObjectMapper om; '
        'void s(OrderEvent e) throws Exception { template.send("t.a", om.writeValueAsString(e)); }',
        'void s(p.OrderEvent e) { template.send("t.a", e); }',
    ],
    ids=["type-variable", "object", "json-string", "qualified-name"],
)
def test_uninformative_or_qualified_producer_type_pairs_with_consumer(tmp_path: Path, producer: str) -> None:
    # Rules ADR-76 points 1 and 2: these producers no longer block the edge.
    edges = _kafka_edges(tmp_path, producer, "void on(OrderEvent event) {}")
    assert [(edge.from_service, edge.to_service) for edge in edges] == [("orders", "stock")]


def test_string_consumer_pairs_with_typed_producer(tmp_path: Path) -> None:
    edges = _kafka_edges(tmp_path, 'void s(OrderEvent e) { template.send("t.a", e); }', "void on(String json) {}")
    assert [(edge.from_service, edge.to_service) for edge in edges] == [("orders", "stock")]


def test_different_known_dto_types_still_block_pairing(tmp_path: Path) -> None:
    # The blocking rule itself is unchanged: two named DTOs that differ conflict.
    edges = _kafka_edges(
        tmp_path, 'void s(OrderPlacedEvent e) { template.send("t.a", e); }', "void on(OrderEvent event) {}",
    )
    assert edges == []


@pytest.mark.parametrize(
    ("produced", "consumed", "conflict"),
    [
        ("p.OrderEvent", "OrderEvent", False),
        ("java.util.List<p.OrderEvent>", "List<OrderEvent>", False),
        ("String", "OrderEvent", False),
        ("java.lang.Object", "OrderEvent", False),
        ("Map<String, Object>", "OrderEvent", False),
        (None, "OrderEvent", False),
        ("OrderPlacedEvent", "OrderEvent", True),
        ("List<OrderEvent>", "OrderEvent", True),
    ],
)
def test_message_type_comparison_key(produced: str | None, consumed: str, conflict: bool) -> None:
    # Persisted indexes keep the spelling: the key is computed at comparison time.
    from systemlens.domain.message_types import message_types_conflict

    assert message_types_conflict(produced, consumed) is conflict


# ------------------------------------------- indexing review 2026-11-03, lot 2
_ORDER = "package p; public class Order {}"
_PAYMENT = "package p; public class Payment {}"


def _dto(root: Path) -> None:
    _write(root, "src/main/java/p/Order.java", _ORDER)
    _write(root, "src/main/java/p/Payment.java", _PAYMENT)


def _edges(root: Path) -> list[tuple[str, str]]:
    from systemlens.domain.graph import build_graph

    by_module: dict[str, list] = {}
    for endpoint in infer_kafka_endpoints(root):
        by_module.setdefault(endpoint.path.split("/")[0], []).append(endpoint)
    return [(edge.from_service, edge.to_service) for edge in build_graph(by_module)]
@pytest.mark.parametrize("carrier", [
    "JsonNode", "com.fasterxml.jackson.databind.JsonNode", "ObjectNode",
    "GenericRecord", "SpecificRecord", "Bytes", "ByteBuffer", "java.io.Serializable",
    "Map", "Map<String, Order>", "java.util.HashMap<String, Object>",
])
def test_generic_carrier_types_name_no_dto(carrier: str) -> None:
    """K7: a carrier type is unknown and never blocks a pairing."""
    from systemlens.domain.message_types import informative_message_type, message_types_conflict

    assert informative_message_type(carrier) is None
    assert not message_types_conflict(carrier, "Order")


def test_generic_carrier_producer_pairs_with_a_typed_consumer(tmp_path: Path) -> None:
    """K7: a `JsonNode` producer no longer blocks the `Order` consumer."""
    _write(tmp_path, "b/src/main/java/p/Order.java", _ORDER)
    _write(tmp_path, "a/src/main/java/p/P.java", """package p;
import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.kafka.core.KafkaTemplate;
class P { KafkaTemplate<String, JsonNode> kafkaTemplate; void s(JsonNode o) { kafkaTemplate.send("orders", o); } }
""")
    _write(tmp_path, "b/src/main/java/p/L.java", """package p;
import org.springframework.kafka.annotation.KafkaListener;
class L { @KafkaListener(topics = "orders") void on(Order o) {} }
""")
    assert _edges(tmp_path) == [("a", "b")]
def test_producer_record_value_is_chosen_by_constructor_arity(tmp_path: Path) -> None:
    """K4: the headers or timestamp argument is never the payload."""
    _dto(tmp_path)
    _write(tmp_path, "src/main/java/p/P.java", """package p;
import org.springframework.kafka.core.KafkaTemplate;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.header.Header;
import java.util.List;
class P {
  KafkaTemplate<String, Order> kafkaTemplate;
  void a(Order o, List<Header> headers) { kafkaTemplate.send(new ProducerRecord<>("orders", null, "k", o, headers)); }
  void b(Order o, Iterable<Header> headers) { ProducerRecord<String, Order> r = new ProducerRecord<>("orders-b", null, "k", o, headers); kafkaTemplate.send(r); }
  void c(Order o, org.apache.kafka.common.header.internals.RecordHeaders hs) { kafkaTemplate.send(new ProducerRecord<>("orders-c", null, null, "k", o, hs)); }
  void d(Order o, long ts) { kafkaTemplate.send(new ProducerRecord<>("orders-d", 0, ts, "k", o)); }
  void e(Order o) { kafkaTemplate.send(new ProducerRecord<>("orders-e", 0, "k", o)); }
  void f(List<Header> hs) { kafkaTemplate.send(new ProducerRecord<String, Order>("orders-f", null, "k", null, hs)); }
}
""")
    facts = sorted((e.topic, e.message_type) for e in infer_kafka_endpoints(tmp_path))
    assert facts == [
        ("orders", "Order"),
        ("orders-b", "Order"),
        ("orders-c", "Order"),
        ("orders-d", "Order"),
        ("orders-e", "Order"),
        ("orders-f", "Order"),
    ], facts
def test_send_default_topic_is_the_template_default_never_the_key(tmp_path: Path) -> None:
    """K3: sendDefault has no topic argument; the value is the last argument."""
    _dto(tmp_path)
    _write(tmp_path, "src/main/java/p/P.java", """package p;
import org.springframework.kafka.core.KafkaTemplate;
class P {
  KafkaTemplate<String, Order> kafkaTemplate;
  void a(Order o) { kafkaTemplate.sendDefault("order-key", o); }
  void b(Order o) { kafkaTemplate.sendDefault(o); }
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path))
    assert facts == [
        ("produce", "<dynamic>", True, "Order", "spring-kafka", 5),
        ("produce", "<dynamic>", True, "Order", "spring-kafka", 6),
    ], facts


def test_send_default_uses_the_configured_or_set_default_topic(tmp_path: Path) -> None:
    """K3: `spring.kafka.template.default-topic` or `setDefaultTopic`."""
    _dto(tmp_path)
    _write(tmp_path, "src/main/resources/application.yml",
           "spring:\n  kafka:\n    template:\n      default-topic: orders\n")
    _write(tmp_path, "src/main/java/p/P.java", """package p;
import org.springframework.kafka.core.KafkaTemplate;
class P {
  KafkaTemplate<String, Order> kafkaTemplate;
  void a(Order o) { kafkaTemplate.sendDefault("order-key", o); }
}
""")
    _write(tmp_path, "src/main/java/p/Q.java", """package p;
import org.springframework.kafka.core.KafkaTemplate;
class Q {
  KafkaTemplate<String, Payment> paymentTemplate;
  Q(KafkaTemplate<String, Payment> t) { paymentTemplate = t; paymentTemplate.setDefaultTopic("payments"); }
  void a(Payment p) { paymentTemplate.sendDefault(0, "k", p); }
}
""")
    facts = sorted(
        (e.path.rsplit("/", 1)[-1], e.topic, e.topic_dynamic, e.message_type)
        for e in infer_kafka_endpoints(tmp_path)
    )
    assert facts == [
        ("P.java", "orders", False, "Order"),
        ("Q.java", "payments", False, "Payment"),
    ], facts
_STREAMS_IMPORTS = """package p;
import org.apache.kafka.common.serialization.Serdes;
import org.apache.kafka.streams.*;
import org.apache.kafka.streams.kstream.*;
import org.springframework.context.annotation.Bean;
import org.springframework.kafka.support.serializer.JsonSerde;
"""


def test_kafka_streams_facts_are_never_typed_by_the_enclosing_return_type(
    tmp_path: Path,
) -> None:
    """K6: the KStream declaration or the serde types a stream, else unknown."""
    _dto(tmp_path)
    _write(tmp_path, "src/main/java/p/T.java", _STREAMS_IMPORTS + """class T {
  @Bean public Topology topo(StreamsBuilder builder) {
    KStream<String, Order> s = builder.stream("orders");
    s.mapValues(o -> new Payment()).to("payments");
    return builder.build();
  }
  @Bean public KStream<String, Payment> k(StreamsBuilder builder) {
    KStream<String, Order> s = builder.stream("orders-b");
    KStream<String, Payment> out = s.mapValues(o -> new Payment());
    out.to("payments-b");
    return out;
  }
  @Bean public KStream<String, Payment> serdes(StreamsBuilder builder) {
    KStream<String, Payment> out = builder.stream("orders-c", Consumed.with(Serdes.String(), new JsonSerde<>(Order.class)))
        .mapValues(o -> new Payment());
    out.to("payments-c", Produced.with(Serdes.String(), new JsonSerde<>(Payment.class)));
    return out;
  }
  @Bean public KStream<String, Payment> chained(StreamsBuilder builder) {
    KStream<String, Payment> out = builder.<String, Order>stream("orders-d").mapValues(o -> new Payment());
    return out;
  }
}
""")
    facts = sorted(
        (e.role, e.topic, e.message_type) for e in infer_kafka_endpoints(tmp_path)
    )
    assert facts == [
        ("consume", "orders", "Order"),
        ("consume", "orders-b", "Order"),
        ("consume", "orders-c", "Order"),
        ("consume", "orders-d", "Order"),
        ("produce", "payments", None),
        ("produce", "payments-b", "Payment"),
        ("produce", "payments-c", "Payment"),
    ], facts


def test_kafka_streams_consumer_pairs_with_a_typed_producer(tmp_path: Path) -> None:
    """K6: a `Topology` bean no longer blocks the pairing on `orders`."""
    for module in ("a", "b"):
        _write(tmp_path, f"{module}/src/main/java/p/Order.java", _ORDER)
    _write(tmp_path, "a/src/main/java/p/P.java", """package p;
import org.springframework.kafka.core.KafkaTemplate;
class P { KafkaTemplate<String, Order> kafkaTemplate; void s(Order o) { kafkaTemplate.send("orders", o); } }
""")
    _write(tmp_path, "b/src/main/java/p/T.java", _STREAMS_IMPORTS + """class T {
  @Bean public Topology topo(StreamsBuilder builder) {
    KStream<String, Order> s = builder.stream("orders"); s.foreach((k, v) -> {}); return builder.build();
  }
}
""")
    assert _edges(tmp_path) == [("a", "b")]
def test_strategy1_get_topics_outside_a_send_is_not_a_producer(tmp_path: Path) -> None:
    """K1: a topic bean, a log line and a commented-out send publish nothing."""
    _write(tmp_path, "src/main/resources/kafka.yml",
           "kafka:\n  topics:\n    commande-creee:\n      nom: cmd.creee\n")
    _write(tmp_path, "src/main/java/p/C.java", """package p;
import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.kafka.config.TopicBuilder;
class C {
  Props props;
  @org.springframework.context.annotation.Bean NewTopic t() { return TopicBuilder.name(props.getTopics().getCommandeCreee()).build(); }
  void log() { System.out.println("listening " + props.getTopics().getCommandeCreee()); }
  // kafkaTemplate.send(props.getTopics().getCommandeCreee(), dto);
  /* kafkaTemplate.send(props.getTopics().getCommandeCreee(), dto); */
}
""")
    assert infer_kafka_topic_strategy1_endpoints(tmp_path) == []


def test_strategy1_get_topics_send_and_record_are_producers(tmp_path: Path) -> None:
    """K1: the topic argument of a send or a ProducerRecord is the evidence."""
    _dto(tmp_path)
    _write(tmp_path, "src/main/resources/kafka.yml",
           "kafka:\n  topics:\n    commande-creee:\n      nom: cmd.creee\n")
    _write(tmp_path, "src/main/java/p/C.java", """package p;
import org.springframework.kafka.core.KafkaTemplate;
import org.apache.kafka.clients.producer.ProducerRecord;
class C {
  Props props;
  KafkaTemplate<String, Order> kafkaTemplate;
  void a(Order o) { kafkaTemplate.send(props.getTopics().getCommandeCreee(), o); }
  void b(Order o) {
    String topic = props.getTopics().getCommandeCreee();
    kafkaTemplate.send(topic, o);
  }
  void c(Order o) { kafkaTemplate.send(new ProducerRecord<>(props.getTopics().getCommandeCreee(), o)); }
}
""")
    facts = sorted(
        (e.role, e.topic, e.topic_dynamic, e.message_type, e.start_line)
        for e in infer_kafka_topic_strategy1_endpoints(tmp_path)
    )
    assert facts == [
        ("produce", "cmd.creee", False, "Order", 7),
        ("produce", "cmd.creee", False, "Order", 10),
        ("produce", "cmd.creee", False, "Order", 12),
    ], facts
def test_strategy1_commented_listener_is_not_a_consumer_and_ast_payload_wins(
    tmp_path: Path,
) -> None:
    """K2: listeners come from the AST; the @Payload parameter is the type."""
    _dto(tmp_path)
    _write(tmp_path, "src/main/resources/kafka.yml",
           "kafka:\n  topics:\n    commande-creee:\n      nom: cmd.creee\n"
           "    paiement:\n      nom: pay.done\n")
    _write(tmp_path, "src/main/java/p/L.java", """package p;
import org.springframework.kafka.annotation.*;
import org.springframework.kafka.support.KafkaHeaders;
class L {
  // @KafkaListener(topics = "${kafka.topics.paiement.nom}")
  @KafkaListener(topics = "${kafka.topics.commande-creee.nom}")
  public void consume(@Header(KafkaHeaders.RECEIVED_KEY) String key, @Payload Order order) {
  }
  private void helper(Payment p) {
  }
}
""")
    facts = _facts(infer_kafka_endpoints(tmp_path, strategy1=True))
    assert facts == [
        ("consume", "cmd.creee", False, "Order", "kafka-topic-strategy1", 6),
    ], facts


def test_strategy1_get_topics_in_a_message_topic_header_is_a_producer(tmp_path: Path) -> None:
    """K1: a `Message` whose TOPIC header is the accessor is still sent."""
    _dto(tmp_path)
    _write(tmp_path, "src/main/resources/kafka.yml",
           "kafka:\n  topics:\n    commande-creee:\n      nom: cmd.creee\n")
    _write(tmp_path, "src/main/java/p/C.java", """package p;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.KafkaHeaders;
import org.springframework.messaging.support.MessageBuilder;
class C {
  Props props;
  KafkaTemplate<String, Order> kafkaTemplate;
  void a(Order o) {
    kafkaTemplate.send(MessageBuilder.withPayload(o).setHeader(KafkaHeaders.TOPIC, props.getTopics().getCommandeCreee()).build());
  }
  void b(Order o) {
    var message = MessageBuilder.withPayload(o).setHeader(KafkaHeaders.TOPIC, props.getTopics().getCommandeCreee()).build();
    kafkaTemplate.send(message);
  }
}
""")
    facts = sorted(
        (e.topic, e.message_type, e.start_line)
        for e in infer_kafka_topic_strategy1_endpoints(tmp_path)
    )
    assert facts == [("cmd.creee", "Order", 9), ("cmd.creee", "Order", 13)], facts


# BL-09 (review finding K5): a variable is typed in its own scope. Each
# fixture declares an outer field ``Order e`` whose type must never leak
# into a pattern variable, a resource or an anonymous-class field ``e``.
_SCOPED_SHAPES = [
    ("instanceof", "Order e; void s(Object x) { if (x instanceof OrderEvent e) { SEND(e); } }", "OrderEvent"),
    ("instanceof-and",
     "Order e; void s(Object x) { if (x instanceof OrderEvent e && e.id() != null) SEND(e); }", "OrderEvent"),
    ("instanceof-else",
     "Order e; void s(Object x) { if (!(x instanceof OrderEvent e)) { return; } else { SEND(e); } }",
     "OrderEvent"),
    ("instanceof-early-return",
     "Order e; void s(Object x) { if (!(x instanceof OrderEvent e)) return; SEND(e); }", "OrderEvent"),
    ("instanceof-ternary",
     "Order e; Object s(Object x) { return x instanceof OrderEvent e ? SEND(e) : null; }", "OrderEvent"),
    ("record-pattern",
     "record Wrap(OrderEvent v) {} Order e; void s(Object x) { if (x instanceof Wrap(OrderEvent e)) SEND(e); }",
     "OrderEvent"),
    ("instanceof-unproven-scope",
     "Order e; void s(Object x) { if (!(x instanceof OrderEvent e)) { log(); } SEND(e); } void log() {}",
     None),
    ("switch-rule",
     "Order e; void s(Object x) { switch (x) { case OrderEvent e -> SEND(e); default -> {} } }", "OrderEvent"),
    ("switch-group",
     "Order e; void s(Object x) { switch (x) { case OrderEvent e: SEND(e); break; default: } }", "OrderEvent"),
    ("switch-guard",
     "Order e; void s(Object x) { switch (x) { case OrderEvent e when e.id() != null -> SEND(e); "
     "default -> {} } }", "OrderEvent"),
    ("try-resource",
     "Order e; OrderEvent open() { return null; } void s() { try (OrderEvent e = open()) { SEND(e); } }",
     "OrderEvent"),
    ("try-resource-var",
     "Order e; OrderEvent open() { return null; } void s() { try (var e = open()) { SEND(e); } }",
     "OrderEvent"),
    ("try-resource-unknown", "Order e; void s() { try (var e = open()) { SEND(e); } }", None),
    ("anonymous-field",
     "Order e; void s() { new Runnable() { OrderEvent e; public void run() { SEND(e); } }; }", "OrderEvent"),
    ("anonymous-this-field",
     "Order e; void s() { new Runnable() { OrderEvent e; public void run() { SEND(this.e); } }; }",
     "OrderEvent"),
    ("anonymous-this-field-unknown",
     "Order e; void s() { new Runnable() { public void run() { SEND(this.e); } }; }", None),
    # The outer field itself stays typed where nothing shadows it.
    ("outer-field", "Order e; void s() { SEND(e); }", "Order"),
    ("outer-field-from-anonymous",
     "Order e; void s() { new Runnable() { public void run() { SEND(e); } }; }", "Order"),
    ("outer-field-beside-pattern",
     "Order e; void s(Object x) { if (x instanceof OrderEvent v) { return; } SEND(e); }", "Order"),
]


@pytest.mark.parametrize("path", ["template", "strategy1"])
@pytest.mark.parametrize(
    ("shape", "members", "expected"), _SCOPED_SHAPES, ids=[shape for shape, _, _ in _SCOPED_SHAPES],
)
def test_payload_variable_is_typed_in_its_own_scope(
    tmp_path: Path, path: str, shape: str, members: str, expected: str | None,
) -> None:
    assert _producer_types(tmp_path, members, path) == [expected], shape
