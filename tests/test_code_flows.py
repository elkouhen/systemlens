import json
import shutil
from dataclasses import replace
from pathlib import Path

from typer.testing import CliRunner

from systemlens.cli import app
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule, MongoMethod
from systemlens.indexing.code_flows import materialize_code_flows
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
        assert store.get_meta("schema_version") == "27"
