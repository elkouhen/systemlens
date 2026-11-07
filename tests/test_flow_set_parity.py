"""One persisted flow set whatever the engine or command (BL-11).

Review findings F4, F5 and F12 of 2026-11-03: the engines and ``flows
calculate`` persisted different flows for one method with several effects,
a scheduled flow twice under two trigger labels, and ``flows calculate``
dropped the index configuration. ``tests/fixtures/flow_parity`` holds an
HTTP entry with two publications and a helper call, and a scheduler with
one publication and the same helper call.
"""

import shutil
from pathlib import Path

import pytest

from systemlens.application.flow_calculation import calculate_persisted_flows
from systemlens.callgraph.registry import declaration
from systemlens.indexing.service import index_repo
from systemlens.infrastructure.config import Config
from systemlens.storage.sqlite import Store

FIXTURE = Path(__file__).parent / "fixtures" / "flow_parity"
SOURCE_ENGINE = "source"

# Each persisted flow, as its ordered step names.
EXPECTED_FLOWS = {
    # Both publications of ``create`` stay in one same-method flow (F4).
    ("POST /orders/create", "orders", "audit"),
    ("POST /orders/create", "example.Notifier.notifyCreated", "notifications"),
    # One label for the scheduler, whichever pass builds the flow (F5).
    ("@Scheduled(cron=0 * * * * *)", "ticks"),
    ("@Scheduled(cron=0 * * * * *)", "example.Notifier.notifyCreated", "notifications"),
}


def _flows(store: Store) -> dict[str, tuple[str, ...]]:
    return {
        flow.id: tuple(step.name for step in flow.steps)
        for flow in store.all_code_flows()
    }


def _copy(tmp_path: Path, name: str, fixture: Path = FIXTURE) -> Path:
    repo = tmp_path / name
    shutil.copytree(fixture, repo)
    return repo


def _available(engine: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        declaration(engine).unavailable_reason() is not None,
        reason=f"{engine} is not installed",
    )


@pytest.mark.parametrize("engine", [
    SOURCE_ENGINE,
    pytest.param("codeql", marks=[pytest.mark.slow, _available("codeql")]),
    pytest.param("joern", marks=[pytest.mark.slow, _available("joern")]),
])
def test_every_engine_and_flows_calculate_persist_one_flow_set(
    tmp_path: Path, engine: str,
) -> None:
    reference_repo = _copy(tmp_path, "reference")
    with Store(reference_repo) as store:
        index_repo(reference_repo, Config(call_graph_engine=SOURCE_ENGINE), store, full=True)
        reference = _flows(store)

    repo = _copy(tmp_path, engine)
    with Store(repo) as store:
        index_repo(repo, Config(call_graph_engine=engine), store, full=True)
        indexed = _flows(store)
        with store.transaction():
            calculate_persisted_flows(store)
        calculated = _flows(store)
        # ``--module`` rebuilds the module's same-method flows too (F12).
        module = store.all_code_flows()[0].module
        with store.transaction():
            calculate_persisted_flows(store, module=module)
        module_calculated = _flows(store)

    assert set(reference.values()) == EXPECTED_FLOWS
    assert indexed == reference
    assert calculated == reference
    assert module_calculated == reference


INTERFACE_DISPATCH = {
    "pom.xml": (
        "<project><modelVersion>4.0.0</modelVersion><groupId>example</groupId>"
        "<artifactId>orders</artifactId><version>1.0</version></project>"
    ),
    "src/main/java/example/Api.java": (
        "package example;\n"
        "import org.springframework.web.bind.annotation.PostMapping;\n"
        "import org.springframework.web.bind.annotation.RestController;\n"
        "@RestController\n"
        "class Api {\n"
        "    private Sink sink;\n"
        "    @PostMapping(\"/orders\")\n"
        "    public void create() {\n"
        "        sink.publish();\n"
        "    }\n"
        "}\n"
    ),
    "src/main/java/example/Sink.java": (
        "package example;\ninterface Sink {\n    void publish();\n}\n"
    ),
    "src/main/java/example/KafkaSink.java": (
        "package example;\n"
        "import org.springframework.kafka.core.KafkaTemplate;\n"
        "class KafkaSink implements Sink {\n"
        "    private KafkaTemplate<String, String> template;\n"
        "    public void publish() {\n"
        "        template.send(\"orders\", \"created\");\n"
        "    }\n"
        "}\n"
    ),
    "src/main/java/example/AuditSink.java": (
        "package example;\n"
        "import org.springframework.kafka.core.KafkaTemplate;\n"
        "class AuditSink implements Sink {\n"
        "    private KafkaTemplate<String, String> template;\n"
        "    public void publish() {\n"
        "        template.send(\"audit\", \"created\");\n"
        "    }\n"
        "}\n"
    ),
}


def _write_repo(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def test_flows_calculate_applies_the_configured_edge_confidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo, INTERFACE_DISPATCH)
    with Store(repo) as store:
        index_repo(repo, Config(call_graph_engine=SOURCE_ENGINE), store, full=True)
        # Two implementations: each route is a possible dispatch.
        assert {flow.steps[-1].name for flow in store.all_code_flows()} == {"orders", "audit"}
        assert {flow.confidence for flow in store.all_code_flows()} == {"low"}

        config = repo / ".systemlens" / "config.yml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("analysis:\n  engine_edge_confidence: exact\n", encoding="utf-8")
        with store.transaction():
            count = calculate_persisted_flows(store)

        assert count == 0
        assert store.all_code_flows() == []


def test_flows_calculate_keeps_key_tracing_and_after_commit_phase(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo, {
        "pom.xml": INTERFACE_DISPATCH["pom.xml"],
        "src/main/java/example/Order.java": (
            "package example;\nimport jakarta.persistence.Entity;\n"
            "import jakarta.persistence.Id;\n"
            "@Entity public class Order { @Id private String id; }\n"
        ),
        "src/main/java/example/OrderRepository.java": (
            "package example;\n"
            "import org.springframework.data.jpa.repository.JpaRepository;\n"
            "public interface OrderRepository extends JpaRepository<Order, String> {}\n"
        ),
        "src/main/java/example/Api.java": (
            "package example;\n"
            "import org.springframework.kafka.annotation.KafkaListener;\n"
            "class Api {\n"
            "    private Remover remover;\n"
            "    private Relay relay;\n"
            "    @KafkaListener(topics = \"orders.closed\")\n"
            "    public void closed(String id) {\n"
            "        remover.remove(id);\n"
            "        relay.relay(id);\n"
            "    }\n"
            "}\n"
        ),
        "src/main/java/example/Remover.java": (
            "package example;\n"
            "class Remover {\n"
            "    private OrderRepository repository;\n"
            "    void remove(String key) {\n"
            "        repository.deleteById(key);\n"
            "    }\n"
            "}\n"
        ),
        "src/main/java/example/Relay.java": (
            "package example;\n"
            "import org.springframework.kafka.core.KafkaTemplate;\n"
            "import org.springframework.transaction.event.TransactionalEventListener;\n"
            "class Relay {\n"
            "    private KafkaTemplate<String, String> template;\n"
            "    @TransactionalEventListener\n"
            "    void relay(String id) {\n"
            "        template.send(\"orders.events\", id);\n"
            "    }\n"
            "}\n"
        ),
    })

    def facts(store: Store) -> set[tuple[str, str | None, str | None]]:
        return {
            (step.kind, step.key_identity, step.phase)
            for flow in store.all_code_flows() for step in flow.steps[1:]
            if step.kind != "method_call"
        }

    with Store(repo) as store:
        index_repo(repo, Config(call_graph_engine=SOURCE_ENGINE), store, full=True)
        indexed = facts(store)
        with store.transaction():
            calculate_persisted_flows(store)
        calculated = facts(store)
        with store.transaction():
            calculate_persisted_flows(store, module=store.all_code_flows()[0].module)
        module_calculated = facts(store)

    assert ("message_publish", None, "after_commit") in indexed
    assert any(
        kind == "data_write" and key is not None and ":parameter:" in key
        for kind, key, _phase in indexed
    )
    assert calculated == indexed
    assert module_calculated == indexed
