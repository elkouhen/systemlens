import json
import shutil
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from typer.testing import CliRunner

from systemlens.cli import app
from systemlens.domain.code_flows import CodeFlow, CodeFlowStep
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule, MongoMethod
from systemlens.indexing.code_flows import materialize_code_flows
from systemlens.indexing.code_flows import materialize_codeql_code_flows
from systemlens.indexing.code_flows import materialize_kafka_flow_continuations
from systemlens.indexing.codeql import CodeQLCall
from systemlens.indexing.integration_methods import materialize_integration_methods
from systemlens.indexing import service as indexing_service
from systemlens.infrastructure.config import Config
from systemlens.indexing.service import index_repo
from systemlens.storage.sqlite import Store


FIXTURES = Path(__file__).parent / "fixtures"
RUNNER = CliRunner()


def _endpoint(
    identifier: str, role: str, system: str, topic: str, path: str, line: int
) -> MessageEndpoint:
    return MessageEndpoint(
        id=identifier,
        role=role,
        system=system,
        topic=topic,
        topic_dynamic=False,
        source="code",
        framework="test",
        path=path,
        start_line=line,
        end_line=line,
        snippet=topic,
        module="orders",
        qualified_name="com.example.OrderController",
    )


def test_materialize_code_flows_orders_same_method_effects(tmp_path: Path) -> None:
    module_root = tmp_path / "orders"
    relative_source = "orders/src/main/java/com/example/OrderController.java"
    source = tmp_path / relative_source
    source.parent.mkdir(parents=True)
    source_text = """class OrderController {
  void place() {
    client.call();
    kafka.send();
    mongo.save();
  }
  void unrelated() { kafka.send(); }
}
"""
    source.write_text(source_text, encoding="utf-8")
    module = DiscoveredModule(
        name="orders",
        path=module_root,
        build_system="maven",
        version=None,
        kind="application",
        starts_application=True,
        configuration_example="",
        mongo_methods=(
            MongoMethod(
                operation="save",
                receiver="mongo",
                path="src/main/java/com/example/OrderController.java",
                line=5,
                collection="orders",
                owner_method="place",
            ),
        ),
    )
    endpoints = [
        _endpoint("entry", "serve", "rest", "POST /orders", relative_source, 2),
        _endpoint("call", "call", "rest", "POST /payments", relative_source, 3),
        _endpoint("publish", "produce", "kafka", "orders.created", relative_source, 4),
        _endpoint("unrelated", "produce", "kafka", "audit", relative_source, 7),
    ]

    flows = materialize_code_flows(tmp_path, endpoints, [module])

    assert len(flows) == 1
    assert flows[0].method == "com.example.OrderController.place"
    assert flows[0].status == "potential"
    assert flows[0].confidence == "medium"
    assert [(step.order, step.kind, step.name) for step in flows[0].steps] == [
        (1, "http_entry", "POST /orders"),
        (2, "http_call", "POST /payments"),
        (3, "message_publish", "orders.created"),
        (4, "data_write", "orders"),
    ]

    source.write_text(f"\n{source_text}", encoding="utf-8")
    shifted_module = replace(
        module,
        mongo_methods=(replace(module.mongo_methods[0], line=6),),
    )
    shifted_endpoints = [
        replace(endpoint, start_line=endpoint.start_line + 1, end_line=endpoint.end_line + 1)
        for endpoint in endpoints
    ]

    shifted_flows = materialize_code_flows(
        tmp_path, shifted_endpoints, [shifted_module]
    )

    assert shifted_flows[0].id == flows[0].id


def test_kafka_continuations_require_concrete_topics_and_preserve_producer_effects() -> None:
    producer = CodeFlow(
        id="producer", module="orders", method="OrderController.place", path="OrderController.java",
        start_line=1, end_line=8, status="potential", confidence="medium", reason="test",
        steps=(
            CodeFlowStep(1, "http_entry", "POST /orders", "OrderController.java", 1, 1, "entry"),
            CodeFlowStep(2, "message_publish", "orders.created", "OrderController.java", 3, 3, "publish"),
        ),
    )
    consumer = CodeFlow(
        id="consumer", module="inventory", method="OrderConsumer.consume", path="OrderConsumer.java",
        start_line=1, end_line=8, status="potential", confidence="medium", reason="test",
        steps=(
            CodeFlowStep(1, "message_entry", "orders.created", "OrderConsumer.java", 1, 1, "consume"),
            CodeFlowStep(2, "message_publish", "stock.reserved", "OrderConsumer.java", 3, 3, "out"),
        ),
    )
    dynamic_consumer = replace(
        consumer, id="dynamic", steps=(
            replace(consumer.steps[0], endpoint_id="dynamic-consume"), consumer.steps[1],
        ),
    )
    endpoints = [
        _endpoint("entry", "serve", "rest", "POST /orders", "OrderController.java", 1),
        _endpoint("publish", "produce", "kafka", "orders.created", "OrderController.java", 3),
        _endpoint("consume", "consume", "kafka", "orders.created", "OrderConsumer.java", 1),
        replace(_endpoint("dynamic-consume", "consume", "kafka", "orders.created", "Other.java", 1), topic_dynamic=True),
    ]

    flows = materialize_kafka_flow_continuations([producer, consumer, dynamic_consumer], endpoints)

    combined = [flow for flow in flows if flow.id not in {"producer", "consumer", "dynamic"}]
    assert len(combined) == 1
    assert [step.kind for step in combined[0].steps] == [
        "http_entry", "message_publish", "message_entry", "message_publish",
    ]

    downstream = replace(
        consumer,
        id="downstream",
        module="shipping",
        method="ShippingConsumer.consume",
        steps=(
            CodeFlowStep(1, "message_entry", "stock.reserved", "ShippingConsumer.java", 1, 1, "stock-in"),
            CodeFlowStep(2, "http_call", "POST /shipments", "ShippingConsumer.java", 3, 3, "ship-out"),
        ),
    )
    endpoints.extend([
        _endpoint("out", "produce", "kafka", "stock.reserved", "OrderConsumer.java", 3),
        _endpoint("stock-in", "consume", "kafka", "stock.reserved", "ShippingConsumer.java", 1),
        _endpoint("ship-out", "call", "rest", "POST /shipments", "ShippingConsumer.java", 3),
    ])
    chained = materialize_kafka_flow_continuations([producer, consumer, downstream], endpoints)
    assert any(
        [step.kind for step in flow.steps] == [
            "http_entry", "message_publish", "message_entry", "message_publish",
            "message_entry", "http_call",
        ]
        for flow in chained
    )

    producer_with_later_effect = replace(
        producer, steps=(*producer.steps, CodeFlowStep(
            3, "data_write", "orders", "OrderController.java", 4, 4,
        )),
    )
    assert materialize_kafka_flow_continuations(
        [producer_with_later_effect, consumer], endpoints
    ) == [consumer, producer_with_later_effect]


def test_index_persists_and_cli_exposes_same_method_flow(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "endpoint_index_repo", repo)
    (repo / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion>"
        "<groupId>com.example</groupId><artifactId>orders</artifactId>"
        "<version>1.0.0</version></project>",
        encoding="utf-8",
    )
    with Store(repo) as store:
        index_repo(repo, Config(), store)
        flows = store.all_code_flows()

    assert len(flows) == 1
    assert [step.kind for step in flows[0].steps] == [
        "message_entry",
        "http_call",
    ]

    result = RUNNER.invoke(app, ["flows", "--root", str(repo), "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == [
        {
            "id": flows[0].id,
            "module": flows[0].module,
            "method": flows[0].method,
            "trigger": {"kind": "message_entry", "name": "orders.created"},
            "effects": 1,
            "status": "potential",
            "confidence": "medium",
        }
    ]

    detail = RUNNER.invoke(
        app, ["flows", "show", flows[0].id, "--root", str(repo), "--json"]
    )

    assert detail.exit_code == 0
    assert [step["kind"] for step in json.loads(detail.output)["steps"]] == [
        "message_entry",
        "http_call",
    ]

    source = repo / "app/OrderConsumer.java"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            'restTemplate.postForObject("http://payment-service/charge", payload, String.class);',
            'System.out.println("no external effect");',
        ),
        encoding="utf-8",
    )
    with Store(repo) as store:
        index_repo(repo, Config(), store)
        assert store.all_code_flows() == []


def test_index_uses_automatic_codeql_database_when_available(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "endpoint_index_repo", repo)
    (repo / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion>"
        "<groupId>com.example</groupId><artifactId>orders</artifactId>"
        "<version>1.0.0</version></project>",
        encoding="utf-8",
    )
    observed_roots = []

    @contextmanager
    def automatic_database(root: Path):
        observed_roots.append(root)
        yield tmp_path / "codeql-db"

    monkeypatch.setattr(indexing_service, "codeql_executable", lambda: "codeql")
    monkeypatch.setattr(indexing_service, "automatic_codeql_database", automatic_database)
    monkeypatch.setattr(indexing_service, "extract_codeql_calls", lambda _database: [])

    with Store(repo) as store:
        index_repo(repo, Config(), store)

    assert observed_roots == [repo]


def test_store_round_trips_code_flow_steps(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "endpoint_index_repo", repo)
    (repo / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion>"
        "<groupId>com.example</groupId><artifactId>orders</artifactId>"
        "<version>1.0.0</version></project>",
        encoding="utf-8",
    )
    with Store(repo) as store:
        index_repo(repo, Config(), store)
        before = store.all_code_flows()

    with Store(repo, readonly=True) as store:
        after = store.all_code_flows()

    assert after == before


def test_store_additively_migrates_previous_schema_for_code_flows(
    tmp_path: Path,
) -> None:
    with Store(tmp_path) as store:
        store.conn.execute("DROP TABLE code_flows")
        store.set_meta("schema_version", "26")

    with Store(tmp_path) as store:
        table = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'code_flows'"
        ).fetchone()
        assert table is not None
        methods_table = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'integration_methods'"
        ).fetchone()
        assert methods_table is not None
        assert store.get_meta("schema_version") == "28"


def test_codeql_calls_join_ast_entry_and_output_methods(tmp_path: Path) -> None:
    source = "orders/src/main/java/com/example/OrderController.java"
    target = "orders/src/main/java/com/example/OrderPublisher.java"
    for path, content in {
        source: """package com.example;
class OrderController {
  void receive() { publish(); }
  void publish() {}
}
""",
        target: """package com.example;
class OrderPublisher {
  void send() { kafka.send(); }
}
""",
    }.items():
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
    module = DiscoveredModule(
        name="orders", path=tmp_path / "orders", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    endpoints = [
        _endpoint("entry", "consume", "kafka", "orders.in", source, 3),
        replace(
            _endpoint("output", "produce", "kafka", "orders.out", target, 3),
            qualified_name="com.example.OrderPublisher",
        ),
    ]
    methods = materialize_integration_methods(tmp_path, endpoints, [source, target], [module])
    calls = [
        CodeQLCall("com.example.OrderController.receive", source, 3,
                   "com.example.OrderController.publish", source, 4, 3),
        CodeQLCall("com.example.OrderController.publish", source, 4,
                   "com.example.OrderPublisher.send", target, 3, 4),
    ]
    flows = materialize_codeql_code_flows(methods, endpoints, calls)

    assert len(flows) == 1
    assert [step.kind for step in flows[0].steps] == [
        "message_entry", "method_call", "method_call", "message_publish",
    ]


def test_codeql_calls_from_lambda_join_enclosing_entry_method(tmp_path: Path) -> None:
    source = "orders/src/main/java/com/example/OrderController.java"
    target = "orders/src/main/java/com/example/OrderPublisher.java"
    for path, content in {
        source: """package com.example;
class OrderController {
  void receive() {
    Runnable callback = () -> publisher.send();
  }
}
""",
        target: """package com.example;
class OrderPublisher {
  void send() { kafka.send(); }
}
""",
    }.items():
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
    module = DiscoveredModule(
        name="orders", path=tmp_path / "orders", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    endpoints = [
        _endpoint("entry", "consume", "kafka", "orders.in", source, 3),
        replace(
            _endpoint("output", "produce", "kafka", "orders.out", target, 3),
            qualified_name="com.example.OrderPublisher",
        ),
    ]
    methods = materialize_integration_methods(tmp_path, endpoints, [source, target], [module])
    flows = materialize_codeql_code_flows(methods, endpoints, [
        CodeQLCall("<anonymous class>.run", source, 4,
                   "com.example.OrderPublisher.send", target, 3, 4),
    ])

    assert len(flows) == 1
    assert [step.kind for step in flows[0].steps] == [
        "message_entry", "method_call", "message_publish",
    ]


def test_codeql_possible_virtual_dispatch_keeps_each_output_candidate(tmp_path: Path) -> None:
    source = "orders/src/main/java/com/example/OrderController.java"
    first_target = "orders/src/main/java/com/example/FirstPublisher.java"
    second_target = "orders/src/main/java/com/example/SecondPublisher.java"
    for path, content in {
        source: """package com.example;
class OrderController { void receive() { port.send(); } }
""",
        first_target: """package com.example;
class FirstPublisher { void send() { kafka.send(); } }
""",
        second_target: """package com.example;
class SecondPublisher { void send() { kafka.send(); } }
""",
    }.items():
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
    module = DiscoveredModule(
        name="orders", path=tmp_path / "orders", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    endpoints = [
        _endpoint("entry", "consume", "kafka", "orders.in", source, 2),
        replace(
            _endpoint("first", "produce", "kafka", "orders.first", first_target, 2),
            qualified_name="com.example.FirstPublisher",
        ),
        replace(
            _endpoint("second", "produce", "kafka", "orders.second", second_target, 2),
            qualified_name="com.example.SecondPublisher",
        ),
    ]
    methods = materialize_integration_methods(
        tmp_path, endpoints, [source, first_target, second_target], [module]
    )
    calls = [
        CodeQLCall("com.example.OrderController.receive", source, 2,
                   "com.example.FirstPublisher.send", first_target, 2, 2, "possible"),
        CodeQLCall("com.example.OrderController.receive", source, 2,
                   "com.example.SecondPublisher.send", second_target, 2, 2, "possible"),
    ]

    flows = materialize_codeql_code_flows(methods, endpoints, calls)

    assert {flow.steps[-1].name for flow in flows} == {"orders.first", "orders.second"}
    assert {flow.confidence for flow in flows} == {"low"}
    assert all("possible virtual-dispatch" in flow.reason for flow in flows)


def test_codeql_flow_materialization_bounds_exploration(tmp_path: Path) -> None:
    source = "orders/src/main/java/com/example/OrderController.java"
    target = "orders/src/main/java/com/example/OrderPublisher.java"
    for path, content in {
        source: "package com.example; class OrderController { void receive() {} }\n",
        target: "package com.example; class OrderPublisher { void send() {} }\n",
    }.items():
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
    module = DiscoveredModule(
        name="orders", path=tmp_path / "orders", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    endpoints = [
        _endpoint("entry", "consume", "kafka", "orders.in", source, 1),
        replace(_endpoint("output", "produce", "kafka", "orders.out", target, 1),
                qualified_name="com.example.OrderPublisher"),
    ]
    methods = materialize_integration_methods(tmp_path, endpoints, [source, target], [module])
    stats: dict[str, int] = {}

    flows = materialize_codeql_code_flows(
        methods,
        endpoints,
        [CodeQLCall("com.example.OrderController.receive", source, 1,
                    "com.example.OrderPublisher.send", target, 1, 1)],
        max_paths=1,
        stats=stats,
    )

    assert len(flows) == 1
    assert stats == {"calls": 1, "joined_calls": 1, "explored_paths": 1, "truncated_paths": 0}


def test_integration_method_ids_distinguish_java_overloads(tmp_path: Path) -> None:
    relative_source = "orders/src/main/java/com/example/OrderController.java"
    source = tmp_path / relative_source
    source.parent.mkdir(parents=True)
    source.write_text(
        """package com.example;
class OrderController {
  void publish(String order) { kafka.send(); }
  void publish(int order) { kafka.send(); }
}
""",
        encoding="utf-8",
    )
    module = DiscoveredModule(
        name="orders", path=tmp_path / "orders", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    endpoints = [
        replace(
            _endpoint("first", "produce", "kafka", "orders.string", relative_source, 3),
            qualified_name="com.example.OrderController",
        ),
        replace(
            _endpoint("second", "produce", "kafka", "orders.int", relative_source, 4),
            qualified_name="com.example.OrderController",
        ),
    ]

    methods = materialize_integration_methods(
        tmp_path, endpoints, [relative_source], [module]
    )

    assert len(methods) == 2
    assert len({method.id for method in methods}) == 2


def test_integration_methods_keep_lexical_owner_for_portless_helpers(tmp_path: Path) -> None:
    relative_source = "orders/src/main/java/com/example/OrderController.java"
    source = tmp_path / relative_source
    source.parent.mkdir(parents=True)
    source.write_text(
        """package com.example;
class OrderController { void receive() {} }
class FirstHelper { void execute() {} }
class SecondHelper { void execute() {} }
""",
        encoding="utf-8",
    )
    module = DiscoveredModule(
        name="orders", path=tmp_path / "orders", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    endpoints = [replace(
        _endpoint("entry", "serve", "rest", "GET /orders", relative_source, 2),
        qualified_name="com.example.OrderController",
    )]

    methods = materialize_integration_methods(
        tmp_path, endpoints, [relative_source], [module]
    )

    assert {method.qualified_method for method in methods} == {
        "com.example.OrderController.receive",
        "com.example.FirstHelper.execute",
        "com.example.SecondHelper.execute",
    }
    assert len({method.id for method in methods}) == 3
