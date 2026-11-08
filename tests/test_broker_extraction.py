"""RabbitMQ and JMS ports from the Java AST (PDR-15, PDR-16)."""

from pathlib import Path

from systemlens.scanner import infer_broker_endpoints


def _write(root: Path, name: str, body: str) -> str:
    path = root / "src" / "main" / "java" / "example" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"package example;\n\n{body}\n", encoding="utf-8")
    return path.relative_to(root).as_posix()


def _ports(root: Path, path: str) -> list[tuple[str, str, str, bool]]:
    return sorted(
        (endpoint.system, endpoint.role, endpoint.topic, endpoint.topic_dynamic)
        for endpoint in infer_broker_endpoints(root, [path])
    )


def test_rabbit_publications_name_the_routing_key_or_the_exchange(tmp_path: Path) -> None:
    path = _write(tmp_path, "Sender.java", """
import org.springframework.amqp.core.AmqpTemplate;
import org.springframework.amqp.rabbit.connection.CorrelationData;

class Sender {
    private AmqpTemplate amqp;
    private static final String QUEUE = "audit";

    void send(Object body, String computed) {
        amqp.convertAndSend("orders", body);
        amqp.convertAndSend("", "default-exchange", body);
        amqp.convertAndSend("events", "order.created", body);
        amqp.convertAndSend(QUEUE, body, message -> message);
        amqp.convertAndSend(body);
        amqp.convertAndSend(body, message -> message);
        amqp.convertAndSend(computed, body);
        amqp.convertAndSend(Queues.NAME_FROM_ELSEWHERE, body);
        amqp.receive("orders");
    }
}
""")
    assert _ports(tmp_path, path) == sorted([
        ("rabbitmq", "produce", "orders", False),
        ("rabbitmq", "produce", "default-exchange", False),
        ("rabbitmq", "produce", "events/order.created", False),
        ("rabbitmq", "produce", "audit", False),
        ("rabbitmq", "produce", "<dynamic>", True),
        ("rabbitmq", "produce", "<dynamic>", True),
        ("rabbitmq", "produce", "<dynamic>", True),
        ("rabbitmq", "produce", "<dynamic>", True),
    ])


def test_jms_publications_name_the_destination(tmp_path: Path) -> None:
    path = _write(tmp_path, "Sender.java", """
import org.springframework.jms.core.JmsTemplate;

class Sender {
    private final JmsTemplate jms;

    Sender(JmsTemplate jms) { this.jms = jms; }

    void send(Object body) {
        jms.convertAndSend("audit-queue", body);
        jms.send("creator-queue", session -> session.createTextMessage("x"));
        jms.convertAndSend(body);
        jms.convertAndSend(body, message -> message);
    }
}
""")
    assert _ports(tmp_path, path) == sorted([
        ("jms", "produce", "audit-queue", False),
        ("jms", "produce", "creator-queue", False),
        ("jms", "produce", "<dynamic>", True),
        ("jms", "produce", "<dynamic>", True),
    ])


def test_a_send_on_another_receiver_is_no_publication(tmp_path: Path) -> None:
    path = _write(tmp_path, "Mailer.java", """
class Mailer {
    private MailClient client;

    void send(Object body) {
        client.send("orders", body);
        client.convertAndSend("orders", body);
    }
}
""")
    assert _ports(tmp_path, path) == []


def test_rabbit_and_jms_listeners_are_consumers(tmp_path: Path) -> None:
    path = _write(tmp_path, "Listeners.java", """
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.jms.annotation.JmsListener;

class Listeners {
    @RabbitListener(queues = {"orders", "refunds"})
    void onOrder(String body) {}

    @RabbitListener(bindings = @QueueBinding(value = @Queue("bound")))
    void onBinding(String body) {}

    @JmsListener(destination = "audit-queue")
    void onAudit(String body) {}

    // @RabbitListener(queues = "commented")
}
""")
    assert _ports(tmp_path, path) == sorted([
        ("rabbitmq", "consume", "orders", False),
        ("rabbitmq", "consume", "refunds", False),
        ("rabbitmq", "consume", "<dynamic>", True),
        ("jms", "consume", "audit-queue", False),
    ])


def test_class_level_rabbit_listener_reaches_its_handlers(tmp_path: Path) -> None:
    from systemlens.indexing.integration_methods import materialize_integration_methods

    path = _write(tmp_path, "Handlers.java", """
import org.springframework.amqp.rabbit.annotation.RabbitHandler;
import org.springframework.amqp.rabbit.annotation.RabbitListener;

@RabbitListener(queues = "orders")
class Handlers {
    @RabbitHandler
    void onText(String body) {}

    @RabbitHandler
    void onBytes(byte[] body) {}

    void helper() {}
}
""")
    endpoints = [
        endpoint.__class__(**{**endpoint.__dict__, "module": "app"})
        for endpoint in infer_broker_endpoints(tmp_path, [path])
    ]
    methods = materialize_integration_methods(tmp_path, endpoints, [path], [])
    inputs = {method.qualified_method: method.input_endpoint_ids for method in methods}
    assert inputs["example.Handlers.onText"] == (endpoints[0].id,)
    assert inputs["example.Handlers.onBytes"] == (endpoints[0].id,)
    assert inputs["example.Handlers.helper"] == ()
