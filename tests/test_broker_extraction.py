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
