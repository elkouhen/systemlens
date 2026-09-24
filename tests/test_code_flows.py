import json
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from typer.testing import CliRunner

from systemlens import cli
from systemlens.delivery.cli import app
from systemlens.domain.code_flows import CodeFlow, CodeFlowStep, IntegrationMethod
from systemlens.domain.graph import GraphEdge
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule, MongoMethod
from systemlens.indexing.code_flows import (
    CodeQLCallGraph,
    _deduplicate_code_flows,
    codeql_join_methods_signature,
)
from systemlens.indexing.code_flows import materialize_code_flows, reconcile_code_flows
from systemlens.indexing.code_flows import materialize_codeql_code_flows
from systemlens.indexing import codeql
from systemlens.indexing.codeql import CodeQLCall
from systemlens.indexing.integration_methods import materialize_integration_methods
from systemlens.indexing import service as indexing_service
from systemlens.infrastructure.config import Config
from systemlens.indexing.service import index_repo
from systemlens.storage.sqlite import Store


FIXTURES = Path(__file__).parent / "fixtures"
RUNNER = CliRunner()


def _endpoint(
    identifier: str, role: str, system: str, topic: str, path: str, line: int,
    message_type: str | None = None,
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
        message_type=message_type or ("TestMessage" if system == "kafka" else None),
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


def test_materialize_code_flows_keeps_scheduled_kafka_publication_local(tmp_path: Path) -> None:
    module_root = tmp_path / "orders"
    relative_source = "orders/src/main/java/com/example/ScheduledPublisher.java"
    source = tmp_path / relative_source
    source.parent.mkdir(parents=True)
    source.write_text(
        """import org.springframework.scheduling.annotation.Scheduled;

class ScheduledPublisher {
  @Scheduled(cron = "0 * * * * *")
  void publish() { kafka.send(); }
}
""",
        encoding="utf-8",
    )
    module = DiscoveredModule(
        name="orders", path=module_root, build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    producer = _endpoint(
        "publish", "produce", "kafka", "orders.created", relative_source, 5,
        message_type="OrderCreated",
    )
    consumers = [
        replace(_endpoint("inventory", "consume", "kafka", "orders.created", "inventory/Consumer.java", 4, message_type="OrderCreated"), module="inventory"),
        replace(_endpoint("restock", "consume", "kafka", "orders.created", "restock/Consumer.java", 5, message_type="OrderCreated"), module="restock"),
    ]

    flows = materialize_code_flows(tmp_path, [producer, *consumers], [module])

    assert {(flow.module, flow.steps[-1].endpoint_id) for flow in flows} == {
        ("orders", "publish"),
    }
    assert all(flow.steps[0].kind == "cron_entry" for flow in flows)
    assert {flow.steps[0].name for flow in flows} == {"0 * * * * *"}


def test_materialize_code_flows_does_not_root_on_untriggered_publication(tmp_path: Path) -> None:
    module_root = tmp_path / "orders"
    relative_source = "orders/src/main/java/com/example/Publisher.java"
    source = tmp_path / relative_source
    source.parent.mkdir(parents=True)
    source.write_text("class Publisher { void publish() { kafka.send(); } }\n", encoding="utf-8")
    module = DiscoveredModule(
        name="orders", path=module_root, build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    producer = _endpoint(
        "publish", "produce", "kafka", "orders.created", relative_source, 1,
        message_type="OrderCreated",
    )
    consumer = replace(
        _endpoint("inventory", "consume", "kafka", "orders.created", "inventory/Consumer.java", 1, message_type="OrderCreated"),
        module="inventory",
    )

    assert materialize_code_flows(tmp_path, [producer, consumer], [module]) == []

def test_code_flow_deduplication_keeps_shortest_strongest_route() -> None:
    entry = _endpoint("entry", "serve", "rest", "POST /orders", "Orders.java", 1)
    output = _endpoint("output", "call", "rest", "POST /inventory", "Orders.java", 8)
    long = CodeFlow(
        id="long", module="orders", method="Orders.place", path="Orders.java",
        start_line=1, end_line=8, status="potential", confidence="low", reason="long",
        steps=(
            CodeFlowStep(1, "http_entry", entry.topic, entry.path, 1, 1, entry.id),
            CodeFlowStep(2, "method_call", "Orders.helper", entry.path, 3, 3),
            CodeFlowStep(3, "method_call", "Orders.other", entry.path, 5, 5),
            CodeFlowStep(4, "http_call", output.topic, output.path, 8, 8, output.id),
        ),
    )
    short = replace(long, id="short", confidence="medium", steps=(
        CodeFlowStep(1, "http_entry", entry.topic, entry.path, 1, 1, entry.id),
        CodeFlowStep(2, "http_call", output.topic, output.path, 8, 8, output.id),
    ))

    assert _deduplicate_code_flows([long, short]) == [replace(short, alternative_count=2)]


def test_codeql_call_graph_groups_edges_without_reversing_direction() -> None:
    caller = IntegrationMethod("a", "orders", "A.receive", "A.java", 1, 1, ("in",), ())
    middle = IntegrationMethod("b", "orders", "B.process", "B.java", 1, 1, (), ())
    target = IntegrationMethod("c", "orders", "C.send", "C.java", 1, 1, (), ("out",))
    call_ab = CodeQLCall("A.receive", "A.java", 1, "B.process", "B.java", 1, 1)
    call_bc = CodeQLCall("B.process", "B.java", 1, "C.send", "C.java", 1, 1)
    graph = CodeQLCallGraph(
        adjacency={
            caller.id: [(middle, call_ab, False)],
            middle.id: [(target, call_bc, False)],
        },
        synthetic_calls=set(),
        call_count=2,
        locate=lambda _name, _path, _line: None,
    )

    assert graph.connected_components(("a", "b", "c", "isolated")) == (
        frozenset({"a", "b", "c"}),
        frozenset({"isolated"}),
    )
    assert [edge[0].id for edge in graph.adjacency["a"]] == ["b"]


def test_reconcile_code_flows_marks_missing_topology_as_partial() -> None:
    entry = _endpoint("entry", "serve", "rest", "POST /orders", "orders/Orders.java", 1)
    output = replace(
        _endpoint("output", "call", "rest", "POST /payments", "orders/Orders.java", 2),
        module="payments",
    )
    flow = CodeFlow(
        id="flow", module="orders", method="Orders.place", path="orders/Orders.java",
        start_line=1, end_line=2, status="potential", confidence="medium", reason="test",
        steps=(
            CodeFlowStep(1, "http_entry", entry.topic, entry.path, 1, 1, entry.id),
            CodeFlowStep(2, "http_call", output.topic, output.path, 2, 2, output.id),
        ),
    )
    edge = GraphEdge("rest", "orders", "payments", output, None)

    assert reconcile_code_flows([flow], [entry, output], [edge])[0].reconciliation == "complete"
    assert reconcile_code_flows([flow], [entry, output], [])[0].reconciliation == "partial"


def test_reconcile_code_flows_requires_the_matching_topology_endpoints() -> None:
    entry = _endpoint("entry", "serve", "rest", "POST /orders", "orders/Orders.java", 1)
    output = replace(
        _endpoint("output", "call", "rest", "POST /payments", "orders/Orders.java", 2),
        module="payments",
    )
    unrelated = replace(
        _endpoint("unrelated", "serve", "rest", "POST /other", "payments/Other.java", 3),
        module="payments",
    )
    downstream = replace(
        _endpoint("downstream", "consume", "kafka", "orders.created", "inventory/Other.java", 4),
        module="inventory",
    )
    flow = CodeFlow(
        id="flow", module="orders", method="Orders.place", path="orders/Orders.java",
        start_line=1, end_line=3, status="potential", confidence="medium", reason="test",
        steps=(
            CodeFlowStep(1, "http_entry", entry.topic, entry.path, 1, 1, entry.id),
            CodeFlowStep(2, "http_call", output.topic, output.path, 2, 2, output.id),
            CodeFlowStep(3, "message_entry", downstream.topic, downstream.path, 4, 4, downstream.id),
        ),
    )
    wrong_edge = GraphEdge("rest", "orders", "payments", output, unrelated)

    assert reconcile_code_flows(
        [flow], [entry, output, unrelated, downstream], [wrong_edge]
    )[0].reconciliation == "partial"


def test_reconcile_code_flows_marks_ambiguous_topology_as_partial() -> None:
    entry = _endpoint("entry", "serve", "rest", "POST /orders", "orders/Orders.java", 1)
    output = replace(
        _endpoint("output", "call", "rest", "POST /payments", "orders/Orders.java", 2),
        module="payments",
    )
    edges = [
        GraphEdge("rest", "orders", "payments", output, None),
        GraphEdge("rest", "orders", "payments", output, None),
    ]
    flow = CodeFlow(
        id="flow", module="orders", method="Orders.place", path="orders/Orders.java",
        start_line=1, end_line=2, status="potential", confidence="medium", reason="test",
        steps=(
            CodeFlowStep(1, "http_entry", entry.topic, entry.path, 1, 1, entry.id),
            CodeFlowStep(2, "http_call", output.topic, output.path, 2, 2, output.id),
        ),
    )

    assert reconcile_code_flows([flow], [entry, output], edges)[0].reconciliation == "partial"


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
                "reconciliation": "complete",
                "alternative_count": 1,
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
    materializations = []
    original_materialize = indexing_service.materialize_codeql_code_flows

    def counted_materialize(*args, **kwargs):
        materializations.append(1)
        return original_materialize(*args, **kwargs)

    monkeypatch.setattr(indexing_service, "materialize_codeql_code_flows", counted_materialize)

    @contextmanager
    def automatic_database(
        root: Path,
        *,
        timeout_seconds: int = 600,
        threads: int = 1,
        ram_mb: int | None = None,
        deadline: float | None = None,
    ):
        observed_roots.append(root)
        assert timeout_seconds == 600
        assert threads == 0
        assert ram_mb is None
        yield tmp_path / "codeql-db"

    monkeypatch.setattr(indexing_service, "codeql_executable", lambda: "codeql")
    monkeypatch.setattr(indexing_service, "automatic_codeql_database", automatic_database)
    monkeypatch.setattr(
        indexing_service, "extract_codeql_calls", lambda _database, **_kwargs: []
    )
    monkeypatch.setattr(
        indexing_service, "extract_codeql_reachability",
        lambda _database, _methods, **_kwargs: [],
    )

    checkpoints = []
    persisted_checkpoints = []
    progress_messages: list[str] = []

    def observe_checkpoint(checkpoint) -> None:
        checkpoints.append(checkpoint)
        with Store(repo, readonly=True) as reader:
            persisted_checkpoints.append(
                (reader.get_meta("code_flow_snapshot_status"), len(reader.all_code_flows()))
            )

    with Store(repo) as store:
        index_repo(
            repo,
            Config(),
            store,
            progress=progress_messages.append,
            call_graph_progress=observe_checkpoint,
        )

    assert observed_roots == [repo]
    assert len(materializations) == 1  # shared by progress and final persistence
    project_checkpoints = [item for item in checkpoints if item.phase == "projects"]
    join_checkpoints = [item for item in checkpoints if item.phase == "join"]
    assert [(item.engine, item.completed_projects, item.total_projects, item.project_name) for item in project_checkpoints] == [
        ("codeql", 1, 1, "orders"),
    ]
    assert join_checkpoints
    assert join_checkpoints[-1].completed_units == join_checkpoints[-1].total_units
    assert project_checkpoints[0].relations
    assert project_checkpoints[0].project_name == "orders"
    assert project_checkpoints[0].project_input_methods
    assert project_checkpoints[0].project_output_methods
    assert any(
        method.qualified_method.endswith(".onOrderCreated")
        for method in project_checkpoints[0].project_input_methods
    )
    assert all(status == "partial" for status, _count in persisted_checkpoints)
    assert any("checkpoint 1/1 persisté" in message for message in progress_messages)
    with Store(repo, readonly=True) as reader:
        assert reader.get_meta("code_flow_snapshot_status") == "complete"
    progress_html = tmp_path / "codeql-progress.html"
    cli._write_call_graph_progress_html(repo, progress_html, project_checkpoints[0])
    content = progress_html.read_text(encoding="utf-8")
    assert "INDEXATION CODEQL EN COURS" in content
    assert "1/1 projet(s) terminé(s)" in content
    assert "module Maven : orders" in content
    assert "Méthodes Java cherchées : IN [" in content
    assert 'id="progress-notice"' in content
    assert '"progress_notice": "INDEXATION CODEQL EN COURS' in content
    join_progress_html = tmp_path / "codeql-join-progress.html"
    cli._write_call_graph_progress_html(repo, join_progress_html, join_checkpoints[-1])
    join_content = join_progress_html.read_text(encoding="utf-8")
    assert "jointure CodeQL : 1/1 méthode(s) IN traitée(s)" in join_content


def test_codeql_timeout_commits_partial_snapshot_and_runs_post_processing(
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

    @contextmanager
    def timed_out_database(*_args, **kwargs):
        raise subprocess.TimeoutExpired(["codeql"], kwargs.get("timeout_seconds", 600))
        yield  # pragma: no cover - keeps this a contextmanager for the patch

    monkeypatch.setattr(indexing_service, "codeql_executable", lambda: "codeql")
    monkeypatch.setattr(indexing_service, "automatic_codeql_database", timed_out_database)
    progress: list[str] = []

    with Store(repo) as store:
        report = index_repo(repo, Config(), store, progress=progress.append)
        assert report.codeql_timed_out is True
        assert store.all_endpoints()
        assert store.all_architecture_relations()
        assert store.all_code_flows()
        assert store.get_meta("code_flow_signature") is None
        assert store.get_meta("code_flow_snapshot_status") == "partial"

    assert any("délai dépassé" in message for message in progress)


def test_real_codeql_database_creation_timeout_is_soft_boundary(
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

    def timeout(*_args, **kwargs):
        raise subprocess.TimeoutExpired(["codeql", "database", "create"], kwargs["timeout"])

    monkeypatch.setattr(indexing_service, "codeql_executable", lambda: "codeql")
    monkeypatch.setattr(codeql, "codeql_executable", lambda: "codeql")
    monkeypatch.setattr(codeql, "_run_with_progress", timeout)
    progress: list[str] = []

    with Store(repo) as store:
        report = index_repo(repo, Config(), store, progress=progress.append)
        assert report.codeql_timed_out is True
        assert store.all_architecture_relations()
        assert store.all_code_flows()

    assert any("post-traitements" in message for message in progress)
    assert any("matérialisation des flux" in message for message in progress)
    assert any("statistiques par module" in message for message in progress)


def test_codeql_module_roots_assign_each_java_file_to_its_deepest_project(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    nested = first / "nested"
    modules = [
        DiscoveredModule("first", first, "maven", None, "library", False, ""),
        DiscoveredModule("nested", nested, "maven", None, "library", False, ""),
        DiscoveredModule("second", second, "gradle", None, "library", False, ""),
    ]

    roots = indexing_service._codeql_module_roots(
        tmp_path,
        [
            "first/src/main/java/A.java",
            "first/nested/src/main/java/B.java",
            "second/src/main/java/C.java",
        ],
        modules,
    )

    assert roots == [
        ("first", first, "first"),
        ("nested", nested, "first/nested"),
        ("second", second, "second"),
    ]


def test_global_codeql_calls_are_partitioned_without_losing_cross_project_edges(
    tmp_path: Path,
) -> None:
    roots = [
        ("first", tmp_path / "first", "first"),
        ("second", tmp_path / "second", "second"),
    ]
    calls = [
        CodeQLCall(
            "first.Controller.handle", "first/src/Controller.java", 10,
            "second.Client.send", "second/src/Client.java", 20, 11,
        ),
        CodeQLCall(
            "second.Client.send", "second/src/Client.java", 20,
            "first.Repository.save", "first/src/Repository.java", 30, 21,
        ),
    ]

    partitioned = indexing_service._partition_codeql_calls(calls, roots)

    assert [name for name, _calls in partitioned] == ["first", "second"]
    assert partitioned[0][1] == [calls[0]]
    assert partitioned[1][1] == [calls[1]]


def test_cli_no_codeql_keeps_ast_only_flow_indexing(
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
    monkeypatch.chdir(repo)
    assert RUNNER.invoke(app, ["init"]).exit_code == 0

    result = RUNNER.invoke(app, ["index", "--no-codeql"])

    assert result.exit_code == 0
    assert "CodeQL" not in result.output
    with Store(repo, readonly=True) as store:
        assert [step.kind for step in store.all_code_flows()[0].steps] == [
            "message_entry", "http_call",
        ]


def test_cli_codeql_progress_html_requires_codeql(tmp_path: Path) -> None:
    result = RUNNER.invoke(
        app,
        ["index", "--no-codeql", "--codeql-progress-html", str(tmp_path / "progress.html")],
    )

    assert result.exit_code == 2
    assert "requiert CodeQL" in result.output


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
        call_edges_table = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'codeql_call_edges'"
        ).fetchone()
        assert call_edges_table is not None
        assert store.get_meta("schema_version") == "32"


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
    persisted_edges = []
    flows = materialize_codeql_code_flows(
        methods,
        endpoints,
        calls,
        call_graph_sink=lambda graph: persisted_edges.extend(graph.edges()),
    )

    assert len(flows) == 1
    assert [step.kind for step in flows[0].steps] == [
        "message_entry", "method_call", "method_call", "message_publish",
    ]
    assert [(edge.caller_id, edge.callee_id, edge.line) for edge in persisted_edges] == [
        (methods[0].id, methods[1].id, 3),
        (methods[1].id, methods[2].id, 4),
    ]


def test_codeql_materializes_same_method_contract_input_and_output(tmp_path: Path) -> None:
    source = "orders/src/main/java/com/example/OrderController.java"
    file = tmp_path / source
    file.parent.mkdir(parents=True)
    file.write_text("package com.example; class OrderController { void place() {} }\n", encoding="utf-8")
    module = DiscoveredModule(
        name="orders", path=tmp_path / "orders", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    entry = _endpoint("entry", "serve", "rest", "POST /orders", source, 1)
    output = replace(_endpoint("output", "call", "rest", "POST /inventory", source, 1), module="orders")
    methods = materialize_integration_methods(tmp_path, [entry, output], [source], [module])

    flows = materialize_codeql_code_flows(methods, [entry, output], [])

    assert len(flows) == 1
    assert [step.kind for step in flows[0].steps] == ["http_entry", "http_call"]


def test_codeql_join_methods_signature_changes_when_input_method_facts_change(
    tmp_path: Path,
) -> None:
    source = "orders/src/main/java/com/example/OrderController.java"
    file = tmp_path / source
    file.parent.mkdir(parents=True)
    file.write_text("package com.example; class OrderController { void place() {} }\n", encoding="utf-8")
    module = DiscoveredModule(
        name="orders", path=tmp_path / "orders", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    entry = _endpoint("entry", "serve", "rest", "POST /orders", source, 1)
    output = replace(_endpoint("output", "call", "rest", "POST /inventory", source, 1), module="orders")
    methods = materialize_integration_methods(tmp_path, [entry, output], [source], [module])

    changed = [replace(methods[0], end_line=methods[0].end_line + 1), *methods[1:]]

    assert codeql_join_methods_signature(methods) != codeql_join_methods_signature(changed)


def test_possible_signature_only_call_uses_explicit_low_confidence_join(tmp_path: Path) -> None:
    source = "orders/src/main/java/com/example/OrderController.java"
    target = "orders/src/main/java/com/example/OrderPublisher.java"
    for path, content in {
        source: """package com.example;
class OrderController { void receive() { publisher.send(); } }
""",
        target: """package com.example;
class OrderPublisher { void send() { kafka.send(); } }
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
        replace(_endpoint("output", "produce", "kafka", "orders.out", target, 2),
                qualified_name="com.example.OrderPublisher"),
    ]
    methods = materialize_integration_methods(tmp_path, endpoints, [source, target], [module])
    flows = materialize_codeql_code_flows(methods, endpoints, [
        CodeQLCall("com.example.OrderController.receive:void()", source, 2,
                   "com.example.OrderPublisher.send:void()", "", 0, 2, "possible"),
    ])

    assert len(flows) == 1
    assert flows[0].confidence == "low"
    assert [step.kind for step in flows[0].steps] == [
        "message_entry", "method_call", "message_publish",
    ]


def test_codeql_call_normalizes_method_signature_and_path_spelling(tmp_path: Path) -> None:
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

    flows = materialize_codeql_code_flows(methods, endpoints, [
        CodeQLCall(
            "com.example.OrderController.receive:void()", source, 1,
            "com.example.OrderPublisher.send(java.lang.String)",
            target.replace("/", "\\"), 1, 1,
        ),
    ])

    assert len(flows) == 1
    assert flows[0].steps[-1].name == "orders.out"


def test_codeql_edge_confidence_can_exclude_possible_dispatch(tmp_path: Path) -> None:
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
    call = CodeQLCall(
        "com.example.OrderController.receive", source, 1,
        "com.example.OrderPublisher.send", target, 1, 1, "possible",
    )

    assert materialize_codeql_code_flows(
        methods, endpoints, [call], codeql_edge_confidence="exact"
    ) == []
    assert len(materialize_codeql_code_flows(
        methods, endpoints, [call], codeql_edge_confidence="possible"
    )) == 1


def test_codeql_call_without_callee_source_location_uses_explicit_low_confidence_join(tmp_path: Path) -> None:
    source = "orders/src/main/java/com/example/OrderController.java"
    target = "publisher/src/main/java/com/example/OrderPublisher.java"
    for path, content in {
        source: """package com.example;
class OrderController { void receive() { publisher.send(); } }
""",
        target: """package com.example;
class OrderPublisher { void send() { kafka.send(); } }
""",
    }.items():
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
    modules = [
        DiscoveredModule("orders", tmp_path / "orders", "maven", None, "library", False, ""),
        DiscoveredModule("publisher", tmp_path / "publisher", "maven", None, "library", False, ""),
    ]
    endpoints = [
        _endpoint("entry", "consume", "kafka", "orders.in", source, 2),
        replace(
            _endpoint("output", "produce", "kafka", "orders.out", target, 2),
            module="publisher",
            qualified_name="com.example.OrderPublisher",
        ),
    ]
    methods = materialize_integration_methods(tmp_path, endpoints, [source, target], modules)

    flows = materialize_codeql_code_flows(methods, endpoints, [
        CodeQLCall(
            "com.example.OrderController.receive", source, 2,
            "com.example.OrderPublisher.send", "external/OrderPublisher.java", 1, 2,
        ),
    ])

    assert len(flows) == 1
    assert flows[0].confidence == "low"
    assert "unique indexed method signature" in flows[0].reason


def test_codeql_call_bridges_unique_cross_module_output_implementation(tmp_path: Path) -> None:
    source = "orders/src/main/java/com/example/OrderController.java"
    port = "orders/src/main/java/com/example/StockPort.java"
    target = "inventory/src/main/java/com/example/StockAdapter.java"
    for path, content in {
        source: "package com.example; class OrderController { void receive() { port.publish(); } }\n",
        port: "package com.example; interface StockPort { void publish(); }\n",
        target: "package com.example; class StockAdapter implements StockPort { public void publish() {} }\n",
    }.items():
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
    modules = [
        DiscoveredModule("orders", tmp_path / "orders", "maven", None, "application", True, ""),
        DiscoveredModule("inventory", tmp_path / "inventory", "maven", None, "library", False, ""),
    ]
    endpoints = [
        _endpoint("entry", "consume", "kafka", "orders.in", source, 1),
        replace(_endpoint("output", "produce", "kafka", "inventory.out", target, 1),
                module="inventory", qualified_name="com.example.StockAdapter"),
    ]
    methods = materialize_integration_methods(tmp_path, endpoints, [source, port, target], modules)

    flows = materialize_codeql_code_flows(methods, endpoints, [
        CodeQLCall(
            "com.example.OrderController.receive", source, 1,
            "com.example.StockPort.publish", port, 1, 1,
        ),
    ], repo_root=tmp_path)

    assert len(flows) == 1
    assert flows[0].confidence == "low"
    assert flows[0].steps[-1].name == "inventory.out"


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
        stats=stats,
    )

    assert len(flows) == 1
    assert stats == {"calls": 1, "joined_calls": 1, "explored_paths": 1}


def test_ast_fallback_uses_call_arity_to_resolve_output_overloads(tmp_path: Path) -> None:
    source = "orders/src/main/java/com/example/OrderController.java"
    text = """package com.example;
class OrderController {
  void receive(String order) { this.send(order); }
  void send() { kafka.send(); }
  void send(String order) { kafka.send(); }
}
"""
    path = tmp_path / source
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    module = DiscoveredModule(
        name="orders", path=tmp_path / "orders", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    endpoints = [
        replace(_endpoint("entry", "serve", "rest", "POST /orders", source, 3),
                qualified_name="com.example.OrderController"),
        replace(_endpoint("zero", "produce", "kafka", "orders.zero", source, 4),
                qualified_name="com.example.OrderController"),
        replace(_endpoint("one", "produce", "kafka", "orders.one", source, 5),
                qualified_name="com.example.OrderController"),
    ]
    methods = materialize_integration_methods(tmp_path, endpoints, [source], [module])

    flows = materialize_codeql_code_flows(methods, endpoints, [], repo_root=tmp_path)

    assert len(flows) == 1
    assert flows[0].steps[-1].name == "orders.one"
    assert flows[0].confidence == "low"


def test_ast_fallback_uses_declared_receiver_hierarchy(tmp_path: Path) -> None:
    source = "orders/src/main/java/com/example/OrderController.java"
    text = """package com.example;
interface StockPort { void publish(); }
interface OtherPort { void publish(); }
class StockAdapter implements StockPort { public void publish() { kafka.send(); } }
class OtherAdapter implements OtherPort { public void publish() { kafka.send(); } }
class OrderController {
  StockPort port;
  void receive() { port.publish(); }
}
"""
    path = tmp_path / source
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    module = DiscoveredModule(
        name="orders", path=tmp_path / "orders", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    endpoints = [
        replace(_endpoint("entry", "serve", "rest", "POST /orders", source, 8),
                qualified_name="com.example.OrderController"),
        replace(_endpoint("stock", "produce", "kafka", "orders.stock", source, 4),
                qualified_name="com.example.StockAdapter"),
        replace(_endpoint("other", "produce", "kafka", "orders.other", source, 5),
                qualified_name="com.example.OtherAdapter"),
    ]
    methods = materialize_integration_methods(tmp_path, endpoints, [source], [module])

    flows = materialize_codeql_code_flows(methods, endpoints, [], repo_root=tmp_path)

    assert len(flows) == 1
    assert flows[0].steps[-1].name == "orders.stock"
    assert flows[0].confidence == "low"


def test_ast_fallback_keeps_ambiguous_candidates_unresolved(tmp_path: Path) -> None:
    source = "orders/src/main/java/com/example/OrderController.java"
    text = """package com.example;
class FirstAdapter { public void publish() { kafka.send(); } }
class SecondAdapter { public void publish() { kafka.send(); } }
class OrderController {
  void receive() { publish(); }
  void publish() { kafka.send(); }
}
"""
    path = tmp_path / source
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    module = DiscoveredModule(
        name="orders", path=tmp_path / "orders", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    endpoints = [
        replace(_endpoint("entry", "serve", "rest", "POST /orders", source, 5),
                qualified_name="com.example.OrderController"),
        replace(_endpoint("first", "produce", "kafka", "orders.first", source, 2),
                qualified_name="com.example.FirstAdapter"),
        replace(_endpoint("second", "produce", "kafka", "orders.second", source, 3),
                qualified_name="com.example.SecondAdapter"),
    ]
    methods = materialize_integration_methods(tmp_path, endpoints, [source], [module])

    flows = materialize_codeql_code_flows(methods, endpoints, [], repo_root=tmp_path)

    assert flows == []


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


def test_integration_methods_match_unique_openapi_override_by_operation_id(tmp_path: Path) -> None:
    java_path = "orders/src/main/java/com/example/OrderController.java"
    contract_path = "orders/src/main/resources/static/openapi.yaml"
    source = tmp_path / java_path
    source.parent.mkdir(parents=True)
    source.write_text(
        """package com.example;
@RestController
class OrderController {
  @Override String placeOrder(Object request) { return \"ok\"; }
  String helper() { return \"ignored\"; }
}
""",
        encoding="utf-8",
    )
    contract = tmp_path / contract_path
    contract.parent.mkdir(parents=True)
    contract.write_text(
        """openapi: 3.0.3
paths:
  /api/orders:
    post:
      operationId: placeOrder
""",
        encoding="utf-8",
    )
    module = DiscoveredModule(
        name="orders", path=tmp_path / "orders", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    endpoint = replace(
        _endpoint("openapi-entry", "serve", "rest", "POST /api/orders", contract_path, 4),
        framework="openapi",
    )

    methods = materialize_integration_methods(tmp_path, [endpoint], [java_path], [module])

    place_order = next(method for method in methods if method.qualified_method.endswith(".placeOrder"))
    assert place_order.input_endpoint_ids == ("openapi-entry",)
